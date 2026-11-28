"""Logos's lock, projectile field and shared necrosis receiver in H5-1."""
from dataclasses import dataclass

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_first_spell import _selected_clip, attack_key_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .elemental_casters import _board
from .elemental_projectiles import stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .logos_lock import LOGOS, lock_definition, talent_launch
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer, deploy_skill
from .trace_motion import ordinary_trace_step, paracurve_horizontal_step
from .volley_damage import _raw_f32


class LogosElements:
    def __init__(self, battle):
        self.battle = battle
        self.receivers = {}

    def receiver(self,target):
        if target.id not in self.receivers:
            self.receivers[target.id] = EnemyElements.from_scene_enemy(self.battle.tables,
                dict(level=target.level, hp_q32=target.hp, ep_resistance_q32=_raw_f32(target.attr("epResistance")),
                     ep_damage_resistance_q32=_raw_f32(target.attr("epDamageResistance")), palsy_immune=bool(target.attr("palsyImmune"))))
        return self.receivers[target.id]

    def prepare_arts(self, actor, target, attack):
        if actor.uid == LOGOS:
            self.receiver(target)
        state = self.receivers.get(target.id)
        if state is None:
            return attack, 0
        state.hp = target.hp
        if actor.uid == LOGOS:
            state._source_calculate_damage(actor.card)
        curse = state.logos_curse
        return (attack + curse.attack_add, curse.resistance_add) if curse else (attack, 0)

    def after_arts(self, actor, target, damage):
        state = self.receivers.get(target.id)
        if state is None:
            return
        state.hp = target.hp
        if actor.uid == LOGOS and (actor.card["module_info"] or {}).get("typeIcon") == "ccr-d":
            state.apply_module_arts(self.battle.frame, actor.card, damage)
            self._settle(target, state, finish=False)

    def talent_element(self, actor, target, amount):
        state = self.receivers[target.id]
        if state.burst == "DARK" and target.alive:
            state.hp = target.hp
            state.apply_elemental_hp(self.battle.frame, amount, source="logos_delta_talent", source_card=actor.card)
            self._settle(target, state)

    def _settle(self, target, state, *, finish=True):
        target.hp = state.hp
        for row in state.events:
            event = dict(row)
            event.pop("frame")
            if event["kind"]=="burst_start" and event["element"]=="DARK":
                actor=self.battle.operators.get("char_4146_nymph")
                if actor is not None and actor.handler:
                    actor.handler.dark_started(target)
            self.battle.log(event.pop("kind"), target=target.id, **event)
        state.events.clear()
        if finish and target.hp <= 0 and target.alive:
            self.battle.finish_enemy(target)

    def tick(self):
        for key, state in tuple(self.receivers.items()):
            target = self.battle.live.get(key)
            if target is None:
                del self.receivers[key]
                continue
            state.hp = target.hp
            state.tick(self.battle.frame)
            self._settle(target, state)

    def enemy_atk_scale(self, target):
        state = self.receivers.get(target.id)
        return ONE + state.atk_ratio if state else ONE

    def projectile_scale(self, pos):
        actor = self.battle.operators.get(LOGOS)
        if actor and actor.handler and actor.handler.field and actor.handler.contains(pos):
            return actor.handler.board["projectile_move_scale"]
        return 1


@dataclass
class _Shot:
    target: object
    position: tuple
    offset: tuple
    first: int
    curve: bool
    speed: float
    talent: bool
    cached: int


class LogosCombat:
    def __init__(self, battle, actor):
        self.b, self.actor, self.card = battle, actor, actor.card
        if actor.uid != LOGOS or self.card["skill"] not in (2, 3):
            raise ValueError("全息逻各斯入口支持二、三技能")
        self.lock = lock_definition(battle.tables, self.card) if self.card["skill"] == 2 else None
        self.ev = self.lock.evidence if self.lock else caster_prefab_evidence(battle.tables, LOGOS, 3,
            skill_level=self.card["skill_level"], module_id=self.card["module"], module_level=self.card["module_level"])
        self.board = {r["key"]: r["value"] for r in self.ev["skill_blackboard"]}
        self.talent = _board(self.card, 0, "11_root") or _board(self.card, 0, "1")
        self.trait = _trait_values(self.card)
        self.hit_sp = int(self.trait.get("sp", 0))
        self.pen = _raw_f32(self.trait.get("magic_resist_penetrate_fixed", 0))
        self.normal = default_attack(battle.tables, LOGOS)["fields"]
        parts = self.ev["character"]["components"]
        keys = [self.normal["_projectileKey"], "projectile_chr_logos_t"]
        if self.lock:
            self.lock_target = self.lock_due = None
            self.stacks = 0
            self.in_attack = False
            self.focus = RecoveryTimer(self.lock.focus_poll_q32)
        else:
            self.skill, = (c["fields"] for c in parts if c["path"] == "Modes/S3/Body/Attack" and c["script"] == "RangedAttack")
            replace, = (c["fields"] for c in parts if c["path"] == "Modes/S3" and c["script"] == "SimpleUnitAnimatorHooker")
            self.skill_anim = next(r["toAnimKey"] for r in replace["_replaceAnimPairs"] if r["fromAnimKey"] == "Attack")
            keys.append(self.skill["_projectileKey"])
            end, = (r["fields"] for r in parts if r["script"] == "EliminateProjectileAbility")
        self.movers = {}
        for key in keys:
            components = self.ev["projectiles"][key]["logic"]["components"]
            mover, = (c for c in components if c["script"] in ("ParacurveMovement", "AdvancedMovement"))
            self.movers[key] = mover["script"] == "ParacurveMovement", mover["fields"]["_speed"]
            if key == "projectile_chr_logos_t":
                self.delay = next(c["fields"]["_randomDelayToBorn"] for c in components if c["script"] == "SimpleProjectile")
        self.face = "Back" if actor.direction == "UP" else "Front"
        self.sign = -1 if actor.direction == "LEFT" else 1
        tile = battle.tiles[actor.row, actor.col]
        self.place = dict(actor.place, tile_z=tile["position"]["z"] - tile["locate_height_offset"])
        self.base_cells = range_cells(self.card["base_range"], actor.place, battle.rows, battle.cols)
        self.skill_cells = self.base_cells if self.lock else range_cells(self.card["skill_range"], actor.place, battle.rows, battle.cols)
        self.gauge, _ = deploy_skill(battle.tables, self.card, actor.deployed_frame)
        self.timing = run_committed_cast(battle.tables, self.ev, horizon=0, facing=self.face,
                                         direction=actor.direction)["natural_end"]
        if not self.lock:
            clip, _ = _selected_clip(battle.tables, self.ev, end["_animKey"], self.face)
            self.clear_delay, _ = attack_key_clock(battle.tables, self.ev, self.face, end["_animKey"], clip["duration"])
            self.restore_delay = fixed_wait_frames(single(clip["duration"] / battle.dt))
        self.born_end = actor.deployed_frame + _birth(battle.tables, self.card, self.ev, self.face)["born_exit_update"]
        self.poll = self.born_end + 1
        self.recover = actor.deployed_frame + 1
        self.active = self.closed = self.field = False
        self.commit = self.mode = self.end = self.clear = self.restore = self.pending = None
        self.index = self.mount = 0
        self.shots, self.clocks, self.noted = [], {}, set()

    def _log(self, kind, **fields):
        self.b.log(kind, operator_id=LOGOS, **fields)

    def contains(self, pos):
        return circle_in_range(pos[1], pos[0], 0, self.skill_cells)

    def _enemy(self, target):
        def note(rule):
            if (target.key, rule) not in self.noted:
                self.noted.add((target.key, rule))
                self._log("rule_applied", target=target.id, rule=rule)
        return dict(id=target.id, prefab=target.prefab, airborne=target.airborne, row=target.row, col=target.col,
                    tile_z=0, receiver_mount_rule=note)

    def _attack(self):
        return self.b.attack(self.actor, self.board.get("atk", 0) if self.active else 0)

    def defenses(self):
        panel = self.card["panel_attributes"]
        bonus = self.board["magic_resistance"] if self.active and self.lock else 0
        return panel["def"], panel["magicResistance"] + bonus

    def move_multiplier(self, enemy):
        if self.active and self.lock and enemy is self.lock_target:
            return (ONE + self.stacks * _raw_f32(self.board["attack@move_speed"])) / ONE
        return 1

    def _clear_lock(self):
        self.lock_target, self.stacks = None, 0

    def _search(self):
        targets = self.b.targets(self.actor, self.skill_cells if self.active else self.base_cells)
        frame = self.b.frame
        if self.active and self.lock:
            self.in_attack = bool(targets)
            if targets:
                target = next((t for t in targets if t is self.lock_target), targets[0])
                if target.col != self.actor.col:
                    self.sign = 1 if target.col > self.actor.col else -1
                self.lock_due = frame + self.lock.wait - 1, target
                self.poll = frame + self.lock.period
                self._log("lock_attack_start", target=target.id, hit_frame=self.lock_due[0])
            else:
                self.poll = frame + 3
            return
        if not targets:
            self.poll = frame + 3
            return
        targets = targets[:int(self.board["attack@max_target"]) if self.active else 1]
        if targets[0].col != self.actor.col:
            self.sign = 1 if targets[0].col > self.actor.col else -1
        action = self.skill_anim if self.active else ("Attack", "Attack_2")[self.index % 2]
        self.index += not self.active
        interval = single(self.card["panel_attributes"]["baseAttackTime"] * 100 / self.b.attack_speed(self.actor))
        cache = (action, interval)
        if cache not in self.clocks:
            self.clocks[cache] = attack_key_clock(self.b.tables, self.ev, self.face, action, interval)
        delay, clock = self.clocks[cache]
        self.pending = frame + delay, targets, clock, self.skill if self.active else self.normal
        self.poll = frame + fixed_wait_frames(single(interval / self.b.dt))
        self._log("logos_attack_start", targets=[t.id for t in targets], release_frame=frame + delay, action=action)

    def _append(self, target, origin, dest, first, key, talent):
        curve, speed = self.movers[key]
        offset = dest[0] - target.col, dest[1] - target.row, dest[2]
        self.shots.append(_Shot(target, origin[:2] if curve else origin, offset, first, curve,
                                speed, talent, self._attack()))

    def _talent(self):
        stream, frame = self.b.rng, self.b.frame
        success = stream.dice(self.talent["prob"])
        self._log("first_talent_dice", triggered=success)
        if not success:
            return
        rows = sorted(self.b.targets(self.actor, self.skill_cells if self.active else self.base_cells),
                      key=lambda t: t.order)
        if not rows:
            return
        for _ in range(int(self.talent.get("emit_count", 1))):
            target, = stream.select_targets(rows, 1)
            draw = dict(mount=stream.range_int(0, 6), birth_delay_seconds=single(stream.value() * self.delay))
            event = dict(placement=self.place, view=self.face, enemy=self._enemy(target))
            key, origin, dest, first, _ = talent_launch(self.b.tables, self.card, self.ev, event, frame, draw,
                                                        caster_face_sign=self.sign)
            self._append(target, origin, dest, first, key, True)
            self._log("logos_talent_release", target=target.id, mount=draw["mount"], first_move_frame=first)

    def _release(self):
        _, targets, clock, ability = self.pending
        self.pending = None
        for target in targets:
            if not target.alive or target.cursor.hidden:
                continue
            mount = 2
            if ability["_useMountGroup"]:
                points = ability["_mountGroup"]["mountPoints"]
                mount = points[self.mount % len(points)]
                self.mount += 1
            field = "muzzlaTransform" if mount == 2 else "specialPoint" + str(mount - 6)
            origin, dest = stationary_cast_mounts(self.b.tables, LOGOS, clock, self.place,
                self._enemy(target), self.face, mount=mount, field=field, caster_face_sign=self.sign)
            self._append(target, origin, dest, self.b.frame + 1, ability["_projectileKey"], False)
            self._log("logos_projectile_release", target=target.id, mount=mount)
            self._talent()

    def _advance(self, shot):
        t = shot.target
        if not t.alive or t.cursor.hidden:
            return False
        if self.b.frame < shot.first:
            return True
        goal = t.col + shot.offset[0], t.row + shot.offset[1], shot.offset[2]
        if shot.curve:
            shot.position, hit = paracurve_horizontal_step(shot.position, goal, shot.speed, self.b.dt, True)
        else:
            step = ordinary_trace_step(shot.position, goal, effective_dt=self.b.dt, round_position=True, speed=shot.speed)
            shot.position, hit = step.position, step.reached
        if not hit:
            return True
        attack = shot.cached if self.closed else self._attack()
        self.b.arts(self.actor, t, attack, pen_raw=self.pen,
                    scale_raw=_raw_f32(self.talent["atk_scale"]) if shot.talent else ONE,
                    kind="logos_talent_hit" if shot.talent else "logos_ordinary_hit")
        if shot.talent and t.alive:
            if "element_atk_scale" in self.talent:
                self.b.logos.talent_element(self.actor, t, mul(attack, _raw_f32(self.talent["element_atk_scale"])))
            if t.alive:
                ticks = fixed_wait_frames(single(self.talent["sluggish"] / self.b.dt))
                self.b.slow(t, self.sluggish, self.b.frame + ticks + 1)
        if not self.closed and self.b.frame >= self.recover and t.level in ("ELITE", "BOSS"):
            sp = self.gauge.state
            sp.points = min(sp.maximum, sp.points + self.hit_sp)
        return False

    @property
    def sluggish(self):
        modifier, = self.b.tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"]
        return abs(modifier["value"])

    def objects(self):
        f = self.b.frame
        if not self.closed and f >= self.recover:
            self.gauge.state.advance(mul(self.gauge.step, _raw_f32(self.b.sp_rate(self.actor))))
        if f == self.commit:
            self.gauge.state.points -= self.gauge.state.cost
        if f == self.mode:
            self.active, self.field = True, self.lock is None
            if self.lock:
                self.focus = RecoveryTimer(self.lock.focus_poll_q32)
            self.poll = f + 1
        self.shots = [shot for shot in self.shots if self._advance(shot)]
        if self.closed:
            return
        if f == self.end:
            self.active = False
            self.pending = self.poll = None
            if self.lock:
                self._clear_lock()
                self.in_attack, self.lock_due, self.poll = False, None, f + 1
            else:
                self.clear = f + 1 + self.clear_delay
                self.restore = f + 1 + self.restore_delay
        if f == self.clear:
            before = self.b.hostile.flights
            cleared = [shot for shot in before if self.contains(shot[4])]
            self.b.hostile.flights = [shot for shot in before if not self.contains(shot[4])]
            self._log("projectile_elimination", cleared=[shot[1].id for shot in cleared])
            self.field = False
        if f == self.restore:
            self.poll = f + 1
        if f == self.poll:
            self._search()
        if self.active and self.lock and f > self.mode and self.focus.advance(int(self.b.dt * ONE)) and not self.in_attack:
            self._clear_lock()

    def coroutines(self):
        if self.lock and self.lock_due and self.b.frame == self.lock_due[0]:
            _, target = self.lock_due
            self.lock_due = None
            if target.alive and not target.cursor.hidden:
                if self.lock_target is not target:
                    self.lock_target, self.stacks = target, 0
                self.b.arts(self.actor, target, self._attack(), pen_raw=self.pen,
                            scale_raw=self.lock.base_scale + self.stacks * self.lock.scale_step, kind="logos_lock_hit")
                self.stacks = min(self.stacks + 1, int(self.board["attack@max_stack_cnt"]))
                if not target.alive:
                    self._clear_lock()
                self._talent()
        if self.pending and self.b.frame == self.pending[0]:
            self._release()

    def press(self):
        f = self.b.frame
        if self.closed or f < self.born_end or self.end is not None and f <= self.end:
            self._log("skill_button_rejected", reason="inactive_or_busy")
            return False
        cast = resolve_cast_start(self.b.tables, self.ev, frame=f, phase="after_tick", facing=self.face,
            direction=self.actor.direction, sp=self.gauge.state.points, owner_state="attack" if self.pending else "idle")
        if cast["spell_frame"] is None:
            self._log("skill_button_rejected", reason=cast["status"])
            return False
        self.commit, self.mode = cast["cast_frame"], cast["spell_frame"]
        self.b.skill_started(self.actor, self.commit)
        if self.commit == f:
            self.gauge.state.points = cast["sp_after"]
        self.end = (self.mode + fixed_wait_frames(single(single(self.lock.duration) / self.b.dt)) if self.lock
                    else self.commit + self.timing["end"] + 1)
        self.recover = self.commit + self.timing["first_recovery_tick"]
        self.poll = self.pending = None
        self._log("skill_pressed", skill=self.card["skill"], mode_frame=self.mode, end_frame=self.end)
        return True

    def close(self):
        self.closed = True
        self.active = self.field = False
        if self.lock:
            self._clear_lock()
            self.lock_due = None
        self.commit = self.mode = self.end = self.clear = self.restore = self.pending = self.poll = None

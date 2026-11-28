"""Mantra's S3 field, entry listener and paralysis on the shared H5-1 battlefield."""
from math import floor
from types import SimpleNamespace

from .attack_range import range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_model import _assets
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .chain_targets import nearest_chain_target
from .elemental_casters import _board
from .elemental_projectiles import ranged_shot_release, stable_enemy_mount, stationary_cast_mounts
from .fixed import ONE, mul
from .mantra_battle import burst_output_scale
from .mantra_stage import entry_talent, palsy_delay
from .marcil_deploy import _birth
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer, _divide, deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class MantraCombat:
    def __init__(self, battle, actor):
        self.b, self.a, self.card = battle, actor, actor.card
        t = battle.tables
        self.ev = _assets(t, self.card)
        self.board = {r["key"]: r["value"] for r in self.ev["skill_blackboard"]}
        self.talent = _board(self.card, 0, "1")
        self.module = burst_output_scale(self.card)
        parts = self.ev["character"]["components"]
        self.normal = default_attack(t, actor.uid)["fields"]
        self.skill, = (c["fields"] for c in parts if c["path"] == "Modes/S3/Body/Attack" and c["script"] == "RangedAttack")
        self.speeds = {}
        for action in (self.normal, self.skill):
            key = action["_projectileKey"]
            self.speeds[key], = (c["fields"]["_speed"] for c in self.ev["projectiles"][key]["logic"]["components"]
                                 if c["script"] == "AdvancedMovement")
        buff, = (b for r in self.ev["skill"]["components"] if r["script"] == "ActiveBuffAbility"
                 for b in r["fields"]["_buffs"] if b["templateKey"] == "mantra_s_3[self]")
        self.first_delay = buff["firstTriggerInterval"]
        flush = self.ev["templates"]["mantra_s_3[enemy_trigger]"]["eventToActions"]["ON_BUFF_FINISH"][0]["_succeedNodes"][-1]
        overflow_key = flush["_loopBody"][0]["_projectileKey"]
        over = self.ev["projectiles"][overflow_key]["base"]["components"]
        self.bounce, = (c["fields"] for c in over if c["script"] == "BouncedAdvancedMovement")
        self.radius, = (c["fields"]["m_Radius"] for c in over if c["type"] == "CircleCollider2D")
        aura, = (c["fields"] for c in parts if c["script"] == "GlobalAuraAbility" and c["path"] == "Modes/Default/Body/Talents/1")
        self.aura = RecoveryTimer(_raw_f32(aura["_interval"]))
        self.aura.remaining = 0
        self.listeners = set()
        self.contacts = {}
        self.entry = entry_talent(t, self.card, self.ev, SimpleNamespace(scene=battle.scene, history={}), actor.place, stream=battle.rng)
        battle.log("mantra_entry_tile", operator_id=actor.uid, tile=self.entry["selected_start_tile"])
        self.delay = palsy_delay(t)
        graph = t.read("battle/buff_template_data.json")["palsy[stack]"]["eventToActions"]
        control = t.read("buff_table.json")[graph["ON_BUFF_TRIGGER"][2]["_buffKey"]]
        seconds, = (v["value"] for v in control["blackboard"] if v["key"] == control["durationKey"])
        self.control = fixed_wait_frames(seconds / battle.dt) + 1
        self.base, self.wide = (range_cells(self.card[k], actor.place, battle.rows, battle.cols) for k in ("base_range", "skill_range"))
        self.face = "Back" if actor.direction == "UP" else "Front"
        self.sign = -1 if actor.direction == "LEFT" else 1
        self.step = _raw_f32(battle.dt)
        self.clock = run_committed_cast(t, self.ev, horizon=0, facing=self.face, direction=actor.direction)["natural_end"]
        self.gauge, _ = deploy_skill(t, self.card, battle.frame)
        self.born = battle.frame + _birth(t, self.card, self.ev, self.face)["born_exit_update"]
        self.poll, self.recover = self.born + 1, battle.frame + 1
        self.members, self.notes = {}, set()
        self.mode = self.commit = self.end = self.pending = self.grant_timer = None
        self.active = self.closed = False
        self.flights = []
        self.grants = 0
        battle.mantra = self

    def state(self, e):
        s = self.b.logos.receiver(e)
        s.hp = e.hp
        return s

    def atk(self):
        return self.b.attack(self.a, self.board["atk"] if self.active else 0)

    def row(self, e):
        def note(rule):
            if (e.key, rule) not in self.notes:
                self.notes.add((e.key, rule))
                self.b.log("rule_applied", rule=rule, target=e.id)
        return dict(id=e.id, row=e.row, col=e.col, tile_z=0, airborne=e.airborne, prefab=e.prefab, receiver_mount_rule=note)

    def grant(self, actor=None):
        if not self.active or self.grants >= int(self.board["max_trigger_cnt"]):
            return
        if actor is not None and (actor is self.a or (actor.row, actor.col) not in self.wide):
            return
        selected = [e for e in self.b.live.values() if e.id in self.members and
                    (floor(e.row+.5), floor(e.col+.5)) in self.wide]
        if selected:
            self.grants += 1
            for e in selected:
                s = self.state(e)
                s.add_palsy(self.b.frame, int(self.board["per_active"]), "mantra_s_3")
                self.b.logos._settle(e, s)
            self.b.log("mantra_palsy_grant", source=actor.uid if actor else self.a.uid,
                       grants=self.grants, targets=[e.id for e in selected])

    def can_interrupt(self, e):
        s = self.b.logos.receivers.get(e.id)
        return bool(s and s.palsy_stacks and not s.palsy_immune)

    def interrupt(self, e):
        if not self.can_interrupt(e):
            return False
        s = self.state(e)
        aura = not self.closed and e.id in self.listeners
        s.palsy_stacks -= int(not aura or self.b.rng.dice(1 - self.talent["prob"]))
        e.stun_until = max(e.stun_until, self.b.frame + self.control)
        if aura:
            s.apply_elemental_hp(self.b.frame, mul(self.atk(), _raw_f32(self.talent["atk_scale"])),
                                 source="mantra_t_1", output_scale_q32=self.module if s.burst else ONE)
            self.b.logos._settle(e, s)
        self.b.log("mantra_attack_interrupted", target=e.id, stacks=s.palsy_stacks, control_until=e.stun_until)
        return True

    def _entry_tick(self):
        f = self.b.frame
        origin = self.entry["selected_start_tile"]
        if not self.closed:
            for e in self.b.live.values():
                if f != e.attack_ready_from or e.cursor.hidden or e.attr("palsyImmune"):
                    continue
                start = self.b.scene.level["routes"][e.spawn["route_index"]]["startPosition"]
                if (start["row"], start["col"]) == origin == (floor(e.row+.5), floor(e.col+.5)):
                    self.contacts[e.id] = RecoveryTimer(_raw_f32(self.entry["first_trigger_interval_seconds"]))
        for key, timer in tuple(self.contacts.items()):
            if timer.advance(self.step):
                del self.contacts[key]
                e = self.b.live.get(key)
                if e and not e.cursor.hidden:
                    s = self.state(e)
                    s.add_palsy(f, 1, "mantra_t_2")
                    self.b.logos._settle(e, s)

    def _overflow(self, e, s):
        s.palsy_overflow -= 1
        self.flights.append(dict(e=e, position=(e.col, e.row, 0.), speed=self.bounce["_speed"],
                                 born=self.b.frame, overflow=True, jump=False, power=self.atk()))
        self.b.log("mantra_overflow_release", target=e.id)

    def _field(self):
        live = {e.id: e for e in self.b.targets(self.a, self.wide)} if self.active else {}
        for key in tuple(self.members):
            if key not in live:
                e, _, _ = self.members.pop(key)
                s = self.b.logos.receivers.get(key)
                if s:
                    while s.palsy_overflow:
                        self._overflow(e, s)
                    s.limit_palsy(None)
        for key, e in live.items():
            if key not in self.members:
                self.state(e).limit_palsy(int(self.board["max_target"]))
                self.members[key] = [e, RecoveryTimer(_raw_f32(.1)), None]
        if self.grant_timer and self.b.frame > self.mode and self.grant_timer.advance(self.step):
            self.grant()
            self.grant_timer = None
        for e, timer, elapsed in tuple(self.members.values()):
            if timer.advance(self.step):
                if elapsed is None or elapsed > self.board["interval_projectile_trigger"]:
                    elapsed = None
                    s = self.state(e)
                    if s.palsy_overflow:
                        self._overflow(e, s)
                        elapsed = 0.
                else:
                    elapsed = single(elapsed + single(.1))
                self.members[e.id][2] = elapsed

    def _flights(self):
        f = self.b.frame
        for shot in tuple(self.flights):
            if f <= shot["born"]:
                continue
            e = shot["e"]
            live = e.alive and not e.cursor.hidden
            if live:
                move = ordinary_trace_step(shot["position"], stable_enemy_mount(self.b.tables, self.row(e)),
                                           effective_dt=self.b.dt, round_position=True, speed=shot["speed"])
                shot["position"] = move.position
                if not move.reached:
                    continue
                s = self.state(e)
                attack = shot["power"] if self.closed else self.atk()
                scale = self.module if s.burst else ONE
                if shot["overflow"]:
                    s.apply_elemental_hp(f, mul(attack, _raw_f32(self.board["atk_scale"])), source="mantra_s_3_overflow", output_scale_q32=scale)
                    self.b.logos._settle(e, s)
                else:
                    self.b.arts(self.a, e, attack, output_raw=scale, kind="mantra_attack_hit")
            if shot["overflow"] and not shot["jump"] and int(self.board["chain_times"]):
                rows = [self.row(t) for t in self.b.live.values() if not t.cursor.hidden]
                origin = dict(col=shot["position"][0], row=shot["position"][1])
                other = nearest_chain_target(f, origin, rows, {k:t.radius for k,t in self.b.live.items()},
                        [e.id], self.radius, max_candidates=128, continuous=True,
                        nearest_tie=lambda keys: min(keys, key=lambda k:self.b.live[k].priority()))
                if other is not None:
                    shot.update(e=self.b.live[other], speed=self.bounce["_speedAfterFirstReach"], jump=True)
                    self.b.log("mantra_overflow_bounce", source=e.id, target=other)
                    continue
            self.flights.remove(shot)

    def objects(self):
        f = self.b.frame
        self._entry_tick()
        if self.closed:
            self._flights()
            return
        if f == self.commit:
            self.gauge.state.points -= self.gauge.state.cost
            self.commit = None
        if f == self.mode:
            self.active = True
            self.grants = 0
            self.grant_timer = RecoveryTimer(_raw_f32(self.first_delay))
            self.aura.remaining = 0
            self.poll = f + 1
        if f == self.end:
            self.active = False
            self.aura.remaining = 0
            self.mode = self.end = self.pending = self.grant_timer = None
            self.poll = f + 1
        if f >= self.recover:
            set_period(self.gauge.state, _divide(ONE, _raw_f32(self.b.sp_rate(self.a))), stopped=False)
            self.gauge.state.advance(self.gauge.step)
        if self.aura.advance(self.step):
            self.listeners = {k for k,e in self.b.live.items() if not e.cursor.hidden}
        if self.active or self.members:
            self._field()
        self._flights()
        if f == self.poll:
            rows = self.b.targets(self.a, self.wide if self.active else self.base)
            if not rows:
                self.poll += 3
                return
            e = rows[0]
            if e.col != self.a.col:
                self.sign = 1 if e.col > self.a.col else -1
            action = self.skill if self.active else self.normal
            period = single(self.card["panel_attributes"]["baseAttackTime"] * 100 / self.b.attack_speed(self.a))
            shot = ranged_shot_release(self.b.tables, self.ev, action, attack_frame=f, attack_interval=period,
                                       facing=self.face, mode_path="Modes/S3" if self.active else None)
            self.pending = shot, action, e, self.sign
            self.poll = f + fixed_wait_frames(single(period / self.b.dt))

    def coroutines(self):
        if not self.pending or self.pending[0]["release_frame"] != self.b.frame:
            return
        shot, action, e, sign = self.pending
        self.pending = None
        targets = self.b.targets(self.a, self.wide)[:int(self.board["attack@max_target"])] if action is self.skill else [e]
        for t in targets:
            if t.alive and not t.cursor.hidden:
                origin, _ = stationary_cast_mounts(self.b.tables, self.a.uid, shot["clock"], self.a.place, self.row(t), self.face, caster_face_sign=sign)
                self.flights.append(dict(e=t, position=origin, speed=self.speeds[action["_projectileKey"]],
                                         born=self.b.frame, overflow=False, jump=False, power=self.atk()))

    def press(self):
        f = self.b.frame
        state = "skill" if self.mode is not None else "born" if f < self.born else "attack" if self.pending else "idle"
        cast = resolve_cast_start(self.b.tables, self.ev, frame=f, phase="after_tick", facing=self.face,
                                  direction=self.a.direction, owner_state=state, sp=self.gauge.state.points)
        if cast["spell_frame"] is None or self.mode is not None:
            self.b.log("skill_rejected", operator_id=self.a.uid, reason=cast["status"])
            return
        at = cast["cast_frame"]
        if at == f:
            self.gauge.state.points = cast["sp_after"]
        else:
            self.commit = at
        self.mode = cast["spell_frame"]
        self.end, self.recover = at + self.clock["end"], at + self.clock["first_recovery_tick"]
        self.poll = self.pending = None
        self.b.log("skill_pressed", operator_id=self.a.uid, skill=3, mode_frame=self.mode, end_frame=self.end)

    def close(self):
        self.closed = True
        self.active = False
        self.listeners.clear()
        self.pending = self.grant_timer = None
        self._field()

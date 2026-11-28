"""Mostima S3's attacks, knockback and two auras on the shared Recal battle clock."""
from math import hypot

from .area_caster_skills import MOSTIMA, execute_area_skill
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_first_spell import attack_key_clock
from .caster_model import _assets
from .caster_modules import splash_range_module
from .caster_start import resolve_cast_start
from .fixed import ONE
from .marcil_deploy import _birth
from .mostima_aura import aura_profile, set_period
from .mystic_timing import fixed_wait_frames
from .skill_recovery import _divide, deploy_skill
from .volley_damage import _raw_f32


class MostimaCombat:
    def __init__(self, battle, actor):
        self.battle, self.actor = battle, actor
        self.card = actor.card
        if actor.uid != MOSTIMA or self.card["skill"] != 3:
            raise ValueError("全息时序之匙入口需要莫斯提马三技能")
        tables = battle.tables
        self.evidence = _assets(tables, self.card)
        evidence = self.evidence
        self.board = {e["key"]: e["value"] for e in evidence["skill_blackboard"]}
        self.face = "Back" if actor.direction == "UP" else "Front"
        self.clock = run_committed_cast(tables, evidence, horizon=round(self.card["skill_row"]["duration"]/battle.dt)+120,
                                        facing=self.face, direction=actor.direction)
        self.born_end = actor.deployed_frame + _birth(tables, self.card, evidence, self.face)["born_exit_update"]
        components = evidence["character"]["components"]
        attacks = [next(c["fields"] for c in components if c["path"] == f"Modes/{mode}/Body/Attack"
                        and c["script"] == "MeleeAttack") for mode in ("Default", "S2")]
        self.animations = [a["_animKey"] for a in attacks]
        selector = next(c for c in components if c["id"] == attacks[0]["_selector"]["m_PathID"])
        self.splash = next(c["fields"]["m_Radius"] for c in components
                           if c["path"] == selector["path"] and c["script"] == "CircleCollider2D")
        base_range = self.card["base_range"]
        if self.card["module_info"] and self.card["module_info"]["typeIcon"] == "spc-x":
            base_range = tables.read("excel/range_table.json")[splash_range_module(tables, self.card)["range_id"]]
        self.ranges = [range_cells(shape, actor.place, battle.rows, battle.cols)
                       for shape in (base_range, self.card["skill_range"] or base_range)]
        self.map_cells = [(r,c) for r in range(battle.rows) for c in range(battle.cols)]
        self.sp_profile = aura_profile(tables, self.card)
        self.multipliers = {}
        for boosted in (False, True):
            for inside in (False, True):
                sample = execute_area_skill(tables, self.card, dict(kind="slow_aura", frame=actor.deployed_frame,
                                            in_range=inside, skill_affecting=boosted))["result"]
                self.multipliers[inside, boosted] = 1 + sample["move_speed_ratio_delta"]
        self.characters = tables.read("excel/character_table.json")
        base = self.characters[MOSTIMA]["phases"][self.card["elite"]]["attributesKeyFrames"][-1]["data"]
        self.force = int(base["baseForceLevel"] + self.board["attack@force"])
        self.gauge, _ = deploy_skill(tables, self.card, actor.deployed_frame)
        self.poll = self.born_end + 1
        self.recover_from = actor.deployed_frame + 1
        self.attack_clocks = {}
        self.pending = self.cooldown = None
        self.mode = self.finish = None
        self.active = self.closed = False

    def _log(self, kind, **fields):
        self.battle.log(kind, operator_id=self.actor.uid, **fields)

    def move_multiplier(self, enemy):
        if self.closed or not self.actor.alive:
            return 1.
        inside = circle_in_range(enemy.row, enemy.col, enemy.radius, self.ranges[self.active], box_query=True)
        return self.multipliers[inside, self.active]

    def sp_bonus(self, ally):
        if self.closed or not self.actor.alive or not ally.alive or self.characters[ally.uid]["profession"] != "CASTER":
            return 0
        return self.sp_profile["owner" if ally is self.actor else "ally"]

    def _timing(self):
        speed = self.battle.attack_speed(self.actor)
        key = (self.active, speed)
        if key not in self.attack_clocks:
            base = self.card["panel_attributes"]["baseAttackTime"]
            interval = single(single(base + (self.board["base_attack_time"] if self.active else 0))*100/single(speed))
            delay, _ = attack_key_clock(self.battle.tables, self.evidence, self.face, self.animations[self.active], interval)
            self.attack_clocks[key] = delay, fixed_wait_frames(single(interval / self.battle.dt))
        return self.attack_clocks[key]

    def _search(self):
        frame = self.battle.frame
        rows = self.battle.targets(self.actor, self.ranges[self.active])
        if not rows:
            self.poll = frame + 3
            self.cooldown = None
            return
        delay, period = self._timing()
        self.pending = (frame + delay, rows[0], self.active)
        self.poll = frame + period
        self.cooldown = (period, frame + delay)
        self._log("mostima_attack", target=rows[0].id, release_frame=frame+delay, skill_active=self.active)

    def objects(self):
        if self.closed:
            return
        frame = self.battle.frame
        rate = self.battle.sp_rate(self.actor)
        set_period(self.gauge.state, _divide(ONE,_raw_f32(rate)) if rate>0 else None, stopped=frame<self.recover_from)
        if frame >= self.recover_from:
            self.gauge.state.advance(self.gauge.step)
        if self.cooldown and self.poll >= frame:
            old, release = self.cooldown
            _, period = self._timing()
            if old != period:
                remaining = self.poll - (frame-1)
                self.poll = max(release+1, frame-1 + (remaining*period+old-1)//old)
                self.cooldown = period, release
        if frame == self.poll and frame not in (self.mode, self.finish):
            self._search()

    def _impact(self, primary, boosted):
        battle = self.battle
        if boosted:
            # ON_SPELL reselects the expanded box and keeps a still-live input that has walked out.
            rows = battle.targets(self.actor, self.ranges[True])
            if primary.alive and all(t.id != primary.id for t in rows):
                rows = [*rows, primary]
        else:
            if not primary.alive:
                self._log("mostima_target_lost", target=primary.id)
                self._search()
                return
            rows = [t for t in battle.targets(self.actor, self.map_cells)
                    if t is primary or hypot(t.row-primary.row, t.col-primary.col) <= self.splash + t.radius]
        attack = battle.attack(self.actor, self.board["atk"] if boosted else 0)
        for enemy in sorted(rows, key=lambda e: (e.birth,e.order)):
            if enemy.alive:
                battle.arts(self.actor, enemy, attack, kind="mostima_key_hit" if boosted else "mostima_ordinary_hit")
                if boosted and enemy.alive and not enemy.airborne:
                    battle.push(self.actor, enemy, self.force)

    def coroutines(self):
        if self.closed:
            return
        frame = self.battle.frame
        if self.mode == frame or self.finish == frame:
            self.active = self.mode == frame
            self._log("mostima_key_mode" if self.active else "mostima_key_ended")
            if not self.active:
                self.mode = self.finish = None
            self.poll = frame + 1
            self.pending = self.cooldown = None
        if self.pending and self.pending[0] == frame:
            _, primary, boosted = self.pending
            self.pending = None
            self._impact(primary, boosted)

    def press(self):
        frame = self.battle.frame
        if self.closed or self.mode is not None:
            self._log("skill_button_rejected", reason="inactive_or_busy")
            return False
        state = "born" if frame < self.born_end else "attack" if self.pending else "idle"
        cast = resolve_cast_start(self.battle.tables,self.evidence,frame=frame,phase="after_tick",facing=self.face,
                                  direction=self.actor.direction,owner_state=state,sp=self.gauge.state.points)
        if cast["spell_frame"] is None:
            self._log("skill_button_rejected", reason=cast["status"], sp=self.gauge.state.points)
            return False
        self.gauge.state.points = cast["sp_after"]
        self.mode = cast["spell_frame"]
        self.battle.skill_started(self.actor,cast["cast_frame"])
        self.finish = cast["cast_frame"] + self.clock["end_frame"]
        self.recover_from = cast["cast_frame"] + self.clock["first_unblocked_recovery_tick"]
        self.poll = self.mode+1
        self.pending = self.cooldown = None
        self._log("skill_pressed", skill=3, mode_frame=self.mode, expire_frame=self.finish)
        return True

    def close(self):
        self.active = False
        self.closed = True
        self.pending = self.cooldown = None

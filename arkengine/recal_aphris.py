"""Run Aphris's existing cast, channel, magazine and relay clocks on the live Recal board."""
from types import SimpleNamespace

from .aphris_magazine import aphris_magazine_profile
from .aphris_relays import AphrisRelays
from .aphris_stage import AphrisCombat as StageAphrisCombat, Beam
from .caster_prefabs import caster_prefab_evidence
from .fixed import ONE
from .mostima_aura import set_period
from .skill_recovery import _divide
from .volley_damage import _raw_f32


class _TargetView:
    """Expose the current spell's receiver positions to the existing bend-delay scheduler."""

    def __init__(self, battle, actor):
        self.battle = battle
        self.actor = actor
        self.wave = self
        self.positions = {}
        self.receivers = {}

    def targets(self, frame, cells, *, search=False):
        targets = self.battle.targets(self.actor, cells, tile_only=True)
        self.receivers.update((target.id, target) for target in targets)
        self.positions = {frame: {t.id: (t.row, t.col) for t in targets}}
        return [target.id for target in targets]


class _EventSink:
    def __init__(self, battle, actor):
        self.battle = battle
        self.actor = actor

    def append(self, event):
        fields = dict(event)
        kind = fields.pop("kind")
        frame = fields.pop("frame")
        if frame != self.battle.frame:
            raise ValueError("谬因回放事件必须由当前战斗帧提交")
        fields.setdefault("operator_id", self.actor.uid)
        self.battle.log(kind, **fields)

    def extend(self, events):
        for event in events:
            self.append(event)


class AphrisCombat(StageAphrisCombat):
    def __init__(self, battle, actor):
        if actor.card["skill"] not in (2, 3):
            raise ValueError("谬因整局回放使用二或三技能")
        self.arena = battle
        self.actor = actor
        card = actor.card
        evidence = caster_prefab_evidence(battle.tables, actor.uid, card["skill"],
            skill_level=card["skill_level"], module_id=card["module"], module_level=card["module_level"])
        context = SimpleNamespace(tables=battle.tables, scene=battle.scene, card=card,
                                  deploy=actor.place, evidence=evidence)
        field = SimpleNamespace(card=AphrisRelays(battle.tables, card, evidence))
        beam = Beam(context, field)
        super().__init__(context, {"end_frame": None}, field, _TargetView(battle, actor),
                         beam, _EventSink(battle, actor))
        self.relay_event_index = 0
        self.closed = False
        speed = card["panel_attributes"]["attackSpeed"]
        self.speed_profiles = {speed: dict(self.profile)}
        self.last_speed = speed
        self.last_attack = None
        self.slow_ratio = abs(battle.tables.read("buff_table.json")["sluggish"]["attributes"]
                              ["attributeModifiers"][0]["value"])

    def _refresh_allies(self):
        self.beam.present = {uid: ally.place for uid, ally in self.arena.operators.items() if ally.alive}
        if self.closed:
            self.beam.present.pop(self.actor.uid, None)

    def _relay_events(self):
        events = self.field.card.events
        for event in events[self.relay_event_index:]:
            self.events.append(event)
        self.relay_event_index = len(events)

    def active_relay_cells(self):
        return {(r["row"], r["col"]) for r in self.field.card.active.values()}

    def penetration_at(self, ally):
        if self.closed or not self.actor.alive or not ally.alive:
            return 0
        self._refresh_allies()
        return self.beam.penetration_for(ally.uid)

    def relay(self, action):
        frame = self.arena.frame
        card = self.field.card
        key = action["relay"]
        if self.closed or not self.actor.alive:
            card.record(frame, action["kind"] + "_rejected", relay=key, reason="host_not_deployed")
        elif action["kind"] == "relay_withdraw":
            card.withdraw(frame, key, "manual")
        elif action["kind"] == "relay_deploy":
            self.arena.dp = card.deploy(frame, action, self.arena.scene, self.actor.place,
                                        self.arena.dp, self.arena.occupied())
        else:
            raise ValueError("无法识别的谬因中继器操作")
        self._relay_events()

    def _update_attack_clock(self, frame):
        speed = self.arena.attack_speed(self.actor)
        if speed == self.last_speed:
            return
        if speed not in self.speed_profiles:
            panel = dict(self.card["panel_attributes"], attackSpeed=speed)
            card = dict(self.card, panel_attributes=panel)
            self.speed_profiles[speed] = {
                mode: aphris_magazine_profile(self.tables, card, self.evidence, self.deploy,
                                              ordinary=mode == "ordinary")[4:]
                for mode in self.profile
            }
        self.profile = self.speed_profiles[speed]
        if self.last_attack is not None and self.poll is not None and self.poll > frame:
            mode, old_deadline = self.last_attack
            if self.poll == old_deadline:
                # A cold aura changes the running attack cooldown's remaining proportion.
                old_period = self.speed_profiles[self.last_speed][mode][1]
                new_period = self.profile[mode][1]
                remaining = self.poll - (frame - 1)
                self.poll = frame - 1 + (remaining * new_period + old_period - 1) // old_period
                self.last_attack = (mode, self.poll)
        self.last_speed = speed

    def objects(self):
        if self.closed:
            return
        frame = self.arena.frame
        self._refresh_allies()
        self.field.card.tick(frame)
        self._relay_events()
        self._update_attack_clock(frame)
        rate = self.arena.sp_rate(self.actor)
        period = _divide(ONE, _raw_f32(rate)) if rate > 0 else None
        set_period(self.gauge.state, period, stopped=self.mode != "ordinary" or frame < self.recovery)
        super().objects(frame)
        if self.shot and self.shot["start"] == frame:
            mode = "ordinary" if self.shot["ordinary"] else "magazine"
            self.last_attack = (mode, self.poll)
        self._relay_events()

    def coroutines(self):
        if not self.closed:
            self._refresh_allies()
            super().coroutines(self.arena.frame)
            self._relay_events()

    def press(self):
        frame = self.arena.frame
        self._refresh_allies()
        previous = len(self.buttons)
        started=super().press(frame)
        if started is not None:
            self.arena.skill_started(self.actor,started)
        if len(self.buttons) > previous:
            button = self.buttons[-1]
            self.arena.log("skill_button", operator_id=self.actor.uid, skill=self.slot,
                           outcome=button["outcome"], sp=button["sp"])

    def close(self):
        if not self.closed:
            self.closed = True
            self.drop(self.arena.frame, "host_removed")
            self._relay_events()

    def _attack(self, skill_bonus=0):
        relay_bonus = self.field.card.atk_bonus if self.field.card.active else 0
        return self.arena.attack(self.actor, skill_bonus + relay_bonus)

    def beam_hit(self, key, frame, ordinary):
        target = self.world.receivers[key]
        if not target.alive or self.closed:
            self.log("relay_segment_target_invalid", frame, target=key)
            return
        tile = (round(target.row), round(target.col))
        extent = self.beam.cells.get(tile, (0, 0))[1]
        attack = self._attack(0 if ordinary else self.board["atk"])
        scale = 1 if ordinary else self.board["attack@atk_scale"]
        self.arena.arts(self.actor, target, attack, scale_raw=_raw_f32(scale),
                        kind="ordinary_beam_impact" if ordinary else "beam_impact")
        if ordinary or not target.alive or self.closed:
            return
        applied = min(extent, int(self.board["attack@max_times"]))
        if applied:
            self.arena.arts(self.actor, target, attack,
                            scale_raw=_raw_f32(self.board["attack@extra_atk_scale"]), kind="relay_extra_impact")
        if target.alive:
            duration = _raw_f32(self.board["attack@origin_sluggish"]) + applied * _raw_f32(self.board["attack@extra_sluggish"])
            ticks = (duration + self.step - 1) // self.step
            self.arena.slow(target, self.slow_ratio, frame + ticks)

    def channel_hit(self, frame):
        targets = self.arena.targets(self.actor, self.beam.cells, tile_only=True)
        attack = self._attack()
        for target in targets:
            if self.closed:
                break
            self.arena.arts(self.actor, target, attack, scale_raw=_raw_f32(self.board["atk_scale"]), kind="channel_hit")

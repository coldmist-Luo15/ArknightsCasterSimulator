"""GALLUS² ordinary attacks and its deployment talent on the V079 battle clock."""
from math import hypot

from .animation_assets import read_skin_actions
from .attack_range import circle_in_range, range_cells
from .caster_catalog import GALLUS
from .caster_prefabs import caster_prefab_evidence
from .continuous_ranged import prepare_continuous_ranged
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import run_with_phalanx_waves
from .stage_deployment import RangedReentries
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


class GallusAttacks:
    def __init__(self, tables, card, scene, world, states, events):
        self.tables, self.card, self.scene = tables, card, scene
        self.world, self.states, self.events = world, states, events
        self.ev = caster_prefab_evidence(tables, GALLUS, None)
        components = self.ev["character"]["components"]
        self.attacks = {p["path"].split("/")[1]: p["fields"] for p in components if p["script"] == "RangedAttack"}
        deck = next(p["fields"] for p in components if p["script"] == "DeckBuffTalent")
        self.deck_effects = deck["_deckBuffs"]
        self.set_hand_state(0, True)
        passive = next(p["fields"] for p in components if p["script"] == "PassiveAttachmentAbility")
        source_mark, = passive["_buffs"]
        attachment, = passive["_additiveActiveBuffs"]
        gate, = self.ev["templates"][attachment["templateKey"]]["eventToActions"]["ON_BUFF_START"]
        create, = gate["_succeedNodes"]
        mark = create["_buff"]
        attr, = mark["attributes"]["attributeModifiers"]
        talent, = card["base_talents"]
        board = {b["key"]: b["value"] for b in talent["blackboard"]}
        self.dt = tables.fixed_timestep()
        step = int(ONE * self.dt)
        self.window = (_raw_f32(board[source_mark["durationKey"]]) + step - 1) // step
        self.duration = _raw_f32(board[mark["durationKey"]])
        self.scale = ONE + _raw_f32(board[attr["attributeType"].lower()])
        panel = card["panel_attributes"]
        self.attack = _raw_f32(panel["atk"])
        self.interval = panel["baseAttackTime"] * 100 / panel["attackSpeed"]
        self.period = fixed_wait_frames(self.interval / self.dt)
        projectile = self.ev["projectiles"][self.attacks["Talent"]["_projectileKey"]]["logic"]["components"]
        self.speed = next(p["fields"]["_speed"] for p in projectile if p["script"] == "AdvancedMovement")
        seconds = next(p["fields"]["_lifeTime"] for p in projectile if p["script"] == "SimpleProjectile")
        self.lifetime = (_raw_f32(seconds) + step - 1) // step
        self.skin = read_skin_actions(tables, "chararts/"+GALLUS+".ab", "defaultskin", ["Attack"])
        self.place = self.pending = None
        self.flights = []

    def set_hand_state(self, frame, in_hand):
        field = "affectInHand" if in_hand else "affectOutOfHand"
        self.hand_buffs = [effect["buff"]["buffKey"] for effect in self.deck_effects if effect[field]]
        self.events.append(dict(kind="gallus_card_buffs", frame=frame, buffs=self.hand_buffs))

    def enter(self, frame, place):
        self.place = place
        self.set_hand_state(frame, False)
        self.view = "Back" if place["direction"] == "UP" else "Front"
        self.face = self.view
        self.sign = {"LEFT": -1, "RIGHT": 1}.get(place["direction"], self.skin["default_horizontal_face_sign"])
        self.cells = range_cells(self.card["base_range"], place, self.scene["rows"], self.scene["cols"])
        self.poll = frame + _birth(self.tables, self.card, self.ev, self.view)["born_exit_update"] + 1
        self.until = frame + self.window
        self.events.append(dict(kind="gallus_deploy", frame=frame, talent_end=self.until))

    def leave(self, frame):
        self.place = self.pending = None
        self.set_hand_state(frame, True)

    def search(self, frame, mode):
        rows = [r for r in self.world.snapshot(frame) if self.states[r["id"]].hp > HP_EPSILON
                and circle_in_range(r["row"], r["col"], self.world.wave.radii[r["id"]], self.cells, box_query=True)]
        self.poll = frame + 3
        if not rows:
            return
        source = self.place["frame"]
        options = [r for r in rows if source not in self.states[r["id"]].gallus_marks] if mode == "Talent" else []
        candidates = {row["id"]: row for row in options or rows}
        first = self.world.wave.attack_order(list(candidates), frame, observer_born=source)[0]
        target = candidates[first]
        dx, dy = target["col"] - self.place["col"], target["row"] - self.place["row"]
        distance = hypot(dx, dy)
        # CharacterAnimator keeps its horizontal facing inside a 0.2-tile dead zone.
        if abs(dx) > 0.2:
            self.sign = 1 if dx > 0 else -1
        if distance > 0.2 and abs(dy) > abs(dx) + 0.25 * distance:
            self.face = "Back" if dy > 0 else "Front"
        clock = ranged_shot_release(self.tables, self.ev, self.attacks[mode], attack_frame=frame,
                                    attack_interval=self.interval, facing=self.face, mode_path="Modes/"+mode)
        self.pending = target["id"], clock
        self.poll = frame + self.period
        self.events.append(dict(kind="gallus_attack_start", frame=frame, target=target["id"], mode=mode))

    def advance(self, frame):
        if self.place:
            if frame == self.until:
                self.pending = None
                self.events.append(dict(kind="gallus_talent_end", frame=frame))
            if frame >= self.poll:
                self.search(frame, "Talent" if frame < self.until else "Default")
        for shot in tuple(self.flights):
            key = shot["target"]
            row = self.world.by_id(key, frame, projectile_born=shot["born"])
            state = self.states[key]
            if row is None or state.hp <= HP_EPSILON:
                self.flights.remove(shot)
                continue
            x, y, z = shot["offset"]
            aim = row["col"] + x, row["row"] + y, row["tile_z"] + z
            move = ordinary_trace_step(shot["position"], aim, effective_dt=self.dt, speed=self.speed, round_position=True)
            shot["position"] = move.position
            if not move.reached and frame < shot["born"] + self.lifetime:
                continue
            self.flights.remove(shot)
            flags = self.world.wave.flags
            if flags and "INVINCIBLE" in flags.at(key, frame):
                continue
            # V079 ActionController.OnHitTarget applies AbilityAttachment before _RunActions.
            if self.place and self.place["frame"] == shot["source"] and frame < self.until:
                refresh = shot["source"] in state.gallus_marks
                state.apply_gallus_mark(frame, shot["source"], self.scale, self.duration)
                self.events.append(dict(kind="gallus_mark", frame=frame, target=key, refresh=refresh))
            result = state.receive_ordinary(frame, source=GALLUS, damage_type="arts", attack_q32=self.attack,
                                           mitigation_q32=_raw_f32(self.world.stats[key]["res"]) + state.res_add)
            self.events.append(dict(kind="gallus_hit", frame=frame, target=key, born=shot["born"],
                                    damage_q32=result["damage_q32"], hp_after_q32=state.hp))
        if self.pending and frame == self.pending[1]["release_frame"]:
            key, clock = self.pending
            self.pending = None
            row = self.world.receiver_at(key, frame)
            if row and self.states[key].hp > HP_EPSILON:
                origin, goal = stationary_cast_mounts(self.tables, GALLUS, clock["clock"], self.place, row,
                                                      self.face, caster_face_sign=self.sign)
                offset = goal[0]-row["col"], goal[1]-row["row"], goal[2]-row["tile_z"]
                self.flights.append(dict(born=frame, source=self.place["frame"], target=key, position=origin, offset=offset))
                self.events.append(dict(kind="gallus_release", frame=frame, target=key))


def run_gallus_stage(tables, request):
    return run_with_phalanx_waves(_simulate, tables, request)


def _simulate(tables, request, releases):
    deck_only = not any(a.get("kind", "deploy") == "deploy" for a in request["actions"])
    card, stage, _, scene, world = prepare_continuous_ranged(tables, request, GALLUS, releases, deck_only=deck_only)
    events = []
    states = {k: EnemyElements.from_scene_enemy(tables, dict(r, hp_q32=_raw_f32(r["hp"])))
              for k, r in world.stats.items()}
    actor = GallusAttacks(tables, card, scene, world, states, events)
    cards = RangedReentries(tables, stage, world, request, first=-1)
    world.present, world.hp = False, 0
    if world.incoming:
        world.incoming.units.clear()
    stop = "horizon"
    for frame in range(request["end_frame"]+1):
        placement = cards.advance(frame, events)
        if placement:
            actor.enter(frame, placement)
        world.receive(frame, states, events)
        if actor.place and not world.present:
            actor.leave(frame)
            cards.leave(frame, GALLUS, manual=False)
        if world.settle_exits(frame, lambda k: states[k].hp > HP_EPSILON) is not None:
            stop = "stage_defeat"
            break
        for key, state in states.items():
            if world.births[key]["frame"] <= frame and key not in world.departed and state.hp > HP_EPSILON:
                state.tick(frame)
        world.allied_impacts(frame, states, events)
        actor.advance(frame)
        for action in cards.orders.get(frame, ()):
            if action.get("kind") == "withdraw" and world.present:
                world.remove_owner(frame, events, "withdraw")
                cards.leave(frame, GALLUS, manual=True)
                actor.leave(frame)
        if world.settled(frame, states, events):
            stop = "stage_victory_projected"
            break
    return world.stage_result(dict(operator_id=GALLUS, skill=None, module=None, stage=stage["code"],
                                   potential=card["potential"], through_frame=frame, stop=stop, events=events, card_buffs=actor.hand_buffs,
                                   slots=stage["initial"]["cards"][GALLUS]["slots"],
                                   element_events={k:s.events for k,s in states.items() if s.events}))

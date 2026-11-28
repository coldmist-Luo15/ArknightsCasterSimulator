"""Lappland S3 cruise projectiles and FEARED movement on the shared H5-1 board."""
from math import cos, hypot, pi, sin
from types import SimpleNamespace

from .baked_mount import single as f32
from .battle_random import BattleRandom
from .caster_cast import run_committed_cast
from .caster_model import _assets
from .caster_start import resolve_cast_start
from .enemy_fear import FearMotion, _cell
from .enemy_motion import advance_direct_move
from .fear_navigation import GroundFearNavigation
from .fixed import ONE, mul
from .lappland_stage import LapplandAttacks
from .mostima_aura import set_period
from .recal_lappland import LapplandCombat
from .skill_recovery import RecoveryTimer, _divide, deploy_skill
from .volley_damage import _raw_f32
from .whitw_wolves import _chase, _hover, _nearest, _respawn, _xy_step, wolf_release_frame


def _fear_events(battle, target):
    motion = target.wolf_fear
    for event in motion.events:
        fields = dict(event)
        kind = fields.pop("kind")
        fields.pop("frame", None)
        battle.log(kind, target=target.id, **fields)
    motion.events.clear()


def apply_recal_fear(battle, target, source, duration, buff_id):
    if target.attr("fearedImmune") or not duration:
        return
    cursor = target.cursor
    motion = getattr(target, "wolf_fear", None)
    if not motion:
        navigation = battle.navigation
        if target.airborne:
            if not hasattr(battle, "wolf_air_navigation"):
                air = GroundFearNavigation(battle.scene, static_terrain=True)
                air.cost = {(r, c): 1 for r in range(air.rows) for c in range(air.cols)}
                battle.wolf_air_navigation = air
            navigation = battle.wolf_air_navigation
        if not hasattr(battle, "wolf_map_random"):
            battle.wolf_map_random = BattleRandom(battle.request.get("map_random_seed", 0))
        end = cursor.route["commands"][-1]["target"]
        dest = next(c["target"] for c in cursor.route["commands"][cursor.index:] if c["kind"] in ("END", "MOVE"))
        motion = FearMotion(battle.tables, enemy=dict(row=target.row, col=target.col, velocity=cursor.velocity,
            route_target=(dest["row"], dest["col"]), prefab=target.prefab, move_speed=cursor.route["base_move_speed"],
            one_minus_status_resistance=.5 if target.attr("statusResistance") else 1),
            terrain=dict(navigation=navigation, exit=(end["row"], end["col"]), excluded=navigation.excluded,
                         level_move_multiplier=cursor.route["level_move_multiplier"]),
            start_frame=battle.frame, stream=battle.rng, map_stream=battle.wolf_map_random)
        target.wolf_fear = motion
    motion.frame = battle.frame
    motion.position, motion.velocity = cursor.position, cursor.velocity
    motion.apply(battle.frame, source, duration, buff_id)
    if motion.status != "tracked":
        raise ValueError(f"H5-1 恐惧路径不可用 {target.id} {motion.reason}")
    _fear_events(battle, target)


def wolf_walk(battle, target, cap):
    """Follow a temporary fear waypoint without consuming the authored route command."""
    motion = getattr(target, "wolf_fear", None)
    if not motion or not motion.buffs:
        return False
    frame = battle.frame
    cursor = target.cursor
    motion.frame = frame
    motion.position, motion.velocity = cursor.position, cursor.velocity
    if motion.need_checkpoint:
        motion._select()
    paused = frame < max(getattr(target, "stun_until", 0), getattr(target, "sbell_frozen_until", 0))
    if not paused:
        goal = motion.goal
        cell, dest = _cell(cursor.position), _cell(goal)
        waypoint = motion.navigation.get_map(dest, True)[1].get(cell, dest)
        if hypot(target.row - goal[0], target.col - goal[1]) > .1:
            result = advance_direct_move(cursor.position, cursor.velocity, goal if waypoint == dest else waypoint,
                                         cap, cursor.steering, battle.dt, True)
            if result:
                cursor.position, cursor.velocity = result
    motion.position, motion.velocity = cursor.position, cursor.velocity
    for key, buff in list(motion.buffs.items()):
        buff["remaining"] -= motion._buff_delta()
        if buff["remaining"] <= 0:
            motion.remove(frame, key)
    if motion.buffs:
        motion.need_checkpoint = hypot(target.row - motion.goal[0], target.col - motion.goal[1]) <= .1
    else:
        battle.log("fear_return_to_route", target=target.id, position=cursor.position, command=cursor.index)
    _fear_events(battle, target)
    return True


class WolfCombat(LapplandCombat):
    def __init__(self, battle, actor):
        self.battle, self.actor = battle, actor
        self.present, self.active = True, False
        self.notes = set()
        view = "Back" if actor.direction == "UP" else "Front"
        scene = dict(placement=actor.place, view=view, rows=battle.rows, cols=battle.cols)
        ev = _assets(battle.tables, actor.card)
        LapplandAttacks.__init__(self, battle.tables, actor.card, ev, scene, SimpleNamespace(stats={}), battle.events, self)
        self.world = self
        self.born_end = self.start_attack - 1
        self.gauge, _ = deploy_skill(battle.tables, actor.card, battle.frame)
        state = self.gauge.state
        state.points = min(state.maximum, state.points + int(self.talents[1]["sp"]))
        self.native = run_committed_cast(self.tables, ev, horizon=0, facing=view, direction=actor.direction)["natural_end"]
        self.unblock = actor.deployed_frame + 1
        self.spell = self.release = self.restore = None
        self.units, self.marks = [], {}
        self.origin = (actor.col, actor.row)
        self.angle = {"RIGHT": 0, "UP": pi/2, "LEFT": pi, "DOWN": -pi/2}[actor.direction]
        cs = ev["character"]["components"]
        self.emitter, = (c["fields"] for c in cs if c["script"] == "Whitw2Skill3FunnelRangedAttack")
        key = self.emitter["_cruiseProjectileKeys"][0]
        parts = ev["projectiles"][key]["logic"]["components"]
        self.straight, = (p["fields"] for p in parts if p["path"] == "InitStraightLine" and p["script"] == "AdvancedMovement")
        self.curve, = (p["fields"] for p in parts if p["path"] == "TraceTargetCurve" and p["script"] == "AdvancedMovement")
        self.ctrl, = (p["fields"] for p in parts if p["script"] == "MovementSwitchControllerForWhitw2")
        self.aura, = (buff for c in cs if c["script"] == "RangedAttack" and c["path"].endswith("WolvesDispersed/S3Funnel1")
                      for buff in c["fields"]["_activeBuffs"] if buff["templateKey"] == "whitw2_s3_t[slow_and_damage]")
        self.radius = self.board["attack@range_radius"]
        self.turn = f32(mul(_raw_f32(self.board["attack@projectile_turn_speed"]), self.step)/ONE)

    def _log(self, kind, **fields):
        self.battle.log(kind, operator_id=self.actor.uid, **fields)

    def move_multiplier(self, target):
        return 1 + self.board["attack@move_speed"] if target.id in self.marks else 1.

    def press(self):
        f = self.battle.frame
        state = self.gauge.state
        if not self.present or self.active or self.spell is not None or f < self.unblock - 1:
            self._log("skill_rejected", reason="owner_busy", sp=state.points)
            return False
        cast = resolve_cast_start(self.tables,self.ev,frame=f,phase="after_tick",sp=state.points,trigger="MANUAL",
            direction=self.actor.direction,facing=self.view,owner_state="born" if f < self.born_end else "attack" if self.attack else "idle")
        if cast["cast_frame"] is None:
            self._log("skill_rejected", reason=cast["status"], sp=state.points)
            return False
        state.points = cast["sp_after"]
        self.spell = cast["spell_frame"]
        self.restore = cast["cast_frame"] + self.native["end"]
        self.battle.skill_started(self.actor,cast["cast_frame"])
        self.unblock = cast["cast_frame"] + self.native["first_recovery_tick"]
        self.release = wolf_release_frame(self.ev,self.spell,self.step)
        self.module_started = True
        self.attack, self.start_attack = None, self.spell + 1
        self._log("skill_pressed", skill=3, mode_frame=self.spell, release_frame=self.release, end_frame=self.restore)
        return True

    def _spawn(self, index, angle):
        direction = f32(cos(angle)), f32(sin(angle))
        pos = tuple(f32(a + f32(self.emitter["_projectileInitRadius"] * b)) for a, b in zip(self.origin, direction))
        return dict(id=index, projectile=self.emitter["_cruiseProjectileKeys"][index], birth=self.battle.frame,
                    position=pos, direction=direction, mode="spread", speed=self.straight["_speed"], target=None,
                    clock=_raw_f32(self.board["attack@times"]), spatial_reach=False, attached_hits=0)

    def objects(self):
        f = self.battle.frame
        if self.present and f == self.restore:
            self._log("wolves_returned", wolves=len(self.units))
            self.units.clear()
            self.marks.clear()
            self.active = False
            self.reset_attack(f)
            self.restore = None
        if self.present and f == self.spell:
            self.spell = None
            self.active = True
            self.reset_attack(f)
        if self.present and f >= self.unblock:
            rate = self.battle.sp_rate(self.actor)
            set_period(self.gauge.state, _divide(ONE, _raw_f32(rate)) if rate > 0 else None, stopped=False)
            self.gauge.state.advance(self.step)
        self.tick(f,self.active)
        for key, timer in list(self.marks.items()):
            if not self.present:
                break
            if key not in self.battle.live:
                self.marks.pop(key,None)
            elif timer.advance(self.step):
                self.damage(f,key,_raw_f32(self.board["attack@magic_atk_scale"]),"wolf_aura_hit",True)

    def _acquire(self, w, live):
        rows = [dict(id=e.id, row=e.row, col=e.col) for e in live]
        key = _nearest(w["position"], self.origin, rows)
        if key is False:
            x,y = w["position"]
            key = min(live,key=lambda e:((e.col-x)**2+(e.row-y)**2,
                      (e.col-self.actor.col)**2+(e.row-self.actor.row)**2,e.order)).id
        if key is None:
            if w["mode"] != "hover":
                w.update(mode="hover",target=None,hover=None)
            return
        w.update(mode="chase", target=key, speed=self.curve["_speed"], spatial_reach=False, attached_hits=0)
        self._log("wolf_acquired", wolf=w["id"], target=key)

    def _move_wolf(self, w, live):
        f = self.battle.frame
        if w["birth"] == f:
            return
        target = self.battle.live.get(w["target"])
        if w["mode"] == "spread":
            w["clock"] -= self.step
            if w["clock"] > 0:
                w["speed"] = min(self.straight["_finalSpeed"], f32(w["speed"] + f32(self.dt*self.straight["_deltaSpeedPerSec"])))
                w["position"] = _xy_step(w["position"],w["direction"],w["speed"],self.dt)
                return
            self._acquire(w,live)
        elif w["mode"] == "hover":
            self._acquire(w,live)
        elif target is None or target.cursor.hidden:
            if w["mode"] == "attached":
                _respawn(w,self.battle.rng,self.ctrl["_randomOffsetMin"],self.ctrl["_randomOffsetMax"])
                self._acquire(w,live)
            else:
                self._acquire(w,[])
        if w["mode"] == "hover":
            _hover(w,self.radius,self.board["attack@projectile_move_speed"],self.dt)
            return
        target = self.battle.live[w["target"]]
        goal = (target.col,target.row)
        if w["mode"] == "attached":
            w["position"] = goal
        elif w["spatial_reach"]:
            w.update(mode="attached",remaining=0,position=goal)
        else:
            w["spatial_reach"] = _chase(w,goal,self.curve,self.dt,self.turn)
            if w["spatial_reach"]:
                w["first_hit"] = f + 1
                self._log("wolf_attachment",wolf=w["id"],target=target.id)
                apply_recal_fear(self.battle,target,(self.actor.row,self.actor.col),
                            self.board["attack@fear"],(self.actor.deployed_frame,w["id"],f))

    def coroutines(self):
        if not self.present or not self.active:
            return
        f = self.battle.frame
        count = 1 + int(self.board["attack@cnt"]) + int(self.stage(f) == 3)
        if self.release == f:
            self.release = None
            self.units = [self._spawn(i,self.angle+2*pi*i/count) for i in range(count)]
            self._log("release_cruise_wolves",wolves=count)
            return
        if not self.units:
            return
        if len(self.units) < count:
            self.units.append(self._spawn(len(self.units),self.angle))
        live = [e for e in self.battle.live.values() if not e.cursor.hidden]
        for w in self.units:
            self._move_wolf(w,live)
        attached = sorted((w for w in self.units if w["mode"]=="attached"),key=lambda w:(w["first_hit"],w["id"]))
        for w in attached:
            key = w["target"]
            w["remaining"] -= self.step
            if self.present and key in self.battle.live and w["remaining"] <= 0:
                w["remaining"] = max(0,self.interval()-self.step)
                cap = self.trait["max_atk_scale"] * (self.talents[0]["scale"] if self.stage(f)>=1 else 1)
                scale = min(cap,self.trait["init_atk_scale"]+w["attached_hits"]*self.trait["delta_atk_scale"])
                self.damage(f,key,_raw_f32(scale),"wolf_attached_hit",True)
                w["attached_hits"] += 1
        for e in live:
            touching = e.alive and any(hypot(w["position"][0]-e.col,w["position"][1]-e.row) <= self.radius+e.radius for w in self.units)
            if touching and e.id not in self.marks:
                timer = RecoveryTimer(_raw_f32(self.aura["triggerInterval"]))
                timer.remaining = 0
                self.marks[e.id] = timer
            elif not touching:
                self.marks.pop(e.id,None)

    def close(self):
        super().close()
        self.marks.clear()
        self.spell = self.release = self.restore = None

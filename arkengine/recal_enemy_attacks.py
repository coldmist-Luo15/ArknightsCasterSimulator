"""Hostile acquisition, animation keys, homing missiles and delayed artillery on H5-1."""
from dataclasses import dataclass, field
from math import hypot

from .animation_assets import read_spine_events
from .baked_mount import transform_point
from .bombardier_attack import _operator_hit_offset
from .caster_prefabs import _prefab
from .fixed import ONE
from .lin_incoming import _ranged_attack
from .mode_replay import _AttackCooldown
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .recal_scene import DEVICE
from .skill_recovery import READY_EPSILON
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


MAGES = {"enemy_1038_lunmag_2", "enemy_1041_lazerd", "enemy_1041_lazerd_2"}
ARTILLERY = "enemy_1112_emppnt_2"
# These bundles own the external SkeletonDataAsset refs on the three attack animators.
SPINE = {"enemy_1037_lunsbr": "3", "enemy_1029_shdsbr": "base_0", ARTILLERY: "6"}
PASSIVE = {"enemy_1017_defdrn", "enemy_1042_frostd", "enemy_1105_tyokai_2"}


def _spine_duration(tables, asset, prefab, binding):
    ref, = (c["fields"]["skeletonDataAsset"] for c in asset["components"] if c["script"] == "SkeletonAnimation")
    raw, _ = tables.verified_bundle(asset["source"]["member"])
    root = bundle_environment(raw).container[asset["source"]["container"]].deref()
    cab = root.assets_file.externals[ref["m_FileID"] - 1].path.rsplit("/", 1)[-1]
    raw, _ = tables.verified_bundle("refs/arts/enm_art_" + SPINE[prefab] + ".ab")
    art, = (o for o in bundle_environment(raw).objects if o.path_id == ref["m_PathID"] and o.assets_file.name == cab)
    skeleton = art.read_typetree()["skeletonJSON"]
    binary = art.assets_file.objects[skeleton["m_PathID"]].read_typetree()["m_Script"]
    clip, = (c for c in read_spine_events(binary)["animations"] if c["name"] == binding["animName"])
    return clip["duration"] / binding["speed"]


def _profile(b, e):
    prefab = e.prefab
    unit = b.scene.enemies[e.key]
    asset = _prefab(b.tables, "enemies", prefab)
    parts = asset["components"]
    attack, = (c["fields"] for c in parts if c["script"] in ("MeleeAttack", "RangedAttack"))
    if attack["_preDelay"] or attack["_escapeTime"] or not attack["_waitForAttackEvent"] or attack["_atkScale"] != 1:
        raise ValueError("H5-1 敌方普攻时序或攻击倍率已变化")
    animator, = (c["fields"] for c in parts if c["script"] == "SingleSpineAnimator")
    binding, = (r for r in animator["_animations"] if r["animKey"] == attack["_animKey"])
    data = b.tables.read("bakemuzzledata/enemy/" + prefab + ".json")["bakedAnimDatas"]["Default"]
    baked = data["bakedEvents"][binding["animName"]]
    key_time = baked["eventTime"][baked["events"].index("OnAttack")]
    interval = e.attr("baseAttackTime") * 100 / e.attr("attackSpeed")
    profile = dict(kind="melee", radius=0, limit=1, interval=interval,
                   period=(_raw_f32(interval) - READY_EPSILON + _raw_f32(b.dt)) // _raw_f32(b.dt),
                   damage_type=attack["_damageType"], select_at_spell=attack["_selectTargetTiming"] == 1)
    if prefab in MAGES or prefab == ARTILLERY:
        sel, = (c["fields"] for c in parts if c["script"] in ("AdvancedSelector", "SecondaryFilterAdvancedSelector"))
        if not sel["_limitTargetNum"] or sel["_postFilter"] != 4:
            raise ValueError("H5-1 敌方仇恨和人数筛选已变化")
        profile.update(limit=sel["_maxNum"], radius=unit["rangeRadius"]["m_value"])
    if prefab in MAGES:
        profile.update(_ranged_attack(b.tables, unit, e.key), kind="spell")
        profile["height"], = (c["fields"]["_height"] for c in parts if c["script"] == "Enemy")
    else:
        duration = _spine_duration(b.tables, asset, prefab, binding)
        speed = max(1., duration / interval)
        profile.update(wait=baked_key_sample(key_time, speed, b.dt)[0],
                       duration=fixed_wait_frames(duration / speed / b.dt))
    if prefab == ARTILLERY:
        projectile = _prefab(b.tables, "[uc]projectiles", attack["_projectileKey"])
        p = {c["script"]: c["fields"] for c in projectile["components"]}
        attach, hit, body = p["AttachToTarget"], p["HitBehaviour"], p["CircleCollider2D"]
        if (attack["_projectileDestination"] != 2 or attach["_followTarget"] or attach["_stopIfTargetDead"]
                or not hit["_onlyCheckHitWhenReachTarget"] or any(body["m_Offset"].values())
                or sel["_buffKey"] != "mark_neutral[effect]"):
            raise ValueError("H5-1 炮击落点、锁定优先级或范围已变化")
        profile.update(kind="artillery", splash=body["m_Radius"],
                       fuse=fixed_wait_frames(attach["_delayToStart"] / b.dt))
    return profile


@dataclass
class Clock:
    poll: int
    hold_until: int = 0
    ready: int = 0
    release: int | None = None
    targets: list = field(default_factory=list)
    cooldown: object = None
    speed: float = 1.
    palsy_due: int | None = None


def _control(e, frame):
    if hasattr(e, "sbell_frozen_until") or hasattr(e, "sbell_cold_until") or hasattr(e, "sbell_attracted_until"):
        from .recal_sbell import attracted, enemy_attack_speed_multiplier, frozen
        return frozen(e, frame) or attracted(e, frame), enemy_attack_speed_multiplier(e, frame)
    return False, 1.


class EnemyAttacks:
    def __init__(self, battle):
        self.b = battle
        self.states, self.profiles, self.body = {}, {}, {}
        self.flights, self.shells = [], []
        self.step = _raw_f32(battle.dt)

    def holding(self, enemy):
        clock = self.states.get(enemy.id)
        return bool(clock and self.b.frame < clock.hold_until) or _control(enemy, self.b.frame)[0]

    def _body(self, actor):
        uid = actor.uid
        if uid not in self.body:
            group = "[uc]tokens" if uid == DEVICE else "characters"
            shape = self.b.tables._component_fields(group, uid, ("m_Radius", "m_Offset", "m_Enabled"),
                "recal_hostile_receivers", component_type="CircleCollider2D")
            if not shape["m_Enabled"] or any(shape["m_Offset"].values()):
                raise ValueError("H5-1 受击单位的圆形碰撞体已变化")
            offset = (0., 0., 0.) if uid == DEVICE else _operator_hit_offset(
                self.b.tables, uid, actor.card.get("battle_skin", "defaultskin"))
            if offset is None:
                offset = (0., 0., 0.)
                self.b.log("rule_applied", operator_id=uid, rule="tile_center_for_variable_operator_hit_mount")
            self.body[uid] = shape["m_Radius"], offset
        return self.body[uid]

    def _targets(self, e, p):
        if p["kind"] == "melee":
            holder = self.b.blocker(e)
            return [holder] if holder and holder.enemy_selectable(self.b.frame) else []
        candidates = []
        for order, a in enumerate(self.b.operators.values()):
            if a.enemy_selectable(self.b.frame) and hypot(a.row - e.row, a.col - e.col) <= p["radius"] + self._body(a)[0]:
                taunt = a.card["panel_attributes"].get("tauntLevel", 0)
                candidates.append((taunt, a.deployed_frame, order, a))
        return [r[-1] for r in sorted(candidates, reverse=True)[:p["limit"]]]

    def objects(self):
        f = self.b.frame
        for e in tuple(self.b.live.values()):
            if e.prefab in PASSIVE or f < e.attack_ready_from or e.cursor.hidden:
                continue
            if e.key not in self.profiles:
                self.profiles[e.key] = _profile(self.b, e)
            p = self.profiles[e.key]
            c = self.states.setdefault(e.id, Clock(e.attack_ready_from))
            controlled, speed = _control(e, f)
            if controlled or self.b.disarmed(e):
                if c.release is not None:
                    self.b.log("enemy_attack_interrupted", source=e.id, before_release=c.release)
                c.release, c.hold_until, c.poll = None, 0, f + 1
                c.palsy_due = None
                continue
            if c.palsy_due == f:
                c.palsy_due = None
                if c.release is not None and self.b.mantra.interrupt(e):
                    c.release, c.hold_until, c.poll = None, 0, f + 1
                    continue
            if c.speed != speed:
                if c.cooldown and c.ready >= f:
                    c.cooldown.change_period(f - 1, self.step, _raw_f32(p["interval"] / speed))
                    c.ready = f - 1 + (c.cooldown.remaining + self.step - 1) // self.step
                c.speed = speed
            if f < max(c.hold_until, c.ready, c.poll):
                continue
            c.poll = f + 3
            chosen = self._targets(e, p)
            if chosen:
                c.targets = chosen
                # The locked clip has maximum scale 1. A lower ASPD extends cooldown, leaving the clip intact.
                c.release = f + p["wait"]
                mantra = getattr(self.b, "mantra", None)
                c.palsy_due = f + mantra.delay if mantra and mantra.can_interrupt(e) else None
                c.hold_until = f + p["duration"] + 2
                c.cooldown = _AttackCooldown(f, _raw_f32(p["interval"] / speed))
                c.ready = f + (c.cooldown.period - READY_EPSILON + self.step) // self.step
                self.b.log("enemy_attack_start", source=e.id, enemy=e.key, attack=p["kind"],
                           targets=[a.key for a in chosen], release_frame=c.release, ready_frame=c.ready)

    def _emit(self, e, p, actors):
        f = self.b.frame
        attack = _raw_f32(e.attr("atk"))
        for a in actors:
            if not a.alive:
                continue
            if p["kind"] == "melee":
                self.b.hurt(e, a, attack, p["damage_type"], "enemy_melee_hit")
            elif p["kind"] == "artillery":
                self.shells.append((f + p["fuse"], e, (a.row, a.col), attack, p))
                self.b.log("artillery_mark", source=e.id, target=a.key, row=a.row, col=a.col, impact_frame=f + p["fuse"])
            else:
                sign = 1 if a.col >= e.col else -1
                x, y, z = p["local"]
                offset = transform_point((sign * x, y, sign * z), p["parents"])
                start = (e.col + offset[0], e.row + offset[1], offset[2] - p["height"])
                self.flights.append((f, e, a, attack, start, p))
                self.b.log("enemy_spell_release", source=e.id, target=a.key)

    def coroutines(self):
        f = self.b.frame
        for e in tuple(self.b.live.values()):
            c = self.states.get(e.id)
            if c is not None and c.release == f:
                c.release = None
                c.palsy_due = None
                mantra = getattr(self.b, "mantra", None)
                if self.b.disarmed(e) or _control(e, f)[0] or mantra and mantra.interrupt(e):
                    c.hold_until = 0
                    continue
                p = self.profiles[e.key]
                self._emit(e, p, self._targets(e, p) if p["select_at_spell"] else c.targets)
        remaining = []
        for born, e, a, attack, pos, p in self.flights:
            if not a.alive:
                continue
            if f > born:
                x, y, z = self._body(a)[1]
                tile = self.b.tiles[a.row, a.col]
                goal = (a.col + x, a.row + y, tile["position"]["z"] - tile["locate_height_offset"] + z)
                logos = getattr(self.b, "logos", None)
                scale = logos.projectile_scale(pos) if logos else 1.
                step = ordinary_trace_step(pos, goal, effective_dt=self.b.dt, round_position=True, speed=p["speed"] * scale)
                if step.reached or f - born >= p["life_ticks"]:
                    self.b.hurt(e, a, attack, p["damage_type"], "enemy_spell_hit")
                    continue
                pos = step.position
            remaining.append((born, e, a, attack, pos, p))
        self.flights = remaining
        remaining = []
        for impact, e, center, attack, p in self.shells:
            if f < impact:
                remaining.append((impact, e, center, attack, p))
                continue
            victims = [a for a in self.b.operators.values()
                       if a.alive and hypot(a.row - center[0], a.col - center[1]) <= p["splash"] + self._body(a)[0]]
            self.b.log("artillery_detonation", source=e.id, row=center[0], col=center[1], targets=[a.key for a in victims])
            for a in victims:
                self.b.hurt(e, a, attack, p["damage_type"], "artillery_hit")
        self.shells = remaining

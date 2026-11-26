"""V079 护障·P resistance on enemy-root trigger overlap, sampled for an impact."""
from bisect import bisect_right
from collections import defaultdict
from math import hypot

from .health_bounds import _reviewed_regular_enemy

DRONE = "enemy_1355_mrfly_2"


def resistance_aura(tables, scene, spawned, history):
    sources = {instance for instance, birth in spawned.items() if birth["enemy"] == DRONE}
    if not sources:
        return None
    data = scene.enemies[DRONE]
    path = ("Talents","magdef_add")
    cfg = tables._component_fields("enemies", DRONE, ("_buffs","_selfOption","_removeBuffWhenTargetLeave",
        "_removeBuffWhenAbilityDetached","_onlyAddBuffOnceForEachTarget","_targetValidator"), "enemy_aura_prefab", children=path)
    shape = tables._component_fields("enemies", DRONE, ("m_Radius","m_Offset","m_IsTrigger","m_Enabled"),
        "enemy_aura_circle",component_type="CircleCollider2D",children=path)
    validator = tables._component_fields("enemies",DRONE,("_targetOptions",),"enemy_aura_validator",children=path)
    ref = tables.source["enemy_aura_validator"][DRONE]["component_path_id"]
    radius = data["rangeRadius"]
    buff, = cfg["_buffs"]
    options = validator["_targetOptions"]
    modifier, = buff["attributes"]["attributeModifiers"]
    board = {bb["key"]:bb["value"] for bb in data["talentBlackboard"]}
    if (not radius["m_defined"] or radius["m_value"] != shape["m_Radius"] or shape["m_Radius"] != 2.5
            or shape["m_Offset"] != {"x":0,"y":0} or not shape["m_IsTrigger"] or not shape["m_Enabled"]
            or (cfg["_selfOption"],cfg["_removeBuffWhenTargetLeave"],cfg["_removeBuffWhenAbilityDetached"]) != (2,1,1)
            or cfg["_onlyAddBuffOnceForEachTarget"] or cfg["_targetValidator"] != dict(m_FileID=0,m_PathID=ref)
            or (options["targetSide"],options["targetMotion"],options["targetCategory"]) != (1,3,3)
            or buff["buffKey"] != "add_magicdef" or buff["independentCharacterSource"] or buff["maxStackCnt"] != 1
            or modifier != dict(attributeType=3,formulaItem=0,value=0.0,loadFromBlackboard=1,fetchBaseValueFromSourceEntity=0)
            or board.get("magdef_add.magic_resistance") != 30):
        raise ValueError("护障·P光环的条件、碰撞体或法抗加成与 V079 配置不符")
    radii = {}
    for key in {b["enemy"] for b in spawned.values()}:
        prefab = scene.enemies[key]["prefabKey"]
        radii[key] = tables.enemy_circle(prefab["m_value"] if prefab["m_defined"] and prefab["m_value"] else key)
    ordinary = {s for s in sources if _reviewed_regular_enemy(scene,spawned[s])}
    return ResistanceAura(spawned, history, sorted(sources), radii, radius["m_value"], board["magdef_add.magic_resistance"],
                          finish_on_hp_zero=ordinary)


class ResistanceAura:
    def __init__(self, spawned, history, sources, radii, radius, bonus, *, finish_on_hp_zero=()):
        self.spawned, self.sources, self.radii = spawned, sources, radii
        self.radius, self.bonus = radius, bonus
        self.finish_on_hp_zero = frozenset(finish_on_hp_zero)
        self.frames, self.positions = defaultdict(list), defaultdict(list)
        self.first_move = {}
        for frame, events in sorted(history.items()):
            for kind, row in events:
                if kind in ("free_motion_sample","post_wait_motion_sample","exit_motion_sample"):
                    pos = row["row"],row["col"]
                elif kind == "checkpoint_begin":
                    pos = "absent" if row["disappeared"] else (row["position"]["row"],row["position"]["col"])
                elif kind in ("route_end_in_range","range_motion_unresolved"):
                    pos = "absent" if kind == "route_end_in_range" else None
                else:
                    continue
                key = row["instance_id"]
                if row.get("active_ticks", 0) > 0:
                    self.first_move.setdefault(key,frame)
                self.frames[key].append(frame)
                self.positions[key].append(pos)

    def position(self, key, frame):
        if frame < self.spawned[key]["spawn_frame"]:
            return "absent"
        index = bisect_right(self.frames[key],frame)-1
        return self.positions[key][index] if index >= 0 else None

    def overlap(self, source, target, frame):
        a,b = self.position(source,frame),self.position(target,frame)
        if "absent" in (a,b) or source == target:
            return False
        if a is None or b is None:
            return None
        distance = hypot(a[0]-b[0],a[1]-b[1])
        reach = self.radius + self.radii[self.spawned[target]["enemy"]]
        # Keep near-tangent Box2D contacts conditional across physics tolerances.
        if abs(distance-reach) < 1e-5:
            return None
        return distance < reach

    def bonus_bounds(self,target,frame,*,dead_by=None,finished_by=None,finished_before_hit=(),source_alive=None,phase="OBJECTS"):
        if phase not in ("OBJECTS","COROUTINES"):
            raise ValueError("光环查询需要伤害发生在 OBJECTS 或 COROUTINES")
        certain, possible = False, False
        for source in self.sources:
            death = (dead_by or {}).get(source)
            finish = (finished_by or {}).get(source)
            if death is not None and source in self.finish_on_hp_zero:
                finish = min(death,finish) if finish is not None else death
            if source == target or source in finished_before_hit or (finish is not None and finish < frame):
                continue
            if death is not None and death < frame:
                # For an unreviewed lifecycle, HP alone does not prove that Enemy.OnFinish ran.
                possible = True
                continue
            # Fixed simulation is between OBJECTS and COROUTINES. The callback and a
            # device hit can both run in COROUTINES, in which case include the latest pair.
            ticks = range(frame-2,frame + int(phase == "COROUTINES"))
            contacts = [self.overlap(source,target,tick) for tick in ticks]
            if all(c is False for c in contacts):
                continue
            possible = True
            alive = source_alive is None or source_alive(source,frame)
            both_active = all(self.first_move.get(key,frame) <= frame-2 for key in (source,target))
            if all(c is True for c in contacts) and both_active and alive and death != frame and finish != frame:
                certain = True
        return (self.bonus if certain else 0, self.bonus if possible else 0)

    def report(self, horizon):
        contacts = []
        for source in self.sources:
            for target, birth in self.spawned.items():
                if target == source:
                    continue
                previous = False
                for frame in range(max(self.spawned[source]["spawn_frame"],birth["spawn_frame"]),horizon+1):
                    inside = self.overlap(source,target,frame)
                    if inside is previous:
                        continue
                    previous = inside
                    contacts.append(dict(frame=frame,source=source,target=target,overlap=inside))
        return dict(enemy=DRONE, sources=self.sources, radius=self.radius, resistance_bonus=self.bonus,
                    receiver_circle_radii=self.radii, stacking_limit=1, affects_self=False,
                    physical_contacts=sorted(contacts,key=lambda e:(e["frame"],e["source"],e["target"])),
                    conditions=["centred_root_circle_overlap_including_receiver_radius",
                                "projected_positions_before_removals_from_combat",
                                "source_talent_active_without_silence",
                                "projectile_OBJECTS_checks_both_preceding_physics_samples_at_contact_edges"])

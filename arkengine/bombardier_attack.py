"""One stationary-target shot of the V079 Blacksteel bombardier."""
from math import hypot

from .animation_assets import read_skin_actions
from .baked_mount import baked_position, mount_parents, single, transform_point
from .fixed import ONE, mul
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .projectile_scene import stage_tile_positions
from .skill_recovery import READY_EPSILON
from .trace_motion import paracurve_reach_ticks
from .volley_damage import _raw_f32


def _gun(tables, scene, enemy):
    cache = ("bombardier_gun", enemy, scene.stage["stageId"])
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    path = ("Modes","Default","Body","Attack")
    expected = dict(_animKey="Attack",_waitForAttackEvent=1,_timeMode=0,_maxAnimScale=1.,
                    _preDelay=0.,_escapeTime=0.,_mountPointType=2,_projectileDestination=0,
                    _faceToTarget=1,_useMountGroup=0,_waitForProjectileInvalid=0,_resetCdStrategy=1,
                    _projectileKey="projectile_enemy_ekulsr",_atkScale=.5,_useCachedAtkOnly=0)
    cfg = tables._component_fields("enemies",enemy,tuple(expected),"bombardier_fire",children=path)
    selector = tables._component_fields("enemies",enemy,("_keepTarget","_minTargetNum","_overrideSearchTargetTick"),
                                        "bombardier_trigger",children=(*path,"Selector"))
    if cfg != expected or selector != dict(_keepTarget=0,_minTargetNum=1,_overrideSearchTargetTick=-1):
        return None
    graphic = tables._component_fields("enemies",enemy,("_muzzleTransform","_spineScale","_animations"),
                                       "bombardier_muzzle",children=("Graphic",))
    bindings = [a for a in graphic["_animations"] if a["animKey"] == cfg["_animKey"]]
    if len(bindings)!=1 or graphic["_spineScale"]!=1 or bindings[0]["speed"]!=1 or bindings[0]["loop"]:
        return None
    clip = bindings[0]["animName"]
    bake = tables.read(f"bakemuzzledata/enemy/{enemy}.json")["bakedAnimDatas"]["Default"]
    event = bake["bakedEvents"][clip]
    if event["events"] != ["OnAttack"] or len(event["eventTime"])!=1:
        return None
    ref = graphic["_muzzleTransform"]
    src = tables.source["bombardier_muzzle"][enemy]
    raw,_ = tables.verified_bundle(src["member"])
    root = bundle_environment(raw).container[src["container"]].deref()
    objects = root.assets_file.objects
    if ref["m_FileID"] or not ref["m_PathID"]:
        return None
    parents = mount_parents(objects, objects[ref["m_PathID"]].read_typetree(), root.path_id)
    track = bake["bakedMPDatas"][clip][str(cfg["_mountPointType"])]
    attrs = scene.enemies[enemy]["attributes"]
    interval = attrs["baseAttackTime"]["m_value"] * 100 / attrs["attackSpeed"]["m_value"]
    # The bound non-looping V079 Spine Attack clip lasts two seconds. The same
    # track contains both endpoints, one sample per nominal 30-Hz bake frame.
    if (clip!="Attack" or track["frameCount"]!=61 or track["bakedStepInterval"]!=1 or interval<2
            or parents[-1]["scale"]!=dict(x=1,y=1,z=1)
            or parents[-1]["rotation"]!=dict(x=0,y=0,z=0,w=1)):
        return None
    dt = tables.fixed_timestep()
    step = int(dt * ONE)
    cooldown_ticks = (int(interval * ONE) - READY_EPSILON + step) // step
    steps, seconds = baked_key_sample(event["eventTime"][0],1.,dt)
    raw,_ = tables.verified_bundle("battle/prefabs/[uc]projectiles.ab")
    projectile = bundle_environment(raw).container[f"dyn/battle/prefabs/[uc]projectiles/{cfg['_projectileKey']}.prefab"].deref()
    parts = [projectile.assets_file.objects[x["component"]["m_PathID"]].read_typetree()
             for x in projectile.read_typetree()["m_Component"]]
    mover, = [c for c in parts if "_raiseHeight" in c]
    box, = [c for c in parts if "m_Size" in c and "m_Offset" in c]
    life, = [c for c in parts if "_lifeTime" in c]
    if (any(mover[k] for k in ("_useRandomHeight","_randomInverseParacurve","_delayAfterReached",
                               "_comeBack","_useSourceDirection","_useTargetDirection","_flyToPredefinedLocation"))
            or mover["_speed"]<=0 or any(box["m_Offset"].values()) or life["_lifeTimeType"]!=1):
        return None
    mods = tables.read("buff_table.json")["act28side_energy_buff"]["attributes"]["attributeModifiers"]
    atk, = (m for m in mods if m["attributeType"]=="ATK")
    if atk["formulaItem"]!="MULTIPLIER" or atk["loadFromBlackboard"] or atk["fetchBaseValueFromSourceEntity"]:
        return None
    data = dict(key_steps=steps, cooldown_ticks=cooldown_ticks, local=baked_position(track,seconds,dt), parents=parents[:-1],
                duration_ticks=fixed_wait_frames(2/dt), speed=mover["_speed"], box=box["m_Size"],
                lifetime=life["_lifeTime"], atk=attrs["atk"]["m_value"], multiplier=atk["value"],scale=cfg["_atkScale"])
    tables._prefab_data[cache] = data
    return data


def _operator_hit_offset(tables, key, skin):
    cache = ("bombardier_operator_hit",key,skin)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    group = "chararts" if skin=="defaultskin" else "skinpack"
    asset = read_skin_actions(tables,f"{group}/{key}.ab",skin,(),[(1,"hitTransform")])
    baked = tables.read(f"bakemuzzledata/character/{key}_{skin}.json")["bakedAnimDatas"]
    tree = tables._component_fields("characters",key,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
                                  "bombardier_target_graphic",component_type="Transform",children=("Graphic",))
    graphic = dict(position=tree["m_LocalPosition"],rotation=tree["m_LocalRotation"],scale=tree["m_LocalScale"])
    offsets = set()
    for face,view in asset["views"].items():
        for tracks in baked[face.title()]["bakedMPDatas"].values():
            track = tracks.get("1")
            if not track or track["frameCount"]!=1:
                return None
            point = baked_position(track,0,tables.fixed_timestep())
            if abs(point[0])>1e-6 or abs(point[2])>1e-6:
                return None
            offsets.add(transform_point(point,[*view["mount_parents"][1],graphic]))
    if len(offsets)!=1:
        return None
    offset = offsets.pop()
    tables._prefab_data[cache] = offset
    return offset


def first_shot(tables, scene, source, prefab, frame, position, target, operators, skins, round_position):
    gun = _gun(tables,scene,prefab)
    target_id = target["operator_id"]
    if not gun or target_id not in skins:
        return None
    off = _operator_hit_offset(tables,target_id,skins[target_id])
    if off is None or position[1]==target["col"]:
        return None
    tiles = stage_tile_positions(tables,scene)
    row,col = position
    ground = tiles.get((int(row+.5),int(col+.5)))
    top = tiles[target["row"],target["col"]]
    if not ground or ground["locate_height_offset"] or ground["position"]["z"] or top["locate_height_offset"]:
        return None
    side = 1 if target["col"]>col else -1
    x,y,z = gun["local"]
    shift = transform_point((side*x,y,side*z),gun["parents"])
    origin = tuple(single(v+d) for v,d in zip((col,row,0),shift))
    dest = tuple(single(top["position"][k]+v) for k,v in zip("xyz",off))
    dt = tables.fixed_timestep()
    flight = paracurve_reach_ticks(origin,dest,speed=gun["speed"],effective_dt=dt,round_position=round_position,
                                  limit=int(gun["lifetime"]/dt))
    if flight is None:
        return None
    release = frame+gun["key_steps"]
    affected = []
    for op,radius in operators:
        if op["frame"]>release+flight:
            continue
        # The last stored position lies within .1 of the stationary hit mount.
        # Resolve only if every point in that envelope selects the same body.
        dx = abs(op["col"]-dest[0])-gun["box"]["x"]/2
        dy = abs(op["row"]-dest[1])-gun["box"]["y"]/2
        if hypot(max(0,dx+.1),max(0,dy+.1)) <= radius:
            affected.append(op["operator_id"])
        elif hypot(max(0,dx-.1),max(0,dy-.1)) <= radius:
            return None
    return dict(source=source,target=target_id,acquire_frame=frame,baked_event_frame=release-1,release_frame=release,
                impact_frame=release+flight,flight_ticks=flight,affected_operators=affected,ignore_for_sp=False,
                source_position=position,origin_map_xyz=origin,destination_map_xyz=dest,
                cast_end_frame=frame+gun["duration_ticks"],next_ready_frame=frame+gun["cooldown_ticks"],
                energy_at_release=None,energy_at_impact=None,
                scaled_attack_q32=None)


def shell_attack(tables,scene,prefab,stacks):
    p = _gun(tables,scene,prefab)
    return mul(mul(_raw_f32(p["atk"]),ONE+stacks*_raw_f32(p["multiplier"])),_raw_f32(p["scale"]))

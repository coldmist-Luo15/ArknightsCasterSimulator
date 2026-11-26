"""Baked caster attack releases and flights to stationary ground targets."""
from .animation_assets import read_skin_actions
from .baked_mount import baked_position, single, transform_point
from .caster_first_spell import _selected_clip
from .caster_flight import projectile_reach
from .caster_prefabs import sample_clip_events
from .mystic_cycle import baked_key_sample
from .projectile_scene import enemy_hit_mount


def stationary_ranged_shot(tables,evidence,attack,*,attack_frame,attack_interval,placement,enemy,
                           facing,round_position,max_flight_ticks,mode_path=None):
    """One attack starting in OBJECTS, sampled in LATE_OBJECTS and released on the next coroutine drain."""
    shot = ranged_shot_release(tables,evidence,attack,attack_frame=attack_frame,attack_interval=attack_interval,
                               facing=facing,mode_path=mode_path)
    origin,target = stationary_cast_mounts(tables,evidence["operator_id"],shot["clock"],placement,enemy,facing)
    if type(max_flight_ticks) is not int or max_flight_ticks<1:
        raise ValueError("飞行帧数需要正整数")
    flight = projectile_reach(tables,evidence,dict(projectile=attack["_projectileKey"],frame=shot["release_frame"]+1,
                              origin=origin,target_mounts=[target]*max_flight_ticks,round_position=round_position))
    if flight["status"]!="reached":
        raise ValueError("弹体在本次观察窗口内没有实际到达目标")
    return dict(shot,reach_frame=flight["reach_frame"],flight=flight)


def ranged_shot_release(tables,evidence,attack,*,attack_frame,attack_interval,facing,mode_path=None):
    """Resolve the baked launch independently of target motion after release."""
    if (not attack["_waitForAttackEvent"] or attack["_timeMode"]!=0 or attack["_preDelay"] or
            attack["_mountPointType"]!=2 or attack["_useMountGroup"] or attack["_projectileDestination"] or
            attack_interval<=0 or evidence["animations"]["skin"]!="defaultskin"):
        raise ValueError("本发射入口需要普通弹体挂点、默认皮肤及按攻击间隔缩放的 OnAttack 动作")
    if attack["_downAnimKey"] or attack["_upAnimKey"]:
        raise ValueError("该攻击使用单独的上下朝向动作，需要传入实际瞄准方向")
    action = attack["_animKey"]
    if mode_path is not None:
        replacements = [p["toAnimKey"] for c in evidence["character"]["components"]
                        if c["path"]==mode_path and c["script"]=="SimpleUnitAnimatorHooker" and c["enabled"] and c["active"]
                        for p in c["fields"]["_replaceAnimPairs"] if p["fromAnimKey"]==action]
        if replacements:
            action, = replacements
    clip,_ = _selected_clip(tables,evidence,action,facing)
    if clip["loop"]:
        raise ValueError("持续循环动作须按独立的多次发射时钟处理")
    scale = max(single(.1),single(single(attack_interval)/single(clip["duration"])))
    if attack["_maxAnimScale"]>=0:
        scale = min(scale,attack["_maxAnimScale"])
    speed = single(1/scale)
    samples = sample_clip_events(evidence["animations"],facing,clip["clip"],playback_speed=speed,timestep=tables.fixed_timestep())
    if [e["event"] for e in samples]!=["OnAttack"]:
        raise ValueError("攻击动作需要包含唯一的 OnAttack 发射事件")
    key, = samples
    release = attack_frame+key["clip_tick"]
    clock = dict(action=action,first_key_seconds=key["seconds"],playback_speed=speed,
                 cached_duration_seconds=single(single(clip["duration"])*scale))
    return dict(attack_frame=attack_frame,baked_event_frame=release-1,release_frame=release,clock=clock)


def stationary_cast_mounts(tables, uid, clock, placement, enemy, facing, *, mount=2,field="muzzlaTransform",
                           static_mode_clips=(),sample_seconds=None,caster_face_sign=None,skin_data=None):
    action = clock["action"]
    dt = tables.fixed_timestep()
    cache = ("stationary_elemental_skin",uid,action,mount,field)
    if skin_data is None and cache not in tables._prefab_data:
        tables._prefab_data[cache] = read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",[action],mounts=[(mount,field)])
    skin = skin_data if skin_data is not None else tables._prefab_data[cache]
    view = skin["views"][facing.lower()]
    clip = view["actions"][action]["clip"]
    bake = tables.read("bakemuzzledata/character/"+uid+"_defaultskin.json")["bakedAnimDatas"]
    tracks = bake[facing]["bakedMPDatas"]
    track = tracks[clip][str(mount)]
    if static_mode_clips:
        samples = [points[str(mount)] for name,points in tracks.items() if name in static_mode_clips]
        if not samples or any(s["frameCount"]!=1 or baked_position(s,0,dt)!=baked_position(track,0,dt) for s in samples):
            raise ValueError("该模式与朝向的天赋挂点随动作变化")
        time = 0
    elif sample_seconds is None:
        _,time = baked_key_sample(clock["first_key_seconds"],clock["playback_speed"],dt)
    else:
        time = sample_seconds
    point = baked_position(track,time,dt)
    graph = tables._component_fields("characters",uid,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
                    "character_graphic",component_type="Transform",children=("Graphic",))
    parent = dict(position=graph["m_LocalPosition"],rotation=graph["m_LocalRotation"],scale=graph["m_LocalScale"])
    sign = caster_face_sign if caster_face_sign is not None else (1 if enemy["col"]>placement["col"] else -1)
    if sign not in (-1,1):
        raise ValueError("发射者的当前水平朝向须为 -1 或 1")
    offset = transform_point((sign*point[0],point[1],sign*point[2]),[*view["mount_parents"][mount],parent])
    origin = tuple(v+d for v,d in zip((placement["col"],placement["row"],placement["tile_z"]),offset))
    return origin,stable_enemy_mount(tables,enemy)


def stable_enemy_mount(tables,enemy):
    receiver = enemy_hit_mount(tables,enemy["prefab"])
    note=enemy.get("receiver_mount_rule")
    if receiver is None and note:
        note("tile_center_for_variable_enemy_hit_mount")
        return enemy["col"],enemy["row"],enemy["tile_z"]
    if receiver and note and receiver["height"] and receiver["spine_height_update"]:
        note("prefab_height_for_animated_enemy_hit_mount")
    if receiver is None or not note and receiver["height"] and (not enemy.get("raw_airborne",enemy.get("airborne")) or receiver["spine_height_update"]):
        raise ValueError("需要稳定的地面或固定高度空中命中挂点")
    point = baked_position(receiver["hit_track"],0,tables.fixed_timestep())
    parents = receiver["parents"]
    offset = transform_point(point,parents)
    if abs(offset[0])>1e-5:
        # A translation on Graphic is outside FaceSwitcher and never turns with it.
        # Any yaw of the switch leaves a local point exactly on its Y axis unchanged.
        switches = [i for i,p in enumerate(parents) if p["object"]=="FaceSwitcher"]
        if len(switches)!=1:
            if note:
                note("prefab_transform_for_variable_enemy_facing")
                return tuple(v+d for v,d in zip((enemy["col"],enemy["row"],enemy["tile_z"]-receiver["height"]),offset))
            raise ValueError("未解析目标的朝向旋转中心")
        axis_point = transform_point(point,parents[:switches[0]])
        if axis_point[0]!=0 or axis_point[2]!=0:
            if note:
                note("prefab_transform_for_variable_enemy_facing")
            else:
                raise ValueError("该敌人的受击挂点随朝向变化")
    return tuple(v+d for v,d in zip((enemy["col"],enemy["row"],enemy["tile_z"]-receiver["height"]),offset))


def moving_ranged_shot(tables,evidence,attack,event,world,enemy,*,frame,interval,mode_path,through,
                       allow_target_exit=False,caster_face_sign=None):
    shot = ranged_shot_release(tables,evidence,attack,attack_frame=frame,attack_interval=interval,
                               facing=event["view"],mode_path=mode_path)
    return flight_to_moving_target(tables,evidence,attack,event,world,enemy,shot,through=through,
                                   allow_target_exit=allow_target_exit,caster_face_sign=caster_face_sign)


def flight_to_moving_target(tables,evidence,attack,event,world,enemy,shot,*,through,allow_target_exit=False,
                            caster_face_sign=None):
    """Trace a baked ordinary attack or a committed skill projectile on the same native route."""
    face,place = event["view"],event["placement"]
    shot=dict(shot)
    release = shot["release_frame"]
    shot["reach_frame"] = None
    if release>=through:
        return shot
    key = enemy["id"]
    if allow_target_exit and world.by_id(key,release+1,projectile_born=release) is None:
        return dict(shot,target_invalid_frame=release+1)
    if enemy["col"]==place["col"] and caster_face_sign is None:
        raise ValueError(f"F{release} 的目标与干员同列，需要当时的角色左右朝向")
    origin,base_mount = stationary_cast_mounts(tables,evidence["operator_id"],shot["clock"],place,enemy,face,
                                              caster_face_sign=caster_face_sign)
    def targets_in_flight():
        for f in range(release+1,through+1):
            current = world.by_id(key,f,projectile_born=release)
            if current is None:
                if allow_target_exit:
                    # The only receiver in this direct shot has permanently left the map.
                    shot["target_invalid_frame"]=f
                    return
                raise ValueError(f"F{f} 空中弹体失去锁定目标，需处理离场或无效对象")
            yield (base_mount[0]+current["col"]-enemy["col"],base_mount[1]+current["row"]-enemy["row"],
                   base_mount[2]+current.get("height_offset",0))
    flight = projectile_reach(tables,evidence,dict(projectile=attack["_projectileKey"],frame=release+1,
                              origin=origin,target_mounts=targets_in_flight(),round_position=event["round_position"]))
    if flight["status"]=="reached":
        shot["reach_frame"]=flight["reach_frame"]
    elif flight["status"]!="in_flight_at_horizon":
        raise ValueError(f"F{release} 的移动弹道需要解析目标离场或特殊运动")
    return shot

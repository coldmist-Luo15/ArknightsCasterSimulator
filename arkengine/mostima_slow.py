"""Mostima's global/local movement-speed modifiers on an ordinary unobstructed stage route."""
from math import hypot

from .area_caster_skills import execute_area_skill
from .attack_range import circle_in_range
from .baked_mount import single
from .caster_first_spell import attack_key_clock
from .damage import ordinary_arts_hit
from .enemy_motion import ROUTE_REACH_DISTANCE, _limits, _straight_walk, advance_direct_move
from .enemy_speed import move_attribute, movement_cap
from .fixed import ONE
from .eyja_ignition import ignition_circle_overlap
from .lin_output_aura import trigger_contact
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .route_timing import birth_timer
from .routes import compile_route
from .stage_channel import UnsupportedChannelScene,ordinary_melee_enemy
from .target_priority import _cursor_distance, _distance_legs
from .volley_damage import _raw_f32


def _slow_assets(battle,global_delta):
    if global_delta:
        group, = [a for a in battle.evidence["equipment"].values() if any(
            c["script"]=="GlobalAuraAbility" and c["path"]=="global" for c in a["components"])]
        comps = group["components"]
        parent, = [c for c in comps if c["script"]=="AbilityParallelGroup"]
        children = {c["id"]:c for c in comps if c["script"] in ("AuraAbility","GlobalAuraAbility")}
        local,world = [children[ref["m_PathID"]] for ref in parent["fields"]["_abilities"]]
        if world["script"]!="GlobalAuraAbility" or world["fields"]["_interval"] or world["fields"]["_onlyDetactTargetWhenStarted"]:
            raise ValueError("全场光环首次应用和出兵订阅条件变化")
        components = (local,world)
    else:
        comps = battle.evidence["character"]["components"]
        components = [c for c in comps if c["script"]=="AuraAbility" and
                      c["path"] in ("Modes/Default/Body/Talent/2","Modes/S2/Body/Talent/2")]
        if len(components)!=2:
            raise ValueError("未能确认莫斯提马普通及技能模式的范围光环")
    for component in components:
        fields = component["fields"]
        buff, = fields["_buffs"]
        attr, = buff["attributes"]["attributeModifiers"]
        validator, = [c["fields"]["_targetOptions"] for c in comps if c["id"]==fields["_targetValidator"]["m_PathID"]]
        if (buff["overrideKey"]!="mostma_e_t[slow]" or buff["priorityBBKeys"]!=(["move_speed"] if global_delta else []) or
                (attr["attributeType"],attr["formulaItem"],attr["loadFromBlackboard"])!=(6,3,1) or
                (validator["targetSide"],validator["targetMotion"],validator["targetCategory"])!=(2,3,1)):
            raise ValueError("主观缓时光环的覆盖优先级、移速属性或目标筛选发生变化")
        if component["script"]=="AuraAbility":
            shape, = [c["fields"] for c in comps if c["script"]=="AutoLoadBoxRange" and c["path"]==component["path"]]
            if (shape["_useBlackboardRangeId"] or not fields["_removeBuffWhenTargetLeave"] or
                    not fields["_removeBuffWhenAbilityDetached"] or
                    not validator["enableAdvancedOptions"] or not validator["ignoreTargetFree"] or
                    validator["onlyIgnoreSomeOfTargetFreeCase"]):
                raise ValueError("主观缓时范围或离圈配置变化")
        elif validator["enableAdvancedOptions"]:
            raise ValueError("全局缓时的基本单位筛选发生变化")


def run_slow_channel(battle,result,pulses,through,finish,global_delta,*,mode=None,target_free_spans=(),ordinary_resume=None):
    """Propagate both allowed contacts at the native Box2D skin until the pulse selection agrees."""
    _slow_assets(battle,global_delta)
    t,scene,card = battle.tables,battle.scene,battle.card
    if battle.opening["clock"]["spawn_schedule"]!="native_wave" or not battle.opening["movement"]["continued_move_position_rounding"]:
        raise UnsupportedChannelScene("主观缓时的实际路线需要原生波次与实包坐标取整")
    born = [e for e in battle.opening["events"] if e["kind"]=="spawn" and e["frame"]<=through]
    stop=battle.opening.get("stop")
    if (stop and stop["frame"]<=through) or any(b["wave_index"] or b["block_fragment"] for b in born):
        raise UnsupportedChannelScene("此走位时窗需要不受击杀门槛影响的普通出兵波次")
    dt=t.fixed_timestep()
    profiles={}
    for key in {b["enemy_key"] for b in born}:
        unit=ordinary_melee_enemy(t,scene,key)
        prefab=unit["prefabKey"]["m_value"] or key
        attrs=unit["attributes"]
        timer=birth_timer(t.enemy_birth(prefab))
        if timer["status"]!="conditional_projection" or battle.request["movement"]["first_born_tick_delay_frames"]!=1:
            raise UnsupportedChannelScene("此路径需要原生出兵后次帧开始的普通出生更新")
        profiles[key]=dict(radius=t.enemy_circle(prefab),steering=t.enemy_steering(prefab),prefab=prefab,
                           weight=attrs["massLevel"]["m_value"],
                           first_move_delay=1+timer["first_default_request_update"],max_hp=attrs["maxHp"]["m_value"],
                           res=attrs["magicResistance"]["m_value"],stun_immune=attrs["stunImmune"]["m_value"],
                           taunt=attrs["tauntLevel"]["m_value"])
    result["initial_hp_by_enemy"]={k:p["max_hp"] for k,p in profiles.items()}
    if len(profiles)==1:
        result["initial_hp"]=next(iter(profiles.values()))["max_hp"]
    deploy = battle.deploy["frame"]
    local_delta = execute_area_skill(t,card,dict(kind="slow_aura",frame=deploy,in_range=True,skill_affecting=False))["result"]["move_speed_ratio_delta"]
    cells = battle.geometry["cells"]
    normal_cells = [(r["row"], r["col"]) for r in battle.opening["attack_range"]["cells"]]
    parts = battle.evidence["character"]["components"]
    ability, = (r["fields"] for r in parts
                if r["path"] == "Modes/Default/Body/Attack" and r["script"] == "MeleeAttack")
    area, = (r for r in parts if r["id"] == ability["_selector"]["m_PathID"])
    collider, = (r["fields"] for r in parts if r["path"] == area["path"] and r["script"] == "CircleCollider2D")
    if (area["script"] != "AdvancedSelector" or area["fields"]["_limitTargetNum"] or
            (ability["_selectTargetSource"], ability["_selectTargetTiming"], ability["_damageType"]) != (3, 1, 2) or
            not ability["_alwaysIncludeTarget"] or not ability["_waitForAttackEvent"] or
            ability["_activeBuffs"] or any(collider["m_Offset"].values())):
        raise ValueError("莫斯提马普攻的锁定主靶、范围判定或出伤配置改变")
    panel = card["panel_attributes"]
    interval = single(single(panel["baseAttackTime"]) * 100 / single(panel["attackSpeed"]))
    facing = "Back" if battle.deploy["direction"] == "UP" else "Front"
    delay, _ = attack_key_clock(t, battle.evidence, facing, ability["_animKey"], interval)
    born_end = deploy + _birth(t,card,battle.evidence,facing)["born_exit_update"]
    button_frame = battle.button["frame"]
    resume = finish+1 if ordinary_resume is None else ordinary_resume
    ordinary = dict(poll=born_end+1, delay=delay, period=fixed_wait_frames(single(interval/dt)),
                    radius=collider["m_Radius"], attack=_raw_f32(panel["atk"]), target=None, impact=None)
    result.update(scope="channel_window",first_contact=None,hits=[],global_move_speed_delta=global_delta,
                  local_move_speed_delta=local_delta,global_attachments=[],local_aura_contacts=[],pulse_targets=[],
                  ordinary_attacks=[],ordinary_hits=[])
    routes = {(index,key):compile_route(scene,index,key) for index,key in {(b["route_index"],b["enemy_key"]) for b in born}}
    rank_legs = {key: _distance_legs(scene, route) for key, route in routes.items()}
    by_frame,actors = {},{}
    for b in born:
        by_frame.setdefault(b["frame"],[]).append(b)
    # External TARGET_FREE at the beginning of an object frame. The basic global
    # and ignoreTargetFree local validators both retain this enemy for slowing.
    hidden = {}
    for span in target_free_spans:
        key,start,end=span["id"],span["start_frame"],span["end_frame"]
        if key not in {b["instance_id"] for b in born} or type(start) is not int or type(end) is not int or end<start:
            raise ValueError("不可选区间需要已出兵的敌人编号及闭区间起止帧")
        hidden.setdefault(key,[]).append((start,end))

    def targetable(identity,frame):
        return not any(start<=frame<=end for start,end in hidden.get(identity,()))

    def pending(reason,frame,identity):
        return dict(result,status="unresolved",reason=reason,through_frame=frame-1,
                    unresolved_frame=frame,instance_id=identity)
    validated = set()
    poll=mode["spell"]+1 if mode else None
    impact=None
    locks=[]
    if mode:
        from .forced_motion import enemy_push_profile, push_force, start_push, step_push
        result.update(scope="third_skill_attacks",skill_local_move_speed_delta=mode["local_delta"],attacks=[],forced_motion=[])
    for frame in range(min([deploy]+list(by_frame)),through+1):
        targets = []
        ready=[]
        ready_ranks = {}
        mode_active=bool(mode and mode["spell"] < frame < finish)
        if mode:
            cells=battle.geometry["cells"] if mode_active else mode["normal_cells"]
        if frame==button_frame+1:
            ordinary.update(poll=resume,target=None,impact=None)
        ordinary_start = frame == ordinary["poll"]
        ordinary_impact = frame == ordinary["impact"]
        skill_dealing = mode_active and frame == impact
        dealing=frame in pulses or skill_dealing
        for identity,actor in actors.items():
            if not actor["hp"]:
                continue
            route = actor["route"]
            profile=profiles[actor["key"]]
            radius,steering=profile["radius"],profile["steering"]
            can_target=targetable(identity,frame)
            paths = set()
            selected = set()
            seen=set()
            ranks = set()
            ended,resumed=set(),set()
            for pos,vel,index,inside,motion in actor["paths"]:
                old_pos = pos
                old_index = index
                pushing=motion is not None and motion.active
                cap_index=0 if frame<=deploy else ((3 if mode_active else 2) if inside else 1)
                if mode and frame==finish and inside:
                    normal_contact=trigger_contact(pos,radius,normal_cells)
                    if normal_contact is None:
                        return pending("normal_aura_contact_at_mode_exit",frame,identity)
                    if not normal_contact:
                        # The old aura detaches during the owner's object update. Earlier enemies
                        # have already moved with the skill aura; later ones keep the global buff.
                        cap_index=3 if actor["born"]<deploy else 1
                if pushing:
                    # UNBALANCE moves the rigidbody. MOVE retains its route cursor and steering velocity.
                    motion=step_push(t,profile["body"],motion,frame=frame,scene=scene)
                    if motion.boundary:
                        return pending(motion.boundary,frame,identity)
                    pos=motion.position
                    if not motion.active:
                        ended.add(pos)
                elif frame >= actor["move"] and frame>actor["stun_until"]:
                    if index>=len(route["commands"]):
                        return pending("enemy_at_route_end",frame,identity)
                    command = route["commands"][index]
                    check = actor["key"],route["route_index"],index
                    if check not in validated:
                        start = route["start"] if not index else route["commands"][index-1]["target"]
                        reason = _limits(scene,route,steering,command,index==0) or _straight_walk(scene,route,start,command)
                        if reason:
                            return pending(reason,frame,identity)
                        validated.add(check)
                    cap = actor["caps"][cap_index]
                    if cap*dt>ROUTE_REACH_DISTANCE:
                        return pending("requires_fast_route_snap",frame,identity)
                    goal = tuple(command["target"][a] for a in ("row","col"))
                    if motion is not None:
                        origin={"row":round(pos[0]),"col":round(pos[1])}
                        reason=_straight_walk(scene,route,origin,command,allow_same_cell=True)
                        if reason:
                            return pending(reason,frame,identity)
                    moved = advance_direct_move(pos,vel,goal,cap,steering,dt,True)
                    if moved is None:
                        return pending("coincident_checkpoint",frame,identity)
                    pos,vel = moved
                    if motion is not None:
                        resumed.add((old_pos,pos))
                        motion=None
                    if hypot(pos[0]-goal[0],pos[1]-goal[1])<=ROUTE_REACH_DISTANCE:
                        index+=1
                contact = (trigger_contact(pos,radius,cells) if frame>=deploy else False)
                if contact is None and pos==old_pos and frame>deploy and frame>actor["born"]+1:
                    contact = inside
                for active in ((False,True) if contact is None else (contact,)):
                    paths.add((pos,vel,index,active,motion))
                selected.add(can_target and circle_in_range(*pos,radius,cells,box_query=True))
                if (mode_active and frame==poll) or ordinary_start:
                    already_updated = actor["born"] < deploy and not pushing
                    observed = pos if already_updated else old_pos
                    eligible = can_target and circle_in_range(*observed,radius,cells,box_query=True)
                    seen.add(eligible)
                    if eligible:
                        cursor = index if already_updated else old_index
                        legs = rank_legs[route["route_index"], actor["key"]]
                        leg = legs.get(route["commands"][cursor]["source"]) if cursor < len(route["commands"]) else None
                        distance = _cursor_distance(dict(row=observed[0], col=observed[1]), leg) if leg else None
                        ranks.add((int(distance*ONE)//_raw_f32(.1), actor["born"], actor["order"])
                                  if distance is not None and not profile["taunt"] else None)
            if ended:
                result["forced_motion"].append(dict(kind="push_end",frame=frame,id=identity,positions=sorted(ended)))
            if resumed:
                result["forced_motion"].append(dict(kind="first_walk_after_push",frame=frame,id=identity,
                                                    steps=sorted(resumed)))
            actor["paths"] = paths
            occupancy = {path[3] for path in paths}
            if occupancy!=actor["occupancy"]:
                result["local_aura_contacts"].append(dict(frame=frame,instance_id=identity,phase="COROUTINES",
                    active=next(iter(occupancy)) if len(occupancy)==1 else None))
                actor["occupancy"] = occupancy
            if frame>=deploy and True in selected:
                if result["first_contact"] is None and len(selected)==1:
                    result["first_contact"]=dict(frame=frame,instance_id=identity,kind="attack_range_enter")
            if mode and frame==mode["spell"] and any(circle_in_range(*p[0],radius,battle.geometry["cells"],box_query=True)
                                                       for p in paths):
                return pending("skill_range_changed_around_live_enemy",frame,identity)
            if (mode_active and frame==poll or ordinary_start) and True in seen:
                if len(seen)!=1 or frame<actor["move"]:
                    return pending("aura_contact_changes_initial_skill_target",frame,identity)
                ready.append(identity)
                if None not in ranks and ranks:
                    ready_ranks[identity] = (min(ranks), max(ranks))
            if dealing:
                if len(selected)!=1 or (True in selected and frame<actor["move"]):
                    return pending("aura_contact_changes_pulse_selection",frame,identity)
                if True in selected:
                    targets.append(dict(id=identity,hp_q32=actor["hp"],max_hp=profile["max_hp"],res=profile["res"],
                                        stun_immune=profile["stun_immune"]))
        # Deployment is INPUT after live enemies update. Later native wave births receive the same global buff at Born.
        if global_delta and frame==deploy:
            result["global_attachments"].extend(dict(frame=frame,instance_id=i,phase="INPUT") for i in actors)
        for b in by_frame.get(frame,()):
            key=b["enemy_key"]
            route=routes[b["route_index"],key]
            profile=profiles[key]
            pos = tuple(route["start"][a] for a in ("row","col"))
            deltas=(0,global_delta,local_delta,mode["local_delta"]) if mode else (0,global_delta,local_delta)
            caps = [movement_cap(move_attribute(route["base_move_speed"],ratio_delta=d),route["level_move_multiplier"]) for d in deltas]
            actors[b["instance_id"]] = dict(route=route,key=key,born=frame,order=b["order"],move=frame+profile["first_move_delay"],
                paths={(pos,(0.,0.),0,False,None)},caps=caps,occupancy={False},hp=profile["max_hp"]*ONE,stun_until=-1)
            if global_delta and frame>=deploy:
                result["global_attachments"].append(dict(frame=frame,instance_id=b["instance_id"],phase="COROUTINES"))
            if dealing and targetable(b["instance_id"],frame) and circle_in_range(*pos,profile["radius"],cells,box_query=True):
                return pending("spawn_order_at_pulse_needs_coroutine_index",frame,b["instance_id"])
        if mode and frame == finish and frame == impact and any(actors[k]["hp"] for k in locks):
            return pending("skill_impact_at_natural_mode_exit",frame,locks[0])
        if ordinary_start:
            if ready:
                target = ready[0]
                if len(ready) > 1:
                    if len(ready_ranks) != len(ready):
                        return pending("ordinary_attack_priority_after_skill",frame,target)
                    target = min(ready, key=lambda k: ready_ranks[k][0])
                    if any(ready_ranks[target][1] >= ready_ranks[k][0] for k in ready if k != target):
                        return pending("ordinary_attack_priority_after_skill",frame,target)
                ordinary["target"] = target
                ordinary["impact"] = frame + ordinary["delay"]
                ordinary["poll"] = frame + ordinary["period"]
                result["ordinary_attacks"].append(dict(frame=frame,target=target,hit_frame=ordinary["impact"]))
            else:
                ordinary["poll"] += 3
        if ordinary_impact:
            primary = ordinary["target"]
            if not actors[primary]["hp"] or not targetable(primary,frame):
                return pending("ordinary_splash_primary_invalid",frame,primary)
            centers = {p[0] for p in actors[primary]["paths"]}
            splash = []
            for identity,actor in actors.items():
                if not actor["hp"] or not targetable(identity,frame):
                    continue
                profile = profiles[actor["key"]]
                touching = {ignition_circle_overlap(dict(row=row,col=col),dict(row=r,col=c),
                                                     ordinary["radius"],profile["radius"])
                            for row,col in centers for (r,c),*_ in actor["paths"]}
                if len(touching) != 1:
                    return pending("ordinary_splash_changes_at_aura_boundary",frame,identity)
                if identity == primary or True in touching:
                    if actor["born"] == frame:
                        return pending("spawn_order_at_ordinary_splash",frame,identity)
                    splash.append((identity, actor, profile))
            for identity,actor,profile in splash:
                hit = ordinary_arts_hit(attack_raw=ordinary["attack"],hp_raw=actor["hp"],
                                        resistance_raw=_raw_f32(profile["res"]))
                actor["hp"] = hit.hp_after_raw
                result["ordinary_hits"].append(dict(frame=frame,id=identity,primary=primary,
                                                    damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw))
        if mode_active and frame==poll:
            if ready:
                impact=frame+mode["sample"]
                locks = [k for k in ready if k not in ready_ranks or not any(
                    ready_ranks[j][1] < ready_ranks[k][0] for j in ready_ranks if j != k)]
                result["attacks"].append(dict(frame=frame,trigger_targets=ready,baked_on_attack_frame=impact-1,hit_frame=impact))
                poll=frame+mode["period"]
            else:
                poll=frame+3
        if skill_dealing:
            in_range = {r["id"] for r in targets}
            extras = {key if key not in in_range and actors[key]["hp"] and targetable(key,frame) else None for key in locks}
            if len(extras) > 1:
                return pending("original_attack_input_inclusion_depends_on_priority",frame,locks[0])
            if extras and (key := next(iter(extras))) is not None:
                actor = actors[key]
                targets.append(dict(id=key, res=profiles[actor["key"]]["res"]))
            for r in targets:
                key=r["id"]
                actor=actors[key]
                profile=profiles[actor["key"]]
                hit=ordinary_arts_hit(attack_raw=mode["attack"],hp_raw=actor["hp"],resistance_raw=_raw_f32(r["res"]))
                actor["hp"]=hit.hp_after_raw
                result["hits"].append(dict(frame=frame,id=key,damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw,
                    push_force=mode["force"],possible_positions=sorted({p[0] for p in actor["paths"]})))
                if not hit.hp_after_raw:
                    continue
                force=push_force(t,mode["force"],profile["weight"])
                result["forced_motion"].append(dict(kind="push_request",frame=frame,id=key,weight=profile["weight"],
                    force_level=mode["force"],force=force,source=(battle.deploy["row"],battle.deploy["col"]),
                    move_multiplier=actor["route"]["level_move_multiplier"],positions=result["hits"][-1]["possible_positions"]))
                if not force:
                    continue
                if "body" not in profile:
                    profile["body"]=enemy_push_profile(t,profile["prefab"])
                paths=set()
                for pos,vel,index,inside,previous in actor["paths"]:
                    direction=(pos[0]-battle.deploy["row"],pos[1]-battle.deploy["col"])
                    motion=start_push(t,profile["body"],pos,direction,force_level=mode["force"],weight=profile["weight"],
                                      frame=frame,previous=previous,move_multiplier=actor["route"]["level_move_multiplier"])
                    if motion is not None and motion.boundary:
                        return pending(motion.boundary,frame,key)
                    paths.add((pos,vel,index,inside,motion))
                actor["paths"]=paths
            continue
        if frame in pulses:
            result["pulse_targets"].append(dict(frame=frame,targets=[r["id"] for r in targets]))
        if targets:
            effect=execute_area_skill(t,card,dict(kind="channel_hit",frame=frame,
                actual_attack=card["panel_attributes"]["atk"],targets=targets))["result"]
            step = _raw_f32(dt)
            ticks = (_raw_f32(effect["stun_seconds"])+step-1)//step
            for hit in effect["hits"]:
                actor=actors[hit["id"]]
                actor["hp"]=hit["hp_after_q32"]
                if hit["stun_applied"]:
                    actor["stun_until"]=frame+ticks
                positions = sorted({p[0] for p in actor["paths"]})
                result["hits"].append(dict(**hit,frame=frame,stun_seconds=effect["stun_seconds"],
                    stationary_through_if_alive=actor["stun_until"] if hit["stun_applied"] else None,possible_positions=positions))
    result.update(through_frame=through,status="through_horizon" if through<finish else "skill_finished" if mode else "channel_finished")
    return result

"""One Nymph S2 on a waiting ground target, through fear movement and its delayed blast."""
from math import hypot, isfinite

from .attack_range import circle_in_range, range_cells
from .battle_random import BattleRandom
from .baked_mount import single
from .caster_flight import projectile_reach
from .caster_prefabs import caster_prefab_evidence
from .caster_equipment_traits import _trait_values
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import NYMPH, execute_elemental_event, _board
from .enemy_elements import EnemyElements
from .enemy_motion import ROUTE_REACH_DISTANCE, advance_direct_move
from .enemy_speed import move_attribute, movement_cap
from .fixed import ONE, mul
from .mystic_timing import fixed_wait_frames
from .nymph_soul import SoulOnTarget, SoulRules
from .elemental_projectiles import stationary_cast_mounts
from .volley_damage import _raw_f32


def _approach(tables,enemy,event,frame,until):
    pos = (single(enemy["row"]),single(enemy["col"]))
    velocity = tuple(map(single,enemy.get("velocity",(0,0))))
    motion = enemy.get("pre_hit_motion","move" if any(velocity) else "wait")
    if motion=="wait" and not any(velocity):
        return {f:(pos,velocity) for f in range(frame,until+1)}
    if motion!="move" or len(velocity)!=2:
        raise ValueError("命中前请给直行目标的初速，停驻状态初速须为零")
    terrain = event["fear_terrain"]
    cells = {tuple(c) for c in terrain["walkable"]}
    row,col = zip(*cells)
    goal = tuple(map(single,enemy["route_target"]))
    def cell(p):
        return tuple(round(single(x)) for x in p)
    if (cell(pos) not in cells or cell(goal) not in cells or (len(set(row))>1 and len(set(col))>1)
            or len(cells)!=1+max(row)-min(row)+max(col)-min(col) or enemy.get("blocked")
            or enemy.get("movement_state","MOVE")!="MOVE" or enemy.get("remaining_wait_frames",0)):
        raise ValueError("主弹发射前的运动场景需要无障碍直廊中的单个普通移动段")
    dt = tables.fixed_timestep()
    cap = movement_cap(move_attribute(enemy["move_speed"],ratio_delta=enemy.get("move_ratio_delta",0)),
                       terrain.get("level_move_multiplier",1))
    steering = tables.enemy_steering(enemy["prefab"])
    track = {frame:(pos,velocity)}
    for f in range(frame+1,until+1):
        if hypot(goal[0]-pos[0],goal[1]-pos[1])<=max(ROUTE_REACH_DISTANCE,single(cap*dt)):
            break
        pos,velocity = advance_direct_move(pos,velocity,goal,cap,steering,dt,event["round_position"])
        if cell(pos) not in cells:
            break
        track[f] = pos,velocity
    return track


def run_nymph_s2(tables,card,event,*,stream=None,map_stream=None):
    """First ready INPUT, after an enemy entered following the caster's OBJECTS phase."""
    if card["operator_id"]!=NYMPH or card["skill"]!=2 or not card["module"]:
        raise ValueError("需要妮芙二技能与所选常规模组")
    start,placement,enemy = event["frame"],event["placement"],event["enemy"]
    facing = event["view"]
    if (type(start) is not int or start<0 or facing not in ("Front","Back") or type(enemy["fear_immune"]) is not bool or
            placement["col"]==enemy["col"] or type(event["round_position"]) is not bool):
        raise ValueError("需要合法部署帧、外观朝向和目标的恐惧免疫状态")
    radius = enemy.get("radius")
    if radius is None:
        radius = tables.enemy_circle(enemy["prefab"])
    if not all(isfinite(v) for v in (placement["tile_z"],enemy["tile_z"],radius)) or radius<=0:
        raise ValueError("目标半径和格子高度无效")
    cells = range_cells(card["base_range"],placement,event["rows"],event["cols"])
    if not circle_in_range(enemy["row"],enemy["col"],radius,cells,box_query=True):
        raise ValueError("敌人没有进入技能索敌范围")
    evidence = caster_prefab_evidence(tables,NYMPH,2,skill_level=card["skill_level"],
                                      module_id=card["module"],module_level=card["module_level"])
    opening = deploy_to_first_cast(tables,card,evidence,deploy_frame=start,direction=placement["direction"],facing=facing)
    frame,cast = opening.through_frame,opening.cast
    attack, = [c["fields"] for c in evidence["skill"]["components"] if c["script"]=="RangedAttack"]
    projectile = attack["_projectileKey"]
    if (attack["_mountPointType"]!=2 or attack["_useMountGroup"] or attack["_projectileDestination"] or
            attack["_atkScale"] or [b["templateKey"] for b in attack["_activeBuffs"]]!=["nymph_s_2[damage]"]):
        raise ValueError("技能发射挂点或命中模板变化")
    bb = {b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    templates = tables.read("battle/buff_template_data.json")
    branch = templates["nymph_s_2[trace_taregt]"]["eventToActions"]["ON_BUFF_START"][1]
    fear = branch["_failNodes"][1]
    if (not branch["_conditionNode"]["$type"].split("+")[-1].startswith("IsCharacterOrTokenOrTrap,")
            or fear["_buffOwner"]!="BUFF_OWNER" or not fear["_buff"]["loadFromDB"] or fear["_buff"]["buffKey"]!="fear"):
        raise ValueError("妮芙主弹施加恐惧的对象或前后顺序改变")
    for key in ("nymph_s_2[trace_taregt]","nymph_s_2[damage]"):
        actions = templates[key]["eventToActions"]["ON_BUFF_START"]
        damage, = [n for n in actions if "+AdvancedApplyDamage," in n["$type"]]
        buildup, = [n for n in actions if "+ApplyElementDamage," in n["$type"]]
        if not damage["_assignRealDamageToBB"] or buildup["_elementDamageType"]!="DARK":
            raise ValueError("技能的法伤取样或元素属性发生变化")
    delays = set()
    for p in evidence["projectiles"][projectile].values():
        active = {c["script"]:c["fields"] for c in p["components"] if c["active"] and c["enabled"]}
        life = active["SimpleProjectile"]["_actionController"]
        move,hit = active["AdvancedMovement"],active["HitBehaviour"]
        collider = active["CircleCollider2D"]
        if (not life["_addExtraBuffsWhenReached"] or any(collider["m_Offset"].values()) or
                not collider["m_IsTrigger"] or collider["m_Radius"]!=bb["projectile_range"] or
                move["_forceResetToTargetPosWhenReached"] or move["_fixOnProjectileReached"] or
                [b["templateKey"] for b in life["_extraBuffsInTheEnd"]]!=["nymph_s_2[trace_taregt]"] or
                not move["_clearTraceTargetWhenReached"] or not move["_updateDelayTimeOnlyOnce"] or
                not hit["_onlyCheckHitWhenStop"] or hit["_exceptTraceTarget"] or hit["_onlyTraceTarget"]):
            raise ValueError("二技能到达、停弹或碰撞对象配置变化")
        delays.add(move["_delayAfterReached"])
    delay, = delays
    origin,target = stationary_cast_mounts(tables,NYMPH,cast["ability_clock"],placement,enemy,facing)
    # The reach predicate uses the mount. DealReached then writes the living target's
    # map X/Y to the projectile, freezes the movement and clears the trace reference.
    first = cast["spell_frame"]+1
    track = _approach(tables,enemy,event,frame,first+31)
    for f in range(frame,first):
        if f not in track or not circle_in_range(*track[f][0],radius,cells,box_query=True):
            raise ValueError("主弹释放前敌人已离开已核对的直线移动段或索敌范围")
    mounts = [(target[0]+p[1]-enemy["col"],target[1]+p[0]-enemy["row"],target[2])
              for f,(p,v) in track.items() if f>=first]
    if not mounts:
        raise ValueError("敌人在首个弹体更新之前已到达路线检查点")
    flight = projectile_reach(tables,evidence,dict(frame=first,origin=origin,target_mounts=mounts,
                        projectile=projectile,round_position=event["round_position"]))
    if flight["status"]!="reached":
        raise ValueError("投射物在单次技能窗口中没有确认到达")
    reach,dt = flight["reach_frame"],tables.fixed_timestep()
    center,velocity = track[reach]
    wait = fixed_wait_frames(single(delay/dt))
    # StartBattleCoroutine starts in projectile OBJECTS. Its first fixed waiter is
    # already queued when COROUTINES runs later in that same deterministic tick.
    splash = reach+wait-1
    if splash>=frame+fixed_wait_frames(single(cast["ability_clock"]["cached_duration_seconds"]/dt))-1:
        raise ValueError("范围伤害与脱离施法后的普攻周期重叠")
    state = EnemyElements.from_scene_enemy(tables,enemy)
    soul = SoulOnTarget(SoulRules.from_card(tables,card,evidence))
    stacks = 0
    stat = _raw_f32(card["panel_attributes"]["atk"])
    stack_bonus = _raw_f32(_board(card,1,"2")["atk"])
    burst_factor = _raw_f32(_trait_values(card).get("damage_scale",1))
    def effective_attack():
        return mul(stat,ONE+stacks*stack_bonus)
    events = [dict(frame=start,kind="deploy",sp=opening.initial_sp),dict(frame=opening.first_ready_frame,kind="first_ready",sp=opening.sp),
              dict(frame=frame,kind="target_enters_before_input")]
    events.extend(cast["events"])
    motion = None
    location = track.get(splash,(None,None))[0] if enemy["fear_immune"] else center
    stop = "secondary_blast_settled"
    if "seed" in event:
        if stream is not None:
            raise ValueError("不能同时提供恐惧独立种子和共享随机流")
        stream = BattleRandom(event["seed"])
    for current in range(frame,splash+1):
        state.tick(current)
        soul.tick(current,state,effective_attack(),burst_factor)
        if current not in (reach,splash):
            continue
        before = state.hp
        point = "primary" if current==reach else "splash"
        if before<=HP_EPSILON:
            break
        if current==splash:
            if location is None:
                stop = "requires_next_normal_route_segment"
                break
            distance = hypot(location[0]-center[0],location[1]-center[1])
            limit = bb["projectile_range"]+radius
            if abs(distance-limit)<1e-5:
                stop = "splash_tangent_unresolved"
                break
            if distance>limit:
                events.append(dict(kind="splash_miss",frame=current,body_position=location,blast_center=center))
                break
        dark = state.burst=="DARK"
        soul.before_attack_damage()
        hit = ordinary_arts_hit(attack_raw=mul(effective_attack(),_raw_f32(bb["atk_scale"])),
                                hp_raw=before,resistance_raw=enemy["arts_resistance_q32"]+state.res_add,
                                output_scale_raw=burst_factor if state.burst else ONE)
        state.hp = hit.hp_after_raw
        row = dict(kind=point,frame=current,hp_before_q32=before,arts_damage_q32=hit.damage_raw,
                   hp_after_q32=state.hp)
        if current==reach:
            row.update(fear_request_seconds=bb["fear"],fear_applied=not enemy["fear_immune"],immune=enemy["fear_immune"])
        else:
            row.update(body_position=location,blast_center=center)
        events.append(row)
        state.apply_buildup(current,"DARK",mul(before-state.hp,_raw_f32(bb["ep_damage_ratio"])))
        if state.hp<=HP_EPSILON:
            break
        if state.burst=="DARK" and not dark:
            row["talent"]=execute_elemental_event(tables,card,dict(kind="elemental_skill",frame=current,
                            action="burst_start",burst_type="DARK",
                            in_attack_range=circle_in_range(*(center if current==reach else location),radius,cells,box_query=True),
                            prior_stacks=stacks))
            stacks = row["talent"]["stacks_after"]
        soul.after_s2_buildup(state,reborn=enemy.get("reborn",False))
        if soul.active:
            row["attached_talent_tick_scale"]=soul.active.scale/ONE
        if current==reach and not enemy["fear_immune"]:
            if "fear_terrain" not in event or not all(k in enemy for k in ("move_speed","route_target")):
                stop = "requires_fear_terrain_and_route"
                break
            if stream is None:
                stop = "requires_fear_battle_random"
                break
            from .enemy_fear import trace_fear_motion
            motion = trace_fear_motion(tables,enemy=dict(enemy,row=center[0],col=center[1],velocity=velocity),
                source=(placement["row"],placement["col"]),
                terrain=event["fear_terrain"],start_frame=reach,end_frame=splash,duration_seconds=bb["fear"],
                stream=stream,map_stream=map_stream)
            location = motion.get("positions",{}).get(splash)
            if location is None:
                stop = "fear_motion_unresolved_before_splash"
                break
    effect_end = event.get("effect_end_frame", current)
    if type(effect_end) is not int or effect_end<current:
        raise ValueError("技能余效终点不能早于已结算的技能节点")
    withdrawal = event.get("withdraw_frame")
    if withdrawal is not None and (type(withdrawal) is not int or not current<withdrawal<=effect_end):
        raise ValueError("二技能余效的撤退输入须在本次结算之后、余效时窗内")
    effect_frame = current
    if stop=="secondary_blast_settled" and state.hp>HP_EPSILON:
        for effect_frame in range(current+1,effect_end+1):
            state.tick(effect_frame)
            soul.tick(effect_frame,state,effective_attack(),burst_factor)
            if effect_frame==withdrawal:
                soul.withdraw()
                events.append(dict(kind="withdraw",frame=effect_frame,phase="input"))
            if state.hp<=HP_EPSILON or not (state.burst or soul.listeners):
                break
    return dict(status="nymph_s2_first_cast",through_frame=current,effect_through_frame=effect_frame,
                talent_stacks=stacks,flight=flight,muzzle=origin,
                target_mount=mounts[reach-first],approach_at_impact=dict(position=center,velocity=velocity),
                stop="target_defeated" if state.hp<=HP_EPSILON else stop,blast_center=center,fear_motion=motion,
                deferred_stop_resumes=wait,events=events,element_events=state.events,receiver=state.snapshot(),
                conditions=["stationary_or_single_straight_ground_route_before_impact","initially_empty_scene_then_target_enters_before_ready_input",
                            "default_skin_no_other_source_or_damage_modifiers","through_secondary_blast"])

"""Mantra S1 attack recovery and replacement projectile, and S1/S2 receiver settlement."""
import json

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .chain_targets import nearest_chain_target
from .elemental_casters import MANTRA
from .elemental_projectiles import stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .mystic_timing import fixed_wait_frames
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _fields(evidence, slot):
    source = evidence["skill"] if slot==1 else evidence["character"]
    path = "" if slot==1 else "Modes/S2/Body/Attack"
    node, = [n for n in source["components"] if n["script"]=="RangedAttackWithExtraActions" and n["path"]==path]
    return node["fields"]


def burst_output_scale(card):
    """Only PRI-X attaches mantra_e_002[tr]'s ON_OUTPUT_DAMAGE modifier."""
    if card["module"] is None:
        return ONE
    return _raw_f32(_trait_values(card)["damage_scale"])


def apply_mantra_hit(tables,card,evidence,state,frame,*,attack_q32,arts_resistance_q32,bounce=0):
    """Run one confirmed primary or bounced projectile action against a persistent receiver."""
    slot = card["skill"]
    if card["operator_id"]!=MANTRA or slot not in (1,2):
        raise ValueError("需要真言一或二技能")
    bb = {r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    if type(bounce) is not int or not 0<=bounce<=(0 if slot==1 else bb["chain_times"]):
        raise ValueError("不合法的跳跃序号")
    args = _fields(evidence,slot)
    arts,_,buildup,_,condition = json.loads(args["_actions"]["SerializedState"])
    if (not args["_overwriteAbilityActions"] or arts["_damageType"]!="MAGICAL" or not arts["_assignRealDamageToBB"] or
            buildup["_fixedEpDamageKey"]!="value" or buildup["_elementDamageType"]!="SANITY" or
            condition["_conditionNode"]["_elementType"]!="SANITY"):
        raise ValueError("真言弹体的法伤、损伤或爆发追加条件已变化")
    prefix = "" if slot==1 else "attack@"
    scale = bb[prefix+("chain.atk_scale" if bounce else arts["_atkScaleVar"])]
    ratio = bb[prefix+buildup["_fixedEpDamageScale"]]
    module = burst_output_scale(card)
    before = state.hp
    if before<=HP_EPSILON:
        return dict(frame=frame,status="already_defeated")
    hit = ordinary_arts_hit(attack_raw=mul(attack_q32,_raw_f32(scale)),hp_raw=before,
                            resistance_raw=arts_resistance_q32)
    dealt = mul(hit.damage_raw,module if state.burst else ONE)
    state.hp = max(0,before-dealt)
    if state.hp<HP_EPSILON:
        state.hp = 0
    ep = state.apply_buildup(frame,"SANITY",mul(before-state.hp,_raw_f32(ratio)))
    extra = None
    if state.burst=="SANITY" and state.hp>HP_EPSILON:
        damage = condition["_succeedNodes"][0]
        if damage["_damageType"]!="ELEMENT":
            raise ValueError("真言爆发后追加伤害类型变化")
        extra = state.apply_elemental_hp(frame,mul(attack_q32,_raw_f32(bb[prefix+damage["_atkScaleVar"]])),
                                         source="mantra_projectile",output_scale_q32=module)
    return dict(frame=frame,arts_damage_q32=dealt,hp_before_q32=before,buildup=ep,extra=extra,hp_after_q32=state.hp)


def mantra_bounce_profile(tables,card,ev):
    """Resolve the equipped mode's attack clock and native bouncing projectile fields."""
    f = _fields(ev,2)
    selector,=(n["fields"] for n in ev["character"]["components"]
               if n["path"]=="Modes/S2/Body/Attack/Selector" and n["script"]=="AdvancedSelector")
    if (f["_animKey"] or f["_waitForAttackEvent"] or f["_waitForProjectileInvalid"] or f["_timeMode"]!=0
            or f["_mountPointType"]!=2 or f["_useMountGroup"] or f["_maxAnimScale"]!=-1
            or (f["_selectTargetSource"],f["_selectTargetTiming"],f["_alwaysIncludeTarget"])!=(2,0,1)
            or any(f[k] for k in ("_allowNoTarget","_interuptIfTargetDead","_emitToInputPosWhenTargetIsInvalid",
                                  "_emitToInputRootTileWhenTargetIsInvalid"))
            or (selector["_targetMotion"],selector["_postFilter"],selector["_maxNum"],selector["_limitTargetNum"])!=(3,4,1,1)):
        raise ValueError("技能模式的空动作前摇或挂点已变化")
    dt = tables.fixed_timestep()
    bb = {r["key"]:r["value"] for r in ev["skill_blackboard"]}
    attr = card["panel_attributes"]
    period = single(single(attr["baseAttackTime"]+bb["base_attack_time"])*100/single(attr["attackSpeed"]))
    delay = fixed_wait_frames(single(single(min(period,f["_preDelay"]))/dt))
    clocks = set()
    projectile = f["_projectileKey"]
    for variant in ev["projectiles"][projectile].values():
        parts = {r["script"]:r["fields"] for r in variant["components"] if r["active"] and r["enabled"]}
        move,hit = parts["BouncedAdvancedMovement"],parts["BounceHitBehaviour"]
        selector=parts["NearestRangeSelector"]
        if (not hit["_onlyCheckHitWhenReachTarget"] or hit["_bounceTimesAsDamageTimes"] or hit["_waitFirstPeriod"]
                or hit["_allowRepetitionIfNoTarget"] or hit["_dealHitWithOtherSelector"] or hit["_isChainLightingUse"]
                or move["_followLogicProjectile"] or move["_resetToTargetPosWhenReached"] or move["_moveType"]!=1 or move["_delayToStart"] or move["_delayToBounce"]
                or move["_comeBack"] or move["_delayAfterReached"] or not hit["_overWriteAtkScale"]
                or hit["_bounceConditionValidator"]["m_PathID"] or hit["_sortTargetsByHitCountAsc"]
                or hit["_dontHitWhenTargetDead"] or hit["_useMapPosIfTargetInvalid"]
                or hit["_canSelectAllExceptTracetarget"] or selector["_limitTargetNum"] or selector["_targetMotion"]!=3):
            raise ValueError("真言弹体的跳跃模式或确认命中前摇改变")
        clocks.add((move["_speed"],move["_speedAfterFirstReach"],parts["CircleCollider2D"]["m_Radius"]))
    (speed0,speed1,radius), = clocks
    life,=(p["fields"] for p in ev["projectiles"][projectile]["logic"]["components"] if p["script"]=="BounceProjectile")
    if life["_lifeTimeType"]!=2 or life["_managedBySource"]:
        raise ValueError("真言弹体的默认寿命或归属已变化")
    return dict(projectile=projectile,period=period,delay=delay,speed=speed0,bounce_speed=speed1,radius=radius,
                jumps=int(bb["chain_times"]))


def run_mantra_bounce(tables,card,event):
    """One first projectile against stationary enemies with only its primary in cast range."""
    if card["operator_id"]!=MANTRA or card["skill"]!=2:
        raise ValueError("需要真言二技能")
    place,face = event["placement"],event.get("view","Front")
    units = [event["enemy"]]
    if "secondary" in event:
        if "secondaries" in event:
            raise ValueError("重复提供了真言的弹射候选列表")
        units.append(event["secondary"])
    else:
        units.extend(event.get("secondaries",[]))
    cells = range_cells(card["base_range"],place,event["rows"],event["cols"])
    for i,e in enumerate(units):
        if not e["palsy_immune"] or circle_in_range(e["row"],e["col"],e["radius"],cells,box_query=True)!=(i==0):
            raise ValueError("主目标须在基础范围内，其余弹射候选须在范围外，均为免麻痹静止地面敌人")
    ev = caster_prefab_evidence(tables,MANTRA,2,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    profile=mantra_bounce_profile(tables,card,ev)
    dt=tables.fixed_timestep()
    attr=card["panel_attributes"]
    period,delay,projectile=profile["period"],profile["delay"],profile["projectile"]
    speed0,speed1,radius=profile["speed"],profile["bounce_speed"],profile["radius"]
    clock = dict(action="Attack",first_key_seconds=0,playback_speed=1)
    shots = [stationary_cast_mounts(tables,MANTRA,clock,place,e,face,static_mode_clips=("Default","Idle","Skill_2_Begin","Skill_2_Loop"))
             for e in units]
    pos = shots[0][0]
    candidates=[]
    for ident,unit in enumerate(units):
        row=dict(unit,id=ident)
        if "distance_to_exit" in row:
            distance=row["distance_to_exit"]
            uid=row.get("instance_uid")
            if type(distance) not in (int,float) or not 0<=distance<1000 or type(uid) is not int or not 0<=uid<=0xffffffff:
                raise ValueError("等距普通目标需要有效的剩余路程和无符号实例编号")
            taunt=row.get("taunt_level",0)
            if type(taunt) is not int or abs(taunt)>20:
                raise ValueError("普通目标嘲讽等级超出定点计算范围")
            row["hatred_q32"]=-_raw_f32(distance)+_raw_f32(single(taunt*100000000))
        elif any(k in row for k in ("hatred_q32","taunt_level","instance_uid")):
            raise ValueError("静止弹射靶须提供剩余路程才能从普通敌人属性求仇恨")
        candidates.append(row)
    radii={i:u["radius"] for i,u in enumerate(units)}
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=event["frame"],direction=place["direction"],facing=face)
    cast = opening.cast
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("真言未进入二技能模式")
    first = cast["spell_frame"]+1
    release = first+delay-1
    events = [dict(kind="deployment",frame=event["frame"]),dict(kind="first_ready",frame=opening.first_ready_frame),*cast["events"],
              dict(kind="attack_start",frame=first),dict(kind="projectile_release",frame=release,projectile=projectile)]
    states = [EnemyElements.from_scene_enemy(tables,e) for e in units]
    idx,visited,primary_hit=0,[],None
    period_ticks=fixed_wait_frames(single(period/dt))
    for frame in range(release+1,release+2*period_ticks+1):
        if primary_hit is not None and frame>=primary_hit+period_ticks and states[0].hp>HP_EPSILON:
            raise ValueError("下一次主攻击也已到达，需要推进两个弹体")
        for state in states:
            state.tick(frame)
        sample = ordinary_trace_step(pos,shots[idx][1],effective_dt=dt,round_position=event["round_position"],
                                     speed=speed0 if idx==0 else speed1)
        pos = sample.position
        if not sample.reached:
            continue
        hit = apply_mantra_hit(tables,card,ev,states[idx],frame,attack_q32=_raw_f32(attr["atk"]),
                               arts_resistance_q32=units[idx]["arts_resistance_q32"],bounce=len(visited))
        events.append(dict(kind="bounce_impact" if idx else "primary",target=idx,**hit))
        if not visited:
            primary_hit=frame
        visited.append(idx)
        if len(visited)>profile["jumps"]:
            break
        next_id=nearest_chain_target(frame,units[idx],(r for r in candidates if states[r["id"]].hp>HP_EPSILON),
                                     radii,visited,radius,max_candidates=128)
        if next_id is None:
            break
        events.append(dict(kind="change_bounce_target",frame=frame,from_target=idx,to_target=next_id))
        pos = shots[idx][1]
        idx=next_id
    else:
        raise ValueError("此次飞行跨越下一发，需使用多弹体场景")
    return dict(status="mantra_first_bounce_scene",through_frame=frame,events=events,
                receivers=[s.snapshot() for s in states],element_events=[s.events for s in states],
                conditions=["one_stationary_primary_and_nearest_valid_secondaries","no_repeat_targets",
                            "through_first_projectile"])

def run_mantra_impacts(tables,card,event):
    """Apply supplied confirmed S1/S2 hits, retaining the same target's HP and SANITY bar."""
    start,enemy = event["frame"],event["enemy"]
    if type(start) is not int or start<0 or enemy["palsy_immune"] is not True:
        raise ValueError("此受击序列需要明确的起始帧及麻痹免疫目标")
    ev = caster_prefab_evidence(tables,card["operator_id"],card["skill"],skill_level=card["skill_level"],
                                module_id=card["module"],module_level=card["module_level"])
    state = EnemyElements.from_scene_enemy(tables,enemy)
    previous = start-1
    hits = []
    for shot in event["impacts"]:
        frame = shot["frame"]
        if type(frame) is not int or frame<max(start,previous):
            raise ValueError("命中帧必须按顺序位于初始帧之后")
        for tick in range(previous+1,frame+1):
            state.tick(tick)
        if state.hp<=HP_EPSILON:
            break
        hits.append(apply_mantra_hit(tables,card,ev,state,frame,attack_q32=_raw_f32(card["panel_attributes"]["atk"]),
                            arts_resistance_q32=enemy["arts_resistance_q32"],bounce=shot.get("bounce",0)))
        previous = frame
    return dict(status="confirmed_mantra_impacts",hits=hits,element_events=state.events,receiver=state.snapshot(),
                conditions=["supplied_valid_projectile_arrivals_after_receiver_update","sole_source_and_palsy_immune_receiver"])

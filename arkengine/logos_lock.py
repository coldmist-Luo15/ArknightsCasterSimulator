"""Logos S2 persistent lock on a single stationary target, including the delta-module receiver."""
from dataclasses import dataclass

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_flight import projectile_reach
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON
from .elemental_casters import _board
from .elemental_projectiles import stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .mystic_timing import fixed_wait_frames
from .volley_damage import _raw_f32


LOGOS = "char_4133_logos"


def lock_arts(state,frame,card,attack,resistance,source):
    if (card["module_info"] or {}).get("typeIcon")=="ccr-d":
        hit=state.hit_with_module_arts(frame,card,attack_q32=attack,effective_arts_resistance_q32=resistance)
        return hit.damage_raw
    hit=state.receive_ordinary(frame,source=source,source_card=card,damage_type="arts",
                               attack_q32=attack,mitigation_q32=resistance)
    return hit["damage_q32"]


def talent_launch(tables,card,ev,event,emission,draw,*,caster_face_sign=None):
    mount,delay = draw["mount"],single(draw["birth_delay_seconds"])
    if type(mount) is not int or not 0<=mount<=5 or delay<0:
        raise ValueError("词句论断需要 SPECIAL_0 至 SPECIAL_5 的取样与非负出生延迟")
    modules = (card["module_info"] or {}).get("typeIcon")=="ccr-d" and card["module_level"]>=2
    key = "logos_e_002_t_1" if modules else "logos_t_1"
    emit = tables.read("battle/buff_template_data.json")[key]["eventToActions"]["ON_ABILITY_CAST_ON_TARGET"][-1]
    if (not emit["_useMountPointGroup"] or emit["_mountPointGroup"]["loopType"]!="RANDOM" or
            emit["_mountPointGroup"]["mountPoints"]!=["SPECIAL_"+str(i) for i in range(6)]):
        raise ValueError("词句论断的发射挂点集合变化")
    projectile = "projectile_chr_logos_t"
    for variant in ev["projectiles"][projectile].values():
        spec, = [c["fields"] for c in variant["components"] if c["script"]=="SimpleProjectile"]
        if not 0<=delay<=spec["_randomDelayToBorn"] or spec["_randomDelayToBornKey"] or not spec["_alwaysHitTraceTargetInTheEnd"]:
            raise ValueError("天赋弹体延迟或停弹设置无效")
    # The six special enum values skip GROUND (7). All ordinary and S2 keys
    # visited in this map segment must agree on the same static baked mount.
    number = (5,6,8,9,10,11)[mount]
    place,enemy = event["placement"],event["enemy"]
    origin,target = stationary_cast_mounts(tables,LOGOS,dict(action="Idle"),place,enemy,event["view"],
                mount=number,field="specialPoint"+str(mount),caster_face_sign=caster_face_sign,static_mode_clips=("Default","Idle","Attack_1","Attack_2",
                    "Skill_2_Begin","Skill_2_Loop","Skill_2_End","Skill_Down_2_Begin","Skill_Down_2_Loop","Skill_Down_2_End"))
    # The attack created the coroutine inside this tick's scheduler pass. Its
    # first subsequent fixed resume is next frame. The projectile ticks after birth.
    budget = fixed_wait_frames(single(delay/tables.fixed_timestep())) if delay>single(1e-5) else 0
    first = emission+budget+1
    return projectile,origin,target,first,delay


def _talent_flight(tables,card,ev,event,emission,draw):
    projectile,origin,target,first,delay=talent_launch(tables,card,ev,event,emission,draw)
    flight = projectile_reach(tables,ev,dict(frame=first,origin=origin,target_mounts=[target]*60,projectile=projectile,
                                             round_position=event["round_position"]))
    if flight["status"]!="reached":
        raise ValueError("词句论断的抛物线弹体未命中静止目标")
    return dict(frame=emission,kind="talent_projectile",mount=draw["mount"],origin=origin,first_movement_frame=first,
                impact_frame=flight["reach_frame"],birth_delay_seconds=delay)


@dataclass
class LockDefinition:
    evidence: dict
    board: dict
    talent: dict
    projectile: dict
    base_scale: int
    scale_step: int
    wait: int
    period: int
    timestep: float
    duration: float
    focus_poll_q32: int


def lock_definition(tables,card):
    ev = caster_prefab_evidence(tables,LOGOS,card["skill"],skill_level=card["skill_level"],module_id=card["module"],
                                module_level=card["module_level"])
    mode, = [c["fields"] for c in ev["skill"]["components"] if c["script"]=="ActiveBuffAbility"]
    switch, = mode["_buffs"]
    mode_index, = [b["value"] for b in switch["blackboard"] if b["key"]=="mode"]
    attack, = [n["fields"] for n in ev["character"]["components"] if n["script"]=="MeleeAttack" and n["path"]=="Modes/S2/Body/Attack"]
    if (switch["templateKey"]!="switch_mode_restart_fsm" or mode_index!=2 or attack["_animKey"] or
            attack["_waitForAttackEvent"] or attack["_timeMode"]!=3 or attack["_cooldownKey"]!="cooldown" or
            attack["_escapeTime"]!=0 or
            attack["_activeBuffs"][0]["templateKey"]!="logos_s_2"):
        raise ValueError("锁定模式、前摇或技能附着模板已变化")
    graph = tables.read("battle/buff_template_data.json")
    scale_node, = graph["logos_s_2"]["eventToActions"]["ON_BUFF_START"]
    if not scale_node["_overwriteAtkScale"] or scale_node["_buffKey"]!="logos_s_2[lock]":
        raise ValueError("锁定层数未直接替换本次倍率")
    focus, = [buff for c in ev["character"]["components"]
              if c["path"]=="Modes/S2/Body/GeneralAbilities/Ability0" and c["script"]=="PassiveBuffAbility"
              for buff in c["fields"]["_buffs"] if buff["buffKey"]=="logos_s_2[focus]"]
    targets, = [c["fields"] for c in ev["character"]["components"]
                if c["path"]=="Modes/S2/Body/Attack" and c["script"]=="BuffToCastTargets"]
    selector, = [c["fields"] for c in ev["character"]["components"]
                 if c["path"]=="Modes/S2/Body/Attack/Selector" and c["script"]=="SecondaryFilterAdvancedSelector"]
    focus_events = graph[focus["templateKey"]]["eventToActions"]
    disable, = focus_events["ON_BUFF_DISABLE"]
    check, finish = focus_events["ON_BUFF_TRIGGER"]
    attach, = [n for n in graph["logos_s_2[lock]"]["eventToActions"]["ON_BUFF_ENABLE"]
               if "+AttachAsDerivedBuffById," in n["$type"]]
    if (not focus["isStunnable"] or not focus["isFreezable"] or not focus["waitFirstTriggerInterval"]
            or not targets["_alsoClearBuffWhenCastInterrupted"] or not targets["_clearBuffWhenChangeTarget"]
            or selector["_ignoreHitRange"] or selector["_secondaryFilter"]!=3
            or selector["_withoutThisBuff"] or selector["_buffKey"]!=scale_node["_buffKey"]
            or attach["_sourceType"]!="BUFF_SOURCE" or attach["_buffKey"]!=focus["buffKey"]
            or not attach["_finishDerivedBuffIfParentFinish"]
            or "+CheckUnitInAttackState," not in check["$type"] or not check["_isUnset"]
            or check["_ownerType"]!="BUFF_OWNER"
            or any("+FinishDerivedBuff," not in n["$type"] or not n["_updateOverrideMap"]
                   for n in (disable,finish))):
        raise ValueError("提喻的受控清锁动作已变化")
    extra_talent = _board(card,0,"11_root") or _board(card,0,"1")
    projectile = ev["projectiles"]["projectile_chr_logos_t"]["base"]
    template, = [n["fields"] for n in projectile["components"] if n["script"]=="SimpleProjectile"]
    curve, = [n["fields"] for n in projectile["components"] if n["script"]=="ParacurveMovement"]
    if curve["_useRandomHeight"] or curve["_randomInverseParacurve"]:
        raise ValueError("天赋弹的抛物线增加了随机抽取")
    bb = {r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    dt = tables.fixed_timestep()
    interval = bb["attack@cooldown"]
    duration = card["skill_row"]["duration"]
    wait = fixed_wait_frames(single(single(min(interval,attack["_preDelay"]))/dt))
    period = fixed_wait_frames(single(single(interval)/dt))
    return LockDefinition(ev,bb,extra_talent,template,_raw_f32(bb["attack@"+scale_node["_defaultValue"]]),
                          _raw_f32(bb["attack@"+scale_node["_factorValue"]]),
                          wait,period,dt,duration,_raw_f32(focus["triggerInterval"]))


def run_logos_lock(tables,card,event,*,stream=None):
    """Move and resolve each talent bullet when its two actual random samples are supplied."""
    if card["operator_id"]!=LOGOS or card["skill"]!=2:
        raise ValueError("需要逻各斯二技能")
    start,end = event["frame"],event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<end:
        raise ValueError("部署与观察帧无效")
    enemy,place = event["enemy"],event["placement"]
    if (enemy["radius"]<=0 or not circle_in_range(enemy["row"],enemy["col"],enemy["radius"],
                range_cells(card["skill_range"] or card["base_range"],place,event["rows"],event["cols"]),box_query=True)):
        raise ValueError("敌人需要处于技能索敌范围")
    rolls = event.get("talent_procs",[])
    if not isinstance(rolls,list) or any(type(x) not in (bool,dict) for x in rolls):
        raise ValueError("第一天赋随机判定需要按次列出，触发时可给出具体挂点和出生延迟")
    if "seed" in event:
        if stream is not None:
            raise ValueError("不能同时指定事件种子与共享随机流")
        from .battle_random import BattleRandom
        stream = BattleRandom(event["seed"])
    if stream is not None and "talent_procs" in event:
        raise ValueError("随机种子与显式天赋随机结果只能选一个")
    definition=lock_definition(tables,card)
    ev,bb=definition.evidence,definition.board
    extra_talent,template=definition.talent,definition.projectile
    emit_count=int(extra_talent.get("emit_count",1))
    wait,period,dt,duration=definition.wait,definition.period,definition.timestep,definition.duration
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=start,end_frame=end,direction=place["direction"],
                                   facing=event.get("view","Front"))
    events = [dict(kind="deploy",frame=start,sp=opening.initial_sp)]
    cast = opening.cast
    if cast is None:
        return dict(status="before_ready",through_frame=end,events=events,sp=opening.sp)
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("二技能未正常进入重启状态机的模式")
    mode_start = cast["spell_frame"]
    first = mode_start+1
    # The attack starts during OBJECTS. Its first deterministic coroutine tick
    # precedes the end of this frame, including the first of its fixed waits.
    next_hit = first+wait-1
    mode_finish = mode_start+fixed_wait_frames(single(single(duration)/dt))
    events.extend(e for e in cast["events"] if e["frame"]<=end)
    state = EnemyElements.from_scene_enemy(tables,enemy)
    atk = _raw_f32(card["panel_attributes"]["atk"])
    resistance = enemy["arts_resistance_q32"]
    count,stacks,stop = 0,0,"horizon"
    inflight = {}
    frame = min(end,mode_start)
    for frame in range(mode_start,min(end,mode_finish)+1):
        if frame==mode_finish:
            stop = "mode_end_boundary"
            break
        state.tick(frame)
        for bullet in inflight.pop(frame,[]):
            if state.hp<=HP_EPSILON:
                break
            dealt=lock_arts(state,frame,card,mul(atk,_raw_f32(extra_talent["atk_scale"])),resistance,"talent_impact")
            follow = None
            if state.burst=="DARK" and state.hp>HP_EPSILON and "element_atk_scale" in extra_talent:
                follow = state.apply_elemental_hp(frame,mul(atk,_raw_f32(extra_talent["element_atk_scale"])),
                                                  source="logos_delta_talent",source_card=card)
            events.append(dict(kind="talent_impact",frame=frame,emission_frame=bullet["frame"],
                               arts_damage_q32=dealt,elemental_impact=follow,hp_after_q32=state.hp))
        if state.hp<=HP_EPSILON:
            stop = "enemy_defeated"
            break
        if frame!=next_hit:
            continue
        scale = definition.base_scale+stacks*definition.scale_step
        dealt=lock_arts(state,frame,card,mul(atk,scale),resistance,"lock_hit")
        stacks = min(stacks+1,int(bb["attack@max_stack_cnt"]))
        events.append(dict(kind="lock_hit",frame=frame,start_frame=frame-wait+1,attack_scale_q32=scale,
                           capped_stacks_after=stacks,damage_q32=dealt,hp_after_q32=state.hp))
        if state.hp<=HP_EPSILON:
            stop = "enemy_defeated"
            break
        missing = False
        if stream is not None:
            draw_start = stream.draws
            draw = stream.dice(extra_talent["prob"])
            if draw:
                # RandomSelector calls Range(0,1) for its single target. The
                # two-argument LegacyRandom.Next returns 0 without a sample.
                # MountPointGroup.Range(0,6) and the birth delay each draw once.
                draw = [dict(mount=stream.range_int(0,6),
                             birth_delay_seconds=single(stream.value()*template["_randomDelayToBorn"])) for _ in range(emit_count)]
            events.append(dict(kind="first_talent_dice",frame=frame,draws=[draw_start,stream.draws],
                               triggered=bool(draw),probability=extra_talent["prob"]))
        else:
            missing = count>=len(rolls)
            draw = None if missing else rolls[count]
            if isinstance(draw,dict):
                draw = draw.get("projectiles",[draw])
                if not isinstance(draw,list) or len(draw)!=emit_count or any(not isinstance(p,dict) for p in draw):
                    raise ValueError("需要给出本次天赋触发的每枚弹各自的挂点及出生延迟")
        if missing or draw is True:
            stop = "talent_draw_needed" if missing else "talent_projectile_pending"
            events.append(dict(kind=stop,frame=frame,probability=extra_talent["prob"]))
            break
        for outcome in draw or ():
            bullet = _talent_flight(tables,card,ev,event,frame,outcome)
            inflight.setdefault(bullet["impact_frame"],[]).append(bullet)
            events.append(bullet)
        count += 1
        next_hit += period
    status="logos_s2_delta_lock_scene" if (card["module_info"] or {}).get("typeIcon")=="ccr-d" else "logos_s2_lock_scene"
    return dict(status=status,stop=stop,through_frame=frame,cast=cast,events=events,
                element_events=state.events,receiver=state.snapshot(),first_attack_start=first,
                pending_projectiles=[bullet for batch in inflight.values() for bullet in batch],
                fixed_predelay_resumes=wait,lock_period_frames=period,
                conditions=["single_stationary_enemy_enters_after_caster_update_before_ready_input",
                            "no_other_damage_or_interrupts","isolated_deterministic_random_stream" if stream is not None
                            else "supplied_first_talent_die_results"])

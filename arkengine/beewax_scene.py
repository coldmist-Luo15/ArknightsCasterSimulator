"""Beeswax S1 deployment, area attacks, expiry and recharge against stationary ordinary receivers."""
from fractions import Fraction

from .animation_assets import read_skin_actions
from .attack_range import circle_in_range
from .caster_ranges import skill_range_cells
from .caster_runtime import default_attack
from .damage import HP_EPSILON
from .fixed import ONE
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .phalanx_skills import BEE, _controller, execute_phalanx_event
from .skill_recovery import NaturalSkillPoints, RecoveryTimer, READY_EPSILON
from .volley_damage import _raw_f32


def _timing(tables, card):
    cache = ("beewax_s1_scene_timing",)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    ability = _controller(tables, card)
    mode = default_attack(tables,BEE,1)
    root = tables.source["caster_prefabs"][BEE]
    actor = bundle_environment(tables.verified_bundle(root["member"])[0]).container[root["container"]].deref()
    objs = actor.assets_file.objects
    attack = objs[mode["attack_component"]].read_typetree()
    selector = objs[attack["_selector"]["m_PathID"]].read_typetree()
    raw, src = tables.verified_bundle("battle/prefabs/[uc]skills.ab")
    go = bundle_environment(raw).container["dyn/battle/prefabs/[uc]skills/skchr_beewax_1.prefab"].deref()
    components = [go.assets_file.objects[r["component"]["m_PathID"]].read_typetree() for r in go.read_typetree()["m_Component"]]
    buff_ability, = (f for f in components if "_buffs" in f)
    buff, = buff_ability["_buffs"]
    if (buff["lifeTimeType"] != 1 or buff["lifeTime"] or buff["durationKey"]
            or buff_ability["_resetCdStrategy"] != 1 or buff_ability["_updateLifetimeWhenSpellStart"]
            or mode["ability"]!="MeleeAttack" or mode["trigger"]!="SelectorTrigger" or not mode["enabled"]
            or not mode["base_range"] or attack["_selectTargetTiming"]!=1 or attack["_selectTargetSource"]!=1
            or attack["_alwaysIncludeTarget"] or attack["_timeMode"] or not attack["_waitForAttackEvent"]
            or not selector["m_Enabled"] or selector["_limitTargetNum"] or selector["_targetCategory"]!=1
            or selector["_targetMotion"]!=3 or selector["_targetSide"]!=2 or selector["_ignoreHitRange"]
            or buff["templateKey"]!="switch_mode_restart_fsm" or buff["blackboard"][0]["value"]!=1
            or buff_ability["_waitForCasting"] or buff_ability["_preDelay"] or not ability["play_begin"]):
        raise ValueError("蜜蜡一技能模式切换或群攻索敌配置发生变化")
    skin = read_skin_actions(tables,"chararts/"+BEE+".ab","defaultskin",("Born",ability["begin_action"],attack["_animKey"]))
    durations = []
    baked = tables.read(f"bakemuzzledata/character/{BEE}_defaultskin.json")["bakedAnimDatas"]
    for view in ("front","back"):
        animations = skin["views"][view]["actions"]
        begin = animations[ability["begin_action"]]
        cycle = animations[attack["_animKey"]]
        track = baked[view.capitalize()]["bakedEvents"][cycle["clip"]]
        if (begin["loop"] or begin["speed"]!=1 or cycle["loop"] or cycle["speed"]!=1 or
                track["events"]!=["OnAttack"] or len(track["eventTime"])!=1):
            raise ValueError("蜜蜡默认外观动作含未解析的速率或关键帧")
        durations.append((animations["Born"]["duration"],begin["duration"],cycle["duration"],track["eventTime"][0]))
    if durations[0]!=durations[1] or attack["_maxAnimScale"]!=1:
        raise ValueError("蜜蜡不同朝向或播放速度需要单独核对")
    born, begin, cycle, key = durations[0]
    dt = tables.fixed_timestep()
    interval = card["panel_attributes"]["baseAttackTime"] * 100 / card["panel_attributes"]["attackSpeed"]
    if interval < cycle:
        raise ValueError("攻击间隔发生变化，需要重新计算动作变速")
    result=dict(birth_ticks=fixed_wait_frames(Fraction(born)/Fraction(dt)),
                begin_ticks=fixed_wait_frames(Fraction(begin)/Fraction(dt)),
                interval_ticks=fixed_wait_frames(Fraction(interval)/Fraction(dt)),
                ticks_to_key=baked_key_sample(key,1,dt)[0], attack_event_seconds=key,
                actor_sha256=root["sha256"], skill_sha256=src["sha256"],skin_sha256=skin["source"]["sha256"])
    tables._prefab_data[cache]=result
    return result


def _run(tables, card, event, *, first_impact):
    """Deterministic INPUT-phase press at first readiness; no incoming hits or external modifiers."""
    if card["operator_id"]!=BEE or card["skill"]!=1:
        raise ValueError("只适用于蜜蜡一技能")
    deploy, end = event["frame"], event["end_frame"]
    if any(type(n) is not int for n in (deploy,end)) or not 0<=deploy<=end:
        raise ValueError("需要非负部署帧与其后的终止帧")
    geometry=skill_range_cells(tables,card,event)
    timing=_timing(tables,card)
    step=_raw_f32(tables.fixed_timestep())
    data=card["skill_row"]["spData"]
    cost=data["spCost"]
    sp=NaturalSkillPoints(data["initSp"],cost,cost,RecoveryTimer(ONE))
    log=[dict(frame=deploy,kind="deploy",sp=sp.points)]
    report=dict(scope="stationary_unmodified_receivers_single_manual_skill_cycle",
                input_policy="press_in_input_phase_at_first_ready_tick_after_birth",
                events=log, timing=timing, skill_id=card["skill_id"], module=card["module"], module_level=card["module_level"])
    for frame in range(deploy+1,end+1):
        sp.advance(step)
        if sp.points>=cost and frame>=deploy+timing["birth_ticks"]+1:
            log.append(dict(frame=frame,kind="skill_button",sp=sp.points))
            break
    else:
        return dict(**report,stop="horizon_before_skill_button")
    # CastSkill plays begin before the buff. The input tick is its first
    # coroutine resume, then the non-casting ActiveBuffAbility awaits a fixed tick.
    cast=frame+timing["begin_ticks"]-1
    mode=cast+1
    first_search=mode+1
    impact=first_search+timing["ticks_to_key"]
    for frame,kind in ((cast,"skill_cast_spent_and_recovery_blocked"),(mode,"mode_and_attack_buff")):
        if frame<=end:
            log.append(dict(frame=frame,kind=kind,sp=0))
    if first_search>end:
        return dict(**report,stop="horizon_before_first_search")
    targets=event["targets"]
    selected=[]
    for key,target in targets.items():
        if type(target["eligible"]) is not bool or target["radius"]<=0 or target["hp_q32"]<=HP_EPSILON:
            raise ValueError("本场景需要可验证的存活固定目标")
        if target["eligible"] and circle_in_range(target["row"],target["col"],target["radius"],geometry["cells"],box_query=True):
            selected.append(key)
    log.append(dict(frame=first_search,kind="attack_search",targets=selected))
    if not selected and first_impact:
        return dict(**report,stop="no_stationary_target_in_skill_range")
    duration=card["skill_row"]["duration"]
    natural_end=mode+(_raw_f32(duration)-READY_EPSILON)//step+1
    current={key: dict(target) for key,target in targets.items()}
    search=first_search
    while selected and search<natural_end and search<=end:
        impact=search+timing["ticks_to_key"]
        if search!=first_search:
            log.append(dict(frame=search,kind="attack_search",targets=selected.copy()))
        if impact-1<=end and impact<natural_end:
            log.append(dict(frame=impact-1,kind="baked_OnAttack"))
        if impact>end or impact>=natural_end:
            break
        # Resolve the living selector set once per packet, then commit HP before the next cast.
        living={k:v for k,v in current.items() if v["hp_q32"]>HP_EPSILON}
        result=execute_phalanx_event(tables,card,{**event,"kind":"phalanx_impact","frame":impact,"targets":living})
        hits=result["hits"]
        for key, hit in hits.items():
            current[key]["hp_q32"]=hit["hp_after_q32"]
        log.append(dict(frame=impact,kind="area_arts_damage",hits=hits))
        if first_impact:
            return dict(**report,stop="first_impact",hits=hits,impact_frame=impact)
        selected=[k for k in selected if current[k]["hp_q32"]>HP_EPSILON]
        search+=timing["interval_ticks"]
    hp={key:target["hp_q32"] for key,target in current.items()}
    if first_impact:
        return dict(**report,stop="horizon_before_impact")
    if natural_end>end:
        return dict(**report,stop="horizon_during_skill",hp_after_q32=hp)
    log.append(dict(frame=natural_end,kind="skill_ended_default_disarm_restored",sp=0))
    sp.points=0
    for frame in range(natural_end+1,end+1):
        sp.advance(step)
        if sp.points==cost:
            log.append(dict(frame=frame,kind="skill_ready_again",sp=sp.points))
            return dict(**report,stop="ready_again",ready_again_frame=frame,natural_end_frame=natural_end,hp_after_q32=hp)
    return dict(**report,stop="horizon_during_recharge",natural_end_frame=natural_end,sp=sp.points,hp_after_q32=hp)


def run_beewax_first_impact(tables, card, event):
    return _run(tables,card,event,first_impact=True)


def run_beewax_cycle(tables, card, event):
    return _run(tables,card,event,first_impact=False)

"""Mostima S2 pulses on the portion of each route with provable slow/stun contact."""
from .area_caster_skills import MOSTIMA, execute_area_skill
from .mostima_slow import run_slow_channel
from .stage_channel import prepare_channel,require_ordinary_highland


def _channel_cut(evidence, events, cast_frame, finish, horizon):
    """Owner states are sampled at SKILL.OnTick; an explicit interrupt has a supplied delivery phase."""
    if not events:
        return None
    roots = {c["script"]:c["fields"] for c in evidence["skill"]["components"] if not c["path"] and c["enabled"] and c["active"]}
    skill = roots["CastSkill"]
    if (not skill["_switchToState"] or skill["_shouldCastLikeAttack"] or skill["_uninterruptibleOnAbilityPredelay"]
            or skill["_stopAffectOnInterrupt"] or roots["ChannelingMeleeAttack"]["_ignorePalsyInterrupt"]):
        raise ValueError("荒时之锁的技能状态与中断分派已变化")
    # Within OBJECTS the owner reads effective control flags. Pulses run later in COROUTINES.
    phases = {"owner_tick":0,"before_pulse":1,"after_pulse":2}
    disabled = {"stunned","frozen","levitated","palsying","doze"}
    previous = (-1,-1)
    first_cut = None
    for event in events:
        kind, frame, phase = event["kind"],event["frame"],event["phase"]
        if type(frame) is not int or not cast_frame < frame <= horizon or phase not in phases:
            raise ValueError("施法者状态事件需要在技能提交之后，注明实际观察或分派相位")
        if (frame,phases[phase]) < previous:
            raise ValueError("施法者状态事件须按实际发生顺序给出")
        previous = frame,phases[phase]
        if not ((kind in disabled and phase=="owner_tick") or
                (kind in ("interrupt","disarm_on","disarm_off") and phase in ("before_pulse","after_pulse"))):
            raise ValueError("不支持的施法者状态事件或分派相位")
        callbacks = event.get("additional_callbacks","none")
        if callbacks not in ("none","unresolved") or (callbacks=="unresolved" and kind not in ("disarm_on","disarm_off")):
            raise ValueError("缴械的额外回调只可标为无或未解析，已知中断请按实际顺序分派")
        if first_cut is not None or frame>=finish:
            continue
        if kind in disabled or kind=="interrupt":
            first_cut=dict(kind="channel_interrupted",cause=kind,frame=frame,phase=phase,through=frame)
            last_blocked=event.get("end_frame",frame if kind=="interrupt" else None)
            if last_blocked is not None:
                if type(last_blocked) is not int or last_blocked<frame:
                    raise ValueError("控制结束帧必须是对应控制开始之后的最后阻断帧")
                first_cut["ordinary_resume"]=last_blocked+1
        elif callbacks=="unresolved":
            first_cut=dict(kind="additional_disarm_callback_unresolved",frame=frame,
                           phase=phase,through=frame-int(phase!="after_pulse"))
        # A bare DISARMED flag gates Attack/Combat. SKILL.OnTick and the character's
        # CanDoAbilitySpellOn both allow this channel to execute its next spell.
    return first_cut


def run_mostima_channel(tables, request):
    stage_input = dict(request)
    target_free_spans=stage_input.pop("enemy_target_free",())
    owner_events = stage_input.pop("owner_channel_events",[])
    battle=prepare_channel(tables,stage_input,operator=MOSTIMA,skill=2,contact_checked_during_replay=True)
    require_ordinary_highland(battle.scene,[battle.deploy])
    card,geometry,opening=battle.card,battle.geometry,battle.opening
    range_cells={(cell["row"],cell["col"]) for cell in opening["attack_range"]["cells"]}
    if set(geometry["cells"])!=range_cells:
        raise ValueError("技能范围与关卡的攻击范围接触记录不同")
    points=battle.sp_at_button()
    if points<card["skill_row"]["spData"]["spCost"]:
        return dict(status="rejected",reason="sp_not_ready",frame=battle.button["frame"],sp_before=points,
                    sp_cost=card["skill_row"]["spData"]["spCost"])
    cast,timeline=battle.cast()
    finish=cast["cast_frame"]+timeline["end_frame"]
    end=request["end_frame"]
    pulses=[cast["cast_frame"]+f for f in timeline["channel_pulse_frames"] if cast["cast_frame"]+f<=end]
    through=end
    cut = _channel_cut(battle.evidence,owner_events,cast["cast_frame"],finish,end)
    if cut:
        if "ordinary_resume" not in cut:
            through=min(through,cut["through"])
        pulses=[f for f in pulses if f<cut["frame"] or (f==cut["frame"] and cut["phase"]=="after_pulse")]
    squad={k:battle.config[k] for k in ("operator_id","skill","skill_level","module","module_level","level","elite","potential")}
    result=dict(stage=opening["code"],squad=squad,deployment=battle.placement_event,
                range_id=geometry["range_id"],cast=cast,
                conditions=["specified_stage_fragment_and_birth_clock","unblocked_route_no_external_buffs"],source=opening["source"])
    aura=execute_area_skill(tables,card,dict(kind="slow_aura",frame=battle.deploy["frame"],in_range=False))["result"]
    result=run_slow_channel(battle,result,pulses,through,finish,aura["move_speed_ratio_delta"],
                           target_free_spans=target_free_spans,ordinary_resume=cut.get("ordinary_resume") if cut else None)
    if cut and result["through_frame"]==through and result["status"]!="unresolved":
        if cut["kind"]=="channel_interrupted":
            result.update(status="channel_interrupted",interrupt=cut)
            if end>through:
                result["next_unresolved"]="post_interrupt_ordinary_attacks"
        else:
            result.update(status="unresolved",reason=cut["kind"],unresolved_frame=cut["frame"])
    return result

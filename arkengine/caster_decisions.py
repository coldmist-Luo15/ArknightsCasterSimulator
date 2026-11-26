"""Read-only skill choices at a battle dispatch point. The caller owns the battle state."""
from .caster_special_start import SPECIAL_CONTROLLERS, resolve_special_start
from .caster_start import resolve_cast_start


def skill_actions(tables,card,evidence,state):
    frame, moment, owner = state["frame"], state["moment"], state["owner_state"]
    if type(frame) is not int or frame<0 or moment not in ("input","after_tick","owner_skill_tick","before_attack"):
        raise ValueError("需要有效逻辑帧及输入、技能更新或攻击前阶段")
    ctrl, = (x for x in evidence["skill"]["components"] if not x["path"] and x["active"] and x["enabled"]
             and "_allowSpRecoveryWhenAffecting" in x["fields"])
    name,f = ctrl["script"],ctrl["fields"]
    trigger = card["skill_row"]["skillType"]
    result=dict(operator_id=card["operator_id"],skill_id=card["skill_id"],frame=frame,moment=moment,
                configured_trigger=trigger,player_actions=[],automatic_events=[],needs=[])
    def stop(reason):
        return dict(result,reason=reason)
    def missing(*keys):
        result["needs"]=[k for k in keys if state.get(k) is None]
        return bool(result["needs"])

    if owner in ("undeployed","dead","retreated"):
        return stop("owner_unavailable")
    if owner not in ("idle","attack","skill","born"):
        raise ValueError("需要实际干员状态")
    manual = moment in ("input","after_tick")
    if missing("triggerable"):
        return stop("needs_battle_state")
    if type(state["triggerable"]) is not bool:
        raise ValueError("triggerable 需要当前状态的布尔判定")
    if not state["triggerable"]:
        return stop("rejected_at_trigger_gate")
    if state.get("chant_stage") is not None:
        chant, = (x for x in evidence["skill"]["components"] if x["script"]=="ChantBeforeSkill" and x["enabled"] and x["active"])
        if manual and state["chant_stage"]=="extra" and chant["fields"]["_suspendableWhenExtraChant"]:
            result["player_actions"].append("finish_extra_chant")
        return stop("in_chant")
    if owner=="skill" or state.get("affecting"):
        if manual and name in ("CastSkillWithSuspendable","CastSkillWithLimitTimes"):
            if missing("discardable"):
                return stop("needs_battle_state")
            if type(state["discardable"]) is not bool:
                raise ValueError("discardable 需要当前状态的布尔判定")
            if state["discardable"] and (name=="CastSkillWithSuspendable" or f["_canDiscardRemainingCount"]):
                result["player_actions"].append("discard_active_skill" if name=="CastSkillWithSuspendable" else "discard_remaining_uses")
        return stop("skill_in_progress")

    replacing = name in ("ReplacementSkill","ReplacementSkillFixed")
    if manual != (trigger=="MANUAL") or (moment=="before_attack" and not replacing):
        return stop("waiting_for_configured_dispatch")
    if missing("sp"):
        return stop("needs_battle_state")
    phase = moment if manual else "before_coroutines"
    opts=dict(frame=frame,phase=phase,sp=state["sp"])
    for k in ("facing","direction","state_switch_succeeds","begin_eligible"):
        if k in state:
            opts[k]=state[k]
    if name in SPECIAL_CONTROLLERS:
        if owner=="born" and not replacing:
            if missing("state_switch_succeeds") and f.get("_switchToState"):
                return stop("needs_birth_state_switch_gate")
            result["needs"]=[]
        if replacing:
            if missing("current_replacement"):
                return stop("needs_battle_state")
            opts.update(source="before_attack" if moment=="before_attack" else "auto_tick",
                        current_replacement=state["current_replacement"],trigger_search_passed=state.get("trigger_search_passed"))
        elif name.startswith("ToggleSkill"):
            if missing("toggled"):
                return stop("needs_battle_state")
            opts["toggled"]=state["toggled"]
        else:
            if missing("attached","previous_uses_this_deployment"):
                return stop("needs_battle_state")
            opts.update(attached=state["attached"],previous_uses_this_deployment=state["previous_uses_this_deployment"])
        trial = resolve_special_start(tables,evidence,**opts)
    else:
        is_chant = any(c["script"]=="ChantBeforeSkill" and c["active"] and c["enabled"] for c in evidence["skill"]["components"])
        if not is_chant and type(state["sp"]) is int and 0<=state["sp"]<card["skill_row"]["spData"]["spCost"]:
            return stop("sp_not_ready")
        if f["_trigger"]["m_PathID"] and (not manual or f["_useTriggerInManualMode"]):
            if missing("trigger_search_passed"):
                return stop("needs_selector_result")
            if type(state["trigger_search_passed"]) is not bool:
                raise ValueError("需要当前选择器的布尔结果")
            if not state["trigger_search_passed"]:
                return stop("trigger_found_no_target")
        trial=resolve_cast_start(tables,evidence,owner_state=owner,trigger=trigger,**opts)
    if trial["status"]=="requires_trigger_search_result":
        result["needs"]=["trigger_search_passed"]
    if trial["cast_frame"] is not None:
        result["player_actions" if manual else "automatic_events"].append(
            "activate_skill" if manual else "register_attack_replacement" if replacing else "activate_skill")
    elif trial["status"]=="replacement_already_registered" and moment=="before_attack":
        result["automatic_events"].append("consume_registered_replacement")
    return stop(trial["status"])

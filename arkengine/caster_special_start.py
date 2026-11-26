"""Run a single delivered V079 replacement, toggle or attachment trigger on a supplied battle state."""
from .baked_mount import single
from .caster_start import _begin_clip
from .fixed import ONE
from .mystic_timing import fixed_wait_frames

SPECIAL_CONTROLLERS = {"ReplacementSkill", "ReplacementSkillFixed", "ToggleSkill",
                       "ToggleSkillWithEndAnimation", "AttachOrDetachSkill"}


def resolve_special_start(tables, evidence, *, frame=0, phase="after_tick", facing="Front", direction="RIGHT",
                          source="manual", sp, toggled=None, previous_uses_this_deployment=None,
                          trigger_search_passed=None, current_replacement=False, attached=False,
                          triggerable=True, state_switch_succeeds=True, begin_eligible=True,
                          owner_tick_pending=None):
    """sp is the gauge at dispatch, constant until deduction in this isolated trace.

    A before_attack input is the ordinary attack callback with a valid ordinary input target.
    auto_tick is NextAttackOrCombatSkill.DoTick on a ready ability. A supplied trigger result
    belongs to the appropriate Search(true/false) attempt in this precise callback.
    """
    if (type(frame) is not int or frame < 0 or type(sp) is not int or sp < 0
            or phase not in ("input","before_coroutines","after_tick") or facing not in ("Front","Back")
            or direction not in ("UP","DOWN","LEFT","RIGHT") or source not in ("manual","before_attack","auto_tick")
            or any(type(x) is not bool for x in (current_replacement,attached,triggerable,state_switch_succeeds,begin_eligible))
            or any(x is not None and type(x) is not bool for x in (toggled,trigger_search_passed,owner_tick_pending))):
        raise ValueError("特殊技能需要有效的帧、阶段、触发源、技力与布尔状态")
    roots = [c for c in evidence["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    controller, = (c for c in roots if "_allowSpRecoveryWhenAffecting" in c["fields"])
    cls, f, sid = controller["script"], controller["fields"], evidence["skill_id"]
    row = tables.read("excel/skill_table.json")[sid]["levels"][evidence["rank"]-1]
    events = []
    result = dict(controller=cls, skill_id=sid, events=events, cast_frame=None, spell_frame=None,
                  sp_after=sp, conditions=["living_owner", "no_interrupt_or_external_sp_during_start"])
    def emit(kind, at, **fields):
        events.append(dict(kind=kind,frame=at,**fields))
    def finish(status):
        return dict(result,status=status)
    emit("trigger_delivery",frame,source=source,phase=phase)
    if cls not in SPECIAL_CONTROLLERS:
        return finish("different_controller")
    ability, = (c for c in roots if "_selector" in c["fields"])
    result["ability"] = ability["script"]
    cost = row["spData"]["spCost"]
    if sp > cost * row["spData"]["maxChargeTime"]:
        raise ValueError("技力高于所选技能的储存上限")
    replacing = cls in ("ReplacementSkill","ReplacementSkillFixed")
    if not triggerable:
        return finish("rejected_at_trigger_gate")
    if (source == "manual") == replacing:
        return finish("wrong_trigger_for_controller")
    if replacing:
        mode = 1 + int(bool(f["_allowReplacementOnTick"])) if cls == "ReplacementSkillFixed" else 0
        result["replacement_tick_mode"] = mode
        if current_replacement:
            if source == "before_attack":
                emit("already_registered_attack_selected",frame,ability=ability["script"])
            return finish("replacement_already_registered")
        if mode == 2:
            return finish("requires_preregistration_state")
        ref = f["_trigger"]
        trigger = next((c for c in evidence["skill"]["components"] if c["id"] == ref["m_PathID"]),None)
        if source == "auto_tick" and (mode == 1 or trigger is None):
            return finish("no_tick_trigger")
        if sp < cost:
            return finish("insufficient_sp")
        if trigger is not None:
            if trigger_search_passed is None:
                return finish("requires_trigger_search_result")
            emit("trigger_search",frame,trigger=trigger["script"],force=source=="before_attack",success=trigger_search_passed)
            if not trigger_search_passed:
                return finish("trigger_found_no_target")
        emit("reduce_sp",frame,cost=cost)
        emit("register_attack_replacement",frame,ability=ability["script"])
        result.update(sp_after=sp-cost, cast_frame=frame)
        return finish("replacement_registered")

    if cls == "AttachOrDetachSkill" and attached:
        return finish("ability_already_attached")
    if sp < cost:
        return finish("insufficient_sp")
    cast = frame
    if f["_switchToState"]:
        if not state_switch_succeeds:
            return finish("state_switch_rejected")
        emit("enter_skill_state", frame)
    if cls in ("ToggleSkill","ToggleSkillWithEndAnimation"):
        if toggled is None:
            raise ValueError("姿态切换需要当前开关状态 toggled")
        if f["_playSkillBeginAnim"] and begin_eligible:
            if f["_beginAnimValidator"]["m_PathID"] or not f["_switchToState"]:
                return finish("unresolved_begin_animation_gate")
            action = f["_endAnim"] if toggled else (f["_beginAnimDown"] if direction=="DOWN" and f["_beginAnimDown"] else f["_beginAnim"])
            clip, source_info = _begin_clip(tables,evidence,action,facing)
            if not clip or clip.get("status") or clip["loop"] or clip["speed"] != 1:
                return finish("unresolved_begin_animation")
            wait = fixed_wait_frames(single(single(clip["duration"])/single(tables.fixed_timestep())))
            cast += wait - int(phase!="after_tick")
            emit("begin_animation",frame,action=action,clip=clip["clip"],duration=clip["duration"],fixed_resumes=wait)
            result["animation_source"] = source_info
        emit("reduce_sp",cast,cost=cost)
        components = evidence["character"]["components"]
        character, = (c for c in components if "_modes" in c["fields"])
        default, = (c for c in components if c["path"]=="Modes/Default" and c["script"]=="UnitMode")
        default_index = [m["m_PathID"] for m in character["fields"]["_modes"]].index(default["id"])
        on_mode = f["_downMode"] if f["_switchToModeDown"] and direction=="DOWN" else f["_switchToMode"]
        emit("switch_mode",cast,mode=default_index if toggled else on_mode)
        emit("set_toggled",cast,value=not toggled)
        emit("detach_ability" if toggled else "attach_ability",cast,ability=ability["script"])
        emit("owner_toggle_skill_start",cast)
        result.update(toggled_after=not toggled, sp_after=sp-cost, cast_frame=cast, spell_frame=cast)
        return finish("toggle_applied")

    if sid != "skchr_amgoat_1" or ability["script"] != "PassiveOneOfBuffAbility" or ability["fields"]["_selectMethod"] != 2:
        return finish("unresolved_attachment_ability")
    if type(previous_uses_this_deployment) is not int or previous_uses_this_deployment < 0:
        raise ValueError("艾雅法拉需要这次部署中此前使用二重咏唱的次数")
    buffs = ability["fields"]["_buffs"]
    selected = buffs[min(previous_uses_this_deployment,len(buffs)-1)]["buffKey"]
    emit("reduce_sp",cast,cost=cost)
    emit("attach_ability",cast,ability=ability["script"],buff=selected)
    emit("initialize_duration",cast,seconds=row["duration"])
    emit("skill_start",cast)
    result.update(sp_after=sp-cost,cast_frame=cast,spell_frame=cast,attached_buff=selected,
                  blocked_while_attached=not bool(f["_allowSpRecoveryWhenAffecting"]))
    # The deterministic phase order is OBJECTS, INPUT, COROUTINES. Both a
    # delivered INPUT and a post-tick operation miss this frame's owner tick.
    pending = owner_tick_pending if phase == "before_coroutines" else False
    if pending is not None:
        delta = int(tables.fixed_timestep()*ONE)
        duration = int(row["duration"]*ONE)
        ticks = (duration+delta-1)//delta
        end = cast + ticks - int(pending)
        emit("detach_ability",end,ability=ability["script"])
        result["detach_frame"] = end
        result["conditions"].append("one_uninterrupted_owner_tick_per_frame")
    return finish("ability_attached")

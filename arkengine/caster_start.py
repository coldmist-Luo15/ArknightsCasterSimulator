"""Successful V079 regular CastSkill start paths, from operation delivery to ability callbacks."""
from .animation_assets import read_skin_actions
from .baked_mount import single
from .mystic_timing import fixed_wait_frames


CAST_CONTROLLERS = ("CastSkill", "CastSkillWithSuspendable", "CastSkillWithLimitTimes")


def player_operation_delivery(frame, *, deterministic, submit_phase):
    """Submit against a known queue phase. A paused deterministic queue needs a resumed tick."""
    if type(frame) is not int or frame < 0 or type(deterministic) is not bool or submit_phase not in ("before_input","after_tick"):
        raise ValueError("需要非负提交帧和明确的队列模式、收取阶段")
    if deterministic:
        return dict(frame=frame+int(submit_phase=="after_tick"), phase="input", route="queued_input_drain")
    if submit_phase=="before_input":
        raise ValueError("普通编队实时时钟需要相对于 Unity 固定更新的提交相位")
    return dict(frame=frame, phase="after_tick", route="immediate_dispatch")


def _begin_clip(tables, evidence, action, facing):
    uid, skin = evidence["operator_id"], evidence["animations"]["skin"]
    cache = ("cast_start_clip", uid, skin, action)
    if cache not in tables._prefab_data:
        member = ("chararts" if skin == "defaultskin" else "skinpack") + "/" + uid + ".ab"
        tables._prefab_data[cache] = read_skin_actions(tables, member, skin, [action], allow_unbound=True)
    data = tables._prefab_data[cache]
    return data["views"][facing.lower()]["actions"].get(action), data["source"]


def resolve_cast_start(tables, evidence, *, frame=0, phase="after_tick", facing="Front", direction="RIGHT",
                       trigger=None, owner_state="idle", triggerable=True, begin_eligible=True,
                       cast_succeeds=True, discardable=False, state_switch_succeeds=True, sp=None,
                       chant_options=None, effective_attack_interval=None):
    """One delivered trigger; readiness, state/target eligibility and interrupted casts belong to the scene.

    input is the deterministic INPUT dispatch before this tick's COROUTINES; after_tick
    is ordinary squad input after a completed tick, followed by one fixed update per
    advancing frame. before_coroutines is a trigger before the deterministic scheduler drain.
    A pre-cast action duration is resolved from the selected skin, independent of its OnAttack key.
    """
    if (type(frame) is not int or frame < 0 or phase not in ("input","before_coroutines","after_tick")
            or direction not in ("LEFT","RIGHT","UP","DOWN") or facing not in ("Front","Back")
            or owner_state not in ("attack","idle","born","skill")
            or not all(type(v) is bool for v in (triggerable,begin_eligible,cast_succeeds,discardable,state_switch_succeeds))):
        raise ValueError("技能触发请求的帧、相位、朝向或状态无效")
    row = tables.read("excel/skill_table.json")[evidence["skill_id"]]["levels"][evidence["rank"]-1]
    configured = row["skillType"]
    cost = row["spData"]["spCost"]
    if sp is not None and (type(sp) is not int or not 0<=sp<=cost*row["spData"]["maxChargeTime"]):
        raise ValueError("交付时整点技力超出当前技能的基础储存上限")
    trigger = configured if trigger is None else trigger
    if trigger not in ("MANUAL","AUTO","PASSIVE"):
        raise ValueError("技能触发方式无效")
    roots = [c for c in evidence["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    controller, = [c for c in roots if "_allowSpRecoveryWhenAffecting" in c["fields"]]
    cls, f = controller["script"], controller["fields"]
    events = [dict(kind="trigger_delivery",frame=frame,phase=phase)]
    report = dict(skill_id=evidence["skill_id"], controller=cls, configured_trigger=configured, sp_before=sp,sp_after=sp,
                  trigger=trigger, events=events, cast_frame=None, spell_frame=None,
                  assumptions=["living_owner","no_external_timing_changes","conditions_hold_through_commit",
                               "one_scheduler_resume_per_advancing_tick"])
    def end(status):
        return dict(report,status=status)

    if not triggerable:
        return end("rejected_at_trigger_gate")
    if cls not in CAST_CONTROLLERS:
        return end("different_controller")
    if discardable and trigger=="MANUAL":
        if cls == "CastSkillWithSuspendable":
            events.append(dict(kind="discard_active_skill",frame=frame))
            return end("discarded")
        if cls == "CastSkillWithLimitTimes" and f["_canDiscardRemainingCount"]:
            events.append(dict(kind="discard_remaining_uses",frame=frame))
            return end("discarded")
        return end("discard_unavailable")
    if trigger!=configured:
        return end("wrong_trigger_for_new_cast")
    has_chant = any(c["script"]=="ChantBeforeSkill" for c in roots)
    if not has_chant and sp is not None and sp<cost:
        return end("sp_not_ready")
    if owner_state=="born" and not f["_canSwitchDuringBorn" if f["_switchToState"] else "_canCastDuringBorn"]:
        return end("rejected_during_birth")
    if owner_state=="skill" and f["_switchToState"]:
        return end("existing_skill_state_requires_state_or_chant_callback")

    events.append(dict(kind="use_skill",frame=frame,route="switch_to_skill_state" if f["_switchToState"] else "direct_cast"))
    has_begin = False
    cast = frame
    if f["_switchToState"]:
        if not state_switch_succeeds:
            return end("state_switch_rejected")
        events.append(dict(kind="enter_skill_state",frame=frame,old_state=owner_state,
                           old_attack_exit_calls_interrupt_if_not=(owner_state=="attack")))
        if has_chant:
            from .marcil_chant import resolve_chant_start
            extra = dict(chant_options or {})
            if sp is not None:
                if extra.get("mana",sp)!=sp:
                    raise ValueError("吟唱和触发帧的魔力数值不一致")
                extra["mana"] = sp
            chant = resolve_chant_start(tables,evidence,frame=frame,phase=phase,facing=facing,
                        owner_state=owner_state,trigger=trigger,triggerable=triggerable,cast_succeeds=cast_succeeds,**extra)
            if evidence["skill_id"]=="skchr_marcil_3" and chant["cast_frame"] is not None:
                from .marcil_blast import blast_clock
                blast=blast_clock(tables,evidence,chant)
                chant["status"]="chant_cast_and_boom_spell"
                chant["spell_frame"]=blast["spell_frame"]
                chant["events"].extend(blast["events"])
                chant["blast_clock"]=blast
            return {**report, **chant, "sp_after": None if chant["cast_frame"] is not None else sp}
        if f["_playSkillBeginAnim"] and begin_eligible:
            if f["_beginAnimValidator"]["m_PathID"]:
                return end("begin_animation_validator_required")
            action = (f["_beginAnimDown"] if direction=="DOWN" and f["_beginAnimDown"] else f["_beginAnim"])
            if not action:
                return end("begin_action_unresolved")
            clip, src = _begin_clip(tables,evidence,action,facing)
            if not clip or clip.get("status") or clip["speed"]!=1 or clip["loop"] or clip["duration"]<=0:
                return end("begin_animation_binding_or_playback_requires_resolution")
            # The native iterator divides two single-precision values before rounding to nearest-even.
            budget = fixed_wait_frames(single(single(clip["duration"])/single(tables.fixed_timestep())))
            events.append(dict(kind="begin_animation",frame=frame,action=action,clip=clip["clip"],
                               duration_seconds=clip["duration"],fixed_resumes=budget))
            report["begin_animation_source"] = src
            cast += budget - int(phase!="after_tick")
            has_begin = True
    events.append(dict(kind="cast_attempt",frame=cast))
    if not cast_succeeds:
        return end("cast_failed_at_ability_or_target_gate")
    report["cast_frame"] = cast
    events.append(dict(kind="cast_commit",frame=cast))
    events.append(dict(kind="reduce_sp",frame=cast,cost=cost))
    if sp is not None:
        report["sp_after"] = sp-cost
    abilities = [c for c in roots if "_resetCdStrategy" in c["fields"]]
    report["abilities"] = [c["script"] for c in roots if c["type"]=="MonoBehaviour" and c is not controller]
    if len(abilities)==1 and abilities[0]["script"]=="ActiveBuffAbility":
        a = abilities[0]["fields"]
        if not a["_waitForCasting"] or (not a["_waitForAttackEvent"] and not a["_interuptIfTargetDead"] and not a["_preDelay"]):
            spell = cast + int(has_begin or phase=="after_tick")
            report["spell_frame"] = spell
            events.append(dict(kind="active_buff_spell",frame=spell,
                          buffs=[b["buffKey"] for b in a["_buffs"]]))
            return end("cast_and_active_buff_spell")
    from .caster_first_spell import first_ability_spell
    first = first_ability_spell(tables,evidence,roots,cast=cast,phase=phase,facing=facing,direction=direction,
                                effective_attack_interval=effective_attack_interval)
    if first is not None:
        report["spell_frame"] = first["spell_frame"]
        report["ability_clock"] = first["clock"]
        events.extend(first["events"])
        return end("cast_and_first_ability_spell")
    return end("cast_reached_ability_specific_spell")

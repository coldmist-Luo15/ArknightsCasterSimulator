"""Relative V079 cast timing after the selected ability has received a successful commit."""
from fractions import Fraction
from math import isfinite

from .baked_mount import single
from .caster_first_spell import _ordinary_interval, _selected_clip, first_ability_spell
from .caster_prefabs import sample_clip_events
from .device_timeline import channel_offsets
from .fixed import ONE
from .mystic_timing import fixed_wait_frames
from .skill_recovery import READY_EPSILON


def _ordinary_buff_clock(evidence, controller, ability, duration, step, *, watched_buff_survives=False):
    c, a = controller["fields"], ability["fields"]
    if controller["script"] not in ("CastSkill","CastSkillWithSuspendable") or ability["script"] != "ActiveBuffAbility":
        return None
    if (duration <= 0 or c["_maxTriggerTime"] != -1 or a["_resetCdStrategy"] != 1 or
            any(c.get(k) for k in ("_resetAbilityCooldownWhenCastEnd","_checkFinishedBuffWhenStopAffect",
                                  "_useEscapeTime","_earlySkillFinishAtAttackFinished")) or
            (a["_checkBuffEarlyFinish"] and not watched_buff_survives) or
            any(a[k] for k in ("_preDelay","_escapeTime","_updateLifetimeWhenSpellStart",
                               "_enableUpdateCooldown","_ignoreInfiniteBuffCooldown","_passiveBuffs"))):
        return None
    # 73811 first checks SkillDataProvider, then asks the main ability. The auxiliary
    # projectile aura and visual/talent listeners do not participate in this predicate.
    if any(part["script"]=="SkillDataProvider" and part["enabled"] and part["active"] for part in evidence["skill"]["components"]):
        return None
    buffs = a["_buffs"]
    if not buffs or any(b["lifeTimeType"] != 1 or b["loadFromDB"] or b["remainingTimeKey"] or
                        b["durationKey"] not in ("","duration") or b["lifeTime"] for b in buffs):
        return None
    board = {b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    if any(b["durationKey"] and board.get(b["durationKey"],duration) != duration for b in buffs):
        return None
    # Same unconditional ActiveBuffAbility timer/reset options as the V079 native
    # fixtures for Ebenholz (casting wait) and Lin (cooldown on object update).
    if a["_waitForCasting"]:
        end = fixed_wait_frames(Fraction(duration)*ONE/step)
        return dict(spell_frame=1, end=end, first_recovery_tick=end+2, end_phase="after_fixed")
    end = 1 + (int(Fraction(duration)*ONE) - READY_EPSILON)//step + 1
    return dict(spell_frame=1, end=end, first_recovery_tick=end+1, end_phase="objects")


def _ordinary_replacement(tables, evidence, ability, facing, interval):
    a = ability["fields"]
    if (ability["script"] not in ("RangedAttack","RangedAttackWithExtraActions") or a["_timeMode"] != 0 or not a["_waitForAttackEvent"]
            or a["_preDelay"] or a["_escapeTime"] or a["_upAnimKey"] or a["_downAnimKey"]
            or a["_interuptIfTargetDead"] or a["_selectTargetTiming"] != 0 or a["_useMountGroup"]
            or a["_projectileDestination"] != 0 or a["_projectileKey"] not in evidence["projectiles"]):
        return None
    interval, origin = _ordinary_interval(tables,evidence,interval)
    action = a["_animKey"]
    animation, source = _selected_clip(tables,evidence,action,facing)
    if animation["loop"] or animation["speed"] != 1 or animation["duration"] <= 0:
        return dict(reason="replacement_clip_options")
    scale = max(single(0.1),single(single(interval)/single(animation["duration"])))
    if a["_maxAnimScale"] >= 0:
        scale = min(scale,a["_maxAnimScale"])
    if not scale or animation["clip"] not in evidence["animations"]["views"][facing]["clips"]:
        return dict(reason="replacement_bake_unavailable")
    events = sample_clip_events(evidence["animations"],facing,animation["clip"],
                                playback_speed=single(1/scale),timestep=tables.fixed_timestep())
    attack = [e for e in events if e["event"]=="OnAttack"]
    if len(attack)!=1:
        return dict(reason="replacement_attack_key_not_unique")
    release = attack[0]["clip_tick"]
    return dict(clip=animation["clip"],source=source, attack_key_seconds=attack[0]["seconds"],
                baked_event=release-1, release=release, projectile=a["_projectileKey"], playback_speed=single(1/scale),
                attack_interval=interval,interval_origin=origin,cached_duration_seconds=single(single(animation["duration"])*scale),
                wait_for_projectile_invalid=bool(a["_waitForProjectileInvalid"]))


def _after_spell_clock(spell, duration, step, *, same_frame_resume=False):
    remaining = max(0,int(duration*ONE)-(spell+int(same_frame_resume))*step)
    end = spell+fixed_wait_frames(Fraction(remaining,step))
    return dict(spell_frame=spell,end=end,first_recovery_tick=end+2,end_phase="after_fixed")


def _fixed_replacement(tables,evidence,ability,facing,direction,step,*,same_frame_resume=False):
    """Two V079 non-projectile replacements, when the ordinary attack actually starts."""
    a = ability["fields"]
    if (ability["script"] not in ("EbnhlzS2SpawnTokenAbility","MeleeAttack") or a["_timeMode"]!=1 or
            a["_upAnimKey"] or a["_interuptIfTargetDead"] or a["_escapeTime"]):
        return None
    key = (a["_downAnimKey"] if direction=="DOWN" and a["_downAnimKey"] else a["_animKey"])
    clip,source = _selected_clip(tables,evidence,key,facing)
    if clip["loop"]:
        return None
    if a["_waitForAttackEvent"]:
        attacks = [e for e in sample_clip_events(evidence["animations"],facing,clip["clip"],
                          playback_speed=1,timestep=tables.fixed_timestep()) if e["event"]=="OnAttack"]
        if len(attacks)!=1:
            return None
        first=attacks[0]["clip_tick"]
        events=[dict(frame=first-1,kind="baked_OnAttack",key=key)]
    else:
        first=fixed_wait_frames(Fraction(int(single(a["_preDelay"])*ONE),step))-int(same_frame_resume)
        events=[]
    kind = "spawn_on_selected_tile" if ability["script"]=="EbnhlzS2SpawnTokenAbility" else "melee_spell"
    events.append(dict(frame=first,kind=kind))
    return dict(spell_frame=first,events=events,clock=dict(cached_duration_seconds=single(clip["duration"]),source=source))


def _end_condition(sid, controller, abilities):
    if sid in ("skchr_qanik_2","skchr_pasngr_1"):
        return "wait_for_projectile_invalidation"
    if controller=="CastSkillWithLimitTimes":
        return "consume_remaining_uses_or_discard"
    if sid in ("skchr_aphris_1","skchr_logos_1","skchr_gdglow_2"):
        return "unlimited_duration"
    if sid=="skchr_sbell2_2":
        return "manual_deactivation_of_unlimited_duration"
    if sid in ("skchr_marcil_1","skchr_marcil_2","skchr_marcil_3"):
        return "mana_driven_skill"
    if any(c["fields"].get("_checkBuffEarlyFinish") for c in abilities):
        return "watched_buff_expiry_or_early_removal"
    return "requires_ability_completion"


def run_committed_cast(tables, evidence, *, horizon, facing="Front", effective_attack_interval=None, finish_frame=None,
                       direction="RIGHT", watched_buff_survives=False,commit_phase="after_tick"):
    """One uninterrupted cast. Frame 0 is a successful commit, not a ready SP gauge or button press.

    An optional externally observed end occurs after that frame's object updates.
    Attack release time uses the aligned first baked sample, like _first_searches.
    This clock emits spell/launch callbacks; receivers and their HP belong to the scene.
    """
    if (type(horizon) is not int or horizon < 0 or facing not in ("Front","Back") or
            direction not in ("UP","DOWN","LEFT","RIGHT") or type(watched_buff_survives) is not bool):
        raise ValueError("需要非负模拟帧和有效朝向")
    if finish_frame is not None and (type(finish_frame) is not int or finish_frame < 0):
        raise ValueError("外部技能结束帧必须为非负整数")
    same_frame_resume=commit_phase=="before_coroutines"
    if commit_phase not in ("after_tick","before_coroutines") or (same_frame_resume and evidence["skill_id"]!="skchr_ifrit_2"):
        raise ValueError("该技能的提交相位尚未核实")
    if effective_attack_interval is not None and (type(effective_attack_interval) not in (int,float)
            or not isfinite(effective_attack_interval) or effective_attack_interval <= 0):
        raise ValueError("需要有效的正攻击间隔")
    skill = tables.read("excel/skill_table.json")[evidence["skill_id"]]["levels"][evidence["rank"]-1]
    roots = [c for c in evidence["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    controller, = [c for c in roots if "_allowSpRecoveryWhenAffecting" in c["fields"]]
    abilities = [c for c in roots if "_resetCdStrategy" in c["fields"]]
    blocks = not bool(controller["fields"]["_allowSpRecoveryWhenAffecting"])
    events = [dict(frame=0, kind="accepted_cast", origin="supplied_successful_commit")]
    pending = []
    report = dict(skill_id=evidence["skill_id"], controller=controller["script"],
        ability_scripts=[c["script"] for c in roots if c["type"] == "MonoBehaviour" and c is not controller],
        blocks_sp_while_affecting=blocks, events=events, unresolved=pending,
        conditions=["successful_cast_in_objects_before_coroutines" if same_frame_resume else "successful_cast_after_object_update",
                    "uninterrupted","no_external_duration_changes",
                    "base_skill_clock_excludes_talent_module_or_stage_sp_grants"])
    clock = shot = first_ability = None
    end_condition = _end_condition(evidence["skill_id"],controller["script"],abilities)
    step = int(tables.fixed_timestep()*ONE)
    cls = controller["script"]
    if cls in ("ToggleSkill","ToggleSkillWithEndAnimation"):
        # The attached PassiveBuffAbility never enters its casting state. A successful
        # cast registers the modifier, and BasicSkill.OnTick removes it next owner tick.
        clock = dict(spell_frame=0,end=0,first_recovery_tick=2,end_phase="commit",payload="toggle")
    elif cls=="AttachOrDetachSkill" and evidence["skill_id"]=="skchr_amgoat_1":
        duration = int(skill["duration"]*ONE)
        end = (duration+step-1)//step
        # _DetachSkill calls UpdateSpRecovery in this owner tick, after Entity has advanced its timer.
        clock = dict(spell_frame=0,end=end,first_recovery_tick=end+1,end_phase="objects",payload="attachment")
    if not clock and len(abilities)==1:
        ability, = abilities
        clock = _ordinary_buff_clock(evidence,controller,ability,skill["duration"],step,watched_buff_survives=watched_buff_survives)
        if watched_buff_survives and ability["fields"].get("_checkBuffEarlyFinish"):
            report["conditions"].append("watched_buff_remains_valid_until_natural_expiry")
        if controller["script"] in ("ReplacementSkill","ReplacementSkillFixed"):
            shot = _ordinary_replacement(tables,evidence,ability,facing,effective_attack_interval)
            if shot and "release" in shot and not shot["wait_for_projectile_invalid"]:
                clock = _after_spell_clock(shot["release"],shot["cached_duration_seconds"],step)
            if shot is None:
                first_ability = _fixed_replacement(tables,evidence,ability,facing,direction,step,same_frame_resume=same_frame_resume)
                if first_ability:
                    clock = _after_spell_clock(first_ability["spell_frame"],first_ability["clock"]["cached_duration_seconds"],step,
                                               same_frame_resume=same_frame_resume)
                    report["conditions"].extend(["actual_replacement_attack_start","valid_selection_at_spell"])
        elif not clock:
            first_ability = first_ability_spell(tables,evidence,roots,cast=0,phase="after_tick",facing=facing,
                                               direction=direction,effective_attack_interval=effective_attack_interval)
            if first_ability:
                first = first_ability["spell_frame"]
                a = ability["fields"]
                if ability["script"]=="ChannelingMeleeAttack":
                    pulses, tail = channel_offsets(step,int(skill["duration"]*ONE)-first*step,
                        int(a["_triggerDelta"]*ONE),int(a["_postDelayChecker"]*ONE))
                    end = first+tail
                    report["channel_pulse_frames"] = [first+n for n in pulses if finish_frame is None or first+n<finish_frame]
                    clock = dict(spell_frame=first,end=end,first_recovery_tick=end+2,end_phase="after_fixed")
                elif ability["script"]=="ActiveAbilityWrapper" and skill["duration"]>0:
                    end = fixed_wait_frames(Fraction(skill["duration"])*ONE/step)
                    clock = dict(spell_frame=first,end=end,first_recovery_tick=end+2,end_phase="after_fixed")
                elif "cached_duration_seconds" in first_ability["clock"] and not a.get("_waitForProjectileInvalid"):
                    clock = _after_spell_clock(first,first_ability["clock"]["cached_duration_seconds"],step)
    cutoff = horizon if finish_frame is None else min(horizon,finish_frame-1)
    if first_ability:
        events.extend(e for e in first_ability["events"] if e["frame"]<=cutoff)
        events.extend(dict(kind="channel_hit",frame=f) for f in report.get("channel_pulse_frames",[])[1:] if f<=cutoff)
    if shot and "release" in shot:
        report["replacement"] = shot
        if shot["baked_event"] <= cutoff:
            events.append(dict(frame=shot["baked_event"],kind="baked_OnAttack"))
        if shot["release"] <= cutoff:
            events.append(dict(frame=shot["release"],kind="projectile_release",key=shot["projectile"]))
        report["conditions"].extend(["animation_first_sample_aligned_to_commit","valid_target_through_release"])
        if shot["interval_origin"]=="max_progression_equipped_panel":
            report["conditions"].append("panel_interval_without_conditional_attack_speed_modifiers")
    elif controller["script"] in ("ReplacementSkill","ReplacementSkillFixed") and not first_ability:
        pending.append(shot["reason"] if shot else "replacement_payload_or_animation_clock")
    if clock and clock.get("payload")=="toggle":
        report["toggle_cast"] = dict(clock,stance_persists=True)
    elif clock:
        report["natural_end"] = clock
    buff_spell = None
    if len(abilities)==1 and abilities[0]["script"]=="ActiveBuffAbility":
        a = abilities[0]["fields"]
        if not a["_waitForCasting"] or not any(a[k] for k in ("_waitForAttackEvent","_interuptIfTargetDead","_preDelay")):
            buff_spell = 1
            if buff_spell<=cutoff:
                events.append(dict(frame=buff_spell,kind="active_buff_spell",buffs=[b["buffKey"] for b in a["_buffs"]]))
    if not clock and not first_ability and (not shot or "release" not in shot) and buff_spell is None:
        pending.append("spell_callback_requires_controller_or_ability_handler")
    if finish_frame is not None and (not clock or finish_frame < clock["end"]):
        end, resume, end_kind = finish_frame, finish_frame+2, "supplied_after_fixed_end"
    elif clock:
        end, resume, end_kind = clock["end"], clock["first_recovery_tick"], "natural_end"
        if clock.get("payload")=="toggle":
            end_kind="toggle_cast_finished"
        elif clock.get("payload")=="attachment":
            end_kind="detach_ability"
    else:
        end = resume = None
        end_kind = None
        if end_condition != "unlimited_duration":
            pending.append(end_condition)
    if end is not None and end <= horizon:
        events.append(dict(frame=end,kind=end_kind))
    if blocks and resume is not None and resume <= horizon:
        events.append(dict(frame=resume,kind="first_unblocked_recovery_update"))
    report.update(end_frame=end, first_unblocked_recovery_tick=resume if blocks else 1)
    report["recovery_basis"] = end_kind or end_condition
    return report

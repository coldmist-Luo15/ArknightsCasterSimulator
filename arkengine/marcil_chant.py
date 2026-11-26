"""玛露西尔 V079. Resolve a delivered initial trigger, the chant timers and actual DoStartSkill."""
from .animation_assets import read_skin_actions
from .baked_mount import single
from .fixed import ONE
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer


UID = "char_4141_marcil"


def _segments(tables, anim, facing, total):
    keys = tuple(key for key in anim.values() if key)
    cache = ("marcil_chant_skin", keys)
    if cache not in tables._prefab_data:
        tables._prefab_data[cache] = read_skin_actions(tables, "chararts/"+UID+".ab", "defaultskin", keys)
    skin = tables._prefab_data[cache]
    actions = skin["views"][facing.lower()]["actions"]
    begin = actions[anim["beginAnim"]]
    end = actions[anim["endAnim"]] if anim["endAnim"] else None
    if any(a["speed"] != 1 for a in actions.values()):
        raise ValueError("吟唱动画存在尚未核实的速度修正")
    b, e = single(begin["duration"]), single(end["duration"]) if end else 0
    if single(b+e) > total:
        raise ValueError("当前等级的吟唱动作需要压缩播放")
    middle = single(single(total-b)-e)
    parts = [("begin", anim["beginAnim"], b), ("loop", anim["loopAnim"], middle), ("end", anim["endAnim"], e)]
    delta = single(tables.fixed_timestep())
    return [dict(stage=stage, key=key, seconds=seconds,
                 fixed_resumes=fixed_wait_frames(single(seconds/delta)))
            for stage, key, seconds in parts if seconds > 0], skin["source"]


def resolve_chant_start(tables, evidence, *, frame=0, phase="after_tick", facing="Front", mana=None,
                        owner_state="idle", trigger=None, triggerable=True, cast_succeeds=True,
                        repress_frame=None, interrupt_frame=None, owner_unavailable_frame=None,
                        mana_changes=None, first_object_tick=None):
    """Stable 30 Hz. mana_changes applies absolute SP before that frame's OBJECTS phase.

    A re-press is delivered in INPUT (after OBJECTS, before COROUTINES). An
    external interruption is an observed skill-state exit at OBJECTS. The
    auto trigger may supply first_object_tick when it precedes OnTick in its own
    frame. An omitted mana assumes an accepted gate with no later SP changes.
    """
    if evidence["operator_id"] != UID or evidence["animations"]["skin"] != "defaultskin":
        raise ValueError("吟唱时钟仅核对了玛露西尔默认时装")
    if type(frame) is not int or frame < 0 or phase not in ("input", "before_coroutines", "after_tick") or facing not in ("Front","Back"):
        raise ValueError("无效的吟唱触发帧、相位或朝向")
    if owner_state not in ("idle","attack","born") or any(type(v) is not bool for v in (triggerable,cast_succeeds)):
        raise ValueError("吟唱入口需要触发前的现场状态")
    for f in (repress_frame, interrupt_frame, owner_unavailable_frame):
        if f is not None and (type(f) is not int or f <= frame):
            raise ValueError("重按或打断必须位于首次触发之后")
    if first_object_tick is None:
        first_object_tick = frame+1
    if type(first_object_tick) is not int or first_object_tick not in (frame,frame+1) or (first_object_tick==frame and phase!="before_coroutines"):
        raise ValueError("角色首次逻辑更新必须与触发相位相符")
    mana_changes = {} if mana_changes is None else {int(k):v for k,v in mana_changes.items()}
    if mana_changes and mana is None:
        raise ValueError("改变技力前需要明确的初始技力")
    row = tables.read("excel/skill_table.json")[evidence["skill_id"]]["levels"][evidence["rank"]-1]
    bb = {b["key"]: b["value"] for b in row["blackboard"]}
    if any(type(v) is not int or not 0 <= v <= bb["mana_max"] for v in [*mana_changes.values(), *([] if mana is None else [mana])]) or any(f <= frame for f in mana_changes):
        raise ValueError("实际技力必须在所选技能的上下限内，技力变动应晚于触发帧")
    roots = [c for c in evidence["skill"]["components"] if not c["path"] and c["active"] and c["enabled"]]
    chant, = [c["fields"] for c in roots if c["script"] == "ChantBeforeSkill"]
    ctrl, = [c for c in roots if "_switchToState" in c["fields"]]
    if not ctrl["fields"]["_switchToState"] or ctrl["fields"]["_beginAnim"] or ctrl["fields"]["_canSwitchDuringBorn"]:
        raise ValueError("技能入口与已核实的吟唱控制器不同")
    configured, min_sp = row["skillType"], int(bb["skill_cost_min_sp"])
    trigger = configured if trigger is None else trigger
    if trigger not in ("AUTO","MANUAL"):
        raise ValueError("吟唱仅支持手动或自动触发")
    events = [dict(kind="trigger_delivery", frame=frame, phase=phase)]
    result = dict(skill_id=evidence["skill_id"], controller=ctrl["script"], configured_trigger=configured,
                  trigger=trigger, events=events, cast_frame=None, spell_frame=None, minimum_mana=min_sp,
                  mana_at_trigger=mana, first_object_tick=first_object_tick,
                  assumptions=["no_runtime_animation_override", "one_resume_per_advancing_tick",
                               "state_switch_and_begin_gate_succeed"])
    def finish(status):
        result["status"] = status
        return result
    if owner_state == "born":
        return finish("rejected_during_birth")
    if trigger != configured:
        return finish("wrong_trigger_for_new_cast")
    if not triggerable or mana is not None and mana < min_sp:
        return finish("rejected_at_trigger_gate")
    main_seconds, extra_seconds = single(bb["chant_duration"]), single(bb.get("extra_chant_duration", 0))
    segments, source = _segments(tables,chant["_animation"],facing,single(main_seconds+extra_seconds))
    result.update(segments=segments, skin_source=source)
    period = int(main_seconds * ONE)
    dt = int(tables.fixed_timestep()*ONE)
    main, extra = period, 0
    start_buff, = chant["_buffsWhenStartChant"]
    rule = tables.read("battle/buff_template_data.json")[start_buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    if (start_buff["buffKey"] != "marcil_in_chant" or not start_buff["waitFirstTriggerInterval"] or
        [(n["$type"].split("+")[-1].split(",")[0]) for n in rule] != ["CheckHasSp","InterruptCharacterSkill"] or
        rule[0]["_condType"]!="LT" or rule[0]["_checkKey"]!="skill_cost_min_sp" or not rule[1]["_switchOutFromSkillState"]):
        raise ValueError("吟唱中断的技力检测与模板不符")
    mana_timer = RecoveryTimer(int(start_buff["triggerInterval"]*ONE))
    events.extend([dict(kind="enter_skill_state",frame=frame,old_state=owner_state),
                   dict(kind="chant_start",frame=frame,buff=start_buff["buffKey"])])
    segment_starts = {}
    due = frame
    for i, s in enumerate(segments):
        segment_starts[due] = s
        due += s["fixed_resumes"] - int(i == 0 and phase != "after_tick")
    sub = "main"
    def succeed(stage, when, origin):
        key = "_buffsWhenMainChantEnd" if stage=="main" else "_buffsWhenExtraChantEnd"
        buffs = [b["buffKey"] for b in chant[key]]
        events.append(dict(kind=stage+"_chant_succeeded",frame=when,from_path=origin,
                           buffs=buffs))
        if "marcil_3_main_chant" in buffs:
            effect = tables.read("battle/buff_template_data.json")["marcil_3_main_chant"]["eventToActions"]["ON_BUFF_START"][0]
            if effect["_abilityName"]!="BoomStart" or not effect["_castDirectly"]:
                raise ValueError("玛露西尔范围标记的能力入口不符")
            events.append(dict(kind="request_area_marker_ability",frame=when,ability=effect["_abilityName"]))
    def commit(when, origin):
        events.append(dict(kind="do_start_skill",frame=when,from_path=origin))
        if not cast_succeeds:
            return finish("cast_failed_at_ability_or_target_gate")
        result["cast_frame"] = when
        events.append(dict(kind="cast_commit",frame=when))
        result["mana_at_commit"] = mana
        if evidence["slot"]==2:
            during, = [c for c in roots if c["script"]=="BuffDuringSkill"]
            buff, = during["fields"]["_buffs"]
            payment = evidence["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_LATE_ENABLE"][0]
            if (payment["$type"].split("+")[-1].split(",")[0]!="ModifySp" or not payment["_isMinis"]
                    or not payment["_forceFlag"] or payment["_spString"]!="skill_cost_min_sp"):
                raise ValueError("玛露西尔二技能的魔力扣除动作变化")
            events.append(dict(kind="skill_start_buff",frame=when,
                               buffs=[b["buffKey"] for b in during["fields"]["_buffs"]]))
            result["mana_payment"] = dict(frame=when+1,phase="BUFF_LATE_ENABLE",cost=min_sp,
                                          remaining=mana-min_sp if mana is not None else None)
        if evidence["slot"] in (1,2):
            ability, = [c for c in roots if c["script"]=="ActiveBuffAbility"]
            if ability["fields"]["_waitForCasting"]:
                raise ValueError("吟唱后施法使用不同等待路径")
            result["spell_frame"] = when+1
            events.append(dict(kind="active_buff_spell",frame=when+1,
                               buffs=[b["buffKey"] for b in ability["fields"]["_buffs"]]))
        return finish("chant_cast_and_active_buff_spell" if result["spell_frame"] is not None else "chant_cast_reached_channel_ability")
    for now in range(frame, due+1):
        mana = mana_changes.get(now,mana)
        if now >= first_object_tick:
            if now in (interrupt_frame,owner_unavailable_frame):
                events.append(dict(kind="chant_interrupted",frame=now,reason="state_exit" if now==interrupt_frame else "owner_cannot_use_skill"))
                return finish("chant_interrupted")
            if mana_timer.advance(dt) and mana is not None and mana < min_sp:
                events.append(dict(kind="chant_interrupted",frame=now,reason="mana_below_minimum_on_periodic_check"))
                return finish("chant_interrupted")
            if main > 0:
                main -= dt
                if main <= 0:
                    succeed("main", now, "periodic_timer")
                    extra = int(extra_seconds*ONE)
                    sub = "extra" if extra else "finished"
            elif extra > 0:
                extra -= dt
                if extra <= 0:
                    succeed("extra",now,"periodic_timer")
                    sub = "finished"
        if now == repress_frame:
            if sub=="extra" and chant["_suspendableWhenExtraChant"]:
                events.append(dict(kind="additional_chant_stopped",frame=now))
                return commit(now,"repress_in_extra_chant")
            events.append(dict(kind="repress_unavailable",frame=now,stage=sub))
        if now in segment_starts:
            s = segment_starts[now]
            events.append(dict(kind="chant_animation",frame=now,stage=s["stage"],action=s["key"]))
    # The coroutine also dispatches still-valid phase timers immediately before ResetChant.
    if main > 0:
        succeed("main",due,"coroutine_completion")
    if extra > 0:
        succeed("extra",due,"coroutine_completion")
    return commit(due,"natural_chant_completion")

"""Marcille's ordinary card, SharedDataBbModifier and the marcil_t owner-finish writeback."""
from .fixed import ONE
from .module_effects import active_board, equipped_phase, talent_rows
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


def _action(node):
    return node["$type"].split("+")[-1].split(",")[0]


def first_deployment_mana(tables,card,skill,frame):
    from .caster_prefabs import caster_prefab_evidence
    evidence=caster_prefab_evidence(tables,card["operator_id"],card["skill"],skill_level=card["skill_level"],
             module_id=next(iter(card["module_ids"]),None),module_level=card["module_level"])
    mana,_,_=entry_mana(tables,dict(card,skill_row=skill),evidence,dict(frame=frame),None)
    return mana


def field_mana(tables,card,config,deploy,end,initial,*,stops=(),observed=None,payment=None,payments=()):
    """Entity SP ticks after deployment. Aura inputs occur after that frame's OBJECTS."""
    from .caster_catalog import resolve_caster_loadout
    from .mostima_aura import MOSTIMA, aura_profile, effective_period, set_period
    from .skill_recovery import NaturalSkillPoints
    source=resolve_caster_loadout(tables,**config["loadout"])
    if source["operator_id"]!=MOSTIMA:
        raise ValueError("场上术师技力光环须绑定莫斯提马的实际装载")
    profile=aura_profile(tables,source)
    bb={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    base=card["skill_row"]["spData"]["increment"]
    if base!=0:
        raise ValueError("玛露西尔的技能基础自然回复已发生变化")
    active=config.get("active_on_deploy",False)
    def period(enabled):
        if type(enabled) is not bool:
            raise ValueError("光环增减需给布尔状态")
        return effective_period(profile,card["operator_id"],"CASTER",enabled,base_rate=base)
    state=NaturalSkillPoints(initial,int(bb["skill_cost_min_sp"]),int(bb["mana_max"]),None)
    set_period(state,period(active),stopped=False)
    changes=config.get("changes",[])
    if (not isinstance(changes,list) or any(type(op.get("frame")) is not int or op["frame"]<deploy or type(op.get("active")) is not bool
            for op in changes) or [r["frame"] for r in changes]!=sorted(r["frame"] for r in changes)):
        raise ValueError("莫斯提马光环变化须按实际输入帧排列")
    step=int(tables.fixed_timestep()*ONE)
    credits=[]
    observed = observed or {}
    cost_before_objects={}
    cost_after_objects={}
    for op in payments:
        costs=cost_after_objects if op["phase"]=="COROUTINES" else cost_before_objects
        costs[op["frame"]]=costs.get(op["frame"],0)+op["cost"]
    if payment is not None:
        cost_before_objects[payment["frame"]]=payment["cost"]
    first_ready=deploy if initial>=state.cost else None
    index=0
    for frame in range(deploy,end+1):
        stopped=any(a<=frame<=b for a,b in stops)
        if frame in cost_before_objects:
            state.points=max(0,state.points-cost_before_objects[frame])
        if frame>deploy and state.advance(step,stopped=stopped):
            credits.append(dict(frame=frame,mana=state.points))
            if first_ready is None and state.points>=state.cost:
                first_ready=frame
        if frame in observed:
            value=observed[frame]
            if type(value) is not int or not 0<=value<=state.maximum:
                raise ValueError("吟唱前检查到的魔力快照超出该技能上限")
            state.points=value
        while index<len(changes) and changes[index]["frame"]==frame:
            active=changes[index]["active"]
            set_period(state,period(active),stopped=stopped)
            index+=1
        if frame in cost_after_objects:
            state.points=max(0,state.points-cost_after_objects[frame])
    return dict(mana=state.points,first_ready=first_ready,credits=credits,
                base_rate=base,ally_bonus=profile["ally"],rate=base+(profile["ally"] if active else 0),
                period_q32=state.timer.period if state.timer else None,remaining_q32=state.timer.remaining if state.timer else None)


def _blast_payments(activation, end, *, ends_in_input):
    return [
        payment for payment in activation["blast_clock"]["events"]
        if payment["kind"] == "spend_mana" and payment["frame"] <= end
        and not (ends_in_input and payment["frame"] == end and payment["phase"] == "COROUTINES")
    ]


def field_mana_after_blast(tables,card,config,deploy,end,initial,activation,*,observed=None,ends_in_input=False):
    first_unblocked=activation["blast_clock"]["first_unblocked_recovery_tick"]
    return field_mana(tables,card,config,deploy,end,initial,observed=observed,
                      stops=((activation["cast_frame"]+2,first_unblocked-1),),
                      payments=_blast_payments(activation,end,ends_in_input=ends_in_input))


def entry_mana(tables, card, evidence, event, cast_at_deployment):
    board = {row["key"]: row["value"] for row in card["skill_row"]["blackboard"]}
    cap = int(board["mana_max"])

    def checked(value):
        if type(value) is not int or not 0 <= value <= cap:
            raise ValueError("魔力值超出该技能的范围")
        return value

    history = event.get("card_events", [])
    if not isinstance(history, list) or any(not isinstance(op,dict) or op.get("kind") not in ("deploy","finish") for op in history):
        raise ValueError("卡牌历史需按输入顺序记录部署与退场")
    if "entry_mana" in event:
        if history or "completed_off_field_recoveries" in event or "first_card_tick_frame" in event:
            raise ValueError("入场快照与部署前的卡牌时钟不能同时提供")
        return checked(event["entry_mana"]), "stored_deck_mana", None

    fields, = [part["fields"] for part in evidence["character"]["components"]
               if part["script"] == "PeriodModifySharedDataBbTalent" and part["active"] and part["enabled"]]
    if [fields[k] for k in ("_valueKey", "_addKey", "_maxKey", "_initKey")] != ["mana", "mana_add", "mana_max", "mana_init"]:
        raise ValueError("玛露西尔的卡牌共享黑板字段变化")
    raw=tables.read("excel/character_table.json")[card["operator_id"]]["talents"]
    phase=equipped_phase(tables,card)
    level=(card["elite"],card["level"],card["potential"])
    gain=active_board(raw[0]["candidates"],*level)[fields["_addKey"]]
    talent=talent_rows(phase,1,"2",raw[1]["candidates"])
    bonus=active_board(talent,*level).get("mana_init",0)
    mana = int(min(cap, board["mana_init"] + bonus))
    if "completed_off_field_recoveries" in event:
        credits = event["completed_off_field_recoveries"]
        if history or "first_card_tick_frame" in event or type(credits) is not int or credits < 0:
            raise ValueError("场下已回次数需为独立的非负整数快照")
        return int(min(cap, mana + credits * gain)), "first_deployment_skill_and_talent_mana", None

    ops = tables.read("battle/buff_template_data.json")["marcil_t"]["eventToActions"]["ON_OWNER_FINISH"]
    branch, _, copy = ops
    save, = branch["_succeedNodes"]
    clear, = branch["_failNodes"]
    if (branch["_conditionNode"]["_var"] != "clearSp" or _action(save) != "AssignCurSpToBB"
            or any(save[k] for k in ("_isRatio", "_useProgressLayer", "_useProgressToFull", "_useProgressToReady"))
            or _action(clear) != "AssignValueToBB" or clear["_value"] != 0 or not copy["_copyToSharedBlackboard"]
            or any(op["_blackboardKey"] != "mana" for op in (save, clear, copy))):
        raise ValueError("玛露西尔的退场魔力回写动作变化")
    # FrameLoop runs DECK before INPUT, including on its initial frame. A capture may start
    # with the card inserted later; in that case its first actual OnTick anchors the clock.
    first = event.get("first_card_tick_frame", 0)
    if type(first) is not int or first < 0 or first > event["frame"]:
        raise ValueError("首个卡牌更新帧无效")
    step = int(tables.fixed_timestep() * ONE)
    period = _raw_f32(fields["_period"])
    # PeriodicTimer.Update(delta, true) tests MathUtil.LE then Reset(period, true)
    # discards the overshoot, including for the first off-field tick after USING.
    ticks_per_credit = (period - READY_EPSILON) // step + 1
    elapsed = 0
    using = False
    prior_deployment = None
    previous_cast = None
    next_tick = first
    credited = 0
    for op in [*history, {"frame": event["frame"], "kind": "entry"}]:
        frame = op["frame"]
        if type(frame) is not int or frame < next_tick or frame > event["frame"]:
            raise ValueError("卡牌部署/离场记录需按帧递增，且早于本次部署")
        ticks = frame - next_tick + 1
        if using:
            elapsed = 0
        else:
            credits, elapsed = divmod(elapsed + ticks, ticks_per_credit)
            mana = int(min(cap, mana + credits * gain))
            credited += credits
        if op["kind"] in ("deploy", "entry") and not using:
            using = True
            previous_cast = None
            prior_deployment = op
            if op["kind"]=="deploy" and (card["skill"]==2 or "trigger_frame" in op):
                if card["skill"] not in (2,3) or set(op.get("chant_options",{}))-{"repress_frame"}:
                    raise ValueError("退场魔力自动结算支持自动二技能和已记录按键的三技能")
                previous_cast=cast_at_deployment(op,mana)["activation"]
        elif op["kind"] == "finish" and using:
            clear_sp = op.get("clear_sp", False)
            if type(clear_sp) is not bool:
                raise ValueError("离场 clear_sp 应为当时的天赋黑板标志")
            aura=prior_deployment.get("mostima_aura")
            prior=previous_cast or {}
            clock=prior.get("blast_clock")
            if not clear_sp and "mana" not in op:
                if aura and clock:
                    mana=field_mana_after_blast(tables,card,aura,prior_deployment["frame"],frame,mana,prior,
                                                ends_in_input=True)["mana"]
                else:
                    if aura:
                        commit=prior.get("cast_frame")
                        until=min(frame,commit) if commit is not None else frame
                        mana=field_mana(tables,card,aura,prior_deployment["frame"],until,mana)["mana"]
                    payment=prior.get("mana_payment")
                    if payment is not None and payment["frame"]<=frame:
                        # The owner's ordinary late update runs before INPUT.
                        mana=payment["remaining"]
                    post_tick=prior.get("first_object_update_after_commit")
                    if post_tick is not None and post_tick["frame"]<=frame:
                        mana=post_tick["mana"]
                    if clock:
                        for payment in _blast_payments(prior,frame,ends_in_input=True):
                            mana=payment["remaining"]
            # Entity.OnFinish dispatches owner-finish actions before the buff container
            # finishes the pending chant buffs. Their subsequent clearSp cannot change
            # the mana that marcil_t already copied to the card in that dispatch.
            mana = 0 if clear_sp else checked(op.get("mana",mana))
            using = False
        else:
            raise ValueError("卡牌历史需依次给出部署与退场")
        next_tick = frame + 1
    return mana, "deck_card_timer", dict(first_card_tick_frame=first, ticks_per_credit=ticks_per_credit,
            completed_periods=credited, elapsed_ticks=elapsed)

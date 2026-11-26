"""Deployment to skill attacks on stationary receivers for Carnelian and 圣聆初雪."""
from math import isfinite
from .attack_range import range_cells
from .caster_opening import deploy_to_first_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .enemy_speed import MIN_MOVE_Q32
from .fixed import ONE, mul
from .lin_output_aura import trigger_contact
from .phalanx_mode_scene import run_phalanx_active_attacks
from .phalanx_skills import CARN, SNOW, _talent, execute_phalanx_event
from .skill_recovery import NaturalSkillPoints, READY_EPSILON, RecoveryTimer, _divide
from .volley_damage import _raw_f32


def carnelian_talent_first_tick(evidence,deployment):
    talent,=(r["fields"] for r in evidence["character"]["components"] if r["path"]=="Talents/2" and r["script"]=="Talent")
    if talent["_attachInDummy"] or talent["_checkValidBySkillIndex"] or talent["_defaultModeIndex"]!=-1:
        raise ValueError("蓄势待发的首个部署更新点已变化")
    return deployment+1


class CarnelianCharge:
    """Keep the deployed talent's poll clock running while the skill blocks SP."""

    def __init__(self,tables,card,ev,state,start,first,logs):
        talent, = [c for c in ev["character"]["components"] if c["path"]=="Talents/2" and c["script"]=="PassiveBuffAbility"]
        pref, = [b for b in talent["fields"]["_buffs"] if b["buffKey"]=="billro_t_2"]
        branch, = tables.read("battle/buff_template_data.json")[pref["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        check = branch["_conditionNode"]
        create = branch["_succeedNodes"][-1]
        finish, = branch["_failNodes"]
        child = create["_buff"]
        modifier, = child["attributes"]["attributeModifiers"]
        if (not pref["waitFirstTriggerInterval"] or check["_count"]!=1 or check["_condType"]!="GE"
                or not create["_isDerivedBuff"] or child["buffKey"]!="billro_t_2[sp_recover]"
                or (modifier["attributeType"],modifier["formulaItem"])!=("SP_RECOVERY_PER_SEC","ADDITION")
                or not finish["$type"].split(",")[0].endswith("+FinishDerivedBuff")):
            raise ValueError("蓄势待发的技力检查配置发生变化")
        self.state,self.frame,self.first,self.logs=state,start,first,logs
        self.poll=RecoveryTimer(_raw_f32(pref["triggerInterval"]))
        boost=_talent(card,1,"2")["sp_recovery_per_sec"]
        self.period=_divide(ONE,_raw_f32(1+boost))
        self.step=_raw_f32(tables.fixed_timestep())
        if first==start:
            self.poll.advance(self.step)

    def advance_to(self,end,*,blocked=False,wait_for_full=False):
        state=self.state
        for frame in range(self.frame+1,end+1):
            self.frame=frame
            # Entity ticks its buff handler before ordinary SP recovery on the same tick.
            if frame>=self.first and self.poll.advance(self.step):
                ready=state.points>=state.cost
                period=self.period if ready else ONE
                if period!=state.timer.period:
                    state.timer.change_period(period)
                    self.logs.append(dict(frame=frame,kind="accelerated_recovery" if ready else "base_recovery",
                                          sp=state.points,period_q32=period))
            state.advance(self.step,stopped=blocked)
            if wait_for_full and state.points==state.maximum:
                break
        return self.frame


def _aura_count(tables, card, event):
    if card["module_info"]["typeIcon"]!="plx-y":
        return 0
    before = range_cells(card["base_range"],event["placement"],event["rows"],event["cols"])
    after = skill_range_cells(tables,card,event)["cells"]
    count = 0
    for target in event["targets"].values():
        if target["eligible"]:
            pos = target["row"],target["col"]
            pre = trigger_contact(pos,target["radius"],before)
            post = trigger_contact(pos,target["radius"],after)
            if pre is None or post is None or pre!=post:
                return None
            count += pre
    return count


def run_phalanx_deployment(tables, card, event):
    """Input on the first ready tick or wait for the operator's charged skill."""
    if card["operator_id"]==SNOW and any(t.get("airborne") is False for t in event["targets"].values()):
        report = _ground_snow_deployment(tables,card,event)
    else:
        report = _deployment(tables,card,event)
    if card["operator_id"]==CARN and report.get("cast_frame") is not None:
        carnelian_taunt(tables,card,event,report)
    if (card["operator_id"]==SNOW and card["skill"]==1 and "snow_eligible_tiles" in event and
            report.get("cast_frame") is not None and report["status"]!="unresolved"):
        from .sbell_snow import natural_snow_pulses, run_s1_snow_forward
        cast = report["cast_frame"]
        last = report.get("through_frame",event["end_frame"])
        talent = _talent(card,0,"1")
        pulses = list(natural_snow_pulses(tables,card,event["frame"],last))
        maximum = int(talent["max_cast_cnt"])
        n = min(maximum,int(talent["first_snow"])+sum(f<=cast for f in pulses))
        base = range_cells(card["base_range"],event["placement"],event["rows"],event["cols"])
        cells = set(base).intersection(tuple(v) for v in event["snow_eligible_tiles"])
        initial = [dict(row=r,col=c,layers=n) for r,c in cells if n]
        spread = run_s1_snow_forward(tables,card,{**event,"frame":cast,"end_frame":last,"initial_snow":initial,
                                                 "base_growth_frames":[f for f in pulses if f>cast]})
        report["snow_tiles"],report["snow_through_frame"],report["snow_stop"] = spread["tiles"],spread["through_frame"],spread["stop"]
        # Buff updates precede INPUT and primary spell coroutines on the same frame.
        report["events"] = sorted([*spread["events"],*report["events"]],key=lambda r:r["frame"])
    if (card["operator_id"]==SNOW and card["skill"]==2 and report["status"]!="unresolved"
            and "snow_eligible_tiles" in event and "end_tiles" in event):
        from .sbell_snow import s2_terrain_from_deploy
        if {(t["row"],t["col"]) for t in event["targets"].values() if not t["airborne"]}.intersection(tuple(p) for p in event["end_tiles"]):
            return dict(status="unresolved",reason="地面受击者与目标点处于同格，需要冻结目标的实体碰撞和阻挡顺序")
        terrain = s2_terrain_from_deploy(tables,card,event,report.get("cast_frame"),report.get("through_frame",event["end_frame"]))
        report["snow_tiles"],report["frozen_end_tiles"] = terrain["tiles"],terrain["frozen_end_tiles"]
        report["events"] = sorted([*terrain["events"],*report["events"]],key=lambda r:r["frame"])
    return report


def carnelian_taunt(tables,card,event,report):
    """Follow ON_SKILL_START's short-lived judge, then ON_SKILL_FINISH cleanup."""
    evidence=caster_prefab_evidence(tables,CARN,card["skill"],skill_level=card["skill_level"],
                                   module_id=card["module"],module_level=card["module_level"])
    talents=[b for p in evidence["equipment"].values() for c in p["components"] if c["script"]=="PassiveBuffAbility"
             for b in c["fields"]["_buffs"] if b["templateKey"]=="billro_e_003_t"]
    events=report["events"]
    skill=next((r for r in events if r["kind"]=="skill_cast"),None)
    if not skill:
        return
    base=tables.read("excel/character_table.json")[CARN]["phases"][card["elite"]]["attributesKeyFrames"][-1]["data"]["tauntLevel"]
    scale=0
    begin=finish=None
    end=report.get("through_frame",event["end_frame"])
    if talents and skill["charged"]:
        templates=tables.read("battle/buff_template_data.json")
        definition=templates[talents[0]["templateKey"]]["eventToActions"]
        create,=definition["ON_SKILL_START"]
        cleanup,=definition["ON_SKILL_FINISH"]
        judge=create["_buff"]
        check,apply,done=templates[judge["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        child=apply["_buff"]
        stat,=child["attributes"]["attributeModifiers"]
        if (check["_buffKeys"]!=["billro_talent_enhance_mark"] or judge["triggerCnt"]!=1 or
                not judge["waitFirstTriggerInterval"] or cleanup["_buffKey"]!=child["buffKey"] or
                stat["attributeType"]!="TAUNT_LEVEL" or stat["formulaItem"]!="ADDITION" or done["$type"].split(",")[0].split("+")[-1]!="FinishBuff"):
            raise ValueError("满蓄力嘲讽的判定或拆除方式已变化")
        timer=RecoveryTimer(_raw_f32(judge["firstTriggerInterval"]))
        step=_raw_f32(tables.fixed_timestep())
        begin=skill["frame"]
        while not timer.advance(step):
            begin+=1
        begin+=1
        scale=int(stat["value"])
        if "mode_frame" in report:
            duration=_raw_f32(card["skill_row"]["duration"])
            finish=report["mode_frame"]+(duration-READY_EPSILON)//step+1
        if begin<=end:
            events.append(dict(kind="taunt_changed",frame=begin,taunt_level=base+scale))
        if finish is not None and finish<=end and report["stop"]=="skill_ended":
            events.append(dict(kind="taunt_changed",frame=finish,taunt_level=base))
        events.sort(key=lambda r:r["frame"])
    report["taunt_level"]=base+(scale if begin is not None and begin<=end and (finish is None or end<finish) else 0)


def _deployment(tables, card, event, *, ground_contact_handled=False,ground_slow=None):
    uid = card["operator_id"]
    if not card["module"] or uid not in (CARN,SNOW):
        raise ValueError("请选择卡涅利安或圣聆初雪的技能和常规模组")
    start, end = event["frame"], event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<=end:
        raise ValueError("无效的部署与结束帧")
    policy = event.get("activation","first_ready")
    if policy not in ("first_ready","full_charge"):
        raise ValueError("开技策略应为 first_ready 或 full_charge")
    if policy=="full_charge" and uid!=CARN:
        raise ValueError("该技能没有蓄力机制")
    for t in event["targets"].values():
        if (type(t["eligible"]) is not bool or not all(isfinite(t[k]) for k in ("row","col","radius"))
                or t["radius"]<=0 or type(t["hp_q32"]) is not int or t["hp_q32"]<=0):
            raise ValueError("本场景需要初始存活的静止目标和有限的碰撞圆")
        if uid==SNOW and ((t.get("airborne") is not True and not ground_contact_handled)
                          or card["skill"]==3 and t.get("immune_to_induction") is not True and "attraction_terrain" not in event):
            return dict(status="unresolved",reason="该部署片段要求目标不接触地面积雪，且三技能无法移动目标")
    ev = caster_prefab_evidence(tables,uid,card["skill"],skill_level=card["skill_level"],
                               module_id=card["module"],module_level=card["module_level"])
    data = card["skill_row"]["spData"]
    if data["spType"]!="INCREASE_WITH_TIME" or data["increment"]!=1:
        raise ValueError("当前技能的自然回复或蓄力配置不符")
    cost = data["spCost"]
    state = NaturalSkillPoints(data["initSp"],cost,data["maxChargeTime"]*cost,RecoveryTimer(ONE))
    logs = [dict(frame=start,kind="deploy",sp=state.points)]
    report = dict(skill_id=card["skill_id"],operator=uid,module=card["module"],module_level=card["module_level"],
                  deploy_frame=start,events=logs,policy=policy,status="stationary_deployment_cycle")
    auto = card["skill_row"]["skillType"]=="AUTO"
    requested = event.get("first_input_frame")
    if requested is not None and (auto or policy!="first_ready" or type(requested) is not int or requested<start):
        raise ValueError("首次手动开技帧仅用于首轮就绪策略，不能早于部署")
    if uid==CARN:
        first=event.get("talent_first_update",carnelian_talent_first_tick(ev,start))
        if type(first) is not int or first<start:
            raise ValueError("天赋首次逻辑更新不能早于部署")
        charge_clock=CarnelianCharge(tables,card,ev,state,start,first,logs)
    if policy=="first_ready":
        opening = deploy_to_first_cast(tables,card,ev,deploy_frame=start,end_frame=end,
            facing=event.get("facing","Front"),direction=event["placement"]["direction"],
            next_manual_input=(lambda eligible,born_end:max(eligible,requested)) if requested is not None else None)
        if uid==CARN:
            charge_clock.advance_to(opening.through_frame)
            opening.sp=state.points
            if requested is not None and opening.cast is not None:
                opening.cast=resolve_cast_start(tables,ev,frame=opening.through_frame,phase="input",owner_state="idle",sp=state.points,
                                                facing=event.get("facing","Front"),direction=event["placement"]["direction"])
        if opening.cast is None:
            return dict(**report,stop="horizon_before_skill_button",sp_at_horizon=opening.sp)
        cast,frame,sp = opening.cast,opening.through_frame,opening.sp
    else:
        frame=charge_clock.advance_to(end,wait_for_full=True)
        if state.points!=state.maximum:
            return dict(**report,stop="horizon_before_skill_button",sp_at_horizon=state.points)
        sp = state.points
        cast = resolve_cast_start(tables,ev,frame=frame,phase="input",owner_state="idle",sp=sp,
                                  facing=event.get("facing","Front"),direction=event["placement"]["direction"])
    resources = execute_phalanx_event(tables,card,dict(kind="phalanx_activate",frame=cast["cast_frame"],sp=sp,
                    **{k:event[k] for k in ("hp_before_q32","heal_free","outgoing_heal_scales_q32","incoming_heal_scales_q32") if k in event}))
    charged = resources["charged"]
    logs.append(dict(frame=frame,kind="automatic_trigger" if auto else "skill_button",sp=sp))
    if cast["cast_frame"]>end:
        return dict(**report,stop="horizon_before_cast")
    logs.append(dict(frame=cast["cast_frame"],kind="skill_cast",charged=charged,sp=resources["sp_after"]))
    if uid==SNOW and card["skill"]==1:
        if cast["status"]!="cast_and_first_ability_spell" or cast["ability_clock"]["script"]!="MeleeAttack":
            raise ValueError("铃音吹雪的主动攻击入口发生变化")
        for callback in cast["events"]:
            if callback["kind"]=="baked_OnAttack" and callback["frame"]<=end:
                logs.append(callback)
        if cast["spell_frame"]>end:
            return dict(**report,stop="horizon_before_impact",cast_frame=cast["cast_frame"])
        count = _aura_count(tables,card,event)
        if count is None:
            return {**report,"status":"unresolved","reason":"本次命中时 Y 模组光环处于碰撞边界"}
        targets = {k:dict(v,ground=not v["airborne"]) for k,v in event["targets"].items()}
        hits = execute_phalanx_event(tables,card,{**event,"kind":"phalanx_impact","frame":cast["spell_frame"],
                                                 "targets":targets,"aura_count":count})["hits"]
        logs.append(dict(frame=cast["spell_frame"],kind="area_arts_damage",hits=hits))
        return dict(**report,stop="first_skill_impact",cast_frame=cast["cast_frame"],impact_frame=cast["spell_frame"],
                    hp_after_q32={k:hits[k]["hp_after_q32"] if k in hits else t["hp_q32"] for k,t in targets.items()})
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("本技能未走已核实的主动 Buff 入口")
    if cast["spell_frame"]>end:
        return dict(**report,stop="horizon_before_mode_switch",cast_frame=cast["cast_frame"])
    mode = dict(frame=cast["spell_frame"],kind="mode_switch",charged=charged)
    if uid==CARN:
        mode.update(heal_request_q32=resources["heal_request_q32"],heal_after_modifiers_q32=resources["heal_after_modifiers_q32"],
                    skill_max_hp_q32=resources["skill_max_hp_q32"])
        if "owner_hp" in resources:
            mode["owner_hp"]=resources["owner_hp"]
    logs.append(mode)
    stationary_attract = False
    movement_boundary = None
    attack_end = end
    if uid==SNOW and card["skill"]==3 and "attraction_terrain" in event:
        from .sbell_attraction import run_s3_attraction
        receivers = {k:dict(v,velocity=v.get("velocity",(0,0))) for k,v in event["targets"].items()}
        for target in receivers.values():
            if not target["airborne"] and ground_slow is not None and "move_speed" in target:
                target["move_speed_attribute_q32"] = max(MIN_MOVE_Q32,mul(_raw_f32(target["move_speed"]),ONE+ground_slow))
        mature_snow = set()
        if ground_contact_handled and ground_slow is not None:
            base = range_cells(card["base_range"],event["placement"],event["rows"],event["cols"])
            mature_snow = set(base).intersection(map(tuple,event["snow_eligible_tiles"]))
        selection = run_s3_attraction(tables,card,{**event,"frame":cast["spell_frame"],"targets":receivers},mature_snow=mature_snow)
        mode["attraction"] = selection["receivers"]
        skill_cells = skill_range_cells(tables,card,event)["cells"]
        for key,receiver in selection["receivers"].items():
            target = event["targets"][key]
            if receiver["status"] in ("not_selected","no_reachable_anchor"):
                continue
            if receiver["status"]!="selected" or target.get("blocked"):
                return {**report,"status":"unresolved","reason":"地块选择或阻挡解除仍需确认"}
            if receiver["stop"]=="at_destination":
                continue
            if not target["airborne"] and ground_slow is None:
                return {**report,"status":"unresolved","reason":"诱导移动时的地面积雪减速仍在改变"}
            # Static attack arithmetic remains valid while every positional sample has
            # the original definite Y-aura and attack overlap. Stop before a tile callback.
            limit = receiver["through_frame"]-int(receiver["stop"]=="tile_enter_and_leave_callbacks")
            for tick,pos in receiver["positions"].items():
                if trigger_contact(pos,target["radius"],skill_cells) is not True:
                    limit = min(limit,tick-1)
                    break
            if limit<cast["spell_frame"]:
                return {**report,"status":"unresolved","reason":"诱导起手时目标正处在 Y 模组光环的物理接触边界"}
            if limit<attack_end:
                attack_end,movement_boundary = limit,receiver["stop"]
        stationary_attract = True
    aura_count = _aura_count(tables,card,event)
    if aura_count is None:
        return {**report,"status":"unresolved","reason":"Y 模组范围有边界接触或切换后成员发生变化，需要光环接触的更新帧"}
    attacks = run_phalanx_active_attacks(tables,card,{**event,"frame":cast["spell_frame"],"end_frame":attack_end,"charged":charged,"aura_count":aura_count},
                                         ground_contact_handled=ground_contact_handled,stationary_attract=stationary_attract)
    if uid==CARN:
        charge_clock.advance_to(cast["cast_frame"])
        state.points=resources["sp_after"]
        recovery_end=attacks["events"][-1]["impact_frame"] if attacks["stop"]=="aura_needs_new_count_after_death" else attack_end
        mode_end=attacks.get("mode_end_frame",recovery_end)
        charge_clock.advance_to(min(recovery_end,mode_end),blocked=True)
        charge_clock.advance_to(recovery_end)
        report["sp_at_horizon"]=state.points
    for a in attacks["events"]:
        logs.extend([dict(kind="attack_start",frame=a["start_frame"],targets=list(a["hits"])),
                     dict(kind="area_arts_damage",frame=a["impact_frame"],hits=a["hits"])])
    # For Y, the segment stops at a death until aura removal is dispatched. No later frame is projected.
    if "remaining_marks_by_target" in attacks:
        report["remaining_marks_by_target"]=attacks["remaining_marks_by_target"]
    if "mark_cleanup" in attacks:
        logs.append(dict(kind="mark_cleanup",**attacks["mark_cleanup"]))
    if attacks["stop"]=="aura_needs_new_count_after_death":
        report["through_frame"]=attacks["events"][-1]["impact_frame"]
    if uid==CARN and "owner_hp" in resources and "mode_end_frame" in attacks:
        hp=event.get("owner_hp_at_mode_finish_q32",resources["owner_hp"]["hp_after_q32"])
        finish=execute_phalanx_event(tables,card,dict(kind="phalanx_deactivate",frame=attacks["mode_end_frame"],
                                                     charged=charged,hp_before_q32=hp))
        logs.append(dict(kind="owner_hp_after_skill",frame=attacks["mode_end_frame"],
                         hp_after_q32=finish["hp_after_q32"],max_hp_q32=finish["max_hp_q32"]))
    if movement_boundary and attacks["stop"]=="horizon":
        report.update(through_frame=attack_end,next_movement_callback=movement_boundary)
    logs.sort(key=lambda row:row["frame"])
    return dict(**report,stop="requires_attraction_motion_continuation" if "next_movement_callback" in report else attacks["stop"],
                cast_frame=cast["cast_frame"],mode_frame=cast["spell_frame"],
                hp_after_q32=attacks["hp_after_q32"],timing=attacks["timing"])


def _ground_snow_deployment(tables,card,event):
    """A new ground receiver spawns on a permanent snow tile and waits there until the primary attack(s)."""
    from .sbell_snow import natural_snow_pulses, run_snow_contact
    start,end = event["frame"],event["end_frame"]
    if len(event["targets"])!=1:
        return dict(status="unresolved",reason="该部署片段支持一名在常驻积雪上新生成的静止地面敌人")
    key,enemy = next(iter(event["targets"].items()))
    enter = enemy.get("spawn_on_snow_frame")
    if type(enter) is not int or "snow_eligible_tiles" not in event:
        return dict(status="unresolved",reason="需要敌人实际生成于雪地的帧 spawn_on_snow_frame 和可覆雪地块 snow_eligible_tiles")
    cell = enemy["row"],enemy["col"]
    base = range_cells(card["base_range"],event["placement"],event["rows"],event["cols"])
    if (not card["module"] or type(start) is not int or type(end) is not int or not 0<=start<enter<=end
            or any(type(v) is not int for v in cell) or cell not in base or
            cell not in {tuple(p) for p in event["snow_eligible_tiles"]} or not enemy["eligible"]):
        raise ValueError("敌人须在部署后生成于攻击范围内可覆雪的普通地面格心")
    talent = _talent(card,0,"1")
    maximum = int(talent["max_cast_cnt"])
    pulses = list(natural_snow_pulses(tables,card,start,end))
    initial = min(maximum,int(talent["first_snow"])+sum(f<=enter for f in pulses))
    if not initial:
        return dict(status="unresolved",reason="目标生成时尚无积雪，需要处理后续首次落雪对已占位目标的挂载")
    result = _deployment(tables,card,event,ground_contact_handled=True,
                         ground_slow=_raw_f32(talent["move_speed"])*maximum if initial==maximum else None)
    if result["status"]=="unresolved":
        return result
    cast = result.get("cast_frame")
    if any(r["kind"] in ("skill_button","automatic_trigger") and r["frame"]<=enter+2 for r in result["events"]):
        return dict(status="unresolved",reason="该片段要求地面目标在开技前已稳定进入范围")
    last = min(end,result.get("impact_frame",end),result.get("through_frame",end))
    if card["skill"]==3 and cast is not None and int(talent["first_snow"])+sum(f<=cast for f in pulses)<maximum:
        button = next(r["frame"] for r in result["events"] if r["kind"]=="skill_button")
        fast = next(b["value"] for b in card["skill_row"]["blackboard"] if b["key"]=="interval")
        # Neither the original clock nor a newly attached fast clock can change the snow
        # before this lower bound, even if skill-start and the subsequent mode spell differ.
        boundary = min(next((f for f in pulses if f>button),end+1),button+int(fast/tables.fixed_timestep()))
        if last>=boundary:
            last = boundary-1
            result["stop"] = "requires_s3_snow_timer_replacement"
    # Only S2 consumes this window on receiver ticks. The remaining slots deal one entry hit before the cast.
    window = [cast,last+1] if cast is not None and card["skill"]==2 else None
    contact = run_snow_contact(tables,card,dict(frame=enter,end_frame=last,permanent=True,layers=initial,
        skill_window=window,aura_count_at_entry=0,aura_count=1,
        layer_pulses_before_receiver=[f for f in pulses if enter<f<=last],
        enemy=dict(motion="WALK",hp_q32=enemy["hp_q32"],resistance_q32=enemy["resistance_q32"])))
    if cast is not None and any(row["kind"]=="snow_periodic_arts" and row["frame"]==cast for row in contact["events"]):
        return dict(status="unresolved",reason="同一帧首次启用二技能与受击方积雪更新需要确定对象顺序")
    for mode in result["events"]:
        if "attraction" not in mode:
            continue
        for transition in mode["attraction"][key].get("tile_transitions",[]):
            tick = transition["frame"]
            if tick>last:
                break
            contact["events"].append(dict(kind="snow_walk_off",frame=tick,tile=transition["departed"],layers_after=0))
            crossing = run_snow_contact(tables,card,dict(frame=tick,end_frame=tick,permanent=True,layers=maximum,
                skill_window=[mode["frame"],tick+1],aura_count=1,
                enemy=dict(motion="WALK",hp_q32=enemy["hp_q32"],resistance_q32=enemy["resistance_q32"])))
            contact["events"].extend(dict(row,tile=transition["entered"]) for row in crossing["events"])
    # Receiver buff ticks are in OBJECTS. Primary attacks are delivered by the later coroutine.
    stream = [*contact["events"],*result["events"]]
    stream.sort(key=lambda row:row["frame"])
    rows,hp = [],enemy["hp_q32"]
    for row in stream:
        if row["frame"]>last:
            break
        packet = row if row["kind"].startswith("snow_") else row.get("hits",{}).get(key)
        rows.append(row)
        if packet and "damage_q32" in packet:
            hp -= packet["damage_q32"]
            hp = hp if hp>=HP_EPSILON else 0
            packet["hp_after_q32"] = hp
            if hp<=HP_EPSILON:
                result["stop"] = "enemy_defeated"
                last = row["frame"]
                break
    for field in ("cast_frame","mode_frame","impact_frame"):
        if result.get(field,0)>last:
            result.pop(field)
    for row in rows:
        traced = row.get("attraction",{}).get(key)
        if traced and traced.get("through_frame",0)>last:
            traced["positions"] = {f:p for f,p in traced["positions"].items() if f<=last}
            traced["tile_transitions"] = [v for v in traced["tile_transitions"] if v["frame"]<=last]
            traced.update(through_frame=last,stop=result["stop"])
    return {**result,"events":rows,"through_frame":last,"hp_after_q32":{key:hp},"initial_contact_layers":initial,
            "conditions":["one_already_targetable_ground_receiver_spawns_in_coroutines_on_permanent_snow",
                          "first_tile_enter_precedes_new_receiver_physics_registration","no_earlier_occupants",
                          "deployed_caster_ticks_before_later_receiver","receiver_waits_at_tile_center_before_skill",
                          "ordinary_target_without_external_effects"]}

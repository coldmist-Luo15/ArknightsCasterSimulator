"""Carnelian's charge, defenses and per-target effects on ordinary ground waves."""
from .caster_cast import run_committed_cast
from .caster_runtime import default_attack, phalanx_default
from .caster_start import resolve_cast_start
from .carnelian_defense import s1_defense
from .fixed import ONE,mul
from .lin_incoming import CROSSBOWS,HighlandCrossbows
from .module_effects import trait_board
from .phalanx_deployment import CarnelianCharge,carnelian_talent_first_tick,carnelian_taunt
from .phalanx_mode_scene import _active_mode,_timing
from .phalanx_skills import CARN,execute_phalanx_event
from .phalanx_wave import PhalanxWave,ResumedGroundWalk,apply_sluggish_walk
from .skill_recovery import RecoveryTimer,deploy_skill
from .stage_channel import UnsupportedChannelScene,prepare_channel
from .volley_damage import _raw_f32


def _hold_walker(wave,key,hit,ticks,end):
    """A bound MOVE cursor retains position and velocity until the hold expires."""
    kinds={"free_motion_sample","exit_motion_sample","post_wait_motion_sample"}
    for f in range(hit-1,hit+2):
        records=[(kind,r) for kind,r in wave.history.get(f,()) if r["instance_id"]==key]
        if len(records)!=1 or records[0][0] not in kinds or not records[0][1].get("active_ticks") or records[0][1].get("leg_end"):
            return hit+1
    absolute_wait=next((f for f in range(hit+1,end+1) if any(kind=="checkpoint_begin" and
        row["instance_id"]==key and row["command"] in ("WAIT_CURRENT_FRAGMENT_TIME","WAIT_CURRENT_WAVE_TIME")
        for kind,row in wave.history.get(f,()))),None)
    boundary=absolute_wait+ticks if absolute_wait is not None else end+1
    for f in range(min(end,boundary-1),hit,-1):
        old=max(hit,f-ticks)
        if key in wave.active[old]:
            wave.positions[f][key]=wave.positions[old][key]
            wave.active[f].add(key)
        else:
            wave.positions[f].pop(key,None)
            wave.active[f].discard(key)
        history=[row for row in wave.history.get(f,()) if row[1]["instance_id"]!=key]
        if old!=hit:
            history.extend(row for row in wave.history.get(old,()) if row[1]["instance_id"]==key)
        wave.history[f]=history
    if key in wave.escapes:
        wave.escapes[key]+=ticks
    return boundary if boundary<=end else None


def run_carnelian_stage(tables,request):
    slot=request["squad"][0]["skill"]
    if slot not in (1,2,3):
        raise ValueError("卡涅利安的技能序号需要在 1 至 3 之间")
    deploy,*presses=request["actions"]
    end=request["end_frame"]
    press_by_frame={a["frame"]:a for a in presses}
    if (not presses or len(press_by_frame)!=len(presses) or list(press_by_frame)!=sorted(press_by_frame) or
            any(a!=dict(kind="skill",operator_id=CARN,frame=f,phase="after_tick") or
                not deploy["frame"]<f<=end for f,a in press_by_frame.items())):
        raise ValueError("需要依次输入部署后的帧末技能按钮")
    first_request=dict(request,actions=[deploy,presses[0]])
    heal_options={k:first_request.pop(k) for k in ("heal_free","outgoing_heal_scales_q32","incoming_heal_scales_q32") if k in first_request}
    owner_hp=first_request.pop("hp_before_q32",None)
    battle=prepare_channel(tables,first_request,operator=CARN,skill=slot,phalanx_default_mode=True,
                           allow_no_module=True)
    owner,card=battle.deploy,battle.card
    start=owner["frame"]
    gauge,_=deploy_skill(tables,card,start)
    events=[dict(kind="deploy",frame=start,sp=gauge.state.points)]
    first_poll=carnelian_talent_first_tick(battle.evidence,start)
    charge_clock=CarnelianCharge(tables,card,battle.evidence,gauge.state,start,first_poll,events)
    default=default_attack(tables,CARN)
    if phalanx_default(tables,card,default,battle.placement_event,end,[])["status"]!="phalanx_default_disarmed":
        raise ValueError("卡涅利安的默认禁攻模式需核对")
    direction=owner["direction"]
    facing="Back" if direction=="UP" else "Front"
    natural=run_committed_cast(tables,battle.evidence,horizon=end-start,direction=direction,facing=facing)["natural_end"]
    restore,=tables.read("battle/buff_template_data.json")[f"billro_s_{slot}"]["eventToActions"]["ON_BUFF_FINISH"]
    if (natural["end_phase"]!="objects" or not restore["$type"].split(",")[0].endswith("+SwitchMode") or
            not restore["_restartFSM"] or not restore["_restoreDefault"]):
        raise ValueError("卡涅利安技能退态的默认回调需核对")
    ranged=any(e["kind"]=="spawn" and e["enemy_key"] in CROSSBOWS for e in battle.opening["events"])
    wave=PhalanxWave(battle,end,allow_ranged=ranged,allow_flying=True)
    grid=battle.scene.level["mapData"]["map"]
    layout=dict(placement=owner,rows=len(grid),cols=len(grid[0]))
    held={}
    marks={}
    motion_boundaries={}
    life=battle.scene.level["options"]["maxLifePoint"]
    cycles=[]
    current=None
    next_attack=hit_at=None
    defeated=[]
    incoming=None
    defense_poll=None
    half_active=False
    ranged_retimed={}
    base_defense=card["panel_attributes"]["def"]
    skill_board={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    unit=tables.read("excel/character_table.json")[CARN]
    trait=trait_board(unit,card["module_phase"] or {},card)
    if ranged:
        incoming=HighlandCrossbows(tables,wave,[owner],{CARN:card},live_projectiles=True)
        incoming.events=events
        if owner_hp is None:
            owner_hp=incoming.units[CARN]["hp"]
        module_poll=[b for part in battle.evidence["equipment"].values() for p in part["components"]
                     if p["script"]=="PassiveBuffAbility" for b in p["fields"]["_buffs"]
                     if b["templateKey"]=="billro_e_002[half_attribute]"]
        if module_poll:
            buff,=module_poll
            if not buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1:
                raise ValueError("卡涅利安 X 模组的双抗检查起点已变化")
            defense_poll=RecoveryTimer(_raw_f32(buff["triggerInterval"]))

    def active_at(frame):
        return current is not None and current["mode"]<frame<=current["expires"]

    def suppressed_at(frame):
        return active_at(frame) and (slot!=1 or not current["cast"]["charged"])

    def retime_ranged(key,frame):
        if key in ranged_retimed or not incoming.prepare_retiming(key,frame):
            return
        slow=tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"][0]["value"]
        ranged_retimed[key]=ResumedGroundWalk(tables,wave,key,frame,slow)
        wave.escapes.pop(key,None)
        incoming.enemies[key]["rerouted"]=True

    def finish(stop,frame,**extra):
        defeat=stop=="stage_defeated"
        visible=[e for e in events if e["frame"]<frame or e["frame"]==frame and (not defeat or e["kind"]=="enemy_escaped")]
        last_cycle=cycles[-1] if cycles else None
        report=dict(stage=battle.opening["code"],deployment=battle.placement_event if frame>start or frame==start and not defeat else None,
                    skill=slot,charged=last_cycle["cast"]["charged"] if last_cycle else None,
                    stop=stop,through_frame=frame,remaining_life=life,sp=gauge.state.points,
                    spawns=wave.spawns(frame),source=tables.source,defeated=defeated,
                    remaining_hp_q32={k:wave.attrs[k]["hp_q32"] for k,b in wave.births.items() if b["frame"]<=frame},
                    **extra)
        if owner_hp is not None:
            report["owner_hp_q32"]=owner_hp
        for cycle in cycles:
            taunt=dict(events=[cycle["cast"]],mode_frame=cycle["mode"],through_frame=min(frame,cycle["expires"]),
                       stop="skill_ended" if cycle["expires"]<=frame else stop)
            carnelian_taunt(tables,card,request,taunt)
            visible.extend(e for e in taunt["events"] if e["kind"]=="taunt_changed")
            report.update(mode_frame=cycle["mode"],taunt_level=taunt["taunt_level"])
        report["events"]=sorted(visible,key=lambda e:e["frame"])
        return report

    step=_raw_f32(tables.fixed_timestep())
    has_y=bool(card["module_info"] and card["module_info"]["typeIcon"]=="plx-y")
    for f in range(min(start,min(wave.escapes.values(),default=start)),end+1):
        if incoming and f>start:
            if defense_poll and defense_poll.advance(step):
                half_active=suppressed_at(f)
            incoming.advance(f,{})
            incoming.advance_arrows(f,lambda _:1)
            for key,motion in tuple(ranged_retimed.items()):
                if wave.attrs[key]["hp_q32"]<=0 or key not in held:
                    del ranged_retimed[key]
                    continue
                origin,ticks,charged=held[key]
                controlled=f<=origin+ticks
                if incoming.holding(key,f) or controlled and charged:
                    continue
                try:
                    walking=motion.advance(wave,f,controlled)
                except UnsupportedChannelScene as error:
                    return finish("requires_control_route_checkpoint",f-1,callback_frame=f,enemy=key,reason=str(error))
                if not walking:
                    wave.escapes[key]=f
                    del ranged_retimed[key]
            if f<end:
                for key in incoming.enemies.keys() & held.keys():
                    origin,ticks,_=held[key]
                    if f<origin+ticks and key in wave.active[f] and wave.attrs[key]["hp_q32"]>0:
                        try:
                            retime_ranged(key,f)
                        except UnsupportedChannelScene as error:
                            return finish("requires_control_route_checkpoint",f,callback_frame=f+1,enemy=key,reason=str(error))
        for k,boundary in motion_boundaries.items():
            if f==boundary and wave.attrs[k]["hp_q32"]>0:
                return finish("requires_control_route_checkpoint",f-1,callback_frame=f,enemy=k)
        for k,v in wave.escapes.items():
            if v==f and wave.attrs[k]["hp_q32"]>0:
                loss=battle.scene.enemies[wave.births[k]["enemy_key"]]["lifePointReduce"]
                if not loss["m_defined"]:
                    return finish("requires_enemy_life_point_reduce",f-1,callback_frame=f)
                life-=loss["m_value"]
                events.append(dict(kind="enemy_escaped",frame=f,enemy=k,remaining_life=life))
                if life<=0:
                    return finish("stage_defeated",f)
        for k,(origin,ticks,charged) in held.items():
            if f in (origin+1,origin+ticks,origin+ticks+1) and k in wave.active.get(f,()):
                events.append(dict(kind="root_motion" if charged else "sluggish_motion",frame=f,enemy=k,
                                   **({"movement_suppressed":f<=origin+ticks} if charged else {"sluggish_active":f<=origin+ticks}),
                                   position=wave.positions[f][k]))
        charge_clock.advance_to(f,blocked=current is not None)
        if current and f==current["expires"]:
            ended=dict(kind="mode_end",frame=f,cleared_marks=dict(marks))
            marks.clear()
            if owner_hp is not None:
                ended["owner_hp"]=execute_phalanx_event(tables,card,dict(kind="phalanx_deactivate",frame=f,
                    charged=current["cast"]["charged"],hp_before_q32=owner_hp))
                owner_hp=ended["owner_hp"]["hp_after_q32"]
            events.append(ended)
            current=None
            next_attack=hit_at=None
        if slot==1 and f in (start,cycles[-1]["expires"] if cycles else -1):
            events.append(dict(kind="owner_defenses",frame=f,profiles=s1_defense(tables,card,active=False,charged=False)))
        if current and f==current["mode"]:
            charged=current["cast"]["charged"]
            events.append(dict(kind="mode_switch",frame=f,charged=charged))
            if slot==1:
                events.append(dict(kind="owner_defenses",frame=f,profiles=s1_defense(tables,card,active=True,charged=charged)))
            next_attack=f+1
        if incoming and f>start:
            active=active_at(f)
            bonus=0 if suppressed_at(f) else trait["def"]
            if half_active:
                bonus+=trait["billro_e_002[buff].def"]
            if active and slot==1:
                bonus+=skill_board["def"]
            recipient=incoming.units[CARN]
            recipient.update(hp=owner_hp,defense=mul(_raw_f32(base_defense),ONE+_raw_f32(bonus)))
            for enemy,_,amount in incoming.hits(f,{CARN:0}):
                owner_hp=max(0,owner_hp-amount)
                recipient["hp"]=owner_hp
                events.append(dict(kind="crossbow_hit",frame=f,source=enemy,holder=CARN,damage_q32=amount,
                                   defense_q32=recipient["defense"],hp_after_q32=owner_hp))
                if not owner_hp:
                    return finish("carnelian_defeated",f)
        if f==next_attack:
            selected=wave.search(f)
            if selected:
                timing=current["timing"]
                events.append(dict(kind="attack_start",frame=f,targets=selected))
                hit_at=f+timing["ticks_to_key"]
                next_attack=f+timing["interval_ticks"]
            else:
                next_attack=f+3
        if f==hit_at:
            charged=current["cast"]["charged"]
            age=f-current["mode"]
            members=wave.aura_members(f) if has_y else []
            if members is None:
                return finish("requires_aura_physics_contact",f-1,callback_frame=f)
            targets={k:dict(v,prior_marks=marks.get(k,0)) for k,v in wave.snapshot(f).items()}
            outcome=execute_phalanx_event(tables,card,dict(**layout,kind="phalanx_impact",frame=f,targets=targets,
                    aura_count=len(members),charged=charged,ticks_since_mode=age))
            packets=outcome["hits"]
            impact=dict(kind="area_arts_damage",frame=f,hits=packets,aura_members=members)
            if slot==3:
                impact["ramp"]=outcome["ramp"]
            if slot==1:
                impact["owner_defenses"]=s1_defense(tables,card,active=True,charged=charged,mode_age=age)
            events.append(impact)
            for key,hit in packets.items():
                wave.attrs[key]["hp_q32"]=hit["hp_after_q32"]
                if hit["hp_after_q32"]==0:
                    defeated.append(key)
                    marks.pop(key,None)
                    held.pop(key,None)
                    continue
                if "marks_after" in hit:
                    marks[key]=hit["marks_after"]
                if slot!=2:
                    continue
                duration=hit["status_requests"]["root_seconds" if charged else "sluggish_seconds"]
                ticks=(_raw_f32(duration)+step-1)//step
                if incoming and key in incoming.enemies:
                    if f<end:
                        try:
                            retime_ranged(key,f)
                        except UnsupportedChannelScene as error:
                            return finish("requires_control_route_checkpoint",f,callback_frame=f+1,enemy=key,reason=str(error))
                elif f<end:
                    boundary=(_hold_walker(wave,key,f,ticks,end) if charged else
                              apply_sluggish_walk(battle,wave,key,f,ticks,end))
                    if boundary is not None:
                        motion_boundaries[key]=boundary
                    else:
                        motion_boundaries.pop(key,None)
                held[key]=(f,ticks,charged)
                hit["root_start_position" if charged else "sluggish_start_position"]=wave.positions[f][key]
                hit["movement_resumes_frame" if charged else "ordinary_speed_resumes_frame"]=f+ticks+1
        if f not in press_by_frame:
            continue
        points=gauge.state.points
        button=dict(kind="skill_button",frame=f,sp=points)
        events.append(button)
        if current:
            button["outcome"]="skill_active"
            continue
        cast=resolve_cast_start(tables,battle.evidence,sp=points,frame=f,phase="after_tick",facing=facing,direction=direction)
        button["outcome"]=cast["status"]
        if cast.get("spell_frame") is None:
            continue
        if cast["cast_frame"]!=f or cast["spell_frame"]!=f+natural["spell_frame"]:
            raise ValueError("卡涅利安的技能提交与动作时钟需核对")
        options=dict(heal_options)
        if owner_hp is not None:
            options["hp_before_q32"]=owner_hp
        charge=execute_phalanx_event(tables,card,dict(kind="phalanx_activate",frame=f,sp=points,**options))
        charged=charge["charged"]
        gauge.state.points=charge["sp_after"]
        entry=dict(kind="skill_cast",frame=f,charged=charged,sp=charge["sp_after"],
                   heal_request_q32=charge["heal_request_q32"],heal_after_modifiers_q32=charge["heal_after_modifiers_q32"])
        if "owner_hp" in charge:
            entry["owner_hp"]=charge["owner_hp"]
            owner_hp=charge["owner_hp"]["hp_after_q32"]
        events.append(entry)
        index=_active_mode(tables,card,charged)
        profile=default_attack(tables,CARN,index)
        attack=profile["fields"]
        if ((attack["_selectTargetTiming"],attack["_selectTargetSource"])!=(1,1) or
                profile["trigger_parameters"]!=dict(_minTargetNum=1,_overrideSearchTargetTick=-1,_keepTarget=0)):
            raise ValueError("卡涅利安的重新索敌入口已变化")
        components=battle.evidence["character"]["components"]
        native_attack,=(p for p in components if p["id"]==profile["attack_component"])
        selector,=(p["fields"] for p in components if p["id"]==native_attack["fields"]["_selector"]["m_PathID"])
        if profile["selector"]["_targetMotion"]!=3 or selector["_targetMotion"]!=3:
            raise ValueError("卡涅利安的地面与飞行目标筛选已变化")
        if slot==2 and charged:
            buff,=attack["_activeBuffs"]
            if buff["attributes"]["abnormalFlags"]!=[13] or buff["durationKey"]!="root" or buff["loadFromDB"]:
                raise ValueError("沙缚镣锁的束缚模板已变化")
        current=dict(cast=entry,mode=cast["spell_frame"],expires=f+natural["end"],timing=_timing(tables,card,index))
        cycles.append(current)
    return finish("horizon",end)

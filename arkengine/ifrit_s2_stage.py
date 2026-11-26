"""Ifrit's ordinary line, automatic Scorch and per-enemy burn on the native wave clock."""
from copy import copy
from collections import Counter

from .area_caster_skills import IFRIT
from .caster_catalog import resolve_caster_loadout
from .eyja_aura import EYJA,FlameBreath
from .eyja_live import EyjaAttacks
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .enemy_elements import EnemyElements
from .fixed import ONE,mul
from .ifrit_channel import ifrit_distance_factor
from .ifrit_delta import ifrit_delta_hit,ifrit_delta_rules,ifrit_x_pulse,scorched_earth_debuff
from .ifrit_replay import (ifrit_line_attack_clock,
                           rhine_circuit_definition)
from .ifrit_scorch import ScorchBurnGroup,scorch_definition,scorch_defense_rule
from .marcil_deploy import _birth
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import RecoveryTimer,deploy_skill
from .stage_deployment import RangedReentries
from .volley_damage import _raw_f32


def run_ifrit_stage(tables,request):
    return run_with_phalanx_waves(_run_ifrit_line,tables,request)


def _run_ifrit_line(tables,request,releases):
    slot=next(p["skill"] for p in request["squad"] if p["operator_id"]==IFRIT)
    placement,*actions=[a for a in request["actions"] if a["operator_id"]==IFRIT]
    pick=next((p for p in request["squad"] if p["operator_id"]==EYJA),None)
    partner=resolve_caster_loadout(tables,EYJA,pick["skill"],pick["module"],skill_level=pick["skill_level"],
                                   module_level=pick["module_level"]) if pick else None
    flame=FlameBreath.from_card(tables,partner) if partner else None
    buttons={a["frame"] for a in actions if a.get("kind")=="skill"}
    withdrawals={a["frame"]:a.get("phase","after_tick") for a in actions if a.get("kind")=="withdraw"}
    card,stage,deploy,event,world=prepare_continuous_ranged(tables,request,IFRIT,releases)
    repeats=RangedReentries.for_request(tables,stage,world,request,placement["frame"])
    evidence=caster_prefab_evidence(tables,IFRIT,slot,skill_level=card["skill_level"],
                                    module_id=card["module"],module_level=card["module_level"])
    board={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    atk=_raw_f32(card["panel_attributes"]["atk"])
    panel=card["panel_attributes"]
    interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]))
    ordinary_delay,period,_=ifrit_line_attack_clock(tables,evidence,interval)
    start,end,face=event["frame"],event["end_frame"],event["view"]
    if slot==2:
        buff,board=scorch_definition(evidence)
        cast=run_committed_cast(tables,evidence,horizon=180,facing=face,direction=deploy["direction"],commit_phase="before_coroutines")
        special_delay,=(e["frame"] for e in cast["events"] if e["kind"]=="melee_spell")
        controller,=(p["fields"] for p in evidence["skill"]["components"] if p["script"]=="ReplacementSkill")
        selector,=(p["fields"] for p in evidence["skill"]["components"] if p["script"]=="AdvancedSelector")
        if controller["_trigger"]["m_PathID"] or selector["_limitTargetNum"]:
            raise ValueError("炎爆替换触发器或全体选择器变化")
    else:
        channel=run_committed_cast(tables,evidence,horizon=round(card["skill_row"]["duration"]/tables.fixed_timestep())+120,
                                   facing=face,direction=deploy["direction"])
        debuff=scorched_earth_debuff(evidence)
        channel_ticks=set()
        self_damage=None
        component=next(c["fields"] for c in evidence["skill"]["components"] if c["script"]=="BuffDuringCasting")
        self_period=_raw_f32(component["_buffs"][0]["triggerInterval"])
        special_delay=None
    aura,=(p["fields"] for p in evidence["character"]["components"] if p["script"]=="AuraAbility")
    resistance_mod,=aura["_buffs"][0]["attributes"]["attributeModifiers"]
    if (not aura["_removeBuffWhenTargetLeave"]
            or (resistance_mod["attributeType"],resistance_mod["formulaItem"])!=(3,3)):
        raise ValueError("炎爆替换触发器、全体选择器或精神融解修正变化")
    sp,_=deploy_skill(tables,card,start)
    gauge=sp.state
    res_ratio=ONE+_raw_f32(next(b["value"] for t in card["base_talents"] if t["prefabKey"]=="1"
                                for b in t["blackboard"] if b["key"]=="magic_resistance"))
    gain=rhine_circuit_definition(card,evidence)
    pulse=RecoveryTimer(_raw_f32(gain["interval"]))
    bonus="ifrit_e_002[dice_sp]"
    rng=tables.combat_random(request.get("battle_random_seed",0))
    births=Counter(b["frame"] for b in world.births.values())
    if bonus+".prob" in gain:
        if gain[bonus+".interval"]!=gain["interval"]:
            raise ValueError("莱茵回路两次回技周期不同")
    rules=ifrit_delta_rules(card,evidence) if card["module_info"]["typeIcon"]=="bla-d" else None
    trait=_trait_values(card)
    states={k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    cells=range_cells(card["base_range"],deploy,event["rows"],event["cols"])
    if slot==2:
        defense_rule=scorch_defense_rule(buff,board)
        burn_duration=(defense_rule[1]+sp.step-1)//sp.step
    factors={k:_raw_f32(v) for k,v in (("attack_hit",board.get("atk_scale",1)),("burn_tick",board.get("burn.atk_scale",0)),("ordinary_attack",1))}
    search=start+_birth(tables,card,evidence,face)["born_exit_update"]+1
    due=None
    burns={}
    logs=[dict(kind="deployment",frame=start,sp=gauge.points)]
    stop="horizon"
    recovery=start
    on_field=world.present
    skill_from=skill_until=None

    def aura_scale(key,frame,phase):
        res=_raw_f32(world.stats[key]["res"])
        if res+states[key].res_add<=0:
            return ONE
        row=world.receiver_at(key,frame if phase=="COROUTINES" else frame-1)
        return res_ratio if on_field and row and circle_in_range(row["row"],row["col"],radii[key],cells,box_query=True) else ONE

    def hit(key,frame,source,scale):
        receiver=states[key]
        res=_raw_f32(world.stats[key]["res"])
        if actor and frame<actor.debuff.get(key,-1):
            scale=mul(scale,ONE+_raw_f32(actor.ignition["debuff_ratio"]))
        attack=atk
        if flame:
            on_flame_field=bool(actor.place and (source!="burn_tick" or frame>actor.place["frame"]))
            attack=flame.source_attack(tables,card,owner_on_field=on_flame_field,
                                      other_atk_ratio=0)
        if rules:
            packet=ifrit_delta_hit(receiver,card,rules,frame,source,attack,res,resistance_scale_q32=scale)
            damage=packet.damage_raw if packet else 0
        elif source=="channel_hit":
            distance=ifrit_distance_factor(deploy,trait,world.positions[frame][key])
            damage=ifrit_x_pulse(receiver,card,debuff,frame,IFRIT,attack,res,distance,scale)["damage_q32"]
        else:
            distance=ifrit_distance_factor(deploy,trait,world.positions[frame][key])
            packet=receiver.receive_ordinary(frame,source=source,damage_type="arts",attack_q32=mul(attack,factors[source]),
                                             mitigation_q32=res,resistance_scale_q32=scale,output_scale_q32=distance)
            damage=packet["damage_q32"]
        logs.append(dict(frame=frame,kind=source,target=key,damage_q32=damage,hp_after_q32=receiver.hp))
        if flame:
            logs[-1]["source_attack_q32"]=attack

    support_actions={a["frame"]:a for a in request["actions"] if a["operator_id"]==EYJA}
    successful={e["frame"] for e in stage["events"] if e["kind"]=="deployed" and e["operator_id"]==EYJA}
    actor=EyjaAttacks(tables,partner,event,copy(world),rng,
                      states,logs,resistance_scale=lambda key,f:aura_scale(key,f,"COROUTINES")) if partner else None

    def withdraw_support():
        actor.leave()
        if world.incoming:
            world.incoming.withdraw(EYJA)

    frame=start
    first=min([start+1,*successful])
    rng.advance_spawn_offsets(sum(count for f,count in births.items() if f<first))
    for frame in range(first,end+1):
        new_place=repeats.advance(frame,logs) if repeats else None
        if new_place:
            deploy=new_place
            start=frame
            face="Back" if deploy["direction"]=="UP" else "Front"
            cells=range_cells(card["base_range"],deploy,event["rows"],event["cols"])
            sp,_=deploy_skill(tables,card,start)
            gauge=sp.state
            pulse=RecoveryTimer(_raw_f32(gain["interval"]))
            recovery=start
            search=start+_birth(tables,card,evidence,face)["born_exit_update"]+1
            skill_from=skill_until=due=None
            if slot==2:
                cast=run_committed_cast(tables,evidence,horizon=180,facing=face,direction=deploy["direction"],commit_phase="before_coroutines")
                special_delay,=(e["frame"] for e in cast["events"] if e["kind"]=="melee_spell")
            else:
                channel=run_committed_cast(tables,evidence,horizon=round(card["skill_row"]["duration"]/tables.fixed_timestep())+120,facing=face,direction=deploy["direction"])
            logs.append(dict(kind="deployment",frame=start,sp=gauge.points))
        was_present=world.present
        order=support_actions.get(frame,{})
        if actor and frame in successful:
            actor.enter(frame,order)
        if order.get("kind")=="withdraw" and order.get("phase") in ("input","before_coroutines"):
            withdraw_support()
        if world.settle_exits(frame,lambda k:states[k].hp>HP_EPSILON) is not None:
            stop="stage_defeat"
            break
        for key in world.active[frame]:
            states[key].tick(frame)
        world.receive(frame,states,logs)
        on_field=world.present and frame>start
        if actor and actor.place and world.incoming and EYJA not in world.incoming.units:
            actor.leave()
        if not on_field:
            due=None
        def ally_mod(hit,key,atk,res):
            return atk,mul(res,aura_scale(key,frame,"COROUTINES"))
        world.allied_impacts(frame,states,logs,arts_modifier=ally_mod)
        if slot==3 and on_field and skill_from is not None and skill_from<frame<=skill_until:
            if self_damage.advance(sp.step):
                loss=mul(_raw_f32(panel["maxHp"]),_raw_f32(board["hp_ratio"]))
                world.hp=max(0,world.hp-loss)
                if world.incoming:
                    world.incoming.units[IFRIT]["hp"]=world.hp
                logs.append(dict(kind="self_hp_tick",frame=frame,hp_q32=world.hp))
                if world.hp<=HP_EPSILON:
                    world.remove_owner(frame,logs,"operator_defeated")
                    on_field=False
            if on_field and frame in channel_ticks:
                for key,pos in world.positions[frame].items():
                    if (key in world.active[frame] and states[key].hp>HP_EPSILON and world.selectable(key,frame)
                            and not world.stats[key].get("airborne") and circle_in_range(*pos,radii[key],cells,box_query=True)):
                        hit(key,frame,"channel_hit",aura_scale(key,frame,"COROUTINES"))
        enhanced=False
        if on_field and frame==search:
            target=any(states[e["id"]].hp>HP_EPSILON and circle_in_range(e["row"],e["col"],radii[e["id"]],cells,box_query=True)
                       for e in world.snapshot(frame))
            if not target:
                search+=3
            else:
                enhanced=slot==2 and gauge.points>=gauge.cost
                if enhanced:
                    delivery=resolve_special_start(tables,evidence,frame=frame,phase="before_coroutines",source="before_attack",sp=gauge.points)
                    if delivery["status"]!="replacement_registered":
                        raise ValueError("已充能的炎爆未在普通攻击起手时注册")
                    gauge.points=delivery["sp_after"]
                    recovery=frame+cast["first_unblocked_recovery_tick"]
                delay=special_delay if enhanced else ordinary_delay
                due=(frame+delay,enhanced)
                logs.append(dict(kind="attack_start",frame=frame,skill_attack=enhanced,release_frame=frame+delay,sp=gauge.points))
                search+=period

        blocked=frame<recovery
        credited=gauge.advance(sp.step,stopped=blocked) if on_field else 0
        if on_field and pulse.advance(sp.step):
            amount=int(gain["sp"])
            if bonus+".prob" in gain:
                if rng.dice(gain[bonus+".prob"]):
                    amount+=int(gain[bonus+".sp"])
            before=gauge.points
            if not blocked:
                gauge.points=min(gauge.maximum,before+amount)
            logs.append(dict(kind="rhine_circuit",frame=frame,attempt=amount,credited=gauge.points-before,sp=gauge.points))
        if credited:
            logs.append(dict(kind="natural_sp",frame=frame,sp=gauge.points))
        for key,group in tuple(burns.items()):
            if key in world.active[frame] and states[key].hp>HP_EPSILON:
                for _ in range(group.tick(frame,sp.step)):
                    if not on_field:
                        # Ordinary manual withdrawal recycles this deployment before the next object update.
                        logs.append(dict(frame=frame,kind="scorch_burn_skipped",target=key,reason="source_reference_invalid"))
                        continue
                    scale=aura_scale(key,frame,"OBJECTS")
                    hit(key,frame,"burn_tick",scale)
                if group.expirations and states[key].hp>HP_EPSILON:
                    continue
            del burns[key]
            logs.append(dict(frame=frame,kind="burn_finished",target=key))
        withdraw_before_release=withdrawals.get(frame) in ("before_coroutines","input")
        if due and frame==due[0] and not withdraw_before_release:
            source="attack_hit" if due[1] else "ordinary_attack"
            due=None
            targets=[k for k in world.stats if k in world.active[frame] and world.selectable(k,frame) and states[k].hp>HP_EPSILON and
                     circle_in_range(*world.positions[frame][k],radii[k],cells,box_query=True)]
            mitigation={k:aura_scale(k,frame,"COROUTINES") for k in targets}
            logs.append(dict(kind="line_release",frame=frame,source=source,targets=targets))
            for key in targets:
                if source=="attack_hit":
                    if key not in burns:
                        burns[key]=ScorchBurnGroup(burn_duration,_raw_f32(buff["triggerInterval"]))
                        logs.append(dict(kind="defense_delta",target=key,frame=frame,delta=board["def"]))
                    else:
                        logs.append(dict(kind="burn_waiting",target=key,frame=frame,
                                         active_expires=burns[key].expirations[0]))
                    burns[key].attach(frame)
                    states[key].apply_scorch_defense(frame,IFRIT,defense_rule)
                hit(key,frame,source,mitigation[key])
        if frame in withdrawals:
            world.remove_owner(frame,logs,"withdraw")
            on_field=False
            due=None
        if slot==3 and frame in buttons and on_field and frame>=recovery:
            attempt=resolve_cast_start(tables,evidence,frame=frame,phase="after_tick",sp=gauge.points,facing=face,direction=deploy["direction"])
            if attempt["cast_frame"] is not None:
                skill_from=attempt["cast_frame"]
                skill_until=skill_from+channel["end_frame"]
                self_damage=RecoveryTimer(self_period)
                if "owner_hp_q32_at_cast" in request:
                    world.hp=request["owner_hp_q32_at_cast"]
                channel_ticks={skill_from+t for t in channel["channel_pulse_frames"]}
                gauge.points=attempt["sp_after"]
                recovery=skill_from+channel["first_unblocked_recovery_tick"]
                search=skill_until+1
                due=None
                logs.append(dict(kind="channel_start",frame=skill_from,end=skill_until))
        if was_present and not world.present and repeats:
            repeats.leave(frame,IFRIT,manual=frame in withdrawals)
            burns.clear()
        if actor:
            actor.tick(frame)
            if order.get("kind")=="skill" and actor.place:
                actor.press(frame,order)
            if order.get("kind")=="withdraw" and order.get("phase","after_tick")=="after_tick":
                withdraw_support()
        rng.advance_spawn_offsets(births[frame])
        if world.settled(frame,states,logs):
            stop="stage_victory_projected"
            break
    result=dict(stage=stage["code"],operator_id=IFRIT,skill=slot,module=card["module"],module_level=card["module_level"],
                 events=logs,through_frame=frame,stop=stop,boundary=None,sp=gauge.points,
                random_draws=rng.draws,
                remaining_hp_q32={k:v.hp for k,v in states.items()},element_events={k:v.events for k,v in states.items() if v.events},
                selection_cells=cells,ordinary_delay_frames=ordinary_delay,scorch_delay_frames=special_delay,
                active_defense_delta={k:board["def"] for k,state in states.items()
                                      if slot==2 and state.hp>HP_EPSILON and IFRIT in state.scorch_defense})
    world.stage_result(result,fixed_timestep=tables.fixed_timestep(),target_field="target",hp_field="hp_after_q32",projection_horizon=end)
    return dict(result,source=tables.source)


run_ifrit_s2_stage = run_ifrit_stage

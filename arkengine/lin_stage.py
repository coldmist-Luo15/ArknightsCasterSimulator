"""Replay Lin S1 and S2 on the ordinary highland battle clock."""
from fractions import Fraction
from collections import defaultdict

from .animation_assets import read_skin_actions
from .battle_outcome import ordinary_battle_outcome
from .baked_mount import single
from .caster_runtime import default_attack
from .caster_catalog import resolve_caster_loadout,shared_barrier_panel
from .caster_cast import run_committed_cast
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .ebenholz_stage import AlliedEbenholz
from .damage import HP_EPSILON,ordinary_arts_hit
from .fixed import ONE,mul
from .health_bounds import ordinary_life_points
from .lin_carnelian import CarnelianS2
from .lin_equipment import LinDefenseClock,lin_output_stacks
from .lin_incoming import ordinary_ranged,HighlandCrossbows,incoming_by_frame
from .lin_interactions import LIN,EBENHOLZ
from .lin_output_aura import LinStageAura,verify_aura
from .lin_shared_barrier import SharedLinBarriers
from .module_effects import active_board,talent_rows,trait_board
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import PhalanxWave,ResumedGroundWalk,run_with_phalanx_waves
from .phalanx_skills import CARN
from .skill_recovery import deploy_skill
from .skill_assets import lin_hit_talent
from .stage_route_cursor import ControlledRoute
from .stage_channel import UnsupportedChannelScene,prepare_channel
from .volley_damage import _raw_f32


def _mode_timing(tables,card,evidence):
    slot=card["skill"]
    skin=card.get("battle_skin","defaultskin")
    bundle=("chararts" if skin=="defaultskin" else "skinpack")+"/"+LIN+".ab"
    profile=default_attack(tables,LIN,slot)
    attack=profile["fields"]
    parts=evidence["character"]["components"]
    mode,=(r["fields"] for r in parts if r["path"]==f"Modes/S{slot}" and r["script"]=="UnitMode")
    hook,=(r["fields"] for r in parts if r["id"]==mode["_animatorHooker"]["m_PathID"] and r["script"]=="SimpleUnitAnimatorHooker")
    clip_map={item["fromAnimKey"]:item["toAnimKey"] for item in hook["_replaceAnimPairs"]}
    if (profile["ability"]!="MeleeAttack" or not attack["_waitForAttackEvent"] or attack["_timeMode"]!=0 or
            (attack["_selectTargetTiming"],attack["_selectTargetSource"])!=(0,1) or attack["_preDelay"] or
            profile["trigger_parameters"]!=dict(_minTargetNum=1,_overrideSearchTargetTick=-1,_keepTarget=0)):
        raise ValueError("林的技能群攻索敌或等待分支已变化")
    board={v["key"]:v["value"] for v in card["skill_row"]["blackboard"]}
    panel=card["panel_attributes"]
    interval=single(single(panel["baseAttackTime"]+board.get("base_attack_time",0))*100 /(panel["attackSpeed"]+board.get("attack_speed",0)))
    ticks=fixed_wait_frames(Fraction(interval)*30)
    if not profile["base_range"] or profile["selector"]["_postFilter"]!=4 or profile["selector"]["_limitTargetNum"]:
        raise ValueError("林的群攻筛选次序或范围发生变化")
    if slot==1:
        action=clip_map[attack["_animKey"]]
        keys=read_skin_actions(tables,bundle,skin,(action,))
        bake=tables.read(f"bakemuzzledata/character/{LIN}_{skin}.json")["bakedAnimDatas"]
        clocks=set()
        for facing in ("Front","Back"):
            clip=keys["views"][facing.lower()]["actions"][action]
            row=bake[facing]["bakedEvents"][clip["clip"]]
            if clip["loop"] or clip["speed"]!=1 or row["events"]!=["OnAttack"] or attack["_maxAnimScale"]!=1:
                raise ValueError("玲珑的攻击关键帧或播放倍率规则已变化")
            scale=min(1,max(single(.1),single(interval/clip["duration"])))
            clocks.add(baked_key_sample(row["eventTime"][0],single(1/scale),tables.fixed_timestep())[0])
        wait,=clocks
    else:
        if attack["_animKey"] or set(clip_map)!={"Idle"} or attack["_maxAnimScale"]!=-1:
            raise ValueError("荫庇的空攻击动作或待机映射已变化")
        keys=read_skin_actions(tables,bundle,skin,(clip_map["Idle"],))
        if any(not v["actions"][clip_map["Idle"]]["loop"] or v["actions"][clip_map["Idle"]]["events"] for v in keys["views"].values()):
            raise ValueError("荫庇的待机动作可发出攻击事件")
        # With an empty attack animation the attack-event wait times out after the full cooldown.
        wait=ticks
    return board,ticks,wait


def run_lin_stage(tables,request):
    return run_with_phalanx_waves(_run_lin_stage,tables,request)


def _run_lin_stage(tables,request,wave_releases):
    pick,=(p for p in request["squad"] if p["operator_id"]==LIN)
    slot=pick["skill"]
    if slot not in (1,2):
        raise ValueError("此方阵关卡入口需要林的一技能或二技能")
    request=dict(request,actions=[{k:v for k,v in a.items() if k!="kind"} if a.get("kind")=="deploy" else dict(a)
                                  for a in request["actions"]])
    ally_buttons=[a for a in request["actions"] if a["operator_id"]==CARN and a.get("kind")=="skill"]
    eben_buttons=[a for a in request["actions"] if a["operator_id"]==EBENHOLZ and a.get("kind")=="skill"]
    support=[a for a in request["actions"] if a["operator_id"]!=LIN and a not in ally_buttons and a not in eben_buttons]
    if any((slot!=2 and a["operator_id"]!=EBENHOLZ) or a.get("kind","deploy") not in ("deploy","withdraw","move_ally") for a in support):
        raise UnsupportedChannelScene("荫庇需要已接入的同场队友行为")
    moves=defaultdict(list)
    for a in support:
        if a.get("kind")=="move_ally":
            moves[a["frame"]].append(a)
    allies=[a for a in support if a.get("kind","deploy")=="deploy"]
    ally_birth={a["operator_id"]:a["frame"] for a in allies}
    if len(ally_birth)!=len(allies):
        raise UnsupportedChannelScene("同场队友再部署还需要撤退费用和再部署时钟")
    ally_presses={a["frame"] for a in ally_buttons}
    if ally_buttons:
        configs=[p for p in request["squad"] if p["operator_id"]==CARN]
        if not configs or configs[0]["skill"]!=2:
            raise UnsupportedChannelScene("荫庇的攻击型队友目前接通卡涅利安 S2")
        if (CARN not in ally_birth or len(ally_presses)!=len(ally_buttons) or any(
                a!=dict(kind="skill",operator_id=CARN,phase="after_tick",frame=a["frame"]) or
                not ally_birth[CARN]<a["frame"]<=request["end_frame"] for a in ally_buttons)):
            raise ValueError("需要卡涅利安部署后的帧末技能按键")
    leaves=defaultdict(list)
    leaving_holders=set()
    for action in support:
        if action.get("kind")!="withdraw":
            continue
        holder,f,phase=(action[key] for key in ("operator_id","frame","phase"))
        if (set(action)!={"kind","operator_id","frame","phase"} or holder not in ally_birth
                or phase not in ("before_coroutines","after_tick") or type(f) is not int
                or not ally_birth[holder]<f<=request["end_frame"] or holder in leaving_holders):
            raise ValueError("友军撤退需要已部署的持有者和确定的帧内阶段")
        leaves[f,phase].append(holder)
        leaving_holders.add(holder)
    withdrawn=set()
    buttons = sorted((a for a in request["actions"] if a["operator_id"] == LIN and a.get("kind") == "skill"),
                     key=lambda a: a["frame"])
    scheduled = {a["frame"] for a in buttons}
    if len(scheduled) != len(buttons) or any(
            a != dict(kind="skill",operator_id=LIN,frame=a["frame"],phase="after_tick")
            or not 0 < a["frame"] <= request["end_frame"] for a in buttons):
        raise ValueError("需要有效且不重复的林技能帧末按键")
    opening_request=dict(request,actions=[a for a in request["actions"] if a not in buttons[1:] and a not in ally_buttons and a not in eben_buttons
                                          and a.get("kind") not in ("withdraw","move_ally")])
    base={k:v for k,v in opening_request.items() if k!="battle_random_seed"}
    b=prepare_channel(tables,base,operator=LIN,skill=slot,phalanx_default_mode=True,supporting_deploys=allies,
                      allow_no_module=True,allow_idle=True,completing_waves=wave_releases,allow_partial_movement=True)
    card,owner=b.card,b.deploy
    first,end=owner["frame"],request["end_frame"]
    deployed=[a for a in request["actions"] if a is owner or a in allies]
    combat_start=min(a["frame"] for a in deployed)
    cards={LIN:card}
    ally_defenses={}
    ally_resistances={}
    ally_assumptions=[]
    for ally in allies:
        cfg,=(p for p in request["squad"] if p["operator_id"]==ally["operator_id"])
        if cfg["operator_id"]==EBENHOLZ or cfg["operator_id"]==CARN and ally_buttons:
            a=resolve_caster_loadout(tables,cfg["operator_id"],cfg["skill"],cfg["module"],skill_level=cfg["skill_level"],module_level=cfg["module_level"])
        else:
            a=shared_barrier_panel(tables,cfg)
        a["battle_skin"]=b.opening["initial"]["cards"][a["operator_id"]].get("battle_skin","defaultskin")
        cards[a["operator_id"]]=a
        panel=a["panel_attributes"]
        ally_defenses[a["operator_id"]]=_raw_f32(panel["def"])
        ally_resistances[a["operator_id"]]=_raw_f32(panel["magicResistance"])
        if a["branch"]=="phalanx":
            unit=tables.read("excel/character_table.json")[a["operator_id"]]
            trait=trait_board(unit,a["module_phase"] or {},a)
            ally_defenses[a["operator_id"]]=mul(ally_defenses[a["operator_id"]],ONE+_raw_f32(trait["def"]))
            ally_resistances[a["operator_id"]]+=_raw_f32(trait["magic_resistance"])
        if a["operator_id"]!=EBENHOLZ and not (a["operator_id"]==CARN and ally_buttons) and a["branch"]!="phalanx":
            ally_assumptions.append(dict(kind="rule_applied",frame=ally["frame"],target=a["operator_id"],
                                         rule="ally_receives_shield_and_damage_without_own_attack_schedule"))
    ranged=any(e["kind"]=="spawn" and ordinary_ranged(b.scene,e["enemy_key"]) for e in b.opening["events"])
    if scheduled and min(scheduled)<=first:
        raise ValueError("林的技能按键须晚于部署")
    events=[dict(kind="deploy",frame=first),*ally_assumptions]
    gauge,_=deploy_skill(tables,card,first)
    direction=owner["direction"]
    facing="Back" if direction=="UP" else "Front"
    clock=run_committed_cast(tables,b.evidence,horizon=0,facing=facing,direction=direction).get("natural_end") if slot==2 else None
    if slot==2 and (not clock or clock["end_phase"]!="objects"):
        raise UnsupportedChannelScene("荫庇的自然退态时相尚未确定")
    mode=None
    ends=None
    switch=None
    busy_until=first
    recover_at=first+1
    stance=False
    owner_alive=True
    pending_points=gauge.state.points
    board,interval,delay=_mode_timing(tables,card,b.evidence)
    events.extend(dict(kind="ally_deploy",frame=a["frame"],operator=a["operator_id"]) for a in allies)
    wave=PhalanxWave(b,end,allow_ranged=ranged,allow_flying=True,allow_thrower=True,native_first_walk=slot==1 or bool(ally_buttons),
                     allow_later_waves=True,first_sample=combat_start,absolute_route_times=True,allow_partial_movement=True)
    carnelian=CarnelianS2(tables,cards[CARN],next(a for a in allies if a["operator_id"]==CARN),wave,end,events) if ally_buttons else None
    external=incoming_by_frame(request)
    incoming=HighlandCrossbows(tables,wave,deployed,cards,allow_thrower=True,track_stuns=bool(external or moves)) if ranged or external or moves else None
    if moves:
        incoming.moving_holders={a["operator_id"] for group in moves.values() for a in group}
    unit=tables.read("excel/character_table.json")[LIN]
    phase=card["module_phase"] or {}
    shield=active_board(talent_rows(phase,0,"1",unit["talents"][0]["candidates"]),card["elite"],card["level"],card["potential"])
    shared=SharedLinBarriers(tables,card,b.evidence)
    stacks=lin_output_stacks(tables,phase,card)
    aura = None
    if stacks:
        verify_aura(tables,card)
        if card["skill_range"] is not None:
            raise ValueError("林当前技能的通常姿态与技能姿态不再共用射程")
        aura = LinStageAura(tables,wave,owner,card["base_range"])
    panel=card["panel_attributes"]
    atk=mul(_raw_f32(panel["atk"]),ONE+_raw_f32(board.get("atk",0)))
    defense=LinDefenseClock(tables,card)

    def record_defense(frame,phase,reason):
        events.append(dict(kind="lin_defense_update",frame=frame,phase=phase,reason=reason,**defense.stats(stance)))

    record_defense(first,"INPUT","deployment")
    if incoming:
        incoming.events=events
        roll,=lin_hit_talent(tables,card)["rolls"]
        random=tables.combat_random(request.get("battle_random_seed",0))
        births=defaultdict(int)
        for row in wave.births.values():
            births[row["frame"]]+=1
        late=defaultdict(list)
        placements={a["operator_id"]:a for a in [owner,*allies]}
        older=[key for key in incoming.enemies if wave.births[key]["frame"]<first]
        younger=[key for key in incoming.enemies if wave.births[key]["frame"]>=first]
    else:
        older=younger=[]
    carnelian_before_lin=bool(carnelian and (carnelian.start,deployed.index(carnelian.placement))<
                              (first,deployed.index(owner)))
    carnelian_side=older if carnelian_before_lin else younger
    if carnelian:
        before_carnelian=[key for key in carnelian_side if wave.births[key]["frame"]<carnelian.start]
        after_carnelian=[key for key in carnelian_side if wave.births[key]["frame"]>=carnelian.start]
    life=initial_life=b.scene.level["options"]["maxLifePoint"]
    schedule=b.opening["wave_schedule"]
    scheduled_roster={event["instance_id"] for event in schedule["events"] if event["kind"]=="spawn"}
    complete_roster=not schedule["pending"] and scheduled_roster==wave.births.keys()
    deaths={}
    eben=next((AlliedEbenholz(b,request,wave,a,cards[EBENHOLZ],events,deaths) for a in allies
               if a["operator_id"]==EBENHOLZ),None)
    if eben and incoming:
        incoming.attack_ratio=lambda k:eben.world.receivers[k].atk_ratio if k in eben.world.receivers else 0
    escapes=[]
    victory_at=None

    def outcome(frame):
        case=dict(through_frame=frame,exit_coverage=True,exits=escapes,unresolved=[],finishes=[
            dict(instance=key,first_possible_frame=tick,finished_by_frame=tick,scheduler_killed_by_frame=tick)
            for key,tick in deaths.items()])
        bounds=ordinary_life_points(initial_life,[case],frame)
        return ordinary_battle_outcome(b.scene,schedule,[case],bounds,tables.fixed_timestep(),use_stage_objective=True)

    def finish(stop,frame,**extra):
        finished={key:tick for key,tick in deaths.items() if tick<=frame}
        finished.update({e["instance"]:e["frame"] for e in escapes if e["frame"]<=frame})
        log=sorted((e for e in [*events,*wave.applied_rules(frame,finished)] if e["frame"]<=frame),key=lambda e:e["frame"])
        return dict(stage=b.opening["code"],stop=stop,through_frame=frame,remaining_life=life,events=log,
                    deployment=b.placement_event if any(e["kind"]=="deploy" for e in log) else None,
                    **({"holder_recovery_frame":dict(shared.holders)} if shared else {}),
                    battle_random_seed=request.get("battle_random_seed",0),sp=gauge.state.points,defeated=[k for k,birth in wave.births.items()
                        if birth["frame"]<=frame and wave.attrs[k]["hp_q32"]<=HP_EPSILON],
                    spawns=wave.spawns(frame),source=tables.source,battle_outcome=outcome(frame),
                    wave_schedule_pending=schedule["pending"],scheduler_release_frames=finished,
                    **({"carnelian_s2":dict(sp=carnelian.gauge.state.points,hp_q32=carnelian.hp,
                                            active=carnelian.active,present=carnelian.present)} if carnelian else {}),
                    **({"holder_hp_q32":{key:r["hp"] for key,r in incoming.units.items()}} if incoming else {}),**extra)
    search = None
    impact=locked=None
    selected_at=None
    slowed={}
    retimed={}
    slow_ratio=tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"][0]["value"]
    step=_raw_f32(tables.fixed_timestep())
    slow_ticks=(_raw_f32(board.get("attack@sluggish",0))+step-1)//step

    def stack_count(members,uncertain):
        if not stacks:
            return 0
        maximum=stacks["maximum"]
        least=min(maximum,len(members))
        return least

    def arts_packet(enemy,count):
        scale=ONE+_raw_f32(stacks["per_enemy"])*count if stacks else ONE
        return ordinary_arts_hit(attack_raw=atk,hp_raw=enemy["hp_q32"],
                                 resistance_raw=enemy["resistance_q32"],output_scale_raw=scale)

    def record_aura(frame,phase,reason):
        active,uncertain=aura.members()
        events.append(dict(kind="lin_aura_update",frame=frame,phase=phase,reason=reason,
                           members=active,uncertain=uncertain,stacks=stack_count(active,uncertain)))

    def settle_late_shatter(frame,receivers):
        attack=atk if stance else _raw_f32(card["panel_attributes"]["atk"])
        for key in sorted(receivers,key=lambda key:wave.births[key]["order"]):
            state=wave.attrs[key]
            if state["hp_q32"]<=HP_EPSILON or key not in wave.active[frame] or "INVINCIBLE" in wave.flags.at(key,frame):
                continue
            members,uncertain=aura.members() if aura else ([],[])
            count=stack_count(members,uncertain)
            packet=shared.late_damage(state,source_attack=attack,aura_count=count)
            events.append(dict(kind="barrier_shatter_damage",frame=frame,target=key,**packet))
            if packet["hp_after_q32"]<=HP_EPSILON:
                deaths[key]=frame
                if aura and aura.remove(key):
                    record_aura(frame,"OBJECTS","receiver_finished")
                if carnelian and carnelian.aura:
                    carnelian.aura.remove(key)
        return None

    def advance_enemies(frame,keys):
        if not incoming or frame<combat_start:
            return None
        taunt={LIN:board.get("taunt_level",0) if stance else 0}
        if carnelian:
            taunt[CARN]=carnelian.taunt
        incoming.advance(frame,taunt,keys=keys)
        if frame in late:
            scheduled_keys=set(keys)
            return settle_late_shatter(frame,(key for key in late[frame] if key in scheduled_keys))
        return None

    def advance_objects(frame,before_lin):
        if carnelian and before_lin==carnelian_before_lin:
            result=advance_enemies(frame,before_carnelian)
            if result is not None:
                return result
            carnelian.objects(frame)
            if incoming and carnelian.present:
                incoming.units[CARN]["hp"]=carnelian.hp
            return advance_enemies(frame,after_carnelian)
        return advance_enemies(frame,older if before_lin else younger)

    def withdraw_ally(frame,phase):
        for holder in leaves.get((frame,phase),()):
            if incoming and incoming.units[holder]["hp"]<=HP_EPSILON:
                continue
            withdrawn.add(holder)
            held=bool(shared and holder in shared.holders)
            if shared:
                shared.holders.pop(holder,None)
            if holder==EBENHOLZ and eben:
                eben.world.hp=0
            if incoming:
                incoming.withdraw(holder)
            if carnelian and holder==CARN:
                carnelian.leave()
            events.append(dict(kind="ally_withdraw",frame=frame,phase=phase,holder=holder,barrier_removed=held))

    def start_retiming(key,frame):
        if key in retimed or incoming and not incoming.prepare_retiming(key,frame):
            return
        try:
            retimed[key]=ResumedGroundWalk(tables,wave,key,frame,slow_ratio,live_checkpoints=True)
        except UnsupportedChannelScene:
            retimed[key]=ControlledRoute(tables,wave,key,frame,slow_ratio)
        wave.escapes.pop(key,None)
        if incoming:
            incoming.enemies[key]["rerouted"]=True

    def settle_carnelian_attack(frame):
        packets=carnelian.packets(frame)
        charged=carnelian.cast["charged"]
        status="root_seconds" if charged else "sluggish_seconds"
        for key,packet in packets.items():
            wave.attrs[key]["hp_q32"]=packet["hp_after_q32"]
            if packet["hp_after_q32"]<=HP_EPSILON:
                deaths[key]=frame
                if aura and aura.remove(key):
                    record_aura(frame,"COROUTINES","receiver_finished")
                if carnelian.aura:
                    carnelian.aura.remove(key)
            else:
                duration=packet["status_requests"][status]
                slowed[key]=frame,(_raw_f32(duration)+step-1)//step,charged
                if frame<end:
                    start_retiming(key,frame)
        return None

    def settle_lin_attack(f):
        nonlocal search,impact,locked
        members,uncertain=aura.members() if aura else ([],[])
        count=stack_count(members,uncertain)
        recipients=[key for key in locked if key in wave.active[f] and wave.targetable(key,f) and wave.attrs[key]["hp_q32"]>HP_EPSILON]
        if aura and len(recipients)>1 and any(
                key in members and not arts_packet(wave.attrs[key],count).alive_after for key in recipients):
            ordered=wave.attack_order(recipients,selected_at)
            recipients=ordered
        hits={}
        live_members=set(members or ())
        for k in recipients:
            enemy=wave.attrs[k]
            packet=arts_packet(enemy,count)
            hits[k]=dict(damage_q32=packet.damage_raw,hp_after_q32=packet.hp_after_raw)
            if aura:
                hits[k]["aura_stacks"]=count
                if not packet.alive_after and k in live_members:
                    live_members.remove(k)
                    count=stack_count(live_members,uncertain)
            if slot==1:
                hits[k]["sluggish_seconds"]=board["attack@sluggish"]
        events.append(dict(kind="area_arts_damage",frame=f,hits=hits,aura_members=members))
        for k,hit in hits.items():
            wave.attrs[k]["hp_q32"]=hit["hp_after_q32"]
            if hit["hp_after_q32"]<=HP_EPSILON:
                deaths[k]=f
                if aura and aura.remove(k):
                    record_aura(f,"COROUTINES","receiver_finished")
                if carnelian and carnelian.aura:
                    carnelian.aura.remove(k)
            if hit["hp_after_q32"]>HP_EPSILON and slot==1:
                slowed[k]=f,slow_ticks,False
                if f<end:
                    start_retiming(k,f)
        search = f + max(1,interval-delay)
        impact = locked = None
        return None

    begin=min(combat_start,min(wave.escapes.values(),default=combat_start),
              min((row["frame"] for row in wave.births.values()),default=combat_start))
    for f in range(begin,end+1):
        for action in moves.get(f,()):
            holder=action["operator_id"]
            if holder in incoming.units and incoming.units[holder]["hp"]>HP_EPSILON and ally_birth[holder]<=f:
                incoming.move_holder(holder,action["row"],action["col"])
                if holder==EBENHOLZ:
                    eben.world.update_range()
                events.append(dict(kind="ally_moved",frame=f,holder=holder,row=action["row"],col=action["col"]))
        result=advance_objects(f,True)
        if result is not None:
            return result
        if shared and f==first:
            shared.attach(LIN)
        if owner_alive and f>first and defense.advance(stance,gauge.step):
            record_defense(f,"OBJECTS","module_poll")
        if owner_alive and f>=recover_at and f>busy_until and not (slot==2 and stance):
            gauge.state.advance(gauge.step)
        if f==ends:
            events.append(dict(kind="shared_barriers_detached",frame=f,removed=shared.detach()))
            stance=False
            record_defense(f,"OBJECTS","mode_end")
            if aura and aura.detach():
                record_aura(f,"OBJECTS","mode_end")
            mode=ends=search=impact=locked=None
        result=advance_objects(f,False)
        if result is not None:
            return result
        if incoming:
            if f>=combat_start:
                for holder in shared.advance(f):
                    events.append(dict(kind="barrier_restored",frame=f,holder=holder))
                late.pop(f,None)
                defenses=defense.stats(stance)
                carnelian_defenses=carnelian.defenses() if carnelian and carnelian.present else None
                if carnelian_defenses:
                    ally_defenses[CARN]=carnelian_defenses["defense_q32"]
                    ally_resistances[CARN]=carnelian_defenses["resistance_q32"]
                resistances={**ally_resistances,LIN:defenses["resistance_q32"]}
                for source,holder,amount,family,packet in incoming.damage_events(
                        f,{LIN:defenses["defense_q32"],**ally_defenses},resistances,external):
                    hit=shared.receive(f,holder,amount,packet=packet)
                    if hit["breaks"]:
                        victims=shared.victims(placements[holder],len(b.scene.level["mapData"]["map"]),
                            len(b.scene.level["mapData"]["map"][0]),wave.snapshot(f))
                        for target in victims:
                            enemy=b.scene.enemies[wave.births[target]["enemy_key"]]
                            if not enemy["attributes"]["stunImmune"]["m_value"]:
                                incoming.stun(target,f+fixed_wait_frames(shield["stun"]/tables.fixed_timestep())+1)
                        late[f+1].extend(victims)
                        events.append(dict(kind="barrier_shatter",frame=f,holder=holder,source=LIN,targets=victims))
                    receiver=incoming.units[holder]
                    receiver["hp"]=max(0,receiver["hp"]-hit["hp_loss_q32"])
                    if carnelian and holder==CARN:
                        carnelian.hp=receiver["hp"]
                    if receiver["hp"]<=HP_EPSILON:
                        receiver["hp"]=0
                        shared.holders.pop(holder,None)
                    hit_defense=defenses if holder==LIN else carnelian_defenses if holder==CARN and carnelian_defenses else {
                        "defense_q32":ally_defenses[holder]}
                    events.append(dict(kind=family+"_hit",frame=f,source=source,holder=holder,hp_after_q32=receiver["hp"],
                                       **hit,**hit_defense))
                    if carnelian and holder==CARN and not receiver["hp"]:
                        carnelian.leave()
                        carnelian.log("ally_defeated",f)
                    if holder==LIN:
                        success=not packet.get("ignore_for_sp",False) and random.dice(roll["probability"])
                        points=gauge.state.points
                        if f>=recover_at and f>busy_until and not (slot==2 and stance):
                            if success:
                                gauge.state.points=min(gauge.state.maximum,points+roll["sp"])
                        events.append(dict(kind="lin_hit_sp",frame=f,success=success,sp_before=points,sp_after=gauge.state.points))
                        if not receiver["hp"]:
                            owner_alive=False
                            events.append(dict(kind="operator_defeated",frame=f,operator_id=LIN))
                            events[:]=[event for event in events if event["frame"]<=f or
                                       event["kind"] not in ("skill_cast","mode_switch")]
                            shared.detach()
                            if aura and aura.detach():
                                record_aura(f,"OBJECTS","source_defeated")
                            stance=False
                            switch=mode=ends=search=impact=locked=None
        for key,motion in tuple(retimed.items()):
            if wave.attrs[key]["hp_q32"]<=0:
                del retimed[key]
                continue
            origin,ticks,rooted=slowed[key]
            if incoming and incoming.holding(key,f):
                continue
            try:
                controlled=f<=origin+ticks
                walking=motion.advance(wave,f,controlled and not rooted,paused=controlled and rooted)
            except UnsupportedChannelScene:
                # advance may already have reached the previous waypoint on this tick.
                retimed[key]=ControlledRoute(tables,wave,key,f,slow_ratio,prior=motion)
                wave.positions[f][key]=motion.position
                wave.active[f].add(key)
                walking=True
            for kind,row in wave.history.get(f,()):
                if row["instance_id"]==key and kind in ("checkpoint_begin","checkpoint_reached"):
                    events.append(dict(kind="enemy_checkpoint",frame=f,event=kind,enemy=key,
                                       command=row["command"],position=row["position"]))
            if not walking:
                wave.escapes[key]=f
                del retimed[key]
        if incoming and f<end:
            for key,(marked,ticks,_) in slowed.items():
                if f>=marked+ticks or key not in wave.active[f] or wave.attrs[key]["hp_q32"]<=0:
                    continue
                start_retiming(key,f)
        for key,exit_frame in wave.escapes.items():
            if exit_frame==f and wave.attrs[key]["hp_q32"] > 0:
                loss=b.scene.enemies[wave.births[key]["enemy_key"]]["lifePointReduce"]["m_value"]
                life=max(0,life-loss)
                escapes.append(dict(instance=key,frame=f,scheduler_release_window=[f,f],
                    outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
                events.append(dict(kind="enemy_escaped",frame=f,enemy=key,remaining_life=life))
                if aura and aura.remove(key):
                    record_aura(f,"OBJECTS","receiver_finished")
                if carnelian and carnelian.aura:
                    carnelian.aura.remove(key)
                if life<=0:
                    events[:]=[e for e in events if e["frame"]!=f or e["kind"]=="enemy_escaped"]
                    return finish("stage_defeated",f)
        if incoming:
            random.advance_spawn_offsets(births[f])
        if leaves:
            withdraw_ally(f,"before_coroutines")
        if slot==2 and mode is not None and f>=mode+2:
            contacts=[a["operator_id"] for a in allies
                      if a["frame"]<=f and a["operator_id"] not in shared.holders and (a["row"],a["col"]) in wave.cells
                      and a["operator_id"] not in withdrawn
                      and (not incoming or incoming.units[a["operator_id"]]["hp"]>HP_EPSILON)]
            for holder in contacts:
                shared.attach(holder)
            if f==mode+2:
                shared.attach(LIN)
                events.append(dict(kind="barrier_holders_snapshot",frame=f,holders=[LIN,*contacts],threshold=shield["value"],
                                   recovery_seconds=shield["interval"],atk_speed_bonus=board["attack_speed"],taunt_delta=board["taunt_level"]))
            elif contacts:
                events.append(dict(kind="barrier_holder_enter",frame=f,phase="COROUTINES",holders=contacts))
        if eben:
            eben.step(f,incoming)
        if search==f:
            selected=wave.search(f)
            if selected:
                locked=selected
                selected_at=f
                impact=f+delay
                events.append(dict(kind="attack_start",frame=f,targets=selected,interval_ticks=interval))
                search=None
            else:
                search+=3
        ally_hits_now=carnelian is not None and f==carnelian.hit
        ally_first=ally_hits_now and (f!=impact or
            (carnelian.attack_start,carnelian.start,deployed.index(carnelian.placement)) <
            (selected_at,first,deployed.index(owner)))
        if ally_first:
            result=settle_carnelian_attack(f)
            if result is not None:
                return result
        if f==impact:
            result=settle_lin_attack(f)
            if result is not None:
                return result
        if ally_hits_now and not ally_first:
            result=settle_carnelian_attack(f)
            if result is not None:
                return result
        if aura and owner_alive and f>=first and aura.advance(f):
            record_aura(f,"COROUTINES","physics_callbacks")
        if carnelian:
            carnelian.resume(f)
        if leaves:
            withdraw_ally(f,"after_tick")
        if f in ally_presses:
            carnelian.press(f)
            if incoming and carnelian.present:
                incoming.units[CARN]["hp"]=carnelian.hp
        for k,(origin,ticks,rooted) in slowed.items():
            if f in (origin+1,origin+ticks,origin+ticks+1) and k in wave.active[f] and wave.attrs[k]["hp_q32"]>HP_EPSILON:
                fields={"movement_suppressed" if rooted else "sluggish_active":f<=origin+ticks}
                events.append(dict(kind="root_motion" if rooted else "sluggish_motion",frame=f,enemy=k,
                                   position=wave.positions[f][k],**fields))
        if complete_roster and len(deaths)+len(escapes)==len(scheduled_roster):
            complete_roster=False
            projection=outcome(end)
            window=projection.get("finish_game_projection",{})
            if projection["status"]=="victory_projected" and window.get("within_combat_horizon"):
                victory_at=window["frames"][1]
        if f==victory_at:
            return finish("stage_victory_projected",f)
        if switch is not None and f==switch[0]:
            _,stance=switch
            switch=None
            mode=f if stance else None
            search=f+1 if stance else None
            if slot==1:
                events.append(dict(kind="skill_cast",frame=f,sp=gauge.state.points))
                events.append(dict(kind="mode_switch",frame=f,phase="UNITY_FIXED_RESUME",mode=slot if stance else 0))
            record_defense(f,"UNITY_FIXED_RESUME","mode_switch")
            if aura and aura.detach():
                record_aura(f,"UNITY_FIXED_RESUME","mode_switch")
        if f not in scheduled:
            continue
        points=gauge.state.points
        if not owner_alive:
            cast=dict(status="operator_defeated")
        elif f<busy_until:
            cast=dict(status="toggle_animation_in_progress" if slot==1 else "skill_busy")
            points=pending_points
        elif slot==1:
            cast=resolve_special_start(tables,b.evidence,frame=f,phase="after_tick",sp=points,
                direction=direction,facing=facing,toggled=stance)
        elif stance:
            cast=dict(status="skill_busy")
        else:
            cast=resolve_cast_start(tables,b.evidence,frame=f,phase="after_tick",sp=points,
                direction=direction,facing=facing)
        events.append(dict(kind="skill_button",frame=f,sp=points,outcome=cast["status"]))
        spell=cast.get("spell_frame")
        if spell is None:
            continue
        busy_until=spell
        pending_points=points
        gauge.state.points=cast["sp_after"]
        next_stance=cast.get("toggled_after",True)
        switch=spell,next_stance
        if slot==2:
            if spell!=cast["cast_frame"]+clock["spell_frame"]:
                raise UnsupportedChannelScene("荫庇的施法与退态时钟不一致")
            ends=cast["cast_frame"]+clock["end"]
            recover_at=cast["cast_frame"]+clock["first_recovery_tick"]
            events.extend([dict(kind="skill_cast",frame=cast["cast_frame"],sp=cast["sp_after"]),
                           dict(kind="mode_switch",frame=spell,mode=slot if next_stance else 0)])
        if impact is not None and impact>f:
            events.append(dict(kind="attack_interrupted_by_skill_input",frame=f,scheduled_impact=impact))
        search=impact=locked=None
    return finish("horizon",end)

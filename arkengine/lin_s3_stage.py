"""V079 Lin S3 direct attacks, incoming projectiles and shield refreshes."""
from collections import Counter,defaultdict

from .attack_range import circle_in_range,range_cells
from .battle_outcome import ordinary_battle_outcome
from .caster_cast import run_committed_cast
from .caster_start import resolve_cast_start
from .caster_catalog import resolve_caster_loadout
from .ebenholz_stage import AlliedEbenholz
from .damage import HP_EPSILON
from .health_bounds import ordinary_life_points
from .lin_active import lin_s3_impact,lin_s3_profile
from .lin_equipment import LinDefenseClock
from .lin_incoming import ordinary_ranged,HighlandCrossbows,incoming_by_frame
from .lin_interactions import LIN,EBENHOLZ
from .lin_output_aura import LinStageAura,verify_aura
from .lin_shared_barrier import SharedLinBarriers
from .phalanx_wave import PhalanxWave,run_with_phalanx_waves
from .skill_assets import lin_hit_talent
from .skill_recovery import deploy_skill
from .stage_channel import UnsupportedChannelScene,prepare_channel
from .volley_damage import _raw_f32


def run_lin_s3_stage(tables,request):
    return run_with_phalanx_waves(_run_lin_s3_stage,tables,request)


def _run_lin_s3_stage(tables,request,wave_releases):
    actions=[{k:v for k,v in a.items() if k!="kind"} if a.get("kind")=="deploy" else a for a in request["actions"]]
    deploy,*buttons=[a for a in actions if a["operator_id"]==LIN]
    allies=[a for a in actions if a["operator_id"]==EBENHOLZ and a.get("kind","deploy")=="deploy"]
    placements=[a for a in actions if a is deploy or a in allies]
    combat_start=min(a["frame"] for a in placements)
    first,end=deploy["frame"],request["end_frame"]
    presses={a["frame"]:a for a in buttons}
    if (len(presses)!=len(buttons) or list(presses)!=sorted(presses) or
            any(a!=dict(kind="skill",operator_id=LIN,frame=f,phase="after_tick") or not first<f<=end
                for f,a in presses.items())):
        raise ValueError("流光乍裂需要部署后的帧末技能按键")
    opening=dict(request,actions=[a for a in actions if a in placements or a in buttons[:1]])
    opening.pop("battle_random_seed",None)
    battle=prepare_channel(tables,opening,operator=LIN,skill=3,phalanx_default_mode=True,allow_no_module=True,allow_idle=True,
                           supporting_deploys=allies,completing_waves=wave_releases,allow_partial_movement=True)
    card,evidence=battle.card,battle.evidence
    profile=lin_s3_profile(tables,card)
    if profile["status"]!="lin_s3_profile":
        raise UnsupportedChannelScene(profile["reason"])
    schedule=battle.opening["wave_schedule"]
    ranged=any(e["kind"]=="spawn" and ordinary_ranged(battle.scene,e["enemy_key"]) for e in battle.opening["events"])
    rng=tables.combat_random(request.get("battle_random_seed",0))
    wave=PhalanxWave(battle,end,allow_ranged=ranged,allow_flying=True,allow_thrower=True,
                     first_sample=combat_start,allow_later_waves=True,absolute_route_times=True,allow_partial_movement=True)
    cards={LIN:card}
    if allies:
        cfg=next(p for p in request["squad"] if p["operator_id"]==EBENHOLZ)
        cards[EBENHOLZ]=resolve_caster_loadout(tables,EBENHOLZ,3,cfg["module"],module_level=cfg["module_level"],skill_level=cfg["skill_level"])
        cards[EBENHOLZ]["battle_skin"]=battle.opening["initial"]["cards"][EBENHOLZ].get("battle_skin","defaultskin")
    incoming=HighlandCrossbows(tables,wave,placements,cards,allow_thrower=True,track_stuns=True)
    gauge,_=deploy_skill(tables,card,first)
    events=[dict(kind="deploy",frame=first,sp=gauge.state.points)]
    if incoming:
        incoming.events=events
        older=[key for key in incoming.enemies if wave.births[key]["frame"]<first]
        younger=[key for key in incoming.enemies if wave.births[key]["frame"]>=first]
    external=incoming_by_frame(request)
    births=Counter(b["frame"] for b in wave.births.values())
    shared=SharedLinBarriers(tables,card,evidence)
    shared.attach(LIN)
    roll,=lin_hit_talent(tables,card)["rolls"]
    defense=LinDefenseClock(tables,card)
    dt=tables.fixed_timestep()
    face="Back" if deploy["direction"]=="UP" else "Front"
    clock=run_committed_cast(tables,evidence,horizon=0,facing=face,direction=deploy["direction"])["natural_end"]
    if clock["end_phase"]!="objects":
        raise ValueError("流光乍裂的自然退态时相已变化")
    grid=battle.scene.level["mapData"]["map"]
    normal_cells=range_cells(card["base_range"],deploy,len(grid),len(grid[0]))
    skill_cells=wave.cells
    wave.cells=normal_cells
    recovery=first+1
    cast_at=mode=ends=search=impact=None
    holding=False
    locked=[]
    selected_at=None
    refreshes={}
    disable_attached=None
    late=defaultdict(list)
    initial_life=life=battle.scene.level["options"]["maxLifePoint"]
    hp=_raw_f32(card["panel_attributes"]["maxHp"])
    base_attack=_raw_f32(card["panel_attributes"]["atk"])
    stacks=profile["output_stacks"]
    aura=None
    if stacks:
        verify_aura(tables,card)
        aura=LinStageAura(tables,wave,deploy,card["base_range"])
    deaths={}
    eben=AlliedEbenholz(battle,request,wave,allies[0],cards[EBENHOLZ],events,deaths) if allies else None
    if eben:
        events.append(dict(kind="ally_deploy",frame=allies[0]["frame"],operator_id=EBENHOLZ))
        incoming.attack_ratio=lambda k:eben.world.receivers[k].atk_ratio if k in eben.world.receivers else 0
    escapes=[]
    scheduled_roster={e["instance_id"] for e in schedule["events"] if e["kind"]=="spawn"}
    complete_roster=not schedule["pending"] and scheduled_roster==wave.births.keys()
    victory_at=None

    def outcome(frame):
        case=dict(through_frame=frame,exit_coverage=True,exits=escapes,unresolved=[],
                  finishes=[dict(instance=k,first_possible_frame=f,finished_by_frame=f,scheduler_killed_by_frame=f)
                            for k,f in deaths.items()])
        bounds=ordinary_life_points(initial_life,[case],frame)
        return ordinary_battle_outcome(battle.scene,schedule,[case],bounds,dt,use_stage_objective=True)

    def finish(frame, stop, **extra):
        releases={k:f for k,f in deaths.items() if f<=frame}
        releases.update({e["instance"]:e["frame"] for e in escapes if e["frame"]<=frame})
        return dict(stage=battle.opening["code"],operator_id=LIN,skill=3,source=tables.source,through_frame=frame,
                    events=sorted((e for e in [*events,*wave.applied_rules(frame,releases)] if e["frame"]<=frame),key=lambda e:e["frame"]),
                    battle_random_seed=request.get("battle_random_seed",0),stop=stop,sp=gauge.state.points,
                    owner_hp_q32=hp,remaining_life=life,shield_recovery_frame=shared.holders.get(LIN),
                    enemy_hp_q32={k:e["hp_q32"] for k,e in wave.attrs.items() if wave.births[k]["frame"]<=frame},
                    spawns=wave.spawns(frame),battle_outcome=outcome(frame),
                    wave_schedule_pending=schedule["pending"],scheduler_release_frames=releases,**extra)

    def aura_count():
        if not aura:
            return 0
        members,uncertain=aura.members()
        lower=min(len(members),stacks["maximum"])
        return lower

    def record_aura(frame,phase,reason):
        members,uncertain=aura.members()
        events.append(dict(kind="lin_aura_update",frame=frame,phase=phase,reason=reason,
                           members=members,uncertain=uncertain,stacks=aura_count()))

    def record_defense(frame,phase,reason):
        events.append(dict(kind="lin_defense_update",frame=frame,phase=phase,reason=reason,**defense.stats(holding)))

    def enemy_finished(key,frame,phase):
        if aura and aura.remove(key):
            record_aura(frame,phase,"receiver_finished")

    record_defense(first,"INPUT","deployment")

    def leave_mode(frame, **fields):
        nonlocal holding,cast_at,mode,ends,search,impact
        if holding:
            events.append(dict(kind="skill_mode_ended",frame=frame,**fields))
            holding=False
            phase="AFTER_TICK" if fields.get("phase")=="after_tick" else "OBJECTS"
            record_defense(frame,phase,fields.get("reason","mode_end"))
            if aura and aura.detach():
                record_aura(frame,phase,"mode_end")
            if aura:
                aura.set_range(card["base_range"])
        holding=False
        cast_at=mode=ends=search=impact=None
        wave.cells=normal_cells

    def shatter(frame,reason):
        refresh=reason in ("kill_refresh","kill_refresh_on_defeat")
        cells=skill_cells if holding or refresh else normal_cells
        targets=(wave.search(frame,cells=cells) if reason=="kill_refresh" else
                 [k for k,e in wave.snapshot(frame).items()
                  if circle_in_range(e["row"],e["col"],e["radius"],cells,box_query=True)])
        for key in targets:
            # The kill mark retains its S3 selector. The late-enable callback
            # runs when the selected receiver next updates, using live source ATK.
            receiver_already_updated=reason!="kill_refresh" or wave.births[key]["frame"]<first
            damage_frame=frame+int(receiver_already_updated)
            late[damage_frame].append(key)
            attrs=wave.scene.enemies[wave.births[key]["enemy_key"]]["attributes"]
            if attrs["stunImmune"]["m_value"]:
                continue
            ticks=profile["stun_object_ticks"]
            incoming.stun(key,damage_frame+ticks)
        events.append(dict(kind="barrier_shatter",frame=frame,reason=reason,targets=targets))

    def settle_late_shatter(frame,receivers):
        source=profile["direct_attack_q32"] if holding else base_attack
        for key in sorted(receivers,key=lambda key:wave.births[key]["order"]):
            if wave.attrs[key]["hp_q32"]<=HP_EPSILON or key not in wave.active[frame] or "INVINCIBLE" in wave.flags.at(key,frame):
                continue
            count=aura_count()
            packet=shared.late_damage(wave.attrs[key],source_attack=source,aura_count=count)
            events.append(dict(kind="barrier_shatter_damage",frame=frame,target=key,**packet))
            if packet["hp_after_q32"]<=HP_EPSILON:
                deaths[key]=frame
                enemy_finished(key,frame,"OBJECTS")
        return None

    begin=min(combat_start,min(wave.escapes.values(),default=first),min(births,default=first))
    for frame in range(begin,end+1):
        if incoming and frame>=combat_start:
            incoming.advance(frame,{},keys=older)
        if frame in late:
            result=settle_late_shatter(frame,(key for key in late[frame] if wave.births[key]["frame"]<first))
            if result is not None:
                return result
        if hp>HP_EPSILON and frame>first and defense.advance(holding,gauge.step):
            record_defense(frame,"OBJECTS","module_poll")
        if hp>HP_EPSILON and frame>=recovery:
            gauge.state.advance(gauge.step)
        if frame==cast_at:
            gauge.state.points-=gauge.state.cost
            events.append(dict(kind="skill_commit",frame=frame,sp=gauge.state.points))
        if frame==ends:
            leave_mode(frame)
        for holder in shared.advance(frame):
            events.append(dict(kind="barrier_restored",frame=frame,holder=holder,reason="timer"))
        if frame in refreshes:
            del refreshes[frame]
            if shared.holders[LIN] is None:
                shatter(frame,"kill_refresh")
            else:
                shared.holders[LIN]=None
                events.append(dict(kind="barrier_restored",frame=frame,holder=LIN,reason="kill_refresh"))
        if incoming and frame>=combat_start:
            incoming.advance(frame,{},keys=younger)
        for key,exit_frame in wave.escapes.items():
            if exit_frame==frame and wave.attrs[key]["hp_q32"]>HP_EPSILON:
                loss=wave.scene.enemies[wave.births[key]["enemy_key"]]["lifePointReduce"]["m_value"]
                life=max(0,life-loss)
                escapes.append(dict(instance=key,frame=frame,scheduler_release_window=[frame,frame],
                                    outcomes=["reached_exit"],life_loss_min=loss,life_loss_max=loss))
                events.append(dict(kind="enemy_escaped",frame=frame,enemy=key,remaining_life=max(0,life)))
                enemy_finished(key,frame,"OBJECTS")
                if life<=0:
                    return finish(frame,"stage_defeat")
        if frame in late:
            result=settle_late_shatter(frame,(key for key in late.pop(frame) if wave.births[key]["frame"]>=first))
            if result is not None:
                return result
        pending_break=False
        if incoming and frame>=combat_start:
            stats=defense.stats(holding)
            defenses={LIN:stats["defense_q32"]}
            resistances={LIN:stats["resistance_q32"]}
            if eben:
                panel=cards[EBENHOLZ]["panel_attributes"]
                defenses[EBENHOLZ]=_raw_f32(panel["def"])
                resistances[EBENHOLZ]=_raw_f32(panel["magicResistance"])
            for source,holder,amount,family,packet in incoming.damage_events(frame,defenses,resistances,external):
                if holder==EBENHOLZ:
                    unit=incoming.units[holder]
                    unit["hp"]=max(0,unit["hp"]-amount)
                    events.append(dict(kind=family+"_hit",frame=frame,holder=holder,source=source,hp_after_q32=unit["hp"]))
                    if unit["hp"]<=HP_EPSILON:
                        events.append(dict(kind="operator_defeated",frame=frame,operator_id=holder))
                    continue
                if holding:
                    hit=shared.receive(frame,holder,amount,threshold_q32=profile["barrier_threshold_q32"],packet=packet,defer_break=True)
                    pending_break|=hit["breaks"]
                else:
                    hit=shared.receive(frame,holder,amount,packet=packet)
                    if hit["breaks"]:
                        disable_attached=(frame,False)
                        shatter(frame,"incoming_hit")
                hp=max(0,hp-hit["hp_loss_q32"])
                incoming.units[LIN]["hp"]=hp
                events.append(dict(kind=family+"_hit",frame=frame,source=source,holder=holder,hp_after_q32=hp,**hit,**stats))
                if hp<=HP_EPSILON:
                    for attached in refreshes.values():
                        # The default-priority buffs clear in attachment order, before the selector detaches.
                        if attached<frame and (shared.holders[LIN] is None or disable_attached<(attached,True)):
                            shatter(frame,"kill_refresh_on_defeat")
                    refreshes.clear()
                    pending_break=False
                    events.append(dict(kind="operator_defeated",frame=frame,operator_id=LIN))
                    leave_mode(frame,phase="objects",reason="operator_defeated")
                    if aura and aura.detach():
                        record_aura(frame,"OBJECTS","source_defeated")
                    shared.holders.pop(LIN,None)
                    continue
                proc=not packet.get("ignore_for_sp",False) and rng.dice(roll["probability"])
                before=gauge.state.points
                if proc and frame>=recovery:
                    gauge.state.points=min(gauge.state.maximum,before+roll["sp"])
                events.append(dict(kind="lin_hit_sp",frame=frame,success=proc,sp_before=before,sp_after=gauge.state.points))
        if eben:
            eben.step(frame,incoming)
        if frame==search:
            locked=wave.search(frame)
            if locked:
                selected_at=frame
                impact=frame+profile["ticks_to_event"]
                search=frame+profile["interval_frames"]
                events.append(dict(kind="attack_start",frame=frame,targets=locked))
            else:
                search=frame+3
        rng.advance_spawn_offsets(births[frame])
        if frame==impact:
            receivers={k:wave.attrs[k] for k in locked if k in wave.active[frame] and wave.targetable(k,frame) and wave.attrs[k]["hp_q32"]>HP_EPSILON}
            count=aura_count()
            hit=lin_s3_impact(profile,receivers,aura_count=count or 0)
            if aura and receivers:
                members,uncertain=aura.members()
                if len(receivers)>1 and any(k in members and v["killed"] for k,v in hit["hits"].items()):
                    order=wave.attack_order(receivers,selected_at)
                    for index,key in enumerate(order):
                        packet=lin_s3_impact(profile,{key:receivers[key]},aura_count=count)["hits"][key]
                        hit["hits"][key]=dict(packet,aura_stacks=count)
                        if packet["killed"] and key in members:
                            members.remove(key)
                            count=min(len(members),stacks["maximum"])
                    hit["hits"]={key:hit["hits"][key] for key in order}
                else:
                    for packet in hit["hits"].values():
                        packet["aura_stacks"]=count
            events.append(dict(kind="area_arts_damage",frame=frame,hits=hit["hits"]))
            for key,packet in hit["hits"].items():
                wave.attrs[key]["hp_q32"]=packet["hp_after_q32"]
                if packet["killed"]:
                    deaths[key]=frame
                    enemy_finished(key,frame,"COROUTINES")
            if hit["kill_refresh"]:
                # The direct hit starts a separate ability; its spell attaches the mark next frame.
                attached=frame+1
                refreshes[attached+profile["kill_refresh"]["minimum_object_ticks_to_expiry"]]=attached
        if pending_break:
            shatter(frame,"incoming_hit")
            disable_attached=(frame,True)
            shared.holders[LIN]=frame+shared.cooldown
        if aura and frame>=first and hp>HP_EPSILON and aura.advance(frame):
            record_aura(frame,"COROUTINES","physics_callbacks")
        if frame==mode:
            holding=True
            wave.cells=skill_cells
            search=frame+1
            events.append(dict(kind="skill_mode",frame=frame,threshold_q32=profile["barrier_threshold_q32"]))
            record_defense(frame,"UNITY_FIXED_RESUME","mode_switch")
            if aura and aura.detach():
                record_aura(frame,"UNITY_FIXED_RESUME","mode_switch")
            if aura:
                aura.set_range(card["skill_range"])
        if complete_roster and len(deaths)+len(escapes)==len(scheduled_roster):
            complete_roster=False
            projected=outcome(end)
            window=projected.get("finish_game_projection",{})
            if projected["status"]=="victory_projected" and window.get("within_combat_horizon"):
                victory_at=window["frames"][1]
        if frame==victory_at:
            return finish(frame,"stage_victory_projected")
        if frame in presses:
            if hp<=HP_EPSILON:
                events.append(dict(kind="skill_button",frame=frame,sp=gauge.state.points,outcome="operator_defeated"))
                continue
            if cast_at is not None and cast_at<=frame<ends:
                events.append(dict(kind="skill_button",frame=frame,sp=gauge.state.points,outcome="stopped"))
                leave_mode(frame,phase="after_tick",reason="manual_stop")
                # The next Unit tick still sees the block. BasicSkill clears it later that tick.
                recovery=frame+2
                continue
            cast=resolve_cast_start(tables,evidence,frame=frame,phase="after_tick",sp=gauge.state.points,
                                    facing=face,direction=deploy["direction"]) if frame>=recovery-1 else dict(status="skill_active")
            events.append(dict(kind="skill_button",frame=frame,sp=gauge.state.points,outcome=cast["status"]))
            if cast.get("spell_frame") is not None:
                cast_at,mode=cast["cast_frame"],cast["spell_frame"]
                ends,recovery=cast_at+clock["end"],cast_at+clock["first_recovery_tick"]
    return finish(end,"horizon")

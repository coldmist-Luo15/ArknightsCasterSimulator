"""Veen's charged volleys, marked receivers and bouncing shots on an ordinary native wave."""
from collections import Counter

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_ammo import CasterAmmoCounter
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_drone_chain_mystic import VEEN, _veen
from .caster_equipment_traits import _trait_values
from .caster_first_spell import _selected_clip,attack_key_clock
from .caster_opening import deploy_to_first_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .chain_targets import projectile_hatred_tie
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import stationary_cast_mounts
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .module_effects import active_board, talent_rows, trait_board
from .mystic_timing import fixed_wait_frames
from .mode_replay import _AttackCooldown
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import READY_EPSILON,RecoveryTimer,deploy_skill
from .trace_motion import ordinary_trace_step
from .veen_bounce_battle import _bounce_pool
from .veen_storage import StorageToggle,storage_modifiers
from .volley_damage import _raw_f32


_MOUNTS={5:"specialPoint0",6:"specialPoint1",8:"specialPoint2",9:"specialPoint3",10:"specialPoint4"}


def _abilities(ev,slot):
    comp=ev["character"]["components"]
    def at(path,cls):
        row,=(c["fields"] for c in comp if c["path"]==path and c["script"]==cls)
        return row
    mode=f"S{slot}" if slot else "Default"
    root=f"Modes/{mode}/Body/Attack"
    ordinary=at(root if slot==3 else root+"/AttackBase","MultiChargeRangedAttack")
    merged=ordinary if slot==3 else at(root+"/AttackWithMergeBall","MultiChargeRangedAttack")
    charge=at(f"Trait/ChargeableGroup/ChargeAttack{mode}","MergeableChargeRangeAttack")
    if (any(not a["_waitForAttackEvent"] or a["_animKey"]!="Attack" or a["_timeMode"]!=0 or a["_preDelay"]
            or a["_maxAnimScale"]!=1 or not a["_useChargeableGroup"] or a["_useMountGroup"] for a in (ordinary,merged))
            or bool(charge["_onlyAssignChargeCntToBaseAbility"])!=(slot==3)):
        raise ValueError("维伊的储能释放、攻击事件或弹射传参变化")
    return ordinary,merged,charge


def run_veen_stage(tables,request):
    deploy,button,*additional=request["actions"]
    prior=button["frame"]
    for entry in additional:
        frame=entry["frame"]
        if (type(frame) is not int or entry!=dict(kind="skill",operator_id=VEEN,frame=frame,phase="after_tick")
                or not prior<frame<=request["end_frame"]):
            raise ValueError("维伊后续操作需要按帧顺序输入")
        prior=frame
    other_presses={e["frame"] for e in additional}
    request=dict(request,actions=[deploy,button])
    card,stage,place,scene,world=prepare_ranged_stage(tables,request,operator=VEEN,manual=True,portable=True,
                                              track_exits=True,simulate_before_button=True)
    slot=card["skill"]
    if slot not in (2,3):
        raise ValueError("此入口支持维伊二、三技能")
    start,press,end=place["frame"],scene["press_frame"],scene["end_frame"]
    view=scene["view"]
    ev=caster_prefab_evidence(tables,VEEN,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    ordinary,merged,storage=_abilities(ev,slot)
    default=_abilities(ev,0)
    unit=tables.read("excel/character_table.json")[VEEN]
    panel=card["panel_attributes"]
    phase=card["module_phase"] or {}
    trait=trait_board(unit,phase,card)
    talents=[active_board(talent_rows(phase,i,str(i+1),t["candidates"]),card["elite"],card["level"],card["potential"])
             for i,t in enumerate(unit["talents"][:2])]
    board={q["key"]:q["value"] for q in card["skill_row"]["blackboard"]}
    args=(board,panel,trait,talents)
    dt=tables.fixed_timestep()
    step=_raw_f32(dt)
    base,aspd=panel["baseAttackTime"],panel["attackSpeed"]
    cap=int(trait["times"])
    merge=int(trait["merge_cnt"])
    if (cap,merge)!=(storage["_maxChargeTimes"],storage["_mergeSize"]):
        raise ValueError("储能上限或转置所需数量变化")
    speed_with_storage=_trait_values(card).get("attack_speed",0)
    stored_toggle,merged_toggle=StorageToggle(1),StorageToggle(merge)
    stored_toggle.attach_checker()
    merged_toggle.attach_checker()
    def interval(stacks,active):
        modifiers=storage_modifiers(stored_toggle.active,merged_toggle.active,talents[0],speed_with_storage)
        speed=aspd+modifiers["attack_speed"]
        period=base
        if active:
            period+=board.get("base_attack_time",0)
            speed+=board.get("attack_speed",0)+stacks*board.get("attack@veen_s_2_buff[stack].attack_speed",0)
        return single(single(period)*100/single(speed))
    log=[dict(kind="deployed",frame=start)]
    hp={k:_raw_f32(e["hp"]) for k,e in world.stats.items()}
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    cells=range_cells(card["skill_range"] or card["base_range"],place,scene["rows"],scene["cols"])
    base_cells=range_cells(card["base_range"],place,scene["rows"],scene["cols"])
    initial_ammo=int(board["attack@trigger_time"]) if slot==3 else None
    ammo=initial_ammo
    energy=stacks=0
    skill_clock={}
    gauge,_=deploy_skill(tables,card,start)
    sp=gauge.state
    recovery=start+1
    next_cast=start+_birth(tables,card,ev,view)["born_exit_update"]+1
    def result(frame,status,**extra):
        events=sorted((event for event in log if event["frame"]<=frame),key=lambda event:event["frame"])
        replay=dict(stage=stage["code"],stage_id=request["stage_id"],deployment=place,operator_id=VEEN,skill=slot,
            module=card["module"],through_frame=frame,status=status,stop=status,events=events,remaining_hp_q32=hp,
            energy=energy,sp=sp.points,s2_stacks=stacks if slot==2 else None,ammo=ammo,source=tables.source,**skill_clock,**extra)
        return world.stage_result(replay,fixed_timestep=dt,target_field="target",hp_field="hp_q32")
    opening=deploy_to_first_cast(tables,card,ev,deploy_frame=start,direction=place["direction"],facing=view,end_frame=end,
        manual_phase=scene["press_phase"],next_manual_input=lambda earliest,born:press)
    if not opening.cast or opening.cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("按键未让维伊进入技能姿态")
    pending_cast=opening.cast
    mode=pending_cast["spell_frame"]
    log.extend(e for e in pending_cast["events"] if e["frame"]<=end)
    restore=None
    mode_finish,=ev["templates"]["switch_mode_restart_fsm"]["eventToActions"]["ON_BUFF_FINISH"]
    if not mode_finish["_restoreDefault"] or not mode_finish["_restartFSM"]:
        raise ValueError("维伊退态后不再重启常态动作")
    if slot==2:
        duration=card["skill_row"]["duration"]
        clock=run_committed_cast(tables,ev,horizon=fixed_wait_frames(single(duration/dt))+8,facing=view,
            direction=place["direction"],effective_attack_interval=interval(stacks,True),watched_buff_survives=True)
        commit=opening.cast["cast_frame"]
        skill_clock=dict(skill_end_frame=commit+clock["end_frame"],
                         first_unblocked_recovery_tick=commit+clock["first_unblocked_recovery_tick"])
        restore=skill_clock["skill_end_frame"]
    settings={}
    keys={ordinary["_projectileKey"],merged["_projectileKey"],*(a["_projectileKey"] for a in default),default[2]["_mergeProjectileKey"]}
    if slot==2:
        keys.update((storage["_projectileKey"],storage["_mergeProjectileKey"]))
    for key in keys:
        parts={p["script"]:p["fields"] for p in ev["projectiles"][key]["logic"]["components"]}
        bouncing="BounceProjectile" in parts
        mover=parts["BouncedAdvancedMovement" if bouncing else "AdvancedMovement"]
        life=parts["BounceProjectile" if bouncing else "SimpleProjectile"]
        if mover["_moveType"]!=1 or mover["_delayToStart"] or life["_randomDelayToBorn"] or life["_managedBySource"]:
            raise ValueError("维伊普通追踪弹的首段计时发生变化")
        if bouncing and (parts["BounceHitBehaviour"]["_useMapPosIfTargetInvalid"]
                        or mover["_forceResetToTargetPosWhenReached"] or mover["_comeBack"]):
            raise ValueError("维伊弹射在挂点失效后使用的坐标分支发生变化")
        settings[key]=dict(speed=mover["_speed"],bounce_speed=mover.get("_speedAfterFirstReach"),bouncing=bouncing,
                           radius=parts.get("CircleCollider2D",{}).get("m_Radius"))
    launches={}
    projectiles=[]
    marks={}
    deaths={}
    recycle_wait=fixed_wait_frames(single(1./dt))
    receiver_order=sorted(hp,key=lambda k:(world.births[k]["frame"],world.births[k]["order"]))
    ammo_end=None
    attack_end=None
    counter=None
    attack_cooldown=None
    animation_restart_after=start
    face_sign=1 if place["direction"]!="LEFT" else -1
    animation_lengths={}
    if slot==3:
        controller,=(c["fields"] for c in ev["skill"]["components"] if c["script"]=="CastSkillWithLimitTimes" and not c["path"])
        if ordinary["_waitForProjectileInvalid"] or not controller["_canDiscardRemainingCount"]:
            raise ValueError("维伊三技能末发消耗或残留弹体的能力选项发生变化")
    def damage(frame,key,attack,kind,*,receiver,**details):
        packet=ordinary_arts_hit(attack_raw=attack,resistance_raw=_raw_f32(world.stats[key]["res"]),hp_raw=hp[key])
        hp[key]=packet.hp_after_raw
        if slot==3 and hp[key]<=HP_EPSILON:
            deaths[key]=(frame,receiver)
        log.append(dict(kind=kind,frame=frame,target=key,damage_q32=packet.damage_raw,hp_q32=hp[key],**details))

    def press_after_update(frame):
        nonlocal ammo,ammo_end,press,mode,restore,counter,attack_end,recovery,pending_cast
        if frame not in other_presses:
            return
        if pending_cast is not None and frame<mode or recovery is None or frame<recovery-1:
            if slot==3 and counter is not None and counter.stop_frame is None and not counter.discarded and counter.remaining:
                counter.discard(frame)
                log.append(counter.events[-1])
                ammo=0
                ammo_end=frame
            else:
                log.append(dict(kind="skill_button_rejected",frame=frame,reason="skill_active",sp=sp.points))
            return
        attempt=resolve_cast_start(tables,ev,frame=frame,phase="after_tick",sp=sp.points,facing=view,direction=place["direction"])
        if attempt["status"]!="cast_and_active_buff_spell":
            log.append(dict(kind="skill_button_rejected",frame=frame,reason=attempt["status"],sp=sp.points))
            return
        press,mode=frame,attempt["spell_frame"]
        pending_cast=attempt
        restore=None if slot==3 else attempt["cast_frame"]+clock["end_frame"]
        if slot==2:
            skill_clock.update(skill_end_frame=restore,
                               first_unblocked_recovery_tick=attempt["cast_frame"]+clock["first_unblocked_recovery_tick"])
        ammo_end,counter,attack_end=None,None,None
        log.extend(attempt["events"])

    for frame in range(start,end+1):
        if world.settle_exits(frame,lambda k:hp[k]>HP_EPSILON) is not None:
            return result(frame,"stage_defeat")
        receiver_index=0
        def tick_marks_before(allocation_frame):
            nonlocal receiver_index
            # BuffContainer ticks on its receiver. INPUT allocates the caster
            # before same-frame COROUTINES wave spawns. Each receiver ticks once.
            while receiver_index<len(receiver_order):
                key=receiver_order[receiver_index]
                if world.births[key]["frame"]>=allocation_frame:
                    break
                receiver_index+=1
                if key not in marks:
                    continue
                mark=marks[key]
                receiver=world.receiver_at(key,frame)
                if receiver is None:
                    del marks[key]
                    continue
                if hp[key]>HP_EPSILON and mark["timer"].advance(step):
                    damage(frame,key,_raw_f32(talents[1]["attack@value"]*mark["layers"]),"warfare_dot",
                           receiver=receiver,layers=mark["layers"])
                mark["life"]-=step
                if mark["life"]<=0 or hp[key]<=HP_EPSILON:
                    del marks[key]
        tick_marks_before(start)
        if frame>start:
            sp.advance(gauge.step,stopped=recovery is None or frame<recovery)
            for toggle in (stored_toggle,merged_toggle):
                toggle.observe_charge(energy)
                toggle.tick_checker()
        if pending_cast is not None and frame==pending_cast["cast_frame"]:
            sp.points=pending_cast["sp_after"]
            recovery=None if slot==3 else skill_clock["first_unblocked_recovery_tick"]
            pending_cast=None
        if frame==press+1:
            launches.clear()
        if frame==mode:
            skill_clock["stored_at_mode_entry"]=energy
            if slot==3:
                counter=CasterAmmoCounter(ev,stored_on_attach=energy)
                if not counter.multi or counter.count_event!="spell_on":
                    raise ValueError("维伊的多蓄能弹药计数发生变化")
                ammo=counter.remaining
            next_cast=frame+1
            attack_cooldown=None
        if counter is not None and counter.ready_to_reset and counter.stop_frame is None and counter.owner_tick(frame):
            log.append(counter.events[-1])
            skill_clock["ammo_stop_request_frame"]=frame
            # The controller marks the mode buff after the owner's BuffContainer
            # update. Its ON_BUFF_FINISH runs at the next owner update.
            restore=frame+1
        if frame==restore:
            launches.clear()
            log.append(dict(kind="restore_default_mode",frame=frame))
            skill_clock["skill_end_frame"]=frame
            if slot==3:
                recovery=frame+1
                skill_clock["first_unblocked_recovery_tick"]=recovery
            stacks=0
            next_cast=frame+1
            attack_cooldown=None
        active=mode<frame and (restore is None or frame<restore)
        if attack_cooldown is not None and frame<=next_cast:
            current_period=_raw_f32(interval(stacks,active))
            if current_period!=attack_cooldown.period:
                attack_cooldown.change_period(frame-1,step,current_period)
                ticks=max(1,(attack_cooldown.remaining-READY_EPSILON+step-1)//step)
                next_cast=max(animation_restart_after,frame-1+ticks)
        if next_cast==frame and (frame<=press or frame>mode) and (not active or ammo_end is None):
            area=cells if active else base_cells
            rows=[e for e in world.snapshot(frame) if hp[e["id"]]>HP_EPSILON and e["id"] not in marks
                  and circle_in_range(e["row"],e["col"],radii[e["id"]],area,box_query=True)]
            target=world.select(rows,frame) if rows else None
            if target is None and energy>=cap:
                next_cast+=3
            else:
                period=interval(stacks,active)
                action=(f"Skill_{slot}_" if active else "")+("Attack" if target else "Charge")
                offset,clock=attack_key_clock(tables,ev,view,action,period)
                if target is not None and target["col"]!=place["col"]:
                    face_sign=1 if target["col"]>place["col"] else -1
                launches[frame+offset]=(target,clock,frame,period,face_sign)
                attack_cooldown=_AttackCooldown(frame,_raw_f32(period))
                if action not in animation_lengths:
                    clip,_=_selected_clip(tables,ev,action,view)
                    animation_lengths[action]=clip["duration"]
                animation_restart_after=frame+fixed_wait_frames(single(min(period,animation_lengths[action])/dt))
                log.append(dict(kind=("attack_start" if active else "ordinary_attack_start") if target else "charge_start",frame=frame,
                    **(dict(target=target["id"]) if target else {}),energy=energy,period_seconds=period,action=action))
                next_cast+=fixed_wait_frames(single(period/dt))
        for shot in projectiles[:]:
            key=shot["target"]
            tick_marks_before(shot["release"])
            tied=[k for k in marks if world.births[k]["frame"]==shot["release"]]
            if tied:
                return result(frame-1,"same_coroutine_allocation_order",unresolved=dict(frame=frame,targets=tied))
            alive=hp[key]>HP_EPSILON
            if not alive and not shot["bouncing"]:
                projectiles.remove(shot)
                continue
            if shot["delay"]>single(1e-5):
                shot["delay"]=single(shot["delay"]-dt)
                continue
            if alive:
                enemy=world.by_id(key,frame,projectile_born=shot["release"])
            else:
                death_frame,enemy=deaths[key]
                # Ordinary damage and mark ticks kill in OBJECTS. The pool's
                # last waiting tick recycles the receiver in COROUTINES.
                if frame-death_frame>=recycle_wait:
                    enemy=None
            if not shot["bouncing"] and enemy is None:
                projectiles.remove(shot)
                log.append(dict(kind="projectile_target_left_map",frame=frame,target=key,release=shot["release"]))
                continue
            if enemy is not None:
                dx,dy,z=shot["hit_offset"]
                shot["last_mount"]=(enemy["col"]+dx,enemy["row"]+dy,z)
            target_pos=shot["last_mount"]
            move=ordinary_trace_step(shot["position"],target_pos,effective_dt=dt,round_position=True,speed=shot["speed"])
            shot["position"]=move.position
            if not move.reached:
                continue
            idx=sum(shot["hits"].values())
            power=mul(shot["attack"],_raw_f32(board["attack@bounce_atk_scale" if idx else "attack@base_atk_scale"])) if shot["bouncing"] else shot["attack"]
            if alive and enemy is not None:
                kind="bounce_hit" if idx else "projectile_hit" if shot["skill"] else "ordinary_projectile_hit"
                damage(frame,key,power,kind,receiver=enemy,release=shot["release"],
                       projectile_kind=shot["kind"],bounce_index=idx,position=[enemy["row"],enemy["col"]])
            else:
                log.append(dict(kind="reached_invalid_target_mount" if enemy is None else "reached_retained_dead_target",
                                frame=frame,target=key,release=shot["release"],bounce_index=idx))
            shot["hits"][key]+=1
            if enemy is not None and hp[key]>HP_EPSILON:
                if world.births[key]["frame"]==shot["release"]:
                    return result(frame,"same_coroutine_allocation_order",unresolved=dict(target=key))
                mark=marks.setdefault(key,dict(layers=0,life=0,timer=RecoveryTimer(ONE)))
                mark["layers"]=min(int(talents[1]["attack@max_stack_cnt"]),mark["layers"]+shot["layers"])
                mark["life"]=_raw_f32(talents[1]["attack@duration"])
                log.append(dict(kind="warfare_mark",frame=frame,target=key,layers=mark["layers"]))
            if shot["bouncing"] and shot["remaining"]:
                rows={r["id"]:dict(r,radius=radii[r["id"]]) for r in world.snapshot(frame,projectile_born=shot["release"])}
                center=enemy if enemy is not None else dict(id=key,row=move.position[1],col=move.position[0])
                next_key,why=_bounce_pool(rows,hp,center,shot["hits"],shot["radius"],frame=frame,
                          nearest_tie=lambda keys:projectile_hatred_tie(world,keys,frame,shot["release"]))
                if why:return result(frame,why,unresolved=dict(target=key))
                if next_key:
                    receiver=rows[next_key]
                    probe=dict(receiver,col=place["col"]+1)
                    _,mount=stationary_cast_mounts(tables,VEEN,shot["clock"],scene["placement"],probe,view,
                                                     mount=10,field=_MOUNTS[10])
                    displacement=(mount[0]-probe["col"],mount[1]-probe["row"],mount[2])
                    shot.update(target=next_key,remaining=shot["remaining"]-1,delay=single(board["attack@delay"]),
                        position=target_pos if enemy is not None else move.position,speed=shot["bounce_speed"],
                        hit_offset=displacement,last_mount=(receiver["col"]+displacement[0],receiver["row"]+displacement[1],displacement[2]))
                    log.append(dict(kind="bounce_lock",frame=frame,target=next_key,remaining=shot["remaining"]))
                    continue
            projectiles.remove(shot)
        tick_marks_before(frame)
        if counter is not None and frame==attack_end:
            counter.ability_event(frame,"cast_end")
            if counter.ready_to_reset and counter.stop_frame is None and not counter.discarded:
                log.append(counter.events[-1])
        if frame not in launches:
            press_after_update(frame)
            continue
        target,clock,attack_start,period,release_sign=launches.pop(frame)
        if target is None:
            energy=min(cap,energy+1)
            if counter is not None and active:
                counter.ability_event(frame,"charge_spell")
            log.append(dict(kind="stored",frame=frame,energy=energy,merged=energy//merge))
            press_after_update(frame)
            continue
        key=target["id"]
        if hp[key]<=HP_EPSILON or key not in world.active[frame]:
            log.append(dict(kind="attack_target_invalid_at_release",frame=frame,target=key))
            next_cast=frame+1
            attack_cooldown=None
            press_after_update(frame)
            continue
        live_receiver=world.receiver_at(key,frame)
        normal_attack,merged_attack,charge_attack=(ordinary,merged,storage) if active else default
        shot_slot=slot if active else 0
        main=merged_attack if energy>=merge else normal_attack
        if shot_slot==3 and counter.discarded:
            # The linked main ability remains attached until the mode buff is
            # finished. The already-started spell uses the stored charge count.
            bounce_count=energy
        else:
            volley=_veen(shot_slot,"volley",dict(energy_units=energy,ammo=ammo,targets=[dict(id=key,has_veen_dot=False)]),*args)
            bounce_count=volley.get("bounce_count",0)
        merged_count,normal_count=energy//merge,energy%merge
        packed=charge_attack["_mountPointForProjectiles"]
        if shot_slot!=3:
            parts=[("merged",charge_attack["_mergeProjectileKey"],m) for m in packed[:merged_count]]
            parts.append(("primary",main["_projectileKey"],main["_mountPointType"]))
            parts.extend(("normal",charge_attack["_projectileKey"],m) for m in packed[merged_count:merged_count+normal_count])
        else:
            parts=[("primary",main["_projectileKey"],main["_mountPointType"])]
            counter.ability_event(frame,"spell_on")
            ammo=counter.remaining
            if not counter.discarded and ammo!=volley["ammo"]:
                raise ValueError("维伊的已预约弹药与主攻击实际消耗不一致")
            tail=_after_spell_clock(frame-attack_start,period,step,same_frame_resume=True)
            attack_end=attack_start+tail["end"]
            if not ammo:
                ammo_end=frame
        for kind,pkey,mount in parts:
            effect=_veen(shot_slot,"projectile_release",dict(projectile_kind=kind,s2_stacks=stacks,
                         has_merged_attack_buff=merged_toggle.active),*args)
            stacks=effect.get("s2_stacks",stacks)
            origin,receiver=stationary_cast_mounts(tables,VEEN,clock,scene["placement"],target,view,
                                                   mount=mount,field=_MOUNTS[mount],caster_face_sign=release_sign)
            hit_offset=(receiver[0]-target["col"],receiver[1]-target["row"],receiver[2])
            last_mount=(live_receiver["col"]+hit_offset[0],live_receiver["row"]+hit_offset[1],hit_offset[2])
            projectiles.append(dict(target=key,release=frame,clock=clock,position=origin,kind=kind,
                hit_offset=hit_offset,last_mount=last_mount,delay=0.,
                remaining=bounce_count,hits=Counter(),layers=effect["dot_stacks_on_hit"],
                attack=effect["direct_attack_raw"],skill=active,**settings[pkey]))
            log.append(dict(kind="projectile_release",frame=frame,target=key,projectile=pkey,mount=mount,projectile_kind=kind,
                cached_atk_q32=effect["cached_atk_raw"],stacks_after=stacks,ammo_after=ammo,energy_before=energy,skill_attack=active))
        energy=0
        press_after_update(frame)
    return result(end,"horizon")

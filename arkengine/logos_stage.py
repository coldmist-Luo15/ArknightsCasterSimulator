"""Logos S2 lock, talent projectiles and changing receiver speed on native ground waves."""
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_flight import projectile_reach
from .caster_start import resolve_cast_start
from .marcil_deploy import _birth
from collections import Counter
from .caster_runtime import default_attack
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .enemy_elements import EnemyElements
from .enemy_speed import movement_cap,scaled_move_attribute
from .fixed import ONE,mul
from .logos_lock import LOGOS,lock_arts,lock_definition,talent_launch
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import RecoveryTimer, deploy_skill
from .sluggish_wave import SluggishWave
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step, paracurve_horizontal_step
from .volley_damage import _raw_f32


def run_logos_s2_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    card,stage,deploy,event,world=prepare_continuous_ranged(tables,request,LOGOS,releases,native_walk=False)
    rule=lock_definition(tables,card)
    spec=rule.evidence
    start,end=event["frame"],event["end_frame"]
    presses={a["frame"]:a for a in request["actions"] if a.get("kind")=="skill"}
    retreats={a["frame"] for a in request["actions"] if a.get("kind")=="withdraw"}
    args=dict(direction=deploy["direction"],facing=event["view"])
    timeline=run_committed_cast(tables,spec,horizon=0,**args)["natural_end"]
    through=end
    born=start+_birth(tables,card,spec,event["view"])["born_exit_update"]
    search=born+1
    control_changes={c["frame"]:c["state"] for c in request.get("caster_control",())}
    recovery=start+1
    entered=until=None
    active=False
    pending_cast=None
    cells=range_cells(card["base_range"],deploy,event["rows"],event["cols"])
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    states={k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    walks=SluggishWave(tables,world,continue_route=True)
    atk=_raw_f32(card["panel_attributes"]["atk"])
    stream=tables.combat_random(request.get("battle_random_seed",0))
    spawn_draws=Counter(b["frame"] for b in world.births.values())
    stream.advance_spawn_offsets(sum(n for f,n in spawn_draws.items() if f<=start))
    talent=rule.talent
    maximum=int(rule.board["attack@max_stack_cnt"])
    stacks=0
    lock=None
    due=None
    in_attack=False
    controlled=False
    focus=RecoveryTimer(rule.focus_poll_q32)
    projectiles=[]
    ordinary_due=None
    ordinary_index=0
    default=default_attack(tables,LOGOS)["fields"]
    emitter,=(c["fields"] for c in spec["character"]["components"] if c["script"]=="UberAnimationEffectEmitter" and
              c["path"]=="Modes/Default/Body/Attack")
    actions=[r["anim"] for r in emitter["_replaceGroup"]]
    if emitter["_sourceAnimKey"]!="Attack" or emitter["_loopType"]!=0 or actions!=["Attack","Attack_2"]:
        raise ValueError("逻各斯的常态普攻动作轮转发生变化")
    normal_parts={c["script"]:c["fields"] for c in spec["projectiles"][default["_projectileKey"]]["logic"]["components"]}
    move=normal_parts["AdvancedMovement"]
    life=normal_parts["SimpleProjectile"]
    if (move["_moveType"]!=1 or move["_delayToStart"] or move["_delayAfterReached"] or move["_useDynamicSpeed"] or
            life["_managedBySource"] or life["_randomDelayToBorn"]):
        raise ValueError("逻各斯普通追踪弹的移动选项发生变化")
    panel=card["panel_attributes"]
    normal_interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]))
    normal_period=fixed_wait_frames(single(normal_interval/rule.timestep))
    gauge,_=deploy_skill(tables,card,start)
    grant=int(_trait_values(card).get("sp",0))
    if grant:
        branch,=spec["templates"]["v035_common_addsp"]["eventToActions"]["ON_CALCULATE_DAMAGE"]
        gain,=branch["_succeedNodes"]
        if branch["_conditionNode"]["_targetLevelMask"]!="ELITE_AND_BOSS" or gain["_forceFlag"] or gain["_spString"]!="sp":
            raise ValueError("逻各斯 Y 模组的受击者等级判定或阻回选项发生变化")
    face_sign=-1 if deploy["direction"]=="LEFT" else 1
    slowed_until={}
    logs=[dict(frame=start,kind="deployment",sp=gauge.state.points)] if world.present else []

    def report(frame,stop,**boundary):
        result=dict(stage=stage["code"],operator_id=LOGOS,skill=2,module=card["module"],through_frame=frame,
                    stop=stop,boundary=boundary or None,events=[e for e in logs if e["frame"]<=frame],
                    remaining_hp_q32={k:s.hp for k,s in states.items()},mode_start=entered,mode_end=until,
                    first_unblocked_recovery_tick=recovery,sp=gauge.state.points,
                    element_events={k:s.events for k,s in states.items() if s.events},
                    random_draws=stream.draws if stream else None)
        world.stage_result(result,fixed_timestep=rule.timestep,target_field="target",hp_field="hp_after_q32")
        return dict(result,source=tables.source)

    def living(key):
        return states[key].hp>HP_EPSILON

    def in_range(key,pos):
        return circle_in_range(*pos,radii[key],cells,box_query=True)

    def damage(key,frame,scale,kind):
        state=states[key]
        res=_raw_f32(world.stats[key]["res"])
        base=mul(atk,scale)
        amount=lock_arts(state,frame,card,base,res,kind)
        if kind=="talent_impact" and state.burst=="DARK" and living(key) and "element_atk_scale" in talent:
            state.apply_elemental_hp(frame,mul(atk,_raw_f32(talent["element_atk_scale"])),
                                     source="logos_delta_talent",source_card=card)
        logs.append(dict(kind=kind,frame=frame,target=key,position=world.positions[frame][key],
                         scale_q32=scale,arts_damage_q32=amount,hp_after_q32=state.hp))
        if grant and world.present and world.stats[key]["level"] in ("ELITE","BOSS"):
            before=gauge.state.points
            if frame>=recovery:
                gauge.state.points=min(gauge.state.maximum,before+grant)
            logs.append(dict(kind="module_sp",frame=frame,target=key,requested=grant,
                             granted=gauge.state.points-before,blocked=frame<recovery,source=kind,sp=gauge.state.points))

    def launch_talent(frame,key):
        triggered=stream.dice(talent["prob"])
        logs.append(dict(kind="first_talent_dice",frame=frame,triggered=triggered))
        if not triggered:
            return None
        receivers=native_box_candidates(world,(k for k in world.active[frame] if living(k) and world.selectable(k,frame) and in_range(k,world.positions[frame][k])))
        for _ in range(int(talent.get("emit_count",1)) if receivers else 0):
            other,=stream.select_targets(receivers,1)
            row,col=world.positions[frame][other]
            enemy=world.receiver_at(other,frame)
            draw=dict(mount=stream.range_int(0,6),birth_delay_seconds=single(stream.value()*rule.projectile["_randomDelayToBorn"]))
            identity,origin,mount,first,_=talent_launch(tables,card,spec,dict(event,enemy=enemy),frame,draw,caster_face_sign=face_sign)
            motion=projectile_reach(tables,spec,dict(frame=first,projectile=identity,origin=origin,target_mounts=[mount],round_position=True))
            if motion.get("movement")!="ParacurveMovement" or "speed" not in motion:
                raise ValueError("词句论断弹体移动参数已变化")
            projectiles.append(dict(first=first,target=other,position=origin,speed=motion["speed"],
                                    offset=(mount[0]-col,mount[1]-row),ordinary=False))
            logs.append(dict(kind="talent_projectile",frame=frame,target=other,candidates=receivers,mount=draw["mount"],
                             birth_delay_seconds=draw["birth_delay_seconds"],born_frame=first-1,first_move_frame=first))
        return None

    routes={}

    def project(key,frame):
        if key not in routes and frame<end:
            routes[key]=world.start_walk(tables,key,frame,walks.modifier)

    def advance_route(key,frame,walk):
        # Attributes._CalculateAttributeValue multiplies the per-provider final factors at 0x269c30c.
        scale=ONE+stacks*_raw_f32(rule.board["attack@move_speed"]) if key==lock else ONE
        slowed=mul(scale,ONE+_raw_f32(walks.modifier))
        scale=slowed if frame<=slowed_until.get(key,-1) else scale
        cap=movement_cap(scaled_move_attribute(walk.route["base_move_speed"],scale),walk.route["level_move_multiplier"])
        return world.step_walk(tables,walk,key,frame,walks.modifier,cap=cap)

    def clear_lock(frame,reason,*,restore_mode=False):
        nonlocal lock,stacks
        old_lock,old_stacks=lock,stacks
        lock,stacks=None,0
        anchor=frame
        if restore_mode and old_lock is not None and world.births[old_lock]["frame"]>=start:
            anchor-=1
        if old_stacks and living(old_lock) and old_lock in world.active[anchor]:
            logs.append(dict(kind="lock_cleared",frame=frame,target=old_lock,stacks=old_stacks,reason=reason))
            project(old_lock,anchor)

    for frame in range(start+1,through+1):
        stream.advance_spawn_offsets(spawn_draws[frame])
        if frame in retreats:
            world.remove_owner(frame,logs,"withdraw")
        world.receive(frame,states,logs,res_bonus=rule.board.get("magic_resistance",0) if active else 0)
        for key,walk in tuple(routes.items()):
            updated=advance_route(key,frame,walk) if living(key) else None
            if updated is None:
                del routes[key]
            else:
                routes[key]=updated
        if world.settle_exits(frame,living) is not None:
            return report(world.defeated_at,"stage_defeat")
        if not world.present:
            if lock is not None:
                clear_lock(frame,"owner_removed")
            due=ordinary_due=pending_cast=search=None
            active=False
        if frame>=recovery and world.present:
            gauge.state.advance(gauge.step)
        if pending_cast and frame==pending_cast["cast_frame"]:
            gauge.state.points=pending_cast["sp_after"]
        if pending_cast and frame==pending_cast["spell_frame"]:
            active=True
            due=ordinary_due=None
            search=frame+1
            pending_cast=None
            focus=RecoveryTimer(rule.focus_poll_q32)
        if frame==until and active:
            active=False
            if due is not None:
                logs.append(dict(kind="lock_attack_interrupted",frame=frame,target=due[1],hit_frame=due[0]))
            due,in_attack=None,False
            clear_lock(frame,"skill_mode_end",restore_mode=True)
            logs.append(dict(kind="restore_default_mode",frame=frame))
            search=None if controlled else frame+1
        for key,state in states.items():
            if living(key) and (key in world.active[frame] or state.last_tick is not None):
                state=states[key]
                before=state.hp
                state.tick(frame)
                if before!=state.hp:
                    logs.append(dict(kind="element_tick",frame=frame,target=key,hp_after_q32=state.hp))
        world.allied_impacts(frame,states,logs)
        if (control:=control_changes.get(frame)) is not None:
            logs.append(dict(kind="caster_control",frame=frame,state=control))
            controlled=control!="clear"
            if not controlled:
                # This object update exits the abnormal state; Idle searches on the next tick.
                search=frame+1
                face_sign=-1 if deploy["direction"]=="LEFT" else 1
            else:
                if due is not None:
                    logs.append(dict(kind="lock_attack_interrupted",frame=frame,target=due[1],hit_frame=due[0]))
                due,search,in_attack=None,None,False
                clear_lock(frame,control)
        if frame==search:
            visible=[e for e in world.snapshot(frame) if living(e["id"]) and in_range(e["id"],(e["row"],e["col"]))]
            current=next((e for e in visible if e["id"]==lock),None)
            target=current or (world.select(visible,frame) if len(visible)>1 else next(iter(visible),None))
            in_attack=target is not None
            if target is None:
                search+=3
            else:
                key=target["id"]
                if target["col"]!=deploy["col"]:
                    face_sign=1 if target["col"]>deploy["col"] else -1
                if active:
                    due=frame+rule.wait-1,key
                    logs.append(dict(kind="lock_attack_start",frame=frame,target=key,position=[target["row"],target["col"]]))
                    search+=rule.period
                else:
                    shot=ranged_shot_release(tables,spec,default,attack_frame=frame,attack_interval=normal_interval,
                                             facing=event["view"],mode_path=None)
                    action=actions[ordinary_index%len(actions)]
                    ordinary_index+=1
                    shot["clock"]["action"]=action
                    ordinary_due=dict(shot,target=key,sign=face_sign)
                    logs.append(dict(kind="ordinary_attack_start",frame=frame,target=key,action=action))
                    search+=normal_period
        if active and frame>entered and not controlled and focus.advance(int(rule.timestep*ONE)) and not in_attack and lock is not None:
            clear_lock(frame,"attack_state_exit")

        # Talent bullets are ordinary objects created after their receiver.
        for bullet in tuple(projectiles):
            if bullet["first"]>frame:
                continue
            key=bullet["target"]
            if not living(key) or key not in world.active[frame]:
                # This trace-only projectile carries the original TargetPtr. Its
                # hit controller has no alternate target or area callback.
                logs.append(dict(kind="ordinary_target_lost" if bullet["ordinary"] else "talent_target_lost",frame=frame,target=key))
                projectiles.remove(bullet)
                continue
            row,col=world.positions[frame][key]
            goal=col+bullet["offset"][0],row+bullet["offset"][1]
            if bullet["ordinary"]:
                shifted=ordinary_trace_step(bullet["position"],(*goal,bullet["offset"][2]),effective_dt=rule.timestep,
                                            speed=bullet["speed"],round_position=True)
                bullet["position"],reached=shifted.position,shifted.reached
            else:
                bullet["position"],reached=paracurve_horizontal_step(bullet["position"],goal,bullet["speed"],rule.timestep,True)
            if reached:
                damage(key,frame,ONE if bullet["ordinary"] else _raw_f32(talent["atk_scale"]),
                       "ordinary_impact" if bullet["ordinary"] else "talent_impact")
                projectiles.remove(bullet)
                if not bullet["ordinary"] and living(key):
                    ticks=fixed_wait_frames(single(single(talent["sluggish"])/rule.timestep))
                    slowed_until[key]=frame+ticks
                    project(key,frame)
        if due and frame==due[0] and (not living(due[1]) or due[1] not in world.active[frame]):
            # AbilityStandard.DoCastOnTargets checks the retained entity pointer's
            # alive bit before OnCastOnTarget, including its talent dice callback.
            logs.append(dict(kind="lock_hit_skipped",frame=frame,target=due[1]))
            due=None
        if due and frame==due[0]:
            key=due[1]
            due=None
            if lock!=key:
                if lock is not None:
                    clear_lock(frame,"target_changed")
                lock=key
            damage(key,frame,rule.base_scale+stacks*rule.scale_step,"lock_hit")
            stacks=min(maximum,stacks+1)
            if living(key):
                project(key,frame)
            else:
                lock,stacks=None,0
            launch_talent(frame,key)
        if ordinary_due and frame==ordinary_due["release_frame"]:
            shot,ordinary_due=ordinary_due,None
            key=shot["target"]
            if living(key) and key in world.active[frame]:
                receiver=world.receiver_at(key,frame)
                origin,mount=stationary_cast_mounts(tables,LOGOS,shot["clock"],event["placement"],receiver,event["view"],
                                                   caster_face_sign=shot["sign"])
                offset=(mount[0]-receiver["col"],mount[1]-receiver["row"],mount[2])
                projectiles.append(dict(first=frame+1,target=key,position=origin,speed=move["_speed"],offset=offset,ordinary=True))
                logs.append(dict(kind="ordinary_projectile_release",frame=frame,target=key,action=shot["clock"]["action"]))
                launch_talent(frame,key)
        if frame in presses and world.present and not controlled and not active and pending_cast is None:
            cast=resolve_cast_start(tables,spec,frame=frame,phase=presses[frame].get("phase","after_tick"),
                sp=gauge.state.points,owner_state="born" if frame<born else "attack" if ordinary_due else "idle",**args)
            logs.extend(cast["events"])
            if cast["cast_frame"] is not None:
                entered=cast["spell_frame"]
                until=entered+fixed_wait_frames(single(single(rule.duration)/rule.timestep))
                recovery=cast["cast_frame"]+timeline["first_recovery_tick"]
                pending_cast=cast
                if cast["cast_frame"]==frame:
                    gauge.state.points=cast["sp_after"]
                search=ordinary_due=None
        if world.settled(frame,states,logs):
            return report(frame,"stage_victory_projected")
    return report(through,"horizon")

"""Mantra's S2 mode and concurrent homing chains against moving ground-wave receivers."""
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .chain_targets import nearest_chain_target,projectile_hatred_tie
from .damage import HP_EPSILON
from .elemental_casters import MANTRA
from .elemental_projectiles import stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE
from .mantra_battle import apply_mantra_hit,burst_output_scale,mantra_bounce_profile
from .mantra_interrupt import MantraCrossbowWave
from .mantra_stage import entry_recipient_tick,entry_talent
from .stage_deployment import RangedReentries
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _next_receiver(frame,center,visited,world,states,radii,radius,born,*,eligible=None):
    rows=world.snapshot(frame,projectile_born=born)
    live=(r for r in sorted(rows,key=lambda row:world.births[row["id"]]["order"]) if states[r["id"]].hp>HP_EPSILON
          and (eligible is None or eligible(r["id"],frame)))
    def equal_distance(keys):
        return projectile_hatred_tie(world,keys,frame,born)
    return nearest_chain_target(frame,center,live,radii,visited,radius,max_candidates=128,nearest_tie=equal_distance,continuous=hasattr(world,"wave"))


def run_mantra_chain_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    presses=[a for a in request["actions"] if a.get("kind")=="skill" and a["frame"]<=request["end_frame"]]
    withdrawals={a["frame"] for a in request["actions"] if a.get("kind")=="withdraw"}
    card,stage,deployment,event,world=prepare_continuous_ranged(tables,request,MANTRA,releases,native_walk=False)
    if card["skill"]!=2:
        raise ValueError("本入口需要真言二技能")
    ev=caster_prefab_evidence(tables,MANTRA,2,skill_level=card["skill_level"],module_id=card["module"],
                              module_level=card["module_level"])
    place,face=event["placement"],event["view"]
    dt=tables.fixed_timestep()
    profile=mantra_bounce_profile(tables,card,ev)
    period=fixed_wait_frames(single(profile["period"]/dt))
    through=event["end_frame"]
    def schedule(deployment,place,face):
        by_press,activations,expirations,rejections={},{},{},{}
        if presses:
            options=dict(facing=face,direction=place["direction"])
            clock=run_committed_cast(tables,ev,horizon=0,**options)["natural_end"]
            restore,=ev["templates"]["switch_mode_restart_fsm"]["eventToActions"]["ON_BUFF_FINISH"]
            if clock["end_phase"]!="objects" or not restore["_restartFSM"] or not restore["_restoreDefault"]:
                raise ValueError("真言三段动作退出时的模式切换需核对")
            gauge,_=deploy_skill(tables,card,deployment["frame"])
            recovery=deployment["frame"]+1
            for press in presses:
                f=press["frame"]
                if f<deployment["frame"]:
                    continue
                if f<recovery:
                    rejections[f]=dict(kind="skill_button_rejected",frame=f,reason="skill_active")
                    continue
                for _ in range(recovery,f+1):
                    gauge.state.advance(gauge.step)
                recovery=f+1
                attempt=resolve_cast_start(tables,ev,frame=f,phase="after_tick",sp=gauge.state.points,**options)
                if attempt["cast_frame"] is None:
                    rejections[f]=dict(kind="skill_button_rejected",frame=f,reason=attempt["status"],sp=gauge.state.points)
                    continue
                commit=attempt["cast_frame"]
                if commit<f or attempt["spell_frame"]!=commit+clock["spell_frame"]:
                    raise ValueError("真言二技能从按键到模式切入的时序已变化")
                by_press[f]=attempt
                activations[attempt["spell_frame"]]=f
                expirations[commit+clock["end"]]=f
                recovery=commit+clock["first_recovery_tick"]
                gauge.state.points=attempt["sp_after"]
        return by_press,activations,expirations,rejections

    by_press,activations,expirations,rejections=schedule(deployment,place,face)
    cells=range_cells(card["base_range"],place,event["rows"],event["cols"])
    source=_raw_f32(card["panel_attributes"]["atk"])
    module=burst_output_scale(card)
    base=default_attack(tables,MANTRA)
    normal=base["fields"]
    interval=single(single(card["panel_attributes"]["baseAttackTime"])*100/single(card["panel_attributes"]["attackSpeed"]))
    ordinary_period=fixed_wait_frames(single(interval/dt))
    states={k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    logs=[dict(kind="deploy",frame=deployment["frame"])]
    seed=request.get("battle_random_seed",0)
    incoming=MantraCrossbowWave(tables,card,ev,world,deployment,event,states,cells,radii,logs,seed)
    talent=entry_talent(tables,card,ev,world,deployment,stream=incoming.rng)
    stop="horizon"
    next_poll=deployment["frame"]+_birth(tables,card,ev,face)["born_exit_update"]+1
    ordinary_end=-1
    active=False
    pending={}
    ordinary_releases={}
    ordinary_live_shots={}
    airborne=[]
    mounts={}
    face_sign=-1 if place["direction"]=="LEFT" else 1
    deaths={}
    recycle_wait=fixed_wait_frames(single(1.0/dt))

    def geometry(row):
        sign=face_sign if row["col"]==place["col"] else (1 if row["col"]>place["col"] else -1)
        k=row["prefab"],sign
        if k not in mounts:
            origin,mount=stationary_cast_mounts(tables,MANTRA,dict(action="Attack",first_key_seconds=0,playback_speed=1),
                place,row,face,static_mode_clips=("Default","Idle","Skill_2_Begin","Skill_2_Loop","Skill_2_End"),
                caster_face_sign=sign)
            mounts[k]=origin,(mount[0]-row["col"],mount[1]-row["row"],mount[2])
        origin,d=mounts[k]
        return origin,(row["col"]+d[0],row["row"]+d[1],d[2])

    def search(frame,*,after_objects=False):
        nonlocal ordinary_end,face_sign
        # OnCastEnd may call Character.TryToStartAttack synchronously after the
        # locked target failed its spell check. That search sees the completed enemy ticks.
        rows=([world.receiver_at(key,frame) for key in sorted(world.active[frame])] if after_objects else world.snapshot(frame))
        candidates=[r for r in rows if states[r["id"]].hp>HP_EPSILON and
                    circle_in_range(r["row"],r["col"],radii[r["id"]],cells,box_query=True)]
        if not candidates:
            return frame+3
        samples={r["id"]:frame for r in candidates} if after_objects else None
        primary=candidates[0] if len(candidates)==1 else world.select(candidates,frame,frame_by_target=samples)
        if primary["col"]!=place["col"]:
            face_sign=1 if primary["col"]>place["col"] else -1
        if not active:
            shot,sign=incoming.schedule(frame,normal,interval,primary)
            release,key=shot["release_frame"],primary["id"]
            clock=_after_spell_clock(release-frame,shot["clock"]["cached_duration_seconds"],int(dt*ONE),same_frame_resume=True)
            ordinary_end=frame+clock["end"]
            logs.append(dict(kind="ordinary_attack_start",frame=frame,target=key))
            next_button=next((f for f in by_press if f>=frame),None)
            if next_button is None or release<=next_button:
                ordinary_releases[release]=key
                ordinary_live_shots[release]=shot,sign
            else:
                logs.append(dict(kind="normal_attack_interrupted",frame=next_button,target=key))
            return frame+ordinary_period
        release=frame+profile["delay"]-int(not after_objects)
        pending[release]=dict(born=release,target=primary["id"],pos=geometry(primary)[0],visited=[])
        logs.append(dict(kind="attack_start",frame=frame,target=primary["id"],phase="COROUTINES" if after_objects else "OBJECTS",
                         selection_position=[primary["row"],primary["col"]]))
        return frame+period

    repeats=RangedReentries.for_request(tables,stage,world,request,deployment["frame"])
    for frame in range(deployment["frame"],through+1):
        new_place=repeats.advance(frame,logs) if repeats else None
        if new_place:
            deployment=place=new_place
            face="Back" if place["direction"]=="UP" else "Front"
            cells=range_cells(card["base_range"],place,event["rows"],event["cols"])
            event=dict(event,placement=place,view=face)
            by_press,activations,expirations,rejections=schedule(deployment,place,face)
            talent=entry_talent(tables,card,ev,world,deployment,stream=incoming.rng)
            incoming.deploy,incoming.event,incoming.cells=deployment,event,cells
            incoming.face_sign=face_sign=-1 if place["direction"]=="LEFT" else 1
            incoming.aura.mode=None
            mounts.clear()
            next_poll=frame+_birth(tables,card,ev,face)["born_exit_update"]+1
            ordinary_end=-1
            active=False
            logs.append(dict(kind="deploy",frame=frame))
        was_present=world.present
        if frame in withdrawals:
            world.remove_owner(frame,logs,"withdraw")
        incoming.aura.advance(frame,_raw_f32(dt),2 if active else 0)
        if frame>deployment["frame"] or new_place:
            incoming.advance(frame)
        if repeats and was_present and not world.present:
            repeats.leave(frame,MANTRA,manual=frame in withdrawals)
        if not world.present:
            pending.clear()
            ordinary_releases.clear()
            ordinary_live_shots.clear()
        world.allied_impacts(frame,states,logs)
        if world.settle_exits(frame,lambda k:states[k].hp>HP_EPSILON) is not None:
            frame=world.defeated_at
            stop="stage_defeat"
            break
        if world.present and frame in activations:
            active=True
            next_poll=frame+1
        elif frame in expirations:
            active=False
            next_poll=frame+1
            logs.append(dict(kind="skill_mode_end",frame=frame))
            for future,shot in pending.items():
                logs.append(dict(kind="skill_attack_interrupted",frame=frame,target=shot["target"],would_release=future))
            pending.clear()
        if world.present and frame>=next_poll:
            next_poll=search(frame)

        updated=set()
        def tick_through(birth):
            for key,state in states.items():
                born=world.births[key]["frame"]
                if key not in updated and born<frame and born<=birth and state.hp>HP_EPSILON:
                    entry_recipient_tick(talent,key,state,frame,world)
                    state.tick(frame)
                    updated.add(key)

        ordinary_hits=[]
        ordinary=None
        for key,born,_ in incoming.impacts(frame):
            if ordinary is not None:
                ordinary_hits.append(ordinary)
            ordinary=key,born
        def ordinary_hit(receipt):
            key,born=receipt
            tick_through(born)
            state=states[key]
            if state.hp>HP_EPSILON:
                hit=state.receive_ordinary(frame,source=MANTRA,damage_type="arts",attack_q32=source,
                    mitigation_q32=max(0,_raw_f32(world.stats[key]["res"])+state.res_add),
                    output_scale_q32=module if state.burst else ONE)
                logs.append(dict(hit,kind="ordinary_impact",target=key,release_frame=born))
                if state.hp<=HP_EPSILON:
                    deaths[key]=(frame,world.by_id(key,frame,projectile_born=born))
        for receipt in ordinary_hits:
            ordinary_hit(receipt)
        survivors=[]
        for flight in airborne:
            born,key=flight["born"],flight["target"]
            if ordinary is not None and ordinary[1]<=born:
                ordinary_hit(ordinary)
                ordinary=None
            tick_through(born)
            alive=states[key].hp>HP_EPSILON
            if alive:
                target=world.by_id(key,frame,projectile_born=born)
            else:
                death_frame,target=deaths.setdefault(key,(frame,world.by_id(key,frame,projectile_born=born)))
                # The first coroutine pass follows the death's object tick.
                # It removes the UID after the final object-phase observation.
                if frame-death_frame>=recycle_wait:
                    target=None
            # TRACE holds the most recent mount after MountPoint.isValid fails.
            if target is not None:
                flight["last_mount"]=geometry(target)[1]
            mount=flight["last_mount"]
            visited=flight["visited"]
            step=ordinary_trace_step(flight["pos"],mount,effective_dt=dt,round_position=event["round_position"],
                                     speed=profile["bounce_speed"] if visited else profile["speed"])
            if not step.reached:
                flight["pos"]=step.position
                survivors.append(flight)
                continue
            anchor=target
            if target is None:
                anchor=dict(row=step.position[1],col=step.position[0])
                logs.append(dict(kind="reached_invalid_target_mount",frame=frame,target=key,release_frame=born,
                                 bounce=len(visited),position=[anchor["row"],anchor["col"]]))
            elif alive:
                hit=apply_mantra_hit(tables,card,ev,states[key],frame,attack_q32=source,
                                     arts_resistance_q32=max(0,_raw_f32(target["res"])+states[key].res_add),bounce=len(visited))
                logs.append(dict(kind="bounce_impact" if visited else "primary_impact",target=key,release_frame=born,
                                 bounce=len(visited),position=[target["row"],target["col"]],**hit))
                if states[key].hp<=HP_EPSILON:
                    deaths[key]=(frame,target)
            else:
                logs.append(dict(kind="reached_retained_dead_target",frame=frame,target=key,release_frame=born,
                                 bounce=len(visited),position=[target["row"],target["col"]]))
            visited.append(key)
            if len(visited)<=profile["jumps"]:
                successor=_next_receiver(frame,anchor,visited,world,states,radii,profile["radius"],born)
                if successor is not None:
                    logs.append(dict(kind="change_bounce_target",frame=frame,release_frame=born,from_target=key,to_target=successor))
                    # DealReached snaps to the traced mount while its UID is valid.
                    next_row=world.by_id(successor,frame,projectile_born=born)
                    flight.update(target=successor,pos=mount if target is not None else step.position,
                                  last_mount=geometry(next_row)[1])
                    survivors.append(flight)
        if ordinary is not None:
            ordinary_hit(ordinary)
        tick_through(frame)
        airborne=survivors
        if frame in ordinary_releases:
            key=ordinary_releases.pop(frame)
            if frame in ordinary_live_shots:
                shot,sign=ordinary_live_shots.pop(frame)
                incoming.release(shot,sign,key,frame,normal,False)
            logs.append(dict(kind="projectile_release",frame=frame,target=key,projectile=normal["_projectileKey"]))
        if frame in pending:
            shot=pending.pop(frame)
            if states[shot["target"]].hp<=HP_EPSILON or shot["target"] not in world.active[frame]:
                logs.append(dict(kind="locked_target_dead_at_release",frame=frame,target=shot["target"],main_fired=0))
                next_poll=search(frame,after_objects=True)
            else:
                shot["last_mount"]=geometry(world.receiver_at(shot["target"],frame))[1]
                airborne.append(shot)
                logs.append(dict(kind="projectile_release",frame=frame,target=shot["target"],projectile=profile["projectile"]))
        if world.present and frame in by_press:
            next_poll=by_press[frame]["spell_frame"]+1
            for e in by_press[frame]["events"]:
                if e["kind"]=="enter_skill_state" and frame<=ordinary_end:
                    e=dict(e,old_state="attack",old_attack_exit_calls_interrupt_if_not=True)
                logs.append(e)
        if frame in rejections:
            logs.append(rejections[frame])
        if world.settled(frame,states,logs):
            stop="stage_victory_projected"
            break
    talent["ordinary_enemy_attack_interrupts"]=sum(e["kind"]=="enemy_attack_interrupted" for e in logs)
    result=dict(status="mantra_s2_moving_wave",through_frame=frame,stop=stop,
                events=sorted((e for e in logs if e["frame"]<=frame),key=lambda e:e["frame"]),
                remaining_hp_q32={key:s.hp for key,s in states.items()},receivers={key:s.snapshot() for key,s in states.items()},
                element_events={key:s.events for key,s in states.items() if s.events},entry_talent=talent)
    world.stage_result(result,fixed_timestep=dt,target_field="target",hp_field="hp_after_q32",projection_horizon=event["end_frame"])
    return dict(stage=stage["code"],operator_id=MANTRA,skill=2,module=card["module"],deployment=deployment if frame>=deployment["frame"] else None,
                spawns={k:dict(enemy=v["enemy_key"],frame=v["frame"],route=v["route_index"]) for k,v in world.births.items()},
                **result,source=tables.source)

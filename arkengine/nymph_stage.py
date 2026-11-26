"""Nymph's ranged skills, on the actual enemy instances of an ordinary stage wave."""
from collections import defaultdict
from math import hypot

from .animation_assets import read_skin_actions
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .battle_random import BattleRandom
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_flight import projectile_reach
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .elemental_casters import NYMPH, _board
from .elemental_projectiles import ranged_shot_release,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .eyja_ignition import ignition_circle_overlap
from .fear_navigation import FearedGroundRoute, GroundFearNavigation, CursorFearRoute
from .fixed import ONE,mul
from .lin_output_aura import trigger_contact
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .nymph_impact import apply_s3_hit, s3_damage_scales
from .nymph_soul import NymphRecovery, SoulOnTarget, SoulRules, y_recovery_period
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .mode_replay import _AttackCooldown
from .caster_cast import _after_spell_clock
from .skill_recovery import READY_EPSILON,_divide
from .skill_recovery import deploy_skill
from .stage_channel import object_position_frame
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def run_nymph_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    buttons=[a for a in request["actions"] if a.get("kind")=="skill"]
    presses={a["frame"]:a for a in buttons}
    withdrawals={a["frame"] for a in request["actions"] if a.get("kind")=="withdraw"}
    card,stage,place,scene,world=prepare_continuous_ranged(tables,request,NYMPH,releases,native_walk=True)
    slot=card["skill"]
    ev=caster_prefab_evidence(tables,NYMPH,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    board={b["key"]:b["value"] for b in ev["skill_blackboard"]}
    mode_path=f"Modes/S{slot}"
    if slot==2:
        skill,=(c["fields"] for c in ev["skill"]["components"] if c["script"]=="RangedAttack")
        variants=ev["projectiles"][skill["_projectileKey"]].values()
        if any(not c["fields"]["_stopWhenSourceInvalid"] for p in variants for c in p["components"]
               if c["script"]=="SimpleProjectile"):
            raise ValueError("妮芙二技能的无效来源停止条件变化")
        delays={c["fields"]["_delayAfterReached"] for p in variants for c in p["components"] if c["script"]=="AdvancedMovement"}
        delay,=delays
    else:
        skill,=(c["fields"] for c in ev["character"]["components"] if c["path"]==mode_path+"/Body/Attack" and c["script"]=="RangedAttackWithExtraActions")
        s3_scales=s3_damage_scales(skill,board)
        selector,=(c["fields"] for c in ev["character"]["components"] if c["path"]==mode_path+"/Body/Attack/Selector" and c["script"]=="AdvancedSelector")
        if selector["_maxTargetKey"]!="max_target" or selector["_postFilter"]!=4:
            raise ValueError("妮芙三技能的同时攻击数或排序条件变化")
        restore,=ev["templates"]["switch_mode_restart_fsm"]["eventToActions"]["ON_BUFF_FINISH"]
        if not restore["_restartFSM"] or not restore["_restoreDefault"]:
            raise ValueError("妮芙三技能的退态动作配置变化")
    base=default_attack(tables,NYMPH)["fields"]
    default_aim,=(p["fields"] for p in ev["character"]["components"]
                  if p["path"]=="Modes/Default/Body/Attack" and p["script"]=="RangedAttack")
    facing_fields=("_faceToTarget","_faceToDefault","_faceToFront","_faceToFirstCastTarget",
                   "_checkFaceToFirstCastTargetDownAttack","_forceFaceToDirection")
    if any(tuple(a[k] for k in facing_fields)!=(1,0,0,0,0,0) for a in (default_aim,skill)):
        raise ValueError("妮芙的主目标朝向或技能内逐目标翻面配置发生变化")
    if slot==3 and skill["_emitToInputPosWhenTargetIsInvalid"]:
        raise ValueError("心防溃决对无效目标的发射配置变化")
    if slot==3 and (base["_waitForProjectileInvalid"] or base["_useCachedAtkOnly"] or base["_transferSource"]):
        raise ValueError("妮芙普攻弹的留场或命中取攻配置变化")
    stats=card["panel_attributes"]
    speeds={}
    for attack in (base,skill):
        key=attack["_projectileKey"]
        trace=projectile_reach(tables,ev,dict(projectile=key,frame=0,origin=(0,0,0),
                                            target_mounts=[(0,0,0)],round_position=True))
        if trace.get("movement")!="AdvancedMovement" or trace["status"]!="reached":
            raise ValueError("妮芙关卡弹道需要已核对的 AdvancedMovement")
        speeds[key]=trace["speed"]
    start,end=place["frame"],request["end_frame"]
    dt=tables.fixed_timestep()
    view=scene["view"]
    face_sign={"LEFT":-1,"RIGHT":1}.get(place["direction"])
    if face_sign is None:
        skin=read_skin_actions(tables,"chararts/"+NYMPH+".ab","defaultskin",["Attack"])
        face_sign=skin["default_horizontal_face_sign"]
    interval=single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"]))
    skill_interval=single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"]+board.get("attack_speed",0)))
    gauge,_=deploy_skill(tables,card,start)
    navigation=None
    stream=tables.combat_random(request.get("battle_random_seed",0)) if slot==2 else None
    map_stream=BattleRandom(request.get("map_random_seed",0)) if slot==2 else None
    spawn_draws=defaultdict(int)
    if stream is not None and map_stream is not None:
        for birth in world.births.values():
            spawn_draws[max(start-1,birth["frame"])]+=2
        for _ in range(spawn_draws[start-1]):
            stream.value()
    period=y_recovery_period(tables,card,ev)
    recovery=NymphRecovery(gauge,period) if period is not None else None
    cast=None
    casts=[]
    first=start+_birth(tables,card,ev,view)["born_exit_update"]+1
    next_scan=first
    events=[dict(kind="deployed",frame=start)] if world.present else []
    hp={k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    rules=SoulRules.from_card(tables,card,ev)
    souls={k:SoulOnTarget(rules) for k in hp}
    stacks=0
    talent=_board(card,1,"2")
    stack_bonus=_raw_f32(talent["atk"])
    burst_factor=_raw_f32(_trait_values(card).get("damage_scale",1))
    base_attack=_raw_f32(stats["atk"])
    radii={k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    base_cells=range_cells(card["base_range"],place,scene["rows"],scene["cols"])
    skill_cells=range_cells(card["skill_range"] or card["base_range"],place,scene["rows"],scene["cols"])
    flights=[]
    pending=defaultdict(list)
    mode=restore_frame=None
    awake_until=0
    finish=unblock=None
    sp_commit=None
    splashes=[]
    cooldown=None
    cast_ends=start
    step=gauge.step
    fear_routes={}
    on_field=world.present
    if not on_field:
        next_scan=None
    def aim(target):
        dx=single(single(target["col"])-place["col"])
        dy=single(single(target["row"])-place["row"])
        if dx:
            return view,1 if dx>0 else -1
        if abs(dy)>single(.2):
            return ("Back" if dy>0 else "Front"),face_sign
        return view,face_sign

    def result(frame,reason,**other):
        out=dict(stage=stage["code"],operator_id=NYMPH,skill=slot,module=card["module"],module_level=card["module_level"],
            status=reason,stop=reason,through_frame=frame,cast=cast,skill_end_frame=finish,first_unblocked_recovery_tick=unblock,
            casts=casts,sp=gauge.state.points,y_recovery=recovery.events if recovery else [],
            events=[e for e in events if e["frame"]<=frame],receivers={k:s.snapshot() for k,s in hp.items()},
            talent_stacks=stacks,**other)
        return world.stage_result(out,fixed_timestep=dt,target_field="target",hp_field="hp_q32")
    def targets(frame,cells,limit,*,after_objects=False):
        if after_objects:
            rows=[world.receiver_at(k,frame) for k in world.active[frame] if k not in world.departed]
        else:
            rows=world.snapshot(frame)
        pool=[r for r in rows if world.selectable(r["id"],frame) and hp[r["id"]].hp>HP_EPSILON and circle_in_range(r["row"],r["col"],radii[r["id"]],cells,box_query=True)]
        chosen=[]
        while pool and len(chosen)<limit:
            if len(pool)==1:
                row=pool[0]
            elif any(r["id"] in fear_routes for r in pool):
                def priority(r):
                    key=r["id"]
                    observed=frame if after_objects else object_position_frame(world.births[key],frame,world.positions,
                        world.active,key,observer_born=world.deployment_frame,observer_phase="INPUT")
                    route=fear_routes.get(key)
                    distance=route.distances.get(observed) if route else None
                    if distance is None:
                        verdict=world.priority({key},frame,frame_by_target={key:observed},distance_samples=True)
                        if not verdict:
                            dest=world.scene.level["routes"][world.births[key]["route_index"]]["endPosition"]
                            distance=hypot(r["row"]-dest["row"],r["col"]-dest["col"])
                            world.assumptions.append(dict(kind="rule_applied",frame=frame,target=key,rule="distance_to_exit_at_unknown_checkpoint"))
                        else:
                            distance,=verdict[key]
                    birth=world.births[key]
                    return int(distance*ONE)//_raw_f32(.1),birth["frame"],birth["order"]
                row=min(pool,key=priority)
            else:
                row=world.select(pool,frame,frame_by_target={r["id"]:frame for r in pool} if after_objects else None)
            chosen.append(row)
            pool.remove(row)
        return chosen
    def contact(frame,key):
        pos=world.positions[frame][key]
        touching=trigger_contact(pos,radii[key],base_cells,verified_box_sensor=True)
        return touching if touching is not None else circle_in_range(*pos,radii[key],base_cells,box_query=True)

    def gain_stack(frame,key):
        nonlocal stacks,interval,skill_interval,next_scan,awake_until
        if not on_field or not ((card["module_info"] or {}).get("typeIcon")=="pri-x" and card["module_level"]>=2 or contact(frame,key)):
            return
        before=stacks
        stacks=min(int(talent["max_stack_cnt"]),stacks+1)
        if before!=stacks:
            events.append(dict(kind="dark_talent_stack",frame=frame,target=key,stacks=stacks))
        if before<talent.get("stack_cnt_check",float("inf"))<=stacks:
            speed=stats["attackSpeed"]+talent["attack_speed"]
            interval=single(single(stats["baseAttackTime"])*100/single(speed))
            skill_interval=single(single(stats["baseAttackTime"])*100/single(speed+board.get("attack_speed",0)))
            in_mode=slot==3 and mode is not None and mode<frame<restore_frame
            speed+=board.get("attack_speed",0) if in_mode else 0
            if cooldown and next_scan is not None and next_scan>frame:
                cooldown.change_period(frame,step,_divide(_raw_f32(stats["baseAttackTime"])*100,_raw_f32(speed)))
                next_scan=awake_until=max(cast_ends,frame+max(1,(cooldown.remaining-READY_EPSILON+step)//step))
            events.append(dict(kind="talent_attack_speed",frame=frame,stacks=stacks,attack_speed=speed))
    def hit(frame,key,kind,cached_attack=None):
        state=hp[key]
        prior=state.hp
        # The ordinary projectile keeps its own damage action. Its living source
        # supplies current ATK, including a mode buff attached in a prior COROUTINES.
        mode_bonus=_raw_f32(board["atk"]) if slot==3 and mode is not None and mode<frame<restore_frame else 0
        base_scale=ONE+stacks*stack_bonus+mode_bonus
        attack=mul(base_attack,base_scale) if on_field else cached_attack
        if kind in ("primary","splash"):
            attack=mul(attack,_raw_f32(board["atk_scale"]))
        factor=burst_factor if state.burst else ONE
        souls[key].before_attack_damage()
        resistance=_raw_f32(world.stats[key]["res"])+state.res_add
        element=None
        if kind=="s3":
            packet,element=apply_s3_hit(state,frame,attack,resistance,factor,s3_scales)
        else:
            packet=state.receive_ordinary(frame,source=NYMPH,damage_type="arts",attack_q32=attack,
                mitigation_q32=resistance,output_scale_q32=factor)
        damage=packet["damage_q32"]+(element.get("damage_q32",0) if element else 0)
        events.append(dict(kind=kind+"_hit",frame=frame,target=key,attack_q32=attack,damage_q32=damage,
                           arts_damage_q32=packet["damage_q32"],elemental_damage=element,hp_q32=state.hp))
        if kind in ("primary","splash"):
            was_dark=state.burst=="DARK"
            applied=state.apply_buildup(frame,"DARK",mul(prior-state.hp,_raw_f32(board["ep_damage_ratio"])))
            events[-1]["dark_buildup"]=applied
            if state.burst=="DARK" and not was_dark:
                gain_stack(frame,key)
            souls[key].after_s2_buildup(state)
    def detach(frame,lost):
        nonlocal on_field,flights,next_scan,restore_frame,mode,sp_commit
        on_field=False
        if not lost:
            world.remove_owner(frame,events,"withdraw")
            events[-1].update(cancelled_primary=sum(p["kind"]=="primary" for p in flights),cancelled_splash=bool(splashes))
        flights=[flight for flight in flights if flight["kind"]!="primary"]
        pending.clear()
        next_scan=restore_frame=mode=sp_commit=None
        splashes.clear()
        for soul in souls.values():
            soul.withdraw()
        if recovery:
            recovery.stopped=True
            recovery.sync(frame,False)

    for frame in range(start,end+1):
        world.receive(frame,hp,events)
        lost=on_field and not world.present
        if lost:
            detach(frame,True)
        for key in sorted(fear_routes,key=lambda k:(world.births[k]["frame"],world.births[k]["order"])):
            route=fear_routes[key]
            if hp[key].hp<=HP_EPSILON or route.exited:
                world.active[frame].discard(key)
                continue
            if not route.motion.buffs and world.incoming and key in world.incoming.enemies and world.incoming.holding(key,frame):
                route.motion.frame=route.frame=frame
                world.positions[frame][key]=route.motion.position
                world.active[frame].add(key)
                continue
            resistance=.5 if "STATUS_RESISTANCE" in world.wave.flags.at(key,frame) else 1.
            route.motion.resistance_factor=resistance
            route.advance(frame)
            route.emit_events(events)
            if route.motion.status=="unresolved":
                route=fear_routes[key]=CursorFearRoute(tables,world,key,frame,navigation,stream,map_stream,prior=route)
            if route.exited:
                world.active[frame].discard(key)
                world.exits=world.exits[:world.exit_index]+sorted([*world.exits[world.exit_index:],(frame,key)])
            else:
                world.active[frame].add(key)
                world.positions[frame][key]=route.motion.position
        if world.settle_exits(frame,lambda k:hp[k].hp>HP_EPSILON) is not None:
            return result(world.defeated_at,"stage_defeat")
        blocked=sp_commit is None and unblock is not None and frame<unblock
        if on_field and start<frame:
            if recovery:
                recovery.stopped=blocked
                recovery.tick(frame)
            else:
                gauge.state.advance(gauge.step,stopped=blocked)
        if frame==sp_commit:
            gauge.state.points-=gauge.state.cost
            sp_commit=None
        if on_field and not lost and frame==restore_frame:
            if slot==3:
                pending.clear()
                events.append(dict(kind="restore_default_mode",frame=frame))
                next_scan=frame+1
            else:
                events.append(dict(kind="s2_cast_finished",frame=frame))
                next_scan=frame
            mode=restore_frame=None
            awake_until=0
        # Before the first burst, ticking empty receivers has no effect. All admitted
        # shots have an existing ordinary receiver when its ability locks it.
        active=slot==3 and mode is not None and mode<frame
        active_atk=ONE+stacks*stack_bonus+(_raw_f32(board["atk"]) if active else 0)
        for key,state in hp.items():
            if state.burst or state.last_tick is not None:
                before=state.hp
                state.tick(frame)
                if state.hp<before:
                    events.append(dict(kind="element_tick",frame=frame,target=key,hp_q32=state.hp))
            soul=souls[key]
            if soul.listeners or soul.active:
                before=state.hp
                soul.tick(frame,state,mul(base_attack,active_atk),burst_factor)
                if state.hp<before:
                    events.append(dict(kind="soul_tick",frame=frame,target=key,hp_q32=state.hp,damage_q32=before-state.hp))
        world.allied_impacts(frame,hp,events,on_dark=gain_stack)
        if on_field and not lost and frame==next_scan:
            chosen=targets(frame,skill_cells if active else base_cells,int(board["attack@max_target"]) if active else 1)
            if not chosen:
                next_scan+=3
            else:
                kind="s3" if active else "ordinary"
                secs=skill_interval if active else interval
                duty=skill if active else base
                view,face_sign=aim(chosen[0])
                facing=(view,face_sign)
                shot=ranged_shot_release(tables,ev,duty,attack_frame=frame,attack_interval=secs,facing=view,
                                         mode_path=mode_path if active else None)
                events.append(dict(kind=kind+"_attack_start",frame=frame,targets=[r["id"] for r in chosen]))
                pending[shot["release_frame"]].extend((kind,r,shot,facing) for r in chosen)
                cooldown=_AttackCooldown(frame,_raw_f32(secs))
                cast_ends=frame+_after_spell_clock(shot["release_frame"]-frame,shot["clock"]["cached_duration_seconds"],step)["end"]
                next_scan=awake_until=frame+fixed_wait_frames(single(secs/dt))
        retained=[]
        for flight in flights:
            kind,key,born=flight["kind"],flight["target"],flight["born"]
            pos=world.by_id(key,frame,projectile_born=born)
            if hp[key].hp<=HP_EPSILON or pos is None or flight.get("fixed"):
                if kind!="primary":
                    continue
                pos=flight["last"]
                flight["fixed"]=True
            else:
                flight["last"]=pos
            dx,dy,z=flight["offset"]
            mount=(pos["col"]+dx,pos["row"]+dy,z)
            moved=ordinary_trace_step(flight["position"],mount,effective_dt=dt,round_position=scene["round_position"],speed=flight["speed"])
            if not moved.reached:
                flight["position"]=moved.position
                retained.append(flight)
                continue
            immune=False
            tracked=None
            if kind=="primary":
                unit=world.scene.enemies[world.stats[key]["enemy_key"]]
                immune=unit["attributes"]["fearedImmune"]["m_value"]
                if not immune and not flight.get("fixed"):
                    if navigation is None:
                        navigation=GroundFearNavigation(world.scene,static_terrain=True)
                    tracked=fear_routes.get(key)
                    if tracked is None:
                        try:
                            tracked=FearedGroundRoute(tables,world,key,frame,navigation,stream,map_stream)
                        except ValueError:
                            tracked=CursorFearRoute(tables,world,key,frame,navigation,stream,map_stream)
                    tracked.motion.resistance_factor=.5 if "STATUS_RESISTANCE" in world.wave.flags.at(key,frame) else 1.
                    tracked.apply(frame,(place["row"],place["col"]),board["fear"])
            if not flight.get("fixed"):
                hit(frame,key,kind,flight["cached_attack"])
            if kind=="primary":
                center=pos
                splashes.append((frame+fixed_wait_frames(single(delay/dt))-1,center,key))
                primary=next((e for e in reversed(events) if e["frame"]==frame and e["kind"]=="primary_hit" and e["target"]==key),None)
                if primary and not flight.get("fixed"):
                    primary.update(fear_request_seconds=board["fear"],fear_immune=immune,blast_center=[center["row"],center["col"]])
                if tracked is not None:
                    tracked.emit_events(events)
                    if hp[key].hp>HP_EPSILON:
                        fear_routes[key]=tracked
                        world.exits=[v for i,v in enumerate(world.exits) if i<world.exit_index or v[1]!=key]
                        world.wave.escapes.pop(key,None)
                        if world.incoming and key in world.incoming.enemies:
                            enemy_action=world.incoming.enemies[key]
                            enemy_action.update(rerouted=True,fear_until=max((b["expiry"] for b in tracked.motion.buffs.values()),default=frame),shot=None,hold_until=frame)
        flights=retained
        for kind,r,shot,facing in pending.pop(frame,[]):
            key=r["id"]
            if hp[key].hp<=HP_EPSILON or key not in world.active[frame]:
                if kind in ("ordinary","s3"):
                    events.append(dict(kind=kind+"_release_cancelled",frame=frame,target=key))
                    continue
                if not skill["_emitToInputPosWhenTargetIsInvalid"]:
                    continue
                events.append(dict(kind="primary_to_input_position",frame=frame,target=key,position=[r["row"],r["col"]]))
            if frame<end:
                origin,mount=stationary_cast_mounts(tables,NYMPH,shot["clock"],scene["placement"],r,facing[0],
                                                    caster_face_sign=facing[1])
                speed=speeds[(base if kind=="ordinary" else skill)["_projectileKey"]]
                flights.append(dict(kind=kind,target=key,born=frame,position=origin,speed=speed,
                                    offset=(mount[0]-r["col"],mount[1]-r["row"],mount[2]),
                                    cached_attack=mul(base_attack,active_atk),last=r,
                                    fixed=kind=="primary" and (hp[key].hp<=HP_EPSILON or key not in world.active[frame])))
            events.append(dict(kind=kind+"_release",frame=frame,target=key,view=facing[0],face_sign=facing[1]))
        for splash in tuple(splashes):
            if frame!=splash[0]:
                continue
            splashes.remove(splash)
            center=splash[1]
            struck=[k for k in sorted(world.active[frame]) if hp[k].hp>HP_EPSILON and world.selectable(k,frame) and
                    ignition_circle_overlap(center,dict(zip(("row","col"),world.positions[frame][k])),board["projectile_range"],radii[k])]
            events.append(dict(kind="splash_query",frame=frame,targets=struck,center=[center["row"],center["col"]],
                               feared_positions={k:world.positions[frame][k] for k in struck if k in fear_routes}))
            for key in struck:
                hit(frame,key,"splash")
        if on_field and recovery:
            recovery.sync(frame,any(contact(frame,key) for key in world.active[frame] if hp[key].burst and hp[key].hp>HP_EPSILON))
        if on_field and frame in withdrawals:
            detach(frame,False)
        if stream is not None and map_stream is not None:
            for _ in range(spawn_draws[frame]):
                stream.value()
        if world.settled(frame,hp,events):
            return result(frame,"stage_victory_projected")
        if on_field and frame in presses:
            selected=targets(frame,base_cells,1,after_objects=True) if slot==2 else []
            aim_for_skill=aim(selected[0]) if selected else (view,face_sign)
            owner=("skill" if mode is not None and frame<restore_frame else "born" if frame<first-1 else
                   "attack" if frame<awake_until else "idle")
            attempt=resolve_cast_start(tables,ev,frame=frame,direction=place["direction"],facing=aim_for_skill[0],phase=presses[frame].get("phase","after_tick"),
                owner_state=owner,sp=gauge.state.points,triggerable=slot!=2 or bool(selected))
            events.extend(attempt["events"])
            casts.append(attempt)
            if attempt["cast_frame"] is None:
                if cast is None and len(buttons)==1:
                    cast=attempt

                continue
            cast=attempt
            view,face_sign=aim_for_skill
            if cast["cast_frame"]==frame:
                gauge.state.points=cast["sp_after"]
            else:
                sp_commit=cast["cast_frame"]
            pending.clear()
            clock=run_committed_cast(tables,ev,horizon=0,facing=view,direction=place["direction"],
                                     effective_attack_interval=interval,watched_buff_survives=True)
            finish=cast["cast_frame"]+clock["natural_end"]["end"]
            unblock=cast["cast_frame"]+clock["natural_end"]["first_recovery_tick"]
            mode=cast["spell_frame"]
            restore_frame=finish+1
            if slot==2:
                key,=selected
                events.append(dict(kind="s2_lock",frame=frame,target=key["id"]))
                pending[mode].append(("primary",key,dict(release_frame=mode,clock=cast["ability_clock"]),aim_for_skill))
                next_scan=restore_frame
            else:
                next_scan=mode+1
    return result(end,"horizon")

"""Mostima's channel, time field and relative pushes on the continuous stage clock."""
from math import floor, hypot

from .area_caster_skills import MOSTIMA, execute_area_skill
from .attack_range import circle_in_range, range_cells
from .caster_modules import splash_range_module
from .caster_cast import run_committed_cast
from .caster_first_spell import attack_key_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .continuous_ranged import prepare_continuous_ranged
from .damage import HP_EPSILON
from .enemy_elements import EnemyElements
from .enemy_speed import move_attribute, movement_cap
from .fixed import ONE, mul
from .forced_motion import enemy_push_profile, start_push, step_push
from .marcil_deploy import _birth
from .mostima_aura import aura_profile, effective_period, set_period
from .mystic_timing import fixed_wait_frames
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import deploy_skill
from .stage_deployment import RangedReentries
from .stage_channel import UnsupportedChannelScene
from .stage_route_cursor import ControlledRoute
from .volley_damage import _raw_f32


def run_mostima_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    card,stage,place,event,world=prepare_continuous_ranged(tables,request,MOSTIMA,releases)
    slot=card["skill"]
    dt=tables.fixed_timestep()
    start,end,face=event["frame"],event["end_frame"],event["view"]
    panel=card["panel_attributes"]
    ev=caster_prefab_evidence(tables,MOSTIMA,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    board={p["key"]:p["value"] for p in ev["skill_blackboard"]}
    comps=ev["character"]["components"]
    normal=next(p["fields"] for p in comps if p["script"]=="MeleeAttack" and p["path"]=="Modes/Default/Body/Attack")
    selector=next(p for p in comps if p["id"]==normal["_selector"]["m_PathID"])
    splash=next(p["fields"]["m_Radius"] for p in comps if p["path"]==selector["path"] and p["script"]=="CircleCollider2D")
    normal_grid=card["base_range"]
    if card["module_info"] and card["module_info"]["typeIcon"]=="spc-x":
        normal_grid=tables.read("excel/range_table.json")[splash_range_module(tables,card)["range_id"]]
    def layout(place,face):
        normal_area=range_cells(normal_grid,place,event["rows"],event["cols"])
        wide=range_cells(card["skill_range"] or normal_grid,place,event["rows"],event["cols"])
        interval=panel["baseAttackTime"]*100/panel["attackSpeed"]
        delays=[attack_key_clock(tables,ev,face,normal["_animKey"],interval)[0]]
        periods=[fixed_wait_frames(interval/dt)]
        clock=run_committed_cast(tables,ev,horizon=round(card["skill_row"]["duration"]/dt)+120,facing=face,direction=place["direction"])
        if slot==3:
            ability=next(p["fields"] for p in comps if p["script"]=="MeleeAttack" and p["path"]=="Modes/S2/Body/Attack")
            interval=(panel["baseAttackTime"]+board["base_attack_time"])*100/panel["attackSpeed"]
            delays.append(attack_key_clock(tables,ev,face,ability["_animKey"],interval)[0])
            periods.append(fixed_wait_frames(interval/dt))
        return normal_area,wide,delays,periods,clock

    normal_area,wide,delays,periods,clock=layout(place,face)
    base_atk=_raw_f32(panel["atk"])
    if slot==3:
        phase=tables.read("excel/character_table.json")[MOSTIMA]["phases"][card["elite"]]
        force=phase["attributesKeyFrames"][-1]["data"]["baseForceLevel"]+int(board["attack@force"])
    else:
        channel=next(p["fields"] for p in ev["skill"]["components"] if p["script"]=="ChannelingMeleeAttack")
        stun_seconds=channel["_activeBuffs"][0]["lifeTime"]
        pulse_scale=_raw_f32(board[channel["_atkScaleKey"]])
    def slow(inside,enhanced):
        return execute_area_skill(tables,card,dict(kind="slow_aura",frame=start,in_range=inside,skill_affecting=enhanced))["result"]["move_speed_ratio_delta"]
    modifiers={(inside,mode):slow(inside,mode) for inside in (False,True) for mode in (False,True)}
    gauge,_=deploy_skill(tables,card,start)
    set_period(gauge.state,effective_period(aura_profile(tables,card),MOSTIMA,"CASTER",world.present),stopped=False)
    states={k:EnemyElements.from_scene_enemy(tables,dict(v,hp_q32=_raw_f32(v["hp"]))) for k,v in world.stats.items()}
    radii={k:tables.enemy_circle(v["prefab"]) for k,v in world.stats.items()}
    walks,stuns,pushes,bodies={},{},{},{}
    logs=[dict(kind="deployment",frame=start)]
    actions={a["frame"]:a.get("kind") for a in request["actions"]}
    controls={}
    for e in request.get("owner_channel_events",()):
        controls.setdefault(e["frame"],[]).append(e)
    free={}
    for span in request.get("enemy_target_free",()):
        key=span.get("target",span.get("id"))
        free.setdefault(key,[]).append((span["start_frame"],span["end_frame"]))
    def inside(k,pos,cells):
        return circle_in_range(*pos,radii[k],cells,box_query=True)
    def living(k):
        return states[k].hp>HP_EPSILON
    def valid(k,f):
        return living(k) and world.selectable(k,f) and not any(lo<=f<=hi for lo,hi in free.get(k,()))
    def damage(f,k,atk,kind):
        packet=states[k].receive_ordinary(f,source=MOSTIMA,damage_type="arts",attack_q32=atk,
                    mitigation_q32=_raw_f32(world.stats[k]["res"])+states[k].res_add)
        logs.append(dict(frame=f,kind=kind,target=k,damage_q32=packet["damage_q32"],hp_after_q32=states[k].hp))
    poll=start+_birth(tables,card,ev,face)["born_exit_update"]+1
    pending=None
    pulses=set()
    mode=finish=None
    recovery=disabled_until=start
    repeats=RangedReentries.for_request(tables,stage,world,request,start)
    disarmed=False
    for frame in range(start+1,end+1):
        new_place=repeats.advance(frame,logs) if repeats else None
        if new_place:
            place=new_place
            face="Back" if place["direction"]=="UP" else "Front"
            normal_area,wide,delays,periods,clock=layout(place,face)
            gauge,_=deploy_skill(tables,card,frame)
            set_period(gauge.state,effective_period(aura_profile(tables,card),MOSTIMA,"CASTER",True),stopped=False)
            poll=frame+_birth(tables,card,ev,face)["born_exit_update"]+1
            mode=pending=None
            recovery=disabled_until=frame
            pulses.clear()
            disarmed=False
            logs.append(dict(kind="deployment",frame=frame))
        was_present=world.present
        if actions.get(frame)=="withdraw":
            world.remove_owner(frame,logs,"withdraw")
        world.receive(frame,states,logs)
        if repeats and was_present and not world.present:
            repeats.leave(frame,MOSTIMA,manual=actions.get(frame)=="withdraw")
        enhanced=bool(world.present and slot==3 and mode is not None and mode<=frame<finish)
        cells=wide if enhanced else normal_area
        for k in tuple(world.active.get(frame-1,()) | walks.keys()):
            if not living(k) or k in world.departed:
                walks.pop(k,None)
                continue
            pos=world.positions[frame-1].get(k)
            if pos is None:
                continue
            delta=modifiers[inside(k,pos,cells),enhanced] if world.present else 0
            if k not in walks:
                moving=any(r["instance_id"]==k and "active_ticks" in r for _,r in world.history.get(frame-1,()))
                if (not delta or not moving) and k not in pushes and frame>stuns.get(k,-1):
                    continue
                walks[k]=world.start_walk(tables,k,frame-1,delta)
            walk=walks[k]
            cap=movement_cap(move_attribute(walk.route["base_move_speed"],ratio_delta=delta),walk.route["level_move_multiplier"])
            forced=None
            if k in pushes:
                motion=step_push(tables,bodies[k],pushes[k],frame=frame,scene=world.scene,continuous=True)
                forced=motion.position
                if world.scene.tile(*(floor(n+.5) for n in forced))["tileKey"]=="tile_hole":
                    states[k].hp=0
                    logs.append(dict(kind="fall",frame=frame,target=k,hp_after_q32=0))
                    continue
                if motion.active:
                    pushes[k]=motion
                else:
                    del pushes[k]
            held=world.incoming and k in world.incoming.enemies and world.incoming.holding(k,frame)
            try:
                moving=walk.advance(world,frame,False,paused=bool(held or frame<=stuns.get(k,-1)),speed_cap=cap,forced_position=forced)
            except UnsupportedChannelScene:
                walks[k]=ControlledRoute(tables,world,k,frame,delta,prior=walk)
                world.positions[frame][k]=walk.position
                world.active[frame].add(k)
                moving=True
            if not moving:
                del walks[k]
                if (frame,k) not in world.exits:
                    world.exits[world.exit_index:]=sorted([*world.exits[world.exit_index:],(frame,k)])
        world.allied_impacts(frame,states,logs)
        if world.settle_exits(frame,living) is not None:
            stop="stage_defeat"
            break
        for k in world.active[frame]:
            if living(k):
                states[k].tick(frame)
        if not world.present:
            pulses.clear()
            pending=mode=None
        for c in controls.get(frame,()):
            if c["kind"] in ("disarm_on","disarm_off"):
                disarmed=c["kind"]=="disarm_on"
                if disarmed:
                    pending=None
                continue
            if c["kind"] in ("stunned","frozen","levitated","palsying","doze","interrupt"):
                disabled_until=c.get("end_frame",frame)
                pending=None
                poll=max(frame+1,disabled_until+1)
                if slot==2 and mode is not None and frame<finish:
                    pulses.intersection_update({frame} if c["phase"]=="after_pulse" else ())
                    mode=None
                    recovery=poll
                    logs.append(dict(kind="channel_interrupted",frame=frame,cause=c["kind"]))
        if mode==frame or finish==frame:
            poll=frame+1
            pending=None
            logs.append(dict(kind="skill_mode" if mode==frame else "skill_mode_end",frame=frame))
        channeling=slot==2 and mode is not None and mode<=frame<finish
        if world.present and frame>=recovery:
            gauge.state.advance(gauge.step)
        if world.present and frame>=poll and not channeling and not disarmed and frame>disabled_until:
            pool=[r for r in world.snapshot(frame) if valid(r["id"],frame) and inside(r["id"],(r["row"],r["col"]),cells)]
            if pool:
                target=world.select(pool,frame)
                pending=(frame+delays[enhanced],target["id"],enhanced)
                poll=frame+periods[enhanced]
            else:
                poll=frame+3
        pulse=frame in pulses
        if pulse or pending and pending[0]==frame:
            if pulse:
                kind="channel_hit"
                atk=mul(base_atk,pulse_scale)
            else:
                _,target,boosted=pending
                pending=None
                kind="skill_hit" if boosted else "ordinary_hit"
                atk=mul(base_atk,ONE+_raw_f32(board["atk"])) if boosted else base_atk
            centre=None if pulse else world.positions[frame].get(target)
            for k,pos in tuple(world.positions[frame].items()):
                if k not in world.active[frame] or not valid(k,frame):
                    continue
                if pulse or boosted:
                    if not inside(k,pos,cells) and (pulse or k!=target):
                        continue
                elif centre is None or (k!=target and hypot(pos[0]-centre[0],pos[1]-centre[1])>splash+radii[k]):
                    continue
                damage(frame,k,atk,kind)
                if not living(k):
                    continue
                enemy=world.scene.enemies[world.stats[k]["enemy_key"]]["attributes"]
                if pulse and not enemy["stunImmune"]["m_value"]:
                    fraction=.5 if "STATUS_RESISTANCE" in world.wave.flags.at(k,frame) else 1.
                    stuns[k]=frame+fixed_wait_frames(stun_seconds*fraction/dt)
                    if world.incoming and k in world.incoming.enemies:
                        world.incoming.stun(k,stuns[k])
                elif not pulse and boosted and not world.stats[k].get("airborne"):
                    if k not in bodies:
                        bodies[k]=enemy_push_profile(tables,world.stats[k]["prefab"])
                    motion=start_push(tables,bodies[k],pos,(pos[0]-place["row"],pos[1]-place["col"]),force_level=force,
                                      weight=world.stats[k]["mass_level"],frame=frame,previous=pushes.get(k))
                    if motion:
                        pushes[k]=motion
                        logs.append(dict(kind="push",frame=frame,target=k))
        if world.present and actions.get(frame)=="skill" and frame>=recovery and frame>disabled_until:
            cast=resolve_cast_start(tables,ev,frame=frame,phase="after_tick",sp=gauge.state.points,facing=face,direction=place["direction"])
            commit=cast["cast_frame"]
            if commit is not None:
                gauge.state.points=cast["sp_after"]
                mode=cast["spell_frame"] if slot==3 else commit
                finish=commit+clock["end_frame"]
                recovery=commit+clock["first_unblocked_recovery_tick"]
                pulses={commit+t for t in clock.get("channel_pulse_frames",())}
                pending=None
                poll=mode+1
                logs.append(dict(kind="skill_commit",frame=commit))
        if world.settled(frame,states,logs):
            stop="stage_victory_projected"
            break
    else:
        frame=end
        stop="horizon"
    return world.stage_result(dict(stage=stage["code"],skill=slot,operator_id=MOSTIMA,stop=stop,through_frame=frame,
                   remaining_hp_q32={k:s.hp for k,s in states.items()},events=logs),fixed_timestep=dt,target_field="target",hp_field="hp_after_q32")

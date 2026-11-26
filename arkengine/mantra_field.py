"""Mantra S3 field, overflow projectiles and ongoing attacks against stage targets."""
from collections import Counter,defaultdict
from math import floor

from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .elemental_casters import MANTRA,_board
from .elemental_projectiles import ranged_shot_release,stable_enemy_mount,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE,mul
from .mantra_battle import burst_output_scale
from .mantra_chain_stage import _next_receiver
from .mantra_stage import PalsyAura,palsy_delay,entry_recipient_tick,entry_talent
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .stage_deployment import RangedReentries
from .skill_recovery import RecoveryTimer,deploy_skill
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def run_mantra_field(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    allies=request.get("ally_skill_starts",())
    concealed=request.get("enemy_invisible",())
    card,stage,deploy,event,world=prepare_continuous_ranged(tables,request,MANTRA,releases,native_walk=False)
    presses={a["frame"] for a in request["actions"] if a.get("kind")=="skill"}
    withdrawals={a["frame"] for a in request["actions"] if a.get("kind")=="withdraw"}
    if card["skill"]!=3:
        raise ValueError("无言为真需要真言三技能")
    start,end=event["frame"],event["end_frame"]
    face,place=event["view"],event["placement"]
    ev=caster_prefab_evidence(tables,MANTRA,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    dt=tables.fixed_timestep()
    clock=run_committed_cast(tables,ev,horizon=0,facing=face,direction=deploy["direction"])["natural_end"]
    if clock["end_phase"]!="objects":
        raise ValueError("无言为真的退态相位发生变化")
    board={r["key"]:r["value"] for r in ev["skill_blackboard"]}
    buff,=(b for r in ev["skill"]["components"] if r["script"]=="ActiveBuffAbility" for b in r["fields"]["_buffs"]
           if b["templateKey"]=="mantra_s_3[self]")
    overflow_buff=ev["templates"]["mantra_s_3[enemy_trigger]"]["eventToActions"]
    flush=overflow_buff["ON_BUFF_FINISH"][0]["_succeedNodes"][-1]
    if flush["_loopCntKey"]!="remain" or not buff["waitFirstTriggerInterval"]:
        raise ValueError("无言为真的溢出收尾或首次付与时钟已变化")
    overflow_projectile=flush["_loopBody"][0]["_projectileKey"]
    over=ev["projectiles"][overflow_projectile]["base"]["components"]
    bounce,=(r["fields"] for r in over if r["script"]=="BouncedAdvancedMovement")
    radius,=(r["fields"]["m_Radius"] for r in over if r["type"]=="CircleCollider2D")
    source=_raw_f32(card["panel_attributes"]["atk"])
    power=mul(source,ONE+_raw_f32(board["atk"]))
    module_scale=burst_output_scale(card)
    talent=_board(card,0,"1")
    random=tables.combat_random(request.get("battle_random_seed",0))
    draws=Counter(r["frame"] for r in world.births.values())
    if random:
        for _ in range(sum(n for f,n in draws.items() if f<=start)*2):
            random.value()
    setup=entry_talent(tables,card,ev,world,deploy,stream=random)
    states={k:EnemyElements.from_scene_enemy(tables,dict(v,hp_q32=_raw_f32(v["hp"]))) for k,v in world.stats.items()}
    radii={k:tables.enemy_circle(v["prefab"]) for k,v in world.stats.items()}
    base_cells=range_cells(card["base_range"],place,event["rows"],event["cols"])
    wide_cells=range_cells(card["skill_range"],place,event["rows"],event["cols"])
    ally_frames=defaultdict(list)
    for ally in allies:
        frame,row,col=ally["frame"],ally["row"],ally["col"]
        if not start<frame<=end or not (0<=row<event["rows"] and 0<=col<event["cols"]):
            raise ValueError("友方施法回调须提供时窗内的帧和在场格")
        ally_frames[frame].append(ally)
    concealment=defaultdict(list)
    for row in concealed:
        key,lo,hi=row["id"],row["start_frame"],row["end_frame"]
        if key not in states or not start<=lo<=hi<=end:
            raise ValueError("隐匿状态需要实际目标和闭区间")
        concealment[key].append((lo,hi))
    ordinary=default_attack(tables,MANTRA)["fields"]
    enhanced,=(r["fields"] for r in ev["character"]["components"] if r["path"]=="Modes/S3/Body/Attack" and r["script"]=="RangedAttack")
    skill_parts={r["script"]:r["fields"] for r in ev["projectiles"][enhanced["_projectileKey"]]["logic"]["components"]}
    if enhanced["_selectTargetTiming"]!=1 or skill_parts["ChainedSelector"]["_maxTarget"]!=1:
        raise ValueError("无言为真攻击的重选或单弹目标上限已变化")
    speed={}
    for attack in (ordinary,enhanced):
        key=attack["_projectileKey"]
        speed[key]=next(r["fields"]["_speed"] for r in ev["projectiles"][key]["logic"]["components"] if r["script"]=="AdvancedMovement")
    stats=card["panel_attributes"]
    period_sec=single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"]))
    period=fixed_wait_frames(single(period_sec/dt))
    gauge,_=deploy_skill(tables,card,start)
    log=[dict(kind="deploy",frame=start)]
    mode_at=cast_at=ends_at=None
    recovery=start
    active=False
    sign=1 if deploy["direction"]!="LEFT" else -1
    poll=start+_birth(tables,card,ev,face)["born_exit_update"]+1
    born_end=poll-1
    pending=None
    grants=0
    self_grant=None
    members={}
    projectiles=[]
    aura=PalsyAura(ev,world)
    incoming=world.incoming
    if incoming:
        incoming.live_projectiles=True

    def alive(key):
        return states[key].hp>HP_EPSILON

    def result(frame,stop):
        payload=dict(stage=stage["code"],skill=3,operator_id=MANTRA,events=log,through_frame=frame,stop=stop,
                     palsy_grants=grants,entry_talent=setup,remaining_hp_q32={k:v.hp for k,v in states.items()},
                     palsy_layers={k:v.palsy_stacks for k,v in states.items()},
                     element_events={k:v.events for k,v in states.items() if v.events},sp=gauge.state.points)
        return world.stage_result(payload,fixed_timestep=dt,target_field="target",hp_field="hp_after_q32")

    def in_cells(key,row,cells):
        return circle_in_range(row["row"],row["col"],radii[key],cells,box_query=True)

    def concealed_at(key,frame):
        return any(lo<=frame<=hi for lo,hi in concealment.get(key,()))
    def visible(key,frame):
        flags=world.wave.flags.at(key,frame)
        return (not flags.intersection(("TARGET_FREE","INVINCIBLE","TOWER_TARGET_FREE"))
                and (key in members or not (concealed_at(key,frame) or "INVISIBLE" in flags)))

    def choose(frame,rows,count,*,completed=False):
        rows=list(rows)
        selected=[]
        for _ in range(min(len(rows),count)):
            actual={r["id"]:frame for r in rows} if completed else None
            row=world.select(rows,frame,frame_by_target=actual) if len(rows)>1 else rows[0]
            selected.append(row)
            rows.remove(row)
        return selected

    def launch_overflow(frame,key,origin):
        state=states[key]
        state.palsy_overflow-=1
        point=(origin["col"],origin["row"],origin["tile_z"])
        projectiles.append(dict(kind="overflow",born=frame,target=key,position=point,speed=bounce["_speed"],jump=False,root=key))
        log.append(dict(kind="overflow_release",frame=frame,target=key,remaining=state.palsy_overflow))

    def receive_grant(frame,reason):
        nonlocal grants
        if grants>=int(board["max_trigger_cnt"]):
            return
        rows=[k for k in native_box_candidates(world,world.active[frame]) if alive(k) and k in members and
              tuple(floor(x+.5) for x in world.positions[frame][k]) in wide_cells]
        if not rows:
            return
        grants+=1
        for key in rows:
            states[key].add_palsy(frame,int(board["per_active"]),"mantra_s_3")
        log.append(dict(kind="palsy_grant",frame=frame,reason=reason,grants_used=grants,
                        targets={k:states[k].palsy_stacks for k in rows}))

    delay=palsy_delay(tables)

    def interrupt_enemy(frame,key,position):
        state=states[key]
        if not state.palsy_stacks or state.palsy_immune:
            return True
        member=world.present and key in aura.members
        spent=not member or random.dice(1-talent["prob"])
        state.palsy_stacks-=spent
        incoming.stun(key,frame+fixed_wait_frames(.5/dt)+1)
        if member:
            state.apply_elemental_hp(frame,mul(power if active else source,_raw_f32(talent["atk_scale"])),
                source="mantra_t_1",output_scale_q32=module_scale if state.burst else ONE)
        log.append(dict(kind="enemy_attack_interrupted",frame=frame,target=key,stacks=state.palsy_stacks,hp_after_q32=state.hp))
        return False

    repeats=RangedReentries.for_request(tables,stage,world,request,start)
    for frame in range(start+1,end+1):
        new_place=repeats.advance(frame,log) if repeats else None
        if new_place:
            deploy=place=new_place
            face="Back" if place["direction"]=="UP" else "Front"
            base_cells=range_cells(card["base_range"],place,event["rows"],event["cols"])
            wide_cells=range_cells(card["skill_range"],place,event["rows"],event["cols"])
            clock=run_committed_cast(tables,ev,horizon=0,facing=face,direction=place["direction"])["natural_end"]
            gauge,_=deploy_skill(tables,card,frame)
            setup=entry_talent(tables,card,ev,world,place,stream=random)
            recovery=frame
            poll=frame+_birth(tables,card,ev,face)["born_exit_update"]+1
            born_end=poll-1
            mode_at=cast_at=ends_at=pending=self_grant=None
            grants=0
            active=False
            sign=-1 if place["direction"]=="LEFT" else 1
            aura.mode=None
            log.append(dict(kind="deploy",frame=frame))
        was_present=world.present
        if world.settle_exits(frame,alive) is not None:
            return result(frame,"stage_defeat")
        if frame>=recovery:
            gauge.state.advance(gauge.step)
        if frame==cast_at:
            gauge.state.points-=gauge.state.cost
            log.append(dict(kind="skill_commit",frame=frame,sp=gauge.state.points))
        if world.present and frame==mode_at:
            grants=0
            active=True
            self_grant=RecoveryTimer(_raw_f32(buff["firstTriggerInterval"]))
            poll=frame+1
            log.append(dict(kind="skill_mode",frame=frame))
        if frame==ends_at:
            active=False
            pending=None
            poll=frame+1
            log.append(dict(kind="skill_mode_end",frame=frame))
        if frame in withdrawals:
            world.remove_owner(frame,log,"withdraw")
        aura.advance(frame,gauge.step,3 if active else 0,concealed_at)
        world.receive(frame,states,log,before_fire=interrupt_enemy,
                      interrupt_delay=lambda key:delay if states[key].palsy_stacks else None)
        if repeats and was_present and not world.present:
            repeats.leave(frame,MANTRA,manual=frame in withdrawals)
        world.allied_impacts(frame,states,log)
        if not world.present:
            active=False
            pending=poll=None
        if random:
            for _ in range(draws[frame]*2):
                random.value()

        live={k:world.receiver_at(k,frame) for k in native_box_candidates(world,world.active[frame]) if alive(k)}
        for key,state in states.items():
            if world.births[key]["frame"]<frame and alive(key):
                entry_recipient_tick(setup,key,state,frame,world)
                state.tick(frame)
        field={k:row for k,row in live.items() if active and row and not world.wave.flags.at(k,frame).intersection(("TARGET_FREE","INVINCIBLE","TOWER_TARGET_FREE")) and in_cells(k,row,wide_cells)}
        for key in tuple(members):
            if key not in field:
                last,_,_=members.pop(key)
                state=states[key]
                while state.palsy_overflow:
                    launch_overflow(frame,key,live.get(key,last))
                state.limit_palsy(None)
                log.append(dict(kind="field_detached",frame=frame,target=key))
        for key,row in field.items():
            if key not in members:
                states[key].limit_palsy(int(board["max_target"]))
                members[key]=(row,RecoveryTimer(_raw_f32(.1)),None)
                log.append(dict(kind="field_attached",frame=frame,target=key,revealed=True))
        if active:
            if self_grant and frame>mode_at and self_grant.advance(gauge.step):
                receive_grant(frame,"self")
                self_grant=None
            for ally in ally_frames[frame]:
                if (ally["row"],ally["col"]) in wide_cells:
                    receive_grant(frame,ally.get("operator_id","ally"))
        for key,row in field.items():
            _,timer,elapsed=members[key]
            if timer.advance(gauge.step):
                if elapsed is None or elapsed>board["interval_projectile_trigger"]:
                    elapsed=None
                    if states[key].palsy_overflow:
                        launch_overflow(frame,key,row)
                        elapsed=0.
                else:
                    elapsed=single(elapsed+single(.1))
            members[key]=(row,timer,elapsed)

        for flight in tuple(projectiles):
            if flight["born"]==frame:
                continue
            key=flight["target"]
            target=world.by_id(key,frame,projectile_born=flight["born"]) if alive(key) else None
            if target is not None:
                move=ordinary_trace_step(flight["position"],stable_enemy_mount(tables,target),effective_dt=dt,
                                          round_position=True,speed=flight["speed"])
                flight["position"]=move.position
                if not move.reached:
                    continue
            elif flight["kind"]!="overflow" or flight["jump"]:
                projectiles.remove(flight)
                continue
            state=states[key]
            attack=power if active else source
            if target is not None:
                if flight["kind"]=="overflow":
                    item=state.apply_elemental_hp(frame,mul(attack,_raw_f32(board["atk_scale"])),source="mantra_s_3_overflow",
                                                 output_scale_q32=module_scale if state.burst else ONE)
                else:
                    item=state.receive_ordinary(frame,source=MANTRA,damage_type="arts",attack_q32=attack,
                        mitigation_q32=_raw_f32(target["res"]),output_scale_q32=module_scale if state.burst else ONE)
                log.append(dict(kind="overflow_hit" if flight["kind"]=="overflow" else "attack_hit",frame=frame,target=key,
                                jump=flight["jump"],damage_q32=item["damage_q32"],hp_after_q32=state.hp))
            if flight["kind"]=="overflow" and not flight["jump"] and int(board["chain_times"]):
                center=dict(row=flight["position"][1],col=flight["position"][0])
                other=_next_receiver(frame,center,[flight["root"]],world,states,radii,radius,flight["born"],eligible=visible)
                if other is not None:
                    flight.update(target=other,speed=bounce["_speedAfterFirstReach"],jump=True)
                    log.append(dict(kind="overflow_bounce",frame=frame,from_target=key,target=other))
                    continue
            projectiles.remove(flight)

        if frame==poll:
            cells=wide_cells if active else base_cells
            rows=[r for k in world.stats if alive(k) and visible(k,frame) and (r:=world.by_id(k,frame)) is not None and in_cells(k,r,cells)]
            if rows:
                primary=choose(frame,rows,1)[0]
                if primary["col"]!=place["col"]:
                    sign=1 if primary["col"]>place["col"] else -1
                ability=enhanced if active else ordinary
                shot=ranged_shot_release(tables,ev,ability,attack_frame=frame,attack_interval=period_sec,
                                        facing=face,mode_path="Modes/S3" if active else None)
                pending=shot["release_frame"],shot,ability,primary,sign
                poll=frame+period
                log.append(dict(kind="attack_start",frame=frame,target=primary["id"],skill=active))
            else:
                poll+=3
        if pending and pending[0]==frame:
            _,shot,ability,primary,face_sign=pending
            pending=None
            if ability is enhanced:
                rows=[row for k,row in live.items() if alive(k) and visible(k,frame) and in_cells(k,row,wide_cells)]
                selected=choose(frame,rows,int(board["attack@max_target"]),completed=True)
            else:
                key=primary["id"]
                selected=[live[key]] if key in live and alive(key) and visible(key,frame) else []
            for row in selected:
                origin,_=stationary_cast_mounts(tables,MANTRA,shot["clock"],place,row,face,caster_face_sign=face_sign)
                key=ability["_projectileKey"]
                projectiles.append(dict(kind="normal",born=frame,target=row["id"],position=origin,speed=speed[key],jump=False))
                log.append(dict(kind="projectile_release",frame=frame,target=row["id"],projectile=key))
        if world.present and frame in presses and frame>=recovery:
            cast=resolve_cast_start(tables,ev,frame=frame,phase="after_tick",sp=gauge.state.points,facing=face,
                                    direction=deploy["direction"],owner_state="born" if frame<born_end else "idle")
            if cast["spell_frame"] is not None:
                cast_at,mode_at=cast["cast_frame"],cast["spell_frame"]
                ends_at,recovery=cast_at+clock["end"],cast_at+clock["first_recovery_tick"]
                pending=poll=None
            else:
                log.append(dict(kind="skill_rejected",frame=frame,reason=cast["status"]))
        if world.settled(frame,states,log):
            return result(frame,"stage_victory_projected")
    return result(end,"horizon")

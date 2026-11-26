"""Logos S1 execution aura and S3 projectile mode on the native stage clock."""
from collections import Counter

from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_first_spell import _selected_clip,attack_key_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON
from .elemental_casters import _board
from .elemental_projectiles import stable_enemy_mount,stationary_cast_mounts
from .enemy_elements import EnemyElements
from .fixed import ONE,mul
from .logos_lock import LOGOS,lock_arts,talent_launch
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .skill_recovery import RecoveryTimer,deploy_skill
from .sluggish_wave import SluggishWave
from .target_priority import native_box_candidates
from .trace_motion import ordinary_trace_step,paracurve_horizontal_step
from .volley_damage import _raw_f32


def run_logos_mode_stage(tables,request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    pick,=request["squad"]
    slot=pick["skill"]
    if slot not in (1,3):
        raise ValueError("此入口需要逻各斯一技能或三技能")
    base=dict(request)
    controls={e["frame"]:e["state"] for e in base.pop("caster_control",())}
    card,stage,deploy,event,world=prepare_continuous_ranged(tables,base,LOGOS,releases,native_walk=slot==3)
    start,end,face=event["frame"],event["end_frame"],event["view"]
    buttons={a["frame"] for a in base["actions"] if a.get("kind")=="skill"}
    retreats={a["frame"] for a in base["actions"] if a.get("kind")=="withdraw"}
    ev=caster_prefab_evidence(tables,LOGOS,slot,skill_level=card["skill_level"],module_id=card["module"],
                              module_level=card["module_level"])
    board={r["key"]:r["value"] for r in ev["skill_blackboard"]}
    parts=ev["character"]["components"]
    normal=default_attack(tables,LOGOS)["fields"]
    skill,=(r["fields"] for r in parts if r["path"]==f"Modes/S{slot}/Body/Attack" and r["script"]=="RangedAttack")
    if skill["_useCachedAtkOnly"] or skill["_selectTargetTiming"]!=0 or not skill["_waitForAttackEvent"]:
        raise ValueError("逻各斯技能攻击的伤害缓存或锁定时点已变化")
    replacing,=(r["fields"] for r in parts if r["path"]==f"Modes/S{slot}" and r["script"]=="SimpleUnitAnimatorHooker")
    skill_anim=next(row["toAnimKey"] for row in replacing["_replaceAnimPairs"] if row["fromAnimKey"]=="Attack")
    dt=tables.fixed_timestep()
    gauge,_=deploy_skill(tables,card,start)
    points=gauge.state
    stats=card["panel_attributes"]
    attack=_raw_f32(stats["atk"])
    boosted=mul(attack,ONE+_raw_f32(board["atk"]))
    interval=single(single(stats["baseAttackTime"])*100/single(stats["attackSpeed"]))
    period=fixed_wait_frames(single(interval/dt))
    clocks={action:attack_key_clock(tables,ev,face,action,interval) for action in ("Attack","Attack_2",skill_anim)}
    spells={r["fields"]["_projectileKey"] for r in parts if r["path"] in ("Modes/Default/Body/Attack",f"Modes/S{slot}/Body/Attack")
            and r["script"]=="RangedAttack"}
    spell_profiles={}
    for key in spells:
        mover,=(c for c in ev["projectiles"][key]["logic"]["components"]
                if c["script"] in ("AdvancedMovement","ParacurveMovement"))
        spell_profiles[key]=(mover["script"]=="ParacurveMovement",mover["fields"]["_speed"])
    talent=_board(card,0,"11_root") or _board(card,0,"1")
    talent_parts={r["script"]:r["fields"] for r in ev["projectiles"]["projectile_chr_logos_t"]["logic"]["components"]}
    stream=tables.combat_random(request.get("battle_random_seed",0))
    spawn_draws=Counter(row["frame"] for row in world.births.values())
    if stream:
        for frame,count in spawn_draws.items():
            if frame<=start:
                for _ in range(2*count):
                    stream.value()
    grant=int(_trait_values(card).get("sp",0))
    normal_cells=range_cells(card["base_range"],deploy,event["rows"],event["cols"])
    skill_cells=range_cells(card["skill_range"],deploy,event["rows"],event["cols"])
    radii={key:tables.enemy_circle(row["prefab"]) for key,row in world.stats.items()}
    states={key:EnemyElements.from_scene_enemy(tables,dict(row,hp_q32=_raw_f32(row["hp"]))) for key,row in world.stats.items()}
    walks=SluggishWave(tables,world)
    slow_ends={}
    retimed={}
    shots=[]
    aura={}
    logs=[dict(kind="deployment",frame=start,sp=points.points)]
    incoming=world.incoming
    if incoming and slot==3:
        incoming.live_projectiles=True
    mode_at=cast_at=ending_at=restore_at=clear_at=None
    recovery=start
    active=False
    field_on=False
    attack_index=mount_index=0
    sign=1 if deploy["direction"]!="LEFT" else -1
    search=start+_birth(tables,card,ev,face)["born_exit_update"]+1
    born_end=search-1
    control="clear"
    pending=None
    if slot==1:
        halo,=(r["fields"] for r in parts if r["path"]=="Modes/S1/Body/GeneralAbilities/Ability0" and r["script"]=="AuraAbility")
        mark,=halo["_buffs"]
        actions=ev["templates"][mark["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        emitter=actions[-1]
        if (not halo["_removeBuffWhenTargetLeave"] or mark["waitFirstTriggerInterval"] or
                emitter["_startPointTarget"]!="BUFF_OWNER" or emitter["_mountPoint"]!="HIT" or
                not emitter["_actions"][0]["_triggerOnCalculateDamage"]):
            raise ValueError("殁亡的范围附着或派生伤害配置已变化")
        secondary=next(r["fields"]["_speed"] for r in ev["projectiles"]["projectile_chr_logos_s1_2"]["logic"]["components"]
                       if r["script"]=="ParacurveMovement")
    else:
        timing=run_committed_cast(tables,ev,horizon=0,facing=face,direction=deploy["direction"])["natural_end"]
        end_ability,=(r["fields"] for r in parts if r["script"]=="EliminateProjectileAbility")
        end_anim,_=_selected_clip(tables,ev,end_ability["_animKey"],face)
        clear_delay,_=attack_key_clock(tables,ev,face,end_ability["_animKey"],end_anim["duration"])
        restore_delay=fixed_wait_frames(single(end_anim["duration"]/dt))
        validator,=(r["fields"] for r in ev["skill"]["components"] if r["script"]=="ProjectileValidator")
        switch_end,=ev["templates"]["logos_s_3"]["eventToActions"]["ON_BUFF_FINISH"]
        if validator["_targetSide"]!=2 or switch_end["_succeedNodes"][0]["_modeIndex"]!=4:
            raise ValueError("延异视阈的敌方弹体筛选或结束模式已变化")

    def report(frame,stop,**extra):
        result=dict(stage=stage["code"],operator_id=LOGOS,skill=slot,stop=stop,through_frame=frame,events=logs,
                    sp=points.points,remaining_hp_q32={k:v.hp for k,v in states.items()},
                    element_events={k:v.events for k,v in states.items() if v.events},random_draws=stream.draws if stream else None,**extra)
        return world.stage_result(result,fixed_timestep=dt,target_field="target",hp_field="hp_after_q32")

    def living(key):
        return states[key].hp>HP_EPSILON

    def targets(frame,*,completed=False):
        cells=skill_cells if active else normal_cells
        rows=([world.receiver_at(key,frame) for key in native_box_candidates(world,world.active[frame])]
              if completed else world.snapshot(frame))
        return [row for row in rows
                if row and living(row["id"]) and world.selectable(row["id"],frame) and circle_in_range(row["row"],row["col"],radii[row["id"]],cells,box_query=True)]

    def in_projectile_field(position):
        return circle_in_range(position[1],position[0],0,skill_cells)

    def start_sluggish(key,frame):
        if key in retimed or frame>=end:
            return
        retimed[key]=world.start_walk(tables,key,frame,walks.modifier)

    def fire_talent(frame):
        success=stream.dice(talent["prob"])
        logs.append(dict(kind="first_talent_dice",frame=frame,triggered=success))
        if not success:
            return True
        ids=native_box_candidates(world,(r["id"] for r in targets(frame,completed=True)))
        if not ids:
            return True
        for _ in range(int(talent.get("emit_count",1))):
            key,=stream.select_targets(ids,1)
            enemy=world.receiver_at(key,frame)
            draw=dict(mount=stream.range_int(0,6),birth_delay_seconds=single(stream.value()*talent_parts["SimpleProjectile"]["_randomDelayToBorn"]))
            _,origin,_,first,_=talent_launch(tables,card,ev,dict(event,enemy=enemy),frame,draw,caster_face_sign=sign)
            shots.append(dict(target=key,position=origin[:2],curve=True,speed=talent_parts["ParacurveMovement"]["_speed"],
                              first=first,born=frame,kind="talent_impact",fixed=None))
            logs.append(dict(kind="talent_projectile",frame=frame,target=key,mount=draw["mount"],first_move_frame=first))
        return True

    for frame in range(start+1,end+1):
        if world.present and frame>=recovery:
            points.advance(gauge.step)
        if frame in controls:
            control=controls[frame]
            if control!="clear":
                pending=None
                if clear_at is not None and frame<=clear_at:
                    field_on=False
                    clear_at=None
                    restore_at=frame
            else:
                search=frame+1
        if frame==cast_at:
            points.points-=points.cost
            logs.append(dict(kind="skill_commit",frame=frame,sp=points.points))
        if frame==mode_at:
            active=True
            search=frame+1
            logs.append(dict(kind="skill_mode",frame=frame,skill=slot))
        if frame in retreats:
            world.remove_owner(frame,logs,"withdraw")
        world.receive(frame,states,logs,field_scale=lambda p:board["projectile_move_scale"] if field_on and in_projectile_field(p) else 1)
        if not world.present:
            active=field_on=False
            clear_at=mode_at=ending_at=restore_at=pending=search=None
        for key,walk in tuple(retimed.items()):
            if not living(key):
                del retimed[key]
                continue
            updated=world.step_walk(tables,walk,key,frame,walks.modifier,slowed=frame<=slow_ends[key])
            if updated is None:
                del retimed[key]
                slow_ends.pop(key,None)
            else:
                retimed[key]=updated
        if world.settle_exits(frame,living) is not None:
            return report(world.defeated_at,"stage_defeat")
        for key,last in tuple(slow_ends.items()):
            if key not in retimed and key in world.active[frame] and living(key) and frame<last:
                start_sluggish(key,frame)
        if frame==mode_at and slot==3:
            field_on=True
        if stream:
            for _ in range(2*spawn_draws[frame]):
                stream.value()
        for key,state in states.items():
            if living(key) and (key in world.active[frame] or state.last_tick is not None):
                prior=states[key].hp
                states[key].tick(frame)
                if prior!=states[key].hp:
                    logs.append(dict(kind="element_tick",frame=frame,target=key,hp_after_q32=states[key].hp))
        world.allied_impacts(frame,states,logs)
        for shot in tuple(shots):
            if frame<shot["first"]:
                continue
            key=shot["target"]
            target=world.by_id(key,frame,projectile_born=shot["born"]) if living(key) else None
            if target is None:
                shots.remove(shot)
                continue
            mount=stable_enemy_mount(tables,target)
            if shot["curve"]:
                shot["position"],hit=paracurve_horizontal_step(shot["position"],mount,shot["speed"],dt,True)
            else:
                motion=ordinary_trace_step(shot["position"],mount,effective_dt=dt,round_position=True,speed=shot["speed"])
                shot["position"],hit=motion.position,motion.reached
            if not hit:
                continue
            shots.remove(shot)
            kind=shot["kind"]
            atk=boosted if active else attack
            amount=shot["fixed"] if shot["fixed"] is not None else mul(atk,_raw_f32(talent["atk_scale"])) if kind=="talent_impact" else atk
            damage=lock_arts(states[key],frame,card,amount,_raw_f32(target["res"]),kind)
            if kind=="talent_impact" and living(key):
                if states[key].burst=="DARK" and "element_atk_scale" in talent:
                    states[key].apply_elemental_hp(frame,mul(atk,_raw_f32(talent["element_atk_scale"])),source="logos_delta_talent",source_card=card)
                if living(key):
                    ticks=fixed_wait_frames(single(talent["sluggish"]/dt))
                    slow_ends[key]=frame+ticks
                    start_sluggish(key,frame)
            if world.present and frame>=recovery and target["level"] in ("ELITE","BOSS"):
                points.points=min(points.maximum,points.points+grant)
            logs.append(dict(kind=kind,frame=frame,target=key,damage_q32=damage,hp_after_q32=states[key].hp))
        if slot==1 and active:
            inside={r["id"]:r for r in targets(frame,completed=True)}
            for key in tuple(aura):
                if key not in inside:
                    del aura[key]
            for key,row in inside.items():
                if key not in aura:
                    timer=RecoveryTimer(_raw_f32(mark["triggerInterval"]))
                    timer.remaining=0
                    aura[key]=timer
                    continue
                if (aura[key] is None or not aura[key].advance(gauge.step) or
                        not HP_EPSILON<states[key].hp<mul(boosted,_raw_f32(board["attack@kill_atk_scale"]))):
                    continue
                saved=states[key].hp
                states[key].hp=max(0,saved-_raw_f32(board["attack@kill_damage"]))
                aura[key]=None
                logs.append(dict(kind="execution",frame=frame,target=key,hp_before_q32=saved,hp_after_q32=states[key].hp))
                if living(key):
                    continue
                fire_talent(frame)
                peers=native_box_candidates(world,(other for other in inside if living(other)))
                if peers:
                    other,=stream.select_targets(peers,1)
                    point=stable_enemy_mount(tables,row)
                    shots.append(dict(target=other,position=point[:2],curve=True,speed=secondary,first=frame+1,
                                      born=frame,kind="execution_projectile_hit",fixed=saved))
                    logs.append(dict(kind="execution_projectile",frame=frame,from_target=key,target=other))
        if slot==3 and frame==ending_at:
            active=False
            pending=None
            if control=="clear":
                clear_at=frame+1+clear_delay
                restore_at=frame+1+restore_delay
            else:
                restore_at=frame
                field_on=False
            logs.append(dict(kind="skill_expired",frame=frame,can_eliminate=control=="clear"))
        if frame==clear_at:
            cleared=incoming.eliminate(in_projectile_field) if incoming and control=="clear" else []
            logs.append(dict(kind="projectile_elimination",frame=frame,enabled=control=="clear",cleared=cleared))
            field_on=False
        if frame==restore_at:
            search=frame+1
            logs.append(dict(kind="restore_default_mode",frame=frame))
        if world.present and control=="clear" and frame==search:
            rows=targets(frame)
            if not rows:
                search+=3
            else:
                chosen=[]
                limit=int(board.get("attack@max_target",1)) if active else 1
                for _ in range(min(limit,len(rows))):
                    target=world.select(rows,frame) if len(rows)>1 else rows[0]
                    chosen.append(target["id"])
                    rows.remove(target)
                    if len(chosen)==1 and target["col"]!=deploy["col"]:
                        sign=1 if target["col"]>deploy["col"] else -1
                action=skill_anim if active else ("Attack","Attack_2")[attack_index%2]
                attack_index+=not active
                delay,clock=clocks[action]
                pending=frame+delay,chosen,clock,skill if active else normal,sign
                search=frame+period
                logs.append(dict(kind="attack_start",frame=frame,targets=chosen,release_frame=frame+delay,action=action))
        if pending and pending[0]==frame:
            _,chosen,clock,ability,face_sign=pending
            pending=None
            projectile=ability["_projectileKey"]
            curve,speed=spell_profiles[projectile]
            for key in chosen:
                enemy=world.receiver_at(key,frame) if living(key) else None
                if enemy is None:
                    continue
                mount=2
                if ability["_useMountGroup"]:
                    mounts=ability["_mountGroup"]["mountPoints"]
                    mount=mounts[mount_index%len(mounts)]
                    mount_index+=1
                field="muzzlaTransform" if mount==2 else "specialPoint"+str(mount-6)
                origin,_=stationary_cast_mounts(tables,LOGOS,clock,event["placement"],enemy,face,
                                                mount=mount,field=field,caster_face_sign=face_sign)
                shots.append(dict(target=key,position=origin[:2] if curve else origin,curve=curve,speed=speed,
                                  first=frame+1,born=frame,kind="ordinary_impact",fixed=None))
                logs.append(dict(kind="projectile_release",frame=frame,target=key,projectile=projectile,mount=mount))
                fire_talent(frame)
        requested=(slot==1 and mode_at is None and points.points>=points.cost and frame>=born_end or
                   slot==3 and frame in buttons and not active and (mode_at is None or frame>mode_at))
        if world.present and requested and control in ("clear","disarm"):
            cast=resolve_cast_start(tables,ev,frame=frame,phase="before_coroutines" if slot==1 else "after_tick",
                                    facing=face,direction=deploy["direction"],sp=points.points)
            if cast["spell_frame"] is not None:
                cast_at,mode_at=cast["cast_frame"],cast["spell_frame"]
                pending=search=None
                recovery=end+1 if slot==1 else cast_at+timing["first_recovery_tick"]
                if slot==3:
                    ending_at=cast_at+timing["end"]+1
                logs.append(dict(kind="skill_requested",frame=frame,sp=points.points))
        if world.settled(frame,states,logs):
            return report(frame,"stage_victory_projected")
    return report(end,"horizon")

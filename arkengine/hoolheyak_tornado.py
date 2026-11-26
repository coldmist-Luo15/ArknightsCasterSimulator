"""Moving tornadoes in Ho'olheyak's third skill on an ordinary, unblocked ground wave."""
from dataclasses import dataclass

from .attack_range import circle_in_range, range_cells
from .baked_mount import baked_position, single
from .caster_equipment_traits import _trait_values
from .caster_first_spell import _selected_clip
from .caster_opening import deploy_to_first_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_runtime import default_attack
from .core_caster_skills import _talent
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release,stable_enemy_mount,stationary_cast_mounts
from .hoolheyak_curve import tornado_scale
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .hoolheyak_stage import _WalkingWave, _magic_aura, _mass_aura, _vulnerability
from .levitate import ordinary_levitate
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .ranged_stage import prepare_ranged_stage
from .stage_channel import object_position_frame
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


OPERATOR = "char_4027_heyak"
PROJECTILE = "projectile_chr_heyak_s3"


@dataclass
class Tornado:
    volley: int
    mount: int
    emission: int
    born: int
    centers: set
    mount_candidates: tuple
    age: int = 0


def _profile(tables, card, evidence, view):
    components = evidence["character"]["components"]
    attack, = (c["fields"] for c in components if c["path"]=="Modes/S3/Body/Attack" and c["script"]=="AnimatedActionToOwnerAbility")
    group, = (c["fields"] for c in components if c["script"]=="AbilityParallelGroup" and c["path"].endswith("/WindAttack"))
    by_id = {c["id"]:c for c in components}
    children = [by_id[p["m_PathID"]] for p in group["_abilities"]]
    if (not attack["_faceToDefault"] or attack["_waitForAttackEvent"] or attack["_timeMode"]!=0
            or not all(c["script"]=="RangedAttack" and c["fields"]["_projectileKey"]==PROJECTILE
                       and c["fields"]["_timeMode"]==2 and c["fields"]["_cooldown"]==0
                       and c["fields"]["_waitForAttackEvent"] and not c["fields"]["_animKey"] for c in children)):
        raise ValueError("旋风触发动作、并行发射或者子能力等待条件已变更")
    parts = {c["script"]:c["fields"] for c in evidence["projectiles"][PROJECTILE]["logic"]["components"]}
    life, hit, movement = (parts[n] for n in ("SimpleProjectile","SelectorHitBehaviour","FarthestPointMovement"))
    collider = parts["BoxCollider2D"]
    selector, = (c for c in evidence["projectiles"][PROJECTILE]["logic"]["components"]
                 if c["id"] == hit["_selector"]["m_PathID"])
    select = selector["fields"]
    if (life["_hitNumType"]!=1 or life["_maxHitNum"]!=1 or life["_lifeTimeType"]!=1 or
            life["_stopWhenSourceInvalid"] or life["_managedBySource"] or
            movement["_moveType"]!=4 or not movement["_useSourceDirection"] or movement["_delayToStart"] or
            not hit["_hitPasserby"] or hit["_waitFirstPeriod"] or hit["_interval"]>=tables.fixed_timestep() or
            collider["m_Size"]!={"x":1.,"y":1.} or any(collider["m_Offset"].values()) or
            selector["script"]!="AdvancedSelector" or (select["_postFilter"],select["_maxNum"],select["_limitTargetNum"])!=(4,1,1)
            or select["_sortByTauntAtLast"] or select["_ignoreTargetFree"]):
        raise ValueError("旋风寿命、大小、移动或命中筛选已变化")
    # Component order is retained in the projectile's GetComponents array. Its selector runs
    # before the movement writes the new position. The age has already advanced by that point.
    ordered = [c["script"] for c in evidence["projectiles"][PROJECTILE]["logic"]["components"]]
    if ordered.index("SelectorHitBehaviour")>ordered.index("FarthestPointMovement"):
        raise ValueError("旋风索敌与移动更新顺序已变化")
    tracks = tables.read("bakemuzzledata/character/"+OPERATOR+"_defaultskin.json")["bakedAnimDatas"][view]["bakedMPDatas"]
    mounts = []
    for child in children:
        mount = child["fields"]["_mountPointType"]
        track = tracks[attack["_animKey"]][str(mount)]
        offset = baked_position(track,0,tables.fixed_timestep())
        if track["frameCount"]!=1 or offset[1:]!=(0.,0.):
            raise ValueError("旋风发射挂点在动作内移动")
        mounts.append((mount,offset[0]))
    clip,_ = _selected_clip(tables,evidence,attack["_animKey"],view)
    board = {r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    panel = card["panel_attributes"]
    interval = single(single(panel["baseAttackTime"]+board["base_attack_time"])*100/panel["attackSpeed"])
    scale = min(attack["_maxAnimScale"],max(single(.1),single(interval/single(clip["duration"]))))
    pre = single(scale*attack["_preDelay"])
    curve = parts["ScaleAtkScaleViaTimeRatioBehaviour"]["_scaleCurve"]["m_Curve"]
    first, plateau, last = curve
    if ([k["value"] for k in curve]!=[0.,1.,1.] or first["time"]!=0. or last["time"]!=1.
            or not 0.<plateau["time"]<1. or first["outSlope"]!=plateau["inSlope"]
            or not 0.<first["outSlope"]*plateau["time"]<3. or any(k["weightedMode"] for k in curve)
            or plateau["outSlope"]!={"unity_float":"+Infinity"}):
        raise ValueError("旋风伤害曲线的单调段或平台发生变化")
    return board, mounts, pre, interval, life, movement, curve


def run_tornado_stage(tables, request):
    withdrawals = [a for a in request["actions"] if a.get("kind") == "withdraw"]
    if len(withdrawals)>1:
        raise ValueError("旋风回放只支持一次霍尔海雅撤退")
    withdrawal = next(iter(withdrawals), None)
    base = dict(request, actions=[a for a in request["actions"] if a is not withdrawal])
    card, stage, deploy, encounter, world = prepare_ranged_stage(tables,base,operator=OPERATOR,manual=True,
        portable=True,track_exits=True,simulate_before_button=True,allow_flying=True)
    if withdrawal and (withdrawal.get("operator_id") != OPERATOR or
                       not encounter["press_frame"] < withdrawal["frame"] <= request["end_frame"] or
                       withdrawal.get("phase") not in ("before_coroutines","after_tick")):
        raise ValueError("旋风撤退需要开技后的 before_coroutines 或 after_tick 指令")
    if card["skill"]!=3:
        raise ValueError("此弹道属于霍尔海雅三技能")
    evidence = caster_prefab_evidence(tables,OPERATOR,3,skill_level=card["skill_level"],module_id=card["module"],
                                      module_level=card["module_level"])
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    view = encounter["view"]
    board,mounts,pre,interval,life,movement,curve = _profile(tables,card,evidence,view)
    direction = deploy["direction"]
    dr,dc = dict(UP=(1,0),DOWN=(-1,0),LEFT=(0,-1),RIGHT=(0,1))[direction]
    end = request["end_frame"]
    opening = deploy_to_first_cast(tables,card,evidence,deploy_frame=deploy["frame"],direction=direction,facing=view,
        end_frame=end,manual_phase=encounter["press_phase"],next_manual_input=lambda earliest,born:encounter["press_frame"])
    cast = opening.cast
    log = [dict(kind="deployed",frame=deploy["frame"])]
    if not cast or cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("按键没有进入霍尔海雅三技能模式")
    log.extend(e for e in cast["events"] if e["frame"]<=end)
    mode = cast["spell_frame"]
    cells = range_cells(card["skill_range"],deploy,encounter["rows"],encounter["cols"])
    radii = {k:tables.enemy_circle(e["prefab"]) for k,e in world.stats.items()}
    states = {k:EnemyElements.from_scene_enemy(tables,dict(e,hp_q32=_raw_f32(e["hp"]))) for k,e in world.stats.items()}
    ordinary_cells=range_cells(card["base_range"],deploy,encounter["rows"],encounter["cols"])
    wave = _WalkingWave(world,states,ordinary_cells,radii,step)
    mass_aura = _mass_aura(tables,card,evidence,wave,deploy["frame"],mode="Default")
    rng = tables.combat_random(request["battle_random_seed"])
    talent = _talent(tables,card,0)
    trait = _trait_values(card)
    magic_aura = _magic_aura(tables,evidence,wave,deploy["frame"],trait)
    ordinary_attack=default_attack(tables,OPERATOR)["fields"]
    parts=evidence["projectiles"][ordinary_attack["_projectileKey"]]["logic"]["components"]
    ordinary_speed,=(c["fields"]["_speed"] for c in parts if c["script"]=="AdvancedMovement")
    panel=card["panel_attributes"]
    ordinary_interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]))
    ordinary_period=fixed_wait_frames(single(ordinary_interval/dt))
    ordinary_poll=deploy["frame"]+_birth(tables,card,evidence,view)["born_exit_update"]+1
    ordinary_pending=None
    ordinary_flights=[]
    ordinary_sign=1 if direction!="LEFT" else -1
    next_search = mode+1
    pending, launches = [], {}
    volley = 0
    active_through = mode+fixed_wait_frames(single(single(card["skill_row"]["duration"])/dt))-1
    until = end
    on_field = True
    if withdrawal and not mode<withdrawal["frame"]<=active_through:
        raise ValueError("此撤退输入需要三技能仍在生效")
    result = dict(stage=stage["code"],deployment=deploy,operator_id=OPERATOR,skill=3,module=card["module"],
                  status="running",events=log,through_frame=min(mode,end),source=tables.source)
    def finish(frame,stop):
        result.update(stop=stop,through_frame=frame,status=stop,remaining_hp_q32={k:s.hp for k,s in states.items()},
                      rng=dict(seed=request["battle_random_seed"],draws=rng.draws),remaining_life=world.life,escapes=world.escaped)
        return result

    def advance_ordinary(frame,*,earlier_than):
        for shot in tuple(ordinary_flights):
            born,key=shot["release_frame"],shot["target"]
            if born>=earlier_than or shot.get("advanced")==frame:
                continue
            shot["advanced"]=frame
            receiver=wave.by_id(key,frame,projectile_born=born) if states[key].hp>HP_EPSILON else None
            if receiver is None:
                ordinary_flights.remove(shot)
                continue
            x,y,z=stable_enemy_mount(tables,receiver)
            travel=ordinary_trace_step(shot["position"],(x,y,z+receiver["height_offset"]),effective_dt=dt,
                                       round_position=True,speed=ordinary_speed)
            shot["position"]=travel.position
            if not travel.reached:
                continue
            airborne=receiver.get("airborne",False) or wave.elevated(key,frame)
            scale=talent["atk_scale"] if on_field and airborne else 1
            pen=_raw_f32(trait.get("magic_resist_penetrate_fixed",0)) if on_field else 0
            low,high=_vulnerability(magic_aura,key,trait,before_update=world.births[key]["frame"]>born)
            if low!=high:
                return key
            packet=states[key].receive_ordinary(frame,source=OPERATOR,damage_type="arts",
                attack_q32=mul(_raw_f32(panel["atk"]),_raw_f32(scale)),
                mitigation_q32=max(0,_raw_f32(receiver["res"])-pen),output_scale_q32=low)
            if on_field and airborne and states[key].hp>HP_EPSILON and not receiver["silence_immune"]:
                states[key].silence.attach(tables,frame,talent["silence"],receiver_born=world.births[key]["frame"],projectile_born=born)
            log.append(dict(kind="ordinary_hit",frame=frame,target=key,airborne=airborne,release_frame=born,
                            damage_q32=packet["damage_q32"],hp_after_q32=states[key].hp))
            ordinary_flights.remove(shot)
        return None

    for frame in range(deploy["frame"]+1,until+1):
        log_start = len(log)
        hp_before = {k:s.hp for k,s in states.items()}
        draws_before = rng.draws
        def boundary(reason,**details):
            del log[log_start:]
            result=finish(frame-1,reason)
            result.update(remaining_hp_q32=hp_before,unresolved=dict(frame=frame,reason=reason,**details))
            result["rng"]["draws"]=draws_before
            return result
        paused_before={k:lift.paused_ticks for k,lift in wave.lifts.items()}
        wave.advance(frame,log)
        if paused_before:
            world.exits[world.exit_index:]=sorted((f+(wave.lifts[k].paused_ticks-paused_before[k] if k in paused_before else 0),k)
                                                  for f,k in world.exits[world.exit_index:])
        if world.settle_exits(frame,lambda k:states[k].hp>HP_EPSILON) is not None:
            return finish(frame,"stage_defeat")
        if mass_aura is not None:
            mass_aura.advance()
        if magic_aura is not None:
            magic_aura.advance(save_before_update=True)
        if frame==encounter["press_frame"]+1:
            ordinary_pending=None
        if frame==mode:
            wave.cells=cells
            mass_aura=_mass_aura(tables,card,evidence,wave,frame)
            magic_aura=_magic_aura(tables,evidence,wave,frame,trait)
        if frame==active_through+1 and on_field:
            launches.clear()
            wave.cells=ordinary_cells
            mass_aura=_mass_aura(tables,card,evidence,wave,frame,mode="Default")
            magic_aura=_magic_aura(tables,evidence,wave,frame,trait)
            ordinary_poll=frame+1
            log.append(dict(kind="skill_mode_ended",frame=frame))
        if on_field and (frame<=encounter["press_frame"] or frame>active_through) and frame>=ordinary_poll:
            targets=wave.admitted(frame)
            if targets:
                target=targets[0] if len(targets)==1 else world.select(targets,frame)
                if target["col"]!=deploy["col"]:
                    ordinary_sign=1 if target["col"]>deploy["col"] else -1
                ordinary_pending=ranged_shot_release(tables,evidence,ordinary_attack,attack_frame=frame,
                                                      attack_interval=ordinary_interval,facing=view)
                ordinary_pending.update(target=target["id"],sign=ordinary_sign)
                ordinary_poll=frame+ordinary_period
                log.append(dict(kind="ordinary_attack_start",frame=frame,target=target["id"]))
            else:
                ordinary_poll=frame+3
        if on_field and mode<frame<=active_through and frame==next_search:
            if wave.admitted(frame):
                volley+=1
                # Parent pre-delay starts in OBJECTS. Each empty animation child subscribes
                # to ATTACK with a SPECIFIED duration of zero, whose fixed timeout is one tick.
                spell = frame+fixed_wait_frames(single(pre/dt))-1
                launches[spell+1] = volley
                log.append(dict(kind="tornado_attack",frame=frame,volley=volley,parallel_ability_frame=spell))
                next_search+=fixed_wait_frames(single(interval/dt))
            else:
                next_search+=3
        for shot in pending[:]:
            if not on_field and withdrawal and shot.born>withdrawal["frame"]:
                return boundary("source_withdrawn_before_tornado_birth",volley=shot.volley,mount=shot.mount)
            if frame<=shot.born:
                continue
            uncertain=advance_ordinary(frame,earlier_than=shot.born)
            if uncertain:
                return boundary("magic_fragility_aura_refresh_required",target=uncertain)
            shot.age+=step
            if shot.age>=_raw_f32(life["_lifeTime"]):
                pending.remove(shot)
                log.append(dict(kind="tornado_lifetime",frame=frame,mount_candidates=shot.mount_candidates,volley=shot.volley))
                continue
            recipients = {}
            first_targets = set()
            for row,col in shot.centers:
                present = set()
                for key,state in states.items():
                    if state.hp<=HP_EPSILON:
                        continue
                    enemy = wave.by_id(key,frame,projectile_born=shot.emission)
                    if enemy and circle_in_range(enemy["row"],enemy["col"],radii[key],[(row,col)],box_query=True):
                        present.add(key)
                        recipients[key] = enemy
                if not present:
                    first_targets.add(None)
                elif len(present)==1:
                    first_targets.update(present)
                else:
                    samples = {}
                    for key in present:
                        tick = object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                                     observer_born=shot.emission,observer_phase="COROUTINES")
                        if key in wave.lifts:
                            tick = wave.lifts[key].samples.get(tick, (tick,0.))[0]
                        samples[key] = tick
                    chosen = world.priority(present,frame,frame_by_target=samples)
                    if chosen is None:
                        return boundary("first_target_inside_tornado_requires_selector_order",candidates=sorted(present))
                    first_targets.add(chosen["target"])
            if len(first_targets)>1:
                return boundary("side_mount_mirroring_changes_contact",mount=shot.mount,
                                candidate_targets=sorted(first_targets,key=lambda k:k or ""))
            key = next(iter(first_targets), None)
            if key is not None:
                enemy = recipients[key]
                raw_airborne=enemy.get("airborne",False)
                airborne=raw_airborne or wave.elevated(key,frame)
                mass=mass_aura.weight(enemy) if mass_aura is not None else enemy["mass_level"]
                if mass is None and not airborne:
                    return boundary("effective_weight_aura_required",target=key)
                if (not enemy["levitate_immune"] and not airborne and
                        world.births[key]["frame"]>=shot.emission):
                    return boundary("receiver_updated_after_projectile",target=key)
                effect = ordinary_levitate(board["attack@levitate"],dict(raw_airborne=raw_airborne,
                    mass_level=enemy["mass_level"] if mass is None else mass,levitate_immune=enemy["levitate_immune"],
                    levitate_sequence=int(not raw_airborne and airborne)))
                hp = states[key].hp
                born=world.births[key]["frame"]
                minimum,maximum=_vulnerability(magic_aura,key,trait,before_update=born>shot.emission) if on_field else (ONE,ONE)
                if born==shot.emission:
                    if on_field:
                        before_min,before_max=_vulnerability(magic_aura,key,trait,before_update=True)
                        minimum,maximum=min(minimum,before_min),max(maximum,before_max)
                if minimum!=maximum:
                    return boundary("magic_fragility_aura_refresh_required",target=key)
                talent_scale = talent["atk_scale"] if on_field and effect["airborne_after"] else 1.
                pen = _raw_f32(trait.get("magic_resist_penetrate_fixed",0)) if on_field else 0
                mitigation = max(0,_raw_f32(enemy["res"])-pen)
                scale = tornado_scale(curve,shot.age,_raw_f32(life["_lifeTime"]),board)
                attack = mul(mul(_raw_f32(card["panel_attributes"]["atk"]),scale),_raw_f32(talent_scale))
                impact=states[key].receive_ordinary(frame,source=OPERATOR,damage_type="arts",attack_q32=attack,
                                                    mitigation_q32=mitigation,output_scale_q32=minimum)
                if impact["hp_after_q32"]>HP_EPSILON and effect["levitate_applied"]:
                    if not wave.apply(key,frame,effect["initial_levitate_lifetime_q32"]):
                        return boundary("levitate_movement_transition_required",target=key)
                silence_seconds=talent.get("silence",0) if effect["airborne_after"] and not enemy["silence_immune"] else 0
                if silence_seconds and hp>HP_EPSILON and states[key].hp>HP_EPSILON:
                    effect["silence_expiry_receiver_tick"]=states[key].silence.attach(tables,frame,silence_seconds,
                        receiver_born=world.births[key]["frame"],projectile_born=shot.emission)
                log.append(dict(kind="tornado_hit",frame=frame,target=key,mount_candidates=shot.mount_candidates,volley=shot.volley,
                    age_q32=shot.age,emission_frame=shot.emission,born_frame=shot.born,position=[enemy["row"],enemy["col"]],
                    skill_scale_q32=scale,damage_q32=impact["damage_q32"],
                    projected_damage_q32=[impact["damage_q32"]]*2,hp_lost_q32=hp-states[key].hp,
                    hp_after_q32=impact["hp_after_q32"],silence_seconds=silence_seconds,source_alive=on_field,**effect))
                pending.remove(shot)
                continue
            def advance(v,axis):
                value=single(v+single(axis*single(movement["_speed"]*dt)))
                return single(round(value*10000)/10000) if encounter["round_position"] else value
            shot.centers={(advance(r,dr),advance(c,dc)) for r,c in shot.centers}
        uncertain=advance_ordinary(frame,earlier_than=frame)
        if uncertain:
            return boundary("magic_fragility_aura_refresh_required",target=uncertain)
        if mass_aura is not None:
            mass_aura.contacts(frame)
        if magic_aura is not None:
            magic_aura.contacts(frame)
        withdrawing = bool(withdrawal and withdrawal["frame"]==frame)
        if ordinary_pending is not None and frame==ordinary_pending["release_frame"]:
            key=ordinary_pending["target"]
            target=wave.by_id(key,frame,after_objects=True) if states[key].hp>HP_EPSILON else None
            if target is not None and on_field:
                origin,_=stationary_cast_mounts(tables,OPERATOR,ordinary_pending["clock"],encounter["placement"],
                                                 target,view,caster_face_sign=ordinary_pending["sign"])
                ordinary_pending["position"]=origin
                ordinary_flights.append(ordinary_pending)
                log.append(dict(kind="ordinary_projectile_release",frame=frame,target=key))
            ordinary_pending=None
        if on_field and frame in launches and (not withdrawing or withdrawal["phase"]!="before_coroutines"):
            draws=[]
            for mount,offset in mounts:
                delay=single(rng.value()*life["_randomDelayToBorn"])
                born=frame+(fixed_wait_frames(single(delay/dt)) if delay>single(1e-5) else 0)
                draws.append((mount,offset,born,delay))
            for mount,offset,born,delay in draws:
                # With an equal outer delay, both outer trajectories are present on the
                # same update. Distinct times need the individual visual facing resolved.
                center=(deploy["row"],deploy["col"])
                labels=(mount,)
                if not offset:
                    centers={center}
                elif not dc and draws[0][2]==draws[1][2]:
                    centers={(center[0],center[1]+offset)}
                    labels=tuple(v[0] for v in draws if v[1])
                elif not dc:
                    centers={(center[0],center[1]+off) for off in (-offset,offset)}
                else:
                    return boundary("horizontal_mount_projection_required")
                pending.append(Tornado(launches[frame],mount,frame,born,centers,labels))
                log.append(dict(kind="tornado_emit",frame=frame,volley=launches[frame],mount=mount,born_frame=born,
                                first_fixed_update=born+1,birth_delay_seconds=delay))
        if withdrawing:
            on_field = False
            mass_aura = magic_aura = None
            log.append(dict(kind="withdraw",frame=frame,phase=withdrawal["phase"],tornadoes_in_flight=len(pending)))
    log.sort(key=lambda e:e["frame"])
    return finish(until,"horizon")

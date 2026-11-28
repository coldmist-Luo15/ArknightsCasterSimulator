"""Advance Goldenglow's ordinary attacks, skill funnels and explosion projectiles."""
from .attack_range import circle_in_range, range_cells
from .attached_drone_battle import _blast_profile
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_drone_chain_mystic import GG, _drone
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import _board
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .eyja_ignition import ignition_circle_overlap
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .module_effects import trait_board
from .mystic_timing import fixed_wait_frames
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .stage_deployment import RangedReentries
from .skill_recovery import _divide, deploy_skill
from .stage_channel import UnsupportedChannelScene
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def remote_funnel_profiles(evidence, parts, mode, board, dt):
    keys = []
    explosions = {}
    for ref in mode["_funnelActions"][:1+int(board["attack@cnt"])]:
        key = parts[ref["m_PathID"]]["fields"]["_projectileKey"]
        variants = evidence["projectiles"][key]
        logic = variants.get("logic") or next(iter(variants.values()))
        projectile = {p["script"]:p["fields"] for p in logic["components"]}
        move, hit = projectile["AttachToTarget"], projectile["GdglowHitBehaviour"]
        if (move["_delayToStart"] or move["_followTarget"] or move["_delayAfterReached"]
                or not move["_checkReached"] or hit["_waitFirstPeriod"]):
            raise ValueError("澄闪的浮游单元附着时钟变化")
        keys.append(key)
        explosions[key] = _blast_profile(evidence, hit, dt)
    return keys, explosions


def run_goldenglow_stage(tables, request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    card,stage,placed,scene,world=prepare_continuous_ranged(tables,request,GG,releases)
    slot=card["skill"]
    presses={b["frame"] for b in request["actions"] if b.get("kind")=="skill"}
    withdrawals={b["frame"] for b in request["actions"] if b.get("kind")=="withdraw"}
    end=request["end_frame"]
    stream=tables.combat_random(request.get("battle_random_seed",0))
    ev = caster_prefab_evidence(tables,GG,slot,skill_level=card["skill_level"],
                               module_id=card["module"],module_level=card["module_level"])
    parts = {p["id"]:p for p in ev["character"]["components"] if p["active"] and p["enabled"]}
    default, = (p["fields"] for p in parts.values() if p["script"]=="RangedAttack" and p["path"]=="Modes/Default/Body/Attack")
    support, = (p["fields"] for p in parts.values() if p["script"]=="MultiFunnelNormalAttack"
                 and p["path"]=="Modes/Default/Body/GeneralAbilities/Funnel")
    mode, = (p["fields"] for p in parts.values() if p["script"]=="MultiFunnelRemoteAttack"
              and p["path"].startswith(f"Modes/S{slot}/"))
    if mode["_alwaysUseFunnelSelector"] or not mode["_alwaysIncludeTarget"]:
        raise ValueError("澄闪的派发需要复用主攻击锁定的目标")
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    start,placement,view = placed["frame"],scene["placement"],scene["view"]
    board = {b["key"]:b["value"] for b in ev["skill_blackboard"]}
    stats = card["panel_attributes"]
    trait = trait_board(tables.read("excel/character_table.json")[GG],card["module_phase"],card)
    talents = [_board(card,index,str(index+1)) for index in (0,1)]
    pen = _raw_f32(talents[1]["magic_resist_penetrate_fixed"])
    sp = deploy_skill(tables,card,start)[0].state
    base_atk = _raw_f32(stats["atk"])
    skill_atk = mul(base_atk,ONE+_raw_f32(board["atk"]))
    base_period = single(single(stats["baseAttackTime"])*100 / single(stats["attackSpeed"]))
    speed = stats["attackSpeed"] + board.get("attack_speed",0)
    skill_interval = _divide(_raw_f32(stats["baseAttackTime"]),_divide(_raw_f32(speed),100*ONE))
    unit_period = skill_interval-step
    cycle = {False:fixed_wait_frames(single(base_period / dt)),True:fixed_wait_frames(single(skill_interval / ONE / dt))}
    normal_area = range_cells(card["base_range"],placement,scene["rows"],scene["cols"])
    skill_area = range_cells(card["skill_range"] or card["base_range"],placement,scene["rows"],scene["cols"])
    end_clock = run_committed_cast(tables,ev,horizon=0,facing=view,direction=placed["direction"])["natural_end"]
    hp = {k:_raw_f32(t["hp"]) for k,t in world.stats.items()}
    radii = {k:tables.enemy_circle(t["prefab"]) for k,t in world.stats.items()}
    units, travelling, blasts, walks, slowed_until = [], [], [], {}, {}
    original_target, normal_chain = None, 0
    next_attack = start + _birth(tables,card,ev,view)["born_exit_update"] + 1
    press_cast = pending = None
    spell = restore = unblock = -1
    face_sign = -1 if placed["direction"]=="LEFT" else 1
    events=[dict(frame=start,kind="deployed")]
    init = _raw_f32(trait["init_atk_scale"])
    delta = _raw_f32(trait["delta_atk_scale"])
    maximum = _raw_f32(trait["max_atk_scale"])
    mover = next(p["fields"] for p in ev["projectiles"][default["_projectileKey"]]["logic"]["components"]
                 if p["script"]=="AdvancedMovement")
    if mover["_moveType"]!=1 or mover["_delayToStart"]:
        raise ValueError("澄闪普通追踪弹配置变化")

    drone_keys, explosions = remote_funnel_profiles(ev, parts, mode, board, dt)

    explosion_stacks={key:1 for key in drone_keys}
    births = {}
    for key, data in world.births.items():
        births.setdefault(data["frame"],[]).append(key)
    if stream:
        for f in sorted(births):
            if f>start:
                break
            for _ in range(2*len(births[f])):
                stream.next_double()

    def alive(key):
        return hp[key]>HP_EPSILON

    def damage(frame,key,atk,kind,**extra):
        result = ordinary_arts_hit(attack_raw=atk,resistance_raw=_raw_f32(world.stats[key]["res"]),
                                   fixed_res_penetration_raw=pen,hp_raw=hp[key])
        hp[key] = result.hp_after_raw
        events.append(dict(kind=kind,frame=frame,target=key,damage_q32=result.damage_raw,hp_q32=hp[key],**extra))

    def slow(frame,key):
        if slot!=3 or not alive(key):
            return
        if key not in walks and frame<end:
            rule, = tables.read("buff_table.json")["sluggish"]["attributes"]["attributeModifiers"]
            walks[key] = world.start_walk(tables,key,frame,rule["value"])
        slowed_until[key] = frame + fixed_wait_frames(single(board["attack@sluggish"]/dt))

    def remote_damage(frame,unit,row):
        key = unit["target"]
        unit["remaining"] = unit_period
        state = dict(unit,explosion_stack=explosion_stacks[unit["projectile"]],target=dict(row,hp=hp[key]/ONE),explosion_targets=[])
        packet = _drone(GG,slot,"drone_hit",state,board,stats,trait,talents,stream=stream)
        explosion_stacks[unit["projectile"]]=packet["explosion_stack"]
        if packet["exploded"]:
            delay,radius = explosions[unit["projectile"]]
            blasts.append(dict(born=frame,stop=frame+delay,center=dict(row=row["row"],col=row["col"]),
                               radius=radius,target=key,atk=mul(skill_atk,_raw_f32(talents[0]["attack@atk_scale_2"]))))
            unit.update(target=None,rearm=frame+fixed_wait_frames(single((unit_period-step)/ONE/dt)))
            events.append(dict(kind="drone_explosion_emitted",frame=frame,target=key,unit=unit["projectile"],
                               stop=frame+delay,rearm=unit["rearm"]))
            return
        hit = packet["hit"]
        hp[key] = max(0,hp[key]-hit["damage_raw"])
        if hp[key] < HP_EPSILON:
            hp[key] = 0
        for attr in ("previous_target","prior_same_target_hits"):
            unit[attr] = packet[attr]
        events.append(dict(kind="drone_hit",frame=frame,target=key,unit=unit["projectile"],
                           scale=packet["damage_scale"],damage_q32=hit["damage_raw"],hp_q32=hp[key]))
        slow(frame,key)

    def release(frame,row,attack,trace,kind,scale,*,mount=2,field="muzzlaTransform"):
        origin,hit = stationary_cast_mounts(tables,GG,trace["clock"],placement,row,view,
                                            mount=mount,field=field,caster_face_sign=face_sign)
        travelling.append(dict(born=frame,target=row["id"],position=origin,kind=kind,scale=scale,
                               offset=(hit[0]-row["col"],hit[1]-row["row"],hit[2])))
        events.append(dict(kind=kind+"_release",frame=frame,target=row["id"],projectile=attack["_projectileKey"]))

    repeats=RangedReentries.for_request(tables,stage,world,request,start)
    frame = start
    for frame in range(start+1,end+1):
        new_place=repeats.advance(frame,events) if repeats else None
        if new_place:
            placed=placement=new_place
            view="Back" if placed["direction"]=="UP" else "Front"
            normal_area=range_cells(card["base_range"],placement,scene["rows"],scene["cols"])
            skill_area=range_cells(card["skill_range"] or card["base_range"],placement,scene["rows"],scene["cols"])
            end_clock=run_committed_cast(tables,ev,horizon=0,facing=view,direction=placed["direction"])["natural_end"]
            face_sign=-1 if placed["direction"]=="LEFT" else 1
            sp=deploy_skill(tables,card,frame)[0].state
            spell=restore=unblock=-1
            press_cast=pending=original_target=None
            normal_chain=0
            explosion_stacks={key:1 for key in drone_keys}
            next_attack=frame+_birth(tables,card,ev,view)["born_exit_update"]+1
            events.append(dict(kind="deployed",frame=frame))
        was_present=world.present
        if frame in withdrawals:
            world.remove_owner(frame,events,"withdraw")
        world.receive(frame,hp,events)
        if repeats and was_present and not world.present:
            repeats.leave(frame,GG,manual=frame in withdrawals)
        if not world.present:
            units.clear()
            pending=None
        for key,walk in tuple(walks.items()):
            walk=world.step_walk(tables,walk,key,frame,0,slowed=frame<=slowed_until[key]) if alive(key) else None
            if walk is None:
                del walks[key]
            else:
                walks[key]=walk
        if world.settle_exits(frame,alive) is not None:
            break
        if world.present and frame==spell:
            units = [dict(projectile=key,target=None,rearm=frame,previous_target=None,prior_same_target_hits=0,
                          remaining=0) for key in drone_keys]
            next_attack = frame+1
            events.append(dict(kind="skill_mode",frame=frame,funnel_count=len(units)))
        if frame==restore:
            units.clear()
            pending = None
            next_attack = frame+1
            events.append(dict(kind="restore_default_mode",frame=frame,retained_blasts=len(blasts)))
        active = spell>0 and spell<=frame<restore
        if press_cast is not None and frame == press_cast["cast_frame"]:
            sp.points = press_cast["sp_after"]
        blocked = press_cast is not None and frame<unblock
        sp.advance(step,stopped=blocked)
        if world.present and frame==next_attack and (active or not blocked or frame>=restore):
            area = skill_area if active else normal_area
            rows = [r for r in world.snapshot(frame) if alive(r["id"])
                    and (active and slot==3 or circle_in_range(r["row"],r["col"],radii[r["id"]],area,box_query=True))]
            chosen = world.select(rows,frame) if len(rows)>1 else next(iter(rows),None)
            if chosen:
                if chosen["col"]!=placement["col"]:
                    face_sign = 1 if chosen["col"]>placement["col"] else -1
                ability = mode if active else default
                trace = (ranged_shot_release(tables,ev,ability,attack_frame=frame,
                          attack_interval=skill_interval/ONE if active else base_period,
                          facing=view,mode_path=f"Modes/S{slot}" if active else "Modes/Default")
                         if not (active and slot==3) else dict(release_frame=frame))
                pending = trace,chosen,active
                next_attack = frame+cycle[active]
                events.append(dict(kind="attack_start",frame=frame,target=chosen["id"],skill=active,
                                   release_frame=trace["release_frame"]))
            else:
                next_attack += 3
        for shot in travelling[:]:
            key = shot["target"]
            row = world.by_id(key,frame,projectile_born=shot["born"]) if alive(key) else None
            if row is None:
                travelling.remove(shot)
                continue
            x,y,z = shot["offset"]
            movement = ordinary_trace_step(shot["position"],(row["col"]+x,row["row"]+y,z),
                                           effective_dt=dt,round_position=True,speed=mover["_speed"])
            shot["position"] = movement.position
            if movement.reached:
                damage(frame,key,mul(skill_atk if active else base_atk,shot["scale"]),shot["kind"]+"_hit")
                travelling.remove(shot)
        for unit in units:
            key = unit["target"]
            if key is None or frame<unit["first_tick"]:
                continue
            target = world.by_id(key,frame,projectile_born=unit["born"]) if alive(key) else None
            if target is None:
                unit.update(target=None,rearm=frame+1)
                events.append(dict(kind="drone_return",frame=frame,target=key,unit=unit["projectile"]))
                continue
            unit["remaining"] -= step
            if unit["remaining"]<=0:
                remote_damage(frame,unit,target)
        for blast in blasts[:]:
            if frame!=blast["stop"]:
                continue
            blasts.remove(blast)
            events.append(dict(kind="drone_explosion",frame=frame,center=blast["center"],target=blast["target"]))
            for row in world.snapshot(frame,projectile_born=blast["born"]):
                key = row["id"]
                if alive(key) and ignition_circle_overlap(blast["center"],row,blast["radius"],radii[key]):
                    damage(frame,key,blast["atk"],"drone_explosion_hit")
                    slow(frame,key)
        if pending and frame==pending[0]["release_frame"]:
            trace,chosen,skill_attack = pending
            key = chosen["id"]
            row = world.receiver_at(key,frame) if alive(key) else None
            if row is not None and not skill_attack:
                release(frame,row,default,trace,"ordinary",ONE)
                normal_chain = normal_chain+1 if original_target==key else 0
                original_target = key
                normal_funnel = min(maximum,init+normal_chain*delta)
                release(frame,row,support,trace,"ordinary_funnel",normal_funnel,mount=6,field="specialPoint1")
            elif row is not None:
                if slot==1:
                    release(frame,row,mode,trace,"skill",ONE)
                for unit in units:
                    if unit["target"] is None and frame>=unit["rearm"]:
                        unit.update(target=key,born=frame,first_tick=frame+1,remaining=0,
                                    previous_target=None,prior_same_target_hits=0)
                        events.append(dict(kind="drone_release",frame=frame,target=key,unit=unit["projectile"]))
            pending = None
        if world.present and frame in presses:
            if frame<unblock or sp.points<sp.cost:
                events.append(dict(kind="skill_rejected",frame=frame,sp=sp.points))
            else:
                cast = resolve_cast_start(tables,ev,frame=frame,phase="after_tick",facing=view,direction=placed["direction"],
                                          trigger="MANUAL",sp=sp.points)
                if cast["cast_frame"] is None or cast["spell_frame"] is None:
                    events.append(dict(kind="skill_rejected",frame=frame,reason=cast["status"]))
                else:
                    press_cast = cast
                    if cast["cast_frame"] == frame:
                        sp.points = cast["sp_after"]
                    spell = cast["spell_frame"]
                    restore = cast["cast_frame"]+end_clock["end"]+int(end_clock["end_phase"]=="after_fixed")
                    unblock = cast["cast_frame"]+end_clock["first_recovery_tick"]
                    pending = None
                    events.extend(cast["events"])
        if stream and frame in births:
            for _ in range(2*len(births[frame])):
                stream.next_double()
        if world.settled(frame,hp,events):
            break
    through = world.defeated_at or frame
    status = "stage_defeat" if world.defeated_at else ("stage_victory_projected" if world.victory is not None and frame>=world.victory else "horizon")
    result = dict(status=status,stop=status,through_frame=through,stage=stage["code"],deployment=placed,
                  skill=slot,operator_id=GG,events=sorted([e for e in events if e["frame"]<=through],key=lambda e:e["frame"]),
                  hp_q32=hp,sp=sp.points,battle_random_draws=stream.draws if stream else 0,source=tables.source)
    return world.stage_result(result,fixed_timestep=dt,target_field="target",hp_field="hp_q32",projection_horizon=end)

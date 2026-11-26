"""Eyjafjalla's deployment and S2 attack replacements against stationary, targetable ground units."""
from math import isfinite, sqrt

from .attack_range import OVERLAP_DISTANCE_TOLERANCE, circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_runtime import default_attack
from .core_caster_skills import eyja_deployment
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import stationary_cast_mounts
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .skill_recovery import deploy_skill
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def ignition_circle_overlap(center,receiver,explosion_radius,body_radius):
    """V079 Physics2D.OverlapCircle against a centred enemy CircleCollider2D."""
    dx=single(single(receiver["col"])-single(center["col"]))
    dy=single(single(receiver["row"])-single(center["row"]))
    distance=single(sqrt(single(single(dx*dx)+single(dy*dy))))
    surface_distance=single(distance-single(single(explosion_radius)+single(body_radius)))
    return surface_distance<OVERLAP_DISTANCE_TOLERANCE


def deployment_elite_query(tables,scene,card,placement,spawned,history,radii,removed,*,tracked,alone):
    """CheckEnemyLevelMaskInRange.Execute selects living enemies from the owner's oriented box."""
    from .volley_damage import _position_samples
    frame = placement["frame"]
    candidates = [i for i,s in spawned.items() if scene.enemy_level(s["enemy"]) in ("ELITE","BOSS")
                  and removed.get(i,frame)>=frame]
    confirmed,pending = [],list(candidates)
    if tracked and candidates:
        from .attack_range import RangeContacts
        grid = scene.level["mapData"]["map"]
        shape = RangeContacts()
        shape.deploy(tables,card,placement,len(grid),len(grid[0]))
        trace = {max(0,frame-1),frame}
        active = {}
        pos = _position_samples(history,trace,active)
        pending = []
        for i in candidates:
            if i not in radii or any(p.get(i) is None for p in pos.values()):
                pending.append(i)
                continue
            contact = [circle_in_range(*p[i],radii[i],shape.cells,box_query=True) for p in pos.values()]
            if not any(contact):
                continue
            enemy = spawned[i]["enemy"]
            # The normal 2-2 shieldguard has a single ordinary mode, no general ability,
            # talent, or skills. Prior to the first operator it has no damage/stealth source.
            if (all(contact) and all(i in a for a in active.values()) and alone and not scene.predefines
                    and scene.stage["code"]=="2-2" and enemy=="enemy_1006_shield"
                    and not scene.enemies[enemy]["skills"] and not scene.enemies[enemy]["talentBlackboard"]):
                confirmed.append(i)
            else:
                pending.append(i)
    details = dict(frame=frame,candidates=candidates,confirmed=confirmed,requiring_targetability_check=pending)
    return (True if confirmed else None if pending else False),details


def _ability(tables,card,evidence):
    ordinary = default_attack(tables,card["operator_id"])
    special, = [c["fields"] for c in evidence["skill"]["components"] if c["script"]=="RangedAttack"]
    ordinary_aim,=(c["fields"] for c in evidence["character"]["components"] if c["id"]==ordinary["attack_component"])
    facing_fields=("_faceToTarget","_faceToDefault","_faceToFront","_faceToFirstCastTarget",
                   "_checkFaceToFirstCastTargetDownAttack","_forceFaceToDirection")
    if any(tuple(ability[k] for k in facing_fields)!=(1,0,0,0,0,0) for ability in (ordinary_aim,special)):
        raise ValueError("点燃或普通攻击的瞄准规则发生变化")
    replacement, = (c["fields"] for c in evidence["skill"]["components"] if c["script"] == "ReplacementSkill")
    if (ordinary["selector"]["_postFilter"]!=4 or ordinary["selector"]["_maxNum"]!=1 or
            any(ordinary["fields"][key]!=special[key] for key in
                ("_animKey","_selectTargetTiming","_mountPointType","_resetCdStrategy","_timeMode","_maxAnimScale"))):
        raise ValueError("点燃与普通攻击的索敌、挂点或动作时钟发生变化")
    if (not replacement["_recoverSpIfTargetDead"] or any(special[k] for k in
            ("_allowNoTarget", "_interuptIfTargetDead", "_emitToInputPosWhenTargetIsInvalid",
             "_emitToInputRootTileWhenTargetIsInvalid", "_waitForProjectileInvalid"))):
        raise ValueError("点燃在首次施放前失靶的退还与弹体分支发生变化")
    buff, = special["_activeBuffs"]
    mod, = buff["attributes"]["attributeModifiers"]
    bb = {b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    if (mod["attributeType"]!=3 or mod["formulaItem"]!=3 or not mod["loadFromBlackboard"] or
            buff["lifeTimeType"]!=1 or buff["lifeTime"]!=bb["duration"] or not buff["independentCharacterSource"]):
        raise ValueError("点燃的主目标减抗或持续时间发生变化")
    geometry = set()
    for prefab in evidence["projectiles"][special["_projectileKey"]].values():
        parts = {c["script"]:c["fields"] for c in prefab["components"] if c["enabled"] and c["active"]}
        life,hit,collider = (parts[k] for k in ("SimpleProjectile","HitBehaviour","CircleCollider2D"))
        action = life["_actionController"]
        extra, = action["_extraBuffsInTheEnd"]
        if (life["_hitNumType"]!=2 or not life["_alwaysHitTraceTargetInTheEnd"] or life["_canHitSameTargetMultipleTimes"]
                or not hit["_onlyCheckHitWhenReachTarget"] or hit["_exceptTraceTarget"] or hit["_onlyTraceTarget"]
                or hit["_targetOptions"]["targetMotion"]!=3 or life["_lifeTimeType"]!=1
                or not action["_onlyAddBuffsToTraceTarget"] or action["_addExtraBuffsWhenReached"]
                or extra["templateKey"]!="amgoat_s_2" or any(collider["m_Offset"].values())
                or parts["AdvancedMovement"]["_forceResetToTargetPosWhenReached"]):
            raise ValueError("点燃的到达范围、命中次数或停弹追加配置发生变化")
        geometry.add((collider["m_Radius"],parts["AdvancedMovement"]["_speed"],life["_lifeTime"]))
    (radius,speed,lifetime), = geometry
    extra, = evidence["templates"]["amgoat_s_2"]["eventToActions"]["ON_BUFF_START"]
    if extra["_damageType"]!="MAGICAL" or extra["_emitSourceOnCalculateDamage"]:
        raise ValueError("点燃追加伤害的伤害类型或回技触发已变更")
    normal_speed, = {next(c["fields"]["_speed"] for c in p["components"] if c["script"]=="AdvancedMovement")
                     for p in evidence["projectiles"][ordinary["fields"]["_projectileKey"]].values()}
    return dict(normal=ordinary["fields"]["_projectileKey"],normal_speed=normal_speed,
                special=special["_projectileKey"],speed=speed,radius=radius,lifetime=lifetime,action=special["_animKey"],
                area_scale=bb[special["_atkScaleKey"]],extra_scale=bb[extra["_atkScaleVar"]],
                debuff_ratio=bb["magic_resistance"],duration=bb["duration"])


def run_ignition(tables,card,evidence,event,stream=None,world=None):
    if card["operator_id"]!="char_180_amgoat" or card["skill"]!=2 or not card["module"]:
        raise ValueError("需要装备常规模组的艾雅法拉二技能")
    place,face = event["placement"],event["view"]
    start,end = event["frame"],event["end_frame"]
    if type(end) is not int or end<start or face not in ("Front","Back") or type(event["round_position"]) is not bool:
        raise ValueError("点燃场景需要确定的时钟终点、视图和坐标取整模式")
    enemies = event["enemies"]
    if (not enemies and world is None) or len({e["id"] for e in enemies})!=len(enemies):
        raise ValueError("敌人需要唯一的实例编号")
    cells = range_cells(card["base_range"],place,event["rows"],event["cols"])
    hp,radii = {},{}
    for e in enemies:
        if e["level"] not in ("NORMAL","ELITE","BOSS") or not all(isfinite(e[k]) for k in ("hp","res","tile_z")) or e["hp"]<=0:
            raise ValueError("需要有效的地面敌人等级、生命、法抗和高度")
        hp[e["id"]] = _raw_f32(e["hp"])
        radii[e["id"]] = tables.enemy_circle(e["prefab"])
        if world is None and (not all(isfinite(e[k]) for k in ("remaining_route_distance","col","row"))
                              or e["remaining_route_distance"]<0):
            raise ValueError("静止靶需要位置和非负剩余路程")
    def on_field(frame,projectile_born=None):
        return enemies if world is None else world.snapshot(frame,projectile_born=projectile_born)
    def candidates(frame, after_objects=False):
        return [e for e in on_field(frame, frame if after_objects else None) if hp[e["id"]]>HP_EPSILON and
                circle_in_range(e["row"],e["col"],radii[e["id"]],cells,box_query=True)]
    def select(frame, after_objects=False):
        rows = candidates(frame, after_objects)
        if len(rows)<2:
            return next(iter(rows),None)
        if world is not None:
            positions = {e["id"]: frame for e in rows} if after_objects else None
            return world.select(rows, frame, frame_by_target=positions)
        def rank(e):
            return -e.get("taunt",0),e["remaining_route_distance"]
        rows.sort(key=rank)
        if rank(rows[0])==rank(rows[1]):
            raise ValueError("同嘲讽且同路程的敌人需要额外的原生排序状态")
        return rows[0]
    has_elite = any(e["level"] in ("ELITE","BOSS") for e in candidates(start))
    deployment = eyja_deployment(tables,card,dict(seed=event.get("seed"),elite_or_boss_in_range=has_elite),stream)
    sp,_ = deploy_skill(tables,card,start)
    gauge = sp.state
    gauge.points = min(gauge.maximum,gauge.points+deployment["sp"])
    profile = _ability(tables,card,evidence)
    trait = _trait_values(card)
    penetration = _raw_f32(trait.get("magic_resist_penetrate_fixed",0))
    base = card["panel_attributes"]
    attack = mul(_raw_f32(base["atk"]),ONE+_raw_f32(deployment["caster_attack_bonus"]))
    dt = tables.fixed_timestep()
    interval = single(single(base["baseAttackTime"])*100/single(base["attackSpeed"]+deployment["attack_speed"]))
    attack_period = fixed_wait_frames(single(interval/dt))
    projectile_life=(_raw_f32(profile["lifetime"])+sp.step-1)//sp.step
    committed = run_committed_cast(tables,evidence,horizon=attack_period+2,facing=face,
                                   direction=place["direction"],effective_attack_interval=interval)
    cast = committed["replacement"]
    clock = dict(action=profile["action"],first_key_seconds=cast["attack_key_seconds"],
                 playback_speed=cast["playback_speed"])
    next_attack = start+_birth(tables,card,evidence,face)["born_exit_update"]+1
    log = [dict(kind="deployment",frame=start,sp=gauge.points,talent=deployment)]
    shots = []
    recovery_tick = start+1
    debuff_ends = {}
    stop = None
    side={"LEFT":-1,"RIGHT":1}.get(place["direction"])
    current_view=face

    def begin_attack(frame, after_objects=False):
        nonlocal next_attack, recovery_tick, side, current_view
        primary = select(frame, after_objects)
        if primary is None:
            next_attack = frame + 3
            return
        enhanced = gauge.points >= gauge.cost
        if enhanced:
            gauge.points -= gauge.cost
            recovery_tick = frame + committed["first_unblocked_recovery_tick"]
        horizontal=single(single(primary["col"])-place["col"])
        vertical=single(single(primary["row"])-place["row"])
        if horizontal==0:
            if side is None:
                from .animation_assets import read_skin_actions
                uid=card["operator_id"]
                skin=read_skin_actions(tables,"chararts/"+uid+".ab","defaultskin",[clock["action"]])
                side=skin["default_horizontal_face_sign"]
            if abs(vertical)>single(.2):
                current_view="Back" if vertical>0 else "Front"
            view,sign=current_view,side
        else:
            view,sign=face,1 if horizontal>0 else -1
            if abs(horizontal)>single(.20001) or abs(horizontal)>abs(vertical):
                side=sign
        origin,target=stationary_cast_mounts(tables,card["operator_id"],clock,place,primary,view,caster_face_sign=sign)
        shots.append(dict(primary=primary, position=origin, target=target, release=frame+cast["release"],
                          enhanced=enhanced))
        log.append(dict(kind="replacement" if enhanced else "ordinary", frame=frame, sp=gauge.points,
                        target=primary["id"], first_unblocked_recovery=recovery_tick if enhanced else None))
        next_attack = frame + attack_period

    for frame in range(start+1,end+1):
        if world is not None:
            defeat = world.settle_exits(frame-1,lambda k:hp[k]>HP_EPSILON)
            if defeat is not None or world.all_processed(hp):
                end = defeat if defeat is not None else frame-1
                stop = "stage_defeat" if defeat is not None else "all_enemies_processed"
                break
        if gauge.advance(sp.step,stopped=frame<recovery_tick):
            log.append(dict(kind="natural_sp",frame=frame,sp=gauge.points))
        if frame==next_attack:
            begin_attack(frame)
        for shot in shots[:]:
            if frame<shot["release"]:
                continue
            enhanced,primary = shot["enhanced"],shot["primary"]
            key=primary["id"]
            point=primary if world is None else world.by_id(key,frame,projectile_born=shot["release"])
            valid=point is not None and hp[key]>HP_EPSILON
            if not valid:
                if frame == shot["release"]:
                    before = gauge.points
                    if enhanced:
                        gauge.points = min(gauge.maximum, gauge.points + gauge.cost)
                        recovery_tick = frame + 1
                    shots.remove(shot)
                    log.append(dict(kind="cast_target_dead", frame=frame, target=key,
                                    refunded_sp=gauge.points-before, sp=gauge.points))
                    begin_attack(frame, after_objects=True)
                    continue
                if enhanced:
                    if not shot.get("lost_trace"):
                        shot["lost_trace"]=True
                        log.append(dict(kind="ignition_trace_lost",frame=frame,target=key))
                else:
                    log.append(dict(kind="ordinary_target_invalid",frame=frame,target=key))
                    shots.remove(shot)
                    continue
            if enhanced and frame-shot["release"]>projectile_life:
                shots.remove(shot)
                continue
            if valid:
                target=shot["target"]
                shot["last_target"]=(target[0]+point["col"]-primary["col"],
                                     target[1]+point["row"]-primary["row"],target[2])
            if frame==shot["release"]:
                log.append(dict(kind="projectile_release",frame=frame,muzzle=shot["position"],target=primary["id"],
                                projectile=profile["special"] if enhanced else profile["normal"]))
                continue
            previous = shot["position"]
            target=shot["last_target"]
            motion = ordinary_trace_step(previous,target,effective_dt=dt,round_position=event["round_position"],
                                         speed=profile["speed"] if enhanced else profile["normal_speed"])
            shot["position"] = motion.position
            if not motion.reached:
                continue
            area = []
            if enhanced:
                center=point if valid else dict(col=motion.position[0],row=motion.position[1])
                for e in on_field(frame,projectile_born=shot["release"]):
                    if hp[e["id"]]<=HP_EPSILON:
                        continue
                    # DealReached writes the primary's root XY before invoking OnProjectileReached.
                    # The synchronous overlap sees each receiver at this projectile's object turn.
                    if valid and e["id"]==key or ignition_circle_overlap(center,e,profile["radius"],radii[e["id"]]):
                        area.append(e)
                if valid:
                    duration = _raw_f32(profile["duration"])
                    debuff_ends[key] = frame+(duration+sp.step-1)//sp.step
            packets = [(primary,1,"ordinary_hit")]
            if enhanced:
                packets = [(e,profile["area_scale"],"explosion") for e in area]
                if valid:
                    packets.append((primary,profile["extra_scale"],"stop_hit"))
            for e,scale,kind in packets:
                key = e["id"]
                if hp[key]<=HP_EPSILON:
                    continue
                resistance = _raw_f32(e["res"])
                if frame<debuff_ends.get(key,-1):
                    resistance = mul(resistance,ONE+_raw_f32(profile["debuff_ratio"]))
                hit = ordinary_arts_hit(attack_raw=mul(attack,_raw_f32(scale)),resistance_raw=resistance-penetration,hp_raw=hp[key])
                hp[key] = hit.hp_after_raw
                if kind!="stop_hit" and e["level"]!="NORMAL" and frame>=recovery_tick:
                    gauge.points = min(gauge.maximum,gauge.points+int(trait.get("sp",0)))
                log.append(dict(kind=kind,frame=frame,receiver=key,damage_q32=hit.damage_raw,hp_q32=hp[key],sp=gauge.points))
            shots.remove(shot)
        if stop:
            end=frame
            log.append(dict(kind="unresolved",frame=frame,reason=stop,target=key))
            break
    return dict(status="eyja_ignition_scene",through_frame=end,events=log,hp_q32=hp,sp=gauge.points,
                **({"stop":stop} if stop else {}),
                source_attack_q32=attack,attack_period=attack_period,debuff_end_frames=debuff_ends,
                conditions=["tracked_ordinary_ground_enemies" if world else "stationary_targetable_ground_enemies",
                            "no_external_effects_or_dice","ordinary_remaining_route_priority",
                            "default_skin_constant_view", "object_uid_update_order" if world else "stationary_at_impact"])

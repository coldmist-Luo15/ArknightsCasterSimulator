"""Harmonie's storage, ordinary attacks and S2 water on ground waves."""
from math import hypot

from .animation_assets import read_spine_events
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .blocking import StationaryBlockSearch
from .caster_cast import run_committed_cast
from .caster_first_spell import attack_key_clock
from .caster_prefabs import _prefab, caster_prefab_evidence
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import ranged_shot_release, stationary_cast_mounts
from .enemy_motion import ROUTE_REACH_DISTANCE, _straight_walk, advance_direct_move
from .enemy_speed import move_attribute, movement_cap
from .fear_navigation import GroundFearNavigation
from .five_star_skills import HAMONI, _integer, _number, _targets, execute_five_star
from .fixed import ONE, mul
from .lin_output_aura import trigger_contact
from .marcil_deploy import _birth
from .mystic_projectiles import _ordinary_mover
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .phalanx_wave import resume_move_leg
from .projectile_scene import projectile_profiles
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import RecoveryTimer, deploy_skill
from .stage_channel import UnsupportedChannelScene, object_position_frame
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _water_assets(evidence):
    parts=evidence["skill"]["components"]
    auras={c["path"]:c["fields"] for c in parts if c["script"]=="AuraAbility"}
    for name,ignore in (("DamageAura",0),("SlowAura",1)):
        aura=auras[name]
        options,=(c["fields"]["_targetOptions"] for c in parts if c["id"]==aura["_targetValidator"]["m_PathID"])
        shape,=(c["fields"] for c in parts if c["path"]==name and c["script"]=="AutoLoadBoxRange")
        if (not aura["_removeBuffWhenTargetLeave"] or not aura["_removeBuffWhenAbilityDetached"] or
                not options["enableAdvancedOptions"] or options["onlyIgnoreSomeOfTargetFreeCase"]!=ignore or
                options["abnormalFlag"]!=9 or options["abnormalCombo"]!=2 or
                shape["_useBlackboardRangeId"] or shape["_extendable"] or
                (options["targetSide"],options["targetMotion"],options["ignoreTargetFree"])!=(2,1,ignore)):
            raise ValueError("水域范围、离开移除或地面目标条件已变化")
    switch,=(b for b in auras["SlowAura"]["_passiveBuffs"] if b["buffKey"]=="hamoni_s_2_switch")
    modes=evidence["templates"][switch["templateKey"]]["eventToActions"]
    enter,=modes["ON_BUFF_START"]
    restore,=modes["ON_BUFF_FINISH"]
    if not enter["_restartFSM"] or not enter["_loadModeFromBlackboard"] or not restore["_restartFSM"] or not restore["_restoreDefault"]:
        raise ValueError("和弦水域的切态和状态机重启规则变化")
    dot,=auras["DamageAura"]["_buffs"]
    effect,=evidence["templates"][dot["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    if (dot["waitFirstTriggerInterval"] or dot["firstTriggerInterval"]!=-1 or dot["triggerLifeType"]!=2 or
            not effect["$type"].split(",")[0].endswith("+FixedValueDamage") or
            (effect["_damageType"],effect["_damageKey"],effect["_attackType"])!=("MAGICAL","damage_value","BUFF")):
        raise ValueError("水域计时或固定法伤动作已变化")
    chars=evidence["character"]["components"]
    selector,=(c["fields"] for c in chars if c["script"]=="BlockedTargetAdvancedSelector" and c["path"].startswith("Modes/S2/"))
    default,=(c["fields"] for c in chars if c["path"]=="Modes/Default/Body/Attack" and c["script"]=="MultiChargeRangedAttack")
    if ((selector["_targetMotion"],selector["_targetSide"],selector["_shrinkNum"])!=(1,2,1) or
            not default["_waitForAttackEvent"] or default["_maxAnimScale"]!=1 or default["_timeMode"]!=0):
        raise ValueError("和弦的储能或主弹阻挡筛选已变化")
    return dot["triggerInterval"],default["_chargeAnimKey"]


def _combat_clip(tables,key):
    """Duration of the ordinary committed melee action, with its animator playback speed."""
    cache=("water_combat_clip",key)
    if cache not in tables._prefab_data:
        tables._prefab_data[cache]=_read_combat_clip(tables,key)
    return tables._prefab_data[cache]


def _read_combat_clip(tables,key):
    enemy=_prefab(tables,"enemies",key)
    parts=(enemy or {}).get("components",())
    attack=next((c["fields"] for c in parts if c["script"]=="MeleeAttack" and c["path"]=="Default/Body/Combat"),None)
    view=next((c["fields"] for c in parts if c["script"]=="SingleSpineAnimator" and c["path"]=="Graphic"),None)
    if not attack or not view or attack["_timeMode"]!=0 or attack["_interuptIfTargetDead"]:
        return None
    binding=next((a for a in view["_animations"] if a["animKey"]==attack["_animKey"] and not a["loop"] and a["speed"]>0),None)
    if not binding:
        return None
    skeleton=next((c["fields"].get("skeletonDataAsset") for c in parts if c["id"]==view["_skeleton"]["m_PathID"]),None)
    if not skeleton or not skeleton["m_FileID"]:
        return None
    raw,_=tables.verified_bundle(enemy["source"]["member"])
    root=bundle_environment(raw).container[enemy["source"]["container"]].deref()
    cab=root.assets_file.externals[skeleton["m_FileID"]-1].path.rsplit("/",1)[-1]
    raw,source=tables.verified_bundle("refs/arts/enm_art_base_0.ab")
    art=next((obj for obj in bundle_environment(raw).objects if obj.assets_file.name==cab and obj.path_id==skeleton["m_PathID"]),None)
    if art is None:
        return None
    ref=art.read_typetree()["skeletonJSON"]
    if ref["m_FileID"]:
        return None
    binary=art.assets_file.objects[ref["m_PathID"]].read_typetree()["m_Script"]
    clip=next((c for c in read_spine_events(binary)["animations"] if c["name"]==binding["animName"]),None)
    if clip:
        tables.source["water_combat_clip"]=source
        return single(clip["duration"]/binding["speed"])


class WaterAttacks:
    """One attack mode with shared storage and independently homing packets."""
    def __init__(self,tables,card,evidence,place,facing,cells,attach,stored,charge_key,events,*,active=True):
        dt=tables.fixed_timestep()
        components=evidence["character"]["components"]
        path="Modes/S2/Body/Attack" if active else "Modes/Default/Body/Attack"
        attack,=(c["fields"] for c in components if c["path"]==path and c["script"]=="MultiChargeRangedAttack")
        charge,=(c["fields"] for c in components if c["id"]==attack["_chargeAbility"]["m_PathID"] and c["script"]=="ChargeRangedAttack")
        if (not attack["_useChargeableGroup"] or attack["_selectTargetSource"]!=2 or attack["_selectTargetTiming"]!=0
                or attack["_resetCdStrategy"]!=1 or attack["_chargeAnimKey"]!=charge_key or not attack["_alwaysIncludeTarget"]
                or attack["_atkScale"]!=1 or charge["_atkScale"]!=1 or attack["_interuptIfTargetDead"]):
            raise ValueError("和弦二技能的索敌时机、储能组或基础伤害变化")
        mounts=charge["_mountPointForProjectiles"]
        mount_fields={2:"muzzlaTransform",5:"specialPoint0",6:"specialPoint1",8:"specialPoint2",9:"specialPoint3"}
        keys=(attack["_projectileKey"],charge["_projectileKey"])
        profiles=projectile_profiles(tables,keys)
        if not all(_ordinary_mover(v) and v["has_only_trace_hit"] for variants in profiles.values() for v in variants.values()):
            raise ValueError("和弦二技能弹体的追踪速度或结算动作变化")
        profile=execute_five_star(tables,card,dict(kind="profile",frame=attach))["result"]
        capacity=profile["storage_capacity"]
        if not stored<=capacity<=len(mounts):
            raise ValueError("储能超过技能的可用发射挂点")
        interval=single((profile["base_attack_seconds"] if active else card["panel_attributes"]["baseAttackTime"])*100/profile["attack_speed"])
        cycle=fixed_wait_frames(single(interval/dt))
        shot=ranged_shot_release(tables,evidence,attack,attack_frame=0,attack_interval=interval,facing=facing)
        charge_wait,_=attack_key_clock(tables,evidence,facing,charge_key,interval)
        self.tables,self.place,self.facing=tables,place,facing
        self.cells,self.dt,self.events=cells,dt,events
        self.keys,self.mount_fields=keys,mount_fields
        self.mounts=[attack["_mountPointType"],*mounts]
        self.capacity,self.stored=capacity,stored
        self.cycle,self.shot,self.charge_wait=cycle,shot,charge_wait
        self.power=profile["attack_raw"]
        self.boosted=mul(self.power,_raw_f32(profile["blocked_attack_scale"]))
        self.projectiles=[]
        self.pending=None
        self.search=attach+1
        self.face_sign=-1 if place["direction"]=="LEFT" else 1
        self.active=active
        self.awake_until=None

    def advance(self,frame,enemies,hp,pick,damage,*,move_flights=True):
        for bullet in tuple(self.projectiles) if move_flights else ():
            key=bullet["target"]
            if key not in enemies or hp[key]<=HP_EPSILON:
                self.projectiles.remove(bullet)
                self.events.append(dict(kind="projectile_target_lost",frame=frame,target=key,mount=bullet["mount"]))
                continue
            enemy=enemies[key]
            row,col=enemy["position"]
            dx,dy,z=bullet["hit_offset"]
            move=ordinary_trace_step(bullet["position"],(col+dx,row+dy,z+enemy.get("tile_z",0)+enemy.get("height_offset",0)),effective_dt=self.dt,round_position=True)
            bullet["position"]=move.position
            if move.reached:
                blocking=enemy["blocked"] or enemy.get("is_blocking",False)
                damage(frame,key,self.boosted if blocking else self.power,"projectile_hit",
                       packet=bullet["packet"],mount=bullet["mount"],blocked=enemy["blocked"],is_blocking=enemy.get("is_blocking",False))
                self.projectiles.remove(bullet)
        if frame==self.search:
            candidates=[key for key,e in enemies.items() if hp[key]>HP_EPSILON and e.get("targetable",True)
                        and (not self.active or not e.get("airborne",False) and (e["blocked"] or e.get("is_blocking",False)))
                        and circle_in_range(*e["position"],e["radius"],self.cells,box_query=True)]
            target=pick(candidates,frame) if candidates else None
            if target is not None or self.stored<self.capacity:
                delay=self.shot["release_frame"] if target is not None else self.charge_wait
                self.pending=frame+delay,target
                self.events.append(dict(kind="attack_start" if target is not None else "charge_start",frame=frame,target=target,
                                        mode="skill" if self.active else "default"))
                if target is not None and enemies[target]["position"][1]!=self.place["col"]:
                    self.face_sign=1 if enemies[target]["position"][1]>self.place["col"] else -1
                self.search+=self.cycle
                self.awake_until=self.search
            else:
                self.search+=3
        if self.pending and frame==self.pending[0]:
            _,key=self.pending
            self.pending=None
            if key is None:
                self.stored+=1
                self.events.append(dict(kind="stored",frame=frame,energy=self.stored))
            elif key not in enemies or hp[key]<=HP_EPSILON:
                self.events.append(dict(kind="attack_skipped",frame=frame,target=key,stored=self.stored))
            else:
                target=enemies[key]
                row,col=target["position"]
                recipient=dict(target,row=row,col=col,tile_z=target.get("tile_z",0))
                for index,mount in enumerate(self.mounts[:1+self.stored]):
                    origin,hit=stationary_cast_mounts(self.tables,HAMONI,self.shot["clock"],self.place,recipient,self.facing,
                                                     mount=mount,field=self.mount_fields[mount],caster_face_sign=self.face_sign)
                    packet="stored" if index else "primary"
                    self.events.append(dict(kind="projectile_release",frame=frame,target=key,mount=mount,packet=packet,
                                       projectile=self.keys[int(index>0)],blocked=target["blocked"]))
                    self.projectiles.append(dict(target=key,mount=mount,packet=packet,position=origin,
                                            hit_offset=(hit[0]-col,hit[1]-row,hit[2]-recipient["tile_z"])))
                self.stored=0


def run_harmonie_water_battle(tables,card,scene):
    """Run an active S2 window with a live blocker and ordinary stationary ground receivers."""
    start,end=_integer(scene,"frame"),_integer(scene,"end_frame")
    dt=tables.fixed_timestep()
    step=_raw_f32(dt)
    duration=fixed_wait_frames(single(single(card["skill_row"]["duration"])/dt))
    if scene.get("entry")!="water_attach" or not start<=end<start+duration:
        raise ValueError("和弦二技能靶场须在水域挂载时开始，观察区间限于技能内")
    place=dict(scene["placement"])
    place["tile_z"]=_number(place,"z",minimum=-1000)
    facing=place["spine_view"]
    rows,cols=_integer(scene,"rows"),_integer(scene,"cols")
    if (facing not in ("Front","Back") or place["direction"] not in ("LEFT","RIGHT")
            or _integer(place,"row")>=rows or _integer(place,"col")>=cols):
        raise ValueError("和弦二技能靶场需要地图内高台和水平朝向")
    if scene["blocker"]["first_search_frame"]<start:
        raise ValueError("需从靶场起点开始回放阻挡者")
    targets=_targets(scene)
    enemies={t["id"]:dict(t,position=(_number(t,"row"),_number(t,"col")),blocked=False,
                         priority=_number(t,"route_remaining"),res_q32=_raw_f32(_number(t,"res",minimum=-1000)))
             for t in targets}
    hp={t["id"]:_raw_f32(_number(t,"hp")) for t in targets}
    if (not enemies or len({e["priority"] for e in enemies.values()})!=len(enemies)
            or any(hp[k]<=HP_EPSILON or e["motion_mode"]!="WALK" or e["row"]>=rows or e["col"]>=cols
                   or _number(e,"radius")<=0 for k,e in enemies.items())):
        raise ValueError("需要存活的普通地面靶、固定碰撞体和无并列的路线优先级")
    blocks=StationaryBlockSearch(tables,scene["blocker"],targets)
    cells=range_cells(card["base_range"],place,rows,cols)

    evidence=caster_prefab_evidence(tables,HAMONI,2,skill_level=card["skill_level"],module_id=card["module"],
                                    module_level=card["module_level"])
    pulse,charge_key=_water_assets(evidence)
    events=[]
    attacks=WaterAttacks(tables,card,evidence,place,facing,cells,start,_integer(scene,"stored"),charge_key,events)
    dots={}
    board={b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    water_power=_raw_f32(board["damage_value"])

    def pick(candidates,frame):
        return min(candidates,key=lambda k:enemies[k]["priority"])

    def damage(frame,key,power,kind,**details):
        result=ordinary_arts_hit(attack_raw=power,resistance_raw=enemies[key]["res_q32"],hp_raw=hp[key])
        hp[key]=result.hp_after_raw
        events.append(dict(kind=kind,frame=frame,target=key,damage_q32=result.damage_raw,hp_after_q32=hp[key],**details))

    for frame in range(start,end+1):
        blocks.update(frame,enemies,hp,events)
        for key,timer in dots.items():
            if hp[key]>HP_EPSILON and timer.advance(step):
                damage(frame,key,water_power,"water_damage")
        attacks.advance(frame,enemies,hp,pick,damage)
        for key,e in enemies.items():
            contact=trigger_contact(e["position"],e["radius"],cells) if hp[key]>HP_EPSILON else False
            if contact is None:
                return dict(status="water_trigger_contact_phase",through_frame=frame,events=events,hp_q32=hp,stored=attacks.stored)
            if contact and key not in dots:
                dots[key]=RecoveryTimer(_raw_f32(pulse))
                dots[key].remaining=0
                events.append(dict(kind="water_enter",frame=frame,target=key,first_target_tick=frame+1))
            elif not contact:
                dots.pop(key,None)
    return dict(status="horizon",through_frame=end,events=events,hp_q32=hp,stored=attacks.stored,cycle_ticks=attacks.cycle)


def run_harmonie_water_stage(tables,request):
    stage_request=dict(request)
    invisible_until=stage_request.pop("enemy_invisible_until",{})
    blocker=stage_request.pop("blocker",None)
    deploy,*buttons=request["actions"]
    end=request["end_frame"]
    presses={button["frame"]:button for button in buttons}
    if (not presses or len(presses)!=len(buttons) or list(presses)!=sorted(presses) or
            any(button!=dict(kind="skill",operator_id=HAMONI,frame=f,phase="after_tick") or
                not deploy["frame"]<f<=end for f,button in presses.items())):
        raise ValueError("和弦需要按帧排序的 after_tick 技能按键")
    stage_request["actions"]=[deploy,buttons[0]]
    card,stage,place,scene,world=prepare_ranged_stage(tables,stage_request,operator=HAMONI,manual=True,portable=True,
        track_exits=True,simulate_before_button=True)
    if not isinstance(invisible_until,dict) or any(key not in world.births or type(last) is not int or last<world.births[key]["frame"]
                                                  for key,last in invisible_until.items()):
        raise ValueError("隐匿持续至指定帧的输入需要本次出兵的实例及有效结束帧")
    if card["skill"]!=2:
        raise ValueError("水域由沉溺之灾创建")
    evidence=caster_prefab_evidence(tables,HAMONI,2,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    pulse,action=_water_assets(evidence)
    board={b["key"]:b["value"] for b in evidence["skill_blackboard"]}
    dt=tables.fixed_timestep()
    step=_raw_f32(dt)
    start=place["frame"]
    view=scene["view"]
    first=start+_birth(tables,card,evidence,view)["born_exit_update"]+1
    events=[dict(kind="deployed",frame=start)]
    clock=run_committed_cast(tables,evidence,horizon=0,facing=view,direction=place["direction"])["natural_end"]
    attach=finish=unblock=None
    gauge,_=deploy_skill(tables,card,start)
    casts=[]
    hp={k:_raw_f32(s["hp"]) for k,s in world.stats.items()}
    radii={k:tables.enemy_circle(s["prefab"]) for k,s in world.stats.items()}
    cells=range_cells(card["base_range"],place,scene["rows"],scene["cols"])
    wet=set()
    dots={}
    walks={}
    navigation=None
    blocks=None
    blocked_since={}
    resume_move_at={}
    route_exited=set()
    targets={key:dict(s,radius=radii[key],blocked=False,position=None) for key,s in world.stats.items()}
    attacks=WaterAttacks(tables,card,evidence,scene["placement"],view,cells,first-1,0,action,events,active=False)
    if blocker is not None:
        row,col=blocker["position"]
        if (type(row) is not int or type(col) is not int or not 0<=row<scene["rows"] or not 0<=col<scene["cols"]
                or world.scene.tile(row,col)["buildableType"] not in ("MELEE","ALL")
                or blocker["mode"]!="WALK" or blocker["first_search_frame"]<start):
            raise ValueError("和弦水域波次需要部署后搜索的地面阻挡者，以及有效的近战地块")
        blocks=StationaryBlockSearch(tables,blocker,[dict(t,row=0,col=0,motion_mode="WALK",
            instance_uid=order) for order,t in enumerate(targets.values())])
    def switch(frame,active,visible):
        nonlocal attacks
        prior=attacks
        attacks=WaterAttacks(tables,card,evidence,scene["placement"],view,cells,frame,prior.stored,action,events,active=active)
        attacks.face_sign=prior.face_sign
        attacks.projectiles=prior.projectiles
        events.append(dict(kind="water_mode_start" if active else "restore_default_mode",frame=frame,
                           retained_projectiles=len(prior.projectiles),stored=prior.stored))
        if prior.awake_until is not None and frame<prior.awake_until:
            attacks.search=frame
            attacks.advance(frame,visible,hp,pick,damage,move_flights=False)

    def result(frame,reason,**details):
        observed=[e for e in events if e["frame"]<=frame]
        health={k:_raw_f32(s["hp"]) for k,s in world.stats.items()}
        health.update((e["target"],e["hp_q32"]) for e in observed if "hp_q32" in e)
        out=dict(stage=stage["code"],skill=2,operator_id=HAMONI,module=card["module"],module_level=card["module_level"],
            status=reason,stop=reason,through_frame=frame,skill_end_frame=finish,first_unblocked_recovery_tick=unblock,
            events=observed,stored=attacks.stored,sp=gauge.state.points,casts=casts,remaining_hp_q32=health,**details)
        return world.stage_result(out,fixed_timestep=dt,target_field="target",hp_field="hp_q32")

    def remember_leg(key,frame):
        restored=resume_move_leg(tables,world.scene,world,key,frame)
        if restored is None:
            return False
        route,goal,normal,steering,pos,velocity=restored
        reduced=movement_cap(move_attribute(route["base_move_speed"],ratio_delta=board["move_speed"]),route["level_move_multiplier"])
        sample,=(row for _,row in world.history[frame] if row["instance_id"]==key)
        commands=route["commands"]
        source=sample.get("leg_source",commands[0]["source"])
        index=next(i for i,command in enumerate(commands) if command["source"]==source)
        walks[key]=dict(pos=pos,velocity=velocity,goal=goal,normal=normal,reduced=reduced,steering=steering,route=route,
                        commands=commands,index=index,diagonal=route["flags"]["allowDiagonalMove"])
        world.exits=[pair for i,pair in enumerate(world.exits) if i<world.exit_index or pair[1]!=key]
        return True

    def pick(candidates,frame):
        nonlocal navigation
        if len(candidates)==1:
            return candidates[0]
        if navigation is None:
            navigation=GroundFearNavigation(world.scene)
        weights={}
        for key in candidates:
            if key in walks:
                leg=walks[key]
                distance=navigation.remaining_distance(positions[key],leg["commands"],leg["index"],leg["diagonal"])
            else:
                observed=world.priority({key},frame,frame_by_target={key:frame},distance_samples=True)
                distance=observed[key][0] if observed else None
            if distance is None:
                raise UnsupportedChannelScene("需要多目标各自的剩余路线游标")
            birth=world.births[key]
            weights[key]=(int(distance*ONE)//_raw_f32(.1),birth["frame"],birth["order"])
        selected=min(weights,key=weights.get)
        events.append(dict(kind="route_priority",frame=frame,target=selected,rank_by_target=weights))
        return selected

    def damage(frame,key,power,kind,**details):
        hit=ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(world.stats[key]["res"]),hp_raw=hp[key])
        hp[key]=hit.hp_after_raw
        events.append(dict(kind=kind,frame=frame,target=key,damage_q32=hit.damage_raw,hp_q32=hp[key],position=positions[key],**details))

    for frame in range(start,end+1):
        if world.settle_exits(frame,lambda k:hp[k]>HP_EPSILON) is not None:
            return result(frame,"stage_defeat")
        if frame>start and (unblock is None or frame>=unblock):
            gauge.state.advance(gauge.step)
        water_active=attach is not None and attach<=frame<=finish
        positions={}
        freed=[]
        for key in sorted(world.active[frame] | walks.keys()):
            if hp[key]<=HP_EPSILON or key in route_exited:
                continue
            pos=world.positions[frame].get(key)
            if blocks is not None and key in blocks.manager.blocked:
                pos=targets[key]["position"]
            elif frame<resume_move_at.get(key,0):
                pos=walks[key]["pos"]
            elif key in walks:
                leg=walks[key]
                cap=leg["reduced"] if key in wet else leg["normal"]
                if hypot(leg["pos"][0]-leg["goal"][0],leg["pos"][1]-leg["goal"][1])<=single(cap*dt):
                    pos=leg["goal"]
                else:
                    pos,leg["velocity"]=advance_direct_move(leg["pos"],leg["velocity"],leg["goal"],cap,leg["steering"],dt,True)
                leg["pos"]=pos
                if hypot(pos[0]-leg["goal"][0],pos[1]-leg["goal"][1])<=ROUTE_REACH_DISTANCE:
                    leg["index"]+=1
                    if leg["index"]==len(leg["commands"]):
                        del walks[key]
                        route_exited.add(key)
                        world.active[frame].discard(key)
                        world.exits=world.exits[:world.exit_index]+sorted([*world.exits[world.exit_index:],(frame,key)])
                        wet.discard(key)
                        dots.pop(key,None)
                        continue
                    command=leg["commands"][leg["index"]]
                    origin=dict(row=round(pos[0]),col=round(pos[1]))
                    if (command["kind"] not in ("MOVE","END") or command.get("randomize_reach_offset")
                            or command.get("reach_distance") or any(command.get("reach_offset",{}).values())
                            or _straight_walk(world.scene,leg["route"],origin,command,allow_same_cell=True)):
                        return result(frame,"speed_changed_route_checkpoint",unresolved=dict(frame=frame+1,target=key))
                    goal=command["target"]
                    leg["goal"]=(goal["row"],goal["col"])
                    events.append(dict(kind="water_route_checkpoint",frame=frame,target=key,goal=leg["goal"]))
            positions[key]=pos
        if world.settle_exits(frame,lambda k:hp[k]>HP_EPSILON) is not None:
            return result(frame,"stage_defeat")
        if blocks is not None:
            previous=set(blocks.manager.blocked)
            blocks.update(frame,targets,hp,events,positions=positions)
            freed=[key for key in previous-blocks.manager.blocked.keys() if hp[key]>HP_EPSILON]
            for key in (*blocks.manager.blocked,*freed):
                if hp[key]<=HP_EPSILON:
                    continue
                if key not in previous and key not in walks and not remember_leg(key,frame):
                    return result(frame,"block_at_unsupported_motion",unresolved=dict(frame=frame+1,target=key))
                if key not in previous:
                    blocked_since[key]=frame
                positions[key]=targets[key]["position"]
        for key,pos in positions.items():
            targets[key]["position"]=pos
            targets[key]["targetable"]=frame>invisible_until.get(key,-1)
        for key,pos in positions.items():
            if not water_active:
                break
            # MOVE precedes Buff.OnTick. Physical trigger notifications are consumed in COROUTINES.
            # Rejected physical entrants are polled on the first active owner
            # update and every ten owner updates thereafter (FrameTimer).
            if key in wet and key not in dots and frame>invisible_until.get(key,-1) and (frame-attach-1)%10==0:
                observed=object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                               observer_born=start,observer_phase="INPUT")
                first=frame+int(observed==frame)
                timer=RecoveryTimer(_raw_f32(pulse))
                timer.remaining=0
                dots[key]=(first,timer)
                events.append(dict(kind="water_damage_attached",frame=frame,target=key,first_target_tick=first))
            if key in dots and frame>=dots[key][0] and dots[key][1].advance(step):
                damage(frame,key,_raw_f32(board["damage_value"]),"water_damage")
                if hp[key]<=HP_EPSILON:
                    wet.discard(key)
                    del dots[key]
                    continue
            contact=trigger_contact(pos,radii[key],cells)
            if contact is None or (contact and frame in (attach,world.births[key]["frame"])):
                return result(frame-1,"water_trigger_contact_phase",unresolved=dict(frame=frame,target=key))
            if contact==(key in wet):
                continue
            if contact:
                blocked=blocks is not None and targets[key]["blocked"]
                stored=attacks.stored
                wet.add(key)
                if frame>invisible_until.get(key,-1):
                    timer=RecoveryTimer(_raw_f32(pulse))
                    timer.remaining=0
                    dots[key]=(frame+1,timer)
                if key not in walks and frame<end:
                    if not remember_leg(key,frame):
                        return result(frame,"speed_change_at_unsupported_motion",unresolved=dict(frame=frame+1,target=key))
                events.append(dict(kind="water_enter",frame=frame,target=key,position=pos,blocked=blocked,
                                   primary_eligible=blocked and frame>invisible_until.get(key,-1),
                                   speed_delta=board["move_speed"],stored=stored))
            else:
                wet.remove(key)
                dots.pop(key,None)
                events.append(dict(kind="water_exit",frame=frame,target=key,position=pos))
        visible={key:targets[key] for key in positions}
        try:
            attacks.advance(frame,visible,hp,pick,damage)
        except UnsupportedChannelScene as error:
            return result(frame-1,"blocked_target_priority",unresolved=dict(frame=frame,reason=str(error)))
        for key in freed:
            if hp[key]<=HP_EPSILON:
                continue
            unit=world.scene.enemies[world.stats[key]["enemy_key"]]
            attrs=unit["attributes"]
            since=blocked_since.pop(key)
            if world.stats[key]["prefab"]!="enemy_1007_slime_3":
                return result(frame,"released_enemy_combat_state_boundary",unresolved=dict(frame=frame+1,target=key))
            seconds=single(single(attrs["baseAttackTime"]["m_value"])*100/single(attrs["attackSpeed"]["m_value"]))
            cooldown=fixed_wait_frames(single(seconds/dt))
            clip=_combat_clip(tables,"enemy_1007_slime_3")
            combat_end=since+fixed_wait_frames(single(min(seconds,clip)/dt))+2
            if frame-since>=cooldown or key in blocks.tweens:
                return result(frame,"released_enemy_combat_state_boundary",unresolved=dict(frame=frame+1,target=key))
            # Losing the blocker changes target eligibility immediately. CombatState
            # waits for the committed cast before it requests MOVE.
            first_move=max(frame,combat_end)+1
            resume_move_at[key]=first_move
            walks[key]["pos"]=positions[key]
            events.append(dict(kind="released_enemy_resume_move",frame=frame,target=key,first_move_frame=first_move,
                               position=positions[key]))
        if frame==attach:
            switch(frame,True,visible)
        if frame==finish:
            switch(frame,False,visible)
            events.append(dict(kind="water_auras_detached",frame=finish,owners_losing_water=sorted(wet)))
            wet.clear()
            dots.clear()
        if frame in presses:
            if finish is not None and frame<finish:
                events.append(dict(kind="skill_while_active",frame=frame,sp=gauge.state.points))
                continue
            awaking=attacks.awake_until is not None and frame<attacks.awake_until
            cast=resolve_cast_start(tables,evidence,frame=frame,sp=gauge.state.points,direction=place["direction"],facing=view,
                phase="after_tick",owner_state="born" if frame<first-1 else "attack" if awaking else "idle")
            events.extend(cast["events"])
            casts.append(cast)
            if cast["cast_frame"] is not None:
                gauge.state.points=cast["sp_after"]
                attach=cast["spell_frame"]
                finish=cast["cast_frame"]+clock["end"]
                unblock=cast["cast_frame"]+clock["first_recovery_tick"]
    return result(end,"horizon")

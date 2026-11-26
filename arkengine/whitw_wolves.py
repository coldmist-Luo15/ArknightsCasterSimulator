"""S3 wolf flight against stationary or moving enemies in local, flat map coordinates."""
from math import atan2, cos, hypot, pi, sin, sqrt

from .attack_range import circle_in_range, range_cells
from .baked_mount import single as f32
from .battle_random import BattleRandom
from .branch_skill_window import first_branch_skill
from .caster_prefabs import caster_prefab_evidence
from .damage import HP_EPSILON, ordinary_arts_hit
from .five_star_skills import _flag, _integer, _number, _targets
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows, trait_board
from .skill_recovery import RecoveryTimer, _divide
from .volley_damage import _raw_f32


def _xy_step(pos,direction,speed,dt):
    return tuple(f32(round(f32(p+f32(speed*f32(dt*u)))*10000)/10000) for p,u in zip(pos,direction))


def _nearest(wolf,op,targets):
    if not targets:
        return None
    def key(t):
        return tuple(f32(f32(f32(t["col"]-p[0])**2)+f32(f32(t["row"]-p[1])**2)) for p in (wolf,op))
    ranked=sorted((key(t),t["id"]) for t in targets)
    # UpdateTarget accepts only strict MathUtil.LT improvement in squared
    # distance. The owner's nearest-selector order breaks the first tie.
    closest=ranked[0][0][0]
    near=sorted((pair[1],uid) for pair,uid in ranked if pair[0]<=f32(closest+f32(1e-5)))
    return False if len(near)>1 and near[1][0]<=f32(near[0][0]+f32(1e-5)) else near[0][1]


def _chase(wolf,goal,move,dt,turn):
    pos=wolf["position"]
    v=[f32(b-a) for a,b in zip(pos,goal)]
    dist=f32(sqrt(f32(f32(v[0]*v[0])+f32(v[1]*v[1]))))
    speed=min(move["_finalSpeed"],f32(wolf["speed"]+f32(move["_deltaSpeedPerSec"]*dt)))
    wolf["speed"]=speed
    towards=[f32(d/dist) for d in v] if dist>1e-5 else (0.,0.)
    wolf["direction"]=tuple(f32(a+f32(f32(b-a)*turn)) for a,b in zip(wolf["direction"],towards))
    wolf["position"]=tuple(goal) if dist<=f32(speed*dt) else _xy_step(pos,wolf["direction"],speed,dt)
    dx,dy=(f32(a-b) for a,b in zip(wolf["position"],goal))
    return f32(f32(dx*dx)+f32(dy*dy))<=f32(f32(.1)**2)


def _respawn(wolf,rng,lo,hi):
    offsets=[f32(lo[k]+f32(f32(hi[k]-lo[k])*rng.value())) for k in ("x","y")]
    wolf["position"]=tuple(f32(a+b) for a,b in zip(wolf["position"],offsets))
    return offsets


def _hover(w,radius,speed,dt):
    path=w.get("hover")
    if path is None:
        x,y=w["position"]
        theta=f32(f32(atan2(w["direction"][1],w["direction"][0]))+f32(pi/2))
        left=(f32(cos(theta)),f32(sin(theta)))
        path=w["hover"]=dict(entry=(x,y),small=tuple(f32(f32(a+f32(a+f32(b*radius)))*.5)
                                                      for a,b in zip((x,y),left)),
                             inner_angle=f32(theta+f32(pi)),outer_angle=theta,time=0.,last=(x,y))
    path["time"]=f32(path["time"]+dt)
    fast=f32(f32(speed*2)/radius)
    if path["time"]<=f32(f32(pi)/fast):
        center,scale,key,omega=path["small"],.5,"inner_angle",fast
    else:
        center,scale,key,omega=path["entry"],1.,"outer_angle",f32(speed/radius)
    theta=path[key]=f32(path[key]+f32(omega*dt))
    pos=tuple(f32(a+f32(scale*f32(b*radius))) for a,b in zip(center,(f32(cos(theta)),f32(sin(theta)))))
    delta=tuple(f32(a-b) for a,b in zip(pos,path["last"]))
    length=f32(hypot(*delta))
    w["direction"]=tuple(f32(a/length) for a in delta) if length>1e-5 else (0.,0.)
    path["last"]=pos
    w["position"]=tuple(f32(round(a*10000)/10000) for a in pos)


def wolf_release_frame(evidence,spell,step):
    trigger,=[b for c in evidence["character"]["components"] if c["path"].endswith("/TriggerWolves")
               and c["script"]=="PassiveBuffAbility" for b in c["fields"]["_buffs"]
               if b["buffKey"]=="whitw2_m3_t[trigger_ability]"]
    return spell+(_raw_f32(trigger["lifeTime"])+step-1)//step


def run_wolf_sortie(tables,card,scene,*,stream=None,map_stream=None,stage_owner=None,stage_cast=None):
    start=first_branch_skill(tables,card,scene) if stage_cast is None else stage_cast
    applied=next((e for e in start["events"] if e["kind"]=="active_buff_spell"),None)
    if applied is None:
        return start
    start_frame,end=scene["frame"],scene["end_frame"]
    log=list(start["events"]) if stage_owner is None else stage_owner.events
    world=stage_owner.world if stage_owner else None
    dt=tables.fixed_timestep()
    step=int(dt*ONE)
    board={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    uid=card["operator_id"]
    unit=tables.read("excel/character_table.json")[uid]
    flags=card["elite"],card["level"],card["potential"]
    talent=[active_board(talent_rows(card["module_phase"],i,str(i+1),t["candidates"]),*flags) for i,t in enumerate(unit["talents"][:2])]
    trait=trait_board(unit,card["module_phase"],card)
    evidence=caster_prefab_evidence(tables,uid,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    cs=evidence["character"]["components"]
    emitter,=[c["fields"] for c in cs if c["script"]=="Whitw2Skill3FunnelRangedAttack"]
    period=_raw_f32(talent[0]["interval"])
    deploy_stage=lambda f:min(3,((f-start_frame)*step+_divide(ONE,100000*ONE))//period)
    cast=applied["frame"]
    release=wolf_release_frame(evidence,cast,step)
    active=1+int(board["attack@cnt"])+int(deploy_stage(release)==3)
    keys=emitter["_cruiseProjectileKeys"][:active]
    parts=evidence["projectiles"][keys[0]]["logic"]["components"]
    parent,=[r["fields"] for r in parts if r["script"]=="SimpleProjectile"]
    straight,=[r["fields"] for r in parts if r["path"]=="InitStraightLine" and r["script"]=="AdvancedMovement"]
    curve,=[r["fields"] for r in parts if r["path"]=="TraceTargetCurve" and r["script"]=="AdvancedMovement"]
    ctrl,=[r["fields"] for r in parts if r["script"]=="MovementSwitchControllerForWhitw2"]
    aura,=[b for c in cs if c["script"]=="RangedAttack" and c["path"].endswith("WolvesDispersed/S3Funnel1")
           for b in c["fields"]["_activeBuffs"] if b["templateKey"]=="whitw2_s3_t[slow_and_damage]"]
    if (ctrl["_movementTypes"]!=[0,1,2,3] or ctrl["_periodTimeBBKeys"]!=["times"] or not straight["_awayFromSourceAsDirection"]
            or straight["_moveType"]!=4 or curve["_moveType"]!=1 or not curve["_addInertia"] or not curve["_skipSmoothLerpDirection"]
            or not parent["_actionController"]["_addExtraBuffsWhenReached"] or aura["overrideType"]!=2
            or aura["waitFirstTriggerInterval"] or aura["triggerLifeType"]!=2):
        raise ValueError("荒芜拉普兰德巡航四个运动分支的配置不符")
    place=scene["placement"]
    origin=(place["col"],place["row"])
    angle={"RIGHT":0,"UP":pi/2,"LEFT":pi,"DOWN":-pi/2}[place["direction"]]
    timeline=[]
    wolves=[]
    def make_wolf(index,theta,birth):
        direction=(f32(cos(theta)),f32(sin(theta)))
        pos=tuple(f32(a+f32(emitter["_projectileInitRadius"]*b)) for a,b in zip(origin,direction))
        return dict(id=index,projectile=emitter["_cruiseProjectileKeys"][index],position=pos,direction=direction,
                    birth=birth,mode="spread",speed=straight["_speed"],clock=_raw_f32(board["attack@times"]),
                    target=None,spatial_reach=False,attached_hits=0)
    for i in range(active):
        wolves.append(make_wolf(i,angle+2*pi*i/active,release))
    rows={r["id"]:dict(r) for r in _targets(scene)}
    hp={k:_raw_f32(_number(r,"hp")) for k,r in rows.items()} if stage_owner is None else stage_owner.hp
    normal=range_cells(card["base_range"],place,scene["rows"],scene["cols"])
    if stage_owner is None and any(circle_in_range(r["row"],r["col"],r["radius"],normal,box_query=True) for r in rows.values()):
        return dict(**{**start,"status":"requires_owner_ordinary_attacks_in_parallel"},hp_q32=hp)
    if release>end:
        return dict(**{**start,"status":"before_wolf_release","through_frame":end},hp_q32=hp)
    log.append(dict(frame=release,kind="release_cruise_wolves",projectiles=keys,positions=[w["position"] for w in wolves],stage=deploy_stage(release)))
    radius=board["attack@range_radius"]
    owner_atk=mul(_raw_f32(card["panel_attributes"]["atk"]),ONE+_raw_f32(board["atk"]))
    speed=card["panel_attributes"]["attackSpeed"]+talent[1].get("attack_speed",0)
    attack_interval=stage_owner.interval() if stage_owner else _divide(
        _raw_f32(card["panel_attributes"]["baseAttackTime"]),_divide(_raw_f32(speed),100*ONE))
    attack_period=max(0,attack_interval-step)
    turn=_raw_f32(board["attack@projectile_turn_speed"])
    gain=f32(mul(turn,int(dt*ONE))/ONE)
    if stream is None:
        rng=BattleRandom(scene["native_seed"])
        for _ in range(_integer(scene,"native_prior_draws") if "native_prior_draws" in scene else 0):
            rng.next_double()
    else:
        if "native_seed" in scene or scene.get("native_prior_draws",0):
            raise ValueError("狼群的共享战斗流已经包含初始游标")
        rng=stream
    marks={}
    sources={k:set() for k in rows}
    moving={}
    terrain=scene.get("fear_terrain")
    if terrain is not None:
        from .enemy_fear import FearMotion
        if map_stream is None:
            map_stream=BattleRandom(terrain["map_seed"])
            for _ in range(_integer(terrain,"map_prior_draws") if "map_prior_draws" in terrain else 0):
                map_stream.next_double()
        elif "map_seed" in terrain or terrain.get("map_prior_draws",0):
            raise ValueError("狼群的共享地图流已经包含初始游标")
        for key,row in rows.items():
            if _flag(row,"character_type"):
                continue
            if row["spawn"]<start_frame or row["spawn"]>release:
                return dict(status="requires_enemy_update_order_before_wolves",through_frame=start_frame,events=log,hp_q32=hp)
            moving[key]=FearMotion(tables,enemy=row,terrain=terrain,start_frame=row["spawn"],
                                   stream=rng,map_stream=map_stream)
    seen_events={k:0 for k in moving}
    removals={}
    for row in scene.get("fear_removals",[]):
        frame=_integer(row,"frame")
        if (not release<frame<=end or row.get("phase")!="after_enemy_objects" or row.get("target") not in moving
                or not isinstance(row.get("buff_id"),str)):
            raise ValueError("外部清除恐惧须给出敌人、本次 Buff 和已确认的敌方更新后帧位")
        removals.setdefault(frame,[]).append((row["target"],row["buff_id"]))
    def motion_events(k):
        motion=moving[k]
        for e in motion.events[seen_events[k]:]:
            log.append(dict(e,target=k))
        seen_events[k]=len(motion.events)
        return motion.reason if motion.status=="unresolved" else None

    def move_targets(f):
        if stage_owner is not None:
            stage_owner.receive(f)
            if not world.present:
                return "owner_left"
            stage_owner.routes.advance(f,stage_owner.alive,log,{k:board["attack@move_speed"] for k in marks})
            if world.settle_exits(f,stage_owner.alive) is not None:
                return "stage_defeat"
            stage_owner.tick(f,True)
            for k in world.active[f]:
                rows[k]["row"],rows[k]["col"]=world.positions[f][k]
            return None
        for k,motion in moving.items():
            row=rows[k]
            if hp[k]<=HP_EPSILON or f<row["spawn"]:
                continue
            if f>motion.frame:
                if k in marks and row.get("move_ratio_delta",0):
                    return "requires_move_ratio_combination"
                motion.advance(f,move_ratio_delta=board["attack@move_speed"] if k in marks else row.get("move_ratio_delta",0))
            row["row"],row["col"]=motion.position
            missing=motion_events(k)
            if missing:
                return missing
            if motion.status=="original_checkpoint_reached":
                return "requires_next_ordinary_route_checkpoint"
            if circle_in_range(row["row"],row["col"],row["radius"],normal,box_query=True):
                return "requires_owner_ordinary_attacks_in_parallel"
        return None

    if moving:
        for f in range(min(rows[k]["spawn"] for k in moving),release+1):
            missing=move_targets(f)
            if missing:
                return dict(status=missing,through_frame=f,events=[e for e in log if e["frame"]<=f],hp_q32=hp)
    status="horizon"
    def hit(frame,kind,k,scale,**fields):
        attack=mul(owner_atk,_raw_f32(scale))
        packet=ordinary_arts_hit(attack_raw=attack,resistance_raw=_raw_f32(_number(rows[k],"res",minimum=-1000)),hp_raw=hp[k])
        hp[k]=packet.hp_after_raw
        log.append(dict(frame=frame,kind=kind,target=k,attack_scale=scale,damage_q32=packet.damage_raw,hp_q32=hp[k],**fields))
        if stage_owner is not None and deploy_stage(frame)>=2:
            stage_owner.apply_silence(frame,k,talent[0]["attack@silence_duration"])

    def acquire(w,f,live):
        key=_nearest(w["position"],origin,live)
        if key is False:
            if stage_owner is None:
                return "unresolved_wolf_target_tie"
            key=min(live,key=lambda r:((r["row"]-w["position"][1])**2+(r["col"]-w["position"][0])**2,(r["row"]-origin[1])**2+(r["col"]-origin[0])**2,world.births[r["id"]]["order"]))["id"]
        if key is None:
            if w["mode"]!="hover":
                w.update(mode="hover",target=None,hover=None)
                log.append(dict(frame=f,kind="wolf_entered_hover",wolf=w["id"],position=w["position"]))
            return None
        w.update(target=key,mode="chase",speed=curve["_speed"],spatial_reach=False,attached_hits=0)
        log.append(dict(frame=f,kind="wolf_acquired",wolf=w["id"],target=key,position=w["position"]))
        return None

    f=release
    for f in range(release+1,end+1):
        if (f==scene["skill_end_frame"] if stage_owner else (f-cast)*step>=_raw_f32(card["skill_row"]["duration"])):
            for wolf in wolves:
                wolf.update(mode="returned",target=None)
            log.append(dict(kind="wolves_returned",frame=f,wolves=len(wolves)))
            status="skill_ended"
            break
        if len(wolves)<1+int(board["attack@cnt"])+int(deploy_stage(f)==3):
            extra=make_wolf(len(wolves),angle,f)
            wolves.append(extra)
            log.append(dict(frame=f,kind="release_extra_cruise_wolf",wolf=extra["id"],
                            projectile=extra["projectile"],position=extra["position"]))
        # Each active target ticks before these skill projectiles in this fixture.
        missing=move_targets(f)
        if missing:
            status=missing
            break
        for k,mark in list(marks.items()):
            if hp[k]<=HP_EPSILON or world is not None and k not in world.active[f]:
                del marks[k]
                continue
            if mark["clock"].advance(step):
                hit(f,"wolf_aura_hit",k,board["attack@magic_atk_scale"],phase="OBJECTS")
        live=[r for k,r in rows.items() if r["spawn"]<=f and hp[k]>HP_EPSILON and
              (world is None or k in world.active[f])]
        live_ids={r["id"] for r in live}
        for k,buff_id in removals.get(f,()):
            if buff_id not in moving[k].buffs:
                log.append(dict(frame=f,kind="no_active_fear_to_remove",target=k,buff_id=buff_id))
                continue
            moving[k].remove(f,buff_id)
            motion_events(k)
        for w in wolves:
            if rows and all(h<=HP_EPSILON for h in hp.values()):
                break
            if w["birth"]==f:
                continue
            k=w["target"]
            if w["mode"]=="spread":
                w["clock"]-=step
                if w["clock"]>0:
                    w["speed"]=min(straight["_finalSpeed"],f32(w["speed"]+f32(dt*straight["_deltaSpeedPerSec"])))
                    w["position"]=_xy_step(w["position"],w["direction"],w["speed"],dt)
                    continue
                status=acquire(w,f,[r for r in live if hp[r["id"]]>HP_EPSILON])
            elif w["mode"]=="hover":
                status=acquire(w,f,[r for r in live if hp[r["id"]]>HP_EPSILON])
            elif k is not None and k not in live_ids:
                if w["mode"]=="attached":
                    offset=_respawn(w,rng,ctrl["_randomOffsetMin"],ctrl["_randomOffsetMax"])
                    log.append(dict(frame=f,kind="wolf_lost_attached_target",wolf=w["id"],previous=k,offset=offset,position=w["position"],native_draws=rng.draws))
                    status=acquire(w,f,[r for r in live if hp[r["id"]]>HP_EPSILON])
                else:
                    # Trace state 1 goes directly to hovering on a miss. Its
                    # normal search resumes on the next controller update.
                    status=acquire(w,f,[])
            if status and status!="horizon":
                break
            if w["mode"]=="hover":
                _hover(w,radius,board["attack@projectile_move_speed"],dt)
                continue
            k=w["target"]
            if w["mode"]=="attached":
                w["position"]=(rows[k]["col"],rows[k]["row"])
            elif w["mode"]=="chase":
                if w["spatial_reach"]:
                    w.update(mode="attached",remaining=0)
                    w["position"]=(rows[k]["col"],rows[k]["row"])
                else:
                    w["spatial_reach"]=_chase(w,(rows[k]["col"],rows[k]["row"]),curve,dt,gain)
                    if w["spatial_reach"]:
                        # ActionController's extra buff runs at Reach. The
                        # controller switches to its follow movement next update.
                        w["first_hit"]=f+1
                        fear=0 if _flag(rows[k],"character_type") else board["attack@fear"]
                        log.append(dict(frame=f,kind="wolf_attachment",wolf=w["id"],target=k,
                                        fear_seconds=fear))
                        if fear:
                            if stage_owner is not None:
                                if not rows[k].get("fear_immune"):
                                    stage_owner.routes.apply(f,k,(place["row"],place["col"]),fear,(w["id"],f),log)
                                continue
                            if k not in moving or not all(name in rows[k] for name in ("fear_immune","status_resistance")):
                                status="requires_enemy_fear_route_and_random_stream"
                                break
                            if _flag(rows[k],"fear_immune"):
                                status="requires_fear_immunity_buff_lifecycle"
                                break
                            motion=moving[k]
                            motion.apply(f,(place["row"],place["col"]),fear,f"wolf-{w['id']}-{f}")
                            missing=motion_events(k)
                            if missing:
                                status=missing
                                break
        if status and status!="horizon":
            break
        # The cruise parents were all registered before their subsequent attack
        # children. Resolve all parent flight/selection before child damage.
        attached=sorted((w for w in wolves if w["mode"]=="attached"),key=lambda w:(w["first_hit"],w["id"]))
        for w in attached:
            k=w["target"]
            if f>=w["first_hit"]:
                w["remaining"]-=step
                if w["remaining"]<=0 and hp[k]>HP_EPSILON:
                    w["remaining"]=attack_period
                    cap=trait["max_atk_scale"]*(talent[0]["scale"] if deploy_stage(f)>=1 else 1.)
                    scale=min(cap,trait["init_atk_scale"]+w["attached_hits"]*trait["delta_atk_scale"])
                    hit(f,"wolf_attached_hit",k,scale,wolf=w["id"],silence_seconds=talent[0]["attack@silence_duration"] if deploy_stage(f)>=2 else 0)
                    w["attached_hits"]+=1
        if status and status!="horizon":
            break
        status="horizon"
        for r in live:
            k=r["id"]
            distances=[hypot(w["position"][0]-r["col"],w["position"][1]-r["row"]) for w in wolves]
            reach=radius+r["radius"]
            if stage_owner is None and any(abs(d-reach)<1e-5 for d in distances):
                status="requires_tangent_physics_contact"
                break
            touching={i for i,d in enumerate(distances) if d<=reach}
            if touching==sources[k] or hp[k]<=HP_EPSILON:
                continue
            log.append(dict(frame=f,kind="wolf_aura_contact",target=k,wolves=sorted(touching),phase="PHYSICS"))
            if touching and not sources[k]:
                timer=RecoveryTimer(_raw_f32(aura["triggerInterval"]))
                timer.remaining=0
                marks[k]=dict(clock=timer,first=set(touching))
                if stage_owner is not None:
                    stage_owner.routes.track(k,f)
                log.append(dict(frame=f,kind="wolf_aura_enter",phase="COROUTINES",target=k,slow=board["attack@move_speed"],
                                silence_seconds=talent[0]["attack@silence_duration"] if deploy_stage(f)>=2 else 0))
            elif not touching:
                del marks[k]
                log.append(dict(frame=f,kind="wolf_aura_leave",phase="COROUTINES",target=k))
            elif not touching.issuperset(marks[k]["first"]):
                if stage_owner is None:
                    status="requires_unique_aura_source_handoff"
                    break
                marks[k]["first"]=set(touching)
            sources[k]=touching
        if status!="horizon":
            break
        if scene.get("trace_positions",False):
            timeline.append(dict(frame=f,positions=[w["position"] for w in wolves],
                                 targets={k:m.position for k,m in moving.items() if f>=rows[k]["spawn"]}))
        if stage_owner is not None and world.settled(f,hp,log):
            status="stage_victory_projected"
            break
        if stage_owner is None and rows and all(h<=HP_EPSILON for h in hp.values()):
            status="all_targets_defeated"
            break
        if stage_owner is not None:
            for _ in range(scene["birth_draws"].get(f,0)):
                rng.next_double()
    conditions=(["按关卡对象推进移动、受击、狼群和恐惧"] if stage_owner else
                start["conditions"]+["局部地图 XY 轴与世界方向同向，持续可选的圆形靶，脚下与追踪挂点相同",
                    "敌人的更新先于巡航弹体，检查点内没有其他随机消费者、外部伤害或未输入的移动"])
    return dict(status=status,through_frame=f,events=log,wolves=wolves,positions=timeline,hp_q32=hp,native_draws=rng.draws,
                map_draws=map_stream.draws if map_stream is not None else None,
                target_positions={k:[r["row"],r["col"]] for k,r in rows.items()},
                conditions=conditions)

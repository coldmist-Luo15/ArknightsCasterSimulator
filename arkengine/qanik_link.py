"""Qanipalaat S2 lasso flight, target-owned buffs and landing damage."""
from .animation_assets import read_skin_actions
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_projectiles import stationary_cast_mounts
from .five_star_skills import QANIK
from .fixed import ONE, mul
from .levitate import WalkingLevitate, changeable_airborne, ordinary_levitate
from .lin_output_aura import trigger_contact
from .mystic_timing import fixed_wait_frames
from .qanik_aura import QanikAura, fragility_profile
from .skill_recovery import RecoveryTimer
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def run_qanik_link(tables, card, scene, *, snapshot, choose, mounts, on_lift=None, on_land=None, owner_before=None,
                   prior_hp=None, prior_shots=(),continuation=None):
    """One after-tick button. snapshot returns ordinary live enemy centres in native update order.

    A lifted receiver stays at its impact position. The caller owns motion after
    landing and the initial geometry/HP of off-target neighbours.
    """
    if card["operator_id"]!=QANIK or card["skill"]!=2:
        raise ValueError("坠雪需要雪绒二技能")
    evidence=caster_prefab_evidence(tables,QANIK,2,skill_level=card["skill_level"],
                                    module_id=card["module"],module_level=card["module_level"])
    def fields(script):
        part,=(c["fields"] for c in evidence["skill"]["components"] if c["script"]==script)
        return part
    attack,selector,animation=map(fields,("RangedAttack","AdvancedSelector","ThreePartChannelingAnimation"))
    proj=attack["_projectileKey"]
    parts={c["script"]:c["fields"] for c in evidence["projectiles"][proj]["logic"]["components"]}
    lasso,mover=parts["LassoProjectile"],parts["HarpoonMovement"]
    periodic,=attack["_activeBuffs"]
    templates=tables.read("battle/buff_template_data.json")
    derived=templates[periodic["templateKey"]]["eventToActions"]["ON_BUFF_START"][-1]
    blast=templates[derived["_buff"]["templateKey"]]["eventToActions"]["ON_BUFF_FINISH"][0]
    if (selector["_targetMotion"]!=1 or selector["_postFilter"]!=4 or attack["_selectTargetTiming"]!=0 or
        attack["_atkScale"]!=0 or not attack["_waitForProjectileInvalid"] or not attack["_waitForAnimEndWhenProjectileInvalid"] or
        mover["_acceleration"] or mover["_extendToLenth"] or mover["_keepUpdateTargetpos"] or mover["_onlyReachedInTime"] or
        not lasso["_actionController"]["_detachAllBuffsWhenStopped"] or not derived["_finishDerivedBuffIfParentFinish"] or
        not periodic["waitFirstTriggerInterval"] or blast["_useDynamicTreeForRadius"] or not blast["_useRadius"] or
        blast["_excludeTarget"] or blast["_checkTargetAlive"] or not animation["_waitForAttachFinishEvent"]):
        raise ValueError("套索、附着、落地或收招配置与已解析的普通分支不符")
    bb={v["key"]:v["value"] for v in card["skill_row"]["blackboard"]}
    aura_profile=fragility_profile(tables,card,evidence)
    dt=tables.fixed_timestep()
    step=_raw_f32(dt)
    def ceil_ticks(raw):
        return (raw+step-1)//step
    atk=_raw_f32(card["panel_attributes"]["atk"])
    period=_raw_f32(periodic["triggerInterval"])
    start,end=scene["press_frame"],scene["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<end:
        raise ValueError("坠雪需要有效的按键帧和结束帧")
    aura_first=scene.get("aura_first_update_frame",start+1)
    if type(aura_first) is not int or not 0<=aura_first<=start+1:
        raise ValueError("冰原生存首次对象更新须早于或紧随按键帧")
    place,face=scene["placement"],scene["view"]
    cells=range_cells(card["skill_range"] or card["base_range"],place,scene["rows"],scene["cols"])
    def inside(e):
        return circle_in_range(e["row"],e["col"],e["radius"],cells,box_query=True)
    def airborne(e):
        return changeable_airborne(e["airborne"],e.get("levitate_sequence",0),e.get("ground_bound_sequence",0))
    hp={e["id"]:_raw_f32(e["hp"]) for e in scene["targets"]} if prior_hp is None else prior_hp.copy()
    pool=[e for e in snapshot(start) if hp[e["id"]]>HP_EPSILON and inside(e) and not airborne(e)]
    chosen=[]
    for _ in range(min(len(pool),int(bb["max_target"]))):
        row=choose(pool,start)
        chosen.append(row)
        pool.remove(row)
    if not chosen:
        return dict(status="no_eligible_ground_target",frame=start,cast_frame=None,first_recovery_tick=None,
                    events=[],through_frame=start)
    begin=resolve_cast_start(tables,evidence,frame=start,phase="after_tick",facing=face,direction=place["direction"],
                             sp=scene["sp"],owner_state=scene.get("owner_state","idle"))
    events=list(begin["events"])
    result=dict(events=events,hp_q32=hp,cast_frame=begin["cast_frame"],first_recovery_tick=None)
    if begin["cast_frame"] is None:
        return dict(result,status=begin["status"],through_frame=start)
    release,=(e["frame"] for e in events if e["kind"]=="projectile_release")
    next(e for e in events if e["kind"]=="projectile_release")["targets"]=[e["id"] for e in chosen]
    flights,links,lifted={},{},{}
    ordinary=[dict(shot) for shot in prior_shots]
    auras={key:QanikAura(aura_profile) for key in hp}
    finishing=set()
    shots=[e["id"] for e in chosen]
    last_invalid=None
    stop_phase=None
    def rows_at(f,observer=None):
        return [dict(e,**lifted.get(e["id"],{})) for e in snapshot(f,observer) if hp[e["id"]]>HP_EPSILON]
    def in_air(row):
        return row["id"] in lifted or airborne(row)
    def update_contacts(rows,frame):
        for key,aura in auras.items():
            row=rows.get(key)
            contact=trigger_contact((row["row"],row["col"]),row["radius"],cells) if row else False
            if contact is None:
                if aura.timer is not None or in_air(row):
                    raise ValueError(f"F{frame} 法术脆弱光环落在碰撞体边缘，需要实际接触回调")
                continue
            if contact != aura.touching:
                aura.contact(contact,bool(row and in_air(row)))

    update_contacts({e["id"]:e for e in rows_at(start)},start)
    def damage(f,e,scale,kind,centre=None,**details):
        key=e["id"]
        aura=auras[key]
        hit=ordinary_arts_hit(attack_raw=mul(atk,_raw_f32(scale)),resistance_raw=_raw_f32(e["res"]),hp_raw=hp[key],
                              received_scale_raw=aura.multiplier)
        hp[key]=hit.hp_after_raw
        events.append(dict(frame=f,kind=kind,target=key,damage_q32=hit.damage_raw,hp_after_q32=hp[key],
                           air_fragile=aura.weak,**({"centre":centre} if centre else {}),**details))
        if not hp[key] and key in lifted and key not in finishing:
            land(f,key)
    def land(f,key):
        pos=lifted[key]
        finishing.add(key)
        events.append(dict(frame=f,kind="levitate_finish",target=key,**pos))
        for row in rows_at(f,key):
            if not hp[row["id"]]:
                continue
            dist=(row["row"]-pos["row"])**2+(row["col"]-pos["col"])**2
            if abs(dist-blast["_radius"]**2)<1e-7:
                raise ValueError("落地溅射恰在圆心距离边界")
            if dist<blast["_radius"]**2:
                damage(f,row,bb[blast["_damageScale"]],"landing_arts",key)
        del lifted[key]
        finishing.remove(key)
        if on_land is not None:
            on_land(key,f)
    for f in range(start+1,end+1):
        phases=[]
        rows={e["id"]:e for e in rows_at(f)}
        # All selected receivers already existed when their projectiles were born.
        # Their next object tick precedes the projectile's next object tick.
        retry=(f-aura_first)%10==0
        for key,row in rows.items():
            if not hp[key]:
                continue
            aura=auras[key]
            before=owner_before is None or owner_before(key)
            if retry and before:
                aura.retry(in_air(row))
            link=links.get(key)
            if link is not None:
                if link["timer"].advance(step) and f<=link["buff_end"]:
                    damage(f,row,bb["trigger_atk_scale"],"link_arts")
                if key in lifted and f==link["land"]:
                    land(f,key)
            if hp[key]:
                previous=aura.weak
                aura.tick(step,in_air(row))
                if aura.weak!=previous:
                    events.append(dict(frame=f,kind="qanik_fragility_applied" if aura.weak else "qanik_fragility_removed",
                                       target=key,reason="receiver_poll"))
                if retry and not before:
                    aura.retry(in_air(row))
        for shot in ordinary[:]:
            key=shot["target"]
            row=rows.get(key)
            if row is None or hp[key]<=HP_EPSILON:
                events.append(dict(frame=f,kind="projectile_target_left",target=key,projectile=shot["key"]))
                ordinary.remove(shot)
                continue
            dx,dy,z=shot["hit_offset"]
            mount=(row["col"]+dx,row["row"]+dy,z+row.get("height_offset",0))
            moved=ordinary_trace_step(shot["position"],mount,effective_dt=dt,round_position=True)
            shot["position"]=moved.position
            if moved.reached:
                damage(f,row,1,"projectile_hit",projectile=shot["key"],module_sp=0,target_airborne=in_air(row))
                ordinary.remove(shot)
        if f==release:
            for key in shots:
                if key not in rows or hp[key]<=HP_EPSILON:
                    return dict(result,status="lasso_target_gone_before_release",through_frame=f)
                origin,goal=mounts(rows[key],f)
                flights[key]=(origin,goal)
        for key,(pos,goal) in list(flights.items()):
            if f==release:
                continue
            moved=ordinary_trace_step(pos,goal,effective_dt=dt,round_position=True,speed=mover["_speed"])
            if not moved.reached:
                flights[key]=(moved.position,goal)
                continue
            row=rows.get(key)
            if row is None or hp[key]<=HP_EPSILON:
                events.append(dict(frame=f,kind="link_stopped",target=key,reason="receiver_gone",phase="OBJECTS"))
                del flights[key]
                shots.remove(key)
                phases.append("OBJECTS")
                continue
            admit=ordinary_levitate(bb[derived["_buff"]["durationKey"]],dict(raw_airborne=row["airborne"],
                mass_level=row["mass_level"],levitate_immune=row["levitate_immune"],
                levitate_sequence=row.get("levitate_sequence",0),ground_bound_sequence=row.get("ground_bound_sequence",0)))
            events.append(dict(frame=f,kind="lasso_hit",target=key,**admit))
            lift_time=admit["initial_levitate_lifetime_q32"]
            # Started in OBJECTS. Its immediate MoveNext yields the fixed update;
            # SimulateTick polls the new link in the same frame's COROUTINES.
            links[key]=dict(timer=RecoveryTimer(period),buff_end=f+ceil_ticks(_raw_f32(card["skill_row"]["duration"])),
                            land=f+ceil_ticks(lift_time),expiry=f+ceil_ticks(_raw_f32(lasso["_linkDuration"])))
            if admit["levitate_applied"]:
                lifted[key]=dict(row=row["row"],col=row["col"])
                if on_lift is not None:
                    on_lift(key,f,lift_time,step)
            del flights[key]
        update_contacts(rows,f)
        # The projectile expires in OBJECTS. Its DoLink coroutine also polls the
        # already attached receiver, including on the attachment frame.
        for key,link in list(links.items()):
            row=rows.get(key)
            if f>=link["expiry"]:
                reason,phase="timeout","OBJECTS"
            elif hp[key]<=HP_EPSILON or row is None:
                reason,phase="receiver_gone","COROUTINES"
                if hp[key]<=HP_EPSILON:
                    reason="hp_zero"
            elif row.get("out_of_ground",False):
                reason,phase="out_of_ground","COROUTINES"
            else:
                continue
            events.append(dict(frame=f,kind="link_stopped",target=key,reason=reason,phase=phase))
            if key in lifted:
                land(f,key)
            phases.append(phase)
            del links[key]
            shots.remove(key)
        if not shots and last_invalid is None:
            last_invalid=f
            stop_phase="COROUTINES" if "COROUTINES" in phases else "OBJECTS"
            events.append(dict(frame=f,kind="all_links_invalid"))
        if last_invalid is not None and not ordinary:
            break
    if last_invalid is None:
        return dict(result,status="active_at_horizon",through_frame=end)
    actions=animation["_default"]
    clips=read_skin_actions(tables,"chararts/"+QANIK+".ab","defaultskin",
                            [actions["beginAnim"],actions["endAnim"]])["views"][face.lower()]["actions"]
    begin_clip,end_clip=clips[actions["beginAnim"]],clips[actions["endAnim"]]
    duration=card["skill_row"]["duration"]
    if (begin_clip["speed"]!=1 or end_clip["speed"]!=1
            or duration<=begin_clip["duration"]+end_clip["duration"] or attack["_cooldownKey"]!="duration"):
        raise ValueError("坠雪三段动画的时长缩放发生变化")
    # Animator is started in OnCastStart. The ability's cast coroutine is registered
    # next, then the per-target DoLink coroutines. SimulateTick polls in list order.
    notified=last_invalid+int(stop_phase=="COROUTINES")
    # The finish listener is subscribed before playing Begin. It latches early
    # notifications. Even with that bit set, WaitWhileForFixedSeconds yields once.
    loop_start=begin["cast_frame"]+fixed_wait_frames(single(begin_clip["duration"]/dt))
    end_start=max(loop_start,notified)+1
    cast_end=end_start+fixed_wait_frames(single(end_clip["duration"]/dt))
    # ATTACK_FINISH notifies Character.OnSkillFinish early. Ability.isAffecting
    # still reads isCasting while waiting for ANIM_END. Use the same after-fixed
    # recovery gate as run_committed_cast.
    tail=[(notified,"attack_finish"),(end_start,"end_animation"),(cast_end,"cast_end"),
          (cast_end+2,"first_unblocked_recovery_tick")]
    events.extend(dict(frame=f,kind=k) for f,k in tail if f<=end)
    if continuation is not None:
        continuation.update(hp=hp,auras=auras,sp=begin["sp_after"],shots=ordinary,first_update=f+1,resume=cast_end+2)
    return dict(result,status="cast_ended" if end>=cast_end else "finishing_at_horizon",
                through_frame=min(end,cast_end+2),last_link_frame=last_invalid,cast_end_frame=cast_end,
                first_recovery_tick=cast_end+2)


def stationary_link(tables,card,event):
    """Already deployed caster. The receiver coordinates, hit mounts and current SP are inputs."""
    from .five_star_skills import _targets
    rows=_targets(event)
    place=event["placement"]
    if any(e["level"]!="NORMAL" for e in rows):
        raise ValueError("需计算精英或领袖的模组回技监听")
    if len({row["route_remaining"] for row in rows})!=len(rows):
        raise ValueError("静止目标的原生路程优先级必须明确")
    def mounts(row,f):
        # Explicit three-dimensional muzzle and target positions in this local fixture.
        return tuple(event["muzzle"]),tuple(v+d for v,d in zip((row["col"],row["row"],0),row["hit_offset"]))
    # This optional input is the frame on which the receiver actually gains flag 28,
    # before the projectile's coroutine poll. It can coexist with ordinary levitation.
    for r in rows:
        if "out_of_ground_frame" in r and (type(r["out_of_ground_frame"]) is not int or r["out_of_ground_frame"]<=event["press_frame"]):
            raise ValueError("离地标志须在本次锁定后发生")
    def snapshot(f,observer=None):
        return [dict(r,out_of_ground=f>=r["out_of_ground_frame"]) if "out_of_ground_frame" in r else r for r in rows]
    return run_qanik_link(tables,card,dict(event,view=place["spine_view"]),snapshot=snapshot,
                          choose=lambda pool,f:min(pool,key=lambda r:r["route_remaining"]),mounts=mounts)


def run_qanik_stage(tables,request):
    """Ground-wave ordinary attacks, the committed channel and shared receivers."""
    from .caster_catalog import resolve_caster_loadout
    from .opening_replay import run_opening
    from .ranged_stage import load_ranged_wave
    from .stage_channel import UnsupportedChannelScene,object_position_frame
    from .qanik_battle import run_qanik_battle
    pick,=request["squad"]
    card=resolve_caster_loadout(tables,pick["operator_id"],pick["skill"],pick["module"],
                                skill_level=pick["skill_level"],module_level=pick["module_level"])
    deploy,button=request["actions"]
    if (card["operator_id"]!=QANIK or card["skill"]!=2
        or any(pick[k]!=card[k] for k in ("elite","level","potential"))
        or deploy["operator_id"]!=QANIK or button!=dict(operator_id=QANIK,kind="skill",phase="after_tick",frame=button["frame"])
        or not deploy["frame"]<button["frame"]<=request["end_frame"]):
        raise ValueError("需要雪绒部署和二技能按键")
    open_request=dict(request,actions=[deploy],track_attack_range=True)
    open_request.pop("scene_model",None)
    out_of_ground=open_request.pop("target_out_of_ground_at",{})
    opening=run_opening(open_request,tables=tables)
    placed,event,world=load_ranged_wave(tables,request,opening,enemy_key=None,track_exits=True,allow_flying=True)
    press=button["frame"]
    if not isinstance(out_of_ground,dict) or any(key not in world.births or type(frame) is not int
            or not max(press+1,world.births[key]["frame"])<=frame<=request["end_frame"]
            for key,frame in out_of_ground.items()):
        raise ValueError("离地状态需要实际关卡目标和开技后的生效帧")
    enriched={k:dict(row,radius=tables.enemy_circle(row["prefab"]),airborne=row.get("airborne",False))
              for k,row in world.stats.items()}
    if any(e["apply_way"]!="MELEE" and not e["airborne"] for e in enriched.values()):
        raise UnsupportedChannelScene("此关卡窗口需要无外部能力的近战敌人")
    event["placement"]["spine_view"]=event["view"]
    prior=run_qanik_battle(tables,card,dict(event,end_frame=press,targets=list(world.stats.values())),world=world,
                           ordinary_only=True)
    if prior["status"] not in ("horizon","all_enemies_processed"):
        return dict(status=prior["status"],through_frame=prior["through_frame"],events=prior["events"],
                    hp_q32=prior["hp_q32"],stage=opening["code"],deployment=placed,source=tables.source)
    lift_motion={}
    lift_tracks={}

    def on_lift(key,frame,duration,step):
        samples=[r for kind,r in world.history.get(frame,()) if r["instance_id"]==key and
                 kind in ("free_motion_sample","post_wait_motion_sample","exit_motion_sample")]
        if len(samples)!=1 or not samples[0].get("active_ticks") or samples[0].get("leg_end"):
            raise UnsupportedChannelScene(f"F{frame} 雪绒在路线节点浮空 {key}，需要节点与异常进入顺序")
        lift_motion[key]=WalkingLevitate(frame)
        lift_motion[key].apply(frame,duration,step)
        lift_tracks[key]=dict(lift_motion[key].samples)

    def on_land(key,frame):
        lift=lift_motion[key]
        lift.expires=min(lift.expires,frame)

    def route_sample(key,tick):
        lift=lift_motion[key]
        track=lift_tracks[key]
        for update in range(lift.frame+1,tick+1):
            lift.advance(update)
            track[update]=lift.samples[update]
        return track.get(tick,(tick,0.))

    def snapshot(f,observer=None):
        result=[]
        for k in enriched:
            tick=f if observer in (None,k) else object_position_frame(world.births[k],f,world.positions,world.active,k,
                      observer_born=world.births[observer]["frame"],observer_phase="COROUTINES")
            height=0.
            if k in lift_motion:
                tick,height=route_sample(k,tick)
                if any(kind=="checkpoint_begin" and r["instance_id"]==k and
                       r["command"] in ("WAIT_CURRENT_FRAGMENT_TIME","WAIT_CURRENT_WAVE_TIME")
                       for kind,r in world.history.get(tick,())):
                    raise UnsupportedChannelScene(f"F{f} 雪绒浮空后的路线遇到全局计时检查点")
            if k in world.active[tick]:
                row,col=world.positions[tick][k]
                result.append(dict(enriched[k],row=row,col=col,height_offset=height,
                                   out_of_ground=k in out_of_ground and f>=out_of_ground[k]))
        return result
    def mounts(row,f):
        # Skill_Begin has a static muzzle on both views; Lasso caches its original target point.
        return stationary_cast_mounts(tables,QANIK,dict(action="Skill_Begin"),event["placement"],row,event["view"],
                                      sample_seconds=0)
    def choose(pool,frame):
        return world.select(pool,frame,frame_by_target={row["id"]:frame for row in pool}) if len(pool)>1 else pool[0]
    continuation={}
    result=run_qanik_link(tables,card,dict(event,press_frame=press,sp=prior["sp"],targets=list(enriched.values()),
                          owner_state=prior["owner_state"],aura_first_update_frame=deploy["frame"]+1),
                          snapshot=snapshot,choose=choose,mounts=mounts,on_lift=on_lift,on_land=on_land,
                          owner_before=lambda key:deploy["frame"]<world.births[key]["frame"],
                          prior_hp=prior["hp_q32"],prior_shots=prior["shots"],continuation=continuation)
    result["events"]=[*prior["events"],*result["events"]]
    result.setdefault("hp_q32",prior["hp_q32"])
    if result["cast_frame"] is not None and prior["windup"]:
        old=prior["windup"]
        result["events"].insert(len(prior["events"]),dict(kind="ordinary_windup_interrupted",frame=press,
                                target=old["target"],cancelled_release=old["release"]))
    if continuation and request["end_frame"]>result["first_recovery_tick"]:
        for key,lift in lift_motion.items():
            route_sample(key,request["end_frame"])
            track=lift_tracks[key]
            world.levitations[key]=(min(track),lift.expires,track)
        world.exits[world.exit_index:]=sorted((frame+(lift_motion[key].paused_ticks if key in lift_motion else 0),key)
                                              for frame,key in world.exits[world.exit_index:])
        after=run_qanik_battle(tables,card,dict(event,targets=list(world.stats.values())),world=world,
                               ordinary_only=True,continuation=continuation)
        result.update(through_frame=after["through_frame"],hp_q32=after["hp_q32"],sp=after["sp"],stop=after["status"])
        result["events"].extend(after["events"])
        result["events"].sort(key=lambda e:e["frame"])
    if "hp_q32" in result:
        result["hp_q32"]={k:v for k,v in result["hp_q32"].items() if world.births[k]["frame"]<=result["through_frame"]}
        result["receiver_positions"]={r["id"]:[r["row"],r["col"]] for r in snapshot(result["through_frame"])
                                      if result["hp_q32"].get(r["id"],0)>HP_EPSILON}
    return dict(result,stage=opening["code"],deployment=placed,source=tables.source)

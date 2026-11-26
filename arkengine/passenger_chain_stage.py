"""Passenger's charged S1 replacement and S2 mode in an ordinary unblocked ground wave."""
from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .chain_targets import nearest_chain_target,projectile_hatred_tie
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release, stable_enemy_mount, stationary_cast_mounts
from .fixed import ONE,mul
from .marcil_deploy import _birth
from .module_effects import active_board,talent_rows
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .passenger_storm import passenger_boards,passenger_chain_hit
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import RecoveryTimer,_divide,deploy_skill
from .sluggish_wave import SluggishWave
from .stage_channel import UnsupportedChannelScene
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _projectiles(evidence,slot):
    def ability(root,path):
        r,=(c["fields"] for c in evidence[root]["components"] if c["script"]=="RangedAttack" and c["path"]==path)
        if (r["_useCachedAtkOnly"] or not r["_waitForAttackEvent"] or not r["_waitForProjectileInvalid"] or r["_timeMode"]!=0 or
                r["_waitForAnimEndWhenProjectileInvalid"] or r["_minPostDelayWhenProjectileInvalid"]):
            raise ValueError("异客攻击的烘焙动作或弹体失效等待改变")
        radii=set()
        for v in evidence["projectiles"][r["_projectileKey"]].values():
            p={n["script"]:n["fields"] for n in v["components"] if n["active"] and n["enabled"]}
            hit=p["ChainLightningHitBehaviour"]
            move=p["AdvancedMovement"]
            life=p["SimpleProjectile"]
            if (not hit["_onlyCheckHitWhenReachTarget"] or hit["_selectTargetOnce"] or hit["_waitFirstPeriod"] or
                    move["_moveType"]!=1 or move["_delayToStart"] or move["_delayAfterReached"] or
                    not move["_forceReachedWhenTimeup"] or not move["_clearTraceTargetWhenReached"] or
                    life["_managedBySource"] or life["_stopWhenSourceInvalid"] or
                    p["ChainedSelector"]["_fixAllowRepetitionIfNoTarget"] or any(p["CircleCollider2D"]["m_Offset"].values())):
                raise ValueError("异客连锁弹体的碰撞、近邻或更新方式改变")
            radii.add(p["CircleCollider2D"]["m_Radius"])
        radius,=radii
        return r,radius
    normal=ability("character","Modes/Default/Body/Attack")
    if slot==3:
        return normal,None
    return normal,(ability("skill","") if slot==1 else ability("character","Modes/S2/Body/Attack"))


def chain_flight_profile(evidence, key):
    variants = evidence["projectiles"][key]
    speed, = {c["fields"]["_speed"] for p in variants.values() for c in p["components"]
              if c["script"] == "AdvancedMovement"}
    lifetime, = {c["fields"]["_lifeTime"] for p in variants.values() for c in p["components"]
                 if c["script"] == "SimpleProjectile"}
    return speed, lifetime


def resolve_chain_impact(card,active,trait,talents,hp,marks,radii,frame,shot,live,center,*,isolated,step,nearest_tie):
    count=int(active.get("pasngr_s_1.max_target",active.get("attack@max_target",trait["attack@max_target"])))
    chain=[]
    for _ in range(count):
        key=nearest_chain_target(frame,center,live.values(),radii,chain,shot["radius"],nearest_tie=nearest_tie)
        if key is None:
            break
        chain.append(key)
        center=live[key]
    for bounce,key in enumerate(chain):
        hit=passenger_chain_hit(card,active,trait,talents,hp,marks,frame,live[key],bounce=bounce,isolated=isolated,
                                step=step,projectile_born=shot["release_frame"])
        if hit is None:
            raise UnsupportedChannelScene(f"F{frame} 机理分析刷新与到期的原生先后尚待确认")
        yield dict(kind="chain_impact",frame=frame,release_frame=shot["release_frame"],isolated=isolated,
                   skill_attack=shot["enhanced"],bounce=bounce,position=[live[key]["row"],live[key]["col"]],**hit)


def _attack_intervals(panel,board,evidence):
    base = _raw_f32(panel["baseAttackTime"])
    mode = base
    if "base_attack_time" in board:
        modifiers = [m for c in evidence["skill"]["components"] if c["script"] == "ActiveBuffAbility"
                     for b in c["fields"]["_buffs"] if b["buffKey"] == "pasngr_s_2"
                     for m in b["attributes"]["attributeModifiers"] if m["attributeType"] == 8]
        if len(modifiers) != 1 or modifiers[0]["formulaItem"] != 1 or not modifiers[0]["loadFromBlackboard"]:
            raise ValueError("聚焦指令的基础攻击间隔乘区已变更")
        mode = mul(base,ONE + _raw_f32(board["base_attack_time"]))
    return tuple(single(single(value/ONE)*100/single(panel["attackSpeed"])) for value in (base,mode))


def run_passenger_chain_stage(tables,request):
    uid="char_472_pasngr"
    buttons = [a for a in request["actions"] if a.get("kind") == "skill"]
    with_button = bool(buttons)
    base = dict(request, actions=[a for a in request["actions"] if a not in buttons[1:]])
    card,stage,deploy,event,world=prepare_ranged_stage(tables,base,operator=uid,manual=with_button,
                                             portable=True,track_exits=True,simulate_before_button=True)
    slot=card["skill"]
    if slot not in (1,2) or slot==1 and with_button:
        raise ValueError("异客一技能自动触发，二技能由手动输入触发")
    ev=caster_prefab_evidence(tables,uid,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    normal,special=_projectiles(ev,slot)
    flight={ability["_projectileKey"]:chain_flight_profile(ev,ability["_projectileKey"]) for ability,_ in (normal,special)}
    if slot==1:
        replacement,=(r["fields"] for r in ev["skill"]["components"] if r["script"]=="ReplacementSkill")
        if not replacement["_recoverSpIfTargetDead"]:
            raise ValueError("电能之触失去目标时的回技配置已变化")
    _,talents,trait=passenger_boards(tables,card)
    board={v["key"]:v["value"] for v in card["skill_row"]["blackboard"]}
    isolation_rate=active_board(talent_rows(card["module_phase"] or {},1,"10",[]),card["elite"],card["level"],card["potential"]).get("sp_recovery_per_sec",0)
    passive,=(c["fields"]["_buffs"][0] for c in ev["character"]["components"] if c["script"]=="PassiveBuffAbility" and c["path"]=="Talents/2")
    if isolation_rate:
        equipment,=(n["fields"]["_buffs"][0] for p in ev["equipment"].values() for n in p["components"] if n["script"]=="PassiveBuffAbility")
        if any(equipment[k]!=passive[k] for k in ("triggerInterval","firstTriggerInterval","waitFirstTriggerInterval","blackboard")):
            raise ValueError("异客模组的孤卒回技光环与攻击光环的触发周期不同")
    if passive["templateKey"]!="pasngr_t_2" or not passive["waitFirstTriggerInterval"] or passive["firstTriggerInterval"]!=-1:
        raise ValueError("孤卒的首次检查时间发生变化")
    place,face=event["placement"],event["view"]
    start,end=event["frame"],event["end_frame"]
    dt=tables.fixed_timestep()
    previous_press = start
    for action in buttons:
        f = action["frame"]
        if action != dict(kind="skill", operator_id=uid, frame=f, phase="after_tick") or not previous_press<f<=end:
            raise ValueError("聚焦指令需要依次送达的帧末按键")
        previous_press = f
    presses = {a["frame"] for a in buttons}
    skill_clock = None
    if with_button:
        skill_clock = run_committed_cast(tables, ev, horizon=0, facing=face, direction=place["direction"])["natural_end"]
        if skill_clock["end_phase"] != "objects":
            raise ValueError("聚焦指令的自然结束发生在未核对的阶段")
    replay,_=deploy_skill(tables,card,start)
    g,step=replay.state,replay.step
    periodic=RecoveryTimer(_raw_f32(passive["triggerInterval"]))
    fast=_divide(ONE,ONE+_raw_f32(isolation_rate))
    isolated=False
    base_range=range_cells(card["base_range"],place,event["rows"],event["cols"])
    mode_range=skill_range_cells(tables,card,event)["cells"]
    aura=range_cells(tables.read("excel/range_table.json")[passive["blackboard"][0]["valueStr"]],place,event["rows"],event["cols"])
    interval=_attack_intervals(card["panel_attributes"],board,ev)
    hp={k:_raw_f32(r["hp"]) for k,r in world.stats.items()}
    radii={k:tables.enemy_circle(r["prefab"]) for k,r in world.stats.items()}
    marks={}
    walks=SluggishWave(tables,world,continue_route=True)
    log=[]
    mode=mode_end=end+1
    poll=start+_birth(tables,card,ev,face)["born_exit_update"]+1
    stop="horizon"
    current = None
    shots = []
    stopped=False
    resume=None
    sign=1 if place["direction"]!="LEFT" else -1
    for frame in range(start,end+1):
        if any(f<=frame and hp[k]>HP_EPSILON for k,f in walks.boundaries.items()):
            frame-=1
            stop="sluggish_route_checkpoint_or_overlap"
            break
        if world.settle_exits(frame,lambda k:hp[k]>HP_EPSILON) is not None:
            frame,stop=world.defeated_at,"stage_defeat"
            break
        if frame==start:
            log.append(dict(kind="deploy",frame=frame,sp=g.points))
        else:
            if frame==resume:
                stopped=False
                log.append(dict(kind="replacement_sp_unblocked" if slot == 1 else "skill_sp_unblocked",frame=frame))
            if periodic.advance(step):
                near=any(hp[r["id"]]>HP_EPSILON and circle_in_range(r["row"],r["col"],radii[r["id"]],aura,box_query=True)
                         for r in world.snapshot(frame))
                if near==isolated:
                    isolated=not near
                    log.append(dict(kind="isolation_changed",frame=frame,isolated=isolated))
                if isolation_rate:
                    set_period(g,fast if isolated else ONE,stopped=stopped)
            previous=g.points
            g.advance(step,stopped=stopped)
            if previous<g.cost<=g.points:
                log.append(dict(kind="first_charge_available",frame=frame,sp=g.points))
        if frame==mode_end:
            if current and current["release_frame"] >= frame:
                shots.remove(current)
                log.append(dict(kind="attack_interrupted_by_mode_exit",frame=frame,target=current["target"]))
            current = None
            poll = frame+1
            log.append(dict(kind="skill_mode_ended",frame=frame))
        if frame==mode+1:
            poll=frame
        in_mode = mode < frame < mode_end
        if frame>=poll and (frame!=mode or mode==end+1):
            cells=mode_range if in_mode else base_range
            rows=[r for r in world.snapshot(frame) if hp[r["id"]]>HP_EPSILON and
                  circle_in_range(r["row"],r["col"],radii[r["id"]],cells,box_query=True)]
            if not rows:
                poll=frame+3
            elif current is not None:
                poll = frame+1
            else:
                primary=world.select(rows,frame) if len(rows)>1 else rows[0]
                enhanced=in_mode or slot==1 and g.points>=g.cost
                if enhanced and slot==1:
                    cast=resolve_special_start(tables,ev,frame=frame,phase="before_coroutines",direction=place["direction"],
                                               facing=face,source="before_attack",sp=g.points,trigger_search_passed=True)
                    if cast["status"]!="replacement_registered":
                        raise ValueError("异客替换攻击未成功注册")
                    log.extend(cast["events"])
                    g.points=cast["sp_after"]
                    stopped=True
                ability,radius=special if enhanced else normal
                duration=interval[int(in_mode)]
                shot=ranged_shot_release(tables,ev,ability,attack_frame=frame,attack_interval=duration,facing=face)
                if primary["col"]!=place["col"]:
                    sign=1 if primary["col"]>place["col"] else -1
                shot.update(target=primary["id"],enhanced=enhanced,radius=radius,projectile=ability["_projectileKey"],
                            start=frame,sign=sign)
                shots.append(shot)
                current = shot
                log.append(dict(kind="attack_start",frame=frame,target=primary["id"],skill_attack=enhanced,sp=g.points))
                poll=frame+fixed_wait_frames(single(duration/dt))
        for shot in shots[:]:
            release,key=shot["release_frame"],shot["target"]
            if frame<release:
                continue
            if frame==release:
                target=world.receiver_at(key,frame) if hp[key]>HP_EPSILON else None
                if target is None:
                    refund=0
                    if shot["enhanced"] and slot==1:
                        refund=min(g.cost,g.maximum-g.points)
                        g.points+=refund
                        resume=frame+1
                    log.append(dict(kind="attack_target_invalid_at_release",frame=frame,target=key,refunded_sp=refund))
                    shots.remove(shot)
                    if current is shot:
                        current=None
                        poll=frame+1
                    continue
                shot["position"],shot["last_mount"]=stationary_cast_mounts(tables,uid,shot["clock"],place,target,face,
                                                                            caster_face_sign=shot["sign"])
                log.append(dict(kind="projectile_release",frame=frame,projectile=shot["projectile"],target=key))
                continue
            target=world.by_id(key,frame,projectile_born=release) if hp[key]>HP_EPSILON else None
            if target is not None:
                shot["last_mount"]=stable_enemy_mount(tables,target)
            speed,lifetime=flight[shot["projectile"]]
            movement=ordinary_trace_step(shot["position"],shot["last_mount"],effective_dt=dt,
                                         round_position=True,speed=speed)
            shot["position"]=movement.position
            if not movement.reached and frame-release<fixed_wait_frames(single(lifetime/dt))+1:
                continue
            center=target if target is not None else dict(row=movement.position[1],col=movement.position[0])
            if target is None:
                log.append(dict(kind="chain_reached_lost_target",frame=frame,target=key,release_frame=release))
            live={r["id"]:dict(r,max_hp=r["hp"],spawn=world.births[r["id"]]["frame"])
                  for r in world.snapshot(frame,projectile_born=release) if hp[r["id"]]>HP_EPSILON}
            active=board if shot["enhanced"] else {}
            if slot == 2:
                # The projectile retains its delivery parameters. Damage uses live source ATK.
                active = dict(active, atk=board["atk"] if in_mode else 0)
            for hit in resolve_chain_impact(card,active,trait,talents,hp,marks,radii,frame,shot,live,center,
                    isolated=isolated,step=step,nearest_tie=lambda keys:projectile_hatred_tie(world,keys,frame,release)):
                k=hit["target"]
                log.append(hit)
                if hp[k]>HP_EPSILON:
                    ticks=(_raw_f32(hit["sluggish_seconds"])+step-1)//step
                    walks.apply(k,frame,shot["release_frame"],ticks,end)
                    log.append(dict(kind="sluggish_applied",frame=frame,target=k,last_slow_move_tick=walks.expiry.get(k)))
            if slot==1 and shot["enhanced"]:
                elapsed=frame-shot["start"]
                clock=_after_spell_clock(elapsed,shot["clock"]["cached_duration_seconds"],step,same_frame_resume=True)
                if clock["end"]<=elapsed:
                    stop="projectile_arrives_after_animation_end"
                    break
                resume=shot["start"]+clock["first_recovery_tick"]
                log.append(dict(kind="replacement_projectile_invalid",frame=frame,
                                attack_end_frame=shot["start"]+clock["end"],first_recovery_tick=resume))
            shots.remove(shot)
            if current is shot:
                current = None
        if stop != "horizon":
            break
        if frame in presses:
            if g.points<g.cost or in_mode or stopped:
                log.append(dict(kind="skill_button_rejected",frame=frame,sp=g.points))
                continue
            cast=resolve_cast_start(tables,ev,frame=frame,phase="after_tick",direction=place["direction"],facing=face,
                                    owner_state="attack" if current else "idle",trigger="MANUAL",sp=g.points)
            if cast["status"]!="cast_and_active_buff_spell":
                raise ValueError("异客未进入二技能切换模式")
            if current and current["release_frame"] > frame:
                shots.remove(current)
                log.append(dict(kind="attack_interrupted_by_skill_input",frame=frame,target=current["target"]))
            current = None
            log.extend(cast["events"])
            mode=cast["spell_frame"]
            mode_end = cast["cast_frame"] + skill_clock["end"]
            resume = cast["cast_frame"] + skill_clock["first_recovery_tick"]
            stopped=True
            g.points=cast["sp_after"]
        if world.all_processed(hp):
            stop="all_enemies_processed"
            break
    result=dict(status="passenger_moving_chain",through_frame=frame,stop=stop,sp=g.points,
                events=sorted((e for e in log if e["frame"]<=frame),key=lambda r:r["frame"]),remaining_hp_q32=hp)
    world.stage_result(result,fixed_timestep=dt,target_field="target",hp_field="hp_q32",projection_horizon=end)
    return dict(stage=stage["code"],deployment=deploy if frame>=start else None,**result,source=tables.source)

"""Ceobe's ranged attacks, skill replacements and root against ordinary ground targets."""
from math import isfinite

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import _after_spell_clock, run_committed_cast
from .caster_equipment_traits import _trait_values, module_branch_trait
from .caster_opening import deploy_to_first_cast
from .caster_prefabs import caster_prefab_evidence
from .caster_special_start import resolve_special_start
from .caster_start import resolve_cast_start
from .ceobe_lone import ceobe_alone
from .core_caster_skills import _ceobe, _talent, ceobe_peel_ratio
from .damage import HP_EPSILON
from .elemental_projectiles import moving_ranged_shot, stationary_ranged_shot
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .mode_replay import _AttackCooldown
from .mystic_timing import fixed_wait_frames
from .skill_recovery import READY_EPSILON,deploy_skill
from .stage_channel import object_position_frame
from .volley_damage import _raw_f32


class _BoundRoutes:
    """Hold the original walking cursor and steering velocity between accepted root hits and expiry."""

    def __init__(self,world):
        self.base=world
        self.holds={}

    def route_frame(self,key,tick):
        return tick-sum(max(0,min(tick,end)-start) for start,end in self.holds.get(key,()))

    def by_id(self,key,frame,*,projectile_born=None):
        world=self.base
        tick=object_position_frame(world.births[key],frame,world.positions,world.active,key,
            observer_born=world.deployment_frame if projectile_born is None else projectile_born,
            observer_phase="INPUT" if projectile_born is None else "COROUTINES")
        tick=self.route_frame(key,tick)
        if key not in world.active[tick]:
            return None
        row,col=world.positions[tick][key]
        tile=world.tiles[round(row),round(col)]
        if tile["locate_height_offset"] or tile["position"]["z"]!=0:
            raise ValueError("束缚目标需要已解析高度的地面路线")
        return dict(world.stats[key],row=row,col=col,route_frame=tick)

    def snapshot(self,frame):
        return [e for key in self.base.stats if (e:=self.by_id(key,frame)) is not None]

    def select(self,rows,frame):
        return self.base.select(rows,frame,frame_by_target={e["id"]:e["route_frame"] for e in rows})

    def apply(self,key,frame,ticks):
        spans=self.holds.setdefault(key,[])
        route=self.route_frame(key,frame)
        if not spans or spans[-1][1]<frame:
            # PredictReached may snap directly at a checkpoint before steering.
            # On the middle of a WALK leg canMove gates every position delta.
            moving={"free_motion_sample","post_wait_motion_sample","exit_motion_sample"}
            samples=[[r for kind,r in self.base.history.get(f,()) if kind in moving and r["instance_id"]==key]
                     for f in (route-1,route,route+1)]
            if (any(len(rows)!=1 or rows[0].get("leg_end") for rows in samples) or
                    any(b[0].get("active_ticks",0)!=a[0].get("active_ticks",0)+1 for a,b in zip(samples,samples[1:]))):
                return False
            spans.append((frame,frame+ticks))
            extra=ticks
        else:
            start,old=spans[-1]
            extra=max(0,frame+ticks-old)
            spans[-1]=(start,old+extra)
        world=self.base
        world.exits[world.exit_index:]=sorted((f+extra if k==key else f,k) for f,k in world.exits[world.exit_index:])
        return True

    def clear_before_update(self, key, frame):
        start, end = self.holds[key][-1]
        self.holds[key][-1] = (start, frame-1)
        removed = end-frame+1
        world = self.base
        world.exits[world.exit_index:] = sorted((f-removed if k==key else f,k) for f,k in world.exits[world.exit_index:])


def run_ceobe_battle(tables,card,event,board,*,world=None):
    slot = card["skill"]
    start,end = event["frame"],event["end_frame"]
    place,face = event["placement"],event["view"]
    alone = ceobe_alone(tables,card,event,event["units"]) if "units" in event else event["no_adjacent_ally"]
    alone_at_deploy = (ceobe_alone(tables,card,event,event["units_at_deploy"]) if "units_at_deploy" in event
                       else event.get("no_adjacent_ally_at_deploy",alone))
    if (type(alone) is not bool or type(alone_at_deploy) is not bool or type(event["round_position"]) is not bool or type(start) is not int or
            type(end) is not int or not 0<=start<=end or face not in ("Front","Back")):
        raise ValueError("需要明确的相邻队友状态、坐标取整模式、截止帧及默认皮肤视图")
    evidence = caster_prefab_evidence(tables,card["operator_id"],slot,skill_level=card["skill_level"],
                                      module_id=card["module"],module_level=card["module_level"])
    path = "" if slot==1 else f"Modes/S{slot}/Body/Attack"
    parts = evidence["skill" if slot==1 else "character"]["components"]
    attack, = (c["fields"] for c in parts if c["path"]==path and c["script"]=="RangedAttack")
    selector_path = "Selector" if slot==1 else path+"/Trigger"
    select, = (c["fields"] for c in parts if c["path"]==selector_path and c["script"]=="AdvancedSelector")
    if (select["_postFilter"]!={1:22,2:8,3:9}[slot] or select["_sortByTauntAtLast"] or attack["_waitForProjectileInvalid"]
            or attack["_damageType"]!={1:2,2:2,3:1}[slot] or attack["_resetCdStrategy"]!=1):
        raise ValueError("刻俄柏当前技能的索敌、伤害类型或攻击时钟发生变化")
    mode = None
    normal,=(p["fields"] for p in evidence["character"]["components"]
             if p["path"]=="Modes/Default/Body/Attack" and p["script"]=="RangedAttack")
    if normal["_useCachedAtkOnly"] or attack["_useCachedAtkOnly"]:
        raise ValueError("刻俄柏的普通或技能弹体攻击力读取方式已改变")
    born_idle=start+_birth(tables,card,evidence,face)["born_exit_update"]+1
    gauge,_=deploy_skill(tables,card,start)
    logs=[dict(frame=start,kind="deploy",sp=gauge.state.points)]
    if slot==1:
        if "press_frame" in event:
            raise ValueError("很冰的斧在场上自动替换普通攻击")
        first_attack=born_idle
        routes=_BoundRoutes(world) if world is not None else None
    else:
        manual = event.get("press_frame")
        if event.get("hold_manual_skill"):
            if manual is not None:
                raise ValueError("保留手动技能时不能同时指定按键")
            manual=max(end+1,born_idle)
        if manual is None:
            opening=deploy_to_first_cast(tables,card,evidence,deploy_frame=start,end_frame=end,facing=face,
                direction=place["direction"],manual_phase=event.get("press_phase","input"))
            if opening.cast is None:
                return dict(status="before_manual_cast",events=logs,sp=opening.sp,through_frame=end)
            manual=opening.cast["events"][0]["frame"]
        if type(manual) is not int or manual<born_idle-1:
            raise ValueError("手动开技需要已结束出生状态")
        ability, = (c["fields"] for c in evidence["skill"]["components"] if c["script"]=="ActiveBuffAbility")
        mode_buff, = ability["_buffs"]
        mode_actions=evidence["templates"][mode_buff["templateKey"]]["eventToActions"]
        switch, = mode_actions["ON_BUFF_START"]
        restore, = mode_actions["ON_BUFF_FINISH"]
        if ability["_waitForCasting"] or ability["_escapeTime"] or switch["_restartFSM"]!=(slot==2):
            raise ValueError("需要重新核对切换模式后的首次干员状态更新")
        if not restore["_restoreDefault"] or restore["_restartFSM"]!=(slot==2):
            raise ValueError("技能自然结束的模式和状态机选项已变化")
        clock = run_committed_cast(tables, evidence, horizon=0, facing=face,
                                   direction=place["direction"])["natural_end"]
        if clock["end_phase"] != "objects" or not clock["first_recovery_tick"] == clock["end"]+1:
            raise ValueError("刻俄柏姿态增益需要在持有者的对象更新中到期")
        def plan(press):
            cast = resolve_cast_start(tables,evidence,frame=press,direction=place["direction"],facing=face,
                owner_state="idle",trigger="MANUAL",phase=event.get("press_phase","input"),sp=gauge.state.cost)
            if cast["status"]!="cast_and_active_buff_spell":
                raise ValueError("未确认刻俄柏姿态切换")
            mode_frame = cast["spell_frame"]
            # The non-restarting S3 path has a final fixed wait before returning to IDLE.
            return cast,mode_frame,mode_frame+(1 if switch["_restartFSM"] else 2),mode_frame+clock["end"]-clock["spell_frame"]
        cast,mode,first_attack,mode_end = plan(manual)
        initial_mode = mode
        additional_presses = set(event.get("additional_presses", ()))
    initial_attack = first_attack
    cells = range_cells(card["skill_range"] or card["base_range"],place,event["rows"],event["cols"])
    base_cells=range_cells(card["base_range"],place,event["rows"],event["cols"])
    targets,states,radii = {},{},{}
    for enemy in event["targets"]:
        key = enemy["id"]
        if (key in states or not all(isfinite(enemy[k]) and enemy[k]>=0 for k in ("defense","res","hp"))
                or world is None and (not all(isfinite(enemy[k]) for k in ("row","col","tile_z","remaining_route_distance"))
                                      or enemy["remaining_route_distance"]<0 or enemy["col"]==place["col"])
                or any(type(enemy.get(k,False)) is not bool for k in ("silence_immune","root_immune"))
                or slot==1 and type(enemy.get("blocked")) is not bool):
            raise ValueError("静止靶需要唯一实例、非负属性、剩余路程及确定的左右朝向")
        states[key] = EnemyElements.from_scene_enemy(tables,dict(enemy,hp_q32=_raw_f32(enemy["hp"])))
        radii[key] = tables.enemy_circle(enemy["prefab"])
        if world is not None or any(circle_in_range(enemy["row"],enemy["col"],radii[key],area,box_query=True)
                                    for area in (cells,base_cells)):
            targets[key] = enemy
    snapshot = "cerber_e_003[t_2]" in evidence["templates"]
    lone_active = alone_at_deploy if snapshot else alone
    lone_talent = _talent(tables,card,1)
    lone = lone_talent if lone_active else {}
    panel = card["panel_attributes"]
    interval = single(single(single(panel["baseAttackTime"])*single(board.get("base_attack_time",1)))
                      *100/single(panel["attackSpeed"]+lone.get("attack_speed",0)))
    dt = tables.fixed_timestep()
    period = fixed_wait_frames(single(interval/dt))
    base_interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]+lone.get("attack_speed",0)))
    base_period=fixed_wait_frames(single(base_interval/dt))
    base_strength=mul(_raw_f32(panel["atk"]),ONE+_raw_f32(lone.get("atk",0)))
    strength = mul(_raw_f32(panel["atk"]),ONE+_raw_f32(lone.get("atk",0))+_raw_f32(board.get("atk",0)))
    skill_scale = _raw_f32(board.get("atk_scale",1))
    peel = _talent(tables,card,0)
    trait = _trait_values(card)
    penetration = _raw_f32(trait.get("magic_resist_penetrate_fixed",0))
    through = end
    last = None
    repeats = 0
    # Each enemy is targetable from the first search in the new mode, with equal ordinary taunt.
    property_key = "blocked" if slot==1 else "defense"
    ordered = [] if world is not None else sorted(targets.values(),key=lambda e:e["remaining_route_distance"])
    if len({(e[property_key],e["remaining_route_distance"]) for e in ordered})!=len(ordered):
        raise ValueError("技能优先属性及剩余路程完全相同的目标需要更详细的原生优先级")
    stage_routes = routes if slot==1 else world
    first_ready=start if gauge.state.points>=gauge.state.cost else None
    if slot==1:
        cost = gauge.state.cost
        legacy_entry = world is None and "target_entry_frame" not in event
        legacy_entry_at = first_attack if first_ready is not None else end+1
        next_skill_poll = start+1
        registered = False
        casting_until = -1
        sp_modifier = False
    def effective_defense(key):
        return states[key].effective_defense(_raw_f32(targets[key]["defense"]))

    def select_target(frame,skill_attack):
        area=cells if skill_attack else base_cells
        if world is None:
            if frame<event.get("target_entry_frame",start if slot==1 else initial_attack) or slot==1 and legacy_entry and frame<legacy_entry_at:
                return None
            visible=[e for e in ordered if circle_in_range(e["row"],e["col"],radii[e["id"]],area,box_query=True)]
        else:
            visible=[e for e in stage_routes.snapshot(frame) if
                circle_in_range(e["row"],e["col"],radii[e["id"]],area,box_query=True)]
        candidates=[e for e in visible if states[e["id"]].hp>HP_EPSILON]
        if not candidates:
            return None
        if skill_attack:
            if slot in (2,3):
                candidates=[dict(e,defense=effective_defense(e["id"])) for e in candidates]
            key=_ceobe(tables,card,dict(kind="target",ordered_eligible=candidates),board)["target"]
            attr=next(e[property_key] for e in candidates if e["id"]==key)
            candidates=[e for e in candidates if e[property_key]==attr]
        if len(candidates)==1:
            return candidates[0]
        if world is not None:
            return stage_routes.select(candidates,frame)
        if candidates[0]["remaining_route_distance"]==candidates[1]["remaining_route_distance"]:
            raise ValueError(f"F{frame} 普通索敌需要原生完全同优先级实例顺序")
        return candidates[0]

    def register(frame,source):
        nonlocal sp_modifier
        result=resolve_special_start(tables,evidence,frame=frame,phase="before_coroutines",
            direction=place["direction"],facing=face,source=source,sp=gauge.state.points,trigger_search_passed=True)
        if result["status"]!="replacement_registered":
            raise ValueError("替换技能触发未成功")
        gauge.state.points=result["sp_after"]
        # BasicSkill.OnCastSucceed immediately registers the SP modifier.
        sp_modifier=True
        logs.extend(result["events"])

    next_attack=next_default_poll=born_idle
    attack_cooldown=None
    allies={r["id"]:r for r in event.get("units",())}
    ally_changes={}
    if event.get("ally_unit_events"):
        checker,=(r["fields"] for r in evidence["character"]["components"]
                  if r["path"]=="Talents/2" and r["script"]=="AdjacentAllyToggleChecker")
        if (checker["_distanceType"],checker["_minManhattan"],checker["_maxManhattan"],checker["_checkType"])!=(0,2,-1,1):
            raise ValueError("独行长路的邻格事件与距离过滤已变化")
    for change in event.get("ally_unit_events",()):
        frame,kind=change["frame"],change["kind"]
        if type(frame) is not int or not start<=frame<end or kind not in ("ally_deploy","ally_withdraw"):
            raise ValueError("相邻单位变化需要已部署后的帧末输入")
        ally_changes.setdefault(frame+1,[]).append(change)
    shots,impacts,invalid={},{},{}
    root_expires={}
    resumed={}
    old_attack_end=-1
    last_skill_attack_end=-1
    stop="horizon"
    external_hits={}
    for hit in event.get("buildup_before_attacks",()):
        phase=hit.get("phase","before_attack_search")
        if (hit["target"] not in targets or type(hit["frame"]) is not int or not start < hit["frame"] <= end
                or phase not in ("before_attack_search","after_attack_search")):
            raise ValueError("元素损伤需要明确的本场目标及部署后的逻辑帧")
        external_hits.setdefault((hit["frame"],phase),[]).append(hit)

    def apply_external_buildup(frame,phase):
        for hit in external_hits.get((frame,phase),()):
            key=hit["target"]
            if world is not None and key not in world.active[frame]:
                raise ValueError("元素损伤的敌人尚未入场或已经离场")
            state=states[key]
            before=len(state.events)
            state.apply_buildup(frame,hit["element"],hit["amount_q32"])
            logs.extend(dict(row,target=key) for row in state.events[before:])
    root_clears = {}
    for clear in event.get("root_clears_before_objects", ()):
        key, frame = clear["target"], clear["frame"]
        if slot!=1 or key not in targets or type(frame) is not int or not start<frame<=end:
            raise ValueError("提前解除束缚需指定已部署刻俄柏一技能和有效受击者的对象更新帧")
        root_clears.setdefault(frame, set()).add(key)
    silence_clears={}
    for clear in event.get("silence_clears", ()):
        key, frame, phase=clear["target"],clear["frame"],clear["phase"]
        if slot!=3 or key not in targets or type(frame) is not int or not start<frame<=end or phase not in ("before_objects","after_objects"):
            raise ValueError("解除沉默需要三技能、有效目标及已知对象更新相位")
        silence_clears.setdefault((frame,phase),set()).add(key)
    for probe in event.get("silence_checks_after_objects", ()):
        if (slot!=3 or probe["target"] not in targets or type(probe["frame"]) is not int
                or not start<probe["frame"]<=end):
            raise ValueError("沉默检查需要三技能、有效目标及在场帧")

    def cleanse(frame,phase):
        for key in sorted(silence_clears.get((frame,phase),())):
            states[key].silence.clear()
            logs.append(dict(kind="silence_cleared",frame=frame,phase=phase,target=key))

    frame=start
    for frame in range(start+1,through+1):
        if frame in ally_changes:
            for change in ally_changes[frame]:
                if change["kind"]=="ally_deploy":
                    ally=change["unit"]
                    allies[ally["id"]]=ally
                else:
                    allies.pop(change["id"],None)
            new_lone=lone_active if snapshot else ceobe_alone(tables,card,event,allies.values())
            if new_lone!=lone_active:
                lone_active=new_lone
                active=slot!=1 and mode<frame<mode_end
                lone=lone_talent if lone_active else {}
                base_interval=single(single(panel["baseAttackTime"])*100/single(panel["attackSpeed"]+lone.get("attack_speed",0)))
                interval=single(single(single(panel["baseAttackTime"])*single(board.get("base_attack_time",1)))
                                *100/single(panel["attackSpeed"]+lone.get("attack_speed",0)))
                base_period,period=(fixed_wait_frames(single(seconds/dt)) for seconds in (base_interval,interval))
                base_strength=mul(_raw_f32(panel["atk"]),ONE+_raw_f32(lone.get("atk",0)))
                strength=mul(_raw_f32(panel["atk"]),ONE+_raw_f32(lone.get("atk",0))+_raw_f32(board.get("atk",0)))
                if attack_cooldown is not None and frame<=next_attack:
                    phase=frame-1
                    attack_cooldown.change_period(phase,gauge.step,_raw_f32(interval if active else base_interval))
                    steps=max(1,(attack_cooldown.remaining-READY_EPSILON+gauge.step-1)//gauge.step)
                    next_attack=phase+steps
                logs.append(dict(kind="lone_talent_changed",frame=frame,active=lone_active,next_attack_frame=next_attack))
        cleanse(frame,"before_objects")
        for key in sorted(root_clears.get(frame, ())):
            if key in root_expires and frame <= root_expires[key]:
                root_expires.pop(key)
                if routes is not None:
                    routes.clear_before_update(key, frame)
                    resumed[key] = frame
                logs.append(dict(kind="root_cleared",frame=frame,target=key,first_unbound_move_tick=frame))
        if world is not None and world.settle_exits(frame,lambda k:states[k].hp>HP_EPSILON) is not None:
            stop="stage_defeat"
            break
        if external_hits:
            for key,state in states.items():
                if world is None or key in world.active[frame]:
                    state.tick(frame)
            apply_external_buildup(frame,"before_attack_search")
        for key,expiry in tuple(root_expires.items()):
            if frame==expiry:
                logs.append(dict(kind="root_expired",frame=frame,target=key,first_unbound_move_tick=frame+1))
                del root_expires[key]
                if world is not None:
                    resumed[key]=frame+1
        for key,tick in tuple(resumed.items()):
            if frame==tick:
                del resumed[key]
                if states[key].hp>HP_EPSILON and key not in root_expires:
                    route=routes.route_frame(key,frame)
                    logs.append(dict(kind="first_walk_after_root",frame=frame,target=key,route_frame=route,
                        previous_position=list(world.positions[route-1][key]),position=list(world.positions[route][key])))
        primary=None
        skill_active = slot!=1 and mode<frame<mode_end
        skill_attack = skill_active and frame>=first_attack
        if slot!=1 and frame==mode_end:
            continues=not restore["_restartFSM"] and not attack["_interruptAbilityOnDetach"] and last_skill_attack_end>=frame
            pending_releases = set() if continues else {f for f in shots if f>=frame}
            for release in pending_releases:
                for target,_ in shots.pop(release):
                    logs.append(dict(kind="skill_attack_interrupted_by_mode_exit",frame=frame,target=target))
            if pending_releases:
                for hit,packets in impacts.items():
                    impacts[hit] = [p for p in packets if p[1] not in pending_releases]
                for hit,packets in invalid.items():
                    invalid[hit] = [p for p in packets if p[1] not in pending_releases]
            logs.append(dict(kind="skill_mode_ended",frame=frame))
            next_default_poll=next_attack=(last_skill_attack_end if continues else frame)+1
            attack_cooldown=None
        if slot!=1 and frame==first_attack:
            # Activated RangedAttack attaches its own reset cooldown timer.
            next_default_poll=next_attack=frame
            attack_cooldown=None
        may_attack=slot==1 or frame<=manual or frame>=first_attack
        if may_attack and frame>=next_attack:
            if slot==1 and registered:
                if frame>=next_skill_poll:
                    next_skill_poll=frame+3
                    primary=select_target(frame,True)
                    skill_attack=True
            elif frame>=next_default_poll:
                next_default_poll=frame+3
                primary=select_target(frame,skill_attack)
                if slot==1 and primary is not None and gauge.state.points>=cost and frame>casting_until:
                    primary=select_target(frame,True)
                    if frame>=next_skill_poll:
                        next_skill_poll=frame+3
                    register(frame,"before_attack")
                    skill_attack=True
        if primary is not None:
            target=primary["id"]
            if slot==1 and skill_attack:
                registered=False
            ability=attack if skill_attack else normal
            mode_path=f"Modes/S{slot}" if slot!=1 and skill_attack else None
            attack_interval=interval if skill_attack else base_interval
            if world is None:
                shot=stationary_ranged_shot(tables,evidence,ability,attack_frame=frame,attack_interval=attack_interval,
                    placement=place,enemy=primary,facing=face,round_position=event["round_position"],
                    mode_path=mode_path,max_flight_ticks=180)
            else:
                shot=moving_ranged_shot(tables,evidence,ability,event,stage_routes,primary,frame=frame,
                                        interval=attack_interval,mode_path=mode_path,through=through,allow_target_exit=True)
            interrupted=(slot!=1 and not skill_attack and
                shot["release_frame"]>=manual+(event.get("press_phase","input")=="after_tick"))
            if slot==1 and skill_attack:
                anim_end=_after_spell_clock(shot["release_frame"]-frame,shot["clock"]["cached_duration_seconds"],
                                            gauge.step,same_frame_resume=True)
                casting_until=frame+anim_end["end"]
            if slot!=1 and not skill_attack:
                anim_end=_after_spell_clock(shot["release_frame"]-frame,shot["clock"]["cached_duration_seconds"],
                                            gauge.step,same_frame_resume=True)
                old_attack_end=frame+anim_end["end"]
            elif slot!=1:
                anim_end=_after_spell_clock(shot["release_frame"]-frame,shot["clock"]["cached_duration_seconds"],
                                            gauge.step,same_frame_resume=True)
                last_skill_attack_end=frame+anim_end["end"]
            if not interrupted:
                shots.setdefault(shot["release_frame"],[]).append((target,ability["_projectileKey"]))
            if not interrupted and shot["reach_frame"] is not None:
                impacts.setdefault(shot["reach_frame"],[]).append((target,shot["release_frame"],skill_attack))
            if not interrupted and "target_invalid_frame" in shot:
                invalid.setdefault(shot["target_invalid_frame"],[]).append((target,shot["release_frame"]))
            logs.append(dict(kind="attack_start",frame=frame,target=target,skill_attack=skill_attack,
                             selection_position=[primary["row"],primary["col"]]))
            next_attack=frame+(period if skill_attack else base_period)
            attack_cooldown=_AttackCooldown(frame,_raw_f32(attack_interval))
            if interrupted:
                logs.append(dict(kind="normal_attack_interrupted",frame=manual,target=target))
        apply_external_buildup(frame,"after_attack_search")
        if slot==1:
            # Entity ticks SP before Character ticks its skill. The latter recomputes
            # the modifier from the child ability's casting flag, even while queued.
            gauge.state.advance(gauge.step,stopped=sp_modifier)
            sp_modifier=frame<=casting_until
            if first_ready is None and gauge.state.points>=cost:
                first_ready=frame
                legacy_entry_at=next_default_poll
                logs.append(dict(frame=frame,kind="first_ready",sp=gauge.state.points))
            if frame>casting_until and not registered and frame>=next_skill_poll:
                next_skill_poll=frame+3
                if gauge.state.points>=cost and select_target(frame,True) is not None:
                    register(frame,"auto_tick")
                    registered=True
        else:
            gauge.state.advance(gauge.step,stopped=cast["cast_frame"]<frame<=mode_end)
            if first_ready is None and gauge.state.points>=gauge.state.cost:
                first_ready=frame
                logs.append(dict(frame=frame,kind="first_ready",sp=gauge.state.points))
        for target,projectile in shots.pop(frame,()):
            logs.append(dict(kind="projectile_release",frame=frame,projectile=projectile,target=target))
        for target,_ in invalid.pop(frame,()):
            logs.append(dict(kind="invalid_target",frame=frame,target=target,reason="left_map"))
        for target,release,skill_attack in impacts.pop(frame,()):
            state,enemy=states[target],targets[target]
            if state.hp<=HP_EPSILON:
                logs.append(dict(kind="invalid_target",frame=frame,target=target))
                continue
            repeats=repeats+1 if last==target else 0
            last=target
            ratio=ceobe_peel_ratio(peel,repeats)
            defense=effective_defense(target)
            resistance=max(0,_raw_f32(enemy["res"])+state.res_add-penetration)
            # The projectile attaches the zero-duration talent before running its main damage action.
            packet=state.receive_ordinary(frame,source="peel",damage_type="arts",
                attack_q32=mul(defense,_raw_f32(ratio)),mitigation_q32=resistance)
            physical=slot==3 and skill_attack
            # An older projectile ticks before the mode-switch coroutine on
            # its activation frame. Later impacts read the live source ATK.
            live_attack = strength if skill_active else base_strength
            main=state.receive_ordinary(frame,source="attack",damage_type="physical" if physical else "arts",
                attack_q32=mul(live_attack,skill_scale) if skill_attack else live_attack,
                mitigation_q32=_raw_f32(enemy["defense"]) if physical else resistance)
            credit=module_branch_trait(tables,card,dict(target_level=enemy["level"],normal_attack_hit=True,
                sp_blocked=(sp_modifier if slot==1 else cast["cast_frame"]<frame<=mode_end)))["uncapped_sp_credit"] if "sp" in trait else 0
            if credit:
                gauge.state.points=min(gauge.state.maximum,gauge.state.points+int(credit))
                if first_ready is None and gauge.state.points>=gauge.state.cost:
                    first_ready=frame
                    logs.append(dict(frame=frame,kind="first_ready",sp=gauge.state.points))
            seconds=board.get("duration",0) if skill_attack and slot==1 and not enemy.get("root_immune",False) else 0
            hit=dict(kind="impact",frame=frame,target=target,skill_attack=skill_attack,peel_ratio=ratio,
                effective_defense_q32=defense,peel=packet,main=main,
                silence_seconds=board.get("attack@silence",0) if skill_attack and not enemy.get("silence_immune",False) else 0,
                root_seconds=seconds,module_sp_credit=credit,hp_after_q32=state.hp)
            if world is not None:
                at=stage_routes.by_id(target,frame,projectile_born=release)
                hit.update(impact_position=[at["row"],at["col"]],release_frame=release)
            if hit["silence_seconds"] and state.hp>HP_EPSILON:
                hit["silence_expiry_receiver_tick"]=state.silence.attach(tables,frame,hit["silence_seconds"],
                              receiver_born=world.births[target]["frame"] if world else None,projectile_born=release)
            logs.append(hit)
            if state.hp<=HP_EPSILON:
                root_expires.pop(target,None)
            elif seconds:
                ticks=(_raw_f32(seconds)+gauge.step-1)//gauge.step
                anchor=frame
                if world is not None:
                    born=world.births[target]["frame"]
                    if born==release:
                        stop="root_same_birth_phase_needs_uid"
                        break
                    anchor-=int(born>release)
                if routes is not None:
                    if not routes.apply(target,anchor,ticks):
                        stop="root_at_route_checkpoint"
                        break
                root_expires[target]=max(root_expires.get(target,0),anchor+ticks)
                logs.append(dict(kind="root_applied",frame=frame,target=target,expiry_frame=root_expires[target]))
        if slot!=1 and frame==manual:
            if gauge.state.points<gauge.state.cost:
                raise ValueError(f"F{frame} 技力 {gauge.state.points}，未达到所选技能的消耗 {gauge.state.cost}")
            for e in cast["events"]:
                if e["kind"]=="enter_skill_state" and frame<=old_attack_end:
                    e.update(old_state="attack",old_attack_exit_calls_interrupt_if_not=True)
            logs.extend(e for e in cast["events"] if e["frame"]<=end)
        if slot!=1 and frame==cast["cast_frame"]:
            gauge.state.points-=gauge.state.cost
        if slot!=1 and frame in additional_presses and frame!=manual:
            logs.append(dict(kind="skill_button_rejected_while_affecting",frame=frame,sp=gauge.state.points))
        if slot!=1 and frame==mode_end:
            manual = min((f for f in additional_presses if f>frame),default=end+1)
            cast,mode,first_attack,mode_end = plan(manual)
        cleanse(frame,"after_objects")
        for probe in event.get("silence_checks_after_objects",()):
            if probe["frame"]==frame and states[probe["target"]].hp>HP_EPSILON:
                result=states[probe["target"]].silence.gates(frame,ignore_silence=probe.get("ignore_silence",False),
                                                            silenceable_buff=probe.get("silenceable_buff",True))
                logs.append(dict(kind="silence_check",frame=frame,target=probe["target"],**result))
        if stop in ("root_same_birth_phase_needs_uid","root_at_route_checkpoint"):
            break
        if world is None and targets and all(states[k].hp<=HP_EPSILON for k in targets):
            stop="no_living_target_in_range"
            break
        if world is not None and world.all_processed({k:s.hp for k,s in states.items()}):
            stop="all_enemies_processed"
            break
    status="ceobe_replacement_battle" if slot==1 else "ceobe_mode_battle" if frame>=initial_mode else "ceobe_ordinary_battle"
    return dict(status=status,
        through_frame=frame,mode_start=initial_mode if slot!=1 and frame>=initial_mode else None,period_frames=period,stop=stop,
        final_atk_q32=strength if slot!=1 and mode<=frame<mode_end else base_strength,
        lone_talent_active=lone_active,lone_talent_clock="deployment" if snapshot else "current",
        events=sorted((e for e in logs if e["frame"]<=frame),key=lambda e:e["frame"]),
        sp=gauge.state.points,remaining_hp_q32={k:s.hp for k,s in states.items()},
        conditions=["ordinary_wave_positions_in_object_order" if world is not None else "stationary_ground_targets_at_first_search",
                    "native_remaining_route_priority" if world is not None else "equal_taunt_fixed_remaining_route_distances",
                    "post_deployment_adjacency_stable_before_target_entry","ordinary_hp_no_external_modifiers","default_skin"])

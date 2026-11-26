"""Resolve a manually released GLY-Y3 storm against a native ordinary wave."""
from math import hypot
from types import SimpleNamespace

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_cast import run_committed_cast
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .chain_targets import projectile_hatred_tie
from .damage import HP_EPSILON
from .elemental_projectiles import ranged_shot_release,stable_enemy_mount,stationary_cast_mounts
from .fixed import ONE
from .marcil_deploy import _birth
from .module_effects import active_board, talent_rows
from .mostima_aura import set_period
from .mystic_timing import fixed_wait_frames
from .passenger_chain_stage import _attack_intervals,_projectiles,chain_flight_profile,resolve_chain_impact
from .passenger_storm import passenger_boards, settle_storm, storm_profile, storm_pulse
from .passenger_isolation import isolated_in,isolation_clock
from .projectile_scene import stage_tile_positions
from .ranged_stage import MovingTargets
from .skill_recovery import _divide, deploy_skill
from .sluggish_wave import SluggishWave
from .stage_channel import channel_targets, object_position_frame,prepare_channel
from .target_priority import native_box_candidates, remaining_route_priority
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _run_live_storms(tables, battle, presses, withdrawal, stream, position, snapshot, wave, rows, priority, losses,
                     normal_cells):
    card, ev, owner = battle.card, battle.evidence, battle.deploy
    end = battle.request["end_frame"]
    if battle.request["movement"]["first_born_tick_delay_frames"] != 1:
        raise ValueError("重叠雷暴需要次帧开始的原生敌人对象更新")
    grid = battle.scene.level["mapData"]["map"]
    height, width = len(grid), len(grid[0])
    direction = owner["direction"]
    face = "Back" if direction == "UP" else "Front"
    gauge, _ = deploy_skill(tables, card, owner["frame"])
    charge = gauge.state
    log, zones, launches, marks = [], [], {}, {}
    hp = {k: _raw_f32(v["hp"]) for k,v in rows.items()}
    profile = None
    walks = SluggishWave(tables, wave, continue_route=True)
    _, talents, trait = passenger_boards(tables, card)
    isolation_rate = active_board(talent_rows(card["module_phase"] or {}, 1, "10", []),
                                  card["elite"], card["level"], card["potential"]).get("sp_recovery_per_sec", 0)
    fast_period = _divide(ONE, ONE + _raw_f32(isolation_rate))
    clock = run_committed_cast(tables, ev, horizon=180, facing=face, direction=direction)
    timer, adjacency = isolation_clock(tables, ev, owner, height, width)
    normal, _ = _projectiles(ev, 3)
    ability, radius = normal
    normal_key = ability["_projectileKey"]
    speed, lifespan = chain_flight_profile(ev, normal_key)
    dt = tables.fixed_timestep()
    interval, _ = _attack_intervals(card["panel_attributes"], {}, ev)
    period = fixed_wait_frames(single(interval/dt))
    life_frames = fixed_wait_frames(single(lifespan/dt))+1
    tile = wave.tiles[owner["row"],owner["col"]]
    placement = dict(owner,tile_z=tile["position"]["z"])
    radii = {key: row["radius"] for key,row in rows.items()}
    poll = owner["frame"] + _birth(tables,card,ev,face)["born_exit_update"] + 1
    sign = 1 if direction != "LEFT" else -1
    winding, flights = None, []
    ordinary_slows, storm_slows = {}, {}
    isolated, on_field, resume = False, True, owner["frame"]+1
    through = end
    status = "horizon"
    life = battle.scene.level["options"]["maxLifePoint"]

    def wind_up(frame):
        nonlocal winding, poll, sign
        options = [r for r in wave.snapshot(frame) if hp[r["id"]] > HP_EPSILON and
                   circle_in_range(r["row"],r["col"],radii[r["id"]],normal_cells,box_query=True)]
        poll = frame + 3
        if not options:
            return
        target = options[0] if len(options) == 1 else wave.select(options, frame)
        if target["col"] != owner["col"]:
            sign = 1 if target["col"] > owner["col"] else -1
        winding = ranged_shot_release(tables,ev,ability,attack_frame=frame,attack_interval=interval,facing=face)
        winding.update(target=target["id"],sign=sign,enhanced=False,radius=radius)
        poll = frame + period
        log.append(dict(kind="ordinary_attack_start",frame=frame,target=target["id"]))

    def advance_flight(frame,shot):
        key,born=shot["target"],shot["release_frame"]
        target=wave.by_id(key,frame,projectile_born=born) if hp[key]>HP_EPSILON else None
        if target is not None:
            shot["last_mount"]=stable_enemy_mount(tables,target)
        moving=ordinary_trace_step(shot["position"],shot["last_mount"],effective_dt=dt,round_position=True,speed=speed)
        shot["position"]=moving.position
        if not moving.reached and frame-born<life_frames:
            return
        center=target if target is not None else dict(row=moving.position[1],col=moving.position[0])
        live={r["id"]:dict(r,max_hp=r["hp"],spawn=wave.births[r["id"]]["frame"])
              for r in wave.snapshot(frame,projectile_born=born) if hp[r["id"]]>HP_EPSILON}
        def tie(keys):
            visible={k:object_position_frame(wave.births[k],frame,wave.positions,wave.active,k,
                     observer_born=born,observer_phase="COROUTINES") for k in keys}
            selected=priority(keys,frame,frame_by_target=visible,nearest_tie=True)
            return selected["target"] if selected else None
        for hit in resolve_chain_impact(card,{},trait,talents,hp,marks,radii,frame,shot,live,center,
                                        isolated=isolated,step=gauge.step,nearest_tie=tie):
            log.append(dict(hit,kind="ordinary_chain_impact"))
            key=hit["target"]
            if hp[key]>HP_EPSILON:
                ticks=(_raw_f32(hit["sluggish_seconds"])+gauge.step-1)//gauge.step
                observed=object_position_frame(wave.births[key],frame,wave.positions,wave.active,key,
                           observer_born=born,observer_phase="COROUTINES")
                ordinary_slows[key]=max(ordinary_slows.get(key,-1),observed+ticks)
                walks.apply(key,frame,born,ticks,end)
        flights.remove(shot)

    for frame in range(owner["frame"]+1, end+1):
        while wave.exit_index < len(wave.exits) and wave.exits[wave.exit_index][0] < frame:
            exit_frame, target = wave.exits[wave.exit_index]
            wave.exit_index += 1
            if hp[target] > HP_EPSILON:
                wave.leaked[target] = exit_frame
                life -= losses[target]
            if life <= 0:
                break
        if life <= 0:
            status, through = "stage_defeat", exit_frame
            break
        boundary = next((k for k,f in walks.boundaries.items() if f == frame and hp[k] > HP_EPSILON), None)
        if boundary is not None:
            status, through = "requires_stage_route_after_sluggish_hit", frame-1
            break
        if on_field:
            if timer.advance(gauge.step):
                isolated = isolated_in(adjacency, snapshot(frame,hp,observer_born=owner["frame"]))
                if isolation_rate:
                    set_period(charge, fast_period if isolated else ONE, stopped=frame<resume)
            charge.advance(gauge.step, stopped=frame<resume)
            if frame >= poll and winding is None:
                wind_up(frame)
        for zone in zones:
            elapsed = frame-zone["frame"]
            if not on_field or elapsed <= 0 or elapsed % zone["interval"] or elapsed > zone["interval"]*zone["count"]:
                continue
            for flight in tuple(flights):
                if flight["release_frame"]<zone["frame"] and flight.get("advanced")!=frame:
                    flight["advanced"]=frame
                    advance_flight(frame,flight)
            board, step, _, _ = profile
            live = snapshot(frame,hp,observer_born=zone["frame"],observer_phase="COROUTINES")
            hits, reason = storm_pulse(card, board, trait, talents, hp, marks, frame, live, zone["cells"],
                born=zone["frame"], isolated=zone["isolated"], stream=stream, step=step,
                nearest_tie=lambda keys:projectile_hatred_tie(wave,keys,frame,zone["frame"]))
            if reason:
                status, through = reason, frame-1
                break
            log.append(dict(kind="storm_pulse" if hits else "empty_storm_pulse", frame=frame,
                            zone=zone["id"], pulse=elapsed//zone["interval"], hits=hits))
            for hit in hits:
                target = hit["target"]
                if hp[target] <= HP_EPSILON:
                    continue
                receiver = next(r for r in live if r["id"] == target)
                observed = receiver["observed_frame"]
                duration = _raw_f32(hit["sluggish_seconds"])
                ticks = (duration+step-1)//step
                expiry = max(walks.expiry.get(target, -1), observed+ticks)
                walks.expiry[target] = expiry
                storm_slows[zone["id"],target]=(zone["frame"],observed+ticks)
                slowed = ONE + _raw_f32(walks.modifier)
                walks.project_scale(target, observed, end, lambda t: slowed if t<=expiry else ONE)
        if status != "horizon":
            break
        for flight in tuple(flights):
            if flight.get("advanced")!=frame:
                advance_flight(frame,flight)
        if withdrawal and withdrawal["frame"] == frame and withdrawal["phase"] == "before_coroutines":
            on_field = False
        if on_field and winding is not None and winding["release_frame"]==frame:
            key=winding["target"]
            target=wave.receiver_at(key,frame) if hp[key]>HP_EPSILON else None
            if target is not None:
                winding["position"],winding["last_mount"]=stationary_cast_mounts(tables,card["operator_id"],
                    winding["clock"],placement,target,face,caster_face_sign=winding["sign"])
                flights.append(winding)
                log.append(dict(kind="ordinary_projectile_release",frame=frame,target=key,projectile=normal_key))
            else:
                poll=frame+1
            winding=None
        if on_field and frame in launches:
            shot, target, cached = launches[frame]
            pos = (position(target, frame) if hp[target] > HP_EPSILON else None) or cached
            row, col = (round(single(axis)) for axis in pos)
            if not 0<=row<height or not 0<=col<width:
                status, through = "requires_target_tile_at_exact_destination_callback", frame-1
                break
            if profile is None:
                profile = storm_profile(tables, card, ev, shot["projectile"])
            _, _, interval, count = profile
            center = dict(row=row, col=col)
            cells = skill_range_cells(tables, card, dict(placement=owner, rows=height, cols=width,
                                      area="storm", target_tile=center))["cells"]
            zone = dict(id=len(zones)+1, frame=frame, target=target, center=[row,col], interval=interval,
                        count=count, cells=cells, isolated=isolated)
            zones.append(zone)
            log.append(dict(shot, zone=zone["id"], target=target, center=[row,col]))
        if withdrawal and withdrawal["frame"] == frame:
            on_field = False
            cleared = [z["id"] for z in zones if frame < z["frame"]+z["interval"]*z["count"]]
            log.append(dict(kind="withdraw", frame=frame, phase=withdrawal["phase"], cancelled_zones=cleared))
            affected={key for (_, key), (_, expires) in storm_slows.items() if expires>frame and hp[key]>HP_EPSILON}
            for key in affected:
                sources=[born for (_, k), (born, expires) in storm_slows.items() if k==key and expires>frame]
                last_slow=max(ordinary_slows.get(key,-1),frame+int(any(wave.births[key]["frame"]<=born for born in sources)))
                walks.expiry[key]=last_slow
                slowed=ONE+_raw_f32(walks.modifier)
                walks.project_scale(key,frame,end,lambda tick: slowed if tick<=last_slow else ONE)
                log.append(dict(kind="storm_sluggish_detached",frame=frame+1,target=key,last_slow_move_tick=last_slow))
        if frame not in presses:
            continue
        live = snapshot(frame, hp) if on_field else []
        eligible = [r for r in live if circle_in_range(r["row"],r["col"],r["radius"],battle.geometry["cells"],box_query=True)]
        if not eligible or charge.points < charge.cost or not on_field or frame < resume-1:
            log.append(dict(kind="skill_button_rejected", frame=frame, sp=charge.points))
            continue
        maximum = max(hp[r["id"]] for r in eligible)
        best = {r["id"] for r in eligible if hp[r["id"]] == maximum}
        if len(best) == 1:
            target, = best
        else:
            visible = {r["id"]: r["observed_frame"] for r in eligible if r["id"] in best}
            tie = priority(best, frame, frame_by_target=visible)
            if tie is None:
                status, through = "requires_highest_hp_tiebreak", frame-1
                break
            target = tie["target"]
        cast = resolve_cast_start(tables, ev, frame=frame, phase="after_tick", sp=charge.points,
                                  direction=direction, facing=face)
        shot, = (e for e in cast["events"] if e["kind"] == "projectile_release")
        charge.points = cast["sp_after"]
        resume = cast["cast_frame"] + clock["first_unblocked_recovery_tick"]
        poll=max(poll,cast["cast_frame"]+clock["end_frame"]+1)
        if winding is not None:
            log.append(dict(kind="ordinary_attack_interrupted",frame=frame,target=winding["target"]))
            winding=None
        original=next(r for r in eligible if r["id"]==target)
        launches[shot["frame"]] = (shot,target,(original["row"],original["col"]))
        log.extend(e for e in cast["events"] if e is not shot and e["frame"]<=end)
    if on_field and through == end:
        snapshot(end, hp)
    for frame, key in wave.exits[wave.exit_index:] if life > 0 else ():
        if frame <= through and hp[key] > HP_EPSILON:
            wave.leaked[key] = frame
            life -= losses[key]
            if life <= 0:
                status, through = "stage_defeat", frame
                break
    return dict(status=status, through_frame=through, events=[e for e in log if e["frame"]<=through],
                native_draws=stream.draws, hp_q32=hp, sp=charge.points,
                storms=[{k:v for k,v in z.items() if k!="cells"} for z in zones],
                escaped=wave.leaked, remaining_life=max(0,life))


def run_passenger_stage(tables,request):
    opening={k:v for k,v in request.items() if k!="battle_random_seed"}
    actions = request["actions"]
    buttons = [a for a in actions if a.get("kind")=="skill"]
    withdrawals = [a for a in actions if a.get("kind")=="withdraw"]
    extended = len(buttons)>1 or bool(withdrawals) or request["squad"][0]["module"]=="uniequip_002_pasngr"
    if extended:
        if not buttons or len(withdrawals)>1 or len(actions)!=1+len(buttons)+len(withdrawals):
            raise ValueError("雷暴关卡需要一次部署、至少一次开技及至多一次撤退")
        for action in buttons+withdrawals:
            phase = action.get("phase")
            if (action.get("operator_id")!="char_472_pasngr" or not actions[0]["frame"]<action["frame"]<=request["end_frame"]
                    or phase not in (("after_tick",) if action["kind"]=="skill" else ("after_tick","before_coroutines"))):
                raise ValueError("雷暴的连续按键或撤退需要明确的帧和输入相位")
        if sorted({a["frame"] for a in buttons}) != [a["frame"] for a in buttons]:
            raise ValueError("雷暴的多个按键须按帧严格递增")
        opening["actions"]=[actions[0],buttons[0]]
    battle=prepare_channel(tables,opening,operator="char_472_pasngr",skill=3,
                           stage_id="main_01-09" if request.get("scene_model")!="caster_combat" else None,
                           contact_checked_during_replay=True)
    scene,owner=battle.scene,battle.deploy
    button,end=battle.button["frame"],request["end_frame"]
    extended=extended or any(e["kind"]=="attack_range_enter" for e in battle.opening["events"])
    if not extended:
        cast,_=battle.cast()
        shot,=[e for e in cast["events"] if e["kind"]=="projectile_release"]
        release=shot["frame"]
        if release+1>end:
            raise ValueError("本段需包含雷暴弹体的首次更新")
    ticks=set(range(owner["frame"]-1,end+1))
    births,radii,positions,active,escaped,history=channel_targets(tables,scene,battle.opening,frames=ticks,end=end,
        first_born_tick_delay=request["movement"]["first_born_tick_delay_frames"],enemy_key=None,with_history=True)
    grid=scene.level["mapData"]["map"]
    height,width=len(grid),len(grid[0])
    normal=range_cells(battle.card["base_range"],owner,height,width)
    timer,adjacent=isolation_clock(tables,battle.evidence,owner,height,width)
    isolated=False
    step=int(tables.fixed_timestep()*ONE)
    for frame in range(owner["frame"]+1,owner["frame"]+1 if extended else release+1):
        if timer.advance(step):
            units=[]
            for key,birth in births.items():
                tick=object_position_frame(birth,frame,positions,active,key,observer_born=owner["frame"],observer_phase="INPUT")
                if key in active[tick]:
                    row,col=positions[tick][key]
                    units.append(dict(row=row,col=col,radius=radii[key]))
            isolated=isolated_in(adjacent,units)
    wave=SimpleNamespace(births=births)
    rows={}
    attack_ranges={}
    losses={}
    spawned={}
    for key,birth in births.items():
        unit=scene.enemies[birth["enemy_key"]]
        attr=unit["attributes"]
        if (unit["skills"] or unit["talentBlackboard"] or attr["hpRecoveryPerSec"]["m_value"] or attr["tauntLevel"]["m_value"]
                or not unit["lifePointReduce"]["m_defined"]):
            raise ValueError("该雷暴窗口需要已知耐久损失、无特殊天赋或自然回复的普通敌人")
        losses[key]=unit["lifePointReduce"]["m_value"]
        spawned[key]=dict(enemy=birth["enemy_key"],route=birth["route_index"],spawn_frame=birth["frame"],spawn_order=birth["order"])
        rows[key]=dict(id=key,enemy_key=birth["enemy_key"],radius=radii[key],hp=attr["maxHp"]["m_value"],
                       max_hp=attr["maxHp"]["m_value"],res=attr["magicResistance"]["m_value"],spawn=birth["frame"],
                       prefab=unit["prefabKey"]["m_value"] or birth["enemy_key"],tile_z=0,life_cost=losses[key])
        if unit["applyWay"]["m_value"]=="RANGED":
            if not unit["rangeRadius"]["m_defined"]:
                raise ValueError("需要远程敌人的实际攻击范围")
            attack_ranges[key]=unit["rangeRadius"]["m_value"]
        elif unit["applyWay"]["m_value"]!="MELEE":
            raise ValueError("该敌人的战斗方式需要独立处理")
    priority=remaining_route_priority(scene,spawned,history,additional_base_taunt={r["enemy"] for r in spawned.values()})
    selector,=(c["fields"] for c in battle.evidence["skill"]["components"] if c["script"]=="AdvancedSelector")
    if (selector["_postFilter"],selector["_maxNum"],selector["_sortByTauntAtLast"])!=(15,1,0):
        raise ValueError("辉煌裂片的生命和仇恨排序配置发生变化")

    def run(lag):
        through=owner["frame"]-1
        live_positions = {f: dict(v) for f,v in positions.items()} if extended else positions
        live_active = {f: set(v) for f,v in active.items()} if extended else active
        def in_range(key,frame,cells):
            return circle_in_range(*live_positions[frame][key],radii[key],cells,box_query=True)
        def snapshot(frame,hp,*,observer_born=None,observer_phase="INPUT"):
            nonlocal through
            for f in range(through+1,frame+1):
                tick=f-lag
                if withdrawals and f>withdrawals[0]["frame"]:
                    break
                for key in live_active[tick]:
                    if hp[key]<=HP_EPSILON:
                        continue
                    if not extended and in_range(key,tick,normal):
                        raise ValueError(f"F{f} 有在场目标进入异客普攻范围")
                    if key in attack_ranges:
                        row,col=live_positions[tick][key]
                        if hypot(row-owner["row"],col-owner["col"])<=attack_ranges[key]+owner_radius:
                            raise ValueError(f"F{f} 远程敌人有机会攻击异客，须推进敌方普通攻击")
            through=frame
            if extended:
                receivers=[]
                for key in native_box_candidates(wave,births):
                    if hp[key]<=HP_EPSILON:
                        continue
                    tick=frame if observer_born is None else object_position_frame(births[key],frame,live_positions,
                        live_active,key,observer_born=observer_born,observer_phase=observer_phase)
                    if key in live_active[tick]:
                        row,col=live_positions[tick][key]
                        receivers.append(dict(rows[key],row=row,col=col,observed_frame=tick))
                return receivers
            tick=frame-lag
            return [dict(rows[k],row=live_positions[tick][k][0],col=live_positions[tick][k][1],observed_frame=tick)
                    for k in native_box_candidates(wave,live_active[tick]) if hp[k]>HP_EPSILON]

        if extended:
            live_history=dict(history)
            rank = remaining_route_priority(scene, spawned, live_history, additional_base_taunt={r["enemy"] for r in spawned.values()})
            motion=MovingTargets(rows,live_positions,live_active,stage_tile_positions(tables,scene),rank,births,owner["frame"],
                                 scene=scene,schedule=battle.opening["wave_schedule"],escaped=escaped,history=live_history)
            motion.leaked={}
            def position(key, frame):
                return live_positions[frame][key] if key in live_active[frame] else None
            return _run_live_storms(tables, battle, {a["frame"] for a in buttons},
                withdrawals[0] if withdrawals else None, tables.combat_random(request["battle_random_seed"]),
                position, snapshot, motion, rows, rank, losses,normal)

        full={k:r["hp"]*ONE for k,r in rows.items()}
        eligible=[r for r in snapshot(button,full) if in_range(r["id"],button-lag,battle.geometry["cells"])]
        if not eligible:
            return dict(status="no_target_at_skill_button",through_frame=button)
        best=max(e["hp"] for e in eligible)
        locks={e["id"] for e in eligible if e["hp"]==best}
        if len(locks)==1:
            target,=locks
        else:
            tie=priority(locks,button,frame_by_target=dict.fromkeys(locks,button-lag))
            if tie is None:
                return dict(status="requires_highest_hp_tiebreak",through_frame=button)
            target=tie["target"]
        release_tick=release-lag
        if target not in active[release_tick]:
            return dict(status="requires_target_tile_at_exact_destination_callback",through_frame=release)
        # RangedAttack.OnCastOnTarget resolves TARGET_ROOT_TILE on the baked release key.
        r,c=(round(single(axis)) for axis in positions[release_tick][target])
        if not (0<=r<height and 0<=c<width):
            return dict(status="requires_target_tile_at_exact_destination_callback",through_frame=release)
        stream=tables.combat_random(request["battle_random_seed"])
        start=dict(events=list(cast["events"]),sp=cast["sp_after"],
                   conditions=["普通波次确定性出生和移动、地面无阻挡、无其他干员"])
        args=dict(placement=owner,rows=height,cols=width,targets=list(rows.values()),end_frame=end)
        result=settle_storm(tables,battle.card,args,start,shot,battle.evidence,dict(row=r,col=c),stream=stream,snapshot=snapshot,
                            isolation_at_release=isolated)
        damage_frames={p["target"]:e["frame"] for e in result["events"] if e["kind"]=="storm_pulse" for p in e["hits"] if p["hp_q32"]==0}
        leaked={k:f for k,f in escaped.items() if f<=result["through_frame"] and damage_frames.get(k,end+1)>f}
        life=scene.level["options"]["maxLifePoint"]-sum(losses[k] for k in leaked)
        if life<=0:
            raise ValueError("关卡耐久已耗尽，后续帧不属于有效战斗")
        return dict(result,target=target,target_enemy=rows[target]["enemy_key"],escaped=leaked,remaining_life=life)

    # Entity-target attacks use the character collision radius for a conservative
    # range check. Only use this replay when every living attacker stays outside.
    body=[e["fields"] for e in battle.evidence["character"]["components"] if e["script"]=="CircleCollider2D" and not e["path"]]
    if len(body)!=1 or any(body[0]["m_Offset"].values()):
        raise ValueError("异客受击圆尚未绑定")
    owner_radius=body[0]["m_Radius"]
    cases=[run(lag) for lag in (0,1)]
    if not extended and any(case["status"]=="requires_stage_route_after_sluggish_hit" for case in cases):
        # A surviving enemy needs the same mutable route and receiver state as overlapping storms.
        extended=True
        cases=[run(lag) for lag in (0,1)]
    output=cases[0] if cases[0]==cases[1] else dict(status="motion_phase_changes_outcome",motion_cases=cases)
    return dict(stage=battle.opening["code"],squad=battle.config,deployment=battle.placement_event,
                agrees_for_motion_lags=[0,1] if cases[0]==cases[1] else [],
                **output,source=battle.opening["source"])

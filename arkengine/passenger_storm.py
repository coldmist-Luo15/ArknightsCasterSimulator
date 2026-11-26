"""A single storm after a manual Passenger S3, with HP and acquisition updated at every pulse."""
from .attack_range import circle_in_range, range_cells
from .battle_random import BattleRandom
from .branch_skill_window import first_branch_skill
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .damage import HP_EPSILON
from .five_star_skills import _number
from .fixed import ONE
from .module_effects import active_board, talent_rows, trait_board
from .passenger_isolation import isolated_in,isolation_clock
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


def passenger_boards(tables,card):
    unit=tables.read("excel/character_table.json")[card["operator_id"]]
    phase=card["module_phase"] or {}
    talents=[active_board(talent_rows(phase,i,str(i+1),t["candidates"]),card["elite"],card["level"],card["potential"])
             for i,t in enumerate(unit["talents"][:2])]
    return unit,talents,trait_board(unit,phase,card)


def passenger_chain_hit(card,board,trait,talents,hp,marks,frame,row,*,bounce,isolated,step,projectile_born):
    from .caster_drone_chain_mystic import _passenger
    key=row["id"]
    mark=talents[0]
    earliest,latest=marks.get(key,(-1,-1))
    # Receiver and projectile are updated in creation order. Same-frame
    # allocations require UIDs only if the uncertainty straddles expiration.
    observed_first=frame-int(row["spawn"]>=projectile_born)
    observed_last=frame-int(row["spawn"]>projectile_born)
    qualifies=hp[key]*ONE>=_raw_f32(row["max_hp"])*_raw_f32(mark["hp_ratio"])
    marked=observed_last<earliest
    if qualifies:
        # The ability attachment precedes execution of the damage action on this receiver.
        lifetime=_raw_f32(mark["pasngr_t_1[enhance].duration"])
        ticks=(lifetime+step-1)//step
        # One equal-priority source modifier contributes while any of its
        # same-duration instances is still alive.
        marks[key]=(observed_first+ticks,observed_last+ticks)
        marked=True
    elif observed_first<latest and not marked:
        return None
    res=_passenger(card["skill"],"chain_hit",dict(target=dict(row,hp=hp[key]/ONE,marked=marked),
                   isolated=isolated,bounce_index=bounce),board,card["panel_attributes"],trait,talents)
    amount=res["hit"]["damage_raw"]
    hp[key]=max(0,hp[key]-amount)
    if hp[key]<HP_EPSILON:
        hp[key]=0
    return dict(target=key,damage_q32=amount,hp_q32=hp[key],marked=marked,sluggish_seconds=res["sluggish_seconds"])


def run_storm(tables,card,scene,*,stream=None):
    from .caster_drone_chain_mystic import PASSENGER
    if (card["operator_id"],card["skill"])!=(PASSENGER,3):
        raise ValueError("雷暴靶场需要异客三技能")
    if stream is None:
        stream=BattleRandom(scene["seed"])
    start = first_branch_skill(tables,card,scene)
    shots = [e for e in start["events"] if e["kind"]=="projectile_release"]
    if not shots:
        return start
    shot, = shots
    launch_input, = [e["frame"] for e in start["events"] if e["kind"]=="skill_delivery"]
    if any(f>launch_input for f in scene.get("press_frames",[])):
        raise ValueError("单雷暴场景只接受一次成功释放，后续按键需要多区域战场")
    ev = caster_prefab_evidence(tables,PASSENGER,3,module_id=card["module"],module_level=card["module_level"],skill_level=card["skill_level"])
    center=next(r for r in scene["targets"] if r["id"]==start["target"])
    return settle_storm(tables,card,scene,start,shot,ev,center,stream=stream)


def storm_profile(tables, card, ev, key):
    variants = ev["projectiles"][key]
    board = {x["key"]:x["value"] for x in card["skill_row"]["blackboard"]}
    for prefab in variants.values():
        movement, = [r["fields"] for r in prefab["components"] if r["script"]=="AttachToTarget"]
        timer, = [r["fields"] for r in prefab["components"] if r["script"]=="ChainLightningHitBehaviour"]
        life, = [r["fields"] for r in prefab["components"] if r["script"] == "SimpleProjectile"]
        if (not movement["_immediatelyReach"] or movement["_delayToStart"] or movement["_delayTime"]!=board["duration"]
                or not timer["_waitFirstPeriod"] or timer["_firstPeriod"]>0 or not timer["_ignoreSmallEps"]
                or timer["_onlyCheckHitWhenReachTarget"] or timer["_selectTargetOnce"] or timer["_intervalBbKey"]!="interval"
                or not life["_stopWhenSourceInvalid"]):
            raise ValueError("雷暴弹体的挂载或周期配置已变化")
    source, = (c["fields"] for c in ev["skill"]["components"] if c["script"] == "RangedAttack")
    if not source["_useCachedAtkOnly"] or source["_projectileKey"] != key:
        raise ValueError("雷暴伤害的攻击力来源已变化")
    step = int(tables.fixed_timestep()*ONE)
    # SelectorHitBehaviour starts with one full period, calls PeriodicTimer.Update
    # with ignoreSmallEps, and calls Next after every pulse. The projectile's
    # ordered component list ticks movement before its selector.
    period_raw = _raw_f32(board["interval"])
    interval = (period_raw-READY_EPSILON)//step+1
    pulse_count = int(board["duration"]/board["interval"])
    if pulse_count*board["interval"]!=board["duration"]:
        raise ValueError("雷暴结束时间无法对齐已核实的周期")
    return board, step, interval, pulse_count


def storm_pulse(card, board, trait, talents, hp, marks, frame, live, cells, *, born, isolated, stream, step,
                nearest_tie=None):
    candidates = [r for r in live if circle_in_range(r["row"],r["col"],r["radius"],cells,box_query=True)]
    if not candidates:
        return [], None
    chain = stream.select_targets(candidates, 1)
    for _ in range(int(board["chain.max_target"])-1):
        last = chain[-1]
        near = [((r["row"]-last["row"])**2+(r["col"]-last["col"])**2,r) for r in live if r not in chain]
        if not near:
            break
        d = min(pair[0] for pair in near)
        if d > 1.7**2:
            if any((dist**.5-1.7)<=r["radius"]+.01 for dist,r in near):
                return [], "unresolved_chain_radius_contact"
            break
        tied = [r for dist,r in near if dist==d]
        if len(tied) > 1:
            chosen = nearest_tie([r["id"] for r in tied]) if nearest_tie else None
            if chosen is None:
                return [], "unresolved_chain_distance_tie"
            tied = [r for r in tied if r["id"] == chosen]
        chain.append(tied[0])
    packets = []
    for bounce, row in enumerate(chain):
        packet = passenger_chain_hit(card, board, trait, talents, hp, marks, frame, row,
            bounce=bounce, isolated=isolated, step=step, projectile_born=born)
        if packet is None:
            return packets, "unresolved_repeated_mark_refresh_at_expiry"
        packets.append(packet)
    return packets, None


def settle_storm(tables,card,scene,start,shot,ev,center,*,stream,snapshot=None,isolation_at_release=None):
    """Use the same released projectile, pulse clock, chains and packets with either target provider."""
    t0 = shot["frame"]
    board, step, interval, pulse_count = storm_profile(tables, card, ev, shot["projectile"])
    rows={r["id"]:r for r in scene["targets"]}
    geometry = skill_range_cells(tables,card,dict(scene,area="storm",target_tile=center))
    ordinary = range_cells(card["base_range"],scene["placement"],scene["rows"],scene["cols"])
    hp = {}
    for k,row in rows.items():
        if not 0<_number(row,"hp")<=_number(row,"max_hp"):
            raise ValueError("靶标初始生命值应在最大生命范围内")
        _number(row,"res",minimum=-1000)
        hp[k]=_raw_f32(row["hp"])
    _,talents,trait=passenger_boards(tables,card)
    place=scene["placement"]
    log=start["events"]
    if isolation_at_release is None:
        timer,cells=isolation_clock(tables,ev,place,scene["rows"],scene["cols"])
        isolation_at_release=False
        for f in range(scene["frame"]+1,t0+1):
            if timer.advance(step):
                isolation_at_release=isolated_in(cells,(r for r in rows.values() if r["spawn"]<=f))
    marks={}
    stop="storm_pulses_finished"
    through=t0
    for i in range(1,pulse_count+1):
        frame=t0+i*interval
        if frame>scene["end_frame"]:
            stop="horizon"
            through=scene["end_frame"]
            break
        through=frame
        live=(snapshot(frame,hp) if snapshot else
              [r for k,r in rows.items() if hp[k]>HP_EPSILON and r["spawn"]<=frame])
        if not snapshot and any(circle_in_range(r["row"],r["col"],r["radius"],ordinary,box_query=True) for r in live):
            stop="requires_ordinary_attack_continuation"
            break
        # Wave snapshots already use native IDs. For the static target list,
        # equal-frame entries are supplied in their original creation order.
        if not snapshot:
            live.sort(key=lambda r:r["spawn"])
        packets, unresolved = storm_pulse(card, board, trait, talents, hp, marks, frame, live, geometry["cells"],
                                          born=t0, isolated=isolation_at_release, stream=stream, step=step)
        if unresolved and not packets:
            stop = unresolved
            break
        if not packets:
            log.append(dict(frame=frame,kind="empty_storm_pulse"))
            continue
        log.append(dict(frame=frame,kind="storm_pulse",pulse=i,hits=packets))
        if unresolved:
            stop = unresolved
            break
        if snapshot and any(hp[p["target"]]>HP_EPSILON for p in packets):
            stop="requires_stage_route_after_sluggish_hit"
            break
        if not snapshot and all(v==0 for v in hp.values()):
            stop="all_targets_defeated"
            break
    return dict(status=stop,through_frame=through,events=log,
                **({"first_ready_frame":start["first_ready_frame"]} if "first_ready_frame" in start else {}),
                native_draws=stream.draws,
                sp_after_cast=start["sp"],hp_q32=hp,storm_center=[center["row"],center["col"]],isolated_at_release=isolation_at_release,
                conditions=start["conditions"]+["等嘲讽、无嘲讽掩护，出生时无既存机理分析标记",
                    "同帧出生的静止靶标按原生创建顺序录入，每次非空脉冲用战斗随机流选首靶"])

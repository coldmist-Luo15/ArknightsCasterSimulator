"""Conditional unblocked cardinal ground and aerial movement between checkpoints."""
from math import ceil, hypot, isfinite, sqrt

from .baked_mount import single
from .enemy_speed import route_movement_cap
from .frames import FPS
from .route_timing import TIMED_WAITS,checkpoint_sequence

# V079 ROUTE_REACH_DISTANCE, the exact value of the binary32 literal.
ROUTE_REACH_DISTANCE = 0.05000000074505806
_WALK_TILES = {"tile_start", "tile_road", "tile_floor", "tile_end", "tile_telin", "tile_telout"}
_FLY_TILES = _WALK_TILES | {"tile_flystart", "tile_wall", "tile_fence_bound"}


def apply_walking_stun(history, instance, frame, duration_ticks, horizon):
    """A walking unit retains velocity on entering STUN and resumes on the tick after returning to DEFAULT."""
    if duration_ticks <= 0:
        return False
    motion_kinds = {"free_motion_sample", "exit_motion_sample", "post_wait_motion_sample"}
    prior = []
    for tick in (frame-1, frame):
        rows = [(kind,row) for kind,row in history.get(tick, ()) if row["instance_id"] == instance]
        if len(rows) != 1 or rows[0][0] not in motion_kinds:
            return False
        _, row = rows[0]
        if not row.get("active_ticks") or row.get("leg_end"):
            return False
        prior.append(row)
    if prior[1]["active_ticks"] != prior[0]["active_ticks"]+1:
        return False
    for tick in sorted((t for t in history if t >= frame), reverse=True):
        source = history[tick]
        moved = [(kind,row) for kind,row in source if row["instance_id"] == instance]
        if moved:
            source[:] = [(kind,row) for kind,row in source if row["instance_id"] != instance]
            if tick+duration_ticks <= horizon:
                history.setdefault(tick+duration_ticks, []).extend(moved)
    history[frame].append(("enemy_stun_hold", dict(instance_id=instance, row=prior[0]["row"], col=prior[0]["col"],
        resume_move_frame=frame+duration_ticks)))
    return True


def _limits(scene, route, steering, command, from_spawn):
    if (route["motion_mode"] not in ("WALK", "FLY") or route["motion_mode"] != route["enemy_motion"]
            or scene.level["options"]["steeringEnabled"] is not True):
        return "unsupported_motion_mode"
    if from_spawn and (any(route["spawn_offset"].values()) or any(route["spawn_random_range"].values())):
        return "noncentral_or_random_spawn"
    if route["flags"]["visitEveryTileCenter"] or route["flags"]["visitEveryNodeCenter"]:
        return "intermediate_center_targeting"
    if command["kind"] not in {"MOVE", "END"}:
        return "first_command_is_not_move"
    if command["kind"] == "MOVE" and (command["randomize_reach_offset"]
            or any(command["reach_offset"].values()) or command["reach_distance"] != 0):
        return "custom_reach_condition"
    if not isinstance(steering, dict):
        return "missing_motion_parameters"
    stats = [route["base_move_speed"], route["level_move_multiplier"],
             steering.get("_steeringFactor"), steering.get("_maxSteeringForce")]
    if any(type(v) not in (int, float) or not isfinite(v) for v in stats):
        return "missing_motion_parameters"
    if stats[0] < 0 or min(stats[1:]) <= 0:
        return "unsupported_motion_parameters"
    return None


def _straight_walk(scene, route, start, command, *, allow_same_cell=False):
    target = command["target"]
    dr, dc = target["row"] - start["row"], target["col"] - start["col"]
    if (dr == dc == 0 and not allow_same_cell) or (dr and dc):
        return "requires_other_path_geometry"
    data = scene.level["mapData"]
    if data["blockEdges"] or data["effects"] or data["layerRects"]:
        return "unresolved_map_effects_or_edges"
    step_row, step_col = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
    occupied = {tuple(unit["position"][k] for k in ("row", "col")) for unit in scene.predefines}
    flying = route["motion_mode"] == "FLY"
    for i in range(abs(dr) + abs(dc) + 1):
        row, col = start["row"] + step_row * i, start["col"] + step_col * i
        tile = scene.tile(row, col)
        if (tile["passableMask"] not in (("ALL", "FLY_ONLY") if flying else ("ALL",))
                or tile["tileKey"] not in (_FLY_TILES if flying else _WALK_TILES)
                or tile["effects"] or tile["blackboard"]):
            return "obstacle_or_unmodeled_tile"
        if (row, col) in occupied:
            return "predefined_unit_on_leg"
        if (command["kind"] != "END" and not route["flags"]["visitEveryCheckPoint"]
                and route["commands"][-1]["target"] == {"row": row, "col": col}):
            return "goal_overlaps_first_leg"
    return None


def project_first_leg(scene, route, steering, *, sample_period=FPS, timestep=None, round_position=False):
    commands = route["commands"]
    if timestep is not None:
        return _vector_leg(scene, route, steering, route["start"], 0, timestep, sample_period,
                                 round_position, from_spawn=True)
    return _project_leg(scene, route, steering, route["start"], commands[0],
                        commands[1] if len(commands) > 1 else None, from_spawn=True, sample_period=sample_period)


def project_exit_leg(scene, route, steering, *, sample_period=FPS, incoming=None, sequence=None,
                     timestep=None, round_position=False):
    """Continue from a centered portal exit, optionally with an observed incoming MOVE."""
    commands = route["commands"]
    if len(commands) < 2 or commands[-1]["kind"] != "END":
        raise ValueError("传送出口与路线终点必须是连续指令")
    appear = commands[-2]
    if (appear["kind"] != "APPEAR_AT_POS" or appear["randomize_reach_offset"]
            or any(appear["reach_offset"].values())):
        return dict(status="unresolved", reason="noncentral_or_unknown_exit_position")
    if incoming is not None:
        if not _hidden_sequence(sequence) or timestep is None:
            return dict(status="unresolved", reason="unverified_velocity_at_portal")
        velocity = _last_velocity(incoming)
        leg = _continued_move(scene, route, steering, commands[-1], appear["target"], velocity,
                              timestep, sample_period, round_position=round_position)
        if leg["status"] != "unresolved":
            leg["samples"].insert(0, dict(active_ticks=0, **appear["target"], speed=hypot(*velocity)))
            leg.update(next_command=None, initial_velocity=list(velocity), reach_distance=ROUTE_REACH_DISTANCE,
                       round_position=round_position, conditions=["hidden_sequence_preserves_move_velocity",
                           "no_speed_modifiers_or_blocking", "no_avoidance_or_external_displacement"])
        return leg
    return _project_leg(scene, route, steering, appear["target"], commands[-1],
                        None, from_spawn=False, sample_period=sample_period)


def _project_leg(scene, route, steering, start, cp, next_command, *, from_spawn, sample_period):
    """Project motion from first active tick, conditional on continuous normal MOVE.

    The recurrence follows the path/movement system analysis at
    https://www.bilibili.com/read/cv31743470 . Local raw parameters determine
    the bounds; the arithmetic here uses Python binary64, not native rounding.
    """
    cell = {axis: round(start[axis]) for axis in ("row", "col")}
    reason = _limits(scene, route, steering, cp, from_spawn) or _straight_walk(scene, route, cell, cp)
    if reason:
        return dict(status="unresolved", reason=reason)
    dr, dc = cp["target"]["row"] - start["row"], cp["target"]["col"] - start["col"]
    if dr and dc:
        return dict(status="unresolved", reason="off_axis_leg_start")
    distance = abs(dr) + abs(dc)
    cap = route_movement_cap(route)
    if not isfinite(cap) or cap <= 0:
        return dict(status="unresolved", reason="unsupported_speed_scale")
    lower = (distance - ROUTE_REACH_DISTANCE) / cap * FPS
    # Limit the CPU and trace size even for modified inputs with almost zero speed.
    tick_limit = 180_000
    if not isfinite(lower) or lower > tick_limit:
        return dict(status="unresolved", reason="motion_tick_budget", tick_limit=tick_limit)
    min_ticks = ceil(lower)
    factor, force = steering["_steeringFactor"], steering["_maxSteeringForce"]
    speed = progress = 0.0
    samples = [dict(active_ticks=0, **start, speed=0.0)]
    for tick in range(1, tick_limit + 1):
        remaining = distance - progress
        snap = remaining <= cap / FPS
        if snap:
            step_speed = remaining * FPS
            progress = distance
        else:
            acceleration = min(force, factor * (cap - speed))
            speed = min(cap, speed + acceleration / FPS)
            step_speed = speed
            progress += speed / FPS
        reached = distance - progress <= ROUTE_REACH_DISTANCE
        if tick == 1 or tick % sample_period == 0 or reached:
            samples.append(dict(active_ticks=tick, row=start["row"] + dr * progress / distance,
                                col=start["col"] + dc * progress / distance, speed=step_speed))
        if reached:
            return dict(status="conditional_projection", checkpoint=cp["source"],
                        conditions=["continuous_MOVE_from_rest", "no_speed_modifiers_or_blocking",
                                    "no_avoidance_or_external_displacement", "axis_aligned_clear_path"],
                        speed_cap=cap, steering_factor=factor, max_steering_force=force,
                        reach_distance=ROUTE_REACH_DISTANCE, speed_bound_min_ticks=min_ticks,
                        arrival_ticks=tick, arrival_rule="snap" if snap else "reach_radius",
                        samples=samples, next_command=next_command)
    return dict(status="unresolved", reason="motion_tick_budget", tick_limit=tick_limit)


def project_after_wait(scene, route, steering, sequence, *, sample_period=FPS, timestep=None, round_position=False):
    """Resume a straight leg from the actual wait position, conditional on rest."""
    command = sequence["next_command"]
    index = route["commands"].index(command)
    position = sequence["events"][-1]["position"]
    turns = all(position[k] != command["target"][k] for k in ("row","col"))
    if timestep is not None and (sequence.get("wait_coast") or route["motion_mode"] == "FLY" or turns):
        velocity = sequence.get("wait_coast", {}).get("velocity", (0, 0))
        return _vector_leg(scene, route, steering, position, index, timestep, sample_period,
                           round_position, from_spawn=False, velocity=velocity)
    return _project_leg(scene, route, steering, sequence["events"][-1]["position"], command,
                        route["commands"][index + 1] if command["kind"] != "END" else None,
                        from_spawn=False, sample_period=sample_period)


def _vector_leg(scene, route, steering, start, index, dt, sample_period, rounding, *, from_spawn, velocity=(0,0)):
    commands = route["commands"]
    reason = _limits(scene, route, steering, commands[index], from_spawn)
    if reason:
        return dict(status="unresolved", reason=reason)
    leg = _continued_move(scene, route, steering, commands[index], start, velocity, dt,
                          sample_period, round_position=rounding)
    if leg["status"] == "conditional_projection":
        leg["samples"].insert(0, dict(active_ticks=0, **start, speed=hypot(*velocity), velocity=list(velocity)))
        leg.update(next_command=commands[index+1] if index+1 < len(commands) else None,
                   round_position=rounding, reach_distance=ROUTE_REACH_DISTANCE,
                   conditions=["unblocked_cardinal_corridor_no_external_speed_or_displacement"])
    return leg


def _clamp_vector(r, c, limit):
    size2 = single(single(r*r) + single(c*c))
    if size2 <= single(limit*limit):
        return r, c
    size = single(sqrt(size2))
    return single(single(r/size)*limit), single(single(c/size)*limit)


def advance_direct_move(position,velocity,target,cap,steering,dt,round_position):
    """One unobstructed CalculateMoveDelta tick after the cursor returns a nonzero direction."""
    r,c=position
    vr,vc=velocity
    dr,dc=single(target[0]-r),single(target[1]-c)
    dist=single(sqrt(single(single(dr*dr)+single(dc*dc))))
    if not dist:
        return None
    factor,force=steering["_steeringFactor"],steering["_maxSteeringForce"]
    ar=single(single(single(single(dr/dist)*cap)-vr)*factor)
    ac=single(single(single(single(dc/dist)*cap)-vc)*factor)
    ar,ac=_clamp_vector(ar,ac,force)
    vr,vc=_clamp_vector(single(vr+single(ar*dt)),single(vc+single(ac*dt)),cap)
    r,c=single(r+single(vr*dt)),single(c+single(vc*dt))
    if round_position:
        r,c=(single(round(v*10000)/10000) for v in (r,c))
    return (r,c),(vr,vc)


def coast_during_wait(position, velocity, ticks, steering, timestep, *, round_position=False, sample_period=1):
    """Integrate ordinary MOVE during a WAIT with zero desired direction and no other force.

    The caller establishes that the cursor does not snap to the following target,
    no obstacle pushes the unit and MOVE remains active on every integrated tick.
    """
    dt = single(timestep)
    factor, force = steering["_steeringFactor"], steering["_maxSteeringForce"]
    if (type(ticks) is not int or not 0 <= ticks <= 180000 or type(sample_period) is not int or sample_period < 1
            or not 0 < dt or not 0 < factor * dt <= 1 or not 0 < force):
        raise ValueError("连续等待的步数、采样间隔或转向参数超出已核范围")
    r, c = (single(position[axis]) for axis in ("row", "col"))
    vr, vc = map(single, velocity)
    samples = [dict(active_ticks=0, row=r, col=c, speed=hypot(vr, vc), velocity=[vr, vc])]
    for tick in range(1, ticks + 1):
        ar, ac = _clamp_vector(single(-vr * factor), single(-vc * factor), force)
        vr, vc = single(vr + single(ar * dt)), single(vc + single(ac * dt))
        r, c = single(r + single(vr * dt)), single(c + single(vc * dt))
        if round_position:
            r, c = (single(round(value * 10000) / 10000) for value in (r, c))
        if tick == 1 or tick % sample_period == 0 or tick == ticks:
            samples.append(dict(active_ticks=tick, row=r, col=c, speed=hypot(vr, vc), velocity=[vr, vc]))
    return dict(position=dict(row=r, col=c), velocity=[vr, vc], samples=samples)


def advance_wait_coast(position, velocity, cap, steering, dt):
    """One visible wait update with the live move-speed cap and zero wanted velocity."""
    r, c = position
    vr, vc = velocity
    factor = steering["_steeringFactor"]
    ar, ac = _clamp_vector(single(-vr*factor),single(-vc*factor),steering["_maxSteeringForce"])
    vr, vc = _clamp_vector(single(vr+single(ar*dt)),single(vc+single(ac*dt)),cap)
    r,c = (single(round(single(p+single(v*dt))*10000)/10000) for p,v in ((r,vr),(c,vc)))
    return (r,c),(vr,vc)


def _hidden_sequence(sequence):
    events = [e["command"] for e in (sequence or {}).get("events", ()) if e["event"] == "checkpoint_begin"]
    return (len(events) >= 2 and events[0] == "DISAPPEAR" and events[-1] == "APPEAR_AT_POS"
            and all(k in TIMED_WAITS for k in events[1:-1]))


def _last_velocity(leg):
    if "final_velocity" in leg:
        return tuple(leg["final_velocity"])
    first, last = leg["samples"][0], leg["samples"][-1]
    return tuple(((last[k] > first[k]) - (last[k] < first[k])) * last["speed"] for k in ("row", "col"))


def apply_wait_coast(route, sequence, velocity, steering, dt, period, rounding, *, consecutive=False):
    """During an unobstructed visible ground or aerial WAIT, MOVE steers toward zero velocity."""
    events = sequence["events"]
    if (route["motion_mode"] not in ("WALK", "FLY") or not events or (not consecutive and len(events) != 2)
            or any(e["command"] not in TIMED_WAITS or e["disappeared"] for e in events)
            or sequence["next_command"]["kind"] not in ("MOVE","END")):
        return
    start = events[0]["position"]
    coast = coast_during_wait(start, velocity, sequence["next_offset"], steering, dt,
                              round_position=rounding, sample_period=period)
    # A nearby next target can engage the snap path before steering to zero.
    next_target = sequence["next_command"]["target"]
    if any(round(start[k]) != round(coast["position"][k]) for k in ("row","col")) or any(
            hypot(s["row"]-next_target["row"], s["col"]-next_target["col"]) <= ROUTE_REACH_DISTANCE
            for s in coast["samples"]):
        return
    coast.update(arrival_ticks=sequence["next_offset"], checkpoint=events[0]["source"])
    events[-1]["position"] = dict(coast["position"])
    sequence["wait_coast"] = coast


def _continued_move(scene, route, steering, command, position, velocity, dt, period, *, round_position=False,
                    horizon=None):
    """Normal MOVE targets the unobstructed checkpoint with no obstacle steering force."""
    origin = {axis: round(position[axis]) for axis in ("row", "col")}
    reason = _limits(scene, route, steering, command, False) or _straight_walk(scene, route, origin, command,
                                                                               allow_same_cell=True)
    if reason:
        return dict(status="unresolved", reason=reason)
    cap = route_movement_cap(route)
    if cap*dt > ROUTE_REACH_DISTANCE:
        return dict(status="unresolved", reason="fast_continuation_needs_snap_rule")
    r, c = single(position["row"]), single(position["col"])
    vr, vc = map(single, velocity)
    target = command["target"]
    samples = []
    budget=180000 if horizon is None else min(180000,horizon)
    for tick in range(1,budget+1):
        moved = advance_direct_move((r,c),(vr,vc),(target["row"],target["col"]),cap,steering,dt,round_position)
        if moved is None:
            return dict(status="unresolved", reason="coincident_continuation_checkpoint")
        (r,c),(vr,vc) = moved
        # Only the direct cardinal corridor has been checked for terrain and devices.
        if not (min(origin["row"], target["row"]) - .5 < r < max(origin["row"], target["row"]) + .5
                and min(origin["col"], target["col"]) - .5 < c < max(origin["col"], target["col"]) + .5):
            return dict(status="unresolved", reason="continued_move_left_checked_corridor")
        reached = hypot(target["row"] - r, target["col"] - c) <= ROUTE_REACH_DISTANCE
        if tick == 1 or tick % period == 0 or reached or tick==horizon:
            samples.append(dict(active_ticks=tick, row=r, col=c, speed=hypot(vr, vc), velocity=[vr, vc]))
        if reached:
            return dict(status="conditional_projection", checkpoint=command["source"], samples=samples,
                        arrival_ticks=tick, final_velocity=[vr, vc], target=target)
    if horizon==budget:
        return dict(status="conditional_projection",checkpoint=command["source"],samples=samples,
                    arrival_ticks=None,final_velocity=[vr,vc],target=target)
    return dict(status="unresolved", reason="motion_tick_budget")


def project_route_tail(scene, route, steering, previous_leg, sequence, dt, sample_period, *, native_portals=False,
                       round_position=False, clock_frame=None, clocks=None):
    """Start at the last projected command boundary and preserve each later cursor event."""
    commands = route["commands"]
    last = previous_leg["samples"][-1]
    position = {axis: last[axis] for axis in ("row", "col")}
    velocity = _last_velocity(previous_leg)
    next_command = previous_leg["next_command"]
    hidden = False
    if sequence and sequence["events"]:
        position = sequence["events"][-1]["position"]
        hidden = sequence["events"][-1]["disappeared"]
        if not (native_portals and _hidden_sequence(sequence)):
            velocity = (0, 0)
        next_command = sequence["next_command"]
    offset = 0
    parts = []
    index = commands.index(next_command)
    while index < len(commands):
        command = commands[index]
        if command["kind"] in ("MOVE", "END"):
            if hidden:
                return dict(parts=parts, next_offset=offset, pending=dict(command=command["kind"],
                            source=command["source"], reason="move_while_disappeared"))
            leg = _continued_move(scene, route, steering, command, position, velocity, dt,
                                  sample_period, round_position=round_position)
            if leg["status"] == "unresolved":
                return dict(parts=parts, next_offset=offset, pending=dict(command=command["kind"],
                            source=command["source"], reason=leg["reason"]))
            parts.append(dict(offset=offset, leg=leg, command=command["kind"]))
            velocity = leg["final_velocity"]
            position = {k: leg["samples"][-1][k] for k in ("row", "col")}
            offset += leg["arrival_ticks"]
            index += 1
        else:
            following = checkpoint_sequence(commands,index,position,timestep=dt if native_portals else None,
                clock_frame=None if clock_frame is None else clock_frame+offset,clocks=clocks)
            if not following["events"]:
                return dict(parts=parts, next_offset=offset, pending=dict(command=command["kind"],
                            source=command["source"], reason="unsupported_following_checkpoint"))
            if native_portals:
                apply_wait_coast(route,following,velocity,steering,dt,sample_period,round_position,consecutive=bool(clocks))
            part = dict(offset=offset, events=following["events"])
            if "wait_coast" in following:
                part["wait_coast"] = following["wait_coast"]
            parts.append(part)
            position = following["events"][-1]["position"]
            hidden = following["events"][-1]["disappeared"]
            if "wait_coast" in following:
                velocity = following["wait_coast"]["velocity"]
            elif not (native_portals and _hidden_sequence(following)):
                velocity = (0, 0)
            offset += following["next_offset"]
            index = commands.index(following["next_command"])
    return dict(parts=parts, pending=None, next_offset=offset)

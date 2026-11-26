"""Continue an enemy walking cursor across heat-vent speed changes and immobilization."""
from .enemy_motion import _continued_move
from .enemy_speed import move_attribute, movement_cap
from .fixed import ONE
from .route_timing import checkpoint_sequence


def _slowed_leg(tables, scene, motion, history, birth, board, frame, hold=None):
    key = birth["instance_id"]
    sample_frame = hold[0]-1 if hold else frame
    matches = [(kind, row) for kind, row in history.get(sample_frame, ()) if row["instance_id"] == key and "velocity" in row]
    if len(matches) != 1 or not motion.get("continued_move_position_rounding"):
        return None
    return slowed_walk_from_sample(tables, scene, motion, birth, board, frame, matches[0], hold[1] if hold else None)


def slowed_walk_from_sample(tables, scene, motion, birth, board, frame, sample, resume_frame=None):
    route_profile = motion["profiles"][motion["instance_profiles"][birth["instance_id"]]]
    route = route_profile["route"]
    kind, row = sample
    source = row.get("leg_source") or route_profile.get("exit_leg", {}).get("checkpoint")
    command = next((c for c in route["commands"] if c["source"] == source), None)
    if command is None or row.get("leg_end") or "velocity" not in row or not -1 <= board["move_speed"] < 0:
        return None
    slowed = move_attribute(route["base_move_speed"],ratio_delta=board["move_speed"])
    dt = tables.fixed_timestep()
    travel = _continued_move(scene, dict(route, move_attribute_q32=slowed), tables.enemy_steering(birth["enemy_key"]),
                             command, row, row["velocity"], dt, 1, round_position=True)
    if travel["status"] != "conditional_projection":
        return None
    return dict(frame=frame, kind=kind, start=row, travel=travel, command=command, route=route, held_until=resume_frame,
                steering=tables.enemy_steering(birth["enemy_key"]), state="slowed", last=row,
                slowed_attribute=slowed/ONE, slowed_attribute_q32=slowed,
                level_move_multiplier=route["level_move_multiplier"],
                move_speed=movement_cap(slowed,route["level_move_multiplier"]))


def _walk_tick(scene, leg, time, dt, *, hold_until=None):
    """Continue the current command or start the next direct MOVE from the actual exit position and velocity."""
    route = leg["route"]
    source = leg["command"]
    previous = leg["last"]
    wait = leg.get("wait")
    if wait and time <= wait["until"]:
        rows = list(wait["events"].get(time, ()))
        if hold_until is not None:
            if hold_until > wait["until"]:
                return [], "stun_extends_from_wait_into_next_move"
            # Enemy.OnTick advances the time checkpoint even while StunState prevents walking.
            rows.append(("enemy_stun_hold", dict(instance_id=previous["instance_id"], row=previous["row"],
                        col=previous["col"], resume_move_frame=hold_until, during_checkpoint_wait=True)))
        return rows, None
    if hold_until is not None:
        if previous.get("leg_end") or not previous.get("active_ticks"):
            return [], "stun_at_unreviewed_checkpoint_state"
        leg["held_until"] = max(leg.get("held_until") or time,hold_until)
        return [("enemy_stun_hold",dict(instance_id=previous["instance_id"], row=previous["row"], col=previous["col"],
                                        resume_move_frame=leg["held_until"]))], None
    held_until = leg.get("held_until")
    if held_until is not None and time < held_until:
        return [], None
    resumed_stun = held_until == time
    if resumed_stun:
        leg["held_until"] = None
    start_next = not resumed_stun and time > leg["frame"] + leg["travel"]["arrival_ticks"]
    resume = leg["state"] == "slowed" and time > leg["slow_through"]
    if start_next or resume or resumed_stun:
        if start_next:
            source = wait["next"] if wait else route["commands"][route["commands"].index(source)+1]
            if wait:
                previous = dict(previous, velocity=[0.,0.])
                del leg["wait"]
            if source["kind"] not in ("MOVE", "END"):
                return [], "unreviewed_checkpoint_after_vent_motion"
        if resume:
            leg["state"] = "ordinary"
        current_route = route
        if leg["state"]=="slowed":
            attribute = leg.get("slowed_attribute_q32")
            if attribute is None:
                attribute = move_attribute(leg["slowed_attribute"])
            current_route = dict(route,move_attribute_q32=attribute)
        travel = _continued_move(scene, current_route, leg["steering"], source,
                                 previous, previous["velocity"], dt, 1, round_position=True)
        if travel["status"] != "conditional_projection":
            return [], travel["reason"]
        leg.update(frame=time-1, travel=travel, command=source, start=previous)
        if start_next:
            leg.update(kind="free_motion_sample", start=dict(previous, active_ticks=0, leg_source=source["source"]))
    age = time - leg["frame"]
    sample = leg["travel"]["samples"][age-1]
    reached = age == leg["travel"]["arrival_ticks"]
    row = dict(leg["start"], row=sample["row"], col=sample["col"], velocity=sample["velocity"],
               speed=sample["speed"], active_ticks=leg["start"]["active_ticks"]+age, leg_end=reached)
    row.pop("vent_slow", None)
    if leg["state"] == "slowed":
        row["vent_slow"] = leg["alias"]
    leg["last"] = row
    rows = [(leg["kind"], row)]
    if reached:
        if source["kind"] == "END":
            leg["finished"] = True
            rows.append(("route_end_in_range", dict(instance_id=row["instance_id"], target=source["target"],
                                                    source=source["source"])))
        else:
            index = route["commands"].index(source)+1
            following = route["commands"][index]
            if following["kind"] == "WAIT_FOR_SECONDS":
                pos = {k:row[k] for k in ("row","col")}
                sequence = checkpoint_sequence(route["commands"], index, pos, timestep=dt)
                if any(e["command"] != "WAIT_FOR_SECONDS" for e in sequence["events"]):
                    return [], "unreviewed_checkpoint_after_vent_motion"
                events = {}
                for event in sequence["events"]:
                    fields = dict(instance_id=row["instance_id"], command=event["command"], source=event["source"],
                                  position=event["position"], disappeared=False)
                    events.setdefault(time + event["offset"], []).append((event["event"], fields))
                rows.extend(events.pop(time))
                leg["wait"] = dict(until=time+sequence["next_offset"], events=events, next=sequence["next_command"])
            elif following["kind"] not in ("MOVE","END"):
                return [], "unreviewed_checkpoint_after_vent_motion"
    return rows, None

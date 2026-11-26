"""Ordinary V079 heat vent recovery, channel clock and per-pulse contact snapshots."""
from fractions import Fraction

from .attack_range import circle_in_range, range_cells
from .damage import ordinary_arts_hit
from .fixed import ONE
from .lin_interactions import LIN, _templates, barrier_hit
from .mystic_timing import fixed_wait_frames
from .skill_recovery import LIN_DRAW_OPTIONS, NaturalSkillPoints, RecoveryTimer, lin_hit_draw, lin_native_stream
from .volley_damage import _position_samples


def channel_offsets(step, duration, interval, post_checker):
    """Offsets from an OBJECTS cast. The coroutine processes the zero predelay that frame."""
    if min(step, duration, interval, post_checker) <= 0:
        raise ValueError("喷口周期参数必须为正 Q32.32")
    stride = fixed_wait_frames(Fraction(interval, step))
    offsets = [0]
    while duration - offsets[-1] * step > post_checker:
        offsets.append(offsets[-1] + stride)
    remaining = max(0, duration - offsets[-1] * step)
    return offsets, offsets[-1] + fixed_wait_frames(Fraction(remaining, step))


def _pulse_contact(target, cells, board):
    """Input snapshots supply the selector's non-geometric permission for this unit at this pulse."""
    if not target["eligible"] or not target["ground"] or target.get("entity_category", 1) != 1 or not circle_in_range(
            target["row"], target["col"], target["radius"], cells, box_query=True):
        return None
    key = target["id"]
    record = dict(target_id=key, arts_attack=board["value"], ignore_for_sp=False)
    if target.get("side") != "ally":
        record["slow_buff"] = dict(key="eradio_reduce_speed", move_speed=board["move_speed"],
                                   seconds=board["buff_time"], refresh="max_remaining_or_new_duration")
    if target.get("marked") is False or target.get("overloaded") is True:
        record["energy"] = dict(added=0)
    elif target.get("marked") is True and target.get("overloaded") is False:
        record["energy"] = dict(requested_add=board["energy"], ability="blacksteel_energy")
    else:
        record["energy"] = dict(status="requires_mark_and_overload_snapshot")
    if key == LIN:
        record["talent_roll"] = "pending_battle_die"
    if "hp_q32" in target and "resistance_q32" in target and target.get("ordinary_damage"):
        hit = ordinary_arts_hit(attack_raw=int(board["value"] * ONE),
                                hp_raw=target["hp_q32"], resistance_raw=target["resistance_q32"])
        record["damage_after_resistance_q32"] = hit.damage_raw
        if "barrier_threshold_q32" in target:
            record["barrier"] = barrier_hit(hit.damage_raw, target["barrier_threshold_q32"],
                                            active=target["barrier_active"])
        else:
            record["ordinary_hp_after_q32"] = hit.hp_after_raw
    return record


def vent_timeline(tables, scene, horizon, *, first_object_frame=1, received_hits=(), targets_at=None):
    """Received hits are confirmed ON_TAKE_DAMAGE events with before_objects or after_objects timing.

    targets_at(alias, frame) returns current snapshots of potential contacts, or None for unknown.
    This timeline never invents damage recipients or feeds conditional effects back into movement.
    """
    if type(first_object_frame) is not int or type(horizon) is not int or min(first_object_frame, horizon) < 0:
        raise ValueError("喷口首个对象更新帧与终点须为非负整数")
    _templates(tables)
    profile = tables._prefab_data["lin_ordinary_shield_and_spout"]
    step = int(tables.fixed_timestep() * ONE)
    ranges = tables.read("excel/range_table.json")
    grid = scene.level["mapData"]["map"]
    devices = {}
    hits = {}
    for hit in received_hits:
        frame, alias, phase = hit["frame"], hit["alias"], hit["phase"]
        if (type(frame) is not int or not first_object_frame <= frame <= horizon
                or alias not in scene.device_skills or phase not in ("before_objects", "after_objects")):
            raise ValueError("喷口受击事件缺少在场设备、有效帧或相对对象更新顺序")
        index = (alias, frame, phase)
        hits[index] = hits.get(index, 0) + 1
    for unit in scene.predefines:
        if unit["inst"]["characterKey"] != "trap_124_eradio":
            continue
        alias = unit["alias"]
        skill = scene.device_skills[alias]
        sp = skill["spData"]
        if (skill["prefabId"] != "sktok_eradio" or skill["skillType"] != "AUTO"
                or sp["spType"] != "INCREASE_WITH_TIME" or sp["maxChargeTime"] != 1 or sp["increment"] != 1):
            raise ValueError("该预置喷口的技力或技能配置尚未支持")
        board = {v["key"]: v["value"] for v in skill["blackboard"]}
        if not float(board["sp"]).is_integer() or board["sp"] < 0:
            raise ValueError("不支持非整数或负数的喷口受击回技")
        offsets, end = channel_offsets(step, int(board["duration"] * ONE),
            int(profile["pulse_interval"] * ONE), int(profile["post_delay_checker"] * ONE))
        cells = range_cells(ranges[skill["rangeId"]], dict(**unit["position"], direction=unit["direction"]),
                            len(grid), len(grid[0]))
        gauge = NaturalSkillPoints(min(sp["initSp"], sp["spCost"]), sp["spCost"], sp["spCost"], RecoveryTimer(ONE))
        casts, pulses, credits, ends = [], [], [], []
        devices[alias] = dict(skill_level=unit["mainSkillLvl"], initial_sp=gauge.points, cost=gauge.cost,
                              recovery_per_hit=board["sp"], cells=cells, casts=casts, pulses=pulses, ends=ends, hits=credits)
        start, stopped = None, False
        for frame in range(first_object_frame, horizon + 1):
            def credit(phase):
                for _ in range(hits.get((alias, frame, phase), 0)):
                    before = gauge.points
                    if not stopped:
                        gauge.points = min(gauge.maximum, before + int(board["sp"]))
                    credits.append(dict(frame=frame, phase=phase, before=before, after=gauge.points, stopped=stopped))
            credit("before_objects")
            gauge.advance(step, stopped=stopped)
            stopped = start is not None
            if start is None and gauge.points == gauge.cost:
                start, stopped = frame, True
                gauge.points -= gauge.cost
                casts.append(frame)
            credit("after_objects")
            if start is None:
                continue
            age = frame - start
            if age in offsets:
                current = None if targets_at is None else targets_at(alias, frame)
                contacts = None if current is None else [v for t in current if (v := _pulse_contact(t, cells, board))]
                pulses.append(dict(frame=frame, phase="COROUTINES", contacts=contacts))
            if age == end:
                ends.append(frame)
                start = None
        devices[alias]["final_sp"] = gauge.points
    return dict(devices=devices, first_object_frame=first_object_frame, through_frame=horizon, step_q32=step,
                conditions=["deterministic_fixed_time_and_uninterrupted_channel", "all_external_vent_hits_supplied",
                            "ordinary_unmodified_recovery", "eligible_target_snapshots_sampled_each_pulse"])


def _lin_sp_interval(sp, hit_frames, horizon, samples, step):
    """Before the first possibly accepted manual input the SP extremes are all fails and all successes."""
    cost = sp["cost"]
    amounts = {r["sp"] for r in sp["hit_recovery"]["rolls"]}
    if len(amounts) != 1:
        return dict(status="unresolved_talent_amount")
    amount, = amounts
    low, high = (NaturalSkillPoints(sp["initial_sp"], cost, cost, RecoveryTimer(ONE)) for _ in range(2))
    rate_changes = {r["frame"]:r["period_q32"] for r in sp.get("period_changes",())}
    buttons = {b["frame"]: b for b in sp["buttons"]}
    rows, attempts = [], []
    ready = [None, None]
    status = "bounded_until_horizon"
    for frame in range(sp["deployment_frame"], horizon + 1):
        if frame != sp["deployment_frame"]:
            low.advance(step)
            high.advance(step)
        if frame in rate_changes:
            from .mostima_aura import set_period
            for state in (low,high):
                set_period(state,rate_changes[frame],stopped=False)
        if high.points >= cost and ready[0] is None:
            ready[0] = frame
        if low.points >= cost and ready[1] is None:
            ready[1] = frame
        if frame in buttons:
            outcomes = ["begin_animation"] if low.points >= cost else (
                ["rejected", "begin_animation"] if high.points >= cost else ["rejected"])
            attempts.append(dict(frame=frame, before=[low.points, high.points], possible_outcomes=outcomes))
            if high.points >= cost:
                status = "manual_activation_changes_mode"
                break
        hits = hit_frames.get(frame, 0)
        high.points = min(high.maximum, high.points + amount * hits)
        if high.points >= cost and ready[0] is None:
            ready[0] = frame
        if low.points >= cost and ready[1] is None:
            ready[1] = frame
        if hits or frame in samples or frame == horizon:
            rows.append(dict(frame=frame, minimum=low.points, maximum=high.points, eligible_vent_hits=hits))
    return dict(status=status, through_frame=frame if status == "bounded_until_horizon" else frame - 1,
                samples=rows, buttons=attempts, first_possible_ready=ready[0], guaranteed_ready=ready[1],
                talent_rolls=sp["hit_recovery"]["rolls"], eligible_hits_before_divergence=sum(r["eligible_vent_hits"] for r in rows))


def lin_joint_sp(sp, timeline, shell_impacts, through, samples, protection_end):
    """Carry the gauge and its partially elapsed second across guaranteed casts and manual stops."""
    rolls = sp["hit_recovery"]["rolls"]
    if len(rolls) != 1 or not 0 < rolls[0]["probability"] < 1:
        return None
    hits = {}
    for alias, vent in timeline["devices"].items():
        for pulse in vent["pulses"]:
            if pulse["frame"] >= protection_end and any(c["target_id"] == LIN for c in pulse["contacts"] or ()):
                hits.setdefault(pulse["frame"], []).append(("COROUTINES",alias))
    for shot in shell_impacts:
        if shot["frame"] >= protection_end and shot["ignore_for_sp"] is not True:
            hits.setdefault(shot["frame"], []).append(("OBJECTS",shot["source"]))
    buttons = {r["frame"]:r for r in sp["buttons"]}
    casts = {r["cast_frame"] for r in sp["buttons"] if r["outcome"] == "begin_animation"}
    changes = {r["frame"]:r["stopped"] for r in sp["modifier_changes"]}
    cost, step, amount = sp["cost"], sp["step_q32"], rolls[0]["sp"]
    states = {(sp["initial_sp"], ONE)}
    branches, attempts, stopped = [], [], False
    readiness = [dict(start_frame=sp["deployment_frame"], first_possible=None, guaranteed_by=None)]
    hit_stream=lin_native_stream(sp)

    def after_hits(current, sources, frame, phase):
        if not sources:
            return current
        successes = range(len(sources)+1)
        if hit_stream is not None or (not stopped and any(k in sp for k in LIN_DRAW_OPTIONS)):
            successes = (sum(lin_hit_draw(sp,frame,phase,i,source,hit_stream) for i,source in enumerate(sources)),)
        if stopped:
            return current
        return {(min(cost,points+success*amount), remaining) for points,remaining in current
                for success in successes}

    def bounds():
        values = [p for p,_ in states]
        return [min(values),max(values)]

    def record_button(button):
        nonlocal states
        lo, hi = bounds()
        if button["outcome"] in ("begin_animation", "rejected"):
            outcomes = (["begin_animation"] if lo >= cost else
                        ["rejected"] if hi < cost else ["rejected", "begin_animation"])
        else:
            outcomes = [button["outcome"]]
        attempt = dict(frame=button["frame"], before=[lo, hi], possible_outcomes=outcomes)
        if len(outcomes) > 1:
            attempt["reachable_state_counts"] = dict(
                begin_animation=sum(p >= cost for p,_ in states), rejected=sum(p < cost for p,_ in states))
        if button.get("input_phase") == "after_tick":
            attempt["input_phase"] = "after_tick"
        attempts.append(attempt)
        if button.get("observed_outcome") == "begin_animation":
            attempt.update(observed_outcome="begin_animation", outcomes_before_observation=list(outcomes))
            if "begin_animation" not in outcomes:
                attempt["possible_outcomes"] = []
                return dict(frame=button["frame"], reason="observed_lin_activation_unreachable",
                            reachable_sp=[lo,hi], cost=cost)
            before_count = len(states)
            states = {(points,timer) for points,timer in states if points >= cost}
            attempt.update(possible_outcomes=["begin_animation"], before_condition_state_count=before_count,
                           retained_state_count=len(states), sp_after_condition=bounds())
            outcomes = attempt["possible_outcomes"]
        if outcomes != [button["outcome"]]:
            return dict(frame=button["frame"], reason="lin_button_depends_on_hit_recovery", possible_outcomes=outcomes)

    pending = None
    period = ONE
    rate_changes = {r["frame"]:r["period_q32"] for r in sp.get("period_changes",())}
    for frame in range(sp["deployment_frame"], through+1):
        if frame > sp["deployment_frame"] and not stopped:
            advanced = set()
            for points, remaining in states:
                if points < cost:
                    timer = RecoveryTimer(period)
                    timer.remaining = remaining
                    points = min(cost, points+timer.advance(step))
                    remaining = timer.remaining
                advanced.add((points, remaining))
            states = advanced
        if changes.get(frame) is False:
            stopped = False
        incoming = hits.get(frame, ())
        before = [s for phase,s in incoming if phase=="OBJECTS"]
        after = [s for phase,s in incoming if phase=="COROUTINES"]
        states = after_hits(states,before,frame,"OBJECTS")
        if frame in rate_changes:
            from .mostima_aura import set_period
            adjusted = set()
            for points, remaining in states:
                gauge = NaturalSkillPoints(points,cost,cost,RecoveryTimer(period))
                gauge.timer.remaining = remaining
                set_period(gauge,rate_changes[frame],stopped=stopped)
                adjusted.add((gauge.points,gauge.timer.remaining))
            states, period = adjusted, rate_changes[frame]
        button = buttons.get(frame)
        if button and button.get("input_phase") != "after_tick":
            pending = record_button(button)
            if pending:
                pending["incoming_before_button"] = [dict(phase="OBJECTS",source=s) for s in before]
                break
        if frame in casts:
            states = {(p-cost,t) for p,t in states}
            stopped = True
            readiness.append(dict(start_frame=frame,first_possible=None,guaranteed_by=None))
        # If a vent hits on the cast's COROUTINES frame, the unknown iterator
        # order cannot change capped SP before deduction or add SP after it.
        states = after_hits(states,after,frame,"COROUTINES")
        if button and button.get("input_phase") == "after_tick":
            pending = record_button(button)
            if pending:
                pending["incoming_before_button"] = [dict(phase=p,source=s) for p,s in incoming]
                break
        if not stopped and readiness[-1]["guaranteed_by"] is None:
            low, high = bounds()
            if high == cost and readiness[-1]["first_possible"] is None:
                readiness[-1]["first_possible"] = frame
            if low == cost:
                readiness[-1]["guaranteed_by"] = frame
        if incoming or frame in samples or frame in buttons or frame == through:
            branches.append(dict(frame=frame, sp=bounds(), recovery_stopped=stopped,
                                 incoming=[dict(phase=p,source=s) for p,s in incoming]))
    return dict(through_frame=pending["frame"]-1 if pending else through, pending=pending, samples=branches,
                buttons=attempts,readiness=readiness,talent_roll=rolls[0])


def opening_vents(tables, scene, cards, placements, sp_by_operator, spawned, projected_events, radii,
                  horizon, samples, ordinary_window, *, removed_by=None, received_hits=()):
    """Join a predefined vent clock to opening snapshots. Enemy contacts bound the unmodified movement branch."""
    timeline = vent_timeline(tables, scene, horizon, received_hits=[h for h in received_hits if h["frame"] <= horizon])
    removed_by = removed_by or {}
    timeline["shared_removal_deadlines"] = dict(removed_by)
    pulse_frames = {r["frame"] for device in timeline["devices"].values() for r in device["pulses"]}
    active = {}
    positions = _position_samples(projected_events, pulse_frames, active)
    route_ends = {d["instance_id"]: f for f, rows in projected_events.items()
                  for kind, d in rows if kind == "route_end_in_range"}
    reviewed = set((ordinary_window or {}).get("reviewed_enemies", ()))
    bomber_window = (ordinary_window or {}).get("bombardier_window")
    lin = placements.get(LIN)
    sp = sp_by_operator.get(LIN, {})
    valid_until = min(horizon, (ordinary_window or {}).get("through_frame", -1))
    bound = (ordinary_window or {}).get("pending") or {}
    # An INPUT button precedes that frame's COROUTINES hit. Record its possible outcomes from the incoming gauge.
    bound_frame = bound.get("frame")
    button_frame = bound_frame if bound.get("reason") == "lin_skill_button_input" else None
    lin_hits, earliest = {}, None
    for alias, device in timeline["devices"].items():
        board = {r["key"]: r["value"] for r in scene.device_skills[alias]["blackboard"]}
        cells = device["cells"]
        for pulse in device["pulses"]:
            frame = pulse["frame"]
            occupants = []
            for op, placement in placements.items():
                if not placement["frame"] <= frame or (placement["row"], placement["col"]) not in cells:
                    continue
                occupant = dict(id=op, row=placement["row"], col=placement["col"], radius=0, eligible=True,
                                ground=True, marked=False, side="ally")
                record = _pulse_contact(occupant, cells, board)
                if op == LIN and lin and ordinary_window and ordinary_window.get("status") == "conditional_inactive_lin":
                    if placement["protection_end_exclusive"] <= frame <= valid_until + int(button_frame == valid_until + 1):
                        if frame <= valid_until:
                            record.update(ordinary_shield_breaks=False, hp_loss_q32=0)
                        lin_hits[frame] = lin_hits.get(frame, 0) + 1
                    else:
                        record["talent_roll"] = "requires_current_mode_targetable_and_barrier_state"
                occupants.append(record)
            unresolved = []
            for key, born in spawned.items():
                if born["spawn_frame"] > frame or removed_by.get(key, frame) < frame:
                    continue
                if key not in positions.get(frame, {}) or key not in radii:
                    if scene.level["routes"][born["route"]]["motionMode"] == "WALK" and route_ends.get(key, frame + 1) > frame:
                        unresolved.append(key)
                    continue
                pos = positions[frame][key]
                if pos is None or not circle_in_range(*pos, radii[key], cells, box_query=True):
                    continue
                if scene.level["routes"][born["route"]]["motionMode"] != "WALK":
                    continue
                target = dict(id=key, row=pos[0], col=pos[1], radius=radii[key], eligible=True, ground=True, side="enemy")
                record = _pulse_contact(target, cells, board)
                record.update(enemy=born["enemy"], position=pos, occupancy="conditional_free_motion_if_still_alive_and_selectable",
                              active_in_motion_projection=key in active[frame])
                occupants.append(record)
                if earliest is None or frame < earliest["frame"]:
                    earliest = dict(frame=frame, alias=alias, instance_id=key, reason="possible_vent_damage_slow_and_energy")
            if unresolved:
                pulse["unresolved_enemy_positions"] = unresolved
                if earliest is None or frame < earliest["frame"]:
                    earliest = dict(frame=frame, alias=alias, reason="vent_enemy_occupancy_unknown", instances=unresolved)
            pulse["contacts"] = occupants
    timeline["first_possible_enemy_interaction"] = earliest
    timeline["clock_evidence"] = "runs/2026-11-19-device-timeline/native.json"
    timeline["opening_enemy_input_scope"] = dict(reviewed_enemies=sorted(reviewed),
        reason="unblocked_melee_only_or_no_attack_on_highland_predefined_vents" if reviewed else "external_hits_unspecified",
        next_unreviewed_enemy=min((max(r["spawn_frame"], bomber_window["through_frame"]+1)
            if bomber_window and r["enemy"] == "enemy_1359_ekulsr_2" else r["spawn_frame"]
            for r in spawned.values() if r["enemy"] not in reviewed), default=None))
    if bomber_window:
        timeline["opening_enemy_input_scope"]["bombardier"] = dict(through=bomber_window["through_frame"],
            reason="same_side_vent_excluded_by_projectile_target_options", pending=bomber_window["pending"])
    if lin and valid_until >= lin["frame"] and sp.get("hit_recovery", {}).get("status") == "conditional_hit_recovery":
        sp_end = min(horizon, valid_until + int(button_frame == valid_until + 1))
        timeline["lin_skill_points"] = _lin_sp_interval(sp, lin_hits, sp_end, samples, timeline["step_q32"])
        sp["incoming_vent_sp"] = timeline["lin_skill_points"]
        if lin_hits and not any(k in sp for k in LIN_DRAW_OPTIONS):
            sp["natural_path_condition"] = "no_effective_vent_talent_credits"
    return timeline

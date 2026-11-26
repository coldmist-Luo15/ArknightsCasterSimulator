"""Blacksteel bombardier attacks, movement and periodic energy changes."""
from collections import defaultdict
from math import hypot, sqrt
import json

from .baked_mount import single
from .attack_range import RangeContacts, circle_in_range
from .bombardier_attack import first_shot
from .enemy_motion import _continued_move
from .fixed import ONE
from .opening_scene import bundle_environment
from .skill_recovery import RecoveryTimer
from .vent_walk import _walk_tick, slowed_walk_from_sample

BOMBER = "enemy_1359_ekulsr_2"


def _trigger_frames(start, first_tick_delay, horizon, interval, step):
    clock = RecoveryTimer(int(interval * ONE))
    return {f for f in range(start + first_tick_delay, horizon + 1) if clock.advance(step)}


def _within_steal_radius(a, b, radius):
    # FindTargetsInCircle_DISPOSE reads mapPosition for both units, doing every operation in binary32.
    dr, dc = (single(single(x)-single(y)) for x,y in zip(a,b))
    distance = single(sqrt(single(single(dr*dr)+single(dc*dc))))
    return distance <= single(single(radius)+single(1e-5))


def _energy_rules(tables, axe_key):
    cache = ("blacksteel_periodic_energy", axe_key)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    for actor, talent, template in ((axe_key, "steal_energy", "enemy_erob_steal_energy_ability"),
                                     (BOMBER, "dec_energy", "enemy_esheld_dec_energy[trigger]")):
        buffs = tables._component_fields("enemies", actor, ("_buffs",), "periodic_energy",
                                         children=("Talents",talent))["_buffs"]
        found = [b for b in buffs if b["templateKey"] == template]
        if len(found) != 1:
            return False
        b = found[0]
        if (b["lifeTimeType"], b["triggerLifeType"], b["triggerInterval"], b["waitFirstTriggerInterval"],
                b["firstTriggerInterval"], b["isStunnable"], b["isSilenceable"]) != (2,2,-1,1,-1,0,0):
            return False
    raw, source = tables.verified_bundle("config/buff_template_holder.ab")
    holder, = (o for o in bundle_environment(raw).objects if o.type.name == "MonoBehaviour")
    names = {"enemy_erob_steal_energy_ability", "enemy_erob_steal_energy", "enemy_esheld_dec_energy[trigger]",
             "enemy_esabr_clear_energy", "empty"}
    templates = {r["templateKey"]:r for r in holder.read_typetree()["_templates"] if r["templateKey"] in names}
    nodes = {name: {e["key"]: json.loads(e["value"]["SerializedState"])
                    for e in row["eventToActions"]["_items"]} for name,row in templates.items()}
    outer = nodes["enemy_erob_steal_energy_ability"][2]
    action = outer[-1]
    inner = nodes["enemy_erob_steal_energy"][0]
    reduce = nodes["enemy_esheld_dec_energy[trigger]"][2]
    cond = inner[-1]["_conditionNode"]
    success = inner[-1]["_succeedNodes"]
    mark, clear, update = nodes["enemy_esabr_clear_energy"][0]
    valid = (templates["enemy_erob_steal_energy_ability"]["onEventPriority"] == templates["empty"]["onEventPriority"]
             and not tables.read("buff_table.json")["act28side_over_energy_buff"]["overrideOnEventPriority"]
             and not tables._component_fields("enemies", axe_key, ("_buffs",), "periodic_energy",
                                              children=("Talents","steal_energy"))["_buffs"][0]["overrideOnEventPriority"]
             and outer[0]["_buffKeys"] == ["act28side_over_energy_buff"] and outer[1]["$type"].endswith("+IfNot")
             and action["$type"].endswith("+CreateBuffInCircleRange") and action["_excludeTarget"]
             and not action["_limitCount"] and not action["_checkInArcCircle"]
             and action["_targetOptions"]["targetSide"] == "ALLY"
             and action["_buffs"][0]["templateKey"] == "enemy_erob_steal_energy"
             and [n["_buffKeys"] for n in inner if "_buffKeys" in n] == [
                 ["blacksteel_energy[mark]"], ["act28side_over_energy_buff"], ["erob_immune_steal"]]
             and (cond["_buffKey"],cond["_condType"],cond["_stackCountKey"]) == ("act28side_energy_buff","GE","steal_count")
             and [n["_abilityOwnerType"] for n in success if n["$type"].endswith("+AddAbilityBlackboard")]
                 == ["BUFF_SOURCE","BUFF_OWNER"]
             and [(n["_ownerType"], n["_triggerEnergyBuffAbility"]) for n in success
                  if n["$type"].endswith("+TriggerSpecifiedAbility")] == [("BUFF_OWNER", True), ("BUFF_SOURCE", True)]
             and mark["_buffKeys"] == ["blacksteel_energy[mark]"]
             and clear["$type"].endswith("+ModifyAbilityBlackboard") and clear["_useOtherAbility"]
             and clear["_abilityName"] == clear["_blackboardKeys"] == update["_abilityName"] == "blacksteel_energy"
             and clear["_value"] == 0 and update["_triggerEnergyBuffAbility"]
             and reduce[0]["_buffKeys"] == ["blacksteel_energy[mark]"]
             and reduce[1]["_buffKeys"] == ["act28side_over_energy_buff"] and reduce[2]["$type"].endswith("+IfNot")
             and reduce[3]["_addBlackboardKey"] == "dec_count" and reduce[4]["_triggerEnergyBuffAbility"])
    tables.source["periodic_energy_actions"] = source
    tables._prefab_data[cache] = valid
    return valid


def _bomber_rules(tables, scene):
    enemy = scene.enemies[BOMBER]
    attack_path = ("Modes", "Default", "Body", "Attack")
    ranged = tables._component_fields("enemies", BOMBER, ("_projectileKey", "_atkScale", "_damageType"),
                                      "bombardier_attack", children=attack_path)
    selector = tables._component_fields("enemies", BOMBER, ("_targetSide", "_targetMotion", "_targetCategory",
                                        "_postFilter","_limitTargetNum","_maxNum","_sortByTauntAtLast"),
                                        "bombardier_selector", children=(*attack_path, "Selector"))
    device = tables._component_fields("[uc]tokens", "trap_124_eradio", ("_sideType", "_category"), "bombardier_vent")
    raw, src = tables.verified_bundle("battle/prefabs/[uc]projectiles.ab")
    root = bundle_environment(raw).container["dyn/battle/prefabs/[uc]projectiles/projectile_enemy_ekulsr.prefab"].deref()
    comps = [root.assets_file.objects[k["component"]["m_PathID"]].read_typetree() for k in root.read_typetree()["m_Component"]]
    checker, = [c for c in comps if "_onlyCheckHitWhenReachTarget" in c]
    opts = checker["_targetOptions"]
    board = {k["key"]:k["value"] for k in enemy["talentBlackboard"]}
    if (ranged != dict(_projectileKey="projectile_enemy_ekulsr", _atkScale=.5, _damageType=1)
            or selector != dict(_targetSide=2, _targetMotion=1, _targetCategory=1, _postFilter=4,
                                _limitTargetNum=1,_maxNum=1,_sortByTauntAtLast=0)
            or device != dict(_sideType=2, _category=1) or not checker["_onlyCheckHitWhenReachTarget"]
            or (opts["targetSide"],opts["targetMotion"],opts["targetCategory"],opts["ignoreTargetSide"]) != (2,3,1,0)
            or board.get("blacksteel_energy.init_stack_cnt") != 25 or board.get("blacksteel_energy.max_stack_cnt") != 30
            or board.get("dec_energy.dec_count") != -1 or not enemy["rangeRadius"]["m_defined"]):
        return None
    tables.source["bombardier_splash"] = src
    return enemy["rangeRadius"]["m_value"], board


def _bombardier_target(frame, position, attack_radius, allies):
    candidates = [op for op,body_radius in allies if op["frame"] <= frame and
                  hypot(position[0]-op["row"],position[1]-op["col"]) <= attack_radius+body_radius]
    if len(candidates) < 2:
        return (candidates[0] if candidates else None), None
    # The two reviewed casters have equal taunt. Character.UpdateHatred includes
    # deployment order and time. Same-tick inputs need their native ordering.
    newest = max(op["frame"] for op in candidates)
    latest = [op for op in candidates if op["frame"] == newest]
    if len(latest) == 1 and all(op["operator_id"] in ("char_4080_lin","char_4046_ebnhlz") for op in candidates):
        return latest[0], None
    return None, "bombardier_operator_hatred_unresolved"


def apply_energy_triggers(frame, frame_triggers, bomber, amounts, positions, active, axes, overloads, removed_by, ended):
    """Evaluate object-phase transfers from center positions, before the later vent coroutine."""
    snapshot = positions[frame]
    events = []
    pending = None
    draining = {actor for kind,actor in frame_triggers if kind == "drain"}
    for kind,actor in frame_triggers:
        if removed_by.get(actor,frame) < frame or actor in ended:
            continue
        if actor in overloads:
            over = overloads[actor]
            # Equal event priorities preserve insertion order. Its permanent theft buff ticks before
            # the later overload buff can expire during this unit's first possible expiry update.
            last_blocked = over["through"] + int(kind == "steal" and actor in axes)
            if over["buff_frame"] < frame <= last_blocked:
                continue
            return events, dict(frame=frame, reason="periodic_energy_at_overload_transition", instance_id=actor)
        if kind == "drain":
            before = amounts.get(actor,0)
            amounts[actor] = max(0,before+bomber["drain"])
            events.append(dict(frame=frame,kind="self_drain",instance_id=actor,before=before,after=amounts[actor]))
            continue
        for donor,count in list(amounts.items()):
            if not count or donor in axes or donor in ended or removed_by.get(donor,frame)<frame:
                continue
            if actor not in snapshot or any(donor not in positions[t] for t in (frame-1,frame)):
                pending = dict(frame=frame, reason="axe_or_donor_position_unknown", instances=[actor,donor])
                break
            source = snapshot[actor]
            candidates = [positions[t][donor] for t in (frame-1,frame)]
            hits = [source is not None and pos is not None and _within_steal_radius(source,pos,bomber["steal_radius"])
                    for pos in candidates]
            if not any(hits):
                continue
            if donor in overloads:
                if overloads[donor]["buff_frame"] < frame <= overloads[donor]["through"]:
                    continue
                pending = dict(frame=frame, reason="axe_donor_at_overload_transition", instance_id=donor)
                break
            if (not all(hits) or donor not in active[frame] or removed_by.get(donor)==frame
                    or removed_by.get(actor)==frame or (donor in draining and count == 1)):
                pending = dict(frame=frame, reason="axe_transfer_at_life_or_range_boundary", instances=[actor,donor])
                break
            before = amounts.get(actor,0)
            capacity = bomber["axe_capacity"]
            amounts[actor], amounts[donor] = min(before + 1, capacity), count - 1
            event = dict(frame=frame, kind="steal", source=actor, donor=donor, source_position=source,
                         donor_positions=candidates, source_after=amounts[actor], donor_after=count - 1)
            if before < capacity and amounts[actor] == capacity:
                event["reached_overload"] = True
            events.append(event)
        if pending:
            break
    return events, pending


def _walk_after_gunner_wait(tables, scene, motion, birth, wait_source, sample, frame, resume):
    profile = motion["profiles"][motion["instance_profiles"][birth["instance_id"]]]
    route = profile["route"]
    commands = route["commands"]
    index = next(i for i,c in enumerate(commands) if c["source"] == wait_source)
    command = commands[index+1]
    if command["kind"] not in ("MOVE","END") or "velocity" not in sample:
        return None
    steering = tables.enemy_steering(birth["enemy_key"])
    travel = _continued_move(scene,route,steering,command,sample,sample["velocity"],tables.fixed_timestep(),1,
                             round_position=motion.get("continued_move_position_rounding",False))
    if travel["status"] != "conditional_projection":
        return None
    start = dict(sample,leg_source=command["source"],active_ticks=0,leg_end=False)
    return dict(route=route,command=command,steering=steering,state="ordinary",last=start,start=start,frame=frame,
                travel=travel,kind="post_wait_motion_sample",held_until=resume if resume>frame else None)


def _gunner_route(tables, scene, birth, original, horizon, removed, select_shot, motion, radius, pulses, devices, boards,
                  controls):
    """MOVE searches before advancing the cursor. Vent contacts sample the position after OBJECTS."""
    key = birth["instance_id"]
    projected = defaultdict(list)
    shots = []
    slowed_contacts = {}
    interruptions = []
    applied = []
    first = next((f for f, rows in sorted(original.items()) if any(
        k == "free_motion_sample" and r.get("active_ticks") == 1 for k, r in rows)), None)
    position = RangeContacts()
    walk_cursor = birth["frame"]-1
    resume = next_search = first
    last_sample = leg = wait = casting = None
    stunned_until = -1
    wait_ends = {row["source"]:f for f,rows in original.items() for k,row in rows if
                 k=="checkpoint_reached" and row.get("command")=="WAIT_FOR_SECONDS"}
    dt = tables.fixed_timestep()
    step = int(dt*ONE)
    def stop(frame, reason):
        projected[frame].append(("range_motion_unresolved",dict(instance_id=key)))
        return projected, shots, slowed_contacts, interruptions, applied, dict(frame=frame, reason=reason, instance_id=key)
    for frame in range(birth["frame"], min(horizon, removed)+1):
        if first is None and frame > birth["frame"] + motion["first_born_tick_delay_frames"]:
            return stop(frame, "bombardier_first_move_unknown")
        p = position.positions.get(key)
        eligible = first is not None and frame >= first
        if casting and frame > casting["cast_end_frame"]:
            casting = None
        if frame in controls:
            stun = controls[frame]
            if not eligible or p is None:
                return stop(frame,"bombardier_stun_state_unknown")
            if casting:
                fired = casting["release_frame"] < frame
                interruptions.append(dict(instance_id=key,frame=frame,acquire_frame=casting["acquire_frame"],
                                          projectile_released=fired,next_ready_frame=casting["next_ready_frame"]))
                for kind,hold in projected[casting["acquire_frame"]]:
                    if kind == "enemy_attack_hold":
                        hold["resume_move_frame"] = frame
                if not fired:
                    shots.pop()
                casting = None
            stunned_until = max(stunned_until,frame+stun["duration_ticks"])
            resume = stunned_until
            next_search = max(next_search,resume)
            projected[frame].append(("enemy_stun_hold",dict(instance_id=key,row=p[0],col=p[1],
                resume_move_frame=resume, route_sample=last_sample[1] if last_sample else None)))
            if leg:
                leg["held_until"] = resume
            applied.append(stun)
        if eligible and frame >= next_search:
            next_search = frame+3
            if p is None and key in position.unknown:
                return stop(frame, "bombardier_position_unknown")
            if p is not None:
                shot, reason = select_shot(frame,p)
                if reason:
                    return stop(frame, reason)
                if shot:
                    casting = shot
                    shots.append(shot)
                    resume = shot["cast_end_frame"]+2
                    next_search = max(shot["next_ready_frame"],resume)
                    projected[frame].append(("enemy_attack_hold",dict(instance_id=key,row=p[0],col=p[1],
                        cast_end_frame=shot["cast_end_frame"],resume_move_frame=resume,
                        route_sample=last_sample[1] if last_sample else None)))
                    if leg:
                        leg["held_until"] = resume
        can_move = not eligible or frame >= resume
        rows = []
        if wait:
            if can_move:
                walk_cursor += 1
                rows = [(k,r) for k,r in original.get(walk_cursor,()) if k=="free_motion_sample"
                        and r.get("leg_source")==wait["source"]]
            if frame == wait["end"]:
                sample = rows[-1][1] if rows else last_sample[1]
                marker = dict(instance_id=key,source=wait["source"],command="WAIT_FOR_SECONDS",
                              position={axis:sample[axis] for axis in ("row","col")},disappeared=False)
                rows.append(("checkpoint_reached",marker))
                if walk_cursor == wait["canonical_end"]:
                    rows.extend((k,r) for k,r in original[walk_cursor] if k=="post_wait_motion_sample")
                else:
                    leg = _walk_after_gunner_wait(tables,scene,motion,birth,wait["source"],sample,frame,resume)
                    if leg is None:
                        return stop(frame,"bombardier_wait_successor_unknown")
                    rows.append((leg["kind"],leg["start"]))
                wait = None
        elif can_move:
            if leg is None:
                walk_cursor += 1
                rows = original.get(walk_cursor, ())
            else:
                rows, reason = _walk_tick(scene,leg,frame,dt)
                if reason:
                    return stop(frame,reason)
        if rows:
            projected[frame].extend(rows)
            for kind,row in rows:
                position.update(frame,kind,row)
                if kind in ("post_wait_motion_sample","free_motion_sample","exit_motion_sample"):
                    last_sample = (kind,row)
                elif kind == "route_end_in_range":
                    return projected, shots, slowed_contacts, interruptions, applied, None
                elif (kind=="checkpoint_begin" and row["command"]=="WAIT_FOR_SECONDS" and not row["disappeared"]
                        and leg is None and any(r.get("leg_source")==row["source"] and "velocity" in r
                        for _,r in original.get(walk_cursor+1,()))):
                    end = wait_ends.get(row["source"],horizon+1)
                    wait = dict(source=row["source"],canonical_end=end,end=frame+end-walk_cursor)
                elif kind in ("checkpoint_begin","range_motion_unresolved","enemy_stun_hold"):
                    last_sample = None
        p = position.positions.get(key)
        if p is None or not pulses.get(frame):
            continue
        for alias in pulses[frame]:
            if not circle_in_range(*p,radius,devices[alias]["cells"],box_query=True):
                continue
            board = boards[alias]
            if not motion.get("continued_move_position_rounding") or last_sample is None:
                return stop(frame,"vent_bombardier_movement_unresolved")
            if leg is None or (leg["state"] == "ordinary" and not leg.get("wait")):
                leg = slowed_walk_from_sample(tables,scene,motion,birth,board,frame,last_sample,
                                              resume if eligible and resume>frame else None)
                if leg is None:
                    return stop(frame,"vent_bombardier_movement_unresolved")
            if board["move_speed"] != leg.get("modifier", board["move_speed"]):
                return stop(frame,"different_vent_speed_modifiers")
            leg.update(modifier=board["move_speed"], alias=alias, state="slowed")
            life = int(board["buff_time"]*ONE)
            leg["slow_through"] = max(leg.get("slow_through",frame),frame+(life+step-1)//step)
            slowed_contacts[frame,alias] = {k:leg[k] for k in
                ("slowed_attribute","level_move_multiplier","move_speed","slow_through")}
    return projected, shots, slowed_contacts, interruptions, applied, None


def bomber_frontier(tables, scene, born, history, placements, removed_by, horizon, axe_key, first_tick_delay,
                    *, operator_skins, motion, radii, pulses, devices, boards, motion_stuns=(), coroutine_deaths=()):
    """Evaluate selector polls before movement, stopping when attack or movement is undetermined."""
    bombers = {k:b for k,b in born.items() if b["enemy_key"] == BOMBER}
    if not bombers:
        return None
    start = min(b["frame"] for b in bombers.values())
    rule = _bomber_rules(tables, scene)
    if not rule or placements is None or first_tick_delay is None or not _energy_rules(tables, axe_key):
        return dict(first_spawn=start, through_frame=start-1, pending=dict(frame=start,reason="bombardier_inputs_unknown"))
    attack_radius, board = rule
    axes = {k:b for k,b in born.items() if b["enemy_key"] == axe_key}
    steal = {r["key"]:r["value"] for r in scene.enemies[axe_key]["talentBlackboard"]}
    if steal["steal_energy.steal_count"] != 1 or steal.get("blacksteel_energy.init_stack_cnt",0) != 0:
        return dict(first_spawn=start, through_frame=start-1, pending=dict(frame=start,reason="axe_energy_inputs_unknown"))
    allies = []
    for p in placements:
        if type(p.get("frame")) is not int:
            return dict(first_spawn=start, through_frame=start-1,
                        pending=dict(frame=start,reason="operator_deployment_frame_unresolved"))
        if scene.tile(p["row"], p["col"])["passableMask"] != "FLY_ONLY":
            return dict(first_spawn=start, through_frame=start-1, pending=dict(frame=start,reason="ground_blocking_unreviewed"))
        shape = tables._component_fields("characters",p["operator_id"],("m_Radius","m_Offset","m_Enabled"),
                                         "bombardier_receiver_radius",component_type="CircleCollider2D")
        transform = tables._component_fields("characters",p["operator_id"],("m_LocalScale",),
                                             "bombardier_receiver_scale",component_type="Transform")
        if (not shape["m_Enabled"] or any(shape["m_Offset"].values()) or
                any(transform["m_LocalScale"][axis] != 1 for axis in ("x","y"))):
            return dict(first_spawn=start, through_frame=start-1, pending=dict(frame=start,reason="unknown_operator_body"))
        allies.append((p, shape["m_Radius"]))
    step = int(tables.fixed_timestep()*ONE)
    theft_ticks = {k:_trigger_frames(b["frame"],first_tick_delay,horizon,steal["steal_energy.interval"],step)
                   for k,b in axes.items()}
    drain_ticks = {k:_trigger_frames(b["frame"],first_tick_delay,horizon,board["dec_energy.interval"],step)
                   for k,b in bombers.items()}
    clocks = {}
    for kind, actors in (("steal", theft_ticks), ("drain", drain_ticks)):
        for key, ticks in actors.items():
            for tick in ticks:
                clocks.setdefault(tick, []).append((kind,key))

    shots = []
    def result(through, pending=None):
        return dict(first_spawn=start, through_frame=through, pending=pending, energy_events=[],
                    shots=shots,
                    periodic_triggers={f:rows for f,rows in sorted(clocks.items()) if f <= through},
                    first_object_tick_delay=first_tick_delay, steal_radius=steal["steal_energy.range_radius"],
                    axe_capacity=steal["blacksteel_energy.max_stack_cnt"],
                    bomber_initial=int(board["blacksteel_energy.init_stack_cnt"]), drain=int(board["dec_energy.dec_count"]),
                    axe_interval=steal["steal_energy.interval"], drain_interval=board["dec_energy.interval"])

    by_actor = {key: defaultdict(list) for key in bombers}
    for tick, rows in history.items():
        for kind, row in rows:
            key = row["instance_id"]
            if key in by_actor:
                by_actor[key][tick].append((kind, row))
    slow_contacts = {}
    interruptions = []
    applied = []
    pending = None
    for key, birth in bombers.items():
        removed = removed_by.get(key, horizon+1)
        def select_shot(frame, p):
            operator, reason = _bombardier_target(frame,p,attack_radius,allies)
            if operator is None:
                return None, reason
            shot = first_shot(tables,scene,key,BOMBER,frame,p,operator,allies,operator_skins,
                              motion.get("continued_move_position_rounding",False))
            if shot is None:
                return None, "bombardier_shot_inputs_unknown"
            if key in removed_by and removed <= shot["impact_frame"]:
                if removed < shot["release_frame"]:
                    shot["removed_before_release_by"] = removed
                elif removed == shot["impact_frame"] and (key, removed) in coroutine_deaths:
                    shot["source_removal_after_impact"] = "COROUTINES"
                else:
                    return None, "bombardier_source_lifetime_at_impact"
            return shot, None

        controls = {s["frame"]:s for s in motion_stuns if s["instance_id"]==key}
        projected, actor_shots, contacts, cuts, stuns, stop = _gunner_route(tables,scene,birth,by_actor[key],horizon,
            removed,select_shot,motion,radii[key],pulses,devices,boards,controls)
        interruptions.extend(cuts)
        applied.extend(stuns)
        for tick in by_actor[key]:
            history[tick][:] = [(k,r) for k,r in history[tick] if r["instance_id"] != key]
        for tick,rows in projected.items():
            history.setdefault(tick,[]).extend(rows)
        # Starting the windup still stops movement. Only emitted shells join the damage clock.
        for shot in actor_shots:
            if "removed_before_release_by" in shot:
                interruptions.append(dict(instance_id=key, frame=shot["removed_before_release_by"],
                    acquire_frame=shot["acquire_frame"], projectile_released=False, reason="source_removed_before_release"))
            else:
                shots.append(shot)
        slow_contacts.update({(tick,alias,key):values for (tick,alias),values in contacts.items()})
        if stop and (pending is None or stop["frame"] < pending["frame"]):
            pending = stop
    shots.sort(key=lambda s:s["acquire_frame"])
    return dict(result(pending["frame"]-1 if pending else horizon,pending), slow_contacts=slow_contacts,
                interrupts=interruptions, applied_stuns=applied)

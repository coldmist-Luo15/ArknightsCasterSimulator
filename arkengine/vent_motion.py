"""Heat-vent pulses and movement along checked, unobstructed V079 MOVE legs."""
from collections import defaultdict

from .attack_range import circle_in_range
from .blacksteel_threats import BOMBER, apply_energy_triggers, bomber_frontier
from .bombardier_attack import shell_attack
from .damage import ordinary_arts_hit
from .device_timeline import vent_timeline
from .enemy_aura import DRONE, resistance_aura
from .fixed import ONE
from .lin_interactions import AXE, MELEE_ONLY
from .vent_walk import _slowed_leg, _walk_tick
from .volley_damage import _position_samples


def _overload(tables, enemy_key, stats, frame, step, *, object_trigger=False):
    energy = tables._component_fields("enemies", enemy_key, ("_castAbility", "_overEnergied"),
                                      "vent_enemy_energy", children=("Talents", "blacksteel_energy"))
    ability = tables._component_fields("enemies", enemy_key, ("_waitForCasting", "_waitForAttackEvent", "_preDelay",
        "_checkBuffEarlyFinish", "_buffs"), "vent_enemy_overload", children=("Talents", "over_energy"))
    definition = tables.read("buff_table.json")["act28side_over_energy_buff"]
    modifiers = definition["attributes"]["attributeModifiers"]
    cleanup = tables._component_fields("enemies", enemy_key, ("_onlyCheckOnStopAffect", "_buffs"),
                                      "vent_overload_expiry", children=("Talents", "over_energy"))
    if (not energy["_overEnergied"] or energy["_castAbility"] != "over_energy" or ability["_waitForCasting"]
            or ability["_waitForAttackEvent"] or ability["_preDelay"] or ability["_checkBuffEarlyFinish"]
            or not any(b["buffKey"] == "act28side_over_energy_buff" and b["loadFromDB"] for b in ability["_buffs"])
            or definition["durationKey"] != "duration" or definition["attributes"]["abnormalFlags"]
            or definition["templateKey"] != "empty" or definition["lifeTimeType"] != "LIMITED"
            or cleanup["_onlyCheckOnStopAffect"] or [b["templateKey"] for b in cleanup["_buffs"]] != ["enemy_esabr_clear_energy"]
            or {(m["attributeType"], m["formulaItem"], m["loadFromBlackboard"]) for m in modifiers} != {
                ("DEF","FINAL_SCALER",True), ("MAGIC_RESISTANCE","ADDITION",True), ("ATTACK_SPEED","ADDITION",True)}):
        return None
    duration = stats.get("over_energy.duration")
    resistance = stats.get("over_energy.magic_resistance")
    if not duration or not resistance:
        return None
    # A new waiter registered from within an object callback can resume at the end of this tick
    # or the following one. A vent coroutine has already entered the tick's coroutine dispatch.
    earliest, latest = frame + int(not object_trigger), frame + 1
    life = int(duration * ONE)
    expiry = (life + step - 1) // step
    return dict(trigger=frame, trigger_phase="OBJECTS" if object_trigger else "COROUTINES",
                buff_frame=latest, earliest_buff_frame=earliest, phase="COROUTINES",
                attack_speed_bonus=stats["over_energy.attack_speed"], final_defense_multiplier=stats["over_energy.def"],
                resistance_bonus=resistance, through=earliest + expiry - 1,
                expiry_object_frames=[earliest + expiry, latest + expiry],
                energy_cleared_by=latest + expiry + 1)


def first_vent_segment(tables, scene, motion, history, radii, births, horizon, *, removed_by=None, received_hits=(),
                       placements=None, motion_stuns=(), operator_skins=None, coroutine_deaths=()):
    """Apply pulses from true receiver positions. Stop before unsupported energy, route or timing changes."""
    devices = vent_timeline(tables, scene, horizon, received_hits=received_hits)["devices"]
    pulses = defaultdict(list)
    boards = {}
    for alias, dev in devices.items():
        boards[alias] = {r["key"]:r["value"] for r in scene.device_skills[alias]["blackboard"]}
        for pulse in dev["pulses"]:
            pulses[pulse["frame"]].append(alias)
    if not pulses:
        return None
    dt = tables.fixed_timestep()
    removed_by = removed_by or {}
    step = int(dt * ONE)
    born = {b["instance_id"]:b for b in births if b["kind"] == "spawn"}
    supported = (*MELEE_ONLY, DRONE, AXE, BOMBER)
    unsupported_spawn = min((b["frame"] for b in born.values() if b["enemy_key"] not in supported), default=horizon+1)
    drone_spawn = min((b["frame"] for b in born.values() if b["enemy_key"] == DRONE), default=horizon+1)
    receivers = {k:dict(enemy=b["enemy_key"],spawn_frame=b["frame"],route=b["route_index"]) for k,b in born.items()}
    axe_births = {key:b["frame"] for key,b in born.items() if b["enemy_key"] == AXE}
    bomber = bomber_frontier(tables, scene, born, history, placements, removed_by, horizon, AXE,
                             motion.get("first_born_tick_delay_frames"),operator_skins=operator_skins,
                             motion=motion,radii=radii,pulses=pulses,devices=devices,boards=boards,motion_stuns=motion_stuns,
                             coroutine_deaths=coroutine_deaths)
    triggers = {}
    bomber_contacts = {}
    if bomber:
        triggers = bomber.pop("periodic_triggers",{})
        bomber_contacts = bomber.pop("slow_contacts",{})
        unsupported_spawn = min(unsupported_spawn, bomber["through_frame"]+1)
    if axe_births:
        mode = tables._component_fields("enemies", AXE, ("_attack", "_attackTrigger"), "vent_axe_mode",
                                        children=("Modes","Default"))
        mark = tables._component_fields("enemies", AXE, ("_buffs",), "vent_axe_immune_marker",
                                        children=("CommonAbilities","Ability0"))
        steal = tables._component_fields("enemies", AXE, ("_buffs",), "vent_axe_steal",
                                         children=("Talents","steal_energy"))
        if (mode["_attack"]["m_PathID"] or mode["_attackTrigger"]["m_PathID"]
                or {b["buffKey"] for b in mark["_buffs"]} != {"erob_immune_steal", "blacksteel_energy[mark]"}
                or [b["templateKey"] for b in steal["_buffs"]] != ["enemy_erob_steal_energy_ability"]):
            unsupported_spawn = min(unsupported_spawn, min(axe_births.values()))
    bomber_births = {k:b["frame"] for k,b in born.items() if b["enemy_key"] == BOMBER}
    shot_frames = defaultdict(list)
    for shot in (bomber or {}).get("shots",()):
        for event in ("release","impact"):
            shot_frames[shot[event+"_frame"]].append((event,shot))
    frames = sorted(f for f in (set(pulses) | set(axe_births.values()) | set(bomber_births.values()) | set(triggers) | set(shot_frames))
                    if f < unsupported_spawn)
    movement, energy, overloads, effects, first_samples = {}, {}, {}, [], []
    overload_history = defaultdict(list)
    cleared_overloads = []
    stuns = defaultdict(dict)
    for tick, rows in history.items():
        for kind, item in rows:
            if kind == "enemy_stun_hold":
                stuns[item["instance_id"]][tick] = item["resume_move_frame"]
    requested = {(s["instance_id"],s["frame"]):s for s in motion_stuns}
    applied_stuns = list((bomber or {}).get("applied_stuns",()))
    stun_resumes = []
    for (instance,tick),s in requested.items():
        stuns[instance][tick] = tick+s["duration_ticks"]
    through = positions_through = None
    pending = None
    for idx, frame in enumerate(frames):
        following = frames[idx+1] if idx+1 < len(frames) else min(horizon+1, unsupported_spawn)
        if pending:
            break
        active = {}
        positions = _position_samples(history, {frame,frame-1} if frame in triggers else {frame}, active)
        snapshot = positions[frame]
        aura = resistance_aura(tables, scene, receivers, history) if frame >= drone_spawn else None
        ended = {row["instance_id"] for f, rows in history.items() if f <= frame
                 for kind, row in rows if kind == "route_end_in_range"}
        updates, planned = [], {}
        amounts = dict(energy)
        for key,start in bomber_births.items():
            if frame == start:
                amounts[key] = bomber["bomber_initial"]
        active_overloads = dict(overloads)
        cleared = []
        for key, over in list(active_overloads.items()):
            if frame > over["energy_cleared_by"]:
                if removed_by.get(key, frame) >= frame and key not in ended:
                    cleared.append(dict(instance_id=key, trigger_frame=over["trigger"],
                                        clear_frame_bounds=[over["expiry_object_frames"][0], over["energy_cleared_by"]],
                                        observed_by_frame=frame, energy_after=0))
                amounts[key] = 0
                del active_overloads[key]
        movement = {key:leg for key, leg in movement.items() if removed_by.get(key, frame) >= frame}
        axes = {k for k,start in axe_births.items() if start <= frame <= removed_by.get(k,frame)}
        donors = [k for k,value in amounts.items() if value > 0 and k not in axe_births
                  and removed_by.get(k,frame) >= frame and k not in ended
                  and not (k in active_overloads and active_overloads[k]["buff_frame"] < frame <= active_overloads[k]["through"])]
        if axes and donors and not triggers:
            pending = dict(frame=frame, reason="axe_energy_transfer_requires_live_donors", instances=donors)
            break
        periodic, pending = apply_energy_triggers(frame,triggers.get(frame, ()),bomber,amounts,positions,
                                                  active,axe_births,active_overloads,removed_by,ended)
        for event in periodic:
            if event.get("reached_overload"):
                actor = event["source"]
                stats = {r["key"]:r["value"] for r in scene.enemies[AXE]["talentBlackboard"]}
                trigger = _overload(tables, AXE, stats, frame, step, object_trigger=True)
                if trigger is None:
                    pending = dict(frame=frame, reason="unreviewed_axe_overload_ability", instance_id=actor)
                    break
                active_overloads[actor] = trigger
        if pending:
            break
        for event,shot in shot_frames[frame]:
            count = amounts[shot["source"]]
            shot["energy_at_"+event] = count
            if event=="impact":
                shot["scaled_attack_q32"] = shell_attack(tables,scene,BOMBER,count)
        for alias in pulses[frame]:
            board = boards[alias]
            for key, birth in born.items():
                if (birth["frame"] > frame or key in ended or removed_by.get(key, frame) < frame
                        or scene.level["routes"][birth["route_index"]]["motionMode"] != "WALK"):
                    continue
                if key not in snapshot or key not in radii:
                    pending = dict(frame=frame, reason="vent_enemy_occupancy_unknown", instance_id=key)
                    break
                pos = snapshot[key]
                if pos is None or not circle_in_range(*pos, radii[key], devices[alias]["cells"], box_query=True):
                    continue
                enemy = scene.enemies[birth["enemy_key"]]
                stacks = {r["key"]:r["value"] for r in enemy["talentBlackboard"] or []}
                before = amounts.get(key, stacks.get("blacksteel_energy.init_stack_cnt", 0))
                over = active_overloads.get(key)
                if over and frame > over["through"]:
                    pending = dict(frame=frame, instance_id=key, reason="pulse_at_overload_expiry")
                    break
                cap = stacks.get("blacksteel_energy.max_stack_cnt", 0)
                if any(v != int(v) for v in (before,cap,board["energy"])):
                    pending = dict(frame=frame, reason="nonintegral_blacksteel_energy", instance_id=key)
                    break
                after = int(before if over else min(cap, before + board["energy"]))
                if key not in active[frame] or not cap or not enemy["attributes"]["magicResistance"]["m_defined"]:
                    pending = dict(frame=frame, reason="vent_contact_requires_overload_or_target_state", instance_id=key)
                    break
                if after >= cap and not over:
                    if key in bomber_births:
                        pending = dict(frame=frame, reason="bombardier_overload_changes_attack_speed", instance_id=key)
                        break
                    trigger = _overload(tables, birth["enemy_key"], stacks, frame, step)
                    if trigger is None:
                        pending = dict(frame=frame, reason="overload_ability_unreviewed", instance_id=key)
                        break
                    active_overloads[key] = trigger
                leg = planned.get(key) or movement.get(key)
                if key in bomber_births:
                    leg = bomber_contacts.get((frame,alias,key))
                    if leg is None:
                        pending = dict(frame=frame,reason="vent_bombardier_position_unresolved",instance_id=key)
                        break
                elif leg is None or (leg["state"] == "ordinary" and not leg.get("wait") and not leg.get("held_until")):
                    hold = next(((begin,end) for begin,end in stuns.get(key,{}).items() if begin <= frame < end), None)
                    leg = _slowed_leg(tables, scene, motion, history, birth, board, frame, hold)
                    if leg is None:
                        pending = dict(frame=frame, reason="vent_target_movement_command_unresolved", instance_id=key)
                        break
                    planned[key] = leg
                elif board["move_speed"] != leg.get("modifier", board["move_speed"]):
                    pending = dict(frame=frame, reason="different_vent_speed_modifiers", instance_id=key)
                    break
                amounts[key] = after
                resistance = enemy["attributes"]["magicResistance"]["m_value"]
                bonus_low = bonus_high = 0
                if over and frame >= over["earliest_buff_frame"]:
                    bonus_high = over["resistance_bonus"]
                    if frame > over["buff_frame"]:
                        bonus_low = bonus_high
                low = high = 0
                guaranteeing = []
                if aura:
                    low, high = aura.bonus_bounds(key, frame, finished_by=removed_by, source_alive=lambda source, tick:False,
                                                  phase="COROUTINES")
                    if low != high:
                        guaranteeing = sorted(s for s in aura.sources if aura.bonus_bounds(key,frame,finished_by=removed_by,
                            source_alive=lambda source,tick:source==s,phase="COROUTINES")[0] == high)
                max_hp = int(enemy["attributes"]["maxHp"]["m_value"] * ONE)
                low, high = low + bonus_low, high + bonus_high
                attacks = [ordinary_arts_hit(attack_raw=int(board["value"] * ONE),
                           resistance_raw=int((resistance+bonus)*ONE), hp_raw=max_hp) for bonus in (high,low)]
                known = low == high
                updates.append(dict(frame=frame, alias=alias, instance_id=key, position=list(pos),
                                    resistance=resistance+low if known else None, resistance_bounds=[resistance+low,resistance+high],
                                    guaranteeing_aura_sources=guaranteeing,
                                    damage_q32=attacks[0].damage_raw if known else None,
                                    damage_bounds_q32=[h.damage_raw for h in attacks],
                                    hp_if_previously_full_q32=attacks[0].hp_after_raw if known else None, full_hp_q32=max_hp,
                                    energy_before=before, energy_after=after, overload=(True if bonus_low else
                                        None if bonus_high else False),
                                    slowed_attribute=leg["slowed_attribute"], level_move_multiplier=leg["level_move_multiplier"],
                                    move_speed=leg["move_speed"]))
            if pending:
                break
        if pending:
            break
        energy = amounts
        cleared_overloads.extend(cleared)
        for key, over in active_overloads.items():
            if over["trigger"] == frame:
                overload_history[key].append(over)
        overloads = active_overloads
        if periodic:
            bomber["energy_events"].extend(periodic)
        if not updates and not movement:
            if effects:
                through, positions_through = min(horizon, following-1), min(horizon, following)
            continue
        # The old timeline after the first slowing hit contains a different position and arrival clock.
        for key in planned:
            for f, rows in history.items():
                if f > frame:
                    rows[:] = [(kind, row) for kind, row in rows if row["instance_id"] != key]
        movement.update(planned)
        for rec in updates:
            if rec["instance_id"] in bomber_births:
                contact = bomber_contacts[frame,rec["alias"],rec["instance_id"]]
                rec["slowed_movement_through_if_no_refresh"] = contact["slow_through"]
                continue
            leg = movement[rec["instance_id"]]
            board = boards[rec["alias"]]
            leg["modifier"], leg["alias"] = board["move_speed"], rec["alias"]
            leg["state"] = "slowed"
            # EXTEND refreshes the existing duration. Account for exact Q32 seconds per OBJECTS update.
            life = int(board["buff_time"] * ONE)
            leg["slow_through"] = max(leg.get("slow_through", frame), frame + (life+step-1) // step)
            rec["slowed_movement_through_if_no_refresh"] = leg["slow_through"]
        effects.extend(updates)
        end = min(horizon, following)
        if axes and not triggers and any(rec["energy_after"] and rec["instance_id"] not in axe_births and not rec["overload"]
                        for rec in updates):
            following = min(following, frame+1)
            end = min(end,frame)
            if horizon > frame:
                pending = dict(frame=frame+1, reason="axe_first_transfer_from_newly_energised_target",
                               instances=[rec["instance_id"] for rec in updates if rec["instance_id"] not in axe_births])
        for time in range(frame+1, end+1):
            records = []
            for key, leg in movement.items():
                if leg.get("finished") or removed_by.get(key,time) < time:
                    continue
                restarting = leg.get("held_until") == time
                rows, reason = _walk_tick(scene, leg, time, dt, hold_until=stuns.get(key,{}).get(time))
                if reason:
                    pending = dict(frame=time, reason=reason, instance_id=key)
                    end = time - 1
                    break
                records.extend(rows)
                if (key,time) in requested and any(k == "enemy_stun_hold" for k,_ in rows):
                    applied_stuns.append(requested[key,time])
                if restarting and rows and "velocity" in rows[0][1]:
                    sample = rows[0][1]
                    stun_resumes.append(dict(instance_id=key,frame=time,row=sample["row"],col=sample["col"],
                                             velocity=sample["velocity"],slowed=leg["state"]=="slowed"))
            if pending:
                break
            history.setdefault(time, []).extend(records)
            for _, row in records:
                if ("speed" in row and row["instance_id"] == effects[0]["instance_id"]
                        and time <= effects[0]["slowed_movement_through_if_no_refresh"]):
                    first_samples.append(dict(frame=time, row=row["row"], col=row["col"], speed=row["speed"]))
        through = min(end, following-1)
        positions_through = end
    if not effects:
        return None
    if not pending and unsupported_spawn <= horizon:
        pending = dict(frame=unsupported_spawn, reason="unreviewed_enemy_can_modify_energy_resistance_or_device_timing")
        if bomber and bomber["pending"] and bomber["pending"]["frame"] == unsupported_spawn:
            pending = bomber["pending"]
    if pending:
        if bomber and bomber["through_frame"] >= pending["frame"]:
            bomber.update(through_frame=pending["frame"]-1, pending=pending)
        for key, leg in movement.items():
            if not leg.get("finished") and removed_by.get(key,positions_through+1) > positions_through:
                history.setdefault(positions_through+1, []).append(("range_motion_unresolved",dict(instance_id=key)))
    for shot in (bomber or {}).get("shots", ()):
        if shot["release_frame"]>through:
            shot["release_frame"] = None
        if shot["impact_frame"]>through:
            shot["impact_frame"] = None
    first = effects[0]
    return dict(first, status="vent_pulses_with_continued_movement", through_frame=through,
                base_resistance_through_frame=min([through, unsupported_spawn-1]+[rows[0]["trigger"] for rows in overload_history.values()]),
                overloads={k:rows[0] for k,rows in overload_history.items()}, overload_history=dict(overload_history),
                cleared_overloads=cleared_overloads,
                reviewed_energy_enemies=[*MELEE_ONLY, AXE], bombardier=bomber,
                applied_stuns=applied_stuns, movement_after_stun=stun_resumes,
                position_through_frame=positions_through, next_pulse_frame=next((f for f in frames if f>first["frame"] and f in pulses and pulses[f]),None),
                samples=first_samples, effects=effects, pending=pending,
                conditions=["ordinary_living_targets_on_each_pulse", "ordinary_initial_energy_and_no_external_energy",
                            "no_other_speed_modifiers_or_blocking", "confirmed_unobstructed_move_until_next_command"])

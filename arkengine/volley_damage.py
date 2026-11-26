"""Conditional HP intervals for the ordinary packets and Appoggiatura in one volley."""
from collections import defaultdict
from itertools import permutations
from math import sqrt
import struct

from .attack_range import RangeContacts
from .baked_mount import single
from .damage import HP_EPSILON, ordinary_arts_hit
from .enemy_resistance import overload_resistance_bounds
from .fixed import ONE, from_float32_bits, mul
from .mystic_damage import ordinary_damage_inputs


def _raw_f32(value):
    return from_float32_bits(int.from_bytes(struct.pack("<f", value), "little"))


def _position_samples(history, frames, active_samples=None, hidden_samples=None):
    contacts = RangeContacts()
    samples = {}
    active = set()
    hidden = {}
    for frame in range(max(frames, default=-1) + 1):
        for kind, fields in history.get(frame, ()):
            contacts.update(frame, kind, fields)
            instance = fields["instance_id"]
            if kind == "checkpoint_begin" and fields["disappeared"]:
                pos = fields["position"]
                hidden[instance] = (pos["row"], pos["col"])
            elif kind in ("checkpoint_begin", "free_motion_sample", "exit_motion_sample", "post_wait_motion_sample",
                         "range_motion_unresolved", "route_end_in_range"):
                hidden.pop(instance, None)
            if kind == "route_end_in_range":
                contacts.positions.pop(instance, None)
            if kind in ("route_end_in_range", "range_motion_unresolved") or (
                    kind == "checkpoint_begin" and (fields["disappeared"] or fields.get("command") == "APPEAR_AT_POS")):
                active.discard(instance)
            elif kind in ("free_motion_sample", "exit_motion_sample", "post_wait_motion_sample") and fields.get("active_ticks", 0) > 0:
                active.add(instance)
        if frame in frames:
            samples[frame] = contacts.positions.copy()
            if active_samples is not None:
                active_samples[frame] = active.copy()
            if hidden_samples is not None:
                hidden_samples[frame] = hidden.copy()
    return samples


def _talent_distances(origin, other, radius):
    # V079 MathUtil.CheckDistance(Vector2, Vector2, FP) squares binary32
    # differences, converts their binary32 sum to FP, then uses inclusive LE.
    dy = single(single(origin[0]) - single(other[0]))
    dx = single(single(origin[1]) - single(other[1]))
    squared = single(single(dx*dx) + single(dy*dy))
    fp_radius = _raw_f32(radius)
    in_check = _raw_f32(squared) <= mul(fp_radius, fp_radius)
    # The AoE uses a separate LitePhysicsUtil query: sqrtf, then float LE.
    distance = single(sqrt(squared))
    in_splash = distance <= single(single(radius) + single(1e-5))
    return distance, in_check, in_splash


def _talent_at_hit(inputs, target, frame, lag, spawned, samples, dead_by=None, hidden_samples=None):
    origin = samples[frame-lag].get(target)
    if origin is None:
        return dict(status="receiver_position_unknown", original_target_extra_scale=0)
    nearby = []
    uncertain = []
    splash_receivers = []
    possible_splash = []
    disappeared_neighbors = []
    closest = None
    for instance, unit in spawned.items():
        death = (dead_by or {}).get(instance)
        if instance == target or unit["spawn_frame"] > frame or (death is not None and death < frame):
            continue
        # Actor ordering against other enemies can differ from the receiver's.
        # Only apply an isolated hit when both positions for every other enemy
        # are outside. Unknown routes, portals and same-tick births stay possible.
        proximity = set()
        splash = set()
        disappeared_samples = 0
        for tick in (frame - 1, frame):
            if unit["spawn_frame"] > tick:
                proximity.add(False)
                splash.add(False)
            else:
                position = samples[tick].get(instance)
                distance, inside, receives = ((None, None, None) if position is None else
                                              _talent_distances(origin, position, inputs["talent_radius"]))
                held = (hidden_samples or {}).get(tick, {}).get(instance)
                if held is not None:
                    # A disappeared living enemy still counts for CheckHasEnemyInRange.
                    # The separate AOEDamage selector rejects it via Entity.isTargetFree.
                    distance, inside, _ = _talent_distances(origin, held, inputs["talent_radius"])
                    receives = False
                    disappeared_samples += 1
                if distance is not None:
                    candidate = distance, instance, tick
                    if closest is None or candidate < closest:
                        closest = candidate
                proximity.add(inside)
                splash.add(receives)
        if proximity == {True}:
            nearby.append(instance)
            if disappeared_samples == 2:
                disappeared_neighbors.append(instance)
        elif proximity != {False}:
            uncertain.append(instance)
        if splash == {True}:
            splash_receivers.append(instance)
        elif splash != {False}:
            possible_splash.append(instance)
    if nearby:
        status = "neighbors_in_radius_if_alive"
    elif uncertain:
        status = "neighbor_presence_unresolved"
    else:
        status = "isolated"
    return dict(status=status, center_row_col=list(origin), nearby=nearby, uncertain=uncertain,
                disappeared_neighbors=disappeared_neighbors,
                closest_other_sample=(dict(instance_id=closest[1], sample_frame=closest[2],
                                           distance_tiles=closest[0]) if closest else None),
                original_target_extra_scale=inputs["isolated_scale"] if status == "isolated" else 0,
                splash_receivers_if_alive_and_targetable=splash_receivers if nearby or uncertain else [],
                splash_receivers_with_uncertain_position=possible_splash if nearby or uncertain else [],
                other_targets_splash_scale_if_eligible=inputs["splash_scale"] if nearby or uncertain else 0)


def _scales(inputs):
    return {inputs[kind+"_projectile"]: inputs[kind+"_scale"] for kind in ("main", "stored", "extra")
            if kind+"_projectile" in inputs}


def _hp_timeline(inputs, shots, lag, resistance, last_ordinary_frame, talent=None, *, initial_hp=None,
                 source_at_impact=None, aura_bonuses=None):
    scales = _scales(inputs)
    by_frame = defaultdict(list)
    pending = []
    for shot in shots:
        sample, = (a for a in shot["arrivals"] if a["receiver_sample_lag"] == lag)
        if sample["stop"] != "ordinary_reach":
            pending.append(dict(key=shot["key"], reason=sample["stop"]))
        elif sample["frame"] > last_ordinary_frame:
            pending.append(dict(key=shot["key"], frame=sample["frame"], reason="skill_changed_before_hit"))
        else:
            by_frame[sample["frame"]].append(shot["key"])
    hp = inputs["base_hp"] * ONE if initial_hp is None else initial_hp
    rows = []
    lethal = None
    for frame, keys in sorted(by_frame.items()):
        before = hp
        if hp <= HP_EPSILON:
            lethal = frame
            break
        attachment = (talent or {}).get(frame)
        source = (source_at_impact or {}).get(frame, inputs)
        attack = source.get("attack_q32", source["attack"] * ONE)
        bonus = (aura_bonuses or {}).get(frame, (0, 0))
        res_for_upper = _raw_f32(resistance + bonus[1])
        extra_scale = attachment["original_target_extra_scale"] if attachment else 0
        # Only the main packet can differ from the stored packets. At the
        # exact living threshold a later packet need not apply.
        outcomes = []
        for order in set(permutations(keys)):
            remaining = before
            for key in order:
                packet_scales = (scales[key], extra_scale) if extra_scale else (scales[key],)
                if extra_scale:
                    # Either positive damage may end the hit exactly at the
                    # HP epsilon, even when reversing the pair would reach zero.
                    extra_first = ordinary_arts_hit(attack_raw=mul(attack, _raw_f32(extra_scale)),
                                                    resistance_raw=res_for_upper, hp_raw=remaining)
                    if not extra_first.alive_after:
                        outcomes.append((extra_first.hp_after_raw, False))
                for scale in packet_scales:
                    hit = ordinary_arts_hit(attack_raw=mul(attack, _raw_f32(scale)),
                                            resistance_raw=res_for_upper, hp_raw=remaining)
                    remaining = hit.hp_after_raw
                    if not hit.alive_after:
                        break
                if not hit.alive_after:
                    break
            outcomes.append((remaining, hit.alive_after))
        hp, alive = max(outcomes)
        if not alive:
            lethal = frame
        rows.append(dict(frame=frame, arrivals=keys, hp_upper_before_q32=before,
                         hp_upper_after_q32=hp, hp_upper_after=hp / ONE))
        if bonus[1]:
            rows[-1]["effective_resistance_interval"] = [resistance + b for b in bonus]
        if "mode" in source:
            rows[-1].update(source_attack_mode=source["mode"], source_attack_q32=attack)
        if attachment:
            rows[-1]["hit_talent"] = attachment
        if lethal is not None:
            break
    return dict(resistance=resistance, frames=rows, not_alive_by_frame=lethal, pending=pending)


def _initial_interval(profile, instance, resistance, bounds, floors):
    maximum = profile["base_hp"] * ONE
    upper = (bounds or {}).get(instance, {}).get(resistance, maximum)
    fresh = bounds is None and floors is None
    lower = (floors or {}).get(instance, {}).get(resistance, maximum if fresh else 0)
    if not 0 <= lower <= upper <= maximum:
        raise ValueError(f"敌人 {instance} 在法抗 {resistance} 的初始生命区间无效")
    return lower, upper


def _subtract(hp, damage):
    if hp <= HP_EPSILON:
        return hp
    remaining = hp - damage
    return remaining if remaining >= HP_EPSILON else 0


def _packet(attack, scale, resistance):
    return ordinary_arts_hit(attack_raw=mul(attack, _raw_f32(scale)),
                             resistance_raw=_raw_f32(resistance), hp_raw=ONE).damage_raw


def _resistances(inputs):
    base = inputs["base_res"]
    if inputs.get("base_resistance_only"):
        return [base]
    return sorted({base, base + inputs["overloaded_resistance_bonus"]})


def _resolve_y_attachments(case, target, inputs, spawned, active, profile_for, bounds, floors, bonus_at):
    """Use per-packet living state when all nearby receivers have closed HP and geometry."""
    resistance = case["resistance"]
    primary, upper = _initial_interval(inputs, target, resistance, bounds, floors)
    if primary != upper:
        return False
    scales = _scales(inputs)
    order = {inputs[kind+"_projectile"]: rank for rank, kind in enumerate(("stored", "extra", "main"))
             if kind+"_projectile" in inputs}
    neighbors = {}
    rows, updates = [], []
    for row in case["frames"]:
        if primary <= HP_EPSILON:
            break
        frame = row["frame"]
        talent = row.get("hit_talent", {})
        if (not talent or talent["status"] == "receiver_position_unknown" or talent["uncertain"] or
                talent["splash_receivers_with_uncertain_position"]):
            return False
        res_low, res_high = row.get("effective_resistance_interval", (resistance, resistance))
        if res_low != res_high:
            return False
        nearby = talent["nearby"]
        splash_targets = talent["splash_receivers_if_alive_and_targetable"]
        effective = {}
        disappeared = talent.get("disappeared_neighbors", ())
        for instance in dict.fromkeys((*nearby, *splash_targets)):
            receiver = profile_for(spawned[instance]["enemy"])
            if (receiver["status"] == "unresolved" or not receiver["ordinary_splash_receiver"] or
                    (instance not in disappeared and not all(instance in active[t] for t in (frame-1, frame)))):
                return False
            low, high = bonus_at(instance, frame) if bonus_at else (0, 0)
            if low != high:
                return False
            if instance not in neighbors:
                neighbors[instance] = {}
                for res in _resistances(receiver):
                    hp, upper = _initial_interval(receiver, instance, res, bounds, floors)
                    if hp != upper:
                        return False
                    neighbors[instance][res] = hp
            effective[instance] = low
        before, attack = primary, row["source_attack_q32"]
        packets = []
        splash_counts = defaultdict(int)
        # The projectile's attachment runs before its own ordinary damage. Each
        # successive projectile performs the original-target and neighbor alive checks.
        for key in sorted(row["arrivals"], key=order.__getitem__):
            if primary <= HP_EPSILON:
                break
            living = set()
            for instance in effective:
                possibilities = {hp > HP_EPSILON for hp in neighbors[instance].values()}
                if len(possibilities) != 1:
                    return False
                if possibilities == {True}:
                    living.add(instance)
            receivers = [instance for instance in splash_targets if instance in living]
            isolated = not living.intersection(nearby)
            if isolated:
                primary = _subtract(primary, _packet(attack, inputs["isolated_scale"], res_low))
            else:
                for instance in receivers:
                    for res, hp in neighbors[instance].items():
                        neighbors[instance][res] = _subtract(hp, _packet(attack, inputs["splash_scale"],
                                                                       res + effective[instance]))
                    splash_counts[instance] += 1
            primary = _subtract(primary, _packet(attack, scales[key], res_low))
            packets.append(dict(key=key, isolated=isolated, splash_receivers=[] if isolated else receivers))
        rows.append(dict(row, hp_lower_before_q32=before, hp_upper_before_q32=before,
                         hp_lower_after_q32=primary, hp_upper_after_q32=primary,
                         hp_upper_after=primary / ONE, attachment_packets=packets))
        updates.append(dict(kind="primary", instance=target, resistance=resistance, frame=frame,
                            hp_upper_after_q32=primary, hp_lower_after_q32=primary,
                            coverage="all_feasible_within_case"))
        for instance, count in splash_counts.items():
            for res, hp in neighbors[instance].items():
                updates.append(dict(kind="splash", instance=instance, resistance=res, frame=frame,
                                    hp_upper_after_q32=hp, hp_lower_after_q32=hp, guaranteed_packets=count,
                                    coverage="all_feasible_within_case"))
                if effective[instance]:
                    updates[-1]["effective_resistance_interval"] = [res + effective[instance]] * 2
    lower_updates = [{key: update[key] for key in ("instance", "resistance", "frame", "hp_lower_after_q32")}
                     for update in updates]
    case.update(frames=rows, receiver_updates=updates, receiver_lower_updates=lower_updates,
                receiver_lower_coverage_complete=True,
                not_alive_by_frame=rows[-1]["frame"] if rows and primary <= HP_EPSILON else None)
    return True


def _receiver_intervals(case, target, inputs, spawned, active, profile_for, bounds, floors, geometry, bonus_at=None):
    resistance = case["resistance"]
    primary_lower, primary_upper = _initial_interval(inputs, target, resistance, bounds, floors)
    known_talent = inputs["hit_talent"]["status"]
    complete = (not case["pending"] and
                (known_talent == "no_hit_talent" or (known_talent in ("y3_projectile_hit_attachment", "isolated_projectile_hit_attachment") and geometry)))
    if (complete and known_talent == "y3_projectile_hit_attachment" and
            _resolve_y_attachments(case, target, inputs, spawned, active, profile_for, bounds, floors, bonus_at)):
        return
    scales = _scales(inputs)
    secondary_hp = {}
    updates, lower_updates = [], []
    for row in case["frames"]:
        frame, keys = row["frame"], row["arrivals"]
        attachment = row.get("hit_talent", {})
        before = primary_lower
        attack = row["source_attack_q32"]
        res_for_lower = row.get("effective_resistance_interval", [resistance])[0]
        extra_upper = _packet(attack, inputs["isolated_scale"], res_for_lower)
        for instance in attachment.get("nearby", ()):
            receiver = profile_for(spawned[instance]["enemy"])
            if receiver["status"] == "unresolved" or (instance not in attachment.get("disappeared_neighbors", ()) and
                    not all(instance in active[t] for t in (frame - 1, frame))):
                continue
            # On this closed HP history, even taking one splash per arrival
            # cannot remove this neighbor before the last count check.
            if all(secondary_hp.get((instance, r), _initial_interval(receiver, instance, r, bounds, floors))[0]
                   - len(keys) * _packet(attack, inputs["splash_scale"], r) > HP_EPSILON
                   for r in _resistances(receiver)):
                extra_upper = 0
                break
        # A living primary survives this many ordinary packets in every order.
        # Charge and main packets may share a tick. Count only attachments whose
        # owner survives even when the largest ordinary packets are applied first.
        guaranteed_packets = 0
        for damage in sorted((_packet(attack, scales[key], res_for_lower) for key in keys), reverse=True):
            primary_lower = _subtract(primary_lower, damage)
            if primary_lower > HP_EPSILON:
                guaranteed_packets += 1
            primary_lower = _subtract(primary_lower, extra_upper)
        if primary_lower <= HP_EPSILON:
            primary_lower = 0
        if not complete:
            primary_lower = 0
            guaranteed_packets = 0
        row["hp_lower_before_q32"] = before
        row["hp_lower_after_q32"] = primary_lower
        if primary_upper != before and row["hp_upper_after_q32"] < HP_EPSILON:
            # A previously bounded receiver may already have stopped at exactly
            # epsilon. Subsequent packets need not clear that dead remnant.
            row["hp_upper_after_q32"] = HP_EPSILON
            row["hp_upper_after"] = HP_EPSILON / ONE
        primary_upper = row["hp_upper_after_q32"]
        updates.append(dict(kind="primary", instance=target, resistance=resistance, frame=frame,
                            hp_upper_after_q32=primary_upper, hp_lower_after_q32=primary_lower,
                            coverage="all_feasible_within_case"))
        lower_updates.append(dict(instance=target, resistance=resistance, frame=frame,
                                  hp_lower_after_q32=primary_lower))
        if not attachment or attachment["status"] == "isolated":
            continue
        guaranteed = set(attachment.get("nearby", ())) & set(attachment.get("splash_receivers_if_alive_and_targetable", ()))
        possible = guaranteed | set(attachment.get("splash_receivers_if_alive_and_targetable", ()))
        possible.update(attachment.get("splash_receivers_with_uncertain_position", ()))
        if attachment["status"] == "receiver_position_unknown":
            possible = {i for i, birth in spawned.items() if i != target and birth["spawn_frame"] <= frame}
        for instance in sorted(possible):
            receiver = profile_for(spawned[instance]["enemy"])
            if receiver["status"] == "unresolved":
                complete = False
                continue
            # The reviewed walker is selectable in its normal on-map state.
            # A birth-frame or unknown/potentially hidden actor never provides a
            # guaranteed damage update. An already-dead receiver satisfies the
            # same upper bound by the no-revival condition.
            eligible = (instance in guaranteed and receiver["ordinary_splash_receiver"] and
                        all(instance in active[tick] for tick in (frame - 1, frame)))
            bonus = bonus_at(instance,frame) if bonus_at else (0,0)
            for res in _resistances(receiver):
                key = instance, res
                lower, upper = secondary_hp.get(key, _initial_interval(receiver, instance, res, bounds, floors))
                most_damage = _packet(attack, inputs["splash_scale"], res+bonus[0])
                least_damage = _packet(attack, inputs["splash_scale"], res+bonus[1])
                # Even uncertain or subsequently dead targets cannot take more
                # than one splash from each projected ordinary arrival.
                for _ in keys:
                    lower = _subtract(lower, most_damage)
                if lower <= HP_EPSILON:
                    lower = 0
                if eligible and guaranteed_packets:
                    before_upper = upper
                    for _ in range(guaranteed_packets):
                        upper = _subtract(upper, least_damage)
                    if before_upper > HP_EPSILON and upper < HP_EPSILON:
                        upper = HP_EPSILON
                    updates.append(dict(kind="splash", instance=instance, resistance=res, frame=frame,
                                        hp_upper_after_q32=upper, hp_lower_after_q32=lower,
                                        guaranteed_packets=guaranteed_packets, coverage="all_feasible_within_case"))
                    if bonus[1]:
                        updates[-1]["effective_resistance_interval"] = [res+b for b in bonus]
                lower_updates.append(dict(instance=instance, resistance=res, frame=frame, hp_lower_after_q32=lower))
                secondary_hp[key] = lower, upper
    case.update(receiver_updates=updates, receiver_lower_updates=lower_updates,
                receiver_lower_coverage_complete=bool(complete))


def first_volley_damage(tables, scene, card, spawned, projectiles, last_ordinary_frame, *, history=None, dead_by=None,
                        finished_by=None,
                        receiver_hp_bounds=None, receiver_hp_floors=None, source_mode_by_impact_frame=None,
                        base_resistance_through=None, aura=None, energy_timeline=None):
    """Mode-change keys are the first projectile OBJECTS tick observing the new source ATK."""
    removed_by = dict(finished_by or {})
    for key, frame in (dead_by or {}).items():
        removed_by[key] = min(frame, removed_by.get(key, frame))
    if projectiles["status"] != "conditional_projectile_reach":
        return dict(status=projectiles["status"], reason=projectiles.get("reason", projectiles["status"]))
    profiles = {}
    results = []
    frames = {a["frame"] for branch in projectiles["scenarios"] for shot in branch["shots"]
              for a in shot["arrivals"] if a["stop"] == "ordinary_reach" and a["frame"] <= last_ordinary_frame}
    known_base = {}
    dynamic_energy = energy_timeline is not None and "overloads" in energy_timeline
    energy_enemies = (energy_timeline.get("reviewed_energy_enemies", ()) if dynamic_energy else
                      ("enemy_1353_esabr_2", "enemy_1354_eghost_2"))
    if dynamic_energy or (base_resistance_through is not None and frames and max(frames) <= base_resistance_through):
        for instance, birth in spawned.items():
            enemy = scene.enemies[birth["enemy"]]
            if birth["enemy"] in energy_enemies:
                known_base[instance] = enemy["attributes"]["magicResistance"]["m_value"]
    active = {}
    hidden = {}
    samples = (_position_samples(history, frames | {f-1 for f in frames}, active, hidden) if history is not None else None)
    for branch in projectiles["scenarios"]:
        mode = branch.get("mode", "Default")

        def profile_for(enemy, source_mode=mode):
            cache_key = enemy if source_mode == "Default" else f"{enemy}@{source_mode}"
            if cache_key not in profiles:
                profiles[cache_key] = ordinary_damage_inputs(tables, card, scene.enemies[enemy], mode=source_mode)
                if known_base and enemy in energy_enemies:
                    profiles[cache_key]["base_resistance_only"] = True
            return profiles[cache_key]

        target = branch["target"]
        enemy = spawned[target]["enemy"]
        inputs = profile_for(enemy)
        if inputs["status"] == "unresolved":
            return inputs
        if any(s["key"] not in _scales(inputs) for s in branch["shots"]):
            return dict(status="unresolved", reason="unreviewed_projectile_damage_packet")
        branch_frames = {a["frame"] for shot in branch["shots"] for a in shot["arrivals"]
                         if a["stop"] == "ordinary_reach" and a["frame"] <= last_ordinary_frame}
        changes = sorted((tick, value) for tick, value in (source_mode_by_impact_frame or {}).items()
                         if tick > branch["launch_frame"])
        source_at_impact = {}
        for frame in branch_frames:
            active_mode = next((value for tick, value in reversed(changes) if tick <= frame), mode)
            source = profile_for(enemy, active_mode)
            if source["status"] == "unresolved":
                return source
            if source["hit_talent"] != inputs["hit_talent"]:
                return dict(status="unresolved", reason="hit_attachment_changes_across_source_modes")
            source_at_impact[frame] = source
        aura_at = None
        bonus_at = None
        aura_bonuses = None
        if aura is not None and branch_frames:
            # A provider might die during this volley. Discount its full possible incoming load
            # before asserting that it protects another enemy, including simultaneous impacts.
            max_atk = max(s["attack_q32"] for s in source_at_impact.values())
            def source_alive(provider, frame):
                profile = profile_for(spawned[provider]["enemy"])
                if profile["status"] == "unresolved":
                    return False
                res = profile["base_res"]
                hp, _ = _initial_interval(profile,provider,res,receiver_hp_bounds,receiver_hp_floors)
                for shot in branch["shots"]:
                    if not any(a["stop"] == "ordinary_reach" and a["frame"] <= frame for a in shot["arrivals"]):
                        continue
                    if provider == target:
                        hp -= _packet(max_atk,_scales(inputs)[shot["key"]],res)
                        hp -= _packet(max_atk,inputs["isolated_scale"],res)
                    else:
                        hp -= _packet(max_atk,inputs["splash_scale"],res)
                return hp > HP_EPSILON
            def aura_at(instance, frame):
                return aura.bonus_bounds(instance,frame,dead_by=dead_by,finished_by=finished_by,source_alive=source_alive)
        if aura_at is not None or dynamic_energy:
            def bonus_at(instance, frame):
                aura_low, aura_high = aura_at(instance,frame) if aura_at else (0,0)
                if not dynamic_energy or instance not in known_base:
                    return aura_low, aura_high
                energy = overload_resistance_bounds(energy_timeline, instance, frame)
                if energy is None:
                    energy = (0, profile_for(spawned[instance]["enemy"])["overloaded_resistance_bonus"])
                return aura_low + energy[0], aura_high + energy[1]
            aura_bonuses = {f:bonus_at(target,f) for f in branch_frames}
        for lag in sorted({arrival["receiver_sample_lag"] for shot in branch["shots"] for arrival in shot["arrivals"]}):
            talent = None
            if samples is not None and inputs["hit_talent"]["status"] in ("y3_projectile_hit_attachment", "isolated_projectile_hit_attachment"):
                talent = {f: _talent_at_hit(inputs, target, f, lag, spawned, samples, removed_by, hidden) for f in branch_frames}
            cases = []
            for r in _resistances(inputs):
                _, upper = _initial_interval(inputs, target, r, receiver_hp_bounds, receiver_hp_floors)
                ordinary = _hp_timeline(inputs, branch["shots"], lag, r, last_ordinary_frame, initial_hp=upper,
                                        source_at_impact=source_at_impact, aura_bonuses=aura_bonuses)
                if talent:
                    case = _hp_timeline(inputs, branch["shots"], lag, r, last_ordinary_frame, talent, initial_hp=upper,
                                        source_at_impact=source_at_impact, aura_bonuses=aura_bonuses)
                    case["ordinary_only"] = dict(frames=ordinary["frames"], not_alive_by_frame=ordinary["not_alive_by_frame"])
                else:
                    case = ordinary
                _receiver_intervals(case, target, inputs, spawned, active, profile_for,
                                    receiver_hp_bounds, receiver_hp_floors, talent is not None, bonus_at)
                cases.append(case)
            results.append(dict(target=target, enemy=enemy, mode=mode, launch_frame=branch["launch_frame"],
                face=branch["face"], x_sign=branch["x_sign"], receiver_sample_lag=lag,
                hit_talent_geometry=("projected" if talent else
                    "missing_position_history" if samples is None else inputs["hit_talent"]["status"]),
                resistance_cases=cases))
    return dict(status="conditional_target_hp_upper_bounds", profiles=profiles, scenarios=results,
                **({"known_base_resistances": known_base} if known_base else {}),
                conditions=["ordinary_magic_resistance_formula_branch", "accepted_hits_at_projected_arrival",
                    "initial_full_hp_or_supplied_bounds_no_heal_shield_dodge_or_death_prevention",
                    "no_other_attacker_or_environment_hp_change_during_this_volley",
                    "normal_on_map_selectability_for_reviewed_splash_receivers",
                    "unchanged_negative_hp_modifier_and_base_resistance_during_volley",
                    *(["projected_aura_contact_with_no_silence_or_external_provider_damage"] if aura is not None else []),
                    "source_valid_with_reviewed_mode_at_projectile_impact",
                    "isolated_attachment_only_when_all_other_spawned_enemies_demonstrably_outside",
                    "splash_excludes_original_target_and_does_not_reduce_its_hp_bound"],
                scope="one_volley_primary_and_secondary_hp_bounds")

"""Replay the Mystic Caster's two charge counters across successful mode changes."""
from collections import defaultdict

from .damage import HP_EPSILON
from .blacksteel_threats import BOMBER
from .enemy_resistance import overload_resistance_bounds
from .fixed import ONE, mul
from .health_bounds import HealthBounds, _reviewed_regular_enemy, ordinary_enemy_finishes
from .lin_active import LinImpactClock, apply_joint_lin_attack
from .lin_interactions import AXE, DRONE, MELEE_ONLY
from .module_effects import equipped_phase, selected_branch
from .mystic_damage import delta_projectile_impact, delta_projectile_profile
from .mystic_projectiles import ordinary_projectiles_for_releases
from .mystic_storage import ENEMY_LEVEL_BITS, redistribute_charges, spend_on_target
from .skill_recovery import READY_EPSILON, RecoveryTimer, _divide
from .volley_damage import _position_samples, _raw_f32, _talent_at_hit, first_volley_damage


def impact_mode_changes(switch):
    """A projectile reads the source after a preceding change has completed."""
    ordered = [(b.get("effect_frame", b["input_frame"]), b.get("effect_phase", "input"), b["destination_mode"])
               for b in switch.get("buttons", ())]
    ordered += [(f, "unity_fixed_resume" if f in switch.get("natural_after_fixed", ()) else "coroutines", "Default")
                for f in switch.get("natural_returns_to_default", ())]
    phases = {"input": 0, "coroutines": 1, "unity_fixed_resume": 2, "after_tick": 3}
    return {f+1:mode for f, _, mode in sorted(ordered, key=lambda r: (r[0],phases[r[1]]))}


def _life_cases(damage):
    """A positive HP upper bound allows either outcome at the next target check."""
    groups = defaultdict(set)
    for branch in (damage or {}).get("scenarios", []):
        for case in branch["resistance_cases"]:
            key = (branch["target"], branch["launch_frame"], case["not_alive_by_frame"])
            groups[key].add(case["resistance"])
    return [(key, sorted(res)) for key, res in groups.items()] or [((None, None, None), [])]


class _AttackCooldown:
    """V079 PeriodicTimer on the active attack ability. Times are raw Q32.32 seconds."""
    def __init__(self, frame, period):
        self.frame = frame
        self.period = self.remaining = period

    def remaining_at(self, frame, step):
        return max(0, self.remaining - (frame - self.frame) * step)

    def change_period(self, frame, step, period):
        progress = ONE - _divide(self.remaining_at(frame, step), self.period)
        self.remaining = period - mul(max(0, min(ONE, progress)), period)
        self.frame = frame
        self.period = period


def _attack_frames(switch, changes, range_events, unknown, levels, bounds, on_release,
                   first_frame, first_cast, end, step, priority, device_hit=None, walking_target=None, lin_impacts=None,
                   projectile_tick=None, *, initial_storage=(0,0), storage_speed_state=None, initial_inside=(), live=None):
    normal,extra=initial_storage
    mode = "Default"
    deterministic_skill_start = None
    speed_buff = False
    trigger = switch["module_speed_trigger"]
    timer = (RecoveryTimer(int(trigger["trigger_interval_seconds"] * ONE))
             if trigger["status"] == "periodic_storage_speed" else None)
    if timer and storage_speed_state is not None:
        speed_buff,timer.remaining=storage_speed_state
    masks = switch["target_level_rules"]["modes"]
    limits = switch["charge_limits"]["modes"]
    inside = set(initial_inside)
    events = []
    cast = None
    cooldown = None
    stop = None
    frame = end
    device_hits = defaultdict(list)
    for hit in (device_hit.get("effects", [device_hit]) if device_hit else ()):
        device_hits[max(first_frame-1, hit["frame"])].append(hit)

    def emit(kind, **fields):
        events.append(dict(frame=frame, kind=kind, mode=mode, normal=normal, extra=extra, **fields))

    def finish_lin(tick):
        if lin_impacts:
            return lin_impacts.finish_frame(tick,bounds,device_hits.get(tick, ()))

    def update_range(tick):
        for event in range_events.get(tick, ()):
            if event["kind"] == "attack_range_enter":
                inside.add(event["instance_id"])
            else:
                inside.discard(event["instance_id"])

    finished_through = first_frame - 2
    def finish_tick(tick):
        nonlocal finished_through
        if tick <= finished_through:
            return None
        finished_through = tick
        if any(not bounds.apply_device_hit(hit) for hit in device_hits.get(tick, ())):
            return "device_hit_overlaps_outstanding_projectile"
        return finish_lin(tick)

    def start_cast(search_cutoff, start_speed, late):
        nonlocal cast, cooldown
        mask = masks[mode]
        candidates = []
        if live is not None:
            candidates = [key for key in live.candidates(frame, late)
                          if mask["main"] & ENEMY_LEVEL_BITS[levels[key]]]
            ranked = live.priority(candidates, frame, late) if len(candidates)>1 else None
        else:
            confirmed_dead = bounds.dead_by(search_cutoff)
            for target in sorted(inside | {key for key, since in unknown.items() if since < search_cutoff}):
                if target in confirmed_dead:
                    continue
                grade = levels.get(target)
                if grade not in ENEMY_LEVEL_BITS:
                    return "missing_enemy_level"
                if not mask["main"] & ENEMY_LEVEL_BITS[grade]:
                    continue
                if unknown.get(target, search_cutoff) < search_cutoff:
                    return "incomplete_target_positions"
                if bounds.floor_at(target, search_cutoff-1) <= HP_EPSILON:
                    return "target_life_unresolved_after_volley"
                candidates.append(target)
            ranked = priority(candidates, frame + int(late)) if len(candidates)>1 and priority else None
            if len(candidates) > 1 and not ranked:
                return "multiple_targets_require_priority"
        capacity = limits[mode]
        target = ranked["target"] if ranked else next(iter(candidates), None)
        if target is None and normal >= capacity["normal_limit"] and extra >= capacity["extra_limit"]:
            return None
        kind = "attack" if target else "charge"
        track = next((sample for sample in switch["action_samples"][mode][kind]
                      if sample["module_speed_bonus_active"] == start_speed), None)
        if track is None:
            return "missing_mode_action_track"
        # Playback starts synchronously. The late tick can latch the event;
        # the cast coroutine polls it on the next frame.
        first_sample = frame + int(late)
        animation_key = first_sample + track["samples_to_key"] - 1
        cooldown = _AttackCooldown(frame, track["period_q32"])
        if start_speed != speed_buff:
            # A buff can rescale the cooldown before its first tick without
            # changing the animation duration sampled at cast admission.
            current = next(sample for sample in switch["action_samples"][mode][kind]
                           if sample["module_speed_bonus_active"] == speed_buff)
            cooldown.change_period(frame, step, current["period_q32"])
        cast = dict(start=frame, end=frame + track["action_frames"], period=track["period_q32"],
                    key=animation_key + 1, target=target, animation_time=track["animation_seconds_at_key"])
        emit("cast_started", action=track["action"], target=target, attack_speed=track["attack_speed"],
             first_animation_sample=first_sample,
             baked_event_frame=animation_key, apply_frame=cast["key"], nominal_end=cast["end"],
             **({"target_priority": ranked} if ranked else {}))
        return None

    def phases():
        for tick in range(first_frame, end + 1):
            yield tick, "tick"
            for phase in ("unity_fixed_resume", "after_tick"):
                if (tick,phase) in changes:
                    yield tick, phase

    for frame, phase in phases():
        yield frame, events
        late = phase != "tick"
        completed = frame if late else frame - 1
        stop = finish_tick(completed)
        if stop:
            if stop != "device_hit_overlaps_outstanding_projectile":
                frame = completed
            break
        if not late and projectile_tick:
            stop = projectile_tick(frame)
            if stop:
                break
        if live is not None and not live.owner_alive:
            cast = cooldown = None
            continue
        update_range(completed)
        # Entity.OnTick runs its state machine before buffs and ability timers.
        # A retry from idle therefore sees the last completed ability tick.
        change = changes.get((frame, phase))
        before_switch_search = (not late and change is not None and cast is None and frame > first_cast and
                                switch["modes"][mode].get("_escapeTime") == 0)
        object_search = not late and cast is None and (change is None or before_switch_search)
        search_speed = speed_buff
        object_waiting = object_search and cooldown and cooldown.remaining_at(frame - 1, step) >= READY_EPSILON
        if not late and timer and timer.advance(step):
            active = bool(normal or extra)
            if active != speed_buff:
                speed_buff = active
                if cooldown:
                    track = next(s for s in switch["action_samples"][mode]["attack"]
                                 if s["module_speed_bonus_active"] == active)
                    cooldown.change_period(frame - 1, step, track["period_q32"])
                emit("module_speed_changed", active=active)

        if before_switch_search and not object_waiting:
            # Idle.OnTick searches in OBJECTS before button delivery in INPUT
            # and before the skill can expire in COROUTINES.
            stop = start_cast(frame, search_speed, late=False)
            if stop:
                break

        if change:
            destination, cause = change
            idle = cast is None and (live is not None or frame > first_cast)
            if cast is None and not idle:
                stop = "restart_outside_attack_state"
                break
            if live is None and idle and not late and not before_switch_search:
                dead = bounds.dead_by(frame)
                possible = (inside | {k for k,f in unknown.items() if f < frame}) - dead.keys()
                if (switch["modes"][mode].get("_escapeTime") != 0 or
                        any(k not in levels or masks[mode]["main"] & ENEMY_LEVEL_BITS.get(levels[k], 7)
                            for k in possible)):
                    stop = "idle_search_on_switch_frame_unresolved"
                    break
            old_start, old_end, old_key = (None,None,None) if idle else (cast["start"], cast["end"], cast["key"])
            action_boundary = cause == "natural_end" and not late and frame in (old_key, old_end)
            if live is None and action_boundary and (deterministic_skill_start is None or old_start < deterministic_skill_start):
                stop = "switch_on_action_boundary_order_unresolved"
                break
            before = dict(normal=normal, extra=extra)
            if speed_buff:
                speed_buff = False
                emit("module_speed_changed", active=False, reason="old_mode_talent_detached")
            mode = destination
            # The S3 root yields at least one fixed update before attaching the
            # mode buff, so even an attack restarted in that attachment callback
            # is registered after the skill in the deterministic scheduler.
            deterministic_skill_start = frame if destination == "S3" and not late else None
            capacity = limits[mode]
            normal, extra = redistribute_charges(normal, extra, normal_limit=capacity["normal_limit"],
                                                 extra_limit=capacity["extra_limit"])
            if timer:
                timer = RecoveryTimer(timer.period)
            applied = None if idle else old_key < frame + int(late)
            emit("mode_changed", cause=cause, effect_phase=phase, previous_state="IDLE" if idle else "ATTACK",
                 previous_storage=before, interrupted_start=old_start,
                 interrupted_key=old_key, interrupted_nominal_end=old_end,
                 remaining_frames=None if idle else old_end-frame, old_spell_applied=applied,
                 canceled_pending_spell=False if idle else not applied,
                 **({"coroutine_order":"earlier_registered_skill_before_later_cast"} if action_boundary else {}))
            cast = None
            cooldown = None
            object_search = object_waiting = False
            if idle:
                # Idle.OnEnter plays idle; the next OBJECTS phase performs its first search.
                continue
        if frame < first_cast:
            continue
        if cast is not None and frame >= cast["end"]:
            # HALF_FRAME clears small residuals only when the period still
            # matches the one captured at cast start (_CheckNeedResetCooldown).
            if cooldown.period == cast["period"] and cooldown.remaining_at(frame, step) < step//2 - READY_EPSILON:
                cooldown = None
            cast = None
        search_cutoff = completed + 1
        cfg = switch["modes"][mode]
        locked = (cfg.get("_selectTargetSource"), cfg.get("_selectTargetTiming"), cfg.get("_alwaysIncludeTarget")) == (2,0,1)
        if cast and cast["target"] is not None and frame == cast["key"] and (
                not live.valid(cast["target"],frame) if live is not None else cast["target"] in bounds.dead_by(frame+1)):
            target = cast["target"]
            empty_fallback = all(cfg.get(name)==0 for name in ("_allowNoTarget", "_interuptIfTargetDead",
                "_emitToInputPosWhenTargetIsInvalid", "_emitToInputRootTileWhenTargetIsInvalid"))
            eligible_charges = spend_on_target(normal, extra, levels[target], masks[mode])
            stored_locked=switch.get("locked_stored_attacks",{}).get(mode,False)
            if live is None and (not locked or not empty_fallback or (any(eligible_charges) and not stored_locked)):
                stop = "stored_or_unreviewed_cast_lost_target_before_release"
                break
            # No successful spell: FinishIfNot(TARGET_DEAD) clears remaining cooldown.
            # Its finish callback searches again in COROUTINES before the same late animation tick.
            emit("locked_target_dead_at_release", target=target, main_fired=0, cooldown_remaining_q32=0)
            cast = None
            cooldown = None
            search_cutoff = frame + 1
            update_range(frame)
        if cast is None:
            if object_waiting or (not object_search and cooldown and cooldown.remaining_at(frame, step) >= READY_EPSILON):
                continue
            stop = start_cast(search_cutoff, search_speed if object_search else speed_buff, late)
            if stop:
                break
            if cast is None:
                continue
        if frame == cast["key"]:
            target = cast["target"]
            if target:
                outside = target not in (live.candidates(frame, late) if live is not None else inside)
                if live is None and bounds.floor_at(target,frame) <= HP_EPSILON:
                    stop = "target_survival_unresolved_at_release"
                    break
                if live is None and (unknown.get(target, frame) < frame or
                        outside and not (locked and walking_target and walking_target(target,frame)
                                         and bounds.floor_at(target,frame)>HP_EPSILON)):
                    stop = "target_changed_before_projectile_release"
                    break
                n, e = spend_on_target(normal, extra, levels[target], masks[mode])
                normal -= n
                extra -= e
                emit("volley_released", target=target, enemy_level=levels[target], main_fired=1,
                     normal_fired=n, extra_fired=e, animation_seconds_at_key=cast["animation_time"],
                     **({"locked_target_outside_range":True} if outside else {}))
                stop = on_release(mode, target, frame, n, e, cast)
                if stop:
                    break
            else:
                if normal < limits[mode]["normal_limit"]:
                    normal += 1
                    slot = "normal"
                else:
                    extra += 1
                    slot = "extra"
                emit("charge_gained", slot=slot)
    if not stop:
        stop = finish_tick(frame)
    return dict(events=events, last_frame=frame, stop=stop or "horizon",
                storage=dict(normal=normal, extra=extra), module_speed_active=speed_buff,
                **({"joint_operator_events":lin_impacts.events} if lin_impacts else {}),
                **(dict(hp_upper_bounds=bounds.snapshot(), hp_intervals=bounds.interval_snapshot()) if bounds is not None else {}))


def _simulate(*args, **kwargs):
    frames = _attack_frames(*args, **kwargs)
    while True:
        try:
            next(frames)
        except StopIteration as finished:
            return finished.value


def _exact_trace_packets(flight):
    if flight["status"]!="conditional_projectile_reach":
        return [],flight.get("reason",flight["status"])
    if not all(v["has_only_trace_hit"] for variants in flight["prefabs"].values() for v in variants.values()):
        return [],"unreviewed_projectile_hit_path"
    traces=flight["scenarios"]
    packets=[]
    for index,shot in enumerate(traces[0]["shots"]):
        timings={(a["frame"],a["receiver_sample_lag"],a["stop"]) for branch in traces
                 for a in branch["shots"][index]["arrivals"]}
        if all(timing[2]=="horizon" for timing in timings):
            continue
        if len(timings)!=1:
            return [],"projectile_arrival_not_unique"
        frame,lag,stop=timings.pop()
        if stop!="ordinary_reach":
            return [],stop
        arrival=dict(frame=frame,receiver_sample_lag=lag,stop=stop)
        packets.append((frame,lag,dict(shot,arrivals=[arrival])))
    # Both charge pools are created before the ordinary main projectile.
    order={"stored":0,"extra":1,"main":2}
    return sorted(packets,key=lambda packet:order[packet[2]["packet"]]),None


class _DeltaDeliveries:
    """Deliver issued projectiles and advance their shared target gauges on OBJECTS ticks."""

    def __init__(self, tables, scene, card, spawned, history, end, source_modes, exits, bounds,
                 *, aura, energy, base_resistance_through):
        self.tables, self.scene = tables, scene
        self.card = dict(card,module_info={"typeIcon":"msc-d"},module_phase=equipped_phase(tables,card))
        self.spawned, self.history, self.end = spawned, history, end
        self.modes, self.exits, self.bounds = tuple(sorted(source_modes.items(),reverse=True)), exits, bounds
        self.aura, self.energy, self.base_resistance_through = aura, energy, base_resistance_through
        self.receivers, self.pending = {}, defaultdict(list)
        self.events = []
        self.samples, self.hidden = None, {}

    def queue(self, target, launch, mode, normal, extra, flight, volleys):
        packets,reason=_exact_trace_packets(flight)
        if reason:
            return {"projectile_arrival_not_unique":"delta_projectile_arrival_not_unique",
                    "unreviewed_projectile_hit_path":"unreviewed_delta_projectile_hit_path"}.get(reason,reason)
        row=dict(target=target,launch_frame=launch,mode=mode,normal_fired=normal,extra_fired=extra,
                 status="live_delta_delivery",projectiles=flight,impacts=[],not_alive_by_frame=None)
        if self.scene.enemies[self.spawned[target]["enemy"]]["skills"]:
            row["receiver_assumption"] = "ordinary_receive_rules_for_unmodeled_enemy_skill_states"
        volleys[(target,launch,mode)]=row
        for frame,lag,shot in packets:
            self.pending[frame].append((row,lag,shot["packet"]))

    def _record(self, target, state, frame, before, first_event):
        lower = self.bounds.floor_at(target, frame)
        if before>state.hp:
            self.bounds.apply_arts_interval(target,frame,before-state.hp,before-state.hp)
        for event in state.events[first_event:]:
            row = dict(event, target=target)
            if lower <= HP_EPSILON < before:
                row["conditional_on_target_alive"] = True
            if "hp_after_q32" in row:
                before = row["hp_after_q32"]
                lower = max(0, lower - row["damage_q32"])
                if lower < HP_EPSILON:
                    lower = 0
                if lower != before:
                    row["hp_interval_q32"] = [lower, row.pop("hp_after_q32")]
            self.events.append(row)

    def _resistance(self, target, frame, attrs, dead):
        base=attrs["magicResistance"]["m_value"]
        assumptions = []
        if len(self.bounds.resistances[target])>1 and (
                self.base_resistance_through is None or frame>self.base_resistance_through):
            reviewed=(self.energy or {}).get("reviewed_energy_enemies",())
            interval = (overload_resistance_bounds(self.energy, target, frame)
                        if self.spawned[target]["enemy"] in reviewed else None)
            if interval is None:
                assumptions.append("base_resistance_without_unmodeled_overload")
            else:
                base += interval[0]
                if interval[0] != interval[1]:
                    assumptions.append("lower_overload_resistance_bound")
        if self.aura:
            lower,upper=self.aura.bonus_bounds(target,frame,dead_by=dead,finished_by=self.exits,
                source_alive=lambda key,tick:self.bounds.floor_at(key,tick)>HP_EPSILON)
            if lower!=upper:
                assumptions.append("lower_aura_resistance_bound")
            base+=lower
        return _raw_f32(base), assumptions

    def tick(self, frame, *, advance_receivers=True):
        from .enemy_elements import EnemyElements
        for target,state in self.receivers.items() if advance_receivers else ():
            if state.hp>HP_EPSILON and frame<=self.exits.get(target,self.end):
                # Continue the living branch at its HP upper bound. The shared
                # ledger retains the lower bound, including possible deaths.
                state.hp=self.bounds.upper_at(target,frame)
                before,offset=state.hp,len(state.events)
                state.tick(frame)
                self._record(target,state,frame,before,offset)
        for row,lag,packet in self.pending.pop(frame,()):
            target,launch_mode=row["target"],row["mode"]
            dead=self.bounds.dead_by(frame+1)
            if target in dead or self.exits.get(target,self.end+1)<=frame:
                row["impacts"].append(dict(frame=frame,packet=packet,status="target_no_longer_alive"))
                continue
            if self.samples is None:
                self.samples=_position_samples(self.history,set(range(self.end+1)),hidden_samples=self.hidden)
            mode=next((mode for tick,mode in self.modes if tick<=frame),launch_mode)
            hit=dict(mode=mode,launch_mode=launch_mode,projectile=packet)
            source=delta_projectile_profile(self.tables,self.card,hit)
            peers={k:birth for k,birth in self.spawned.items()
                   if k not in dead and self.exits.get(k,self.end+1)>frame}
            geometry=_talent_at_hit(source,target,frame,lag,peers,self.samples,hidden_samples=self.hidden)
            if geometry["status"] not in ("isolated","neighbors_in_radius_if_alive"):
                return "delta_projectile_isolation_unresolved"
            enemy=self.scene.enemies[self.spawned[target]["enemy"]]
            attrs=enemy["attributes"]
            resistance, resistance_assumptions = self._resistance(target,frame,attrs,dead)
            state=self.receivers.get(target)
            if state is None:
                state=EnemyElements.from_scene_enemy(self.tables,dict(level=self.scene.enemy_level(self.spawned[target]["enemy"]),
                    hp_q32=self.bounds.upper_at(target,frame),
                    ep_resistance_q32=_raw_f32(attrs["epResistance"]["m_value"]),
                    ep_damage_resistance_q32=_raw_f32(attrs["epDamageResistance"]["m_value"])))
                self.receivers[target]=state
            others=[self.samples[frame][k] for k in geometry["nearby"] if self.bounds.floor_at(k,frame)>HP_EPSILON]
            if geometry["nearby"] and not others:
                return "delta_projectile_isolation_unresolved"
            before,offset=state.hp,len(state.events)
            possibly_dead=self.bounds.floor_at(target,frame)<=HP_EPSILON
            delta_projectile_impact(self.tables,self.card,state,frame,
                dict(hit,target_center=geometry["center_row_col"],other_living_centers=others),
                resistance)
            self._record(target,state,frame,before,offset)
            snapshot=state.snapshot()
            lower=self.bounds.floor_at(target,frame)
            impact=dict(frame=frame,packet=packet,source_mode=mode,element_state=snapshot)
            if resistance_assumptions:
                impact["resistance_assumptions"] = resistance_assumptions
            if lower==state.hp:
                impact["hp_after_q32"]=state.hp
            else:
                impact["hp_interval_q32"]=snapshot["hp_interval_q32"]=[lower,snapshot.pop("hp_q32")]
            if possibly_dead:
                impact["conditional_on_target_alive"]=True
            row["impacts"].append(impact)
            if state.hp<=HP_EPSILON:
                row["not_alive_by_frame"]=frame


def mode_replay(tables, scene, card, placement, switch, cycle, sp, damage, events, unknown, spawned, history, end,
                *, round_position=True, priority=None, covered_exits=False, device_hit=None, base_resistance_through=None,
                aura=None, lin_active=None):
    if switch.get("status") not in ("conditional_mode_switch", "ordinary_mode") or not cycle.get("profile"):
        return dict(status="unresolved", reason="missing_mode_or_cycle_profile")
    for field, wanted in (("charge_limits", "conditional_charge_limits"),
                          ("target_level_rules", "target_level_masks")):
        if switch[field]["status"] != wanted:
            return switch[field]
    trigger = switch["module_speed_trigger"]
    if not (trigger["status"] == "no_storage_speed_bonus" or (trigger["status"] == "periodic_storage_speed"
            and trigger["wait_first_trigger_interval"] and trigger["first_trigger_interval_seconds"] < 0)):
        return dict(status="unresolved", reason="unsupported_storage_speed_clock")
    changes = {(r.get("effect_frame",r["input_frame"]), "tick" if r.get("effect_phase","input")=="input"
                else r["effect_phase"]): (r["destination_mode"],r["input"]) for r in switch["buttons"]}
    for frame in switch["natural_returns_to_default"]:
        phase = "unity_fixed_resume" if frame in switch.get("natural_after_fixed", ()) else "tick"
        if (frame,phase) in changes:
            return dict(status="unresolved", reason="skill_expiry_and_input_same_frame")
        changes[frame,phase] = ("Default", "natural_end")
    if changes and min(changes)[0] <= sp["deployment_frame"]:
        return dict(status="unresolved", reason="no_previous_actor_update")
    ranges = defaultdict(list)
    for event in events:
        if (event["kind"] == "route_end_in_range" or
                (event["kind"] in ("attack_range_enter", "attack_range_exit") and
                 event.get("operator_id") == placement["operator_id"])):
            ranges[event["frame"]].append(event)
    levels = {target: scene.enemy_level(row["enemy"]) for target, row in spawned.items()}
    route_ends = {e["instance_id"]:e["frame"] for e in events if e["kind"] == "route_end_in_range"}
    finished_at_exit = {key: tick for key, tick in route_ends.items()
                        if covered_exits and _reviewed_regular_enemy(scene, spawned[key])}
    scenarios = []
    profile = cycle["profile"]
    first_frame = sp["deployment_frame"] + 1
    volleys = {}
    live_delta = damage is not None and selected_branch(tables,card)=="msc-d"
    lin_impacts = {}
    if lin_active and lin_active.get("joint_coverage"):
        through = min(end, lin_active["joint_coverage"]["through_frame"])
        applied_stuns = {(c["instance_id"],c["frame"]) for c in lin_active.get("applied_stuns", ())}
        def walking_control(key, frame):
            enemy = scene.enemies[spawned[key]["enemy"]]
            prefab = enemy["prefabKey"]["m_value"]
            if (prefab not in (*MELEE_ONLY,AXE,DRONE,BOMBER) or enemy["attributes"]["stunImmune"]["m_value"]
                    or enemy["skills"] or device_hit is None):
                return None
            if prefab == BOMBER:
                return dict(instance_id=key, frame=frame, duration_ticks=lin_active["profile"]["stun_object_ticks"],
                            motion_source="bombardier")
            prior_vent = any(e["instance_id"] == key and e["frame"] < frame for e in device_hit.get("effects", ()))
            return dict(instance_id=key, frame=frame, duration_ticks=lin_active["profile"]["stun_object_ticks"],
                        motion_source="vent_adjusted" if prior_vent else "normal_route")
        def apply_lin(bounds, attack):
            return apply_joint_lin_attack(bounds, scene, spawned, history, aura, base_resistance_through,
                                         lin_active["profile"], attack, device_hit,priority=priority)
        for activation in lin_active["activations"]:
            for attack in activation["conditional_attacks"]:
                tick = attack["impact_frame"]
                if tick is not None and tick <= through:
                    lin_impacts[tick] = attack

    def trace_release(mode, target, frame, normal_count, extra_count, anim_time, bounds, pending,
                      *, flight, hp_frame=None):
        arrivals = [a["frame"] for branch in flight.get("scenarios", ()) for shot in branch["shots"]
                    for a in shot["arrivals"] if a["stop"] == "ordinary_reach" and a["frame"] <= end]
        first_impact = min(arrivals, default=None)
        # A direct hit during flight can kill a neighbour or remove its resistance aura.
        if hp_frame is None and damage and first_impact is not None and any(frame <= hit < first_impact for hit in lin_impacts):
            pending[first_impact] = (mode, target, frame, normal_count, extra_count, anim_time, flight)
            return None
        sample = frame if hp_frame is None else hp_frame
        dead_by = bounds.dead_by(sample)
        if target in dead_by:
            if hp_frame is None or not _reviewed_regular_enemy(scene, spawned[target]) or not flight.get("scenarios") or not all(
                v["has_only_trace_hit"] for variants in flight["prefabs"].values() for v in variants.values()
            ):
                return "projectile_target_dead_before_first_impact"
            # The trace reference checks Entity.alive before DealHitTarget. Both
            # the main damage and Appoggiatura require that hit callback.
            volleys[(target, frame, mode, normal_count, extra_count, dead_by[target])] = dict(
                target=target, launch_frame=frame, mode=mode, normal_fired=normal_count, extra_fired=extra_count,
                status="no_hit_target_died_in_flight", target_dead_by_frame=dead_by[target],
                surviving_target_first_impact_frame=first_impact, not_alive_by_frame=None)
            return None
        upper = bounds.before(sample)
        floors = bounds.floors_before(sample)
        prior = tuple(tuple(sorted((key, tuple(sorted(rows.items()))) for key, rows in limits.items()))
                      for limits in (upper, floors))
        signature = (target, frame, normal_count, extra_count, mode, anim_time, sample, prior, tuple(sorted(dead_by.items())))
        if signature not in volleys:
            row = dict(target=target, launch_frame=frame, mode=mode, normal_fired=normal_count,
                       extra_fired=extra_count, not_alive_by_frame=None)
            volleys[signature] = row
            projected = flight
            row.update(projectiles=projected, status=projected["status"])
            if hp_frame is not None:
                row["hp_sample_before_impact_frame"] = hp_frame
            if "reason" in projected:
                row["reason"] = projected["reason"]
            if damage is not None:
                if first_impact is not None and device_hit and hp_frame is None:
                    upper, floors, scheduled = bounds.before_projectile_hits(
                        frame, first_impact, device_hit.get("effects", [device_hit]))
                    if scheduled:
                        row["vent_hits_before_first_impact"] = [dict(frame=e["frame"], instance_id=e["instance_id"],
                            alias=e.get("alias"), first_impact=first_impact) for e in scheduled]
                hp = first_volley_damage(tables, scene, card, spawned, projected, end,
                                         history=history, dead_by=dead_by, finished_by=finished_at_exit, receiver_hp_bounds=upper,
                                         receiver_hp_floors=floors, source_mode_by_impact_frame=impact_mode_changes(switch),
                                         base_resistance_through=base_resistance_through, aura=aura, energy_timeline=device_hit)
                row["damage"] = hp
                if hp["status"] == "unresolved":
                    row.update(status="unresolved", reason=hp["reason"])
                deadlines = [case["not_alive_by_frame"] for b in hp.get("scenarios", []) for case in b["resistance_cases"]]
                if deadlines and all(f is not None for f in deadlines):
                    row["not_alive_by_frame"] = max(deadlines)
        if volleys[signature]["status"] == "unresolved":
            return volleys[signature]["reason"]
        if "damage" in volleys[signature]:
            bounds.apply(volleys[signature]["damage"], release_frame=sample)
            for e in volleys[signature].get("vent_hits_before_first_impact", ()):
                bounds.precredited_vent_hits[e["frame"], e["instance_id"], e["alias"]] = e["first_impact"]
        else:
            bounds.invalidate_floors(frame)
            return volleys[signature].get("reason", "volley_hp_unresolved")

    for case, res in _life_cases(damage):
        bounds = HealthBounds(scene, spawned)
        pending = {}
        last_facing = None
        delta = (_DeltaDeliveries(tables,scene,card,spawned,history,end,impact_mode_changes(switch),finished_at_exit,bounds,
                                  aura=aura,energy=device_hit,base_resistance_through=base_resistance_through)
                 if live_delta else None)

        def projectile_tick(tick):
            if delta:
                return delta.tick(tick)
            if tick in pending:
                mode, target, launch, normal, extra, anim, flight = pending.pop(tick)
                return trace_release(mode, target, launch, normal, extra, anim, bounds, pending, flight=flight, hp_frame=tick)

        def on_release(mode, target, frame, normal_count, extra_count, cast):
            nonlocal last_facing
            anim_time=cast["animation_time"]
            if not delta and (pending or bounds.last_projected_impact is not None and bounds.last_projected_impact>=frame):
                return "overlapping_volleys_require_joint_delivery"
            if not delta and (target,frame)==case[:2]:
                bounds.apply(damage,release_frame=frame,first_case=case,allowed_res=res)
                last_facing=None
                return None
            prior_face=prior_sign=None
            if mode=="S3" and last_facing and last_facing[1]==cast["start"] and not any(
                    last_facing[0]<tick<=cast["start"] for tick,_ in changes):
                prior_face,prior_sign=last_facing[2:]
            flight=ordinary_projectiles_for_releases(tables,scene,placement,profile,
                [(target,frame,normal_count,anim_time)],spawned,history,end,mode=mode,
                extra_count=extra_count,round_position=round_position,
                cast_starts={(target,frame):[cast["start"]]} if mode=="S3" else None,
                previous_face=prior_face,previous_sign=prior_sign)
            faces={branch["face"] for branch in flight.get("scenarios",())}
            signs={branch["x_sign"] for branch in flight.get("scenarios",())}
            last_facing=(cast["start"],cast["end"],next(iter(faces)) if len(faces)==1 else None,
                         next(iter(signs)) if len(signs)==1 else None) if mode=="S3" else None
            if delta:
                if target not in bounds.resistances:
                    return "delta_projectile_receiver_attributes_unknown"
                return delta.queue(target,frame,mode,normal_count,extra_count,flight,volleys)
            return trace_release(mode,target,frame,normal_count,extra_count,anim_time,bounds,pending,flight=flight)

        def walking_target(target, frame):
            return route_ends.get(target,end+1)>frame and all(
                any(r["instance_id"]==target and kind in ("free_motion_sample","exit_motion_sample","post_wait_motion_sample")
                    and r["active_ticks"]>0 for kind,r in history.get(tick,()))
                for tick in (frame-1,frame))

        run = _simulate(switch, changes, ranges, unknown, levels, bounds, on_release, first_frame,
                        first_frame + profile["birth_updates"], end, profile["step_q32"], priority, device_hit, walking_target,
                        LinImpactClock(lin_impacts,apply_lin,walking_control,applied_stuns) if lin_impacts else None,
                        projectile_tick)
        through = run["last_frame"] - int(run["stop"] != "horizon")
        if delta:
            run["element_events"]=delta.events
        run["enemy_lifecycle"] = ordinary_enemy_finishes(scene, spawned, bounds, route_ends, through,
                                                        covered_exits=covered_exits)
        scenarios.append(dict(resistance_cases=res, prior_target=case[0], prior_launch_frame=case[1],
                              prior_target_not_alive_by=case[2], replay=run))
    return dict(status="conditional_mode_replay", enemy_levels=levels, scenarios=scenarios,
                subsequent_volleys=list(volleys.values()), conditions=[
        "spawn_coverage_from_selected_scheduler_and_no_external_combat_interactions",
        "prior_ordinary_cycle_and_damage_upper_bound_conditions",
        "new_mode_talent_timer_first_advanced_on_next_object_tick_after_attachment",
        "direct_reentry_to_attack_and_ready_reattached_ability",
        "uninterrupted_animation_and_pending_cast_coroutine_resumes_after_baked_event",
        "latched_cast_duration_and_separate_attack_ability_cooldown",
        "first_eligible_search_after_full_storage_on_next_object_tick",
        "in_range_living_targets_selectable_without_status_or_external_damage"])

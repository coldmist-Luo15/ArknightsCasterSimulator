"""Conservative HP intervals shared by attacks on a clock without healing or revival."""
from collections import defaultdict
from copy import deepcopy

from .damage import HP_EPSILON
from .fixed import ONE


class HealthBounds:
    def __init__(self, scene, spawned):
        self.maximum = {}
        self.resistances = {}
        self.history = {}
        self.lower_history = {}
        self.last_projected_impact = None
        self.last_isolated_projectile_target = None
        self.precredited_vent_hits = {}
        for instance, birth in spawned.items():
            enemy = scene.enemies[birth["enemy"]]
            hp = enemy["attributes"]["maxHp"]
            resistance = enemy["attributes"]["magicResistance"]
            if hp["m_defined"] and resistance["m_defined"]:
                self.maximum[instance] = hp["m_value"] * ONE
                bonus = next((row["value"] for row in enemy["talentBlackboard"] or []
                              if row["key"] == "over_energy.magic_resistance"), 0)
                base = resistance["m_value"]
                self.resistances[instance] = {base, base+bonus}

    def before(self, frame):
        result = {}
        for instance, changes in self.history.items():
            bound = min((hp for f, hp in changes.items() if f < frame), default=self.maximum[instance])
            if bound < self.maximum[instance]:
                result[instance] = {r: bound for r in self.resistances[instance]}
        return result

    def dead_by(self, frame):
        return {instance: min(deaths) for instance, changes in self.history.items()
                if (deaths := [f for f, hp in changes.items() if f < frame and hp <= HP_EPSILON])}

    def floors_before(self, frame):
        return {instance: {r: self.floor_at(instance, frame-1) for r in resistances}
                for instance, resistances in self.resistances.items()}

    def floor_at(self, instance, frame):
        return min((hp for f, hp in self.lower_history.get(instance, {}).items() if f <= frame),
                   default=self.maximum.get(instance, 0))

    def upper_at(self, instance, frame):
        return min((hp for f,hp in self.history.get(instance, {}).items() if f<=frame),
                   default=self.maximum.get(instance,0))

    def invalidate_floors(self, frame):
        for instance in self.maximum:
            self.lower_history.setdefault(instance, {})[frame] = 0

    def _device_damage(self, event):
        interval = event.get("damage_bounds_q32")
        if interval is None:
            return event["damage_q32"], event["damage_q32"]
        low, high = interval
        if any(self.floor_at(k,event["frame"]) > HP_EPSILON for k in event.get("guaranteeing_aura_sources", ())):
            return low, low
        return low, high

    def apply_device_hit(self, event):
        """Insert the COROUTINES hit after same-frame OBJECTS impacts into the HP histories."""
        frame, key = event["frame"], event["instance_id"]
        low, high = self._device_damage(event)
        if key not in self.maximum:
            return False
        upper = self.upper_at(key,frame)
        if upper <= HP_EPSILON:
            return True
        lower = self.floor_at(key, frame)
        last = self.last_projected_impact
        seeded_at = self.precredited_vent_hits.pop((frame, key, event.get("alias")), None)
        if seeded_at is None and last is not None and last > frame and self.floor_at(key, last) - high <= HP_EPSILON:
            # The earlier death of even a secondary unit can change later proximity or splash checks.
            return False
        for series, before, damage in ((self.history, upper, low), (self.lower_history, lower, high)):
            rows = series.setdefault(key, {})
            rows[frame] = before
            for time, hp in rows.items():
                if time >= frame and (seeded_at is None or time < seeded_at):
                    remaining = hp - damage
                    rows[time] = remaining if remaining >= HP_EPSILON else 0
        return True

    def apply_arts_interval(self, instance, frame, minimum, maximum):
        """Insert a COROUTINES hit with uncertain resistance or target eligibility."""
        if instance not in self.maximum or not 0 <= minimum <= maximum:
            return None
        upper = self.upper_at(instance,frame)
        lower = self.floor_at(instance, frame)
        if upper <= HP_EPSILON:
            return dict(before_q32=[lower,upper], after_q32=[lower,upper], kill="already_dead")
        last = self.last_projected_impact
        if last is not None and last > frame and self.floor_at(instance,last) - maximum <= HP_EPSILON:
            return None
        for series, start, amount in ((self.history,upper,minimum), (self.lower_history,lower,maximum)):
            values = series.setdefault(instance, {})
            values[frame] = start
            for tick in values:
                if tick >= frame:
                    after = values[tick] - amount
                    values[tick] = after if after >= HP_EPSILON else 0
        after = [self.lower_history[instance][frame], self.history[instance][frame]]
        certain = after[1] <= HP_EPSILON and lower > HP_EPSILON
        possible = after[0] <= HP_EPSILON and maximum > 0
        return dict(before_q32=[lower,upper], after_q32=after, kill="certain" if certain else "possible" if possible else "none")

    def before_projectile_hits(self, release, first_impact, device_hits):
        """Preview intervening vent hits only if every living recipient survives until the first impact."""
        events = [e for e in device_hits if release < e["frame"] < first_impact]
        if not events:
            return self.before(release), self.floors_before(release), []
        preview = deepcopy(self)
        for hit in events:
            frame, key = hit["frame"], hit["instance_id"]
            _, maximum = preview._device_damage(hit)
            if key not in preview.dead_by(frame+1) and preview.floor_at(key, frame) - maximum <= HP_EPSILON:
                return self.before(release), self.floors_before(release), []
            if not preview.apply_device_hit(hit):
                return self.before(release), self.floors_before(release), []
        return preview.before(first_impact), preview.floors_before(first_impact), events

    def apply(self, report, *, release_frame, first_case=None, allowed_res=None):
        """At a given frame all feasible profiles must prove the bound, including misses."""
        cases = []
        isolated = True
        targets = set()
        for branch in report.get("scenarios", ()):
            for case in branch["resistance_cases"]:
                if first_case is not None and (
                    (branch["target"], branch["launch_frame"], case["not_alive_by_frame"]) != first_case or
                    case["resistance"] not in allowed_res):
                    continue
                changes = defaultdict(list)
                lower = defaultdict(list)
                targets.add(branch["target"])
                isolated &= (bool(case["frames"]) and not case.get("pending") and
                             all(r.get("hit_talent", {}).get("status") == "isolated"
                                 and r.get("hp_lower_after_q32", 0) > HP_EPSILON for r in case["frames"]))
                updates = case.get("receiver_updates", ())
                for row in updates:
                    if row["coverage"] == "all_feasible_within_case":
                        changes[row["instance"], row["resistance"]].append((row["frame"], row["hp_upper_after_q32"]))
                # The main packet report is also available for older, ordinary-only projectiles.
                changes[branch["target"], case["resistance"]].extend(
                    (row["frame"], row["hp_upper_after_q32"]) for row in case["frames"])
                for row in case.get("receiver_lower_updates", ()):
                    lower[row["instance"], row["resistance"]].append((row["frame"], row["hp_lower_after_q32"]))
                cases.append((branch["target"], case["resistance"], changes, lower))
                if not case.get("receiver_lower_coverage_complete"):
                    self.invalidate_floors(release_frame)
        if not cases:
            self.invalidate_floors(release_frame)
            return
        projected = [f for _, _, upper, lower in cases for data in (upper, lower)
                     for entries in data.values() for f, _ in entries]
        if projected:
            self.last_projected_impact = max(projected)
            self.last_isolated_projectile_target = next(iter(targets)) if isolated and len(targets) == 1 else None
        touched = defaultdict(set)
        for _, _, changes, lower in cases:
            # A lower bound must hold even in the branch that takes most damage.
            for (instance, _), entries in lower.items():
                if instance in self.maximum:
                    history = self.lower_history.setdefault(instance, {})
                    for frame, hp in entries:
                        history[frame] = min(history.get(frame, self.maximum[instance]), hp)
            for (instance, _), entries in changes.items():
                touched[instance].update(f for f, _ in entries)
        for instance, frames in touched.items():
            if instance not in self.maximum:
                continue
            history = self.history.setdefault(instance, {})
            for frame in sorted(frames):
                previous = min((v for f, v in history.items() if f <= frame), default=self.maximum[instance])
                outcomes = []
                for target, primary_res, changes, _ in cases:
                    resistances = [primary_res] if target == instance else self.resistances[instance]
                    if instance in report.get("known_base_resistances", {}):
                        resistances = [report["known_base_resistances"][instance]]
                    for r in resistances:
                        outcomes.append(min((v for f, v in changes.get((instance, r), ()) if f <= frame),
                                            default=previous))
                upper = min(previous, max(outcomes))
                if upper < previous:
                    history[frame] = upper

    def snapshot(self):
        return {instance: [dict(frame=f, hp_upper_q32=hp, hp_upper=hp / ONE)
                           for f, hp in sorted(changes.items())]
                for instance, changes in self.history.items() if changes}

    def interval_snapshot(self):
        result = {}
        for instance in sorted(set(self.history) | set(self.lower_history)):
            upper = self.maximum[instance]
            changes = self.history.get(instance, {})
            frames = set(changes) | set(self.lower_history.get(instance, {}))
            rows = []
            for frame in sorted(frames):
                upper = min(upper, changes.get(frame, upper))
                lower = self.floor_at(instance, frame)
                if lower > upper:
                    raise ValueError(f"生命区间上下界冲突 {instance} {frame}")
                rows.append(dict(frame=frame, hp_lower_q32=lower, hp_upper_q32=upper))
            result[instance] = rows
        return result


def _reviewed_regular_enemy(scene, birth):
    enemy = scene.enemies[birth["enemy"]]
    prefab = enemy["prefabKey"]
    return (birth.get("scheduler_regular_enemy") and prefab["m_defined"]
            and prefab["m_value"] in ("enemy_1353_esabr_2", "enemy_1354_eghost_2", "enemy_1355_mrfly_2",
                                     "enemy_1357_erob_2", "enemy_1359_ekulsr_2") and not enemy["skills"]
            and not enemy["notCountInTotal"]["m_value"])


def ordinary_enemy_finishes(scene, spawned, bounds, route_ends, through_frame, *, covered_exits=False):
    """Finish counters on proven ordinary no-rebirth paths, before any possible exit."""
    entries = []
    unknown = []
    exits = []
    deaths = bounds.dead_by(through_frame+1)
    for instance, deadline in sorted(deaths.items(), key=lambda p:(p[1], p[0])):
        birth = spawned[instance]
        if not _reviewed_regular_enemy(scene, birth):
            unknown.append(dict(instance=instance, reason="unreviewed_enemy_finish_options", hp_dead_by=deadline))
            continue
        if instance in route_ends and route_ends[instance] <= deadline:
            if not covered_exits:
                unknown.append(dict(instance=instance, reason="exit_and_lethality_order_unresolved", hp_dead_by=deadline))
            continue
        lower_changes = bounds.lower_history.get(instance, {})
        earliest = min((f for f,hp in lower_changes.items() if hp <= HP_EPSILON), default=deadline)
        earliest = max(earliest, birth.get("spawn_frame", 0))
        entries.append(dict(instance=instance, enemy=birth["enemy"], reason="hp_zero", first_possible_frame=earliest,
                            finished_by_frame=deadline, scheduler_killed_by_frame=deadline))
    if covered_exits:
        for instance, frame in sorted(route_ends.items(), key=lambda pair: (pair[1], pair[0])):
            if frame > through_frame:
                continue
            birth = spawned[instance]
            loss = scene.enemies[birth["enemy"]].get("lifePointReduce", {})
            if (not _reviewed_regular_enemy(scene, birth) or instance not in bounds.maximum or not loss.get("m_defined")
                    or type(loss["m_value"]) is not int or loss["m_value"] < 0):
                unknown.append(dict(instance=instance, frame=frame, reason="unreviewed_exit_options"))
                continue
            if instance in deaths and deaths[instance] < frame:
                continue
            hp_lower = bounds.floor_at(instance, frame)
            # A same-frame hit with a lethal lower bound has unresolved ordering.
            escaped = hp_lower > HP_EPSILON and deaths.get(instance, frame+1) > frame
            earliest = min((f for f, hp in bounds.lower_history.get(instance, {}).items()
                            if f <= frame and hp <= HP_EPSILON), default=frame)
            earliest = max(earliest, birth.get("spawn_frame", 0))
            exits.append(dict(instance=instance, frame=frame, hp_lower_q32=hp_lower,
                              outcomes=["reached_exit"] if escaped else ["hp_zero", "reached_exit"],
                              life_loss_min=loss["m_value"] if escaped else 0, life_loss_max=loss["m_value"],
                              scheduler_release_window=[frame if escaped else earliest, frame]))
    return dict(through_frame=through_frame, confirmed_kills=len(entries), finishes=entries, unresolved=unknown,
                exit_coverage=covered_exits, exits=exits,
                confirmed_misses=sum(e["outcomes"] == ["reached_exit"] for e in exits),
                conditions=["no_rebirth_or_finish_override", "normal_registered_enemy_no_dummy_or_summon",
                            "no_external_change_to_hp_or_scheduler_options"])


def ordinary_life_points(initial, cases, route_limit):
    """Common time horizon and life interval over the feasible combat branches."""
    if not cases or any(not case.get("exit_coverage") for case in cases):
        return None
    through = min(route_limit, *(c["through_frame"] for c in cases))
    for case in cases:
        for row in case["unresolved"]:
            if row["reason"] == "unreviewed_exit_options":
                through = min(through, row["frame"]-1)
    if through < 0:
        return None
    traces = [[e for e in c["exits"] if e["frame"] <= through] for c in cases]
    frames = sorted({e["frame"] for trace in traces for e in trace})
    rows = []
    lower = upper = initial
    for frame in frames:
        lower = max(0, initial - max(sum(e["life_loss_max"] for e in trace if e["frame"] <= frame)
                                     for trace in traces))
        upper = max(0, initial - min(sum(e["life_loss_min"] for e in trace if e["frame"] <= frame)
                                     for trace in traces))
        rows.append(dict(frame=frame, life_min=lower, life_max=upper))
        if lower == 0:
            through = frame
            break
    return dict(initial=initial, through_frame=through, life_min=lower, life_max=upper,
                events=rows, defeat_possible=lower == 0,
                conditions=["complete_reviewed_damage_until_this_frame", "no_external_healing_or_damage",
                            "normal_exit_no_temp_life_or_global_modifier"])

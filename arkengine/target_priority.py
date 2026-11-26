"""Resolve HATRED_DES from ground and aerial route cursors on reviewed ordinary terrain."""
from functools import lru_cache
from math import floor

from .baked_mount import single
from .enemy_motion import _straight_walk
from .fixed import ONE
from .skill_recovery import READY_EPSILON
from .routes import compile_route


# V079 Enemy roots with no taunt-changing talent or attack in the reviewed battle.
_BASE_TAUNT_TARGETS = frozenset(("enemy_1007_slime_3", "enemy_1353_esabr_2", "enemy_1354_eghost_2", "enemy_1355_mrfly_2",
                                 "enemy_1357_erob_2", "enemy_1359_ekulsr_2"))
_MOTION = ("free_motion_sample", "post_wait_motion_sample", "exit_motion_sample")


def native_box_candidates(world, ids):
    """Deterministic Box2dPhysicsUtil target results, after validation and deduplication."""
    # Entity.CompareTo sorts by creation UID. Other battle objects consume UIDs
    # between births but cannot change the relative order of these ordinary enemies.
    def creation(key):
        birth = world.births[key]
        return birth["frame"], birth["order"]

    return sorted(set(ids), key=creation)


def first_leg_priority(tables, scene, card, spawned, history):
    """Return a resolver only for the actual attack selector and covered motion."""
    options = ("_postFilter","_maxNum","_limitTargetNum","_sortByTauntAtLast","_abilityInputTargetAsSortSource")
    for mode in ("Default","S3"):
        values = tables._component_fields("characters", card["operator_id"], options, "target_priority_"+mode,
                                          children=("Modes",mode,"Body","Attack","Trigger"))
        if tuple(values[k] for k in options) != (4,1,1,0,0):
            return None
    return remaining_route_priority(scene, spawned, history)


def remaining_route_priority(scene, spawned, history, *, additional_base_taunt=()):
    """The caller has verified a HATRED_DES selector without a higher-priority filter."""
    @lru_cache(maxsize=None)
    def route_legs(enemy, route_index):
        route = compile_route(scene, route_index, enemy)
        return _distance_legs(scene, route)

    @lru_cache(maxsize=1)
    def holds():
        return _stationary_intervals(history)

    def select(targets, frame, *, frame_by_target=None, nearest_tie=False, distance_samples=False):
        if frame_by_target is None and not nearest_tie and not distance_samples:
            shared = select_on_shared_leg(scene, spawned, history, targets, frame)
            if shared:
                return shared
        levels = set()
        for key in targets:
            enemy = spawned[key]["enemy"]
            if enemy not in _BASE_TAUNT_TARGETS and enemy not in additional_base_taunt:
                return None
            taunt = scene.enemies[enemy]["attributes"]["tauntLevel"]
            levels.add(taunt["m_value"] if taunt["m_defined"] else 0)
        if levels != {0}:
            return None
        return _rank_by_remaining_distance(spawned, history, targets, frame, route_legs, holds(),
                                           frame_by_target=frame_by_target,nearest_tie=nearest_tie,
                                           distance_samples=distance_samples)

    return select


def _distance_legs(scene, route):
    """For one-cell-cost cardinal corridors, the native path distance equals their Manhattan length."""
    legs = []
    start = route["start"]
    for command in route["commands"]:
        kind = command["kind"]
        if kind == "APPEAR_AT_POS":
            start = command["target"] if not (command["randomize_reach_offset"] or any(command["reach_offset"].values())) else None
        elif kind in ("MOVE", "END"):
            goal = command["target"]
            delta = None
            if (start is not None and not command.get("randomize_reach_offset") and
                    not any(command.get("reach_offset", {}).values()) and not command.get("reach_distance") and
                    not _straight_walk(scene, route, start, command)):
                delta = tuple(goal[k]-start[k] for k in ("row","col"))
            legs.append((command["source"], goal, delta))
            start = goal
        elif kind not in ("DISAPPEAR", "WAIT_FOR_SECONDS", "WAIT_CURRENT_FRAGMENT_TIME", "WAIT_CURRENT_WAVE_TIME"):
            legs.append((command["source"], None, None))
            start = None
    result = {}
    after = 0
    for source, goal, delta in reversed(legs):
        if delta is None:
            break
        axis = "row" if delta[0] else "col"
        length = sum(abs(d) for d in delta)
        sign = 1 if sum(delta)>0 else -1
        result[source] = (goal, axis, sign, after)
        after += length
    # The native cursor skips WAIT checkpoints when looking up the next MOVE.
    following = None
    for cmd in reversed(route["commands"]):
        if cmd["kind"] == "WAIT_FOR_SECONDS":
            if following is not None:
                result[cmd["source"]] = following
        else:
            following = result.get(cmd["source"])
    first = route["commands"][0]["source"]
    if first in result:
        result[None] = result[first]
    return result


def _stationary_intervals(history):
    """Recover stationary route positions during waits, stuns and ranged attacks."""
    waits = {}
    intervals = {}
    for frame, events in sorted(history.items()):
        for kind, row in events:
            key = row["instance_id"]
            if kind == "checkpoint_begin" and row["command"] == "WAIT_FOR_SECONDS" and not row["disappeared"]:
                waits[key, row["source"]] = (frame, dict(row["position"], leg_source=row["source"], active_ticks=1))
            elif kind == "checkpoint_reached" and (key, row["source"]) in waits:
                begin, sample = waits.pop((key, row["source"]))
                intervals.setdefault(key, []).append((begin+1, frame, sample))
            elif kind in ("enemy_stun_hold","enemy_attack_hold"):
                previous = ([row["route_sample"]] if row.get("route_sample") else
                            [r for k,r in history.get(frame-1, ()) if k in _MOTION and r["instance_id"] == key])
                if len(previous) == 1:
                    intervals.setdefault(key, []).append((frame, row["resume_move_frame"], previous[0]))
    # The request horizon can fall in the middle of a projected wait.
    end = max(history, default=-1) + 1
    for (key, _), (begin, sample) in waits.items():
        intervals.setdefault(key, []).append((begin+1, end, sample))
    return intervals


def _cursor_distance(row, leg):
    goal, axis, sign, after = leg
    orthogonal = "col" if axis == "row" else "row"
    cell = floor(row[axis] + .5)
    along = sign * (goal[axis] - cell)
    if floor(row[orthogonal] + .5) != goal[orthogonal] or along < 0:
        return None
    # The goal cell has zero local cost even before the actor reaches its center.
    distance = single(after + along + single(sign * (cell - row[axis]))) if along else single(after)
    return distance if distance == 0 or single(.1) < distance < 1000 else None


def nearest_hatred_winner(hatreds,created):
    """NearestRangeSelector compares raw hatred, then the lower unsigned BObject allocation ID."""
    for key,values in hatreds.items():
        least=min(values)
        def beats(other):
            greatest=max(hatreds[other])
            if least > greatest+READY_EPSILON:
                return True
            return least >= greatest-READY_EPSILON and created[key]<created[other]
        if all(beats(other) for other in hatreds if other!=key):
            return key
    return None


def _rank_by_remaining_distance(spawned, history, targets, frame, route_legs, holds=None,*,
                                frame_by_target=None,nearest_tie=False,distance_samples=False):
    # For these event variants default taunt is zero. The replay excludes
    # external taunt changes, so the negated hatred is exactly the route distance.
    keys = sorted(targets)
    distances = {k:[] for k in keys}
    ranks = {k:[] for k in keys}
    step = int(single(.1)*ONE)
    if frame_by_target is None:
        observations={frame-1:keys,frame:keys}
    else:
        observations={}
        for key in keys:
            observations.setdefault(frame_by_target[key],[]).append(key)
    for tick,visible_keys in observations.items():
        samples = {}
        boundaries = set()
        for kind, row in history.get(tick, ()):
            key = row["instance_id"]
            if kind in _MOTION:
                if key in samples:
                    boundaries.add(key)
                samples[key] = (kind,row)
            elif kind in ("checkpoint_begin", "checkpoint_reached", "route_end_in_range", "range_motion_unresolved"):
                boundaries.add(key)
        for key in visible_keys:
            if key in boundaries:
                return None
            sample = samples.get(key)
            if sample is None and holds:
                sample = next((("held", row) for lo,hi,row in holds.get(key, ()) if lo <= tick < hi), None)
            if sample is None:
                return None
            kind, row = sample
            birth = spawned[key]
            if row.get("active_ticks", 0) < 1:
                return None
            legs = route_legs(birth["enemy"], birth["route"])
            source = row.get("leg_source")
            if kind == "post_wait_motion_sample" and source is None:
                return None
            if source is None and (kind == "exit_motion_sample" or None not in legs):
                source = f"/routes/{birth['route']}/" + ("endPosition" if kind=="exit_motion_sample" else "checkpoints/0")
            if source not in legs:
                return None
            options = [legs[source]]
            if row.get("leg_end"):
                # At a direct corner either cursor can be visible to the selector.
                next_events = [(kind,r) for kind,r in history.get(tick+1, ()) if r["instance_id"] == key]
                if any(kind in ("checkpoint_begin", "checkpoint_reached", "route_end_in_range", "range_motion_unresolved")
                       for kind,_ in next_events):
                    return None
                following = [r for kind,r in next_events if kind in _MOTION]
                if (len(following) != 1 or following[0].get("active_ticks") != 1
                        or following[0].get("leg_source") == source or following[0].get("leg_source") not in legs):
                    return None
                options.append(legs[following[0]["leg_source"]])
            for leg in options:
                distance = _cursor_distance(row, leg)
                if distance is None:
                    return None
                rank=(int(distance*ONE)//step,birth["spawn_frame"])
                if "spawn_order" in birth:
                    rank+= (birth["spawn_order"],)
                ranks[key].append(rank)
                distances[key].append(distance)
    if distance_samples:
        return distances
    if nearest_tie:
        if any(not values for values in distances.values()):
            return None
        created={k:(spawned[k]["spawn_frame"],spawned[k].get("spawn_order",0)) for k in keys}
        winner=nearest_hatred_winner({k:[-int(d*ONE) for d in distances[k]] for k in keys},created)
        if winner is None:
            return None
        return dict(target=winner,basis="nearest_range_hatred_then_instance_uid",candidate_distances_to_exit=distances)
    winner = min(keys, key=lambda key:max(ranks[key]))
    if any(min(rows)<=max(ranks[winner]) for key,rows in ranks.items() if key!=winner):
        return None
    return dict(target=winner, filter="HATRED_DES", basis="checkpoint_distance_and_creation_order",
                candidate_distances_to_exit=distances, candidate_weight_bucket_and_spawn_frame=ranks)


def select_on_shared_leg(scene, spawned, history, targets, frame):
    """The shared remainder cancels. Check both object-order endpoints before resolving a winner."""
    targets = sorted(targets)
    births = [spawned[key] for key in targets]
    if len({b["enemy"] for b in births}) != 1 or births[0]["enemy"] not in ("enemy_1353_esabr_2","enemy_1007_slime_3"):
        return None
    routes = [scene.level["routes"][b["route"]] for b in births]
    route = routes[0]
    if any(r != route for r in routes[1:]):
        return None
    first = next(iter(route["checkpoints"] or ()), None)
    if first and first["type"] != "MOVE":
        return None
    start = route["startPosition"]
    goal = first["position"] if first else route["endPosition"]
    axes = [axis for axis in ("row","col") if start[axis] != goal[axis]]
    if len(axes) != 1:
        return None
    axis, = axes
    fixed_axis = "col" if axis=="row" else "row"
    samples = {key:[] for key in targets}
    for f in (frame-1,frame):
        for kind, row in history.get(f, ()):
            key = row["instance_id"]
            if (key not in samples or kind != "free_motion_sample" or row.get("leg_source") or
                    row.get("active_ticks", 0) < 1 or row[fixed_axis] != goal[fixed_axis]):
                continue
            if row[axis] < min(start[axis], goal[axis]) or row[axis] > max(start[axis],goal[axis]):
                return None
            # On ordinary unblocked road the next-map edge cost is one. At
            # checkpoint transitions the cursor may have advanced this tick.
            tile = scene.tile(floor(row["row"]+.5),floor(row["col"]+.5))
            if tile["tileKey"] != "tile_road" or tile["blackboard"] or tile["effects"]:
                return None
            samples[key].append(single(abs(single(row[axis])-goal[axis])))
    if any(len(rows)!=2 for rows in samples.values()):
        return None
    winner = min(samples, key=lambda key:max(samples[key]))
    gap = min(min(rows) for key,rows in samples.items() if key!=winner) - max(samples[winner])
    if gap <= single(.1):
        return None
    return dict(target=winner, filter="HATRED_DES", basis="equal_taunt_same_route_and_first_straight_walk",
                candidate_distances_to_shared_checkpoint=samples, minimum_lead_tiles=gap)

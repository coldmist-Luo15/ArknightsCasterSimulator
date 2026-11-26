"""Configured opening events and the default V079 scheduler's first-wave queue."""
from fractions import Fraction
from math import isfinite
from struct import pack, unpack

from .frames import FPS


def _frame_delay(seconds, location):
    if type(seconds) not in (int, float) or seconds < 0:
        raise ValueError(f"{location}: expected nonnegative seconds")
    frames = Fraction(str(seconds)) * FPS
    if frames.denominator != 1:
        raise ValueError(f"{location}: native sub-frame scheduling is unresolved")
    return int(frames)


def opening_spawns(level, *, fragment_start_frame=None):
    """Return records ordered by configured delay; equal delays retain file order."""
    if fragment_start_frame is not None and (
        type(fragment_start_frame) is not int or fragment_start_frame < 0
    ):
        raise ValueError("fragment_start_frame must be a nonnegative integer or None")

    fragment = level["waves"][0]["fragments"][0]
    routes = level["routes"]
    event_kinds = {"SPAWN": "spawn", "PREVIEW_CURSOR": "preview",
                   "DISPLAY_ENEMY_INFO": "display_enemy_info"}
    events = []
    for i, action in enumerate(fragment["actions"]):
        source = f"/waves/0/fragments/0/actions/{i}"
        kind = event_kinds.get(action["actionType"])
        if kind is None:
            raise ValueError(f"{source}: unsupported action {action['actionType']}")
        if (action["managedByScheduler"] is not True
                or action["hiddenGroup"] or action["randomSpawnGroupKey"]
                or action["randomSpawnGroupPackKey"] or action["randomType"] != "ALWAYS"
                or action["refreshType"] != "ALWAYS"):
            raise ValueError(f"{source}: unresolved conditional or externally scheduled action")

        count = action["count"]
        if type(count) is not int or count < 1:
            raise ValueError(f"{source}/count: expected a positive integer")
        route = action["routeIndex"]
        if type(route) is not int or not 0 <= route < len(routes):
            raise ValueError(f"{source}/routeIndex: unknown route {route}")
        start = _frame_delay(action["preDelay"], f"{source}/preDelay")
        interval = _frame_delay(action["interval"], f"{source}/interval") if count > 1 else 0
        for j in range(count):
            relative = start + j * interval
            events.append({
                "kind": kind, "action_type": action["actionType"],
                "relative_frame": relative,
                "frame": None if fragment_start_frame is None else fragment_start_frame + relative,
                "wave_index": 0, "fragment_index": 0, "action_index": i, "occurrence_index": j,
                "source": source, "route_index": route, "enemy_key": action["key"],
                "instance_id": f"w0-f0-a{i}-n{j}" if kind == "spawn" else None,
                "route_start": dict(routes[route]["startPosition"]),
                "auto_preview_route": action["autoPreviewRoute"],
                "block_fragment": action["blockFragment"], "dont_block_wave": action["dontBlockWave"],
            })
    # Row order breaks presentation ties only. Execution order needs a native trace.
    return sorted(events, key=lambda event: event["relative_frame"])


def _float(value):
    return unpack("<f", pack("<f", value))[0]


def _sort_queue(queue):
    """V079's middle-pivot QuickSort exchanges equal keys on the two scans."""
    ranges = [(0, len(queue) - 1)]
    while ranges:
        lo, hi = ranges.pop()
        if lo >= hi:
            continue
        pivot = queue[(lo + hi) // 2]["queue_seconds"]
        i, j = lo, hi
        while i <= j:
            while i < hi and queue[i]["queue_seconds"] < pivot:
                i += 1
            while j > lo and queue[j]["queue_seconds"] > pivot:
                j -= 1
            if i <= j:
                queue[i], queue[j] = queue[j], queue[i]
                i += 1
                j -= 1
        ranges.extend(((i, hi), (lo, j)))


def select_combat_actions(level, rng):
    """Resolve the ordinary scheduler's grouped alternatives once per stage."""
    from collections import defaultdict
    groups=defaultdict(list)
    packs=set()
    allowed=set()
    pack_members={}
    for wi,wave in enumerate(level["waves"]):
        for fi,frag in enumerate(wave["fragments"]):
            for ai,action in enumerate(frag["actions"]):
                if action.get("hiddenGroup") or not action["managedByScheduler"] or not action.get("isValid",True):
                    continue
                idx=wi,fi,ai
                group=action.get("randomSpawnGroupKey")
                pack=action.get("randomSpawnGroupPackKey")
                if pack:
                    pack_members[idx]=pack
                if group:
                    groups[group].append((idx,action.get("weight",0),pack))
                else:
                    allowed.add(idx)
    for candidates in groups.values():
        # RandomGroupSchedulerPreprocessor calls randomImp.UniformWithWeight.
        # Its float Range consumes one sample even when the total is zero or one.
        total=0.
        for _,weight,_ in candidates:
            total=_float(total+_float(weight))
        draw=_float(rng.value()*total)
        for idx,weight,pack in candidates:
            if draw<=_float(_float(weight)+_float(1e-5)):
                allowed.add(idx)
                if pack:
                    packs.add(pack)
                break
            draw=_float(draw-_float(weight))
    return {idx for idx in allowed if idx not in pack_members or pack_members[idx] in packs}


def _native_fragment(level, wave, index, born_delay, pre_delay, admitted=None):
    fragment = level["waves"][wave]["fragments"][index]
    queue = []
    for i, action in enumerate(fragment["actions"]):
        source = f"/waves/{wave}/fragments/{index}/actions/{i}"
        if admitted is not None and (wave,index,i) not in admitted:
            continue
        kind = {"SPAWN": "spawn", "PREVIEW_CURSOR": "preview", "DISPLAY_ENEMY_INFO": "display_enemy_info",
                "STORY":"story"}.get(action["actionType"])
        if admitted is not None and kind is None:
            kind="stage_action"
        if admitted is None and (kind is None or action["managedByScheduler"] is not True or action["hiddenGroup"]
                or action["randomSpawnGroupKey"] or action["randomSpawnGroupPackKey"]
                or action["randomType"] != "ALWAYS" or action["refreshType"] != "ALWAYS"
                or action.get("isValid", True) is not True or action.get("useExtraRoute")):
            raise ValueError(f"{source}: unresolved action or conditional group")
        count, route = action["count"], action["routeIndex"]
        if type(count) is not int or count <= 0 or (kind not in ("story","stage_action") and (type(route) is not int or not 0 <= route < len(level["routes"]))):
            raise ValueError(f"{source}: invalid count or route")
        for k in ("preDelay", "interval"):
            if type(action[k]) not in (float, int) or not isfinite(action[k]) or action[k] < 0:
                raise ValueError(f"{source}/{k}: invalid seconds")
        configured = _float(_float(pre_delay) + _float(action["preDelay"]))
        delay = 0
        if kind == "spawn":
            delay = born_delay(action["key"],route) if admitted is not None else born_delay(action["key"])
            if delay is None or delay < 0 or not isfinite(delay):
                raise ValueError(f"{source}: unresolved scheduler enemy birth delay")
        start = max(0, _float(configured - _float(delay))) if kind == "spawn" else configured

        def append(event_kind, when, occurrence, generated, nominal=None):
            queue.append(dict(kind=event_kind, source=source, wave_index=wave, fragment_index=index,
                action_index=i, occurrence_index=occurrence, queue_seconds=when,
                configured_seconds=nominal, enemy_key=action["key"], route_index=route,
                route_start=None if kind in ("story","stage_action") else dict(level["routes"][route]["startPosition"]), generated=generated,
                block_fragment=not generated and action["blockFragment"],
                instance_id=f"w{wave}-f{index}-a{i}-n{occurrence}" if event_kind == "spawn" else None))

        if kind == "spawn":
            for n in range(count):
                offset = _float(_float(n) * _float(action["interval"]))
                append(kind, _float(start + offset), n, False, _float(configured + offset))
            if action["autoPreviewRoute"]:
                preview = _float(start - 3)
                for n in range(2):
                    append("preview", _float(preview + n*_float(.3)), n, True)
            if action["autoDisplayEnemyInfo"]:
                append("display_enemy_info", start, 0, True)
        elif kind == "preview":
            for n in range(2):
                append(kind, _float(start + n*_float(.3)), n, False)
        else:
            append(kind, start, 0, False)
    _sort_queue(queue)
    return queue


def first_wave_schedule(level, born_delay, fixed_timestep, *, fragment_start_frame=0):
    """Native default-handler queue, relative to the existing first fragment action anchor."""
    return _schedule(level, born_delay, fixed_timestep, fragment_start_frame, all_waves=False)


def native_wave_schedule(level, born_delay, fixed_timestep, *, fragment_start_frame=0,
                         enemy_counts=None, wave_releases=None,story_skipped=None,admitted=None):
    """Schedule the default wave handler up to an uncertain admission or action.

    wave_releases maps an instance to its first coroutine pass outside the
    scheduler's managed-wave list, as an exact frame or an inclusive interval.
    """
    return _schedule(level, born_delay, fixed_timestep, fragment_start_frame, all_waves=True,
                     enemy_counts=enemy_counts, wave_releases=wave_releases,story_skipped=story_skipped,
                     admitted=admitted)


def _wait_ticks(seconds, dt):
    return max(1, round(_float(_float(seconds) / _float(dt))))


def _schedule(level, born_delay, fixed_timestep, fragment_start_frame, *, all_waves, enemy_counts=None,
              wave_releases=None,story_skipped=None,admitted=None):
    if type(fragment_start_frame) is not int or not isfinite(fixed_timestep) or fixed_timestep <= 0:
        raise ValueError("invalid fragment origin or fixed timestep")
    result = dict(fragments=[], waves=[], events=[], pending=None, conditions=[
        "default_wave_handler_no_skip", "ordinary_valid_delegates_and_created_enemies",
        "fixed_coroutine_updates_continue", "no_external_wave_or_action_mutations",
        "ordinary_wave_participants_no_extra_allocations_or_releases"])
    cursor = fragment_start_frame
    waves = level["waves"] if all_waves else level["waves"][:1]
    for wave_index, wave in enumerate(waves):
        start = len(result["events"])
        cursor = _wave_fragments(level, wave_index, cursor, result, born_delay, fixed_timestep,
                                 fragment_start_frame,story_skipped,admitted)
        if result["pending"]:
            break
        item = dict(wave_index=wave_index, fragments_complete_frame=cursor)
        result["waves"].append(item)
        if wave_index + 1 == len(waves):
            break
        bound = {}
        for event in result["events"][start:]:
            if event["kind"] != "spawn":
                continue
            action = wave["fragments"][event["fragment_index"]]["actions"][event["action_index"]]
            if action["dontBlockWave"] or action.get("notCountInTotal") or (
                    enemy_counts and enemy_counts(event["enemy_key"]) is False):
                continue
            key = event["instance_id"]
            released = (wave_releases or {}).get(key)
            if type(released) is int:
                released = (released, released)
            if released is not None and (not isinstance(released, (list, tuple)) or len(released) != 2
                    or any(type(v) is not int for v in released) or not event["frame"] <= released[0] <= released[1]):
                raise ValueError(f"invalid scheduler release frames for {key}")
            bound[key] = released
        wait = wave["maxTimeWaitingForNextWave"]
        delays = [wave["postDelay"], waves[wave_index + 1]["preDelay"]]
        if (type(wait) not in (int, float) or not isfinite(wait) or
                any(type(s) not in (int, float) or not isfinite(s) or s < 0 for s in delays)):
            result["pending"] = dict(wave_index=wave_index, fragment_index=0, frame=cursor,
                                     reason="invalid_wave_delay")
            break
        # Even zero seconds and an empty wave list yield one fixed update first.
        first_poll = cursor + 1
        timeout = cursor + _wait_ticks(wait, fixed_timestep) if _float(wait) >= _float(-1e-5) else None
        earliest = max([first_poll] + [v[0] for v in bound.values() if v is not None])
        latest = (max([first_poll] + [v[1] for v in bound.values()])
                  if all(v is not None for v in bound.values()) else None)
        if timeout is not None:
            earliest = min(earliest, timeout)
            latest = min(latest, timeout) if latest is not None else timeout
        following = sum(_wait_ticks(s, fixed_timestep) for s in delays)
        item["barrier"] = dict(first_poll=first_poll, timeout_frame=timeout,
                               earliest_exit_frame=earliest, release_frames=bound,
                               post_delay_ticks=_wait_ticks(delays[0], fixed_timestep),
                               next_wave_pre_delay_ticks=_wait_ticks(delays[1], fixed_timestep))
        if earliest != latest:
            result["pending"] = dict(wave_index=wave_index+1, fragment_index=0, frame=earliest+following,
                    latest_fragment_start_frame=(latest+following if latest is not None else None),
                    reason="wave_enemy_release_frames_unknown", instances=list(bound))
            break
        item["barrier"]["exit_frame"] = earliest
        cursor = earliest + following
    return result


def _wave_fragments(level, wave_index, cursor, result, born_delay, fixed_timestep, origin,story_skipped,admitted=None):
    fragments = level["waves"][wave_index]["fragments"]
    for index, fragment in enumerate(fragments):
        try:
            pre_delay = fragment["preDelay"]
            if type(pre_delay) not in (int, float) or not isfinite(pre_delay) or pre_delay < 0:
                raise ValueError(f"/waves/{wave_index}/fragments/{index}/preDelay: invalid seconds")
            if index == 0 and wave_index == 0:
                cursor -= round(pre_delay/fixed_timestep) if admitted is not None else _frame_delay(pre_delay, "/waves/0/fragments/0/preDelay")
            queue = _native_fragment(level, wave_index, index, born_delay, pre_delay,admitted)
        except ValueError as error:
            result["pending"] = dict(wave_index=wave_index, fragment_index=index, frame=cursor, reason=str(error))
            return cursor
        begin = cursor
        last_delay = 0
        counter = sum(bool(e["block_fragment"]) for e in queue)
        initial_counter = counter
        for event in queue:
            delay = event["queue_seconds"]
            if delay > last_delay:
                cursor += _wait_ticks(_float(delay-last_delay), fixed_timestep)
            if event["kind"]=="story":
                key=event.pop("enemy_key")
                if not story_skipped or not story_skipped(key):
                    result["pending"]=dict(wave_index=wave_index,fragment_index=index,frame=cursor,story_key=key,
                                           reason="story_playback_state_unknown")
                    return cursor
                event.update(kind="story_skipped",story_key=key)
            event.update(frame=cursor, relative_frame=cursor-origin)
            if event["block_fragment"]:
                counter -= 1
                event["block_counter_after_callback"] = counter
            if event["kind"] == "spawn":
                event.update(configured_frame=begin + FPS*event["configured_seconds"],
                             spawn_frame=cursor, precreate_frame=cursor, first_rendered_frame=None)
            result["events"].append(event)
            cursor += 1  # ordinary SPAWN, DISPLAY and PREVIEW each yield the shared fixed update
            last_delay = delay
        item = dict(wave_index=wave_index, fragment_index=index, start_frame=begin, actions_exhausted_frame=cursor,
                    initial_block_counter=initial_counter, final_block_counter=counter)
        result["fragments"].append(item)
        cursor += 1  # WaitWhileFixed always yields once before checking blockCounter > 0.
        item["complete_frame"] = cursor
    return cursor

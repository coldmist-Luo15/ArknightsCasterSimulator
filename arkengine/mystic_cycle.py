"""Conditional ordinary Mystic Caster cycles on the V079 actor and coroutine clocks."""
from collections import defaultdict
from fractions import Fraction

from .module_effects import equipped_phase, selected_branch, trait_board

from .animation_assets import read_skin_actions
from .baked_mount import single
from .fixed import ONE
from .frames import FPS


def baked_key_sample(key_time, playback_speed, timestep):
    """BakeMuzzleController increments binary32 time, then tests strictly greater."""
    delta = single(single(playback_speed) * single(timestep))
    if not 0 < delta or not 0 <= key_time <= 60:
        raise ValueError("动画步长或事件时间越界")
    seconds = 0.0
    key = single(key_time)
    for count in range(1, 10000):
        seconds = single(seconds + delta)
        if seconds > key:
            return count, seconds
    raise ValueError("动画事件超出支持的采样次数")


def ordinary_profile(tables, card, timing):
    key = card["operator_id"]
    if timing.get("status") != "conditional_ordinary_attack_timing":
        return dict(status="unresolved", reason=timing["reason"])
    prefab = timing["prefab"]
    birth = tables.character_birth(key)
    storage = tables.mystic_storage(key)
    if (birth["_useRealBornTimeFromAnim"] or birth["m_overrideBornTime"] != -1
            or not prefab["_waitForAttackEvent"] or storage["_forceExCharge"]):
        return dict(status="unresolved", reason="unreviewed_birth_or_charge_configuration")
    charge = prefab["_chargeAnimKey"]
    mount_fields = {2: "muzzlaTransform", 5: "specialPoint0", 6: "specialPoint1",
                    8: "specialPoint2", 9: "specialPoint3", 10: "specialPoint4"}
    numbers = [prefab["_mountPointType"], *storage["_mountPointForProjectiles"], *storage["_exMounts"]]
    if (prefab["_useMountGroup"] or prefab["_projectileDestination"] != 0
            or any(n not in mount_fields for n in numbers)):
        return dict(status="unresolved", reason="unreviewed_projectile_mounts")
    skin_name = card.get("battle_skin", "defaultskin")
    member = ("chararts" if skin_name == "defaultskin" else "skinpack") + f"/{key}.ab"
    skin = read_skin_actions(tables, member, skin_name, ("Born", "Attack", charge),
                             [(n, mount_fields[n]) for n in numbers])
    tables.source.setdefault("ordinary_animation_skins", {})[key] = dict(skin["source"],
        skeletons={view: item["skeleton_sha256"] for view, item in skin["views"].items()})
    bake_file = f"bakemuzzledata/character/{key}_{skin_name}.json"
    bake = tables.read(bake_file)["bakedAnimDatas"]
    actions = skin["views"]["front"]["actions"]
    if skin["views"]["back"]["actions"] != actions:
        return dict(status="unresolved", reason="action_timing_depends_on_actor_facing")
    keys = {}
    for name in (charge, "Attack"):
        action = actions[name]
        tracks = [view["bakedEvents"][action["clip"]] for view in (bake["Front"], bake["Back"])]
        if (action["loop"] or action["speed"] != 1 or tracks[0] != tracks[1]
                or tracks[0]["events"] != ["OnAttack"] or len(tracks[0]["eventTime"]) != 1):
            return dict(status="unresolved", reason="unreviewed_ordinary_bake_track")
        keys[name] = dict(clip=action["clip"], key_seconds=tracks[0]["eventTime"][0],
                          duration_seconds=action["duration"])
    timestep = tables.fixed_timestep()
    step_raw = int(timestep * ONE)
    born = actions["Born"]
    if born["loop"] or born["speed"] != 1 or born["duration"] != 1:
        return dict(status="unresolved", reason="unreviewed_birth_animation_normalization")
    parents = {}
    for face, item in skin["views"].items():
        chains = list(item["mount_parents"].values())
        if any(chain != chains[0] for chain in chains[1:]):
            return dict(status="unresolved", reason="nonuniform_launch_mount_parent")
        parents[face] = chains[0]
    graphic = tables._component_fields("characters", key, ("m_LocalPosition", "m_LocalRotation", "m_LocalScale"),
                                       "character_graphic", component_type="Transform", children=("Graphic",))
    capacity = storage["_maxChargeTimes"]
    if selected_branch(tables, card) == "msc-x":
        unit = tables.read("excel/character_table.json")[key]
        capacity = int(trait_board(unit, equipped_phase(tables, card), card)["times"])
    birth_raw = int(born["duration"] * ONE)
    return dict(status="ordinary_profile", battle_skin=skin_name, bake_file=bake_file,
                step_seconds=timestep, step_q32=step_raw,
                birth_seconds=born["duration"], birth_updates=(birth_raw + step_raw - 1) // step_raw,
                charge_action=charge, actions=keys, ordinary_capacity=capacity,
                ordinary_projectile=prefab["_projectileKey"], stored_projectile=storage["_projectileKey"],
                ordinary_mount=prefab["_mountPointType"], stored_mounts=storage["_mountPointForProjectiles"],
                mount_parents=parents, graphic_parent=dict(position=graphic["m_LocalPosition"],
                    rotation=graphic["m_LocalRotation"], scale=graphic["m_LocalScale"]))


def first_volley(profile, timing, placement, events, unknown_instances, last_frame, *, priority=None):
    """Project through the first target event, using prior completed motion samples."""
    if profile["status"] != "ordinary_profile":
        return profile
    contacts = defaultdict(list)
    for event in events:
        if (event["kind"] == "route_end_in_range" or
                (event["kind"] in ("attack_range_enter", "attack_range_exit") and
                 event.get("operator_id") == placement["operator_id"])):
            contacts[event["frame"]].append(event)
    begin = placement["frame"]
    birth_end = begin + profile["birth_updates"]
    first_cast = birth_end + 1
    result = dict(profile=profile, conditions=[
        "actor_first_BORN_update_on_frame_after_input",
        "ordinary_reset_step_and_one_animation_second_BORN_timer",
        "first_idle_search_on_next_actor_update_and_immediate_attack_entry",
        "valid_baked_track_starts_at_zero_and_uses_latched_cast_speed",
        "all_in_range_instances_selectable_and_previous_motion_sample_available",
        "no_skill_buff_interrupt_or_external_target_change",
        "continued_cast_starts_when_post_delay_ends_and_cooldown_is_ready",
        "late_baked_event_consumed_on_next_coroutine_pass"],
        born_timer_due_frame=birth_end if birth_end <= last_frame else None, scenarios=[])
    log = []
    inside = set()
    motion_frame = -1
    stored = 0
    cast = first_cast
    stop = "horizon"
    while cast <= last_frame:
        while motion_frame < cast - 1:
            motion_frame += 1
            for event in contacts.get(motion_frame, ()):
                if event["kind"] == "attack_range_enter":
                    inside.add(event["instance_id"])
                else:
                    inside.discard(event["instance_id"])
        if any(frame <= motion_frame for frame in unknown_instances.values()):
            stop = "incomplete_target_positions"
            break
        ranked = priority(inside, cast) if len(inside)>1 and priority else None
        if len(inside) > 1 and not ranked:
            stop = "multiple_targets_require_priority"
            break
        if not inside and stored >= profile["ordinary_capacity"]:
            future_entry = any(e["kind"] == "attack_range_enter"
                               for f, rows in contacts.items() if motion_frame < f < last_frame for e in rows)
            unknown_before_end = any(f < last_frame for f in unknown_instances.values())
            stop = ("full_normal_storage_requires_extra_charge_check" if future_entry or unknown_before_end
                    else "full_normal_storage_no_target_through_horizon")
            break
        target = ranked["target"] if ranked else next(iter(inside), None)
        action = "Attack" if target else profile["charge_action"]
        track = profile["actions"][action]
        mode = timing["stored"] if stored else timing["empty"]
        duration = Fraction(mode["nominal_frames"]) / FPS
        speed = float(Fraction(str(track["duration_seconds"])) / duration)
        key_ticks, anim_time = baked_key_sample(track["key_seconds"], speed, profile["step_seconds"])
        # Late actor update samples even on the admission frame. The next coroutine pass consumes the flag.
        bake_frame = cast + key_ticks - 1
        key_frame = bake_frame + 1
        item = dict(start_frame=cast, action=action, target=target, ordinary_stored_at_start=stored,
                    attack_speed=mode["attack_speed"], fixed_wait_frames=mode["fixed_wait_frames"],
                    first_bake_tick_delay=0, apply_delay_after_bake=1, ticks_to_key=key_ticks,
                    baked_event_frame=bake_frame if bake_frame <= last_frame else None, animation_seconds_at_key=anim_time,
                    on_attack_frame=key_frame if key_frame <= last_frame else None)
        log.append(item)
        if ranked:
            item["target_priority"] = ranked
        if key_frame > last_frame:
            break
        if target:
            item["ordinary_stored_available_for_volley"] = stored
            stop = "first_targeted_on_attack"
            break
        stored += 1
        item["ordinary_stored_after_charge"] = stored
        # get_postDelay uses the duration captured at cast start, including the first speed transition.
        cast += mode["fixed_wait_frames"]
    result["scenarios"].append(dict(first_bake_tick_delay=0, casts=log, stop=stop))
    return result

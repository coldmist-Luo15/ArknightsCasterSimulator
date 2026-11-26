"""First ordinary volley against a freely moving receiver on a verified scene."""
from math import floor, sqrt

from .animation_assets import read_skin_actions
from .attack_range import RangeContacts
from .baked_mount import baked_position, single, transform_point
from .opening_scene import bundle_environment
from .projectile_scene import enemy_hit_mount, projectile_profiles, stage_tile_positions
from .trace_motion import ordinary_trace_step


def _settled_cast_facing(placement,samples,starts,launch,dt,turn_seconds,previous_face,previous_sign=None):
    faces=set()
    signs=set()
    for start in starts:
        # Allow the fixed tween one complete update after its duration.
        if (launch-start-1)*dt<turn_seconds:
            return None
        for tick in (start-1,start):
            position=samples.get(tick)
            if position is None:
                return None
            dy=single(single(position[0])-single(placement["row"]))
            dx=single(single(position[1])-single(placement["col"]))
            squared=single(single(dx*dx)+single(dy*dy))
            if squared<=0.04000000283122063:
                return None
            magnitude=single(sqrt(squared))
            # CharacterAnimator adds 0.25 to the horizontal direction scores.
            vertical=single(abs(dy)/magnitude)
            horizontal=single(single(abs(dx)/magnitude)+0.25)
            if vertical>horizontal:
                faces.add("back" if dy>0 else "front")
            elif horizontal>vertical and previous_face:
                faces.add(previous_face)
            else:
                return None
            if abs(dx)>single(single(0.2)+single(1e-5)):
                signs.add(1 if dx>0 else -1)
            elif abs(dy)>abs(dx) and abs(dx)<single(single(0.2)-single(1e-5)) and previous_sign is not None:
                # A predominantly vertical aim inside the horizontal dead zone keeps L/R.
                signs.add(previous_sign)
            else:
                signs.update((-1,1))
    return (tuple(faces),sorted(signs)) if len(faces)==1 else None


def _ordinary_mover(variant):
    motion, life = variant["movement"], variant["lifecycle"]
    return (variant["movement_script_id"] == "24e93061e69052d3a8d4fffb3590e907"
            and motion["_moveType"] == 1 and motion["_speed"] == 10
            and not any(value for key, value in motion.items() if key not in ("_moveType", "_speed"))
            and not life["_randomDelayToBorn"] and not life["_randomDelayToBornKey"]
            and life["_alwaysHitTraceTargetInTheEnd"] and not life["_alwaysHitTraceTargetWhenReached"]
            and not life["_stopWhenSourceInvalid"])


def _receiver_positions(target, history, launch, end):
    motion = RangeContacts()
    samples = {}
    for frame in range(end + 1):
        for kind, fields in history.get(frame, ()):
            if fields["instance_id"] == target:
                motion.update(frame, kind, fields)
                if kind == "route_end_in_range":
                    motion.positions.pop(target, None)
        if frame >= launch:
            samples[frame] = motion.positions.get(target)
    return samples


def first_projectiles(tables, scene, placement, cycle, spawned, history, end, *, round_position=True):
    profile = cycle.get("profile")
    if not profile:
        return dict(status="unresolved", reason=cycle.get("reason", "missing_attack_profile"))
    scenarios = []
    for schedule in cycle["scenarios"]:
        casts = schedule["casts"]
        if schedule["stop"] == "first_targeted_on_attack":
            shot = casts[-1]
            signature = (shot["target"], shot["on_attack_frame"], shot["ordinary_stored_at_start"],
                         shot["animation_seconds_at_key"])
            if signature not in scenarios:
                scenarios.append(signature)
    if not scenarios:
        stops = {s["stop"] for s in cycle["scenarios"]} - {"horizon", "full_normal_storage_no_target_through_horizon"}
        if stops:
            return dict(status="unresolved", reason=sorted(stops)[0], attack_stops=sorted(stops))
        return dict(status="no_projected_target_event_within_horizon")
    return ordinary_projectiles_for_releases(tables, scene, placement, profile, scenarios, spawned, history, end,
                                             round_position=round_position)


def _release_layout(tables, profile, mode):
    if mode not in ("Default", "S3"):
        return None
    cache = "ebnhlz_release_layout", mode, profile["battle_skin"]
    key = "char_4046_ebnhlz"
    facing_fields=("_faceToTarget","_faceToDefault","_faceToFront","_faceToFirstCastTarget",
                   "_checkFaceToFirstCastTargetDownAttack","_forceFaceToDirection")
    attack = tables._component_fields("characters", key, ("_projectileKey", "_mountPointType", "_animKey",
        "_useMountGroup", "_projectileDestination")+facing_fields, "release_attack_" + mode,
        children=("Modes", mode, "Body", "Attack"))
    storage = tables._component_fields("characters", key, ("_projectileKey", "_mountPointForProjectiles",
        "_exProjectileKey", "_exMounts", "_forceExCharge"), "release_storage_" + mode,
        children=("Trait", "ChargeAttackGroup", "ExChargeAttack" + mode))
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    if attack["_useMountGroup"] or attack["_projectileDestination"] or bool(storage["_forceExCharge"]) != (mode == "S3"):
        return None
    facing_seconds=None
    if mode == "Default":
        clip = profile["actions"]["Attack"]["clip"]
    else:
        skin = profile["battle_skin"]
        member = ("chararts" if skin == "defaultskin" else "skinpack") + f"/{key}.ab"
        action = attack["_animKey"]
        data = read_skin_actions(tables, member, skin, [action])
        front = data["views"]["front"]["actions"][action]
        if front != data["views"]["back"]["actions"][action] or front["loop"] or front["speed"] != 1:
            return None
        clip = front["clip"]
        if tuple(attack[k] for k in facing_fields)==(1,0,0,0,0,0):
            source=data["source"]
            raw,_=tables.verified_bundle(source["member"])
            root=bundle_environment(raw).container[source["container"]].deref()
            animator=root.assets_file.objects[source["component_path_id"]]
            if animator.serialized_type.old_type_hash.hex()=="3f542986ad873354e56cde5b742ce778":
                switch=animator.read_typetree()["_faceSwitcher"]
                if switch["m_FileID"]==0 and switch["m_PathID"]:
                    controller=root.assets_file.objects[switch["m_PathID"]]
                    if controller.serialized_type.old_type_hash.hex()=="12ef59bd4b354194ed3ce30e151da633":
                        facing_seconds=controller.read_typetree()["_switchTime"]
    layout = dict(clip=clip, main=(attack["_projectileKey"], [attack["_mountPointType"]]),
                  stored=(storage["_projectileKey"], storage["_mountPointForProjectiles"]),
                  extra=(storage["_exProjectileKey"], storage["_exMounts"]),facing_seconds=facing_seconds)
    tables._prefab_data[cache] = layout
    return layout


def ordinary_projectiles_for_releases(tables, scene, placement, profile, scenarios, spawned, history, end,
                                      *, mode="Default", extra_count=0, round_position=True, cast_starts=None,previous_face=None,
                                      previous_sign=None,live_positions=None):
    """Trace the selected mode's main, ordinary stored and eligible extra packets."""
    layout = _release_layout(tables, profile, mode)
    if layout is None:
        return dict(status="unresolved", reason="unreviewed_mystic_release_layout")
    return trace_projectile_releases(tables, scene, placement, profile, scenarios, spawned, history, end,
        layout=layout, mode=mode, extra_count=extra_count, round_position=round_position,
        cast_starts=cast_starts,previous_face=previous_face,previous_sign=previous_sign,live_positions=live_positions)


def trace_projectile_releases(tables, scene, placement, profile, scenarios, spawned, history, end,
                              *, layout, mode, extra_count=0, round_position=True, cast_starts=None,previous_face=None,
                              previous_sign=None,live_positions=None):
    """Trace verified main and optional additional mounts through the same stage geometry."""
    tiles = stage_tile_positions(tables, scene)
    base = tiles[placement["row"], placement["col"]]
    if base["locate_height_offset"]:
        return dict(status="unresolved", reason="additional_tile_locate_height")
    keys = {layout["main"][0]}
    if "stored" in layout:
        keys.add(layout["stored"][0])
    if extra_count:
        keys.add(layout["extra"][0])
    variants = projectile_profiles(tables, sorted(keys))
    if not all(_ordinary_mover(v) for choices in variants.values() for v in choices.values()):
        return dict(status="unresolved", reason="outside_speed_10_trace_profile", prefabs=variants)
    baked = tables.read(profile["bake_file"])["bakedAnimDatas"]
    dt = profile["step_seconds"]
    results = []
    for target, launch, count, anim_time in scenarios:
        enemy = spawned[target]["enemy"]
        if (extra_count or mode == "S3") and scene.enemy_level(enemy) not in ("ELITE", "BOSS"):
            return dict(status="unresolved", reason="release_outside_elite_or_boss_target_mask")
        if not (type(count) is int and 0 <= count <= len(layout.get("stored", (None, []))[1]) and
                type(extra_count) is int and 0 <= extra_count <= len(layout.get("extra", (None, []))[1])):
            return dict(status="unresolved", reason="release_exceeds_mode_mount_count")
        source = scene.enemies[enemy]["prefabKey"]
        prefab = source["m_value"] if source["m_defined"] and source["m_value"] else enemy
        receiver = enemy_hit_mount(tables, prefab)
        if not receiver:
            return dict(status="unresolved", reason="moving_or_unreviewed_receiver_mount", prefab=prefab)
        route_index = spawned[target].get("route")
        flying = (route_index is not None and scene.level["routes"][route_index]["motionMode"] == "FLY")
        fixed_flight_height = (flying and prefab == "enemy_1355_mrfly_2"
                               and scene.stage["stageId"] == "act28side_ex07" and not receiver["spine_height_update"])
        if (flying or receiver["height"]) and not fixed_flight_height:
            return dict(status="unresolved", reason="unreviewed_receiver_flight_height", prefab=prefab)
        offset = transform_point(baked_position(receiver["hit_track"], 0, dt), receiver["parents"])
        if abs(offset[0]) > 1e-5:
            return dict(status="unresolved", reason="receiver_facing_changes_hit_mount")
        start = min(cast_starts.get((target, launch), [launch])) if cast_starts else launch
        samples = (_receiver_positions(target, history, max(0, start - 1), end)
                   if live_positions is None else {launch: live_positions.get(target)})
        if samples[launch] is None:
            return dict(status="unresolved", reason="receiver_missing_at_launch", target=target)
        dx = samples[launch][1] - placement["col"]
        signs = [1 if dx > 0 else -1] if abs(dx) > 1e-6 else [-1, 1]
        if cast_starts:
            # A turn at cast start can retain the other facing when the target crosses the caster.
            for frame in cast_starts[target, launch]:
                for tick in (frame - 1, frame):
                    position = samples.get(tick)
                    if position is None or (position[1] - placement["col"]) * dx <= 0:
                        signs = [-1, 1]
        faces=("front","back")
        if cast_starts and layout.get("facing_seconds"):
            aim=_settled_cast_facing(placement,samples,cast_starts[target,launch],launch,dt,
                                     layout["facing_seconds"],previous_face,previous_sign)
            if aim is not None:
                faces,signs=aim
        mounts = [(kind, layout[kind][0], mount) for kind, n in (("main", 1), ("stored", count), ("extra", extra_count))
                  if n for mount in layout[kind][1][:n]]
        # Fresh projectiles get a higher BObject UID than an earlier allocated receiver.
        # The ordinary pass snapshots live objects in ascending UID before invoking ticks.
        born = spawned[target].get("spawn_frame")
        lags = (0,) if born is not None and born < launch else (0, 1)
        for face in faces:
            tracks = baked[face.title()]["bakedMPDatas"].get(layout["clip"], {})
            if any(str(mount) not in tracks for _, _, mount in mounts):
                return dict(status="unresolved", reason="mode_release_mount_missing_bake_track")
            for sign in signs:
                shots = []
                for kind, key, mount in mounts:
                    local = baked_position(tracks[str(mount)], anim_time, dt)
                    value = transform_point((sign*local[0], local[1], sign*local[2]),
                                            [*profile["mount_parents"][face], profile["graphic_parent"]])
                    origin = tuple(base["position"][k] + value[i] for i, k in enumerate("xyz"))
                    arrivals = []
                    for lag in lags:
                        pos = origin
                        arrival = None
                        stop = "horizon"
                        for frame in range(launch + 1, end + 1 if live_positions is None else launch + 1):
                            sample = samples[frame - lag]
                            if sample is None:
                                stop = "receiver_missing"
                                break
                            row, col = sample
                            tile = tiles.get((floor(row + 0.5), floor(col + 0.5)))
                            if not tile or tile["locate_height_offset"] or (not fixed_flight_height and tile["position"]["z"] != 0):
                                stop = "receiver_left_flat_ground"
                                break
                            dest = (col + offset[0], row + offset[1], offset[2] - receiver["height"])
                            step = ordinary_trace_step(pos, dest, effective_dt=dt, round_position=round_position)
                            pos = step.position
                            if step.reached:
                                arrival = frame
                                stop = "ordinary_reach"
                                break
                        arrivals.append(dict(receiver_sample_lag=lag, frame=arrival, stop=stop))
                    shot=dict(key=key, packet=kind, mount=mount,origin_map_xyz=origin,arrivals=arrivals)
                    if live_positions is not None:
                        shot["receiver_offset_xyz"]=(offset[0],offset[1],offset[2]-receiver["height"])
                    shots.append(shot)
                results.append(dict(target=target, mode=mode, animation_clip=layout["clip"],
                                    launch_frame=launch, first_movement_frame=launch+1,
                                    receiver_spawn_frame=born, face=face, x_sign=sign, shots=shots,
                                    **({"receiver_flight_height":receiver["height"]} if fixed_flight_height else {})))
    return dict(status="conditional_projectile_reach", conditions=[
        "main_and_stored_abilities_release_together_on_targeted_key" if "stored" in layout else "single_targeted_main_projectile",
        "no_animation_mixing_or_runtime_graphic_transform_override",
        "ordinary_tile_locate_height_static_ground_or_reviewed_constant_airborne_height",
        "first_effective_movement_on_next_object_update_without_delay",
        "valid_trace_target_no_early_stop_or_interception",
        "ordinary_speed_10_mover"],
        position_rounding=round_position, prefabs=variants, scenarios=results)

"""Flight from an admitted projectile's first active movement tick to spatial reach."""
from math import isfinite

from .caster_prefabs import _prefab
from .baked_mount import single
from .trace_motion import ordinary_trace_step, paracurve_horizontal_step


def _movement(prefab):
    matches = [c for c in prefab["components"] if c["enabled"] and c["active"] and not c["path"]
               and c["script"] and (c["script"].endswith("Movement") or c["script"]=="AttachToTarget")]
    return matches[0] if len(matches) == 1 else None


def _attached_reach(movements,event):
    fields = movements[0]["fields"]
    flags = ("_checkReached","_immediatelyReach","_keepUpdate","_followTarget","_onlyReachedInTime")
    result = dict(projectile=event["projectile"],movement="AttachToTarget",
                  first_move_frame=event["frame"],scope="first_eligible_reach_check_after_start_delay")
    if (any(m["script"]!="AttachToTarget" or any(m["fields"][k]!=fields[k] for k in flags) for m in movements)
            or not fields["_checkReached"] or fields["_onlyReachedInTime"]):
        return dict(**result,status="unresolved",reason="attachment_reach_options")
    if fields["_immediatelyReach"]:
        return dict(**result,status="reached",reach_frame=event["frame"],predicate="immediate_reach_callback")
    if fields["_keepUpdate"] or fields["_followTarget"]:
        return dict(**result,status="unresolved",reason="attachment_position_update")
    if "attached_position" not in event or "cached_target_at_init" not in event:
        return dict(**result,status="unresolved",reason="attachment_stored_position_and_cached_destination_required")
    points = [event[k] for k in ("attached_position","cached_target_at_init")]
    if not all(len(p)==3 and all(type(x) in (int,float) and isfinite(x) for x in p) for p in points):
        raise ValueError("附着弹体需要初始化后实际写回的坐标和缓存目的坐标")
    delta = [single(single(x)-single(y)) for x,y in zip(*points)]
    x,y,z = (single(d*d) for d in delta)
    hit = single(single(x+y)+z) <= single(single(.1)**2)
    return dict(**result,status="reached" if hit else "outside_reach_at_first_check",
                reach_frame=event["frame"] if hit else None,predicate="cached_3d_distance")


def projectile_reach(tables, evidence, event):
    key = event["projectile"]
    if key not in evidence["projectiles"]:
        raise ValueError("当前干员、技能或模组的预制体依赖中没有该弹体")
    variants = evidence["projectiles"][key]
    movements = [_movement(p) for p in variants.values()]
    if not movements or any(m is None for m in movements):
        return dict(status="unresolved", reason="projectile_movement_binding", projectile=key)
    mover = movements[0]
    script = mover["script"]
    if script=="AttachToTarget":
        return _attached_reach(movements,event)
    if script not in ("AdvancedMovement", "ParacurveMovement"):
        return dict(status="unresolved", reason="movement_class", movement=script)
    prototype = "projectile_amgoat" if script == "AdvancedMovement" else "projectile_amgoat_s3"
    reference = _movement(_prefab(tables, "[uc]projectiles", prototype))["fields"]
    fields = mover["fields"]
    # These fields control rendering or events after the spatial predicate.
    # Height is independent of XY only for the ordinary paracurve branch.
    outside_reach = {"_rotateToTarget","_exactMatchCameraForward","_fixOnProjectileReached","_forceReachedWhenTimeup"}
    if script == "AdvancedMovement":
        outside_reach.update(("_clearTraceTargetWhenReached","_updateDelayTimeOnlyOnce","_delayAfterReached"))
    else:
        outside_reach.add("_raiseHeight")
    def settings(data):
        return {k:v for k,v in data.items() if k.startswith("_") and k not in outside_reach and k != "_speed"}
    if any(m["script"] != script or m["fields"]["_speed"] != fields["_speed"]
           or settings(m["fields"]) != settings(reference) for m in movements):
        return dict(status="unresolved", reason="unreviewed_movement_settings", projectile=key, movement=script)
    round_position = event["round_position"]
    frame = event["frame"]
    samples = event["target_mounts"]
    pos = event["origin"]
    def valid_point(v):
        return len(v)==3 and all(type(n) in (int,float) and isfinite(n) for n in v)
    if (type(round_position) is not bool or not samples or not valid_point(pos)
            or isinstance(samples,(list,tuple)) and not all(map(valid_point,samples))):
        raise ValueError("飞行需要起点、逐活动帧三维目标挂点和取整模式")
    dt, speed = tables.fixed_timestep(), fields["_speed"]
    reached = None
    tick = 0
    for tick, target in enumerate(samples, 1):
        if not valid_point(target):
            raise ValueError("飞行中的目标挂点需要三个有效坐标")
        if script == "ParacurveMovement":
            pos, at_mount = paracurve_horizontal_step(pos, target, speed, dt, round_position)
        else:
            step = ordinary_trace_step(pos, target, effective_dt=dt, round_position=round_position, speed=speed)
            pos, at_mount = step.position, step.reached
        if at_mount:
            reached = tick
            break
    if not tick:
        raise ValueError("飞行至少需要一次目标挂点")
    return dict(status="reached" if reached is not None else "in_flight_at_horizon",
                scope="active_movement_until_spatial_reach", projectile=key, movement=script, speed=speed,
                first_move_frame=frame, reach_frame=frame+reached-1 if reached is not None else None,
                conditions=["valid_target_and_source", "no_speed_modifier_timeout_stop_or_early_collision"])

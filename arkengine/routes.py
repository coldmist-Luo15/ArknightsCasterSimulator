"""Compile the pinned stage's route commands without choosing a runtime path or clock."""
from copy import deepcopy
from math import isfinite


def _number(value, field):
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{field} 需要有限数值")
    return value


def _offset(vector, field):
    return {axis: _number(vector[axis], f"{field}/{axis}") for axis in ("x", "y")}


def _cell(scene, position):
    # Check in game coordinates; the scene owns the inverted array row convention.
    scene.tile(**position)
    return dict(position)


def _command(scene, cp, source):
    kind = cp["type"]
    seconds = _number(cp["time"], f"{source}/time")
    if seconds < 0:
        raise ValueError(f"{source}/time 为负数")
    command = dict(kind=kind, source=source)
    if kind in {"MOVE", "APPEAR_AT_POS"} and seconds == 0:
        distance = _number(cp["reachDistance"], f"{source}/reachDistance")
        if distance < 0 or type(cp["randomizeReachOffset"]) is not bool:
            raise ValueError(f"{source} 到达条件无效")
        return dict(command, target=_cell(scene, cp["position"]),
                    reach_offset=_offset(cp["reachOffset"], source + "/reachOffset"),
                    randomize_reach_offset=cp["randomizeReachOffset"], reach_distance=distance)
    unused_target = (cp["position"] == {"row": 0, "col": 0}
                     and cp["reachOffset"] == {"x": 0, "y": 0}
                     and cp["randomizeReachOffset"] is False and cp["reachDistance"] == 0)
    if unused_target:
        if kind in {"WAIT_FOR_SECONDS", "WAIT_CURRENT_FRAGMENT_TIME", "WAIT_CURRENT_WAVE_TIME"}:
            return dict(command, seconds=seconds)
        if kind == "DISAPPEAR" and seconds == 0:
            return command
    # Preserve the exact row so execution can stop here after earlier commands ran.
    return dict(kind="UNSUPPORTED", source=source, raw=deepcopy(cp))


def compile_route(scene, route_index, enemy_id):
    """Join an enemy and ordinary route. All numeric values here are table values.

    Timed waits have a clock specific to their kind. A MOVE target is a
    checkpoint; intervening tiles have to be decided by the movement system.
    """
    routes = scene.level["routes"]
    if type(route_index) is not int or not 0 <= route_index < len(routes):
        raise ValueError(f"未知路线 {route_index}")
    if enemy_id not in scene.enemies:
        raise ValueError(f"关卡未装载敌人 {enemy_id}")
    enemy = scene.enemies[enemy_id]
    speed = enemy["attributes"]["moveSpeed"]
    source = f"/routes/{route_index}"
    route = routes[route_index]
    flags = {k: route[k] for k in ("allowDiagonalMove", "visitEveryTileCenter",
                                    "visitEveryNodeCenter", "visitEveryCheckPoint")}
    if any(type(v) is not bool for v in flags.values()):
        raise ValueError(f"{source} 路线标志无效")
    commands = [_command(scene, cp, f"{source}/checkpoints/{i}")
                for i, cp in enumerate(route["checkpoints"] or [])]
    commands.append(dict(kind="END", source=source + "/endPosition",
                         target=_cell(scene, route["endPosition"])))
    return dict(route_index=route_index, enemy_id=enemy_id, source=source,
                motion_mode=route["motionMode"],
                enemy_motion=enemy["motion"]["m_value"] if enemy["motion"]["m_defined"] else None,
                base_move_speed=_number(speed["m_value"], "moveSpeed") if speed["m_defined"] else None,
                level_move_multiplier=_number(scene.level["options"]["moveMultiplier"], "moveMultiplier"),
                start=_cell(scene, route["startPosition"]),
                spawn_offset=_offset(route["spawnOffset"], source + "/spawnOffset"),
                spawn_random_range=_offset(route["spawnRandomRange"], source + "/spawnRandomRange"),
                flags=flags, commands=commands)

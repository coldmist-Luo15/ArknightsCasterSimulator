"""Ordinary enemy birth and checkpoint updates, on the project's nominal 30 Hz clock."""
from fractions import Fraction
from math import ceil, isfinite

from .baked_mount import single
from .fixed import ONE
from .frames import FPS


ABSOLUTE_WAITS={"WAIT_CURRENT_WAVE_TIME","WAIT_CURRENT_FRAGMENT_TIME"}
TIMED_WAITS={"WAIT_FOR_SECONDS",*ABSOLUTE_WAITS}


def route_clock_snapshots(level,schedule,timestep):
    step=int(timestep*ONE)
    waves={part["wave_index"]:part["start_frame"]*step for part in schedule["fragments"]
           if part["fragment_index"]==0}
    clocks={}
    for part in schedule["fragments"]:
        wave,fragment=part["wave_index"],part["fragment_index"]
        pre_delay=level["waves"][wave]["fragments"][fragment]["preDelay"]
        clocks[wave,fragment]={"WAIT_CURRENT_WAVE_TIME":waves[wave],
                              "WAIT_CURRENT_FRAGMENT_TIME":part["start_frame"]*step+int(single(pre_delay)*ONE)}
    return clocks


def _update_count(seconds):
    # Check completion after an update, even when OnBegin initialized a zero timer.
    return max(1, ceil(Fraction(str(seconds)) * FPS))


def birth_timer(fields):
    """Age of a normal BornState in active updates, independent of spawn scheduling."""
    delay = fields.get("_delayToBorn")
    if type(delay) not in (float, int) or not isfinite(delay):
        return dict(status="unresolved", reason="missing_birth_delay")
    if delay > 0 and fields.get("_onlyDelayToBornOnTileStart") != 0:
        return dict(status="unresolved", reason="birth_delay_tile_condition")
    # MathUtil.GT(delay, 0f) uses this V079 binary32 epsilon.
    seconds = delay if delay > 9.999999747378752e-06 else 0
    return dict(status="conditional_projection", timer_seconds=seconds,
                first_default_request_update=_update_count(seconds),
                first_move_frame=None)


def checkpoint_sequence(commands, first_index, position, *, timestep=None, clock_frame=None, clocks=None):
    """Enter the first command at offset 0. Later offsets count eligible cursor ticks.

    Only ordinary sequential, continuously ticking checkpoints are projected.
    Preserve the reached MOVE position, including its residual distance from center.
    """
    if not 0 <= first_index < len(commands) or commands[-1]["kind"] != "END":
        raise ValueError("检查点序列缺少结束位置或起始索引越界")
    step = None if timestep is None else int(timestep*ONE)
    if step is not None and step<=0:
        raise ValueError("检查点计时步长必须为正数")
    offset = 0
    disappeared = False
    events = []
    for cmd in commands[first_index:]:
        kind = cmd["kind"]
        was_disappeared = disappeared
        if kind == "DISAPPEAR":
            disappeared = True
            ticks = 1
        elif kind == "WAIT_FOR_SECONDS":
            if step is None:
                ticks = _update_count(cmd["seconds"])
            else:
                remaining = int(single(cmd["seconds"])*ONE)
                ticks = max(1, (remaining+step-1)//step)
        elif kind in ABSOLUTE_WAITS and step is not None and clock_frame is not None and clocks:
            deadline=clocks[kind]+int(single(cmd["seconds"])*ONE)
            ticks=max(1,(deadline+step-1)//step-clock_frame-offset)
        elif kind == "APPEAR_AT_POS" and not cmd["randomize_reach_offset"]:
            position = dict(row=cmd["target"]["row"] + cmd["reach_offset"]["y"],
                            col=cmd["target"]["col"] + cmd["reach_offset"]["x"])
            disappeared = False
            ticks = 1
        else:
            break
        events.append(dict(offset=offset, event="checkpoint_begin", command=kind,
                           source=cmd["source"], position=dict(position), disappeared=disappeared))
        offset += ticks
        if was_disappeared and not disappeared:
            # Enemy.OnTick updates the state machine before its cursor. A still
            # active DisappearState first sees the cleared flag on the next tick.
            events.append(dict(offset=offset, event="disappear_default_requested", command=kind,
                               source=cmd["source"], position=dict(position), disappeared=False,
                               state="DEFAULT"))
        events.append(dict(offset=offset, event="checkpoint_reached", command=kind,
                           source=cmd["source"], position=dict(position), disappeared=disappeared))
    return dict(events=events, next_command=cmd, next_offset=offset,
                timebase=("eligible_cursor_updates_at_nominal_30Hz" if step is None else "eligible_cursor_updates_q32"))

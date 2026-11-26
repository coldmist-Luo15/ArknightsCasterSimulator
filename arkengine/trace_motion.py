"""V079 movement updates for ordinary homing and paracurve projectiles with a valid mount."""
from dataclasses import dataclass
from math import isfinite, sqrt
import struct


def _f(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def _squared(v):
    return _f(_f(_f(v[0]*v[0]) + _f(v[1]*v[1])) + _f(v[2]*v[2]))


@dataclass(frozen=True)
class MoveSample:
    position: tuple[float, float, float]
    reached: bool


def paracurve_reach_ticks(origin, target, *, speed, effective_dt, round_position, limit):
    """V079 ordinary ParacurveMovement against a stationary map-space target, before timeout."""
    if (len(origin) != 3 or len(target) != 3 or not all(isfinite(x) for x in (*origin, *target))
            or not (isfinite(speed) and speed > 0 and 0 < effective_dt <= 1)
            or type(round_position) is not bool or type(limit) is not int or limit < 0):
        raise ValueError("抛射物需要有效三维坐标、正速度步长与非负搜索帧数")
    position = origin[:2]
    for tick in range(1, limit + 1):
        position, reached = paracurve_horizontal_step(position, target, speed, effective_dt, round_position)
        if reached:
            return tick
    return None


def paracurve_horizontal_step(position, mount, speed, dt, round_position):
    """One ordinary V079 movement update and its XY reach predicate, with a valid current mount."""
    pos = tuple(map(_f, position[:2]))
    goal = tuple(map(_f, mount[:2]))
    dt, speed = _f(dt), _f(speed)
    dx, dy = (_f(b-a) for a,b in zip(pos, goal))
    distance = _f(sqrt(_f(_f(dx*dx) + _f(dy*dy))))
    if distance <= _f(speed*dt):
        stored = goal
    elif distance > _f(1e-5):
        stored = tuple(_f(p + _f(_f(_f(d/distance)*dt)*speed)) for p,d in zip(pos, (dx,dy)))
    else:
        stored = pos
    if round_position:
        stored = tuple(_f(round(p*10000)/10000) for p in stored)
    dx, dy = (_f(b-a) for a,b in zip(stored,goal))
    reached = _f(_f(dx*dx) + _f(dy*dy)) <= _f(_f(.1)**2)
    return stored, reached


def ordinary_trace_step(position, target, *, effective_dt: float, round_position: bool, speed=10.0) -> MoveSample:
    """Compare the read-back position to this step's target, before DealReached."""
    if not (type(round_position) is bool and 0 < effective_dt <= 1 and isfinite(speed) and speed > 0):
        raise ValueError('Expected known rounding mode and positive small effective dt')
    def vec(v):
        if len(v) != 3 or not all(isfinite(x) and abs(x) <= 10000 for x in v):
            raise ValueError('Expected three finite map coordinates in the tested numeric range')
        return tuple(map(_f, v))
    p, t = vec(position), vec(target)
    dt = _f(effective_dt); delta = tuple(_f(b-a) for a,b in zip(p,t))
    distance = _f(sqrt(_squared(delta)))
    speed = _f(speed)
    if distance <= _f(dt*speed):
        proposal = t
    elif distance > _f(1e-5):
        proposal = tuple(_f(a+_f(speed*_f(dt*_f(d/distance)))) for a,d in zip(p,delta))
    else:
        raise ValueError('Outside the checked normalization branch')
    stored = tuple(_f(round(x*10000)/10000) for x in proposal) if round_position else proposal
    residual = tuple(_f(b-a) for a,b in zip(stored,t))
    return MoveSample(stored, _squared(residual) <= _f(_f(.1)**2))

"""Unweighted damage curve of the V079 Ho'olheyak tornado projectile."""
from .baked_mount import single
from .fixed import ONE, mul
from .skill_recovery import _divide
from .volley_damage import _raw_f32


def tornado_scale(curve, age, life, board):
    start, knee, end = curve
    slope = start["outSlope"]
    if (any(k["weightedMode"] for k in curve) or (start["time"],start["value"],knee["value"],end["value"],
            end["time"]) != (0,0,1,1,1) or not 0 < knee["time"] < 1 or slope != knee["inSlope"]
            or single(knee["time"]*slope) != 1 or knee["outSlope"] != {"unity_float":"+Infinity"}):
        raise ValueError("旋风预制体与已验证的非加权曲线不符")
    if not 0 <= age <= life:
        raise ValueError("旋风命中时间超出弹体寿命")
    t = single(_divide(age,life)/ONE)
    # Unity 0x6fd5ac/b8 forms both scaled tangents as exactly 1.0f.
    # Its cubic and quadratic coefficients therefore cancel in float32.
    value = single(t*slope) if t < knee["time"] else 1.
    low = _raw_f32(board["attack@min_atk_scale"])
    high = _raw_f32(board["attack@max_atk_scale"])
    return low+mul(high-low,_raw_f32(value))

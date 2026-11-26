"""Ordinary integer operator attributes from a phase's V079 AttributesKeyFrame list."""
from .baked_mount import single


def base_attack_at_level(keyframes, level):
    return base_integer_at_level(keyframes, level, "atk")


def base_integer_at_level(keyframes, level, field):
    if type(level) is not int:
        return None
    for frame in keyframes:
        if frame["level"] == level:
            return frame["data"][field]
    for lower,upper in zip(keyframes,keyframes[1:]):
        if lower["level"] < level < upper["level"]:
            fraction = single((level-lower["level"])/(upper["level"]-lower["level"]))
            start = single(lower["data"][field])
            delta = single(upper["data"][field]-start)
            # Integer fields are written after binary32 Lerp, with midpoint-to-even rounding.
            return round(single(start+single(delta*fraction)))
    return None

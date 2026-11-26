"""V079 moveSpeed attribute (type 6) and Entity.get_moveSpeed, before steering."""
from math import isfinite

from .baked_mount import single
from .fixed import ONE, add, mul

# Torappu.AttributesData.moveSpeed has AttributeMeta(type=6, min=0x3dcccccd).
MIN_MOVE_Q32 = 429496736


def _fixed(value):
    if type(value) not in (int,float) or not isfinite(value) or not -(1<<31) <= value < 1<<31:
        raise ValueError("移速和系数必须是 Q32.32 范围内的有限数")
    return int(single(value)*ONE)


def move_attribute(base_speed, *, ratio_delta=0, min_q32=MIN_MOVE_Q32, max_q32=(1<<63)-1):
    """Base speed and one already resolved percentage modifier, with the active attribute range."""
    if type(min_q32) is not int or type(max_q32) is not int or min_q32>max_q32:
        raise ValueError("移动速度上下界无效")
    return scaled_move_attribute(base_speed,max(0,add(ONE,_fixed(ratio_delta))),min_q32=min_q32,max_q32=max_q32)


def scaled_move_attribute(base_speed,scale_q32,*,min_q32=MIN_MOVE_Q32,max_q32=(1<<63)-1):
    """Apply the accumulated native FINAL_SCALER before clamping the MOVE_SPEED attribute."""
    value = mul(_fixed(base_speed),scale_q32)
    return max(min_q32,min(max_q32,value))


def movement_cap(attribute_q32, level_multiplier):
    """Cap passed to the movement controller, in tiles/sec. Velocity can be smaller during steering."""
    return single(mul(attribute_q32,_fixed(level_multiplier))/ONE)


def route_movement_cap(route):
    attribute = route.get("move_attribute_q32")
    if attribute is None:
        attribute = move_attribute(route["base_move_speed"])
    return movement_cap(attribute,route["level_move_multiplier"])

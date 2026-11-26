"""Ordinary LEVITATE buff admission and initial lifetime on a living enemy (V079)."""
from .fixed import ONE
from .skill_recovery import _divide
from .volley_damage import _raw_f32
from .baked_mount import single


def changeable_airborne(raw_air, lifted=0, grounded=0):
    if type(raw_air) is not bool:
        raise ValueError("需要原始空地类型")
    if any(type(seq) is not int or seq<0 for seq in (lifted,grounded)) or (lifted and lifted==grounded):
        raise ValueError("浮空和缚地需使用各自的异常生效顺序，零表示未持有")
    return lifted>grounded if lifted or grounded else raw_air


def ordinary_levitate(seconds, motion):
    """Flag sequence numbers are nonzero while present. Mass is the current effective level."""
    raw_air = motion["raw_airborne"]
    immune = motion["levitate_immune"]
    mass = motion["mass_level"]
    lifted = motion.get("levitate_sequence",0)
    grounded = motion.get("ground_bound_sequence",0)
    if type(immune) is not bool or type(mass) is not int:
        raise ValueError("浮空判定需要当前重量等级和浮空免疫")
    flying = changeable_airborne(raw_air,lifted,grounded)
    admitted = seconds>0 and not immune and not lifted and (not raw_air or grounded!=0)
    duration = _raw_f32(seconds) if admitted else 0
    if admitted and mass>=4:
        duration = _divide(duration,2*ONE)
    return dict(levitate_applied=admitted,initial_levitate_lifetime_q32=duration,
                airborne_after=admitted or flying)


class WalkingLevitate:
    """Ordinary moving ground enemy, hit after its tick on a level uninterrupted route leg."""

    def __init__(self,frame):
        self.frame=frame
        self.expires=frame
        self.in_lift_state=False
        self.paused_ticks=0
        self.height=0.
        self.samples={frame:(frame,0.)}

    def apply(self,frame,duration_q32,step_q32):
        if self.frame!=frame or frame<self.expires or min(duration_q32,step_q32)<=0:
            raise ValueError("该次普通浮空需要在受击者更新后生效且当前未持有浮空")
        self.expires=frame+(duration_q32+step_q32-1)//step_q32

    def advance(self,frame):
        if frame!=self.frame+1:
            raise ValueError("浮空中的行走敌人需要按对象实际更新逐帧推进")
        self.frame=frame
        # Entity first ticks its current FSM state, then its buffs. MOVE requests
        # LEVITATE before steering. LEVITATE returns to DEFAULT without steering.
        flag_before=frame<=self.expires
        self.paused_ticks+=int(flag_before or self.in_lift_state)
        self.in_lift_state=flag_before
        # HeightController ticks AFTER the buff timer and reads isActuallyLevitate.
        goal=single(-.65) if frame<self.expires else 0.
        stride=single(.1)
        if self.height<goal:
            self.height=min(goal,single(self.height+stride))
        elif self.height>goal:
            self.height=max(goal,single(self.height-stride))
        self.samples[frame]=(frame-self.paused_ticks,self.height)
        self.samples.pop(frame-2,None)

    def airborne_after_objects(self,frame):
        return frame<self.expires

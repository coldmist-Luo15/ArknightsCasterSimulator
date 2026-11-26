"""Ordinary stage DP. Frame zero is the initial state before the first tick."""
from fractions import Fraction
import struct

from .fixed import ONE, from_float32_bits
from .frames import FPS
from .skill_recovery import READY_EPSILON


class NaturalCostClock:
    def __init__(self, seconds, timestep, model="native_q32", first_gain=None):
        nominal = Fraction(str(seconds)) * FPS
        if nominal <= 0:
            raise ValueError("自然回费周期须为正")
        # Compatibility scale for cost_units. For fractional nominal frame
        # counts use a fixed binary fraction instead of truncating the period.
        self.units = int(nominal) if nominal.denominator == 1 else ONE
        if model == "native_q32":
            self.period = from_float32_bits(int.from_bytes(struct.pack("<f", seconds), "little"))
            self.step = int(timestep * ONE)
            self.epsilon = READY_EPSILON
        elif model == "uniform_frames" and nominal.denominator == 1:
            self.period, self.step, self.epsilon = int(nominal), 1, 1
        else:
            raise ValueError("未知费用时钟，或旧等距时钟不能表示此周期")
        if self.period < self.epsilon or self.step <= 0:
            raise ValueError("自然回费计时器参数超出普通关卡范围")
        self.remaining = self.period
        natural_first = self.credit_frame(1)
        self.first = natural_first if first_gain is None else first_gain
        # An explicit anchor translates the full-cycle trajectory. It cannot
        # establish a measured HUD fraction before the first observed credit.
        self.remaining += (self.first - natural_first) * self.step
        self.phase_known = self.first == natural_first
        self.cycle_index = 0
        self.cycle_frame = 0

    def credit_frame(self, number):
        """Absolute frame from the opening state, before stops at the cap."""
        return max(0, (self.remaining + (number - 1) * self.period - self.epsilon) // self.step + 1)

    def due(self, frame):
        after = self.remaining - (self.step if frame else 0)
        return max(0, (self.epsilon - 1 - after) // self.period + 1)

    def advance(self, frame):
        count = self.due(frame)
        self.remaining += count * self.period - (self.step if frame else 0)
        if count:
            self.phase_known = True
            self.cycle_index += count
            self.cycle_frame = 0
        elif frame:
            self.cycle_frame += 1
        return count

    def bar(self, dp, cap):
        if not self.phase_known or dp >= cap:
            return dict(dp=dp, natural_gains=self.cycle_index, stopped_at_cap=dp >= cap)
        length = self.cycle_frame + self.credit_frame(1)
        return dict(dp=dp, natural_gains=self.cycle_index, frame_in_cycle=self.cycle_frame,
                    cycle_length=length, ruler_display=f"{self.cycle_frame}/{length - 1}")

    def display_units(self, dp, cap):
        if dp == cap:
            return cap * self.units
        if not self.phase_known:
            return None
        elapsed = max(0, min(self.period, self.period - self.remaining))
        return dp * self.units + (elapsed * self.units + self.period // 2) // self.period

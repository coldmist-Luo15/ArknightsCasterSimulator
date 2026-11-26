"""Isolated gates from the CN 78.0 base client's projectile lifecycle.

The caller supplies actual cast/stop events in execution order. These gates do
not schedule a launch, turn HP=0 into an invalid reference, or predict damage.
"""
from dataclasses import dataclass
from typing import Callable


def clear_on_ranged_cast_end(*, wait_for_projectile_invalid: bool,
                             keep_on_cast_end: bool) -> bool:
    """Whether the base RangedAttack.OnCastEnd requests ClearProjectile."""
    return wait_for_projectile_invalid and not keep_on_cast_end


def stop_on_source_check(*, stop_when_source_invalid: bool,
                         source_valid: Callable[[], bool],
                         extra_check_to_finish: Callable[[], bool]) -> bool:
    """Only the source gate, not expiry or any other reason to stop a shot."""
    return stop_when_source_invalid and not source_valid() and extra_check_to_finish()


@dataclass
class ProjectileStopState:
    stopped: bool = False
    has_hit_trace_target: bool = False

    def stop(self, *, eliminated: bool, hit_trace_at_end: bool, reach_at_end: bool,
             trace_target_valid: Callable[[], bool], deal_forced_hit: Callable[[], None],
             on_reach: Callable[[], None], finish_stop: Callable[[], None]) -> None:
        """Base StopMe + the initial hit/reach branches of OnProjectileStop.

        deal_forced_hit represents DealHitTarget(target, true), *not* HP loss.
        finish_stop must supply the rest of stop notification and disposal.
        Stop and trace-hit latches are set before callbacks (which may re-enter).
        """
        if self.stopped:
            return
        self.stopped = True
        if not eliminated:
            if hit_trace_at_end and not self.has_hit_trace_target and trace_target_valid():
                self.has_hit_trace_target = True
                deal_forced_hit()
            if reach_at_end:
                on_reach()
        finish_stop()

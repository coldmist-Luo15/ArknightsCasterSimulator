"""Local CN 78.0 event-delivery components. Neither emits a projectile.

Input times are already-sampled binary32 animation/baked times, not game frames.
The caller owns listener registration, cancellation, the choice of clock and tick scheduling.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Callable


def live_attack_event(name: str, *, active: bool, bake_valid: bool) -> bool:
    """Whether the live Spine _OnEvent path emits entity ON_ATTACK_EVENT (19)."""
    return active and name == 'OnAttack' and not bake_valid


@dataclass
class BakedAttackKey:
    fired: bool = False

    def scan(self, *, name: str, key_time: float, sampled_time: float, emit: Callable[[], None]) -> None:
        """One key of a valid baked track, without loop/reset or track substitution."""
        if not self.fired and sampled_time > key_time and name == 'OnAttack':
            emit()
            self.fired = True  # Native path writes the consumed flag after Emit returns.


class WaitResult(Enum):
    WAIT = 'yield_again'
    EVENT = 'event_observed'
    TIMEOUT = 'budget_exhausted'


@dataclass
class OrdinaryAttackWait:
    """One already-started and subscribed wait, after its initial yield.

    The native FP overload sets max(1, RoundToInt(duration / deltaPlayTime)) on
    first entry and yields without polling. Caller supplies that derived budget,
    then calls poll only when the waiter actually resumes. `_interuptIfTargetDead=0`.
    EVENT and TIMEOUT expose diagnostics; both let the parent proceed to checks.
    """
    remaining_polls: int
    received: bool = False
    finished: bool = False

    def __post_init__(self):
        if type(self.remaining_polls) is not int or self.remaining_polls <= 0:
            raise ValueError('Expected a positive derived wait budget')

    def on_event(self) -> None:
        if not self.finished:
            self.received = True

    def poll(self) -> WaitResult:
        if self.finished:
            raise RuntimeError('Wait already completed')
        if self.received:
            result = WaitResult.EVENT
        else:
            self.remaining_polls -= 1
            if self.remaining_polls:
                return WaitResult.WAIT
            result = WaitResult.TIMEOUT
        self.finished = True
        self.received = False
        return result

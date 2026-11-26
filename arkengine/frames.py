"""30-Hz component runner; phase order follows the base client's OnTick.

This does not supply absent combat systems, input semantics or extra-frame rules.
"""
from enum import IntEnum
from fractions import Fraction

FPS = 30


class Phase(IntEnum):
    TWEEN = 10
    COST = 20
    GAME_INFO = 30
    DECK = 40
    OBJECTS = 50
    UI = 60
    MAP = 70
    ACTION_EXECUTORS = 80
    LEVEL_EVENTS = 90
    CLIENT_CHECK = 100
    INPUT = 110
    RECYCLE_BUFFS = 120
    PHYSICS = 130
    DIALOG = 140
    COROUTINES = 150
    GLOBAL_BUFFS = 160
    ENVIRONMENT = 170
    LATE_OBJECTS = 180
    # Ordinary Unity coroutines waiting for FixedUpdate resume after the battle's
    # FixedUpdate callback. A player can then click while this completed tick is frozen.
    UNITY_FIXED_RESUME = 190
    AFTER_TICK = 200


class FrameLoop:
    def __init__(self):
        self.frame = 0
        self.phase = None
        self.paused = False
        self.events = []
        self._hooks = {}
        self._failed = False
        self._started = False

    @property
    def seconds(self):
        return Fraction(self.frame, FPS)

    def register(self, phase, callback):
        if self._started or self.frame or self.phase is not None:
            raise RuntimeError('Register components before the first frame')
        phase = Phase(phase)
        if phase in self._hooks or not callable(callback):
            raise ValueError('Each phase requires one callable owner')
        self._hooks[phase] = callback

    def emit(self, kind, **data):
        if {'frame', 'phase', 'order', 'kind'} & data.keys():
            raise ValueError('Reserved event fields')
        self.events.append(dict(data, frame=self.frame, phase=self.phase.name if self.phase else None,
                                order=len(self.events), kind=kind))

    def step(self):
        if self._failed or self.phase is not None:
            raise RuntimeError('Cannot resume a failed or recursively running frame')
        if self.paused:
            return False
        self.frame += 1
        return self._run_phases()

    def run_initial_frame(self):
        """Process hooks anchored at frame zero, only for runs that opt into it."""
        if self._started or self.frame or self.paused:
            raise RuntimeError("The initial frame requires a fresh, running clock")
        return self._run_phases()

    def _run_phases(self):
        self._started = True
        try:
            for phase, callback in sorted(self._hooks.items()):
                self.phase = phase
                callback(self)
        except Exception as exc:
            self._failed = True
            self.emit('model_stop', error=type(exc).__name__, reason=str(exc))
            raise
        finally:
            self.phase = None
        return True

    def advance(self, frames):
        if type(frames) is not int or frames < 0:
            raise ValueError('Expected a nonnegative integer number of logic frames')
        for _ in range(frames):
            if not self.step():
                break

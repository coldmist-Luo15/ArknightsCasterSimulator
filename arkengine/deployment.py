"""User-confirmed 1 s protection after a successful ordinary manual operator deploy.

One instance per placement, created on commit (never on drag/preview). The
caller orders same-frame events; this is not the Born animation or a stealth buff.
"""
from dataclasses import dataclass

from arkengine.frames import FPS


@dataclass(frozen=True)
class ManualDeploymentProtection:
    committed_frame: int

    def __post_init__(self):
        if type(self.committed_frame) is not int or self.committed_frame < 0:
            raise ValueError('Expected a nonnegative deployment frame')

    def active_at(self, frame):
        if type(frame) is not int or frame < self.committed_frame:
            raise ValueError('Query this placement only at or after its commit')
        return frame - self.committed_frame < FPS

    def enemy_selectable(self, frame, *, otherwise_selectable):
        if type(otherwise_selectable) is not bool:
            raise TypeError('Expected the result of other selection rules')
        return not self.active_at(frame) and otherwise_selectable

    def filter_damage(self, frame, incoming):
        """Filter already-resolved nonnegative damage, not healing or HP costs."""
        if type(incoming) is not int or incoming < 0:
            raise ValueError('Expected nonnegative damage in raw engine units')
        return 0 if self.active_at(frame) else incoming

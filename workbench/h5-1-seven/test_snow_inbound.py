"""Regressions for a planned wolf cast while Mostima's SP aura is missing."""
from types import SimpleNamespace
import unittest

from observed_timeline import S, W, cast
from snow_inbound import SnowInbound


class WolfSkillTests(unittest.TestCase):
    def setUp(self):
        self.policy = SnowInbound("normal-snow-inbound-reserve")
        self.sent = []
        self.gauge = SimpleNamespace(points=52)
        handler = SimpleNamespace(active=False, spell=None, unblock=0,
                                  gauge=SimpleNamespace(state=self.gauge))
        wolf = SimpleNamespace(uid=W, handler=handler, card={"skill_row": {"spData": {"spCost": 54}}})
        self.enemy = SimpleNamespace(cursor=SimpleNamespace(hidden=False))
        self.battle = SimpleNamespace(frame=9031, operators={W: wolf}, live={"a1": self.enemy},
                                      actions={9031: [cast(9031, W), cast(9031, S)]})

    def step(self, frame, *, early=False):
        self.battle.frame = frame
        self.policy.third_wolf_skill(self.battle, self.sent.append, early=early)

    def test_unready_cast_is_replaced_by_one_ready_cast(self):
        self.step(9031)
        self.assertEqual(self.battle.actions[9031], [cast(9031, S)])
        self.step(9086)
        self.assertEqual(self.sent, [])
        self.gauge.points = 54
        self.step(9087)
        self.step(9088)
        self.assertEqual(self.sent, [cast(9087, W)])

    def test_ready_cast_keeps_its_planned_frame(self):
        self.gauge.points = 54
        self.step(9031)
        self.step(9032)
        self.assertEqual(self.battle.actions[9031], [cast(9031, W), cast(9031, S)])
        self.assertEqual(self.sent, [])

    def test_existing_early_branch_does_not_repeat(self):
        self.gauge.points = 54
        self.step(9030, early=True)
        self.step(9031)
        self.step(9032)
        self.assertEqual(self.battle.actions[9031], [cast(9031, S)])
        self.assertEqual(self.sent, [cast(9030, W)])

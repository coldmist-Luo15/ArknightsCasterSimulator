from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arkengine.deployment import ManualDeploymentProtection
from arkengine.frames import FrameLoop, Phase


class ManualDeploymentTests(unittest.TestCase):
    def test_selection_and_damage_are_independent_at_both_boundaries(self):
        window = ManualDeploymentProtection(17)
        for frame in range(17, 47):
            self.assertFalse(window.enemy_selectable(frame, otherwise_selectable=True))
            # Can be an area hit; no prior targeting call is needed by the damage rule.
            self.assertEqual(window.filter_damage(frame, 942), 0)
        self.assertTrue(window.enemy_selectable(47, otherwise_selectable=True))
        self.assertFalse(window.enemy_selectable(47, otherwise_selectable=False))
        self.assertEqual(window.filter_damage(47, 942), 942)

    def test_commit_not_preview_pause_not_elapsed_and_redeployment_is_fresh(self):
        loop = FrameLoop()
        windows = []
        loop.register(Phase.INPUT, lambda clock: windows.append(ManualDeploymentProtection(clock.frame))
                      if clock.frame in (3, 35) else None)
        loop.advance(2)
        self.assertEqual(windows, [])
        loop.advance(1)
        loop.paused = True
        loop.advance(100)
        self.assertEqual(loop.frame, 3)
        self.assertTrue(windows[0].active_at(loop.frame))
        loop.paused = False
        loop.advance(32)
        self.assertFalse(windows[0].active_at(loop.frame))
        self.assertTrue(windows[1].active_at(loop.frame))

    def test_invalid_or_precommit_queries_do_not_silently_apply_rules(self):
        for value in (-1, False, 1.5):
            with self.assertRaises(ValueError):
                ManualDeploymentProtection(value)
        window = ManualDeploymentProtection(5)
        for value in (4, 5.0, True):
            with self.assertRaises(ValueError):
                window.filter_damage(value, 0)
        with self.assertRaises(ValueError):
            window.filter_damage(5, -2)

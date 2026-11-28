"""H5-1 V079 rune admission and drone/device field regressions."""
import unittest
from types import SimpleNamespace

from arkengine.damage import ordinary_arts_hit
from arkengine.deployment import ManualDeploymentProtection
from arkengine.fixed import ONE
from arkengine.opening_scene import PinnedTables, load_opening_scene
from arkengine.recal_battle import Operator, RecalBattle
from arkengine.recal_device import DEFENDER, FROST, DeviceCombat, RecalFields
from arkengine.recal_enemy_attacks import EnemyAttacks
from arkengine.recal_scene import DEVICE, STAGE_ID


RUNES = ("base", "token_build_b", "enemy_speed_b", "enemy_silenceImmue_a",
         "emppnt_level_up_2", "airforce_frost_2", "airforce_lazerd_2", "char_respawntime_2",
         "tyokai_fightback_3", "enemy_attri_atk_2", "defdrn_up_2", "emppnt_more_2",
         "global_squadnum_2", "char_exclude_tavasn_3")


class RecalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = PinnedTables()

    def scene(self, ids):
        return load_opening_scene(tables=self.tables, stage_id=STAGE_ID, recal_rune_ids=ids)

    def field(self, scene):
        battle = SimpleNamespace(scene=scene, tables=self.tables, rows=8, cols=12, frame=0, dp=40,
                                 dt=self.tables.fixed_timestep(), live={}, operators={}, log=lambda *a, **k: None)
        return battle, RecalFields(battle)

    def actor(self, battle, uid, row, col):
        place = dict(row=row, col=col, direction="RIGHT", frame=battle.frame)
        a = SimpleNamespace(uid=uid, key=uid, alive=True, row=row, col=col, place=place,
                            deployed_frame=battle.frame)
        battle.operators[a.key] = a
        if uid == DEVICE:
            a.handler = DeviceCombat(battle, a)
        return a

    def enemy(self, battle, key, col):
        data = battle.scene.enemies[key]
        enemy = SimpleNamespace(key=key, id=key, row=3, col=col, alive=True, radius=.1,
            cursor=SimpleNamespace(hidden=False), attr=lambda k: data["attributes"][k]["m_value"])
        battle.live[enemy.id] = enemy
        return enemy

    def test_manual_deployment_changes_actual_hostile_selection_and_damage(self):
        card={"panel_attributes":{"magicResistance":0,"def":0}}
        older=Operator("older",card,{"row":3,"col":2,"frame":20},100*ONE,10,"older")
        newest=Operator("newest",card,{"row":3,"col":3,"frame":100},100*ONE,10,"newest",
                        deployment_protection=ManualDeploymentProtection(100))
        enemy=SimpleNamespace(id="caster",row=3,col=4)
        events=[]
        battle=SimpleNamespace(frame=129,dt=self.tables.fixed_timestep(),
                               operators={a.key:a for a in (older,newest)},logos=None,
                               log=lambda kind,**data:events.append(kind))
        hostile=EnemyAttacks(battle)
        hostile._body=lambda actor:(.1,(0,0,0))
        profile={"kind":"spell","radius":2.,"limit":1}
        self.assertEqual(hostile._targets(enemy,profile),[older])
        self.assertEqual(RecalBattle.hurt(battle,enemy,newest,10*ONE,"arts","artillery_hit"),0)
        self.assertEqual(newest.hp,100*ONE)
        self.assertEqual(events,["manual_deployment_damage_blocked"])

        battle.frame=130
        self.assertEqual(hostile._targets(enemy,profile),[newest])
        expected=ordinary_arts_hit(attack_raw=10*ONE,resistance_raw=0,hp_raw=100*ONE)
        self.assertEqual(RecalBattle.hurt(battle,enemy,newest,10*ONE,"arts","enemy_spell_hit"),expected.damage_raw)
        self.assertEqual(newest.hp,expected.hp_after_raw)

    def test_selected_options_and_no_table_mutation(self):
        slow = self.scene(RUNES + ("global_costrecovery_2",))
        normal = self.scene(RUNES)
        self.assertEqual((slow.stage["code"], slow.recal.score, normal.recal.score), ("H5-1", 24, 22))
        for s, interval in ((slow, 2), (normal, 1)):
            self.assertEqual(s.recal.enabled_groups, {"emppntHidden", "emppntMore"})
            self.assertEqual((s.level["options"]["costIncreaseTime"], s.recal.squad_limit), (interval, 7))
            actions, _ = self.tables.admit_combat_actions(s.level)
            rows = [s.level["waves"][w]["fragments"][f]["actions"][i] for w, f, i in actions]
            self.assertEqual(sum(r["count"] for r in rows if r["actionType"] == "SPAWN"), 64)
            self.assertEqual(s.enemies["enemy_1112_emppnt_2"]["attributes"]["maxHp"]["m_value"], 96000)
            card = s.recal.token_cards[DEVICE]
            self.assertEqual((card["initial_count"], card["slots"], card["panel"]["maxHp"]), (0, 0, 4900))
            self.assertEqual(card["buildable"], ("RANGED",))
            self.assertEqual(s.recal.prepare_panel("a", "CASTER", {"cost": 19}), {"cost": 19})
            self.assertEqual(s.recal.prepare_panel("b", "SNIPER", {"cost": 12}), {"cost": 36})
        with self.assertRaises(ValueError):
            self.scene(RUNES + ("enemy_speed_a",))
        with self.assertRaises(ValueError):
            self.scene(RUNES[1:])

    def test_jammer_frost_defense_and_damage(self):
        b, fields = self.field(self.scene(RUNES))
        host = self.actor(b, "char_180_amgoat", 3, 1)
        jammer = self.actor(b, DEVICE, 3, 2)
        frost = self.enemy(b, FROST, 3.5)
        defense = self.enemy(b, DEFENDER, 4)
        sponsor = self.enemy(b, "enemy_1105_tyokai_2", 4.5)
        ground = self.enemy(b, "enemy_1037_lunsbr", 4.5)
        fields.physics()
        self.assertEqual(fields.attack_speed_multiplier(host), 1)
        fields.physics()
        self.assertEqual(fields.attack_speed_multiplier(host), .5)
        self.assertEqual(fields.movement_multiplier(frost), .5)
        self.assertEqual(fields.movement_multiplier(ground), 1)
        self.assertFalse(fields.silenced(frost))
        self.assertTrue(fields.silenced(defense))
        self.assertFalse(fields.disarmed(frost))
        self.assertTrue(fields.disarmed(sponsor))
        self.assertEqual(fields.defense_bonus(ground), 0)
        self.assertEqual(fields.damage_multiplier(host, frost, "arts"), 1)
        host.col = 0
        self.assertAlmostEqual(fields.damage_multiplier(host, frost, "arts"), .3)
        self.assertEqual(fields.damage_multiplier(host, frost, "pure"), 1)
        self.assertAlmostEqual(fields.reflected_attack(sponsor, "arts") / ONE, 1020, places=3)
        self.assertEqual(fields.reflected_attack(sponsor, "pure"), 0)
        jammer.alive = False
        self.assertEqual(fields.defense_bonus(ground), 1800)
        self.assertEqual(fields.defense_bonus(defense), 0)
        frost.alive = False
        self.assertEqual(fields.attack_speed_multiplier(host), 1)

    def test_alternative_device_and_immunity(self):
        swap = {"token_build_b": "token_build_a", "enemy_silenceImmue_a": "enemy_silenceImmue_b"}
        b, fields = self.field(self.scene(tuple(swap.get(k, k) for k in RUNES)))
        self.assertIn("MELEE", b.scene.recal.token_cards[DEVICE]["buildable"])
        op = self.actor(b, "char_180_amgoat", 3, 2)
        jammer = self.actor(b, DEVICE, 3, 1)
        frost = self.enemy(b, FROST, 3)
        laser = self.enemy(b, "enemy_1041_lazerd", 3)
        fields.physics()
        fields.physics()
        self.assertTrue(fields.silenced(frost))
        self.assertFalse(fields.disarmed(laser))
        self.assertTrue(fields.silenced(laser))
        self.assertEqual(fields.attack_speed_multiplier(op), 1)
        for f in range(1, 92):
            b.frame = f
            jammer.handler.objects()
        self.assertEqual(b.dp, 34)
        jammer.handler.close()
        for _ in range(100):
            jammer.handler.objects()
        self.assertEqual(b.dp, 34)


if __name__ == "__main__":
    unittest.main()

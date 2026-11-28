"""H5-1 interference, drone auras and damage callbacks on the shared battle clock."""
from math import hypot

from .attack_range import circle_in_range, range_cells
from .caster_prefabs import _prefab
from .fixed import ONE, mul
from .recal_scene import DEVICE
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32, _talent_distances


FROST = "enemy_1042_frostd"
DEFENDER = "enemy_1017_defdrn"
DAMAGE_TYPE = {1: "physical", 2: "magical", "physical": "physical", "arts": "magical",
               "magical": "magical", "magic": "magical", "pure": "pure", "element": "element"}


def _component(prefab, script, path=""):
    return next(c["fields"] for c in prefab["components"]
                if c["script"] == script and c["path"] == path and c["active"] and c["enabled"])


def _aura(tables, key, node):
    prefab = _prefab(tables, "enemies", key)
    path = "Talents/" + node
    aura = _component(prefab, "AuraAbility", path)
    buff, = aura["_buffs"]
    if (aura["_selfOption"] != 2 or not aura["_removeBuffWhenTargetLeave"]
            or not aura["_removeBuffWhenAbilityDetached"] or buff["maxStackCnt"] != 1
            or buff["independentCharacterSource"]
            or not _component(prefab, "SilenceableTalent", path)["_isSilenceable"]):
        raise ValueError(f"{key} 的单层可沉默光环配置已改变")


def _board(scene, key):
    return {b["key"]: b["value"] for b in scene.enemies[key]["talentBlackboard"]}


def _actor_instance(actor):
    return actor.key, actor.deployed_frame


def _kind(value):
    return DAMAGE_TYPE.get(value.lower() if isinstance(value, str) else value)


class DeviceCombat:
    def __init__(self, battle, actor):
        self.battle, self.actor = battle, actor
        card = battle.scene.recal.token_cards[actor.uid]
        self.cells = range_cells(card["range"], actor.place, battle.rows, battle.cols)
        self.closed = False
        self.drain = card["buffs"].get("reduce_cost_by_interval")
        if self.drain:
            buff, = _component(_prefab(battle.tables, "[uc]globalbuffs", self.drain["key"]),
                               "GlobalBuff")["_buffs"]
            self.timer = RecoveryTimer(_raw_f32(self.drain["interval"]))
            # waitFirstTriggerInterval=false schedules a pulse on the first buff update.
            if not buff["waitFirstTriggerInterval"]:
                self.timer.remaining = 0

    def objects(self):
        if self.closed or not self.drain:
            return
        pulses = self.timer.advance(_raw_f32(self.battle.dt))
        if pulses:
            before = self.battle.dp
            self.battle.dp = max(0, before + int(self.drain["cost"]) * pulses)
            self.battle.log("device_cost", source=self.actor.key, cost_before=before,
                            cost_after=self.battle.dp)

    def coroutines(self):
        pass

    def press(self):
        self.battle.log("skill_button_rejected", operator_id=DEVICE, reason="passive_skill")
        return False

    def close(self):
        self.closed = True


class RecalFields:
    def __init__(self, battle):
        self.battle = battle
        self.rules = battle.scene.recal
        card = self.rules.token_cards[DEVICE]
        skill = _prefab(battle.tables, "[uc]skills", card["skill"]["prefabId"])
        buff, = _component(skill, "AuraAbility")["_buffs"]
        if (buff["attributes"]["abnormalFlags"] != [11, 12] or buff["maxStackCnt"] != 1
                or _component(skill, "FilterTagTargetValidator")["_tags"] != ["drone"]):
            raise ValueError("干扰装置的异常、目标标签或叠加上限已改变")
        self.move_scale = next(b["value"] for b in card["skill"]["blackboard"] if b["key"] == "move_speed")
        _aura(battle.tables, FROST, "atkSpeedDown")
        _aura(battle.tables, DEFENDER, "DefUp")
        self.frost_speed = 1 + _board(battle.scene, FROST)["atkSpeedDown.attack_speed"]
        self.frost_radius = battle.scene.enemies[FROST]["rangeRadius"]["m_value"]
        defense = _board(battle.scene, DEFENDER)
        self.def_bonus = defense["defup.def"]
        self.def_radius = defense["defup.range_radius"]
        self.drones = {key for key, data in battle.scene.enemies.items()
                       if "drone" in (data["enemyTags"]["m_value"] or ())}
        self.actor_radii = {}
        self.active = self.pending = ({}, {}, {})

    def _actor_radius(self, actor):
        if actor.uid not in self.actor_radii:
            cat = "[uc]tokens" if actor.uid == DEVICE else "characters"
            shape = self.battle.tables._component_fields(cat, actor.uid, ("m_Radius", "m_Offset"),
                "recal_receiver_circles", component_type="CircleCollider2D")
            if any(shape["m_Offset"].values()):
                raise ValueError(f"{actor.uid} 的碰撞中心存在额外偏移")
            self.actor_radii[actor.uid] = shape["m_Radius"]
        return self.actor_radii[actor.uid]

    def physics(self):
        # AuraAbility queues contact work for the following fixed resumption.
        # Alive and silence checks below also detach effects between samples.
        self.active = self.pending
        jam, chill, armor = {}, {}, {}
        self.pending = jam, chill, armor
        allies = self.battle.operators.values()
        live = [e for e in self.battle.live.values() if e.alive and not e.cursor.hidden]
        for actor in allies:
            if actor.uid != DEVICE or not actor.alive:
                continue
            for e in live:
                if e.key in self.drones and circle_in_range(e.row, e.col, e.radius,
                                                            actor.handler.cells, box_query=True):
                    jam.setdefault(e.id, []).append(actor)
        for e in live:
            if e.key == FROST:
                for a in allies:
                    if a.alive and hypot(e.row - a.row, e.col - a.col) <= self.frost_radius + self._actor_radius(a):
                        chill.setdefault(_actor_instance(a), []).append(e)
            elif e.key == DEFENDER:
                for other in live:
                    if e is not other and hypot(e.row - other.row, e.col - other.col) <= self.def_radius + other.radius:
                        armor.setdefault(other.id, []).append(e)

    def _jammed(self, enemy):
        return any(a.alive for a in self.active[0].get(enemy.id, ()))

    def movement_multiplier(self, enemy):
        return self.move_scale if self._jammed(enemy) else 1

    def disarmed(self, enemy):
        return (self._jammed(enemy) and not enemy.attr("disarmedCombatImmune")
                and "disarmed_immune" not in self.rules.enemy_buffs[enemy.key])

    def silenced(self, enemy):
        if enemy.attr("silenceImmune"):
            return False
        silence=getattr(enemy,"silence",None)
        return self._jammed(enemy) or bool(silence and silence.gates(self.battle.frame)["silenced"])

    def attack_speed_multiplier(self, actor):
        sources = self.active[1].get(_actor_instance(actor), ())
        return self.frost_speed if any(e.alive and not self.silenced(e) for e in sources) else 1

    def defense_bonus(self, enemy):
        sources = self.active[2].get(enemy.id, ())
        return self.def_bonus if any(e.alive and not self.silenced(e) for e in sources) else 0

    def damage_multiplier(self, actor, target, damage_type):
        rule = self.rules.enemy_buffs[target.key].get("damage_scale[type]")
        if rule and rule.get(_kind(damage_type)) == 1:
            _, in_radius, _ = _talent_distances((actor.row, actor.col), (target.row, target.col), rule["range_radius"])
            if not in_radius:
                return (ONE - _raw_f32(rule["damage_resistance"])) / ONE
        return 1

    def reflected_attack(self, enemy, damage_type):
        rule = self.rules.enemy_buffs[enemy.key].get("inverse_damage[type]")
        if rule and _kind(damage_type) in ("physical", "magical"):
            if rule["type"] != 2:
                raise ValueError("赞助无人机反击伤害类型已改变")
            return mul(_raw_f32(enemy.attr("atk")), _raw_f32(rule["atk_scale"]))
        return 0

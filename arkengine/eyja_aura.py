"""V079 炎息. The talent supplies ATK's additive percentage bucket to allied Casters."""
from dataclasses import dataclass

from .caster_prefabs import _prefab
from .fixed import ONE, mul
from .module_effects import active_board, equipped_phase, talent_rows
from .volley_damage import _raw_f32


EYJA = "char_180_amgoat"


@dataclass(frozen=True)
class FlameBreath:
    deck: bool
    ratio: int

    @classmethod
    def from_card(cls, tables, card):
        if card["operator_id"] != EYJA:
            raise ValueError("炎息来源必须是艾雅法拉")
        phase = equipped_phase(tables, card)
        units = tables.read("excel/character_table.json")
        rows = talent_rows(phase, 0, "1", units[EYJA]["talents"][0]["candidates"])
        board = active_board(rows, card["elite"], card["level"], card["potential"])
        deck = False
        for part in phase.get("parts", ()):
            if part["target"] != "TALENT" or part["isToken"]:
                continue
            picks = part["addOrOverrideTalentDataBundle"]["candidates"] or ()
            if not any(p["talentIndex"] == 0 and p["prefabKey"] == "1" for p in picks):
                continue
            components = _prefab(tables, "[uc]equips", part["resKey"])["components"]
            cfg, = (r["fields"] for r in components if r["script"] == "DeckBuffTalent" and r["enabled"] and r["active"])
            options = cfg["_options"]
            selector = options["selector"]
            if (options["minCntToTrig"] != 1 or options["useOtherSelectorWhenGetBuff"]
                    or selector["categoryMask"] != 32 or any(v for k,v in selector.items() if k != "categoryMask")):
                raise ValueError("炎息的编队筛选或触发门槛已变化")
            entry, = cfg["_deckBuffs"]
            buff = entry["buff"]
            deck = True
            break
        if not deck:
            components = _prefab(tables, "characters", EYJA)["components"]
            active = {r["script"]:r["fields"] for r in components if r["path"] == "Talents/1" and r["active"] and r["enabled"]}
            cfg = active["GlobalAuraAbility"]
            target = active["TargetValidator"]["_targetOptions"]
            if (cfg["_selfOption"] != 0 or not cfg["_removeBuffWhenAbilityDetached"] or cfg["_interval"]
                    or target["professionMask"] != 32 or target["targetSide"] != 1 or target["targetCategory"] != 1
                    or target["targetMotion"] != 3 or target["ignoreAllyTargetFree"]):
                raise ValueError("炎息在场的移除条件或目标筛选已变化")
            buff, = cfg["_buffs"]
        modifier, = buff["attributes"]["attributeModifiers"]
        if buff["templateKey"] != "empty" or modifier != dict(attributeType=1,formulaItem=1,value=0,
                loadFromBlackboard=1,fetchBaseValueFromSourceEntity=0):
            raise ValueError("炎息攻击力加成的属性或乘区已变化")
        return cls(deck=deck, ratio=_raw_f32(board.get("atk", 0)))

    def bonus(self, profession, *, owner_on_field):
        return self.ratio if profession == "CASTER" and (self.deck or owner_on_field) else 0

    def source_attack(self, tables, card, *, owner_on_field, other_atk_ratio=0, scale=ONE):
        """Read current ATK with concurrent percentage buffs, then apply the attack's scale."""
        if type(scale) is not int or scale < 0 or type(other_atk_ratio) is not int:
            raise ValueError("攻击倍率和其他攻击力比例需要 Q32.32 整数，攻击倍率须非负")
        atk = _raw_f32(card["panel_attributes"]["atk"])
        profession = tables.read("excel/character_table.json")[card["operator_id"]]["profession"]
        bonus = self.bonus(profession, owner_on_field=owner_on_field)
        return mul(mul(atk, ONE + other_atk_ratio + bonus), scale)

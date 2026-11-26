"""V079 logos_t_2 source callback and its limited target-side damage modifier."""
from dataclasses import dataclass

from .elemental_casters import _board
from .volley_damage import _raw_f32


@dataclass(frozen=True)
class LogosCurse:
    duration: int
    resistance_add: int
    attack_add: int

    @classmethod
    def from_card(cls, templates, card):
        board = _board(card, 1, "2")
        if not board:
            return None
        create, = templates["logos_t_2"]["eventToActions"]["ON_CALCULATE_DAMAGE"]
        buff = create["_buff"]
        attribute, = buff["attributes"]["attributeModifiers"]
        add, = templates[buff["templateKey"]]["eventToActions"]["ON_BEING_CALCULATE_DAMAGE"]
        if (not create["$type"].split(",")[0].endswith("+CreateBuff") or create["_buffOwner"] != "TARGET"
                or create["_isDerivedBuff"] or buff["overrideType"] != "EXTEND" or buff["maxStackCnt"] != 1
                or buff["lifeTimeType"] != "LIMITED" or buff["takeSnapshotWhenExtend"]
                or buff["independentCharacterSource"] or not attribute["loadFromBlackboard"]
                or (attribute["attributeType"], attribute["formulaItem"]) != ("MAGIC_RESISTANCE", "ADDITION")
                or not add["$type"].split(",")[0].endswith("+AtkAdditionUpBeforeCalcDamage")
                or not add["_filterDamageType"] or add["_damageType"] != "MAGICAL"):
            raise ValueError("剜魂具辞的目标、刷新或受击回调已变化")
        return cls(_raw_f32(board[buff["durationKey"]]), _raw_f32(board["magic_resistance"]),
                   _raw_f32(board[add["_atkAdditionKey"]]))

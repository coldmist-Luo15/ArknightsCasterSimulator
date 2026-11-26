"""Ordinary V079 HP/MINUS hit through the magic-resistance arithmetic branch."""
from dataclasses import dataclass

from .fixed import ONE, SIGN64, from_float32_bits, mul


# Binary32 literals consumed by BattleFormula, then converted to Q32.32.
MINIMUM_RATIO = from_float32_bits(0x3D4CCCCD)
ONE_PERCENT = from_float32_bits(0x3C23D70A)
HP_EPSILON = 42950


@dataclass(frozen=True)
class ArtsHit:
    damage_raw: int
    hp_after_raw: int
    alive_after: bool


def ordinary_arts_hit(*, attack_raw: int, resistance_raw: int, hp_raw: int, output_scale_raw: int=ONE,
                      fixed_res_penetration_raw: int=0, received_scale_raw: int=ONE) -> ArtsHit:
    """Apply one accepted hit to an ordinary living enemy with running state machine.

    Attack is final ATK after this attack's outgoing scale. Inputs assume the
    usual magic-resistance branch and fixed penetration only. The supplied
    output and receiver scales apply in callback order. Shields, immortality,
    negative-HP pools and death interception need their own receiver.
    alive_after is the HP predicate and does not schedule the death lifecycle.
    """
    for value in (attack_raw, resistance_raw, hp_raw, output_scale_raw, fixed_res_penetration_raw,received_scale_raw):
        if type(value) is not int or not -SIGN64 <= value < SIGN64:
            raise ValueError("伤害输入需要有符号 Q32.32 整数")
    if min(attack_raw,output_scale_raw,fixed_res_penetration_raw,received_scale_raw)<0 or hp_raw <= HP_EPSILON:
        raise ValueError("普通术伤需要非负攻击力和存活目标")
    percent = max(0, 100 * ONE - max(0, resistance_raw - fixed_res_penetration_raw))
    if attack_raw * percent >= SIGN64 * ONE:
        raise ValueError("攻击与法抗系数的中间乘积超出 Q32.32 范围")
    damage = max(mul(attack_raw, MINIMUM_RATIO), mul(mul(attack_raw, percent), ONE_PERCENT))
    damage = mul(mul(damage,output_scale_raw),received_scale_raw)
    return _apply_hp(damage,hp_raw)


def ordinary_physical_hit(*, attack_raw: int, defense_raw: int, hp_raw: int) -> ArtsHit:
    """Apply an ordinary physical packet, with the same HP and minimum-damage rules."""
    if (any(type(v) is not int or not 0 <= v < SIGN64 for v in (attack_raw, defense_raw, hp_raw))
            or hp_raw <= HP_EPSILON):
        raise ValueError("普通物伤需要非负的攻击和有效防御，以及存活目标的 Q32.32 生命")
    return _apply_hp(max(mul(attack_raw, MINIMUM_RATIO), attack_raw-defense_raw), hp_raw)


def _apply_hp(damage, hp_raw):
    remainder = hp_raw - damage
    # MathUtil.LE(hp, 0) and GT(hp, 0) use strict comparisons to epsilon.
    hp_after = 0 if remainder < HP_EPSILON else remainder
    return ArtsHit(damage, hp_after, hp_after > HP_EPSILON)

"""Ebenholz's ordinary empty/stored attack timing before skill activation."""
from fractions import Fraction

from .frames import FPS
from .mystic_equipment import storage_speed_trigger
from .module_effects import equipped_phase, module_stat, selected_branch


def fixed_wait_frames(frame_ratio):
    """V079 fixed-wait budget for a positive duration, with a nearest-even tie."""
    return max(1, round(frame_ratio))


def ebenholz_timing(tables, card):
    key = card["operator_id"]
    attack = tables.mystic_attack(key)
    if attack["_timeMode"] != 0 or attack["_resetCdStrategy"] != 1:
        return dict(status="unresolved", reason="unreviewed_cooldown_strategy", prefab=attack)
    unit = tables.read("excel/character_table.json")[key]
    stats = unit["phases"][card["elite"]]["attributesKeyFrames"]
    base, = {Fraction(str(s["data"]["baseAttackTime"])) for s in stats}
    speed, = {int(s["data"]["attackSpeed"]) for s in stats}
    branch = selected_branch(tables, card)
    if branch in (None, "msc-x", "msc-d"):
        if branch and not card.get("module_level"):
            return dict(status="unresolved", reason="unknown_module_speed")
        speed += int(module_stat(equipped_phase(tables, card), "attack_speed"))
        speed_bonuses = {0}
    elif branch == "msc-y":
        equip = tables.read("excel/battle_equip_table.json")[card["module_ids"][0]]
        speed_bonuses = {int(bb["value"]) for phase in equip["phases"]
                         if card["module_level"] in (None, phase["equipLevel"])
                         for part in phase["parts"] if part["target"] == "TALENT"
                         for row in part["addOrOverrideTalentDataBundle"]["candidates"] or []
                         if row["prefabKey"] == "10"
                         for bb in row["blackboard"] if bb["key"] == "attack_speed"}
    else:
        return dict(status="unresolved", reason="unreviewed_module_for_attack_timing")
    if len(speed_bonuses) != 1:
        return dict(status="unresolved", reason="unknown_stored_energy_speed_bonus")
    bonus, = speed_bonuses

    def budget(attack_speed):
        ratio = base * FPS * 100 / attack_speed
        # HALF_FRAME tests remaining time at cast end. These pinned, stable
        # speeds are away from the threshold and the FP comparison tolerance.
        whole = ratio.numerator // ratio.denominator
        steady = whole + int(ratio - whole >= Fraction(1, 2))
        return dict(attack_speed=attack_speed, nominal_frames=str(ratio),
                    fixed_wait_frames=fixed_wait_frames(ratio), steady_cooldown_frames=steady)

    return dict(status="conditional_ordinary_attack_timing", prefab=attack,
                cooldown_condition="unchanged_period_at_cast_end_and_normal_attack_update",
                empty=budget(speed), stored=budget(speed + bonus),
                stored_speed_bonus=bonus, on_speed_change="reset_cooldown_preserving_progress",
                on_empty_cast_with_space="store_energy_and_occupy_attack_cycle",
                first_targeted_cast_frame=None)

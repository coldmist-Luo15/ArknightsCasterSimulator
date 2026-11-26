"""Redistribute a shared pair of charge counters when a Mystic attack attaches."""
from .module_effects import active_board, equipped_phase, selected_branch, talent_rows, trait_board


ENEMY_LEVEL_BITS = {"NORMAL": 1, "ELITE": 2, "BOSS": 4}


def target_level_masks(tables, key):
    """Read the main trigger and the separately referenced extra-projectile validator."""
    masks = {}
    for mode in ("Default", "S3"):
        main = ("Modes", mode, "Body", "Attack", "Trigger")
        counter = ("Trait", "ChargeAttackGroup", "ExChargeAttack" + mode)
        common = ("_targetSide", "_targetCategory", "_targetMotion", "_limitTargetNum", "_maxNum")
        fields = (*common, "m_Script") if mode == "Default" else (*common, "m_Script", "_enemyLevelMask")
        selector = tables._component_fields("characters", key, fields, "main_selector_" + mode, children=main)
        validator = tables._component_fields("characters", key, ("_enemyLevelMask", "_targetOptions"),
            "extra_validator_" + mode, children=(*counter, "TargetValidator"))
        owner = tables._component_fields("characters", key, ("_targetValidator",), "extra_owner_" + mode,
                                         children=counter)
        path_id = tables.source["extra_validator_" + mode][key]["component_path_id"]
        # MonoScript references of AdvancedSelector and AdvancedSelectorWithEnemyOptions in V079.
        expected_script = 7080728813803358465 if mode == "Default" else -4616102445704182007
        if (owner["_targetValidator"] != dict(m_FileID=0, m_PathID=path_id)
                or selector["m_Script"] != dict(m_FileID=1, m_PathID=expected_script)
                or tuple(selector[k] for k in common) != (2, 1, 3, 1, 1)
                or validator["_enemyLevelMask"] != 6 or selector.get("_enemyLevelMask", 7) != (7 if mode == "Default" else 6)
                or validator["_targetOptions"]["targetSide"] != 2):
            return dict(status="unresolved", reason="unreviewed_mystic_target_selector")
        masks[mode] = dict(main=selector.get("_enemyLevelMask", 7), extra=validator["_enemyLevelMask"])
    return dict(status="target_level_masks", modes=masks, level_bits=dict(ENEMY_LEVEL_BITS))


def spend_on_target(normal, extra, level, masks):
    """Amounts fired alongside a main attack. Its target must already be eligible."""
    bit = ENEMY_LEVEL_BITS[level]
    if not masks["main"] & bit:
        raise ValueError("主攻击不能选择该敌人等级")
    return normal, extra if masks["extra"] & bit else 0


def charge_limits(tables, card):
    """Resolve the shared ordinary and force-extra mode capacities from the equipped trait."""
    key = card["operator_id"]
    if key != "char_4046_ebnhlz" or card["skill_id"] != "skchr_ebnhlz_3" or card["elite"] != 2:
        return dict(status="unresolved", reason="unreviewed_charge_capacity_configuration")
    if card["level"] is None or card["potential"] is None:
        return dict(status="unresolved", reason="unknown_charge_capacity_configuration")
    branch = selected_branch(tables, card)
    if branch not in (None, "msc-x", "msc-y", "msc-d") or (branch and not card.get("module_level")):
        return dict(status="unresolved", reason="unreviewed_charge_capacity_module")
    phase = equipped_phase(tables, card)
    unit = tables.read("excel/character_table.json")[key]
    args = card["elite"], card["level"], card["potential"]
    normal = trait_board(unit, phase, card)["times"]
    talent = active_board(talent_rows(phase, 0, "1", unit["talents"][0]["candidates"]), *args)
    extra = talent["times_2"]
    if any(value < 0 or int(value) != value for value in (normal, extra)):
        return dict(status="unresolved", reason="unreviewed_charge_capacity_value")
    normal, extra = int(normal), int(extra)
    flow = tables._component_fields("characters", key, ("_config",), "charge_capacity_flow")["_config"]
    transfers = [(r["_source"], r["_sourceTalentKey"], r["_sourceKey"], r["_targetKey"])
                 for r in flow if r["_target"] == 2 and r["_targetKey"] in
                 ("times", "times_2", "trait_s3.times", "trait_s3.times_2")
                 and r["_formulaType"] == r["_validateSkillIndices"] == r["_type"] == 0]
    if set(transfers) != {(0, "1", "times_2", "times_2"), (2, "", "times", "trait_s3.times"),
                          (0, "1", "times_2", "trait_s3.times_2")}:
        return dict(status="unresolved", reason="unreviewed_charge_capacity_dataflow")
    group = tables._component_fields("characters", key, ("_abilities",), "charge_capacity_group",
                                     children=("Trait", "ChargeAttackGroup"))
    group_id = tables.source["charge_capacity_group"][key]["component_path_id"]
    root = tables._component_fields("characters", key, ("_traitAbility",), "charge_trait_root")
    if root["_traitAbility"] != dict(m_FileID=0, m_PathID=group_id):
        return dict(status="unresolved", reason="unreviewed_charge_group_binding")
    modes = {}
    for mode, child in (("Default", "ExChargeAttackDefault"), ("S3", "ExChargeAttackS3")):
        fields = tables._component_fields("characters", key, ("_forceExCharge", "_refreshBlackboardOnAttached"),
            "charge_counter_" + mode, children=("Trait", "ChargeAttackGroup", child))
        ref = dict(m_FileID=0, m_PathID=tables.source["charge_counter_" + mode][key]["component_path_id"])
        attack = tables._component_fields("characters", key, ("_chargeAbility", "_useChargeableGroup", "_attachAndDetach"),
            "charge_owner_" + mode, children=("Modes", mode, "Body", "Attack"))
        if (fields["_refreshBlackboardOnAttached"] or ref not in group["_abilities"] or
                attack != {"_chargeAbility": ref, "_useChargeableGroup": 0, "_attachAndDetach": 1}):
            return dict(status="unresolved", reason="unreviewed_charge_handoff")
        force_extra = bool(fields["_forceExCharge"])
        modes[mode] = dict(normal_limit=0 if force_extra else normal,
                           extra_limit=normal + extra if force_extra else extra, force_ex_charge=force_extra)
    return dict(status="conditional_charge_limits", modes=modes, group_component=group_id)


def redistribute_charges(normal: int, extra: int, *, normal_limit: int, extra_limit: int) -> tuple[int, int]:
    """Transfer resolved nonnegative counts using the destination's effective limits."""
    if any(type(v) is not int or v < 0 for v in (normal, extra, normal_limit, extra_limit)):
        raise ValueError("储能数和仓位上限需要非负整数")
    if max(normal + extra, normal_limit, extra_limit) >= 1 << 31:
        raise ValueError("储能数超过客户端有符号整数范围")
    kept_normal = min(normal_limit, normal)
    kept_extra = min(extra_limit, extra)
    return (min(normal_limit, kept_normal + (extra - kept_extra)),
            min(extra_limit, kept_extra + (normal - kept_normal)))

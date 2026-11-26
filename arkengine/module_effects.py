"""Equipment overlays for the selected phase. Character data stays immutable."""
from .opening_cards import _unlocked


def selected_branch(tables, card):
    if card["module"] is None:
        return None
    ids = card["module_ids"]
    if card["module"] == "unknown" or len(ids) != 1:
        return "unknown"
    item = tables.read("excel/uniequip_table.json")["equipDict"][ids[0]]
    label = item["typeName1"] + "-" + item["typeName2"]
    if (card.get("operator_id", item["charId"]) != item["charId"] or
            card["module"] not in (ids[0], label, item["uniEquipName"], item["typeIcon"])):
        return "unknown"
    return item["typeIcon"]


def equipped_phase(tables, card):
    """Only for a concrete battle loadout. Historical unknowns remain unknown."""
    if card["module"] is None:
        return {}
    ids = card["module_ids"]
    if card["module"] == "unknown" or len(ids) != 1 or not card.get("module_level"):
        raise ValueError("模组分支或等级未知")
    return next(p for p in tables.read("excel/battle_equip_table.json")[ids[0]]["phases"]
                if p["equipLevel"] == card["module_level"])


def module_stat(phase, key):
    return next((b["value"] for b in phase.get("attributeBlackboard", ()) if b["key"] == key), 0)


def active_board(rows, elite, level, potential):
    eligible = [r for r in rows if r["requiredPotentialRank"] < potential
                and _unlocked(r["unlockCondition"], elite, level)]
    return {b["key"]: b["value"] for b in eligible[-1]["blackboard"]} if eligible else {}


def talent_rows(phase, talent_index, prefab_key, fallback):
    for part in phase.get("parts", ()):
        if part["isToken"] or part["target"] not in ("TALENT", "TALENT_DATA_ONLY"):
            continue
        rows = [r for r in part["addOrOverrideTalentDataBundle"]["candidates"] or ()
                if r["talentIndex"] == talent_index and r["prefabKey"] == prefab_key]
        if rows:
            return rows
    return fallback


def trait_board(unit, phase, card):
    args = card["elite"], card["level"], card["potential"]
    board = active_board(unit["trait"]["candidates"], *args)
    for part in phase.get("parts", ()):
        if not part["isToken"] and part["target"] in ("TRAIT", "TRAIT_DATA_ONLY"):
            board.update(active_board(part["overrideTraitDataBundle"]["candidates"] or (), *args))
    return board

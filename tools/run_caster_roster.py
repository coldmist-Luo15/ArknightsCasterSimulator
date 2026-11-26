"""Load every obtainable V079 caster through the opening replay's card resolver."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arkengine.opening_cards import resolve_card
from arkengine.opening_scene import PinnedTables


def roster(tables):
    operators = []
    loadouts = 0
    for key, unit in tables.read("excel/character_table.json").items():
        if not (key.startswith("char_") and unit["profession"] == "CASTER" and not unit["isNotObtainable"]):
            continue
        elite = len(unit["phases"])-1
        config = dict(elite=elite, level=unit["phases"][elite]["maxLevel"], potential=unit["maxPotentialLevel"]+1,
                      skill=None, module=None, module_level=None)
        card = resolve_card(tables, key, **config)
        ordinary_modules = [m for m in card["caster_config"]["modules"] if not m["special"]]
        choices = list(enumerate(card["caster_config"]["skills"], start=1)) or [(None, {})]
        for slot, skill in choices:
            for module in [None, *ordinary_modules]:
                pick = dict(config, skill=slot, skill_level=skill.get("max_level"),
                            module=module["id"] if module else None,
                            module_level=max(module["levels"]) if module else None)
                loaded = resolve_card(tables, key, **pick)
                if loaded["skill_id"] != skill.get("id") or (module and loaded["module_ids"] != [module["id"]]):
                    raise ValueError(f"{key} 的技能或模组绑定不符")
                loadouts += 1
        operators.append(dict(id=key, name=card["name"], config=config, cost=card["cost"], **card["caster_config"]))
    return dict(operators=operators, operator_count=len(operators), branches=dict(Counter(r["branch"] for r in operators)),
                ordinary_abilities=dict(Counter(r["default_attack"]["ability"] for r in operators)),
                skill_bindings=sum(len(r["skills"]) for r in operators),
                advanced_module_bindings=sum(len(r["modules"]) for r in operators),
                ordinary_module_bindings=sum(not m["special"] for r in operators for m in r["modules"]),
                maximum_progression_ordinary_loadouts_resolved=loadouts,
                scope="loaded_base_cards_skill_and_module_options_with_default_prefab_bindings",
                source=tables.source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    payload = json.dumps(roster(PinnedTables()), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")

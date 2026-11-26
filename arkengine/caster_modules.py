"""Default-range override supplied by the V079 SPC-X module talent."""
from .opening_scene import bundle_environment
from .skill_assets import _script_classes


def splash_range_module(tables, card):
    ids = card.get("module_ids",())
    if card.get("module") in (None,"unknown") or len(ids) != 1:
        return None
    mid, = ids
    equip = tables.read("excel/uniequip_table.json")["equipDict"][mid]
    if equip["typeIcon"] != "spc-x":
        return None
    cache = ("splash_caster_range",mid,card["module_level"])
    if cache in tables._prefab_data:
        profile,source = tables._prefab_data[cache]
        tables.source["splash_module_prefabs"] = source
        return profile
    raw,source = tables.verified_bundle("battle/prefabs/[uc]equips.ab")
    assets = bundle_environment(raw)
    scripts = _script_classes(tables)
    choices = set()
    attack = set()
    for phase in tables.read("excel/battle_equip_table.json")[mid]["phases"]:
        if card["module_level"] not in (None,phase["equipLevel"]):
            continue
        parts = [p for p in phase["parts"] if not p["isToken"] and p["target"]=="TALENT" and
                 any(c["rangeId"] for c in p["addOrOverrideTalentDataBundle"]["candidates"] or ())]
        part, = parts
        option, = part["addOrOverrideTalentDataBundle"]["candidates"]
        root = assets.container[f"dyn/battle/prefabs/[uc]equips/{part['resKey']}.prefab"].deref()
        components = {}
        for row in root.read_typetree()["m_Component"]:
            obj = root.assets_file.objects[row["component"]["m_PathID"]]
            if obj.type.name == "MonoBehaviour":
                name, = scripts[obj.serialized_type.old_type_hash.hex()]
                components[name["name"]] = obj.read_typetree()
        talent = components.get("Talent",{})
        if (set(components) != {"EmptyAbility","Talent"} or not all(c["m_Enabled"] for c in components.values())
                or option["talentIndex"] != -1 or option["validModeIndices"] or option["requiredPotentialRank"]
                or option["blackboard"] or not talent["_overrideDefaultRangeId"] or talent["_checkValidBySkillIndex"]
                or talent["_defaultModeIndex"] != -1 or talent["_overwriteTalentKey"] != option["prefabKey"]):
            raise ValueError(f"{mid} 的默认攻击范围覆写与已核对模组组件不符")
        choices.add(option["rangeId"])
        stats = {b["key"]:b["value"] for b in phase["attributeBlackboard"]}
        if (card["operator_id"] == "char_1011_lava2" and stats.keys() == {"atk","max_hp"} and
                all(p["target"]=="DISPLAY" or p is part or (p["target"]=="TALENT_DATA_ONLY" and
                    all(c["talentIndex"]==0 and c["prefabKey"]=="1" and
                        {b["key"] for b in c["blackboard"]}=={"lava2_t_1[ally].sp","lava2_t_1[self].sp"}
                        for c in p["addOrOverrideTalentDataBundle"]["candidates"])) for p in phase["parts"])):
            attack.add(stats["atk"])
    range_id, = choices
    tables.source["splash_module_prefabs"] = source
    profile = dict(module_id=mid,range_id=range_id,ordinary_attack_bonus=next(iter(attack)) if len(attack)==1 else None)
    tables._prefab_data[cache] = profile,source
    return profile


def core_penetration_module(tables,card):
    ids = card.get("module_ids",())
    if card.get("module") in (None,"unknown") or len(ids)!=1 or not card["module_level"]:
        return None
    mid, = ids
    if tables.read("excel/uniequip_table.json")["equipDict"][mid]["typeIcon"] != "ccr-x":
        return None
    cache = ("core_caster_penetration",mid,card["module_level"])
    if cache in tables._prefab_data:
        profile,source = tables._prefab_data[cache]
        tables.source["core_module_prefabs"] = source
        return profile
    phase, = (p for p in tables.read("excel/battle_equip_table.json")[mid]["phases"] if p["equipLevel"]==card["module_level"])
    trait, = (p for p in phase["parts"] if p["target"]=="TRAIT" and not p["isToken"])
    candidate, = trait["overrideTraitDataBundle"]["candidates"]
    modifier, = candidate["blackboard"]
    raw,source = tables.verified_bundle("battle/prefabs/[uc]equips.ab")
    root = bundle_environment(raw).container[f"dyn/battle/prefabs/[uc]equips/{trait['resKey']}.prefab"].deref()
    scripts = _script_classes(tables)
    behaviours = [o for row in root.read_typetree()["m_Component"]
                  if (o:=root.assets_file.objects[row["component"]["m_PathID"]]).type.name=="MonoBehaviour"]
    ability, = behaviours
    cfg = ability.read_typetree()
    buff, = cfg["_buffs"]
    if (scripts[ability.serialized_type.old_type_hash.hex()][0]["name"] != "PassiveBuffAbility" or not cfg["m_Enabled"]
            or modifier["key"]!="magic_resist_penetrate_fixed" or modifier["value"]!=10 or buff["templateKey"]!="empty"
            or buff["attributes"]["attributeModifiers"] != [dict(attributeType=27,formulaItem=0,value=0.0,
                loadFromBlackboard=1,fetchBaseValueFromSourceEntity=0)]):
        raise ValueError(f"{mid} 的固定法抗穿透组件不符")
    profile = dict(fixed_resistance_penetration=modifier["value"],phase=phase,
                   attack_bonus=next((b["value"] for b in phase["attributeBlackboard"] if b["key"]=="atk"),0))
    tables._prefab_data[cache] = profile,source
    tables.source["core_module_prefabs"] = source
    return profile

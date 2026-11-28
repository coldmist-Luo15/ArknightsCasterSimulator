"""Resolve the requested CN caster loadouts against the verified local game tables.

A skill's display grid and its attack, summon and splash selectors are distinct.
Module data is returned for exactly one phase, separate from the operator's traits.
"""
from .attributes import base_integer_at_level
from .caster_runtime import current_talents


GALLUS = "char_4227_gallus"
EXTRA_CASTERS = frozenset(("夜魔", "蜜蜡", "和弦", "雪绒", "GALLUS²", "杜林"))
REMOVED_SKILL_SLOTS = {
    "char_4046_ebnhlz": (1, 2),
    "char_134_ifrit": (1,),
    "char_180_amgoat": (1,),
    "char_472_pasngr": (1,),
    "char_4027_heyak": (1,),
    "char_4204_mantra": (1,),
    "char_466_qanik": (1,),
}
PANEL_FIELDS = {
    "atk": "atk", "def": "def", "max_hp": "maxHp", "magic_resistance": "magicResistance",
    "attack_speed": "attackSpeed", "cost": "cost", "respawn_time": "respawnTime",
}


def target_casters(tables):
    return {key: unit for key, unit in tables.read("excel/character_table.json").items()
            if key.startswith("char_") and not unit["isNotObtainable"] and unit["profession"] == "CASTER"
            and (unit["rarity"] == "TIER_6" or unit["name"] in EXTRA_CASTERS)}


def _matching(value, choices, label):
    normalized = str(value).strip().casefold().replace("δ", "d")
    matches = [item for item, aliases in choices
               if any(normalized == str(alias).strip().casefold().replace("δ", "d") for alias in aliases)]
    if len(matches) != 1:
        raise ValueError(f"{label}不存在或不唯一：{value!r}")
    return matches[0]


def _max_progression(unit):
    elite = len(unit["phases"]) - 1
    return dict(elite=elite, level=unit["phases"][elite]["maxLevel"], potential=unit["maxPotentialLevel"]+1)


def _skills(tables, uid, unit):
    data = tables.read("excel/skill_table.json")
    for slot, binding in enumerate(unit["skills"], 1):
        if slot in REMOVED_SKILL_SLOTS.get(uid, ()):
            continue
        sid = binding["skillId"]
        levels = data[sid]["levels"]
        maximum = 1 + len(unit["allSkillLvlup"]) + len(binding["levelUpCostCond"] or ())
        if maximum > len(levels):
            raise ValueError(f"可训练等级超过游戏表记录 {sid}")
        yield slot, binding, levels[:maximum]


def _modules(tables, uid):
    data = tables.read("excel/uniequip_table.json")
    battle = tables.read("excel/battle_equip_table.json")
    for mid in data["charEquip"].get(uid, ()):
        item = data["equipDict"][mid]
        if (item["tmplId"] or item["charId"]) != uid or item["uniEquipId"] != mid:
            raise ValueError(f"模组归属与索引不一致 {mid}")
        if item["type"] == "ADVANCED" and not item["isSpecialEquip"]:
            yield mid, item, battle[mid]["phases"]


def _base_stats(unit, level):
    # The panel before traits, talents or equipment, with full trust and potential.
    phase = unit["phases"][-1]
    frames = phase["attributesKeyFrames"]
    base, = (r["data"] for r in frames if r["level"] == phase["maxLevel"])
    trust = max(unit["favorKeyFrames"], key=lambda row: row["level"])["data"]
    stats = {field: base_integer_at_level(frames, level, field) + trust[field]
             for field in (*PANEL_FIELDS.values(), "blockCnt")}
    stats["baseAttackTime"] = base["baseAttackTime"] + trust["baseAttackTime"]
    for rank in unit["potentialRanks"]:
        for item in (rank["buff"] or {}).get("attributes", {}).get("attributeModifiers", ()):
            field = PANEL_FIELDS.get(item["attributeType"].lower())
            if (field is None or item["formulaItem"] != "ADDITION" or item["loadFromBlackboard"]
                    or item["fetchBaseValueFromSourceEntity"]):
                raise ValueError(f"未解析的潜能属性 {unit['name']}: {item}")
            stats[field] += item["value"]
    return stats


def _selected_module(tables, uid, module, level):
    if module is None or isinstance(module, str) and module.strip().lower() in ("none", "无", "无模组", "不带模组"):
        if level is not None:
            raise ValueError("无模组不能指定模组等级")
        return None, None, None
    choices = [((mid, item, phases), (mid, item["uniEquipName"], item["typeIcon"], item["typeName2"]))
               for mid, item, phases in _modules(tables, uid)]
    mid, item, phases = _matching(module, choices, "所选干员的模组")
    if level is None:
        level = max(p["equipLevel"] for p in phases)
    selected = [p for p in phases if p["equipLevel"] == level and type(level) is int]
    if len(selected) != 1:
        raise ValueError(f"{mid} 模组等级无效 {level!r}")
    return mid, item, selected[0]


def resolve_caster_loadout(tables, operator, skill=None, module=None, *, skill_level=None, module_level=None, level=None):
    """Resolve the selected level and default unspecified upgrades to maximum ranks."""
    uid, unit = _matching(operator, [((k, u), (k, u["name"], u["appellation"])) for k, u in target_casters(tables).items()],
                          "目标术师")
    if unit["skills"]:
        choices = [((slot, binding, levels), (slot, f"S{slot}", binding["skillId"], levels[0]["name"]))
                   for slot, binding, levels in _skills(tables, uid, unit)]
        slot, binding, levels = _matching(skill, choices, "所选干员的技能")
        if skill_level is None:
            skill_level = len(levels)
        if type(skill_level) is not int or not 1 <= skill_level <= len(levels):
            raise ValueError(f"{unit['name']} S{slot} 技能等级无效 {skill_level!r}")
        skill_row = levels[skill_level-1]
    elif skill is None and skill_level is None:
        slot = binding = skill_row = None
    else:
        raise ValueError(f"{unit['name']} 没有可携带的技能")
    mid, item, phase = _selected_module(tables, uid, module, module_level)
    progression = _max_progression(unit)
    if level is not None:
        if type(level) is not int or not 1 <= level <= progression["level"]:
            raise ValueError(f"{unit['name']} 干员等级无效 {level!r}")
        if item and level < item["unlockLevel"]:
            raise ValueError(f"{unit['name']} 的所选模组在 {item['unlockLevel']} 级解锁")
        progression["level"] = level
    base = _base_stats(unit, progression["level"])
    stats = base.copy()
    if phase:
        for entry in phase["attributeBlackboard"]:
            stats[PANEL_FIELDS[entry["key"]]] += entry["value"]
    ranges = tables.read("excel/range_table.json")
    traits = dict(talents=[unit["trait"]]) if unit["trait"] else dict(talents=[])
    return dict(operator_id=uid, name=unit["name"], branch=unit["subProfessionId"], **progression,
                skill=slot, skill_id=binding["skillId"] if binding else None, skill_level=skill_level, skill_row=skill_row, skill_binding=binding,
                base_range=ranges[unit["phases"][-1]["rangeId"]], skill_range=ranges[skill_row["rangeId"]] if skill_row and skill_row["rangeId"] else None,
                base_talents=current_talents(unit, progression), base_traits=current_talents(traits, progression),
                base_attributes=base, panel_attributes=stats,
                module=mid, module_level=phase["equipLevel"] if phase else None, module_ids=[mid] if mid else [],
                module_phase=phase, module_info=item)


def supports_combat_loadout(card):
    unmodded_slots={"char_180_amgoat":(2,3),"char_4204_mantra":(2,),"char_4133_logos":(1,2,3), "char_4146_nymph":(2,3),
                    "char_1046_sbell2":(1,2,3),"char_4229_aphris":(2,3),"char_4226_veen":(2,3),"char_466_qanik":(2,),
                    "char_4080_lin":(1,2,3),"char_4046_ebnhlz":(3,)}
    return card["operator_id"]==GALLUS or bool(card["module"]) or card["skill"] in unmodded_slots.get(card["operator_id"],())


def require_available_skill(operator_id, slot):
    if slot in REMOVED_SKILL_SLOTS.get(operator_id, ()):
        raise ValueError(f"{operator_id} S{slot} 已从术师计算器移除")


def shared_barrier_loadout(tables,pick,*,position_only=False):
    """Resolve a deployed ally for shared barriers or for Aphris beam geometry."""
    if "operator" not in pick:
        return pick
    units=tables.read("excel/character_table.json")
    uid,unit=_matching(pick["operator"],[((k,u),(k,u["name"],u["appellation"])) for k,u in units.items()
                       if not u["isNotObtainable"] and k.startswith("char_")],"持盾干员")
    if ((not position_only and uid in ("char_4080_lin","char_426_billro","char_4046_ebnhlz")) or
            (uid=="char_4046_ebnhlz" and pick.get("skill")==3)):
        return stage_loadout(tables,pick)
    mid,_,phase=_selected_module(tables,uid,pick.get("module"),pick.get("module_level"))
    slot=pick.get("skill")
    if position_only and slot in REMOVED_SKILL_SLOTS.get(uid,()):
        slot=None
    return dict(operator_id=uid,**_max_progression(unit),skill=slot,skill_level=pick.get("skill_level") if slot else None,
                module=mid,module_level=phase["equipLevel"] if phase else None,
                **({"skin_id":pick["skin_id"]} if "skin_id" in pick else {}))


def shared_barrier_panel(tables,config):
    uid=config["operator_id"]
    unit=tables.read("excel/character_table.json")[uid]
    frames=unit["phases"][config["elite"]]["attributesKeyFrames"]
    first,last=frames[0],frames[-1]
    fraction=(config["level"]-first["level"])/max(1,last["level"]-first["level"])
    trust=unit["favorKeyFrames"][-1]["data"] if unit["favorKeyFrames"] else {}
    panel={}
    for field in ("maxHp","def","magicResistance","respawnTime"):
        base=first["data"][field]
        panel[field]=round(base+(last["data"][field]-base)*fraction)+trust.get(field,0)
    for rank in unit["potentialRanks"][:config["potential"]-1]:
        for item in (rank["buff"] or {}).get("attributes",{}).get("attributeModifiers",()):
            field=PANEL_FIELDS.get(item["attributeType"].lower())
            if field in panel:
                panel[field]+=item["value"]
    _,_,phase=_selected_module(tables,uid,config["module"],config["module_level"])
    for item in (phase or {}).get("attributeBlackboard",()):
        field=PANEL_FIELDS.get(item["key"])
        if field in panel:
            panel[field]+=item["value"]
    for talent in unit["talents"] or ():
        unlocked=[row for row in talent["candidates"] or () if row["requiredPotentialRank"]<config["potential"]
                  and (int(row["unlockCondition"]["phase"][-1]),row["unlockCondition"]["level"])<=(config["elite"],config["level"])]
        if unlocked and unlocked[-1]["name"] in ("稳妥降落","熟练猎手"):
            panel["respawnTime"]+=sum(x["value"] for x in unlocked[-1]["blackboard"] if x["key"]=="respawn_time")
    return dict(config,branch=unit["subProfessionId"],panel_attributes=panel,module_phase=phase)


def stage_loadout(tables,pick):
    """Expand a caster shortcut into the same squad record used by the opening and stage replays."""
    if "operator" not in pick:
        require_available_skill(pick.get("operator_id"), pick.get("skill"))
        return pick
    card=resolve_caster_loadout(tables,**{k:v for k,v in pick.items() if k!="skin_id"})
    if not supports_combat_loadout(card):
        raise ValueError("术师快捷装载需要选择一个常规模组")
    config={k:card[k] for k in ("operator_id","elite","level","potential","skill","skill_level","module","module_level")}
    if "skin_id" in pick:
        config["skin_id"]=pick["skin_id"]
    return config


def caster_catalog(tables):
    """Enumerate supported skill slots, equipment branches and legal upgrade levels."""
    operators = []
    ranges = tables.read("excel/range_table.json")
    for uid, unit in target_casters(tables).items():
        skills = []
        for slot, binding, levels in _skills(tables, uid, unit):
            row = levels[-1]
            skills.append(dict(slot=slot, id=binding["skillId"], name=row["name"], max_level=len(levels),
                               range=ranges[row["rangeId"]] if row["rangeId"] else None,
                               prefab=row["prefabId"], summon=binding["overrideTokenKey"],
                               sp=row["spData"], duration=row["duration"], blackboard=row["blackboard"]))
        modules = [dict(id=mid, name=item["uniEquipName"], branch=item["typeIcon"].upper(), special=item["isSpecialEquip"],
                        levels=[p["equipLevel"] for p in phases])
                   for mid, item, phases in _modules(tables, uid)]
        operators.append(dict(id=uid, name=unit["name"], branch=unit["subProfessionId"], **_max_progression(unit),
                              base_range=ranges[unit["phases"][-1]["rangeId"]], skills=skills, modules=modules))
    return dict(source=tables.source, operators=operators)


def caster_loadout_options(tables):
    """Compact equipment and skill menus for choosing a squad before writing its request."""
    catalog = caster_catalog(tables)
    return {row["id"]: dict(name=row["name"],
                skills={f"S{skill['slot']}": skill["name"] for skill in row["skills"]},
                modules={m["branch"]: dict(id=m["id"], name=m["name"], levels=m["levels"])
                         for m in row["modules"]})
            for row in catalog["operators"]}


_SOLE_MODULE = object()


def pick_caster(options, operator_id, skill, *, branch=_SOLE_MODULE):
    """Use the sole available branch by default, or select a branch or None explicitly."""
    row = options[operator_id]
    if f"S{skill}" not in row["skills"] and not (skill is None and not row["skills"]):
        raise ValueError(f"{row['name']} 的候选技能为 {list(row['skills'])}")
    pick = dict(operator_id=operator_id, skill=skill)
    if branch is _SOLE_MODULE:
        if len(row["modules"]) > 1:
            raise ValueError(f"{row['name']} 有多个可选模组，请指定分支 {', '.join(row['modules'])}")
        branch = next(iter(row["modules"]), None)
    if branch is not None:
        if branch not in row["modules"]:
            raise ValueError(f"{row['name']} 的候选模组为 {', '.join(row['modules']) or '无'}")
        module = row["modules"][branch]
        pick.update(module=module["id"], module_level=max(module["levels"]))
    return pick

"""Resolve ordinary first-placement cards using the pinned tables and official prefab.

Potentials and skill slots are one-based. A missing potential, level or module
level describes a set of configurations. None means no module, "unknown"
includes every ordinary module available at the supplied promotion and level.
"""

# The pinned table's 17 obtainable three-stars have no advanced module or
# pre-deployment skill. Fang and Catapult share the self-cost talent below.
# Further complex actors need an audit for squad, card and first-use effects
# before ordinary table arithmetic can be reported as the payable cost.
REVIEWED_HIGH_RARITY = {"char_102_texas", "char_4080_lin", "char_4046_ebnhlz"}


def _check_int(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} 应在 {low} 到 {high} 之间")


def _quantity(value):
    if type(value) not in (int, float) or int(value) != value:
        raise ValueError(f"尚未支持非整数费用 {value!r}")
    return int(value)


def _unlocked(condition, elite, level):
    return (elite, level) >= (int(condition["phase"].removeprefix("PHASE_")), condition["level"])


def _potential_delta(unit, potential):
    delta = 0
    for rank in unit["potentialRanks"][:potential - 1]:
        if rank["buff"] is None:
            continue
        for mod in rank["buff"]["attributes"]["attributeModifiers"]:
            if mod["attributeType"] == "COST":
                if mod["formulaItem"] != "ADDITION" or mod["loadFromBlackboard"] or mod["fetchBaseValueFromSourceEntity"]:
                    raise ValueError("当前模型尚未支持此潜能费用计算方式")
                delta += _quantity(mod["value"])
    return delta


def _talent_costs(unit, elite, level, potential, *, beam_ally=False):
    initial, payable, sources = 0, 0, []
    for slot, talent in enumerate(unit["talents"] or []):
        eligible = [(index, row) for index, row in enumerate(talent["candidates"] or [])
                    if row["requiredPotentialRank"] < potential and _unlocked(row["unlockCondition"], elite, level)]
        if not eligible:
            continue
        index, row = eligible[-1]
        values = [bb["value"] for bb in row["blackboard"] if bb["key"] == "cost"]
        if not values:
            continue
        value, = values
        if row["name"] == "战术快递":
            initial += _quantity(value)
        elif row["name"] == "轻量化" or beam_ally and row["name"] in ("快速部署","小个子支援","稳妥降落","熟练猎手","冲锋领袖"):
            payable += _quantity(value)
        elif beam_ally:
            # Other cost fields belong to team auras or on-field events, not this card.
            continue
        else:
            raise ValueError(f"尚未确认天赋 {row['name']} 的费用生效对象")
        sources.append(f"talents/{slot}/candidates/{index}/blackboard")
    return initial, payable, sources


def _module_choices(tables, operator_id, module, module_level, elite, level):
    if module is None:
        if module_level is not None:
            raise ValueError("未装备模组时不能填写模组等级")
        return [(None, None)]
    if module == "unknown" and module_level is not None:
        raise ValueError("模组未知时请同时保留其等级未知")
    modules = tables.read("excel/uniequip_table.json")
    choices = [(None, None)] if module == "unknown" else []
    for mid in modules["charEquip"].get(operator_id, []):
        item = modules["equipDict"][mid]
        if item["charId"] != operator_id or item["type"] != "ADVANCED" or item["isSpecialEquip"]:
            continue
        label = f"{item['typeName1']}-{item['typeName2']}"
        if module not in (mid, label, "unknown"):
            continue
        if not _unlocked({"phase": item["unlockEvolvePhase"], "level": item["unlockLevel"]}, elite, level):
            continue
        phases = tables.read("excel/battle_equip_table.json")[mid]["phases"]
        choices.extend((mid, p) for p in phases if module_level in (None, p["equipLevel"]))
    return choices


def _module_delta(module_id, phase, elite, level, potential):
    if not phase:
        return 0
    delta = sum(_quantity(item["value"]) for item in phase["attributeBlackboard"] if item["key"] == "cost")
    for part in phase["parts"]:
        for row in part["addOrOverrideTalentDataBundle"]["candidates"] or []:
            if row["requiredPotentialRank"] >= potential or not _unlocked(row["unlockCondition"], elite, level):
                continue
            for bb in row["blackboard"]:
                if bb["key"] == "runtime_cost":
                    # SOL-Y's separate hidden talent reduces the card's first use.
                    # Its squad opening-DP talent and potential costs still apply.
                    if module_id != "uniequip_002_texas" or row["prefabKey"] != "20_root":
                        raise ValueError("尚未支持此模组的动态部署费用")
                    delta += _quantity(bb["value"])
    return delta


def _placement(tables, operator_id, unit, *, beam_ally=False):
    template = tables.placement(operator_id)
    condition = template["_buildCondition"]
    standard_mask = {"MELEE": 1, "RANGED": 2}.get(unit["position"])
    dual = beam_ally and condition["buildableType"] == 3
    if ((not dual and (standard_mask is None or condition["buildableType"] != standard_mask))
            or condition["advancedBuildableMask"] != 1
            or condition["extraBuildConditionArray"] not in (
                {"SerializedState": "null", "SerializedObjectReferences": []},
                {"SerializedState": "[]", "SerializedObjectReferences": []})
            or any(value for key, value in condition.items() if key not in {
                "buildableType", "advancedBuildableMask", "extraBuildConditionArray", "overlapOptions", "needSpecifyDirection"
            })):
        raise ValueError(f"尚未支持 {operator_id} 的特殊部署条件")
    return {"position": "ALL" if dual else unit["position"], "slots": template["_occupiedRemainingCharacterCnt"],
            "needs_direction": bool(condition["needSpecifyDirection"])}


def resolve_card(tables, operator_id, *, elite, level, potential, skill, module, module_level,
                 skin_id=None, skill_level=None, shared_barrier_recipient=False, beam_ally=False):
    unit = tables.read("excel/character_table.json").get(operator_id)
    if not unit or unit["isNotObtainable"]:
        raise ValueError(f"未知或不可获得的干员 {operator_id}")
    if not shared_barrier_recipient and unit["profession"] != "CASTER" and unit["rarity"] != "TIER_3" and operator_id not in REVIEWED_HIGH_RARITY:
        raise ValueError(f"尚未核对 {operator_id} 的开局编队、能力与卡片效果")
    appearance = {}
    if skin_id is not None:
        record = tables.read("excel/skin_table.json")["charSkins"].get(skin_id)
        if not record or record["charId"] != operator_id:
            raise ValueError(f"外观 {skin_id} 与干员 {operator_id} 不符")
        if record["battleSkin"]["overwritePrefab"] or record["tmplId"]:
            raise ValueError("暂未支持更换角色战斗模板的外观")
        appearance = dict(skin_id=skin_id, battle_skin=record["battleSkin"]["skinOrPrefabId"].lower())
    _check_int(elite, 0, len(unit["phases"]) - 1, "精英阶段")
    phase = unit["phases"][elite]
    if level is not None:
        _check_int(level, 1, phase["maxLevel"], "等级")
    if potential is not None:
        _check_int(potential, 1, unit["maxPotentialLevel"] + 1, "潜能")
    if module_level is not None:
        _check_int(module_level, 1, 3, "模组等级")
    selected = None
    if skill is not None:
        from .caster_catalog import require_available_skill

        _check_int(skill, 1, len(unit["skills"]), "技能序号")
        require_available_skill(operator_id, skill)
        selected = unit["skills"][skill - 1]
    skill_conditions = [selected["unlockCond"]] if selected else []
    if skill_level is not None:
        if not selected:
            raise ValueError("指定技能等级需要先选择技能")
        levels = tables.read("excel/skill_table.json")[selected["skillId"]]["levels"]
        requirements = [*unit["allSkillLvlup"], *(selected["levelUpCostCond"] or [])]
        _check_int(skill_level, 1, min(len(levels), len(requirements) + 1), "技能等级（专三为 10）")
        skill_conditions.extend(row["unlockCond"] for row in requirements[:skill_level - 1])
    bases = {frame["data"]["cost"] for frame in phase["attributesKeyFrames"]}
    if len(bases) != 1 or any(frame["data"]["cost"] for frame in unit["favorKeyFrames"]):
        raise ValueError("尚未支持依赖等级插值或信赖的费用")
    base = _quantity(bases.pop())
    potentials = [potential] if potential is not None else range(1, unit["maxPotentialLevel"] + 2)
    costs, bonuses, refs, modules = set(), set(), set(), set()
    for lv in ([level] if level is not None else range(1, phase["maxLevel"] + 1)):
        if any(not _unlocked(condition, elite, lv) for condition in skill_conditions):
            continue
        for mid, mp in _module_choices(tables, operator_id, module, module_level, elite, lv):
            if mp:
                modules.add(mid)
            for pot in potentials:
                initial, talent_delta, rows = _talent_costs(unit, elite, lv, pot, beam_ally=beam_ally)
                costs.add(base + _potential_delta(unit, pot) + talent_delta + _module_delta(mid, mp, elite, lv, pot))
                bonuses.add(initial)
                refs.update(rows)
    if not costs:
        raise ValueError(f"{operator_id} 在指定精英、等级下没有合法的技能/模组配置")
    caster = {}
    if unit["profession"] == "CASTER":
        from .caster_runtime import caster_options
        caster["caster_config"] = caster_options(tables, operator_id, unit)
    return dict(operator_id=operator_id, name=unit["name"], elite=elite, level=level, potential=potential,
                skill=skill, skill_id=selected["skillId"] if selected else None, module=module,
                module_level=module_level, module_ids=sorted(modules), base_cost=base,
                cost=next(iter(costs)) if len(costs) == 1 else None, cost_options=sorted(costs),
                initial_cost_bonus=next(iter(bonuses)) if len(bonuses) == 1 else None,
                initial_cost_bonus_options=sorted(bonuses), talent_cost_refs=sorted(refs),
                **({"skill_level": skill_level} if skill_level is not None else {}),
                **appearance, **caster, **_placement(tables, operator_id, unit, beam_ally=beam_ally))


def tile_permits_card(card, tile):
    """Static type check for ordinary single-player ground or ranged placement."""
    if tile["playerSideMask"] != "ALL":
        raise ValueError("尚未支持有阵营限制的地块")
    kind = tile["buildableType"]
    if kind not in {"NONE", "MELEE", "RANGED", "ALL"}:
        raise ValueError(f"未知的地块部署类型 {kind}")
    return kind in ("ALL", card["position"]) or card["position"] == "ALL" and kind in ("MELEE", "RANGED")

"""Ebenholz equipment proc and projectile attachment bindings, V079."""
import json

from .module_effects import selected_branch
from .opening_scene import bundle_environment
from .skill_assets import _script_classes


def isolated_hit_talent(tables, key):
    fields = tables._component_fields("characters", key, ("_additiveActiveBuffs", "_targetFamilyMask",
        "_targetValidator", "_probKey"), "mystic_base_hit_attachment", children=("Talents", "2"))
    buff, = fields["_additiveActiveBuffs"]
    template = buff["templateKey"]
    nodes = tables.read("battle/buff_template_data.json")[template]["eventToActions"]["ON_BUFF_START"]
    check, hit, effect = nodes
    if (template != "ebnhlz_t_2" or fields["_targetFamilyMask"] != 31 or fields["_targetValidator"]["m_PathID"]
            or fields["_probKey"] or buff["loadFromDB"] or not check["$type"].endswith("+CheckHasEnemyInRange, Assembly-CSharp")
            or (check["_soureceType"], check["_condType"], check["_countKey"]) != ("BUFF_OWNER", "LE", "cnt")
            or not all(check[k] for k in ("_excludeSource", "_checkRadius", "_needCheckCount"))
            or (hit["_damageType"], hit["_atkScaleVar"], hit["_sourceType"], hit["_targetType"]) !=
                ("MAGICAL", "atk_scale", "SOURCE", "TARGET") or hit["_forceUseProjectileCachedAtk"]
            or not effect["$type"].endswith("+CreateEffect, Assembly-CSharp")):
        raise ValueError("黑键本体的倚音弹体触发配置不符")
    return dict(status="isolated_projectile_hit_attachment", template=template)


def delta_hit_talent(tables,card,projectiles,board):
    """Read the ordered on-hit attachments and verify that the projectiles use them."""
    from .caster_prefabs import caster_prefab_evidence
    ev=caster_prefab_evidence(tables,card["operator_id"],card["skill"],skill_level=card["skill_level"],
                              module_id=card["module_ids"][0],module_level=card["module_level"])
    isolated_hit_talent(tables,card["operator_id"])
    for key in projectiles:
        for obj in ev["projectiles"][key].values():
            controller,=(c["fields"]["_actionController"] for c in obj["components"]
                         if c["active"] and c["enabled"] and c["script"]=="SimpleProjectile")
            if any(controller.values()):
                raise ValueError("黑键弹体含额外的命中动作")
    scale=board.get("element_atk_scale",0)
    if scale:
        talent,=(c["fields"] for asset in ev["equipment"].values() for c in asset["components"]
                 if c["active"] and c["enabled"] and c["script"]=="PassiveAttachmentAbility")
        first,second=talent["_additiveActiveBuffs"]
        if (first["templateKey"]!="ebnhlz_t_2" or talent["_targetFamilyMask"]!=31
                or talent["_targetValidator"]["m_PathID"] or talent["_probKey"]):
            raise ValueError("黑键 Δ 的主目标附加动作变化")
        branch,=ev["templates"][second["templateKey"]]["eventToActions"]["ON_BUFF_START"]
        check=branch["_conditionNode"]
        damage,=branch["_succeedNodes"]
        if ((check["_target"],check["_checkRecoveryType"],check["_elementType"])!=("BUFF_OWNER",True,"DARK")
                or (damage["_damageType"],damage["_atkScaleVar"],damage["_sourceType"],damage["_targetType"])
                !=("ELEMENT","element_atk_scale","SOURCE","TARGET") or damage["_forceUseProjectileCachedAtk"]):
            raise ValueError("黑键 Δ 的凋亡附加伤害配置变化")
    return dict(status="delta_projectile_hit_attachment",necrosis_scale=scale)


def y_hit_talent(tables, prefab, projectiles):
    key = ("ebnhlzy_hit_talent", prefab, tuple(projectiles))
    if key in tables._prefab_data:
        rule, source = tables._prefab_data[key]
        tables.source["mystic_hit_attachment"] = source
        return rule
    raw, equip_source = tables.verified_bundle("battle/prefabs/[uc]equips.ab")
    root = bundle_environment(raw).container[f"dyn/battle/prefabs/[uc]equips/{prefab}.prefab"].deref()
    scripts = _script_classes(tables)
    components = {}
    for ref in root.read_typetree()["m_Component"]:
        obj = root.assets_file.objects[ref["component"]["m_PathID"]]
        if obj.type.name == "MonoBehaviour":
            script, = scripts[obj.serialized_type.old_type_hash.hex()]
            components[script["name"]] = obj.read_typetree()
    talent = components["Talent"]
    ability = components["PassiveAttachmentAbility"]
    buff, = ability["_additiveActiveBuffs"]
    template = buff["templateKey"]
    if (not talent["m_Enabled"] or talent["_overwriteTalentKey"] != "2_override" or not ability["m_Enabled"]
            or ability["_targetFamilyMask"] != 31 or ability["_targetValidator"]["m_PathID"] or ability["_probKey"]
            or template != "ebnhlz_e_003_t_2" or template != buff["buffKey"] or buff["loadFromDB"]):
        raise ValueError("倚音命中效果与已核对的 Y 模组不符")
    event, = tables.read("battle/buff_template_data.json")[template]["eventToActions"]["ON_BUFF_START"]
    check = event["_conditionNode"]
    single, *_ = event["_succeedNodes"]
    splash, *_ = event["_failNodes"]
    selection = splash["_targetOptions"]
    if (not check["$type"].startswith("Torappu.Battle.Action.Nodes+CheckHasEnemyInRange,")
            or (check["_soureceType"], check["_countKey"], check["_condType"]) != ("BUFF_OWNER", "cnt", "LE")
            or not check["_checkRadius"] or not check["_excludeSource"] or not check["_needCheckCount"]
            or any(check[k] for k in ("_useAttackRange", "_checkId", "_includedBuffIds", "_excludedBuffIds"))
            or not single["$type"].startswith("Torappu.Battle.Action.Nodes+AdvancedApplyDamage,")
            or single["_baseOnHostAtk"] or single["_forceUseProjectileCachedAtk"]
            or (single["_sourceType"], single["_targetType"], single["_damageType"], single["_atkScaleVar"])
            != ("SOURCE", "TARGET", "MAGICAL", "atk_scale")
            or not splash["$type"].startswith("Torappu.Battle.Action.Nodes+AOEDamage,")
            or (splash["_sourceType"], splash["_targetType"], splash["_damageType"], splash["_damageScale"])
            != ("BUFF_SOURCE", "BUFF_OWNER", "MAGICAL", "atk_scale_2")
            or not splash["_useAbilitySourceSide"] or not splash["_excludeTarget"] or not splash["_useRadius"]
            or splash["_useDynamicTreeForRadius"] or splash["_useDamageFromBB"] or splash["_useAbilitySelector"]
            or splash["_filterType"] != "ALL" or splash["_maxTargetCountKey"]
            or (selection["targetSide"], selection["targetMotion"], selection["targetCategory"]) !=
               ("ENEMY", "ALL", "DEFAULT")
            or not selection["enableAdvancedOptions"] or any(selection[k] for k in
               ("checkUnitType", "ignoreTargetFree", "excludeSomeAbnormalFlags", "containSomeAbnormalFlags"))):
        raise ValueError("倚音半径、施加者或接收者分支不符")
    raw, projectile_source = tables.verified_bundle("battle/prefabs/[uc]projectiles.ab")
    env = bundle_environment(raw)
    controls = {}
    for projectile in projectiles:
        for suffix in ("", "_logic"):
            name = projectile + suffix
            obj = env.container[f"dyn/battle/prefabs/[uc]projectiles/{name}.prefab"].deref()
            ctl, = [tree["_actionController"] for c in obj.read_typetree()["m_Component"]
                    if "_actionController" in (tree := obj.assets_file.objects[c["component"]["m_PathID"]].read_typetree())]
            if any(ctl.values()):
                raise ValueError("弹体采用其他附加 Buff 或到达处理")
            controls[name] = obj.path_id
    rule = dict(status="y3_projectile_hit_attachment", template=template, excludes_original_from_splash=True)
    source = dict(equip=equip_source, prefab=prefab, component_root_id=root.path_id,
                  target_family_mask=ability["_targetFamilyMask"], projectiles=projectile_source,
                  projectile_roots=controls)
    tables._prefab_data[key] = (rule, source)
    tables.source["mystic_hit_attachment"] = source
    return rule


def storage_speed_trigger(tables, card):
    """Resolve the periodic Y-module condition independently of charge-key timing."""
    branch = selected_branch(tables, card)
    if branch in (None, "msc-x", "msc-d"):
        return dict(status="no_storage_speed_bonus")
    if branch != "msc-y":
        return dict(status="unresolved", reason="unreviewed_storage_speed_module")
    equip = tables.read("excel/battle_equip_table.json")[card["module_ids"][0]]
    prefabs = tuple(part["resKey"] for phase in equip["phases"] if card["module_level"] in (None, phase["equipLevel"])
                    for part in phase["parts"] if any(row["prefabKey"] == "10" for row in
                    part["addOrOverrideTalentDataBundle"]["candidates"] or []))
    cache_key = ("storage_speed_trigger", prefabs)
    if cache_key not in tables._prefab_data:
        raw, equip_src = tables.verified_bundle("battle/prefabs/[uc]equips.ab")
        env = bundle_environment(raw)
        timers = set()
        for key in prefabs:
            obj = env.container[f"dyn/battle/prefabs/[uc]equips/{key}.prefab"].deref()
            binds = [b for ref in obj.read_typetree()["m_Component"]
                     for b in obj.assets_file.objects[ref["component"]["m_PathID"]].read_typetree().get("_buffs", [])]
            buff, = binds
            timers.add((buff["templateKey"], buff["triggerLifeType"], buff["triggerInterval"],
                        bool(buff["waitFirstTriggerInterval"]), buff["firstTriggerInterval"]))
        if len(timers) != 1:
            return dict(status="unresolved", reason="different_storage_speed_timers")
        template, lifetime, interval, wait_first, first_interval = timers.pop()
        if template != "ebnhlz_e_003_trait" or lifetime != 2 or interval <= 0:
            return dict(status="unresolved", reason="unreviewed_storage_speed_timer")
        raw, template_src = tables.verified_bundle("config/buff_template_holder.ab")
        obj, = (o for o in bundle_environment(raw).objects if o.type.name == "MonoBehaviour")
        row, = (r for r in obj.read_typetree()["_templates"] if r["templateKey"] == template)
        event, = row["eventToActions"]["_items"]
        ordinary, = json.loads(event["value"]["SerializedState"])
        extra, = ordinary["_failNodes"]
        derived = "ebnhlz_e_003_trait[attr]"
        for branch, is_extra in ((ordinary, False), (extra, True)):
            check = branch["_conditionNode"]
            contains, invert, apply = branch["_succeedNodes"]
            modifier, = apply["_buff"]["attributes"]["attributeModifiers"]
            if (not branch["$type"].endswith("+IfElse") or not check["$type"].endswith("+FilterChargeTimes")
                    or (check["_targetType"], check["_compareType"], check["_times"], check["_isExCharge"])
                    != ("BUFF_OWNER", "GT", 0, is_extra)
                    or not contains["$type"].endswith("+CheckContainsBuff") or contains["_buffKeys"] != [derived]
                    or not invert["$type"].endswith("+IfNot") or not apply["$type"].endswith("+CreateBuff")
                    or apply["_buff"]["buffKey"] != derived or not apply["_isDerivedBuff"]
                    or modifier["attributeType"] != "ATTACK_SPEED" or modifier["formulaItem"] != "ADDITION"
                    or not modifier["loadFromBlackboard"]):
                return dict(status="unresolved", reason="unreviewed_storage_speed_condition")
        finish, = extra["_failNodes"]
        if event["key"] != 2 or not finish["$type"].endswith("+FinishDerivedBuff"):
            return dict(status="unresolved", reason="unreviewed_storage_speed_event")
        result = dict(status="periodic_storage_speed", event="ON_BUFF_TRIGGER", event_key=event["key"],
                      trigger_interval_seconds=interval, wait_first_trigger_interval=wait_first,
                      first_trigger_interval_seconds=first_interval, ordinary_charge_qualifies=True,
                      extra_charge_qualifies=True, derived_buff=derived)
        tables._prefab_data[cache_key] = (result, dict(equip=equip_src, prefabs=list(prefabs),
                                                       template_bundle=template_src, template=template))
    result, source = tables._prefab_data[cache_key]
    tables.source["storage_speed_trigger"] = source
    return result

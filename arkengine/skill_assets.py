"""Bind ordinary manual skill components to scripts in the matching Android player."""
from collections import defaultdict
import hashlib
import json
import zipfile

from .animation_assets import read_skin_actions
from .module_effects import equipped_phase, talent_rows
from .opening_cards import _unlocked
from .opening_scene import bundle_environment,load_unity_data


SCRIPT_ASSET_SHA256 = "3a9143ed23de6c0101d81c34540076bec1001b79fb944d8186f4122a18f68f91"


def _script_classes(tables):
    key = "player_script_classes"
    if key in tables._cache:
        return tables._cache[key]
    path = tables.root.parent / "2026-11-18-core-cn2791/client.apk"
    prefix = "assets/bin/Data/globalgamemanagers.assets.split"
    with zipfile.ZipFile(path) as apk:
        raw = b"".join(apk.read(prefix + str(i)) for i in range(9))
    if hashlib.sha256(raw).hexdigest() != SCRIPT_ASSET_SHA256:
        raise ValueError("V079 MonoScript 资源哈希不符")
    classes = defaultdict(list)
    for obj in load_unity_data(raw).objects:
        if obj.type.name == "MonoScript":
            row = obj.read_typetree()
            digest = bytes(row["m_PropertiesHash"][f"bytes[{i}]"] for i in range(16)).hex()
            classes[digest].append(dict(assembly=row["m_AssemblyName"], namespace=row["m_Namespace"],
                                        name=row["m_ClassName"], path_id=obj.path_id))
    tables._cache[key] = classes
    return classes


def suspendable_buff(tables, prefab):
    """Match both the controller and ability, rejecting ambiguous or extra scripts."""
    release = "battle/prefabs/[uc]skills.ab"
    key = (release, prefab)
    if key in tables._prefab_data:
        result, source = tables._prefab_data[key]
    else:
        raw, source = tables.verified_bundle(release)
        env = bundle_environment(raw)
        path = f"dyn/battle/prefabs/[uc]skills/{prefab}.prefab"
        root = env.container[path].deref()
        scripts = _script_classes(tables)
        specs = {}
        bindings = []
        for ref in root.read_typetree()["m_Component"]:
            obj = root.assets_file.objects[ref["component"]["m_PathID"]]
            if obj.type.name != "MonoBehaviour":
                continue
            fields = obj.read_typetree()
            if not fields["m_Enabled"]:
                continue
            digest = obj.serialized_type.old_type_hash.hex()
            matches = scripts.get(digest, [])
            if len(matches) == 1:
                script, = matches
                bindings.append(dict(component=obj.path_id, properties_hash=digest, **script))
                specs[(script["namespace"], script["name"])] = fields
            else:
                bindings.append(dict(component=obj.path_id, properties_hash=digest, matches=len(matches)))
        source.update(container=path, root_id=root.path_id, scripts=bindings,
                      player_scripts_sha256=SCRIPT_ASSET_SHA256)
        controller = specs.get(("Torappu.Battle", "CastSkillWithSuspendable"))
        ability = specs.get(("Torappu.Battle.Abilities", "ActiveBuffAbility"))
        if len(bindings) != 2 or not controller or not ability:
            result = dict(supported=False, reason="unreviewed_skill_components")
        elif prefab == "skchr_lin_3":
            mode, = ability["_buffs"]
            extra, = controller["_extraAbilities"]
            child = root.assets_file.objects[extra["m_PathID"]]
            child_script = scripts.get(child.serialized_type.old_type_hash.hex(), [])
            child_fields = child.read_typetree()
            empty, = child_fields["_buffs"]
            supported = (mode["buffKey"] == "lin_s_3[switch_mode]" and mode["templateKey"] == "switch_mode_restart_fsm"
                and mode["lifeTimeType"] == 1 and mode["durationKey"] == "duration"
                and mode["blackboard"][0]["key"] == "mode" and mode["blackboard"][0]["value"] == 3
                and not extra["m_FileID"] and len(child_script) == 1 and child_script[0]["name"] == "ActiveBuffAbility"
                and empty["templateKey"] == "empty" and empty["lifeTimeType"] == 0
                and controller["_beginAnim"] == "Skill_3_Begin" and controller["_playSkillBeginAnim"]
                and controller["_switchToState"] and controller["_suspendableLimitedTimes"] == -1
                and not any(controller[k] for k in ("_checkHasTargetBeforeDoCast", "_allowSpRecoveryWhenAffecting",
                    "_useTriggerInManualMode", "_canCastWithNoSp", "_buffsWhenDiscard", "_stopAffectOnInterrupt",
                    "_resetAbilityCooldownWhenCastEnd", "_checkFinishedBuffWhenStopAffect"))
                and not any(controller[k]["m_PathID"] for k in ("_trigger", "_beginAnimValidator", "_availableValidator"))
                and not ability["_selector"]["m_PathID"] and ability["_resetCdStrategy"] == 1
                and not any(ability[k] for k in ("_waitForCasting", "_preDelay", "_escapeTime", "_passiveBuffs",
                    "_updateLifetimeWhenSpellStart", "_enableUpdateCooldown", "_checkBuffEarlyFinish")))
            result = dict(supported=bool(supported), timed_cast=False, manual_stop_limit=-1,
                          recover_sp_when_affecting=False, begin_action=controller["_beginAnim"],
                          timing="skill_begin_then_ability_cooldown", mode_buff=dict(template=mode["templateKey"],
                          blackboard={"mode": 3}, reset_ability_cooldown_when_cast_end=False))
            if not supported:
                result["reason"] = "unreviewed_lin_suspendable_skill_options"
            source["extra_ability"] = dict(component=child.path_id, script=child_script)
        else:
            # Other buff payloads can refund SP or alter the stop condition.
            # The two ordinary S3 buffs are verified in this release.
            payload = [(b["buffKey"], b["templateKey"]) for b in ability["_buffs"]]
            result = dict(supported=bool(payload == [("ebnhlz_s_3", "empty"),
                ("ebnhlz_s_3[mode]", "switch_mode_restart_fsm")]
                and controller["_allowNoTarget"] and controller["_canCastDuringBorn"]
                and controller["_maxTriggerTime"] == -1 and controller["_suspendableLimitedTimes"] == -1
                and not any(controller[k] for k in ("_allowSpRecoveryWhenAffecting", "_canCastWithNoSp",
                    "_switchToState", "_checkHasTargetBeforeDoCast", "_playSkillBeginAnim", "_extraAbilities",
                    "_buffsWhenDiscard", "_checkFinishedBuffWhenStopAffect", "_skipSpCheckInSkillState",
                    "_recoverSpIfNoTarget", "_useTriggerInManualMode", "_onlyAvailableWhenTokenValid"))
                and not controller["_availableValidator"]["m_PathID"]
                and not any(ability[k] for k in ("_preDelay", "_escapeTime", "_waitForAttackEvent",
                    "_checkBuffEarlyFinish", "_enableUpdateCooldown", "_passiveBuffs")) and ability["_waitForCasting"]),
                recover_sp_when_affecting=bool(controller["_allowSpRecoveryWhenAffecting"]),
                manual_stop_limit=controller["_suspendableLimitedTimes"])
            if not result["supported"]:
                result["reason"] = "unreviewed_suspendable_skill_options"
            else:
                result["timed_cast"] = bool(
                    ability["_resetCdStrategy"] == 1
                    and not ability["_updateLifetimeWhenSpellStart"]
                    and not controller["_resetAbilityCooldownWhenCastEnd"]
                    and all(b["lifeTimeType"] == 1 and not any(b[k] for k in
                        ("durationKey", "remainingTimeKey", "lifeTime", "loadFromDB"))
                        for b in ability["_buffs"]))
                mode_buff = ability["_buffs"][1]
                result["mode_buff"] = dict(template=mode_buff["templateKey"],
                    blackboard={v["key"]: v["value"] for v in mode_buff["blackboard"]},
                    reset_ability_cooldown_when_cast_end=bool(controller["_resetAbilityCooldownWhenCastEnd"]))
                result["attribute_modifiers"] = ability["_buffs"][0]["attributes"]["attributeModifiers"]
        tables._prefab_data[key] = result, source
    tables.source.setdefault("manual_skill_prefabs", {})[prefab] = source
    return result


def lin_begin(tables, card):
    key = card["operator_id"]
    skin = card.get("battle_skin", "defaultskin")
    cache = ("lin_begin", skin)
    if cache not in tables._prefab_data:
        member = ("chararts" if skin == "defaultskin" else "skinpack") + f"/{key}.ab"
        result = read_skin_actions(tables, member, skin, ("Skill_3_Begin", "Skill_3_Attack_A"))
        actions = result["views"]["front"]["actions"]
        begin = actions["Skill_3_Begin"]
        if (result["views"]["back"]["actions"] != actions or begin["speed"] != 1
                or begin["loop"] or begin["events"] or begin["duration"] <= 0):
            raise ValueError("林的技能起手动作随朝向、外观或播放速率改变")
        source = dict(result["source"], skeletons={v: d["skeleton_sha256"] for v, d in result["views"].items()})
        tables._prefab_data[cache] = (dict(battle_skin=skin, actions=actions), source)
    result, source = tables._prefab_data[cache]
    tables.source["lin_skill_animation"] = source
    return result


def lin_hit_talent(tables, card):
    """Read the owner's ON_TAKE_DAMAGE branch and reachable selected talent boards."""
    key = card["operator_id"]
    if card["elite"] < 2:
        return dict(status="locked")
    unit = tables.read("excel/character_table.json")[key]
    rows = next(talent["candidates"] for talent in unit["talents"]
                if talent["candidates"][0]["prefabKey"] == "2")
    if card["module"] == "unknown" or (card["module"] is not None and card["module_level"] is None):
        return dict(status="unresolved", reason="unknown_hit_recovery_module")
    rows = talent_rows(equipped_phase(tables, card), 1, "2", rows)
    binding = tables._component_fields("characters", key, ("_buffs",), "lin_hit_talent_prefab",
                                       children=("Others", "Direction", "Talents", "2"))
    (buff,) = binding["_buffs"]
    cache = "lin_hit_talent_template"
    if cache not in tables._prefab_data:
        raw, source = tables.verified_bundle("config/buff_template_holder.ab")
        holder, = [o for o in bundle_environment(raw).objects if o.type.name == "MonoBehaviour"]
        templates = {t["templateKey"]: t for t in holder.read_typetree()["_templates"]}
        event, = templates[buff["templateKey"]]["eventToActions"]["_items"]
        nodes = json.loads(event["value"]["SerializedState"])
        if (buff["templateKey"] != "lin_t_2" or event["key"] != 10 or
                [n["$type"].split("+")[-1] for n in nodes] != ["IsIgnoreForSp", "IfNot", "Dice", "ModifySp"]
                or nodes[2]["_probKey"] != "prob" or nodes[3]["_spString"] != "sp"
                or nodes[3]["_targetType"] != "BUFF_OWNER" or nodes[3]["_spMask"] != "ALL"
                or any(nodes[3][k] for k in ("_forceFlag", "_modifyByRatio", "_modifyBySpCost", "_isMinis"))):
            raise ValueError("林的受击抽取或技力修改配置不符")
        tables._prefab_data[cache] = source
    tables.source[cache] = tables._prefab_data[cache]
    boards = set()
    for potential in ((card["potential"],) if card["potential"] is not None else range(1, 7)):
        eligible = [r for r in rows if r["requiredPotentialRank"] < potential and
                    _unlocked(r["unlockCondition"], card["elite"], card["level"] or 1)]
        if eligible:
            board = {b["key"]: b["value"] for b in eligible[-1]["blackboard"]}
            boards.add((board["prob"], int(board["sp"])))
    return dict(status="conditional_hit_recovery", event="ON_TAKE_DAMAGE", event_key=10,
                rolls=[dict(probability=p, sp=n) for p, n in sorted(boards)],
                ignore_for_sp_skips_roll=True, shield_break_alone_triggers=False, forced_sp=False)


def lin_attack_modes(tables):
    key = "char_4080_lin"
    disarmed = tables._component_fields("characters", key, ("_buffs",), "lin_default_disarmed",
                                        children=("Modes", "Default", "Body", "GeneralAbilities", "Disarmed"))
    buff, = disarmed["_buffs"]
    disarm_ref = dict(m_FileID=0, m_PathID=tables.source["lin_default_disarmed"][key]["component_path_id"])
    modes = {}
    for mode in ("Default", "S3"):
        root = ("Modes", mode)
        fields = tables._component_fields("characters", key, ("_generalAbilities", "_attack"),
                                          "lin_mode_" + mode, children=root)
        attack = tables._component_fields("characters", key, ("_animKey",), "lin_attack_" + mode,
                                           children=(*root, "Body", "Attack"))
        attack_ref = dict(m_FileID=0, m_PathID=tables.source["lin_attack_" + mode][key]["component_path_id"])
        attached = disarm_ref in fields["_generalAbilities"]
        if (buff["buffKey"] != "lin_c_disarmed" or buff["attributes"]["abnormalFlags"] != [18]
                or attached != (mode == "Default") or attack_ref != fields["_attack"]):
            raise ValueError("林普通禁攻被动、技能模式或攻击组件的绑定不符")
        modes[mode] = dict(disarmed=attached, attack_action=attack["_animKey"])
    return modes

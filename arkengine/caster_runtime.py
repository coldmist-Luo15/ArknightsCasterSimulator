"""Caster loadouts and the first ordinary single-target spell on the V079 opening clock."""
from collections import defaultdict

from .animation_assets import read_skin_actions
from .baked_mount import single
from .caster_modules import core_penetration_module, splash_range_module
from .fixed import ONE
from .mystic_cycle import baked_key_sample
from .opening_scene import bundle_environment
from .skill_assets import SCRIPT_ASSET_SHA256, _script_classes
from .skill_recovery import READY_EPSILON


def caster_options(tables, key, unit):
    skills = tables.read("excel/skill_table.json")
    equipment = tables.read("excel/uniequip_table.json")
    upgrades = tables.read("excel/battle_equip_table.json")
    skill_choices = []
    for binding in unit["skills"]:
        sid = binding["skillId"]
        levels = skills[sid]["levels"]
        maximum = 1 + len(unit["allSkillLvlup"]) + len(binding["levelUpCostCond"] or [])
        if maximum > len(levels):
            raise ValueError(f"{key}/{sid} 的可训练等级超过技能配置")
        maximum_row = levels[maximum - 1]
        skill_choices.append(dict(id=sid, name=levels[0]["name"], max_level=maximum,
            unlock=binding["unlockCond"], prefab=maximum_row["prefabId"], type=maximum_row["skillType"],
            duration=maximum_row["duration"], sp=maximum_row["spData"]))
    module_choices = []
    for mid in equipment["charEquip"].get(key, ()):
        item = equipment["equipDict"][mid]
        if item["type"] != "ADVANCED":
            continue
        module_choices.append(dict(id=mid, name=item["uniEquipName"], special=item["isSpecialEquip"],
                                   levels=[p["equipLevel"] for p in upgrades[mid]["phases"]]))
    return dict(branch=unit["subProfessionId"], skills=skill_choices, modules=module_choices,
                default_attack=default_attack(tables, key), scope="configuration_and_prefab_bindings")


def default_attack(tables, key, mode_index=0):
    root = tables._component_fields("characters", key, ("_modes","_commonAbilities","_traitAbility"), "caster_prefabs")
    tables.source["caster_player_scripts_sha256"] = SCRIPT_ASSET_SHA256
    cache = ("caster_default_attack", key, mode_index)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    source = tables.source["caster_prefabs"][key]
    raw, _ = tables.verified_bundle(source["member"])
    objects = bundle_environment(raw).container[source["container"]].deref().assets_file.objects
    classes = _script_classes(tables)

    def resolve(ref):
        if ref["m_FileID"]:
            raise ValueError(f"{key} 普通模式指向外部组件")
        return objects.get(ref["m_PathID"])

    def script(obj):
        matches = classes.get(obj.serialized_type.old_type_hash.hex(), ())
        return matches[0]["name"] if len(matches) == 1 and matches[0]["assembly"] == "Assembly-CSharp.dll" else None

    mode = resolve(root["_modes"][mode_index])
    fields = mode.read_typetree()
    attack = resolve(fields["_attack"])
    trigger = resolve(fields["_attackTrigger"])
    profile = dict(mode_component=mode.path_id, mode_index=mode_index, combat_override=fields["_combat"]["m_PathID"],
                   common_abilities=root["_commonAbilities"], trait=root["_traitAbility"]["m_PathID"],
                   general_abilities=fields["_generalAbilities"],
                   attack_component=attack.path_id if attack else None, ability=script(attack) if attack else None,
                   trigger=script(trigger) if trigger else None)
    if attack is not None and trigger is not None:
        fields = attack.read_typetree()
        keys = ("_projectileKey", "_mountPointType", "_damageType", "_animKey", "_timeMode", "_maxAnimScale",
                "_waitForAttackEvent", "_resetCdStrategy", "_allowNoTarget", "_selectTargetTiming",
                "_useMountGroup", "_upAnimKey", "_downAnimKey", "_projectileDestination", "_activeBuffs",
                "_atkScale", "_attackType", "_extraDamageType", "_extraProjectileKeys",
                "_elementDamageType", "_useDynamicAttackType", "_useCachedAtkOnly", "_transferSource",
                "_waitForProjectileInvalid", "_escapeTime", "_selectTargetSource", "_preDelay")
        profile.update(enabled=bool(fields["m_Enabled"]), fields={k: fields[k] for k in keys if k in fields})
        trig = trigger.read_typetree()
        go = resolve(trig["m_GameObject"]).read_typetree()
        parts = {}
        for row in go["m_Component"]:
            obj = resolve(row["component"])
            if obj.type.name == "MonoBehaviour":
                cfg = obj.read_typetree()
                if cfg["m_Enabled"]:
                    parts[script(obj)] = cfg
        selector = parts.get("AdvancedSelector", {})
        grid = parts.get("AutoLoadBoxRange", {})
        tables._prefab_data["caster_hatred_priority",key,mode_index] = all(
            selector.get(k) == v for k,v in dict(_postFilter=4,_sortByTauntAtLast=0,
                _abilityInputTargetAsSortSource=0,_ignoreHitRange=0,_ignoreTargetFree=0).items())
        profile.update(selector={k: selector[k] for k in (
            "_targetSide", "_targetMotion", "_targetCategory", "_maxNum", "_limitTargetNum", "_postFilter",
            "_needProfessionMask", "_checkUnitType", "_targetOptionHookedKeys") if k in selector},
            trigger_parameters={k: trig.get(k) for k in ("_minTargetNum","_overrideSearchTargetTick","_keepTarget")},
            base_range=bool(grid and not grid["_useBlackboardRangeId"] and not grid["_extendable"]),
            trigger_components=sorted(name or "unidentified" for name in parts))
    tables._prefab_data[cache] = profile
    return profile


def direct_area_shape(tables, key, profile, *, line):
    if profile.get("ability") != "MeleeAttack":
        return None
    source = tables.source["caster_prefabs"][key]
    raw, _ = tables.verified_bundle(source["member"])
    objects = bundle_environment(raw).container[source["container"]].deref().assets_file.objects
    ability = objects[profile["attack_component"]].read_typetree()
    if (bool(ability["_alwaysIncludeTarget"]) == line or ability["_selector"]["m_FileID"]
            or ability["_interuptIfTargetDead"] or (line and ability["_faceToTarget"])):
        return None
    selector = objects[ability["_selector"]["m_PathID"]]
    classes = _script_classes(tables)
    names = classes.get(selector.serialized_type.old_type_hash.hex(), ())
    fields = selector.read_typetree()
    checks = dict(_targetSide=2,_targetMotion=3,_targetCategory=1,_limitTargetNum=0,_ignoreHitRange=0,
                  _ignoreTargetFree=0,_needProfessionMask=0,_checkUnitType=0,_targetOptionHookedKeys=[])
    if (len(names) != 1 or names[0]["name"] != "AdvancedSelector"
            or not fields["m_Enabled"] or any(fields.get(k)!=v for k,v in checks.items())):
        return None
    go = objects[fields["m_GameObject"]["m_PathID"]].read_typetree()
    parts = [objects[row["component"]["m_PathID"]] for row in go["m_Component"]]
    behaviours = {classes[o.serialized_type.old_type_hash.hex()][0]["name"]:o.read_typetree() for o in parts
                  if o.type.name == "MonoBehaviour" and o.read_typetree()["m_Enabled"]}
    if line:
        grid = behaviours.get("AutoLoadBoxRange")
        if (grid and not grid["_extendable"] and not grid["_useBlackboardRangeId"]
                and set(behaviours) <= {"AutoLoadBoxRange","AdvancedSelector","SelectorTrigger"}):
            return "ordinary_range"
        return None
    circles = [o.read_typetree() for o in parts if o.type.name == "CircleCollider2D"]
    if set(behaviours) not in ({"PhysicsRange","AdvancedSelector"},{"CircleRange","AdvancedSelector"}) or len(circles) != 1:
        return None
    shape, = circles
    native_range = behaviours.get("PhysicsRange") or behaviours["CircleRange"]
    if (native_range["_useBlackboardRangeId"] or native_range["_extendable"] or not shape["m_Enabled"]
            or not shape["m_IsTrigger"] or any(shape["m_Offset"].values()) or shape["m_Radius"] != single(1.1)):
        return None
    return shape["m_Radius"]


def phalanx_default(tables, card, profile, placement, horizon, buttons, *, auto_opening=None):
    key = card["operator_id"]
    source = tables.source["caster_prefabs"][key]
    raw, _ = tables.verified_bundle(source["member"])
    objects = bundle_environment(raw).container[source["container"]].deref().assets_file.objects
    classes = _script_classes(tables)
    disarm = []
    for ref in profile["general_abilities"]:
        obj = objects[ref["m_PathID"]]
        scripts = classes.get(obj.serialized_type.old_type_hash.hex(), ())
        if len(scripts) != 1 or scripts[0]["name"] != "PassiveBuffAbility":
            continue
        ability = obj.read_typetree()
        for buff in ability["_buffs"] if ability["m_Enabled"] else ():
            flags = buff["attributes"]["abnormalFlags"]
            if buff["templateKey"] == "empty" and flags in ([11],[18]):
                disarm.append(dict(key=buff["buffKey"],flag="DISARMED" if flags==[11] else "FORCE_DISARMED"))
    if len(disarm)!=1 or not profile["trait"]:
        return dict(status="unresolved",reason="phalanx_default_disarm_binding_unreviewed")
    trait, = objects[profile["trait"]].read_typetree()["_buffs"]
    modifiers = {(m["attributeType"],m["formulaItem"],m["loadFromBlackboard"],m["fetchBaseValueFromSourceEntity"])
                 for m in trait["attributes"]["attributeModifiers"]}
    if modifiers != {(2,1,1,0),(3,0,1,0)}:
        return dict(status="unresolved",reason="phalanx_defense_trait_unreviewed")
    if card["skill_id"]:
        skill = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][0]
        if skill["skillType"] != "MANUAL" and not (skill["skillType"]=="AUTO" and auto_opening is not None and
                (horizon<auto_opening.through_frame or horizon==auto_opening.through_frame and auto_opening.cast is None)):
            return dict(status="unresolved",reason="automatic_skill_changes_phalanx_mode")
    opening = next((b for b in buttons if b["outcome"] in ("begin_animation","activated","unresolved")),None)
    through = min(horizon,opening["frame"]-1) if opening else horizon
    traits = tables.read("excel/character_table.json")[key]["trait"]["candidates"]
    base, = traits
    board = {b["key"]:b["value"] for b in base["blackboard"]}
    defense = None
    if card["module"] is None:
        defense = board
    elif len(card["module_ids"]) == 1 and card["module_level"]:
        equip = tables.read("excel/battle_equip_table.json")[card["module_ids"][0]]
        phase, = (p for p in equip["phases"] if p["equipLevel"] == card["module_level"])
        trait_parts = [p for p in phase["parts"] if p["target"] == "TRAIT"]
        if not trait_parts:
            defense = board
        elif len(trait_parts)==1:
            overrides = trait_parts[0]["overrideTraitDataBundle"]["candidates"]
            if len(overrides)==1:
                defense = dict(board,**{b["key"]:b["value"] for b in overrides[0]["blackboard"] if b["key"] in board})
    return dict(status="phalanx_default_disarmed",through_frame=through,from_deployment=placement["frame"],
                ordinary_attack_count=0,disarm=disarm[0],next_skill_input=opening["frame"] if opening else None,
                ordinary_trait_modifiers=None if defense is None else dict(defense_ratio_added=defense["def"],
                    resistance_added=defense["magic_resistance"]),
                scope="ordinary_attacks_while_in_default_mode",
                conditions=["separate_talent_damage_and_summons_not_in_ordinary_attack_count"])


def first_caster_attack(tables, card, placement, events, unknown, horizon, skill_buttons, *, trace=False, priority=None):
    profile = card.get("caster_config", {}).get("default_attack", {})
    ability = profile.get("ability")

    def pending(reason):
        return dict(status="unresolved", reason=reason, ability=ability)

    if card.get("caster_config",{}).get("branch") == "phalanx":
        return phalanx_default(tables,card,profile,placement,horizon,skill_buttons)
    branch = card["caster_config"]["branch"]
    line = ability == "MeleeAttack" and branch == "blastcaster"
    direct = ability == "MeleeAttack" and branch in ("splashcaster","blastcaster")
    if not direct and ability != "RangedAttack":
        return pending("unreviewed_ordinary_ability")
    if card["operator_id"] == "char_210_stward" and card["level"] is not None:
        unit = tables.read("excel/character_table.json")[card["operator_id"]]
        talents = current_talents(unit,card)
        if talents:
            setting = tables._component_fields("characters",card["operator_id"],("_defaultModeIndex",),
                "caster_talent_mode",children=("Talents",talents[0]["prefabKey"]))
            if setting["_defaultModeIndex"] >= 0:
                profile = default_attack(tables, card["operator_id"], setting["_defaultModeIndex"])
    fields = profile["fields"]
    trigger = profile["trigger_parameters"]
    selector = profile["selector"]
    if (not profile["enabled"] or profile["combat_override"] not in (0,profile["attack_component"])
            or profile["trigger"] != "SelectorTrigger" or not profile["base_range"]
            or profile["trigger_components"] != ["AdvancedSelector","AutoLoadBoxRange","SelectorTrigger"]
            or trigger != dict(_minTargetNum=1, _overrideSearchTargetTick=-1, _keepTarget=0)
            or any(selector.get(k) != v for k,v in dict(_targetSide=2, _targetMotion=3, _targetCategory=1,
                    _maxNum=1, _needProfessionMask=0, _checkUnitType=0,
                    _targetOptionHookedKeys=[]).items())):
        return pending("outside_standard_single_target_selector")
    if selector["_limitTargetNum"] != 1 and not (line and selector["_limitTargetNum"] == 0):
        return pending("outside_standard_cast_trigger_target_count")
    common = dict(_timeMode=0,_waitForAttackEvent=1,_resetCdStrategy=1,_animKey="Attack",
                  _damageType=2,_activeBuffs=[],_upAnimKey="",_downAnimKey="",_preDelay=0)
    if direct:
        shape = direct_area_shape(tables,card["operator_id"],profile,line=line)
        common.update(_allowNoTarget=1,_selectTargetTiming=1,_selectTargetSource=1 if line else 3,_attackType=1,
                      _extraDamageType=0,_elementDamageType=0,_useDynamicAttackType=0,_downAnimKey="Attack_Down" if line else "")
        if not shape or any(fields.get(k) != v for k,v in common.items()):
            return pending("outside_direct_splash_configuration")
    elif (any(fields.get(k) != v for k,v in dict(common,_allowNoTarget=0,_selectTargetTiming=0,
            _useMountGroup=0,_projectileDestination=0,_selectTargetSource=2).items()) or not fields["_projectileKey"]):
        return pending("outside_standard_arts_projectile_cast")
    if card["module"] is not None:
        module = splash_range_module(tables,card)
        support = module is not None and module["ordinary_attack_bonus"] is not None
        if not support and not (card["operator_id"]=="char_180_amgoat" and core_penetration_module(tables,card)):
            return pending("caster_module_attack_effects_unreviewed")
    first_charge = None
    if card["skill_id"]:
        level = card.get("skill_level")
        if not level:
            return pending("caster_skill_level_unknown")
        skill = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][level-1]
        data = skill["spData"]
        if skill["skillType"] != "MANUAL" and not (data["spType"] == "INCREASE_WHEN_ATTACK" and data["initSp"] < data["spCost"]):
            unit = tables.read("excel/character_table.json")[card["operator_id"]]
            rates = {r["data"]["spRecoveryPerSec"] for r in unit["phases"][card["elite"]]["attributesKeyFrames"]}
            if not (line and card["skill_id"] in ("skchr_akkord_1","skchr_aroma_1") and card["elite"] == 0
                    and rates == {1} and data["spType"] == "INCREASE_WITH_TIME" and data["increment"] == 1
                    and data["spCost"] > data["initSp"] and not current_talents(unit,card)):
                return pending("caster_automatic_skill_may_replace_first_attack")
            step = int(tables.fixed_timestep()*ONE)
            first_charge = placement["frame"] + ((data["spCost"]-data["initSp"])*ONE-READY_EPSILON)//step+1
            horizon = min(horizon, first_charge-1)
    for button in skill_buttons:
        if button["outcome"] in ("begin_animation","activated","unresolved"):
            horizon = min(horizon,button["frame"]-1)
    key = card["operator_id"]
    unit = tables.read("excel/character_table.json")[key]
    if card["potential"] is None or card["level"] is None:
        return pending("caster_attack_stats_unspecified")
    base = {(k["data"]["baseAttackTime"], k["data"]["attackSpeed"]) for k in
            unit["phases"][card["elite"]]["attributesKeyFrames"]}
    if len(base) != 1 or any(k["data"]["attackSpeed"] or k["data"]["baseAttackTime"] for k in unit["favorKeyFrames"]):
        return pending("caster_attack_speed_depends_on_progression")
    interval, speed = base.pop()
    for rank in unit["potentialRanks"][:card["potential"]-1]:
        for m in (rank["buff"] or {}).get("attributes", {}).get("attributeModifiers",[]):
            if m["attributeType"] in ("ATTACK_SPEED","BASE_ATTACK_TIME"):
                if m["attributeType"] != "ATTACK_SPEED" or m["formulaItem"] != "ADDITION" or m["loadFromBlackboard"] or m["fetchBaseValueFromSourceEntity"]:
                    return pending("caster_potential_timing_modifier_unreviewed")
                speed += m["value"]
    def eligible(candidate):
        cond = candidate["unlockCondition"]
        return ((card["elite"],card["level"]) >= (int(cond["phase"][-1]),cond["level"])
                and candidate["requiredPotentialRank"] < card["potential"])
    if any("attack_speed" in b["key"] or "attack_time" in b["key"] for slot in unit["talents"] or []
           for candidate in slot["candidates"] if eligible(candidate) for b in candidate["blackboard"]):
        return pending("caster_talent_timing_modifier_unreviewed")
    skin = card.get("battle_skin","defaultskin")
    member = ("chararts" if skin == "defaultskin" else "skinpack") + f"/{key}.ab"
    birth = tables.character_birth(key)
    if birth["_useRealBornTimeFromAnim"] or birth["m_overrideBornTime"] != -1:
        return pending("caster_birth_override")
    try:
        mounts = [(2, "muzzlaTransform")] if trace and fields.get("_mountPointType") == 2 else []
        down = ("Attack_Down",) if line else ()
        decoded = read_skin_actions(tables, member, skin, ("Born","Attack",*down), mounts=mounts,front_only=down)
    except ValueError as error:
        if str(error) != "外观动作映射缺失或重复":
            raise
        return pending("caster_skin_action_binding_missing")
    tables.source.setdefault("caster_skin_actions",{})[key] = decoded["source"]
    bake = tables.read(f"bakemuzzledata/character/{key}_{skin}.json")["bakedAnimDatas"]
    actions = []
    for view, item in decoded["views"].items():
        born = item["actions"]["Born"]
        # In this default birth mode, PlayBornAnimationAndEffect passes the clip's
        # duration as its playback speed. SpineAnimator returns duration/speed.
        if born["loop"] or born["duration"] <= 0 or born["speed"] != 1:
            return pending("caster_birth_or_attack_track_unreviewed")
        for name,action in item["actions"].items():
            if name == "Born":
                continue
            track = bake[view.capitalize()]["bakedEvents"].get(action["clip"])
            if action["loop"] or action["speed"]!=1 or not track or track["events"]!=["OnAttack"] or len(track["eventTime"])!=1:
                return pending("caster_birth_or_attack_track_unreviewed")
            actions.append((action["clip"],action["duration"],track["eventTime"][0]))
    if len({row[1:] if line else row for row in actions}) != 1:
        return pending("caster_attack_timing_depends_on_facing")
    clip, duration, key_time = actions[0]
    # UpdatePlaybackSpeed clamps duration/clip to [0.1, maxAnimScale] before taking the reciprocal.
    scale = max(single(0.1), single(single(single(interval) * 100 / speed) / single(duration)))
    if fields["_maxAnimScale"] >= 0:
        scale = min(scale,fields["_maxAnimScale"])
    dt = tables.fixed_timestep()
    key_steps, anim_time = baked_key_sample(key_time,single(1/scale),dt)
    step = int(dt*ONE)
    idle = placement["frame"] + (ONE+step-1)//step + 1
    resolve_priority = tables._prefab_data["caster_hatred_priority",key,profile["mode_index"]]
    impact = "direct_line_impact" if line else "direct_splash_impact"
    scenarios = _first_searches(key, events, unknown, horizon, idle, key_steps, fields.get("_projectileKey"),
                                priority=priority if resolve_priority else None,direct_impact=impact)
    result = dict(status=("conditional_first_direct_line" if line else "conditional_first_direct_splash") if direct else "conditional_first_arts_projectile",
                ability=ability, first_idle_frame=idle, search_period_frames=3,
                animation_seconds_at_key=anim_time, ticks_to_key=key_steps, scenarios=scenarios,
                conditions=["default_mode_no_external_speed_range_targetability_or_animation_modifiers",
                            "eligible_receiver_survives_to_release_and_is_selectable","no_replacement_skill_or_passive_attack_proc"])
    if direct:
        area = {"shape":"ordinary_range"} if line else {"radius":shape}
        result["area_profile" if line else "splash_profile"] = dict(**area,hatred_priority=resolve_priority,mode_index=profile["mode_index"],horizon=horizon,step_seconds=dt,
            base_interval_seconds=single(single(interval)*100/speed),nominal_cast_seconds=single(single(duration)*scale))
    if line:
        result["conditions"][1] = "one_selectable_enemy_suffices_to_start_cast"
    if first_charge is not None:
        result["first_automatic_charge_frame"] = first_charge
        result["conditions"].append("natural_skill_recovery_without_external_sp")
    if trace and mounts:
        graphic = tables._component_fields("characters",key,("m_LocalPosition","m_LocalRotation","m_LocalScale"),
            "character_graphic",component_type="Transform",children=("Graphic",))
        result["flight_profile"] = dict(bake_file=f"bakemuzzledata/character/{key}_{skin}.json", step_seconds=dt,
            mount_parents={face:view["mount_parents"][2] for face,view in decoded["views"].items()},
            graphic_parent=dict(position=graphic["m_LocalPosition"],rotation=graphic["m_LocalRotation"],
                                scale=graphic["m_LocalScale"]), clip=clip, mount=2,
            mode_index=profile["mode_index"], base_interval_seconds=single(single(interval)*100/speed),
            nominal_cast_seconds=single(single(duration)*scale), horizon=horizon)
    return result


def current_talents(unit, card):
    result = []
    for slot in unit["talents"] or ():
        chosen = [r for r in slot["candidates"] if card["potential"] is not None and card["level"] is not None
                  and r["requiredPotentialRank"] < card["potential"] and (card["elite"],card["level"]) >=
                  (int(r["unlockCondition"]["phase"][-1]),r["unlockCondition"]["level"])]
        if chosen:
            result.append(chosen[-1])
    return result


def _first_searches(key, events, unknown, horizon, idle, key_steps, projectile, dead_by=None, priority=None,
                    direct_impact="direct_splash_impact"):
    # Ordinary SelectorTrigger checks immediately on the first IDLE update, then every third frame.
    contact_events = defaultdict(list)
    for row in events:
        kind = row["kind"]
        if kind == "route_end_in_range" or (kind in ("attack_range_enter","attack_range_exit") and row.get("operator_id") == key):
            contact_events[row["frame"]].append((row["instance_id"],kind == "attack_range_enter"))
    scenarios = []
    for lag in (0,1):
        inside = set()
        sampled = -1
        scenario = dict(target_motion_lag=lag, stop="horizon")
        for frame in range(idle,horizon+1,3):
            while sampled < frame-lag:
                sampled += 1
                for instance, entered in contact_events[sampled]:
                    if entered:
                        inside.add(instance)
                    else:
                        inside.discard(instance)
            if dead_by:
                inside.difference_update(k for k, death in dead_by.items() if death < frame)
            if any(f <= sampled for k,f in unknown.items() if not dead_by or k not in dead_by):
                scenario.update(stop="incomplete_positions_or_target_priority",frame=frame)
                break
            if not inside:
                continue
            release = frame+key_steps
            selected = priority(inside,frame) if len(inside)>1 and priority else None
            if projectile is not None and len(inside)>1 and not selected:
                scenario.update(stop="incomplete_positions_or_target_priority",frame=frame)
                break
            for target in [selected["target"]] if selected else sorted(inside):
                branch = dict(scenario,start_frame=frame,target=target,stop="before_release")
                if selected:
                    branch["priority"] = selected
                elif len(inside)>1:
                    branch["condition"] = "one_of_known_candidates_wins_priority"
                if release <= horizon:
                    branch["baked_event_frame"] = release-1
                    if projectile is None:
                        branch.update(stop=direct_impact,impact_frame=release)
                    else:
                        branch.update(stop="first_projectile_released",launch_frame=release,projectile=projectile)
                scenarios.append(branch)
            scenario = None
            break
        if scenario is not None:
            scenarios.append(scenario)
    return scenarios

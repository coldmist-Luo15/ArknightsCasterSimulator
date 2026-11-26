"""Lin's ordinary barrier and the interval in which her deployment cannot damage enemies."""
import json
from math import ceil

from .attack_range import range_cells
from .attributes import base_integer_at_level
from .damage import HP_EPSILON, MINIMUM_RATIO
from .enemy_aura import DRONE
from .fixed import ONE, SIGN64, mul
from .lin_equipment import lin_defense_trait
from .module_effects import equipped_phase, module_stat, selected_branch, talent_rows
from .opening_cards import _unlocked
from .opening_scene import bundle_environment
from .skill_assets import lin_attack_modes
from .volley_damage import _raw_f32


LIN = "char_4080_lin"
EBENHOLZ = "char_4046_ebnhlz"
MELEE_ONLY = ("enemy_1353_esabr_2", "enemy_1354_eghost_2")
AXE = "enemy_1357_erob_2"


def barrier_hit(damage_raw, threshold_raw, *, active=True, ignore_block=False):
    """One HP damage modifier at ON_TAKE_DAMAGE, after defense, with no other interception."""
    if any(type(v) is not int or not 0 <= v < SIGN64 for v in (damage_raw, threshold_raw)):
        raise ValueError("结算后的伤害与屏障阈值需为非负有符号 Q32.32")
    if type(active) is not bool or type(ignore_block) is not bool:
        raise ValueError("屏障状态和穿透标志须为布尔值")
    if not active or ignore_block:
        return dict(absorbed_q32=0, modifier_q32=damage_raw, cancelled=False, breaks=False, hp_loss_q32=damage_raw)
    absorbed = min(threshold_raw, damage_raw)
    residual = damage_raw - absorbed
    # Cancel uses LE(remainder, 0). The separate break predicate uses LT(dynamic, 0).
    cancelled = residual < HP_EPSILON
    return dict(absorbed_q32=absorbed, modifier_q32=0 if cancelled else residual, cancelled=cancelled,
                breaks=threshold_raw - damage_raw < -HP_EPSILON, hp_loss_q32=0 if cancelled else residual)


def lin_with_block_buffs(tables, damage, threshold, *, active, ignore_block, attached, damage_type=None):
    """Run active ON_TAKE_DAMAGE handlers in descending priority, stable by attachment."""
    templates=tables.read("battle/buff_template_data.json")
    barriers={"lin_t_1","lin_t_1_s_3"}
    callbacks=[]
    for entry in attached:
        key=entry["template"]
        if key not in barriers|{"damage_block[all]","damage_resistance"}:
            raise ValueError("此受击器支持计出万全、无条件格挡与物理法术减伤")
        base=templates[key]["onEventPriority"]
        if base!=("HIGH_PRIORITY" if key=="lin_t_1" else "DEFAULT"):
            raise ValueError("格挡模板的事件优先级发生变化")
        priority=entry.get("on_event_priority",1000 if key=="lin_t_1" else 0)
        if type(priority) is not int:
            raise ValueError("实际覆盖的事件优先级须为整数")
        callbacks.append((priority,entry))
    if sum(entry["template"] in barriers for _,entry in callbacks)!=1:
        raise ValueError("按挂载时间排序的回调中须包含一次计出万全")
    full,=templates["damage_block[all]"]["eventToActions"]["ON_TAKE_DAMAGE"]
    if not full["$type"].split(",")[0].endswith("+BlockDamage") or any(full[k] for k in
            ("_filterDamageType","_filterApplyWay","_useDynamicVar","_useFixedValue","_useSource")):
        raise ValueError("完全格挡模板增加了条件")
    remaining=damage
    cancelled=False
    for _,entry in sorted(callbacks,key=lambda pair:-pair[0]):
        key=entry["template"]
        if key in barriers:
            lin=barrier_hit(remaining,threshold,active=active and not cancelled,ignore_block=ignore_block)
            remaining=lin["hp_loss_q32"]
            cancelled|=lin["cancelled"]
        elif key=="damage_resistance":
            scale,=templates[key]["eventToActions"]["ON_TAKE_DAMAGE"]
            if (not scale["$type"].split(",")[0].endswith("+DamageScale") or
                    not scale["_filterDamageType"] or scale["_damageMask"]!="PHYSICAL_AND_MAGICAL" or
                    not scale["_isOneMinus"] or any(scale[k] for k in ("_isStackable","_filterApplyWay",
                        "_filterModifierKey","_cachedDeltaValueToBBKey","_customKey"))):
                raise ValueError("物理法术减伤模板含新的筛选或叠层选项")
            ratio=entry["resistance"]
            if damage_type not in ("PHYSICAL","MAGICAL","PURE","ELEMENT") or type(ratio) not in (int,float) or not 0<=ratio<=1:
                raise ValueError("减伤回调需要伤害类型和实际减伤比例")
            if not cancelled and damage_type in ("PHYSICAL","MAGICAL"):
                remaining=mul(remaining,ONE-_raw_f32(ratio))
        elif not ignore_block and not cancelled:
            remaining=0
            cancelled=True
    return dict(lin,hp_loss_q32=remaining,modifier_q32=remaining,cancelled=cancelled)


def _templates(tables):
    cache = "lin_ordinary_shield_and_spout"
    if cache not in tables._prefab_data:
        raw, source = tables.verified_bundle("config/buff_template_holder.ab")
        holder, = (o for o in bundle_environment(raw).objects if o.type.name == "MonoBehaviour")
        templates = {r["templateKey"]: {e["key"]: json.loads(e["value"]["SerializedState"])
                      for e in r["eventToActions"]["_items"]} for r in holder.read_typetree()["_templates"]
                     if r["templateKey"] in ("lin_t_1", "trap_eradio_damage_with_energy", "trap_eradio_modify_sp_damaged")}
        nodes = templates["lin_t_1"][10]
        types = [n["$type"].split("+")[-1] for n in nodes]
        block, check = nodes[2:4]
        damage = templates["trap_eradio_damage_with_energy"][0][0]
        credit = templates["trap_eradio_modify_sp_damaged"][10][0]
        if (types != ["CheckContainsBuff", "IfNot", "BlockDamage", "FilterByBlackboardValue",
                "CreateBuffUseAbilitySelector", "CreateBuff", "CreateEffect", "FinishBuffsById"]
                or nodes[0]["_buffKeys"] != ["lin_t_1[disable]"] or not block["_useFixedValue"]
                or not block["_allowNegativeDynamicVar"] or block["_useDynamicVar"] or block["_showDamageNumber"]
                or block["_filterDamageType"] or block["_filterApplyWay"]
                or (check["_blackboardKey"], check["_condType"], check["_valueToCompare"]) != ("dynamic", "LT", 0)
                or damage["$type"].split("+")[-1] != "FixedValueDamage" or damage["_damageType"] != "MAGICAL"
                or damage["_damageKey"] != "value" or damage["_ignoreCancelReasonMask"] != "NONE"
                or any(damage[k] for k in ("_ignoreForSp", "_skipModifierEvent", "_multiplierByKey",
                                           "_devideDamageFromAbilityCnt", "_triggerOnCalculateDamage"))
                or credit["$type"].split("+")[-1] != "ModifySp" or credit["_spString"] != "sp"):
            raise ValueError("普通屏障或喷口模板的伤害、技力、破裂条件不符")
        raw, skill_source = tables.verified_bundle("battle/prefabs/[uc]skills.ab")
        root = bundle_environment(raw).container["dyn/battle/prefabs/[uc]skills/sktok_eradio.prefab"].deref()
        components = [root.assets_file.objects[x["component"]["m_PathID"]].read_typetree()
                      for x in root.read_typetree()["m_Component"]]
        pulse, = (r for r in components if "_triggerDelta" in r)
        caster, = (r for r in components if "_switchToState" in r)
        passive, = caster["_extraAbilities"]
        hit_passive = root.assets_file.objects[passive["m_PathID"]].read_typetree()
        selector = root.assets_file.objects[pulse["_selector"]["m_PathID"]].read_typetree()
        token = tables._component_fields("[uc]tokens", "trap_124_eradio", ("_syncSpViaTiles", "_commonAbilities"),
                                         "heat_vent_token")
        if token["_syncSpViaTiles"] or token["_commonAbilities"]:
            raise ValueError("喷口存在支援技力或额外能力")
        if (pulse["_atkScale"] or pulse["_triggerDelta"] != 1 or selector["_limitTargetNum"]
                or selector["_targetSide"] != 3 or selector["_targetMotion"] != 1 or selector["_targetCategory"] != 1
                or [b["templateKey"] for b in pulse["_activeBuffs"]] != ["empty", "trap_eradio_damage_with_energy"]
                or not caster["_switchToState"] or not caster["_allowNoTarget"] or caster["_playSkillBeginAnim"]
                or caster["_checkHasTargetBeforeDoCast"] or caster["_allowSpRecoveryWhenAffecting"]
                or caster["_availableValidator"]["m_PathID"] or caster["_trigger"]["m_PathID"]
                or pulse["_waitForAttackEvent"] or pulse["_preDelay"] or pulse["_specifiedPredelay"]
                or pulse["_interuptIfTargetDead"] or pulse["_enableUpdateCooldown"] or pulse["_animKey"]
                or not pulse["_allowNoTarget"] or pulse["_selectTargetTiming"] != 1 or pulse["_timeMode"] != 3
                or pulse["_cooldownKey"] != "duration" or pulse["_postDelayChecker"] <= 0
                or hit_passive["_buffs"][0]["templateKey"] != "trap_eradio_modify_sp_damaged"
                or credit["_forceFlag"] or credit["_isMinis"] or credit["_modifyByRatio"] or credit["_spMask"] != "ALL"):
            raise ValueError("废热喷口技能的循环及全目标选择器不符")
        slow, _ = pulse["_activeBuffs"]
        modifier, = slow["attributes"]["attributeModifiers"]
        energy_nodes = templates["trap_eradio_damage_with_energy"][0]
        if (slow["buffKey"] != "eradio_reduce_speed" or slow["durationKey"] != "buff_time"
                or slow["disableOverride"] or slow["overrideType"] != 3 or slow["maxStackCnt"] != 1
                or (modifier["attributeType"], modifier["formulaItem"], modifier["loadFromBlackboard"]) != (6, 1, 1)
                or [n["$type"].split("+")[-1] for n in energy_nodes[1:]] != ["CheckContainsBuff", "CheckContainsBuff",
                        "IfNot", "AddAbilityBlackboard", "TriggerSpecifiedAbility"]
                or [n["_buffKeys"] for n in energy_nodes[1:3]] != [["blacksteel_energy[mark]"], ["act28side_over_energy_buff"]]
                or energy_nodes[4]["_addBlackboardKey"] != "energy" or energy_nodes[4]["_abilityOwnerType"] != "TARGET"
                or energy_nodes[4]["_abilityName"] != "blacksteel_energy" or not energy_nodes[5]["_triggerEnergyBuffAbility"]):
            raise ValueError("废热喷口的减速、能量标记或过载判定不符")
        tables._prefab_data[cache] = dict(buff_source=source, skill_source=skill_source,
                                           event=10, damage_template="trap_eradio_damage_with_energy",
                                           selector_limit=False, pulse_interval=pulse["_triggerDelta"],
                                           post_delay_checker=pulse["_postDelayChecker"])
    tables.source[cache] = tables._prefab_data[cache]


def _ordinary_threshold(tables, card):
    if card.get("elite") != 2 or not card.get("level") or selected_branch(tables, card) not in (None, "plx-x", "plx-y"):
        return None
    if card["module"] is not None and not card.get("module_level"):
        return None
    unit = tables.read("excel/character_table.json")[LIN]
    branch = ("Modes", "Default", "Body", "Talents", "1")
    talent = tables._component_fields("characters", LIN, ("_overwriteTalentKey", "_checkValidBySkillIndex"),
                                      "lin_barrier_talent", children=branch)
    passive = tables._component_fields("characters", LIN, ("_buffs",), "lin_barrier_passive", children=branch)
    if (talent["_overwriteTalentKey"] or talent["_checkValidBySkillIndex"] or len(passive["_buffs"]) != 1
            or passive["_buffs"][0]["templateKey"] != "lin_t_1"):
        return None
    rows = [r for talent in unit["talents"] for r in talent["candidates"]
            if r["prefabKey"] == "1" and _unlocked(r["unlockCondition"], card["elite"], card["level"])]
    phase = equipped_phase(tables, card)
    rows = talent_rows(phase, 0, "1", rows)
    thresholds = set()
    for potential in ((card["potential"],) if card["potential"] is not None else range(1, 7)):
        eligible = [r for r in rows if r["requiredPotentialRank"] < potential]
        if eligible:
            thresholds.update(b["value"] for b in eligible[-1]["blackboard"] if b["key"] == "value")
    if len(thresholds) != 1:
        return None
    value, = thresholds
    return int(value * ONE)


def lin_physical_defense(tables, card):
    """Defense before and during Lin S3, including trust, potential, PLX-X stats and its trait."""
    if (card.get("operator_id") != LIN or card.get("skill_id") != "skchr_lin_3" or not card.get("skill_level")
            or not card.get("potential") or (card["module"] and not card.get("module_level"))):
        return None
    threshold = _ordinary_threshold(tables,card)
    if threshold is None:
        return None
    unit = tables.read("excel/character_table.json")[LIN]
    level = base_integer_at_level(unit["phases"][card["elite"]]["attributesKeyFrames"],card["level"],"def")
    if level is None:
        return None
    potential = 0
    for rank in unit["potentialRanks"][:card["potential"]-1]:
        for value in (rank["buff"] or {}).get("attributes",{}).get("attributeModifiers",()):
            if value["attributeType"] == "DEF":
                if value["formulaItem"]!="ADDITION" or value["fetchBaseValueFromSourceEntity"] or value["loadFromBlackboard"]:
                    return None
                potential += value["value"]
    phase = equipped_phase(tables, card)
    unit_bonus = module_stat(phase, "def")
    default_def, active_def, poll_seconds = lin_defense_trait(tables, unit, phase, card)
    trust = unit["favorKeyFrames"][-1]["data"]["def"]
    base = _raw_f32(level+trust+potential+unit_bonus)
    skill = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][card["skill_level"]-1]
    shield_scale, = (r["value"] for r in skill["blackboard"] if r["key"]=="talent_scale")
    return dict(inputs=dict(level=level, maximum_trust=trust, potential=potential, module=unit_bonus),
                skill_defense_poll_seconds=poll_seconds,
                Default=dict(defense_q32=mul(base,ONE+_raw_f32(default_def)),shield_threshold_q32=threshold),
                S3=dict(defense_q32=mul(base,ONE+_raw_f32(active_def)),
                        shield_threshold_q32=mul(threshold,_raw_f32(shield_scale))))


def lin_physical_hit(defense, attack_q32, *, mode, shield_active=True):
    """One ordinary physical hit after the attacker's scale, before Lin's HP interceptor."""
    if mode not in ("Default","S3") or type(attack_q32) is not int or not 0 <= attack_q32 < SIGN64:
        raise ValueError("林的物理受击需要有效模式与非负 Q32.32 攻击")
    resist = defense[mode]
    amount = max(mul(attack_q32,MINIMUM_RATIO),attack_q32-resist["defense_q32"])
    return dict(attack_q32=attack_q32, after_defense_q32=amount,
                **barrier_hit(amount,resist["shield_threshold_q32"],active=shield_active))


def lin_shell_window(tables, card, sp, bomber):
    """Stop the unchanged-barrier replay if a confirmed shell could change allied HP or duties."""
    if not bomber:
        return None
    through, pending = bomber["through_frame"], bomber["pending"]
    shots = sorted((s for s in bomber.get("shots", ()) if s.get("impact_frame") is not None
                    and s["impact_frame"] <= through), key=lambda s:s["impact_frame"])
    report = dict(through_frame=through, pending=pending, impacts=[])
    if not shots:
        return report
    defense = report["defense"] = lin_physical_defense(tables,card)
    buttons = sp.get("buttons", ())
    windows = []
    for button in buttons:
        if button["outcome"] != "begin_animation":
            continue
        end, poll = min([(button["scheduled_end"], button["scheduled_end"])] + [
            (s["frame"], s["frame"] + int(s.get("input_phase") == "after_tick"))
            for s in buttons if s["frame"] > button["frame"] and s["outcome"] == "stopped"])
        windows.append((button["mode_buff_frame"], end, poll))
    for shot in shots:
        frame = shot["impact_frame"]
        if not shot["affected_operators"]:
            continue
        reason = None
        if set(shot["affected_operators"]) != {LIN} or not defense or shot.get("scaled_attack_q32") is None:
            reason = "bombardier_operator_splash_or_defense_unresolved"
        elif defense["skill_defense_poll_seconds"] and any(any(0 <= frame-change <= ceil(defense["skill_defense_poll_seconds"]/tables.fixed_timestep())+1
                     for change in (begin,poll)) for begin,_,poll in windows):
            reason = "lin_trait_poll_at_mode_transition"
        else:
            # A shell updates in OBJECTS, before this frame's manual input and skill coroutine.
            mode = "S3" if any(start < frame <= stop for start,stop,_ in windows) else "Default"
            hit = lin_physical_hit(defense, shot["scaled_attack_q32"], mode=mode)
            report["impacts"].append(dict(frame=frame, mode=mode, source=shot["source"],
                ignore_for_sp=shot.get("ignore_for_sp"), release_frame=shot["release_frame"], **hit))
            if hit["breaks"] or hit["hp_loss_q32"]:
                reason = "bombardier_breaks_lin_barrier_or_damages_hp"
        if reason:
            report.update(through_frame=frame-1,pending=dict(frame=frame,reason=reason,instance_id=shot["source"]))
            break
    return report


def inactive_window(tables, scene, cards, deployments, skill_points_by_operator, spawned, horizon, *, wave_schedule=None,
                    bomber_window=None):
    """Return an inclusive first operator attack horizon and the next possible Lin interaction."""
    placements = list(deployments.values()) if isinstance(deployments, dict) else list(deployments)
    if len(placements) < 2:
        return dict(status="single_deployment", through_frame=horizon, reason=None, pending=None, conditions=[])
    first, lin = placements[:2]
    start = lin["frame"]
    def conservative(reason):
        return dict(status="unresolved", through_frame=min(horizon, start - 1), reason=reason,
                    pending=dict(frame=start, reason=reason), conditions=[])
    if (scene.stage["stageId"] != "act28side_ex07" or scene.stage["difficulty"] != "NORMAL"
            or [p["operator_id"] for p in placements[:2]] != [EBENHOLZ, LIN]
            or cards[LIN]["skill_id"] != "skchr_lin_3" or scene.tile(lin["row"], lin["col"])["heightType"] != "HIGHLAND"):
        return conservative("additional_operator_combat_interactions")
    sp = (skill_points_by_operator or {}).get(LIN, {})
    if ("buttons" not in sp or sp.get("deployment_frame") != start or wave_schedule is None
            or not wave_schedule.get("fragments")):
        return conservative("lin_input_or_wave_history_unavailable")
    threshold = _ordinary_threshold(tables, cards[LIN])
    if threshold is None or not lin_attack_modes(tables)["Default"]["disarmed"]:
        return conservative("unreviewed_lin_barrier_or_attack_mode")
    # None of these prefabs has an unblocked attack. The rogue also lacks a combat ability.
    harmless_on_highland = (*MELEE_ONLY, DRONE, AXE)
    for enemy in harmless_on_highland:
        if scene.enemies[enemy]["prefabKey"]["m_value"] != enemy:
            return conservative("unreviewed_opening_enemy_prefab")
        cfg = tables._component_fields("enemies", enemy, ("_attack", "_attackTrigger", "_combat"),
                                       "lin_opening_enemy_default", children=("Modes", "Default"))
        if cfg["_attack"]["m_PathID"] or cfg["_attackTrigger"]["m_PathID"] or bool(cfg["_combat"]["m_PathID"]) != (
                enemy != "enemy_1354_eghost_2"):
            return conservative("opening_enemy_can_attack_without_block")
    _templates(tables)
    grid = scene.level["mapData"]["map"]
    ranges = tables.read("excel/range_table.json")
    vents = []
    for unit in scene.predefines:
        alias = unit["alias"]
        if unit["inst"]["characterKey"] != "trap_124_eradio" or alias not in scene.device_skills:
            return conservative("unreviewed_stage_device")
        skill = scene.device_skills[alias]
        if skill["prefabId"] != "sktok_eradio":
            return conservative("unreviewed_vent_ability")
        placement = dict(**unit["position"], direction=unit["direction"])
        cells = range_cells(ranges[skill["rangeId"]], placement, len(grid), len(grid[0]))
        if (lin["row"], lin["col"]) in cells:
            damage = next(b["value"] for b in skill["blackboard"] if b["key"] == "value")
            if barrier_hit(int(damage * ONE), threshold)["breaks"]:
                return conservative("vent_can_break_lin_barrier")
            vents.append(dict(alias=alias, skill_level=unit["mainSkillLvl"], maximum_pre_resistance_damage=damage,
                              shield_breaks=False, natural_initial_sp=skill["spData"]["initSp"],
                              skill_cost=skill["spData"]["spCost"], received_damage_can_roll_talent=True))
    candidates = [dict(frame=b["frame"], reason="lin_skill_button_input", modeled_outcome=b["outcome"])
                  for b in sp["buttons"]]
    if len(placements) > 2:
        candidates.append(dict(frame=placements[2]["frame"], reason="additional_operator_combat_interactions"))
    for event in wave_schedule["events"]:
        if event["kind"] == "spawn" and event["enemy_key"] not in harmless_on_highland:
            until = bomber_window["through_frame"] if bomber_window and event["enemy_key"] == "enemy_1359_ekulsr_2" else -1
            candidates.append(dict(frame=max(start, event["frame"], until+1), reason="unreviewed_enemy_interaction",
                                   enemy=event["enemy_key"], instance_id=event["instance_id"]))
    for instance, record in spawned.items():
        if record["enemy"] not in harmless_on_highland:
            until = bomber_window["through_frame"] if bomber_window and record["enemy"] == "enemy_1359_ekulsr_2" else -1
            candidates.append(dict(frame=max(start, record["spawn_frame"], until+1), reason="unreviewed_enemy_interaction",
                                   enemy=record["enemy"], instance_id=instance))
    if wave_schedule.get("pending"):
        pending = wave_schedule["pending"]
        candidates.append(dict(frame=max(start, pending.get("frame", start)), reason="wave_schedule_pending"))
    last = wave_schedule["fragments"][-1].get("complete_frame")
    all_waves_scheduled = len(wave_schedule.get("waves", ())) == len(scene.level["waves"]) and not wave_schedule.get("pending")
    if isinstance(last, int) and not all_waves_scheduled:
        candidates.append(dict(frame=max(start, last + 1), reason="beyond_scheduled_wave"))
    if not candidates and not all_waves_scheduled:
        return conservative("future_spawns_unresolved")
    pending = min(candidates, key=lambda r: r["frame"], default=None)
    other = min((r["frame"] for r in candidates if r["reason"] != "lin_skill_button_input"), default=horizon+1)
    return dict(status="conditional_inactive_lin", reason="ordinary_lin_cannot_damage_enemies",
                through_frame=min(horizon, pending["frame"] - 1) if pending else horizon, pending=pending,
                reviewed_highland_environment_through=min(horizon, other-1),
                ordinary_shield_threshold_q32=threshold, covering_vents=vents,
                reviewed_enemies=list(harmless_on_highland), bombardier_window=bomber_window,
                evidence="runs/2026-11-19-lin-skill/barrier.json",
                conditions=["ordinary_intact_default_barrier_at_deployment", "no_external_damage_modifiers_or_unlisted_hits",
                            "input_history_complete_and_normal_highland_placement",
                            "existing_enemy_motion_and_vent_damage_or_slow_conditions_still_apply"],
                lin_hit_sp="eligible_vent_hit_can_roll_talent_after_full_absorption")

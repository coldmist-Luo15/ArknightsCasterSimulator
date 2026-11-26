"""Ordinary Ebenholz attack inputs from the pinned CN tables and character prefab."""
import struct
from math import isfinite

from .attributes import base_attack_at_level
from .baked_mount import single
from .fixed import ONE, from_float32_bits, mul
from .module_effects import active_board, equipped_phase, module_stat, selected_branch, talent_rows
from .mystic_equipment import delta_hit_talent, isolated_hit_talent, y_hit_talent
from .skill_assets import suspendable_buff


def mystic_attack_inputs(tables, card, *, mode, live_element_receiver):
    """Resolve attack and projectile modifiers for a living caster in one stable mode."""
    key = card["operator_id"]
    if key != "char_4046_ebnhlz":
        return dict(status="unresolved", reason="unreviewed_ordinary_damage_ability")
    if mode not in ("Default", "S3"):
        return dict(status="unresolved", reason="unreviewed_mystic_damage_mode")
    elite, level, pot = card["elite"], card["level"], card["potential"]
    if level is None or pot is None:
        return dict(status="unresolved", reason="unknown_level_or_potential_for_attack")
    unit = tables.read("excel/character_table.json")[key]
    frames = unit["phases"][elite]["attributesKeyFrames"]
    base = base_attack_at_level(frames,level)
    if base is None:
        return dict(status="unresolved", reason="level_attack_interpolation")
    favor = unit["favorKeyFrames"][-1]["data"]["atk"]
    potential_bonus = 0
    for rank in unit["potentialRanks"][:pot - 1]:
        if rank["buff"] is None:
            continue
        for mod in rank["buff"]["attributes"]["attributeModifiers"]:
            if mod["attributeType"] == "ATK":
                if (mod["formulaItem"] != "ADDITION" or mod["loadFromBlackboard"]
                        or mod["fetchBaseValueFromSourceEntity"] or not mod["value"].is_integer()):
                    return dict(status="unresolved", reason="unreviewed_potential_attack_modifier")
                potential_bonus += int(mod["value"])

    branch = selected_branch(tables, card)
    if branch=="msc-d" and not live_element_receiver:
        return dict(status="unresolved", reason="necrosis_receiver_state_required")
    if branch not in (None, "msc-x", "msc-y", "msc-d"):
        return dict(status="unresolved",reason="unreviewed_damage_module")
    if branch and not card.get("module_level"):
        return dict(status="unresolved", reason="unknown_damage_module_level")
    phase = equipped_phase(tables, card)
    module_bonus = int(module_stat(phase, "atk"))
    charge, extra = [active_board(talent_rows(phase, index, str(index + 1), slot["candidates"]), elite, level, pot)
                     for index, slot in enumerate(unit["talents"])]
    hit_prefab = next((p["resKey"] for p in phase.get("parts", ()) if not p["isToken"]
                       and p["target"] == "TALENT" and any(r["prefabKey"] == "2" for r in
                       p["addOrOverrideTalentDataBundle"]["candidates"] or ())), None)

    prefs = []
    for part, path in (("main", ("Modes", mode, "Body", "Attack")),
                       ("stored", ("Trait", "ChargeAttackGroup", "ExChargeAttack" + mode))):
        cfg = tables._component_fields("characters", key,
            ("_atkScale", "_atkScaleKey", "_damageType", "_extraDamageType", "_attackType", "_projectileKey",
             "_exProjectileKey", "_metadata", "_useCachedAtkOnly"),
            f"mystic_{part}_damage_{mode}", children=path)
        prefix = "trait_s3." if mode == "S3" and part == "stored" else ""
        if ((cfg["_damageType"], cfg["_extraDamageType"], cfg["_attackType"], cfg["_atkScaleKey"]) != (2, 0, 1, "atk_scale")
                or cfg["_useCachedAtkOnly"] or cfg["_metadata"]["blackboardPrefix"] != prefix):
            return dict(status="unresolved", reason="unreviewed_ordinary_damage_type")
        prefs.append(cfg)
    projectile_names = tuple(dict.fromkeys([cfg["_projectileKey"] for cfg in prefs] + [prefs[1]["_exProjectileKey"]]))
    hit_rule = (delta_hit_talent(tables,card,projectile_names,extra) if branch=="msc-d" else
                y_hit_talent(tables, hit_prefab, projectile_names) if hit_prefab
                else isolated_hit_talent(tables, key) if extra.get("atk_scale") else dict(status="no_hit_talent"))
    base_attack = base + favor + potential_bonus + module_bonus
    attack_raw = base_attack * ONE
    stored = charge.get("atk_scale", prefs[1]["_atkScale"])
    flow = tables._component_fields("characters", key, ("_config",), "mystic_damage_dataflow")["_config"]
    transfer, = [r for r in flow if r["_targetKey"] == ("trait_s3.atk_scale" if mode == "S3" else "atk_scale")]
    if (transfer["_target"], transfer["_source"], transfer["_sourceTalentKey"], transfer["_sourceKey"]) != (2, 0, "1", "atk_scale"):
        return dict(status="unresolved", reason="unreviewed_charge_scale_transfer")
    if mode == "S3":
        if (not card.get("skill_level") or card.get("skill_id") != "skchr_ebnhlz_3" or
                (transfer["_formulaType"], transfer["_type"], transfer["_source2"], transfer["_sourceKey2"],
                 transfer["_validateSkillIndices"], transfer["_skillIndices"]) != (1, 2, 1, "talent_scale_multiplier", 1, [2])):
            return dict(status="unresolved", reason="unreviewed_skill_charge_scale_or_level")
        level = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][card["skill_level"]-1]
        board = {v["key"]: v["value"] for v in level["blackboard"]}
        if level["prefabId"] != "skchr_ebnhlz_3" or "atk_scale" in board:
            return dict(status="unresolved", reason="unreviewed_skill_attack_buff")
        buff = suspendable_buff(tables, level["prefabId"])
        modifiers = {(r["attributeType"], r["formulaItem"], r["value"], r["loadFromBlackboard"],
                      r["fetchBaseValueFromSourceEntity"]) for r in buff.get("attribute_modifiers", ())}
        if not buff["supported"] or modifiers != {(7, 0, 0, 1, 0), (1, 1, 0, 1, 0)}:
            return dict(status="unresolved", reason="unreviewed_skill_attribute_modifiers")
        stored = single(single(stored) * single(board["talent_scale_multiplier"]))
        bonus = from_float32_bits(int.from_bytes(struct.pack("<f", board["atk"]), "little"))
        attack_raw = mul(attack_raw, ONE + bonus)
    elif (transfer["_formulaType"], transfer["_type"], transfer["_validateSkillIndices"]) != (0, 0, 0):
        return dict(status="unresolved", reason="unreviewed_ordinary_charge_scale_transfer")
    return dict(status="conditional_ordinary_damage_inputs", damage_type="magical",
                mode=mode, attack=attack_raw / ONE if mode == "S3" else base_attack, attack_q32=attack_raw,
                attack_terms=dict(level=base, maximum_trust=favor, potential=potential_bonus, module=module_bonus),
                main_scale=prefs[0]["_atkScale"], stored_scale=stored, extra_scale=stored,
                main_projectile=prefs[0]["_projectileKey"], stored_projectile=prefs[1]["_projectileKey"],
                extra_projectile=prefs[1]["_exProjectileKey"],
                isolated_scale=extra.get("atk_scale", 0.0), splash_scale=extra.get("atk_scale_2", 0.0),
                talent_radius=extra.get("range_radius", 0.0), splash_excludes_original_target=True,
                hit_talent=hit_rule,
                unresolved=[] if hit_rule["status"] != "unreviewed_hit_talent" else ["unreviewed_hit_talent"])


def ordinary_damage_inputs(tables, card, enemy, *, mode="Default"):
    """Source stats and ordinary target base stats for the existing volley projection."""
    profile=mystic_attack_inputs(tables,card,mode=mode,live_element_receiver=False)
    if profile["status"]=="unresolved":
        return profile
    stats=enemy["attributes"]
    if not all(stats[k]["m_defined"] for k in ("maxHp","magicResistance")):
        return dict(status="unresolved",reason="missing_base_enemy_damage_attributes")
    overload=next((r["value"] for r in enemy["talentBlackboard"] or []
                   if r["key"]=="over_energy.magic_resistance"),0.0)
    if overload:
        row=tables.read("buff_table.json")["act28side_over_energy_buff"]
        changes=[m for m in row["attributes"]["attributeModifiers"] if m["attributeType"]=="MAGIC_RESISTANCE"]
        if (len(changes)!=1 or changes[0]["formulaItem"]!="ADDITION" or not changes[0]["loadFromBlackboard"]
                or changes[0]["fetchBaseValueFromSourceEntity"]):
            return dict(status="unresolved",reason="unreviewed_overload_resistance_modifier")
    prefab=enemy["prefabKey"]["m_value"]
    motion=enemy["motion"]["m_value"]
    tags=enemy["enemyTags"]["m_value"] or []
    ordinary_receiver=(motion=="WALK" and not tags and prefab in ("enemy_1002_nsabr","enemy_1037_lunsbr",
                       "enemy_1353_esabr_2","enemy_1354_eghost_2"))
    static_tag_receiver=((prefab,motion,tags) in (("enemy_1007_slime_3","WALK",["infection"]),
                                               ("enemy_1005_yokai","FLY",["drone"])))
    return dict(profile,base_hp=stats["maxHp"]["m_value"],base_res=stats["magicResistance"]["m_value"],
                ordinary_splash_receiver=not enemy["skills"] and (ordinary_receiver or static_tag_receiver),
                overloaded_resistance_bonus=overload,conditions=["maximum_trust_bonus","active_mode_no_external_attack_modifier",
                    "receiver_uses_base_stats_except_explicit_overload"])


def delta_projectile_profile(tables,card,hit):
    if card["module_info"]["typeIcon"]!="msc-d":
        raise ValueError("该黑键弹体需要已装备 Δ 模组")
    mode=hit["mode"]
    kind=hit["projectile"]
    if mode not in ("Default","S3") or kind not in ("main","stored","extra"):
        raise ValueError("需要黑键的普通或三技能模式，以及主弹、储存弹或额外储存弹的类型")
    cache=("eben_delta_damage",card["module"],card["module_level"],card["skill"],card["skill_level"],
           card["elite"],card["level"],card["potential"],mode)
    if cache not in tables._prefab_data:
        spec=mystic_attack_inputs(tables,card,mode=mode,live_element_receiver=True)
        if spec["status"]=="unresolved":
            raise ValueError(spec["reason"])
        tables._prefab_data[cache]=spec
    return tables._prefab_data[cache]


def delta_projectile_flight(tables,card,hit,end):
    """Follow an already launched trace projectile using its actual target mount per object tick."""
    from .caster_flight import projectile_reach
    from .caster_prefabs import caster_prefab_evidence
    from .mystic_projectiles import _ordinary_mover
    from .projectile_scene import projectile_profiles
    profile=delta_projectile_profile(tables,card,hit)
    projectile=profile[hit["projectile"]+"_projectile"]
    flight=hit["flight"]
    launch=flight["launch_frame"]
    mounts=flight["target_mounts"]
    if type(launch) is not int or launch<0 or launch>=end or not mounts:
        raise ValueError("黑键已发射的弹体需要有效发射帧和飞行窗口内的目标坐标")
    variants=projectile_profiles(tables,[projectile])[projectile].values()
    for v in variants:
        if not _ordinary_mover(v) or not v["has_only_trace_hit"]:
            raise ValueError("黑键弹体的强制命中或停止条件发生变化")
    ev=caster_prefab_evidence(tables,card["operator_id"],card["skill"],skill_level=card["skill_level"],
                              module_id=card["module"],module_level=card["module_level"])
    trajectory=projectile_reach(tables,ev,dict(projectile=projectile,frame=launch+1,origin=flight["origin"],
        target_mounts=mounts[:end-launch],round_position=flight["round_position"]))
    if trajectory["status"]=="in_flight_at_horizon" and len(mounts)<end-launch:
        raise ValueError("目标坐标在命中或观察窗结束前中断")
    if trajectory["status"] not in ("reached","in_flight_at_horizon"):
        raise ValueError("黑键弹体轨迹尚未解析")
    flight_ticks=(trajectory["reach_frame"] or end)-launch
    if any(flight_ticks*tables.fixed_timestep()>=v["lifecycle"]["_lifeTime"] for v in variants):
        raise ValueError("弹体抵达前达到寿命，需要处理寿命结束时的强制命中")
    return trajectory


def delta_projectile_impact(tables,card,receiver,frame,hit,base_resistance):
    """Use live source attack and the projectile's original damage packet."""
    from .damage import HP_EPSILON
    from .volley_damage import _raw_f32, _talent_distances
    profile=delta_projectile_profile(tables,card,hit)
    launch_mode=hit.get("launch_mode",hit["mode"])
    packet=profile if launch_mode==hit["mode"] else delta_projectile_profile(tables,card,dict(hit,mode=launch_mode))
    if receiver.hp<=HP_EPSILON:
        receiver.events.append(dict(frame=frame,kind="projectile_impact_skipped",reason="dead"))
        return
    target=hit["target_center"]
    others=hit["other_living_centers"]
    if any(len(p)!=2 or any(type(v) not in (int,float) or not isfinite(v) for v in p) for p in (target,*others)):
        raise ValueError("倚音需要命中时目标与其他有效敌人的地图中心坐标")
    atk=profile["attack_q32"]
    def deal(scale,source):
        receiver.hit_with_module_arts(frame,card,attack_q32=mul(atk,_raw_f32(scale)),
            effective_arts_resistance_q32=base_resistance+receiver.res_add,source=source)
    # The attachment array is applied in ascending index before this projectile's normal hit.
    if not any(_talent_distances(target,p,profile["talent_radius"])[1] for p in others):
        deal(profile["isolated_scale"],"ebnhlz_t_2")
    if receiver.hp<=HP_EPSILON:
        return
    elemental=profile["hit_talent"]["necrosis_scale"]
    if receiver.burst=="DARK" and elemental:
        receiver.apply_elemental_hp(frame,mul(atk,_raw_f32(elemental)),source="ebnhlz_e_004[t_2_ele]")
    if receiver.hp>HP_EPSILON:
        kind=hit["projectile"]
        deal(packet[kind+"_scale"],packet[kind+"_projectile"])

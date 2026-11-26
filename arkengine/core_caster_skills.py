"""V079 core-caster callbacks on a resolved battle state, after admission by the selector."""
from .baked_mount import single
from .battle_random import BattleRandom
from .caster_prefabs import caster_prefab_evidence
from .damage import ordinary_arts_hit
from .fixed import ONE, mul
from .levitate import ordinary_levitate
from .module_effects import active_board, equipped_phase, selected_branch, talent_rows
from .volley_damage import _raw_f32


def _board(rows):
    return {b["key"]:b["value"] for b in rows}


def _talent(tables, card, index, key=None):
    slots = tables.read("excel/character_table.json")[card["operator_id"]]["talents"]
    key = str(index+1) if key is None else key
    rows = talent_rows(equipped_phase(tables, card), index, key, slots[index]["candidates"] if index >= 0 else ())
    return active_board(rows, card["elite"], card["level"], card["potential"])


def _random(event,stream):
    return stream if stream is not None else BattleRandom(event["seed"])


def volcano_selection(eligible,maximum,stream):
    """SelectorTrigger.Search takes the first target; FROM_OWNER searches again at cast start."""
    if len(eligible)<2:
        targets=list(eligible)
        return dict(targets=targets,trigger_target=next(iter(targets),None),cast_selection=targets)
    if stream is None:
        raise ValueError("火山多候选索敌需要战斗随机流")
    trigger=stream.select_targets(eligible,maximum)[0]
    cast=stream.select_targets(eligible,maximum)
    # AbilityStandard.UpdateTargets keeps an already-included input in place.
    # Otherwise it removes the last selected entry and appends the live input.
    targets=cast if trigger in cast else [*cast[:-1],trigger]
    return dict(targets=targets,trigger_target=trigger,cast_selection=cast)


def _arts(attack, scale, resistance):
    return ordinary_arts_hit(attack_raw=mul(_raw_f32(attack), _raw_f32(scale)),
                             resistance_raw=_raw_f32(max(0, resistance)), hp_raw=ONE).damage_raw


def eyja_deployment(tables,card,event,stream=None):
    first = _talent(tables, card, 0)
    talent = _talent(tables,card,1)
    enhanced = "attack_speed_min" in talent
    if enhanced and type(event.get("elite_or_boss_in_range")) is not bool:
        raise ValueError("装备 Y 时需要部署瞬间范围中有无精英或领袖的判断")
    templates=tables.read("battle/buff_template_data.json")
    graph=templates["amgoat_e_t_2" if enhanced else "amgoat_t_2"]["eventToActions"]["ON_OWNER_LOCATE"]
    forced=enhanced and event["elite_or_boss_in_range"]
    if enhanced:
        selector,=graph
        if selector["_conditionNode"]["_targetLevelMask"]!="ELITE_AND_BOSS":
            raise ValueError("艾雅法拉模组的部署筛选发生变化")
        graph=selector["_succeedNodes" if forced else "_failNodes"]
    if forced:
        if [(n["_inputKey"],n["_minusParamKey"]) for n in graph[:2]]!=[("sp_max","factor"),("attack_speed_max","factor")]:
            raise ValueError("精英分支资源上界计算发生变化")
        sp=int(talent["sp_max"]-talent["factor"])
        speed=int(talent["attack_speed_max"]-talent["factor"])
    else:
        if stream is None:
            stream=BattleRandom(event["seed"])
        setters=[n for n in graph if n["$type"].split("+")[-1].split(",")[0]=="RandomSetter"]
        keys=[("sp",False),("attack_speed",True)] if enhanced else [("sp",False)]
        if [(n["_targetKey"],n["_convertToInt"]) for n in setters]!=keys:
            raise ValueError("乱火黑板的抽样顺序或取整位置发生变化")
        samples={}
        for key,convert_first in keys:
            raw=stream.range_fp(_raw_f32(talent[key+"_min"]),_raw_f32(talent[key+"_max"]))
            # ConvertToInt floors FP first. ModifySp instead reads floor of the stored float.
            samples[key]=raw//ONE if convert_first else int(single(raw/ONE))
        sp,speed=samples["sp"],samples.get("attack_speed",0)
    return dict(sp=sp,attack_speed=speed,caster_attack_bonus=first["atk"],forced_max=bool(forced),
                rng="battle_legacy_random",draws_used=0 if forced else len(keys))


def apply_eyja_opening_talent(tables,card,recovery,seed,elite_query):
    """Character.OnLocate -> BuffContainer.OnOwnerLocate, before the next recovery tick."""
    report = recovery.report
    if card["elite"] < 2:
        return recovery
    if card["module"] == "unknown" or card["potential"] is None or (
            card["module"] and card["module_level"] is None):
        reason = "eyja_deployment_loadout_unknown"
    else:
        talent = _talent(tables,card,1)
        elite = None
        if "attack_speed_min" in talent:
            elite, details = elite_query()
            report["deployment_elite_query"] = details
        reason = None
        if "attack_speed_min" in talent and elite is None:
            reason = "eyja_deployment_elite_selector_unresolved"
        elif not elite and seed is None:
            reason = "eyja_deployment_random_sample_required"
    if reason:
        report.update(status="unresolved",reason=reason,first_ready_frame=None,ready_frames=[])
        return None
    effect = eyja_deployment(tables,card,dict(elite_or_boss_in_range=elite,seed=seed))
    state = recovery.state
    before = state.points
    state.points = min(state.maximum,before+effect["sp"])
    report.update(deployment_talent=dict(effect,sp_before=before,sp_after=state.points,
                  rng_scope="isolated_deployment_draws"),initial_sp=state.points)
    if state.points >= state.cost and not report["ready_frames"]:
        report["first_ready_frame"] = report["deployment_frame"]
        report["ready_frames"].append(report["deployment_frame"])
    return recovery


def _eyja(tables, card, e, b, stream=None):
    slot, kind = card["skill"], e["kind"]
    if kind=="deployment_roll":
        return eyja_deployment(tables,card,e,stream)
    if slot == 1 and kind == "skill_buff":
        count = e["previous_uses_this_deployment"]
        if type(count) is not int or count < 0:
            raise ValueError("部署后的使用次数需要非负整数")
        prefix = "amgoat_s_1[b]." if count else "amgoat_s_1[a]."
        return dict(attack_speed=b[prefix+"attack_speed"], atk_add_ratio=b.get(prefix+"atk", 0))
    if slot == 2 and kind == "ignition_impact":
        primary = e["primary"]
        targets = e["splash_targets"]
        if primary not in targets:
            raise ValueError("点燃的爆炸目标中应包含主目标")
        pen = 10 if selected_branch(tables, card) == "ccr-x" else 0
        packets = []
        for target, resistance in targets.items():
            # Values are effective RES before this cast's debuff. An existing
            # copy of this same debuff must be removed before preparing this input.
            res = resistance * (1 + b["magic_resistance"]) if target == primary else resistance
            packets.append(dict(target=target, kind="explosion", damage_q32=_arts(e["source_atk"], b["atk_scale"], res-pen)))
        res = targets[primary] * (1 + b["magic_resistance"]) - pen
        packets.append(dict(target=primary, kind="primary", damage_q32=_arts(e["source_atk"], b["atk_scale_2"], res)))
        return dict(packets=packets, resistance_debuff_target=primary, res_ratio=b["magic_resistance"],
                    debuff_duration_seconds=b["duration"], source_attack_basis="current_final_atk")
    if slot == 3 and kind == "volcano_select":
        eligible = list(e["eligible"])
        if len(set(eligible)) != len(eligible):
            raise ValueError("火山候选目标重复")
        count = int(b["attack@max_target"])
        rng=_random(e,stream) if len(eligible)>1 else stream
        return dict(**volcano_selection(eligible,count,rng), atk_add_ratio=b["atk"],
                    base_attack_time_add=b["base_attack_time"], rng="battle_legacy_random",
                    condition="same_eligible_entities_during_trigger_and_cast_start_in_native_order")
    raise ValueError("艾雅法拉所选技能不处理该回调")


def ceobe_peel_ratio(peel, previous_hits):
    if type(previous_hits) is not int or previous_hits < 0:
        raise ValueError("连续同目标次数需要非负整数")
    ratio = single(peel["atk_scale"])
    if "delta_atk_scale" in peel:
        ratio,cap,delta = (single(peel[k]) for k in ("basic_atk_scale","max_atk_scale","delta_atk_scale"))
        # AddBuffBlackboard.Execute 0x311b7e0: fadd s8, s8, s0 followed by the cap.
        for _ in range(previous_hits):
            if ratio>=cap:
                break
            ratio=min(cap,single(ratio+delta))
    return ratio


def _ceobe(tables, card, e, b):
    slot = card["skill"]
    if e["kind"]=="lone_check":
        from .ceobe_lone import ceobe_alone
        locked=card["module"]=="uniequip_003_cerber" and card["module_level"]>=2
        active=ceobe_alone(tables,card,e,e["units_at_deploy"] if locked else e["units"])
        talent=_talent(tables,card,1) if active else {}
        return dict(active=active,attack_ratio=talent.get("atk",0),attack_speed_add=talent.get("attack_speed",0),
                    sample="deployment" if locked else "current")
    if e["kind"] == "target":
        targets = e["ordered_eligible"]
        # Stable ordering retains the supplied ordinary priority on a tie.
        key = {1: lambda v: v["blocked"], 2: lambda v: -v["defense"], 3: lambda v: v["defense"]}[slot]
        target = min(targets, key=key) if targets else None
        return dict(target=target["id"] if target else None)
    if e["kind"] == "hit":
        peel = _talent(tables, card, 0)
        pen = 10 if selected_branch(tables, card) == "ccr-x" else 0
        ratio = ceobe_peel_ratio(peel,e["previous_consecutive_hits_on_target"] if "delta_atk_scale" in peel else 0)
        return dict(main_damage_type="physical" if slot == 3 else "arts", main_scale=b.get("atk_scale", 1),
                    root_seconds=b.get("duration", 0), silence_seconds=b.get("attack@silence", 0),
                    peel_ratio=ratio,peel_arts_damage_q32=_arts(e["defense"], ratio, e["resistance"]-pen),
                    skill_atk_add_ratio=b.get("atk", 0), skill_interval_factor=b["base_attack_time"] if slot == 2 else 1)
    raise ValueError("刻俄柏所选技能不处理该回调")


def _hoho(tables, card, e, b, stream=None):
    slot = card["skill"]
    if slot == 2 and e["kind"] == "barrage":
        eligible = list(e["eligible"])
        evidence = caster_prefab_evidence(tables, card["operator_id"], slot, skill_level=card["skill_level"])
        ability, = (c for c in evidence["character"]["components"] if c["path"] == "Modes/S2/Body/Attack"
                    and "_projectileKey" in c["fields"])
        if ability["script"] != "MultiRangedAttack":
            raise ValueError("飘浮九连发的攻击组件不符")
        count = 1 + ability["fields"]["_additionalTimes"]
        rng = _random(e,stream)
        if len(set(eligible))!=len(eligible):
            raise ValueError("飘浮九连发的候选对象重复")
        phase=e.get("selection_phase","before_trigger")
        if phase not in ("before_trigger","before_first_release"):
            raise ValueError("九连发选靶快照需要指定起手前或首次发射前")
        if phase=="before_first_release" and stream is None:
            raise ValueError("从首次发射续算需传入已经执行过宿主触发和其他先行随机事件的战斗随机流")
        before=rng.draws
        trigger=rng.select_targets(eligible,1) if eligible and phase=="before_trigger" else []
        shots=[rng.select_targets(eligible,1)[0] for _ in range(count)] if eligible else []
        return dict(ability=ability["script"], projectile=ability["fields"]["_projectileKey"],
                    scope="fixed_candidates_selection_only",selection_phase=phase,trigger_target=trigger[0] if trigger else None,
                    targets=shots,draws=[before,rng.draws],
                    condition="unchanged_native_ordered_candidates_no_intervening_dice", scale=b["attack@atk_scale"], levitate_probability=b["attack@prob"],
                    levitate_seconds=b["attack@levitate"])
    if e["kind"] != "hit":
        raise ValueError("霍尔海雅所选技能不处理该回调")
    if slot == 1:
        levitate = b["levitate"] if e["issued_projectiles"] == 1 else 0
        scale = b["atk_scale"]
    elif slot == 2:
        levitate = b["attack@levitate"] if _random(e,stream).dice(b["attack@prob"]) else 0
        scale = b["attack@atk_scale"]
    else:
        spec = caster_prefab_evidence(tables, card["operator_id"], slot, skill_level=card["skill_level"])
        parts = spec["projectiles"]["projectile_chr_heyak_s3"]["logic"]["components"]
        curve, = (c["fields"]["_scaleCurve"]["m_Curve"] for c in parts if c["script"]=="ScaleAtkScaleViaTimeRatioBehaviour")
        lifetime, = (c["fields"]["_lifeTime"] for c in parts if c["script"]=="SimpleProjectile")
        from .hoolheyak_curve import tornado_scale
        age = e["projectile_age_q32"] if "projectile_age_q32" in e else _raw_f32(e["projectile_age_seconds"])
        scale = tornado_scale(curve,age,_raw_f32(lifetime),b)/ONE
        levitate = b["attack@levitate"] if e["first_target"] else 0
    talent = _talent(tables, card, 0)
    motion = {}
    if "target_motion" in e:
        motion = ordinary_levitate(levitate,e["target_motion"])
        flying = motion["airborne_after"]
    else:
        flying = e["airborne"] or bool(levitate and e["levitate_accepted"])
    return dict(skill_scale=scale, requested_levitate_seconds=levitate, talent_applies=flying,
                talent_atk_factor=talent["atk_scale"] if flying else 1, silence_seconds=talent["silence"] if flying else 0,**motion)


def _logos(tables, card, e, b):
    slot = card["skill"]
    if slot == 1 and e["kind"] == "execute_check":
        hp, atk = e["hp"], e["source_atk"]
        execute = 0 < hp < atk*b["attack@kill_atk_scale"] and not e["attempted_this_entry"]
        return dict(attempt=execute, sourceless_true_damage=b["attack@kill_damage"] if execute else 0,
                    secondary_arts_base_if_killed=hp if execute else 0)
    if slot == 2 and e["kind"] == "lock_tick":
        ticks = e["prior_stacks"]
        if type(ticks) is not int or ticks < 0:
            raise ValueError("锁定层数无效")
        stacks = min(ticks, int(b["attack@max_stack_cnt"]))
        return dict(interval_seconds=b["attack@cooldown"], arts_scale=b["attack@atk_scale_base"]+stacks*b["attack@atk_scale_delta"],
                    move_speed_ratio=1+stacks*b["attack@move_speed"], next_stacks=min(stacks+1, int(b["attack@max_stack_cnt"])))
    if slot == 3 and e["kind"] == "projectile_field":
        inside = e["hostile"] and e["inside_horizontal_range"]
        return dict(speed_factor=b["projectile_move_scale"] if inside and e["speed_modifiable"] else 1,
                    clear=bool(inside and e["at_skill_end"] and e["eliminable"] and not e["disarmed"]),
                    max_attack_targets=int(b["attack@max_target"]))
    raise ValueError("逻各斯所选技能不处理该回调")


CORE_CALLBACKS = {"char_180_amgoat":_eyja, "char_2013_cerber":_ceobe,
                  "char_4027_heyak":_hoho, "char_4133_logos":_logos}


def execute_core_skill(tables, card, event, stream=None):
    if not card["panel_attributes"]:
        return dict(status="unresolved", reason="special_module_requires_mode_context")
    board = _board(card["skill_row"]["blackboard"])
    if card["operator_id"]=="char_2013_cerber" and event["kind"]=="ceobe_battle":
        from .ceobe_battle import run_ceobe_battle
        return run_ceobe_battle(tables,card,event,board)
    handler = CORE_CALLBACKS[card["operator_id"]]
    result = handler(tables,card,event,board,stream) if handler in (_eyja,_hoho) else handler(tables,card,event,board)
    return {"scope":"selected_skill_callback","frame":event["frame"],**result}

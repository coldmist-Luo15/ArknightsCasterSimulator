"""Load one target caster, skill and equipment branch into the V079 event model."""
from .caster_catalog import require_available_skill, resolve_caster_loadout, supports_combat_loadout
from .caster_equipment_traits import module_branch_trait
from .caster_cast import run_committed_cast
from .caster_flight import projectile_reach
from .caster_prefabs import caster_prefab_evidence
from .caster_prts import caster_prts_evidence
from .caster_ranges import skill_range_cells
from .core_caster_skills import CORE_CALLBACKS, execute_core_skill
from .fixed import mul
from .lin_active import lin_s3_impact, lin_s3_profile
from .lin_interactions import barrier_hit,lin_with_block_buffs
from .module_effects import active_board, equipped_phase, selected_branch, talent_rows
from .mystic_storage import charge_limits, redistribute_charges, spend_on_target, target_level_masks
from .mystic_timing import ebenholz_timing
from .skill_assets import lin_hit_talent
from .volley_damage import _raw_f32


def _lin(tables, c, e, stream=None):
    kind = e["kind"]
    if kind == "incoming_sp":
        rule = lin_hit_talent(tables, c)
        roll, = rule["rolls"]
        flags = ("ignore_for_sp", "sp_blocked")
        if any(type(e[k]) is not bool for k in flags):
            raise ValueError("受击忽略技力标志、阻回状态必须明确")
        if e["phase"] not in ("OBJECTS", "COROUTINES") or (stream is None and type(e.get("seed")) is not int):
            raise ValueError("受击时相或模型种子无效")
        if type(e["index"]) is not int or e["index"] < 0:
            raise ValueError("同帧同阶段的受击序号应从零开始")
        from .battle_random import BattleRandom
        dice=stream if stream is not None else BattleRandom(e["seed"])
        proc=not e["ignore_for_sp"] and dice.dice(roll["probability"])
        return dict(roll=roll,proc=proc,uncapped_sp_credit=roll["sp"] if proc and not e["sp_blocked"] else 0,
                    rng="battle_legacy_random",scope="shared_battle_stream" if stream is not None else "isolated_native_draw")
    if c["skill"] == 3 and kind == "lin_impact":
        p = lin_s3_profile(tables, c)
        if p["status"] == "unresolved":
            return p
        return lin_s3_impact(p, e["receivers"], source=e["source"], aura_count=e.get("aura_count"),
                             shield_at_refresh=e.get("shield_at_refresh"))
    unit = tables.read("excel/character_table.json")[c["operator_id"]]
    phase = equipped_phase(tables, c)
    shield = active_board(talent_rows(phase, 0, "1", unit["talents"][0]["candidates"]),
                          c["elite"], c["level"], c["potential"])
    b = {i["key"]:i["value"] for i in c["skill_row"]["blackboard"]}
    if kind == "barrier_hit":
        threshold = shield["value"] * (b["talent_scale"] if c["skill"] == 3 and e["skill_active"] else 1)
        options=dict(active=e["barrier_active"],ignore_block=e["ignore_block"])
        amount=_raw_f32(e["incoming_after_defense"])
        if "attached_block_buffs" in e:
            hit=lin_with_block_buffs(tables,amount,_raw_f32(threshold),attached=e["attached_block_buffs"],
                                    damage_type=e.get("damage_type"),**options)
        else:
            hit=barrier_hit(amount,_raw_f32(threshold),**options)
        return dict(**hit, recovery_seconds=shield["interval"] if hit["breaks"] else None,
                    explosion_atk_scale=shield["atk_scale"] if hit["breaks"] else 0)
    if c["skill"] == 1 and kind == "toggle":
        if type(e["was_attacking"]) is not bool:
            raise ValueError("切换时需要给明原先是否处于攻击状态")
        on = not e["was_attacking"]
        return dict(attacks=on, atk_add_ratio=b["atk"] if on else 0, attack_time_add=b["base_attack_time"] if on else 0,
                    sluggish_seconds=b["attack@sluggish"] if on else 0, offensive_trait_mode=on)
    if c["skill"] == 2 and kind == "share_barrier":
        return dict(holders=list(dict.fromkeys(e["eligible_allies"])), threshold=shield["value"],
                    separate_recovery_seconds=shield["interval"], on_break_source=c["operator_id"],
                    explosion_atk_scale=shield["atk_scale"], atk_speed_bonus=b["attack_speed"], taunt_delta=b["taunt_level"])
    raise ValueError("所选林技能不处理该回调")


def _eben(tables, c, e):
    branch = selected_branch(tables, c)
    if e["kind"] == "delta_output":
        if branch != "msc-d":
            raise ValueError("该事件需要黑键 Δ 模组")
        phase = equipped_phase(tables, c)
        args = c["elite"], c["level"], c["potential"]
        trait = active_board(talent_rows(phase, -1, "10", []), *args)
        talent = active_board(talent_rows(phase, 1, "2", []), *args)
        return dict(necrosis_buildup_q32=mul(_raw_f32(e["arts_damage"]), _raw_f32(trait["ep_damage_ratio"])),
                    bonus_element_damage_q32=(mul(_raw_f32(e["source_atk"]), _raw_f32(talent.get("element_atk_scale",0)))
                                              if e["in_necrosis_burst"] and e["projectile_talent_hit"] else 0))
    limits = charge_limits(tables, c)
    if limits["status"] == "unresolved":
        return limits
    mode = e["mode"]
    if mode not in limits["modes"]:
        raise ValueError("需要 Default 或 S3 储能模式")
    cap = limits["modes"][mode]
    normal, extra = redistribute_charges(e["normal"], e["extra"], normal_limit=cap["normal_limit"],
                                         extra_limit=cap["extra_limit"])
    if e["kind"] == "switch_storage":
        return dict(normal=normal, extra=extra, limits=cap, timing=ebenholz_timing(tables, c))
    if e["kind"] == "volley_release":
        n, x = spend_on_target(normal, extra, e["target_level"], target_level_masks(tables,c["operator_id"])["modes"][mode])
        return dict(main=1, ordinary_fired=n, extra_fired=x, ordinary_remaining=normal-n, extra_remaining=extra-x)
    raise ValueError("所选黑键技能不处理该回调")


def _start(tables,evidence,options):
    from .caster_start import CAST_CONTROLLERS, resolve_cast_start
    from .caster_special_start import resolve_special_start
    control, = [a for a in evidence["skill"]["components"] if not a["path"] and a["active"] and a["enabled"]
                and "_allowSpRecoveryWhenAffecting" in a["fields"]]
    fn = resolve_cast_start if control["script"] in CAST_CONTROLLERS else resolve_special_start
    if fn is resolve_special_start:
        options = {k:v for k,v in options.items() if k!="effective_attack_interval"}
    return fn(tables,evidence,**options)


def _skill_timeline(tables,evidence,event):
    """One delivered trigger and its subsequent uninterrupted, resolved events on the same clock."""
    frame, end = event["frame"], event["end_frame"]
    if type(end) is not int or end < frame:
        raise ValueError("时钟终点必须是起始帧之后的整数逻辑帧")
    options = {k:v for k,v in event.items() if k not in ("kind","end_frame","watched_buff_survives")}
    start = _start(tables,evidence,options)
    events = [dict(e) for e in start["events"] if e["frame"]<=end]
    result = dict(start_status=start["status"],controller=start["controller"],events=events,
                  cast_frame=start["cast_frame"],unresolved=[],through_frame=end)
    commit = start["cast_frame"]
    if commit is None or commit>end:
        return result
    phase = options.get("phase","after_tick")
    stance = start["status"] in ("toggle_applied","ability_attached")
    # INPUT follows OBJECTS. A start animation finishes inside a later coroutine drain.
    elapsed_begin = commit>frame and any(e["kind"]=="begin_animation" for e in events)
    object_already_ticked = phase=="input" or (phase=="before_coroutines" and event.get("owner_tick_pending") is False)
    fixed_replacement = (evidence["skill_id"]=="skchr_ifrit_2" and start["status"]=="replacement_registered"
                         and options.get("source")=="before_attack" and phase=="before_coroutines")
    if phase!="after_tick" and not elapsed_begin and not (stance and object_already_ticked) and not fixed_replacement:
        result["unresolved"].append("end_clock_for_this_dispatch_phase")
        return result
    if start["status"]=="replacement_registered" and options.get("source")!="before_attack":
        result["unresolved"].append("next_ordinary_attack_start_after_registration")
        return result
    after_options = {k:event[k] for k in ("facing","direction","effective_attack_interval","watched_buff_survives") if k in event}
    if fixed_replacement:
        after_options["commit_phase"]="before_coroutines"
    after = run_committed_cast(tables,evidence,horizon=end-commit,**after_options)
    result["recovery_basis"]=after["recovery_basis"]
    first_free=after["first_unblocked_recovery_tick"]
    result["first_unblocked_recovery_frame"]=commit+first_free if first_free is not None else None
    first_spell = start.get("spell_frame")
    onset_events = {(e["kind"],e["frame"]) for e in events}
    for e in after["events"]:
        at = commit+e["frame"]
        if e["kind"]=="accepted_cast" or (e["kind"],at) in onset_events:
            continue
        events.append(dict(e,frame=at))
    events.sort(key=lambda e:e["frame"])
    result["unresolved"].extend(k for k in after["unresolved"]
                                if first_spell is None or k!="spell_callback_requires_controller_or_ability_handler")
    return result


def execute_caster_event(tables, c, e, *, evidence=None, stream=None, map_stream=None):
    require_available_skill(c["operator_id"], c["skill"])
    if type(e.get("frame")) is not int or e["frame"] < 0:
        raise ValueError("事件需要非负整数逻辑帧")
    kind = e["kind"]
    if kind=="skill_actions":
        from .caster_decisions import skill_actions
        return skill_actions(tables,c,evidence if evidence is not None else _assets(tables,c),e)
    if kind == "module_trait":
        return module_branch_trait(tables, c, e)
    if kind == "animations":
        from .caster_skill_animations import skill_animation_evidence
        return skill_animation_evidence(tables,c["operator_id"],c["skill"])
    if kind in ("start","skill_timeline"):
        ev = evidence if evidence is not None else _assets(tables,c)
        if kind=="skill_timeline":
            return _skill_timeline(tables,ev,e)
        return _start(tables,ev,{k:v for k,v in e.items() if k!="kind"})
    if kind == "range":
        return skill_range_cells(tables, c, e)
    if kind in ("cast", "flight"):
        ev = evidence if evidence is not None else _assets(tables,c)
        if kind == "flight":
            return projectile_reach(tables, ev, e)
        options = {k:e[k] for k in ("horizon","facing","direction","effective_attack_interval","finish_frame","watched_buff_survives") if k in e}
        return dict(commit_frame=e["frame"], relative_to_commit=run_committed_cast(tables, ev, **options))
    if c["module_info"] and c["module_info"]["isSpecialEquip"]:
        return dict(status="unresolved", reason="special_module_stage_and_part_conditions_required")
    uid = c["operator_id"]
    if kind=="marcil_normal_spell":
        from .marcil_deploy import ordinary_splash
        return ordinary_splash(tables,c,e)
    if kind=="marcil_deck_talent":
        from .marcil_deploy import companion_deck_buff
        return companion_deck_buff(tables,c,e["squad"])
    if kind=="marcil_deploy":
        from .marcil_deploy import run_marcil_deploy
        return run_marcil_deploy(tables,c,e)
    if kind=="ammo_counter":
        from .caster_ammo import run_ammo_counter
        return run_ammo_counter(evidence if evidence is not None else _assets(tables,c),e)
    if kind=="enemy_elements":
        from .enemy_elements import run_enemy_elements
        return run_enemy_elements(tables,c,e)
    if kind=="blaze_ring":
        from .blaze_ring import run_blaze_ring
        return run_blaze_ring(tables,c,e)
    if kind=="ignition_scene":
        from .eyja_ignition import run_ignition
        return run_ignition(tables,c,evidence if evidence is not None else _assets(tables,c),e,stream)
    if kind=="logos_s2_scene":
        from .logos_lock import run_logos_lock
        return run_logos_lock(tables,c,e,stream=stream)
    if uid in CORE_CALLBACKS:
        return execute_core_skill(tables,c,e,stream)
    if uid == "char_4080_lin":
        if c["skill"] == 2 and kind == "share_barrier" and "observations" in e:
            from .lin_shared_barrier import replay_shared_barriers
            return replay_shared_barriers(tables,c,evidence if evidence is not None else _assets(tables,c),e)
        return _lin(tables,c,e,stream)
    if uid == "char_4046_ebnhlz":
        return _eben(tables,c,e)
    if c["name"] in ("夜魔","雪绒","和弦"):
        from .five_star_skills import execute_five_star
        return execute_five_star(tables,c,e)
    if c["name"] in ("蜜蜡","卡涅利安","圣聆初雪"):
        if kind == "phalanx_deployment":
            from .phalanx_deployment import run_phalanx_deployment
            return run_phalanx_deployment(tables,c,e)
        if kind == "phalanx_active_attacks":
            from .phalanx_mode_scene import run_phalanx_active_attacks
            return run_phalanx_active_attacks(tables,c,e)
        if kind in ("beewax_first_impact", "beewax_cycle"):
            from .beewax_scene import run_beewax_cycle, run_beewax_first_impact
            return (run_beewax_first_impact if kind == "beewax_first_impact" else run_beewax_cycle)(tables,c,e)
        from .phalanx_skills import execute_phalanx_event
        return execute_phalanx_event(tables,c,e)
    if c["name"] in ("维伊","澄闪","荒芜拉普兰德","异客"):
        from .caster_drone_chain_mystic import execute_caster_branch
        return execute_caster_branch(tables,c,e,stream=stream,map_stream=map_stream)
    if c["name"] in ("莫斯提马","夕","玛露西尔","伊芙利特","谬因"):
        from .area_caster_skills import execute_area_skill
        return execute_area_skill(tables,c,e)
    from .elemental_casters import execute_elemental_event
    return execute_elemental_event(tables,c,e,stream=stream,map_stream=map_stream)


def _assets(tables, c):
    return caster_prefab_evidence(tables,c["operator_id"],c["skill"],skill_level=c["skill_level"],
                                  module_id=c["module"],module_level=c["module_level"])


def run_caster(tables, request):
    if "squad_state" in request:
        options=[]
        seen=set()
        for entry in request["squad_state"]:
            card=resolve_caster_loadout(tables,**entry["loadout"])
            if not supports_combat_loadout(card) or card["operator_id"] in seen:
                raise ValueError("阵容内每位干员应唯一并使用已接入的配置")
            seen.add(card["operator_id"])
            state=dict(entry["state"],frame=request["frame"],moment=request["moment"],kind="skill_actions")
            options.append(execute_caster_event(tables,card,state))
        return dict(frame=request["frame"],moment=request["moment"],operators=options)
    card = resolve_caster_loadout(tables, **request["loadout"])
    if not supports_combat_loadout(card):
        raise ValueError("这批术师配置需要指定一件常规模组")
    evidence = _assets(tables, card)
    prts = caster_prts_evidence(card["operator_id"], card["skill"], card["module"], card["module_level"])
    from .battle_random import BattleRandom
    stream=BattleRandom(request["battle_random_seed"]) if "battle_random_seed" in request else None
    map_stream=BattleRandom(request["map_random_seed"]) if "map_random_seed" in request else None
    events=[]
    for e in request.get("events",()):
        if (stream or map_stream) and events and e["frame"]<events[-1]["input"]["frame"]:
            raise ValueError("共享随机流要求事件按战斗顺序提供")
        if stream is not None:
            if "seed" in e or "native_seed" in e:
                raise ValueError("共享战斗种子和事件独立种子不能同时填写")
            before=stream.draws
        if map_stream is not None:
            if "map_seed" in e.get("fear_terrain",{}):
                raise ValueError("共享地图种子和事件独立地图种子不能同时填写")
            map_before=map_stream.draws
        row=dict(input=e,result=execute_caster_event(tables,card,e,evidence=evidence,stream=stream,map_stream=map_stream))
        if stream is not None:
            row["stream_draws"]=[before,stream.draws]
        if map_stream is not None:
            row["map_stream_draws"]=[map_before,map_stream.draws]
        events.append(row)
    result = dict(loadout=card, prts=prts, events=events, scope="configuration_and_independent_combat_callbacks",
                  projectile_assets=sorted(evidence["projectiles"]), unresolved_assets=evidence["unresolved"])
    if request.get("include_components"):
        result["prefabs"] = evidence
    if stream is not None:
        result["rng"]=dict(seed=request["battle_random_seed"],draws=stream.draws,
                           scope="specified_events_in_order",type="Torappu.LegacyRandom")
    if map_stream is not None:
        result["map_rng"]=dict(seed=request["map_random_seed"],draws=map_stream.draws,
                               scope="specified_map_events_in_order",type="Torappu.LegacyRandom")
    return result

"""Per-impact and per-release callbacks for chain, drone and mystic casters.

Targets have already passed the caller's normal range, life and eligibility tests.
Each drone owns its target, ramp and explosion state. A native scene stream can
supply confirmed Dice and RandomSelector draws at the corresponding callbacks.
"""
from math import ceil

from .baked_mount import single
from .five_star_skills import _arts, _flag, _integer, _number, _targets
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows, trait_board
from .skill_recovery import _divide
from .volley_damage import _raw_f32

GG, LAPPLAND = "char_377_gdglow", "char_1038_whitw2"
PASSENGER, VEEN = "char_472_pasngr", "char_4226_veen"


def _draw(event):
    x = _number(event, "roll")
    if x >= 1:
        raise ValueError("roll 采用左闭右开区间 [0, 1)")
    return x


def _bounded(event, key, maximum):
    x = _integer(event, key)
    if x > maximum:
        raise ValueError(f"{key} 超过该配置的上限 {maximum}")
    return x


def _target_lock(candidates, event, *, random):
    if not candidates:
        return None
    if random:
        return candidates[_bounded(event, "choice_index", len(candidates)-1)]["id"]
    return candidates[0]["id"]


def _point_distance(row, center):
    return sum((_number(row, k, minimum=-1000) - _number(center, k, minimum=-1000))**2
               for k in ("row", "col"))


def _lappland_weighted_target(rows,slot,stream):
    # AdvancedSelector evaluates its delegate once per live candidate. S1's
    # IsStayStill runs after the weight draw, including for a moving enemy.
    gap=_divide(ONE,1000*ONE)
    buckets=[]
    for row in rows:
        weight=_raw_f32(stream.value())
        if slot==2 or not _flag(row,"moving"):
            buckets.append((_divide(weight,gap)//ONE,row["id"]))
    if not buckets:
        return dict(target=None)
    best=min(b for b,_ in buckets)
    tied=[key for b,key in buckets if b==best]
    if len(tied)>1:
        return dict(status="unresolved",reason="requires_native_entity_order_for_weight_tie",candidates=tied)
    return dict(target=tied[0])


def _drone(uid, slot, kind, event, board, stats, trait, talents, *, stream=None):
    attack = mul(_raw_f32(stats["atk"]), ONE + _raw_f32(board["atk"]))
    if uid == LAPPLAND:
        stage = _bounded(event, "talent_stage", 3)
        bonus = talents[0]["scale"] if stage >= 1 else 1
    else:
        stage, bonus = 0, 1
    if kind == "drone_lock":
        rows = _targets(event)
        if stream is not None and uid==LAPPLAND and slot in (1,2):
            return _lappland_weighted_target(rows,slot,stream)
        if uid == LAPPLAND and slot == 1:
            rows = [r for r in rows if not _flag(r, "moving")]
        if uid == LAPPLAND and slot == 3:
            ranked = sorted((_point_distance(r, event["drone"]), _point_distance(r, event["caster"]), r["id"]) for r in rows)
            if len(ranked) >= 2 and ranked[0][:2] == ranked[1][:2]:
                return dict(status="unresolved", reason="追逐距离双重并列", candidates=[r[2] for r in ranked if r[:2] == ranked[0][:2]])
            return dict(target=ranked[0][2] if ranked else None)
        return dict(target=_target_lock(rows, event, random=uid == LAPPLAND))
    if kind == "drone_profile":
        # S1 Lappland's extra unit is passive; the slot's blackboard only counts
        # additional units beyond this passive. Talent stage 3 adds another one.
        count = 1 + int(board["attack@cnt"]) + int(uid == LAPPLAND and slot == 1) + int(stage >= 3)
        return dict(active_drones=count, source_attack_raw=attack,
                    talent_silence_seconds=talents[0]["attack@silence_duration"] if stage >= 2 else 0)
    if uid == LAPPLAND and kind == "catch" and slot == 3:
        return dict(target=event["target"]["id"],
                    fear_seconds=0 if _flag(event["target"], "character_type") else board["attack@fear"])
    if uid == LAPPLAND and kind == "aura_tick" and slot == 3:
        # Targets use the union of all active drone auras; _targets disallows duplicates.
        hits = [_arts(r, mul(attack, _raw_f32(board["attack@magic_atk_scale"]))) for r in _targets(event)]
        return dict(hits=hits, move_speed_change=board["attack@move_speed"],
                    radius=board["attack@range_radius"], silence_seconds=talents[0]["attack@silence_duration"] if stage >= 2 else 0)
    if kind != "drone_hit":
        raise ValueError("该技能没有这个浮游单元事件")
    target = event["target"]
    cap = trait["max_atk_scale"] * bonus
    last_stack = max(int(trait["max_stack_cnt"]), ceil(round((cap-trait["init_atk_scale"])/trait["delta_atk_scale"], 8)))
    count = _bounded(event, "prior_same_target_hits", last_stack)
    ramp = count if event["previous_target"] == target["id"] else 0
    scale = min(cap, trait["init_atk_scale"] + ramp * trait["delta_atk_scale"])
    if uid == GG:
        stack = _bounded(event, "explosion_stack", int(talents[0]["attack@max_stack_cnt"]))
        if stack < 1:
            raise ValueError("每个浮游单元的自爆概率从第 1 层起算")
        base_prob = talents[0]["attack@prob"]
        explode = stack == talents[0]["attack@max_stack_cnt"] or (
            stream.dice(stack*single(base_prob)) if stream is not None else _draw(event)<stack*base_prob)
        penetration = talents[1]["magic_resist_penetrate_fixed"]
        if explode:
            damage = mul(attack, _raw_f32(talents[0]["attack@atk_scale_2"]))
            hits = [_arts(dict(r, res=max(0, _number(r, "res", minimum=-1000)-penetration)), damage)
                    for r in _targets(event, "explosion_targets")]
            return dict(hits=hits, exploded=True, explosion_stack=1,
                        previous_target=event["previous_target"], prior_same_target_hits=count,
                        return_to_operator=True, sluggish_seconds=board.get("attack@sluggish", 0))
        target = dict(target, res=max(0, _number(target, "res", minimum=-1000)-penetration))
        extra = dict(exploded=False, explosion_stack=stack+1, sluggish_seconds=board.get("attack@sluggish", 0))
    else:
        fear = 0
        if slot == 2 and not _flag(target, "character_type"):
            prob = board["attack@prob"]
            applies = stream.dice(prob) if stream is not None else _draw(event)<prob
            if applies:
                fear = board["attack@fear"]
        extra = dict(fear_seconds=fear, silence_seconds=talents[0]["attack@silence_duration"] if stage >= 2 else 0)
    return dict(hit=_arts(target, mul(attack, _raw_f32(scale))), damage_scale=scale, previous_target=target["id"],
                prior_same_target_hits=min(ramp+1, last_stack), **extra)


def _passenger(slot, kind, event, board, stats, trait, talents, *, stream=None):
    if slot == 3 and kind == "storm_center":
        rows = _targets(event)
        row = max(rows, key=lambda r: _number(r, "hp")) if rows else None
        return dict(target=row["id"] if row else None, center_tile=row["center_tile"] if row else None,
                    duration=board["duration"], interval=board["interval"])
    if slot == 3 and kind == "storm_strike":
        rows=_targets(event)
        if stream is not None:
            picked=stream.select_targets(rows,1)
            return dict(target=picked[0]["id"] if picked else None)
        return dict(target=_target_lock(rows,event,random=True))
    if kind == "mark":
        # Read enemy HP at the native ON_BUFF_START condition callback.
        hp, maximum = _number(event, "hp"), _number(event, "max_hp")
        if not 0 < hp <= maximum:
            raise ValueError("机理分析需要存活目标的有效生命比例")
        return dict(applies=hp/maximum >= talents[0]["hp_ratio"],
                    duration=talents[0]["pasngr_t_1[enhance].duration"])
    if kind != "chain_hit":
        raise ValueError("该技能没有这个链术事件")
    count = int(board.get("attack@max_target", board.get("chain.max_target",
                       board.get("pasngr_s_1.max_target", trait["attack@max_target"]))))
    bounce = _bounded(event, "bounce_index", count-1)
    decay = trait.get("attack@chain.atk_scale", .85)
    outgoing = _raw_f32(board.get("atk_scale", board.get("pasngr_s_1.atk_scale", 1)))
    for _ in range(bounce):
        outgoing = mul(outgoing, _raw_f32(decay))
    atk_bonus = board.get("atk", 0) + (talents[1]["atk"] if _flag(event, "isolated") else 0)
    power = mul(mul(_raw_f32(stats["atk"]), ONE + _raw_f32(atk_bonus)), outgoing)
    damage_scale = talents[0]["pasngr_t_1[enhance].damage_scale"] if _flag(event["target"], "marked") else 1
    duration = board.get("pasngr_s_1.sluggish", trait.get("skill@sluggish", board.get("sluggish", trait["attack@sluggish"])))
    return dict(hit=_arts(event["target"], power, fragile=damage_scale), sluggish_seconds=duration,
                remaining_targets=count-bounce-1, next_search_radius=1.7)


def _veen(slot, kind, event, board, stats, trait, talents):
    merge = int(trait["merge_cnt"])
    if kind in ("store", "volley"):
        units = _bounded(event, "energy_units", int(trait["times"]))
        if kind == "store":
            units = min(units+1, int(trait["times"]))
            return dict(energy_units=units, normal=units%merge, merged=units//merge)
        targets = [r for r in _targets(event) if not _flag(r, "has_veen_dot")]
        if not targets:
            return dict(target=None, energy_units=units)
        result = dict(target=targets[0]["id"], energy_units=0, primary=1, normal=units%merge, merged=units//merge)
        if slot == 3:
            ammo = _bounded(event, "ammo", int(board["attack@trigger_time"]))
            if not ammo:
                raise ValueError("零弹药不能开始下一次三技能攻击")
            result.update(ammo=max(0, ammo-units), bounce_count=units, stop_skill=ammo<=units,
                          normal=0, merged=0)
        return result
    if kind == "projectile_release":
        # Each callback is in actual creation order, so a later projectile in
        # the same volley sees the stacks added by the previous one.
        merged = _flag(event, "has_merged_attack_buff")
        layers = _bounded(event, "s2_stacks", int(board["attack@veen_s_2_buff[stack].max_stack_cnt"])) if slot == 2 else 0
        ptype = event["projectile_kind"]
        if ptype not in ("primary", "normal", "merged") or (slot == 3 and ptype != "primary"):
            raise ValueError("当前技能没有这种直接弹道")
        bonus = talents[0]["atk"] if merged else 0
        bonus += layers * board.get("attack@veen_s_2_buff[stack].atk", 0)
        power = mul(_raw_f32(stats["atk"]), ONE + _raw_f32(bonus))
        gain = merge if ptype == "merged" else 1
        result = dict(cached_atk_raw=power, direct_attack_raw=power * gain, dot_stacks_on_hit=gain)
        if slot == 2:
            result["s2_stacks"] = min(layers+gain, int(board["attack@veen_s_2_buff[stack].max_stack_cnt"]))
        return result
    if slot == 3 and kind == "bounce_hit":
        attack = event["cached_atk_raw"]
        if type(attack) is not int or not 0 <= attack < (1<<62):
            raise ValueError("弹射需要脱手时的 Q32.32 攻击力")
        return dict(hit=_arts(event["target"], mul(attack, _raw_f32(board["attack@bounce_atk_scale"]))),
                    dot_stacks_on_hit=1)
    if kind == "dot_tick":
        layers = _bounded(event, "dot_stacks", int(talents[1]["attack@max_stack_cnt"]))
        return dict(hit=_arts(event["target"], _raw_f32(layers * talents[1]["attack@value"])),
                    duration=talents[1]["attack@duration"])
    raise ValueError("该技能没有这个维伊事件")


def execute_caster_branch(tables, loadout, event, *, stream=None, map_stream=None):
    frame = _integer(event, "frame")
    uid, slot, kind = loadout["operator_id"], loadout["skill"], event["kind"]
    if uid not in (GG, LAPPLAND, PASSENGER, VEEN):
        raise ValueError("未知的分支事件干员")
    if kind=="first_skill_window" and uid in (GG,LAPPLAND,PASSENGER,VEEN):
        from .branch_skill_window import first_branch_skill
        return first_branch_skill(tables,loadout,event)
    if kind=="storm_battle" and uid==PASSENGER:
        from .passenger_storm import run_storm
        return run_storm(tables,loadout,event,stream=stream)
    if kind=="attached_drone_battle" and uid in (GG,LAPPLAND):
        from .attached_drone_battle import run_attached_drones
        return run_attached_drones(tables,loadout,event,stream=stream)
    if kind=="wolf_sortie" and uid==LAPPLAND and slot==3:
        from .whitw_wolves import run_wolf_sortie
        return run_wolf_sortie(tables,loadout,event,stream=stream,map_stream=map_stream)
    if kind=="veen_bolt_battle" and uid==VEEN:
        from .veen_projectile_battle import run_veen_bolt
        return run_veen_bolt(tables,loadout,event)
    if not loadout["panel_attributes"]:
        return dict(status="unresolved", reason="特限模组需要模式规则和效果是否激活")
    unit = tables.read("excel/character_table.json")[uid]
    phase = loadout["module_phase"] or {}
    trait = trait_board(unit, phase, loadout)
    talents = [active_board(talent_rows(phase, i, str(i+1), t["candidates"]), loadout["elite"],
                            loadout["level"], loadout["potential"]) for i, t in enumerate(unit["talents"][:2])]
    board = {b["key"]: b["value"] for b in loadout["skill_row"]["blackboard"]}
    args = (slot, kind, event, board, loadout["panel_attributes"], trait, talents)
    if uid in (GG, LAPPLAND):
        result = _drone(uid, *args,stream=stream)
    elif uid == PASSENGER:
        result = _passenger(*args,stream=stream)
    elif uid == VEEN:
        if kind == "storage_callbacks":
            from .caster_equipment_traits import _trait_values
            from .veen_storage import storage_callbacks
            result = storage_callbacks(int(trait["merge_cnt"]), int(trait["times"]), talents[0],
                                       _trait_values(loadout).get("attack_speed",0), event["callbacks"])
        else:
            result = _veen(*args)
    return dict(frame=frame, kind=kind, operator_id=uid, skill_id=loadout["skill_id"],
                module_id=loadout["module"], module_level=loadout["module_level"], result=result)

"""Evaluate the selected caster module's branch trait at its own battle callback."""
from math import isfinite

from .caster_modules import splash_range_module
from .fixed import ONE,mul
from .module_effects import active_board, module_stat
from .skill_recovery import _divide
from .volley_damage import _raw_f32


def _number(event, key, *, integer=False):
    value = event[key]
    types = (int,) if integer else (int, float)
    if type(value) not in types or not isfinite(value) or value < 0:
        raise ValueError(f"{key} 需要非负有效数值")
    return value


def _flag(event, key):
    if type(event[key]) is not bool:
        raise ValueError(f"{key} 需要布尔值")
    return event[key]


def _trait_values(card):
    phase = card["module_phase"]
    if phase is None:
        return {}
    args = card["elite"], card["level"], card["potential"]
    values = {}
    for part in phase["parts"]:
        if part["isToken"]:
            continue
        if part["target"] in ("TRAIT", "TRAIT_DATA_ONLY"):
            values.update(active_board(part["overrideTraitDataBundle"]["candidates"] or (), *args))
        if part["target"] == "TALENT":
            rows = [r for r in part["addOrOverrideTalentDataBundle"]["candidates"] or ()
                    if r["prefabKey"] in ("10", "10_root")]
            values.update(active_board(rows, *args))
    return values


def blast_distance_factor(board,distance):
    """BLA-X changes the outgoing damage value using the receiver's current map position."""
    low,high = _raw_f32(board["min_dist"]),_raw_f32(board["max_dist"])
    measured = max(0,min(_raw_f32(distance),high)-low)
    return ONE+_divide(mul(_raw_f32(board["damage_scale"]),measured),high-low)


def module_branch_trait(tables, card, event):
    """Only the branch trait, in addition to separate phase stats and operator talent overrides."""
    if card["module_info"] is None:
        return dict(scope="selected_branch_trait",module_id=None,level=None)
    icon = card["module_info"]["typeIcon"]
    board = _trait_values(card)
    if icon == "ccr-x":
        result = dict(fixed_resistance_penetration=board["magic_resist_penetrate_fixed"])
    elif icon == "ccr-y":
        level = event["target_level"]
        if level not in ("NORMAL", "ELITE", "BOSS"):
            raise ValueError("目标等级无效")
        qualifies = _flag(event, "normal_attack_hit") and level in ("ELITE", "BOSS")
        result = dict(uncapped_sp_credit=board["sp"] if qualifies and not _flag(event,"sp_blocked") else 0)
    elif icon in ("msc-d", "ccr-d", "bla-d"):
        result = dict(element="burn" if icon=="bla-d" else "necrosis",
                      buildup_request=_number(event, "arts_damage") * board["ep_damage_ratio"])
    elif icon == "msc-x":
        result = dict(ordinary_storage_capacity=int(board["times"]))
    elif icon == "msc-y":
        result = dict(attack_speed_add=board["attack_speed"] if _number(event,"charges_at_module_poll",integer=True) else 0)
    elif icon == "plx-y":
        count = min(_number(event,"eligible_aura_members",integer=True), int(board["max_valid_stack_cnt"]))
        result = dict(damage_factor=1+count*board["damage_scale"], stacks=count)
    elif icon == "plx-x":
        if _flag(event,"active_trait_mode"):
            defense, = (v for k,v in board.items() if k.endswith("[buff].def"))
            resistance, = (v for k,v in board.items() if k.endswith("[buff].magic_resistance"))
        else:
            defense, resistance = board["def"], board["magic_resistance"]
        result = dict(def_add_ratio=defense, res_add=resistance)
    elif icon in ("fun-x", "fun-y"):
        stacks = min(_number(event,"prior_hits_on_same_target",integer=True), int(board["max_stack_cnt"]))
        result = dict(drone_atk_scale=min(board["max_atk_scale"], board["init_atk_scale"]+stacks*board["delta_atk_scale"]))
    elif icon in ("cha-x", "cha-y"):
        jump = _number(event,"jump_index",integer=True)
        result = dict(ordinary_scale_factor=board["attack@chain.atk_scale"]**jump,
                      ordinary_max_targets=int(board["attack@max_target"]), ordinary_sluggish_seconds=board["attack@sluggish"])
    elif icon == "spc-x":
        result = dict(default_range_id=splash_range_module(tables,card)["range_id"])
    elif icon in ("bla-y", "spc-y"):
        result = dict(cost_delta=module_stat(card["module_phase"],"cost"), already_in_panel=True)
    elif icon == "bla-x":
        distance = _number(event,"distance_tiles")
        result = dict(damage_factor=blast_distance_factor(board,distance)/ONE)
    elif icon == "pri-x":
        result = dict(damage_factor=board["damage_scale"] if _flag(event,"in_elemental_burst") else 1)
    elif icon == "pri-y":
        result = dict(natural_sp_rate_add=board["sp_recovery_per_sec"] if _flag(event,"burst_enemy_in_range") else 0)
    elif icon == "soc-x":
        blocked = _flag(event,"blocked_by_own_summon")
        result = dict(add_to_attack_candidates=blocked, atk_factor=board["atk_scale"] if blocked else 1)
    else:
        raise ValueError(f"未知模组特性 {icon}")
    return dict(scope="selected_branch_trait", module_id=card["module"], level=card["module_level"], **result)

"""Translate the S3 RangedAttackWithExtraActions graph into ordered receiver calls."""
import json

from .fixed import mul
from .volley_damage import _raw_f32


def s3_damage_scales(ability, board):
    branch, = json.loads(ability["_actions"]["SerializedState"])
    dark, normal = branch["_succeedNodes"], branch["_failNodes"]
    def node_type(node):
        return node["$type"].split(",")[0].split("+")[-1]
    if (node_type(branch) != "IfElse" or node_type(branch["_conditionNode"]) != "FilterEPBreakRecoveryType"
            or branch["_conditionNode"]["_recoveryType"] != "DARK"
            or [node_type(n) for n in dark] != ["AdvancedApplyDamage", "AlwaysNext", "AdvancedApplyDamage", "CreateEffect"]
            or [node_type(n) for n in normal] != ["AdvancedApplyDamage"]
            or [(n["_damageType"], n["_attackType"]) for n in (normal[0], dark[0], dark[2])]
                != [("MAGICAL", "NORMAL"), ("MAGICAL", "NORMAL"), ("ELEMENT", "BUFF")]):
        raise ValueError("妮芙三技能的凋亡判定或伤害动作次序变化")
    return tuple(_raw_f32(board.get("attack@" + n["_atkScaleVar"], n["_defaultAtkScale"]))
                 for n in (normal[0], dark[0], dark[2]))


def apply_s3_hit(state, frame, attack, resistance, output_scale, scales):
    normal, dark_magic, dark_element = scales
    # The outer condition is tested once for this receiver. AlwaysNext lets the
    # elemental action execute even when the zero-scale magical action deals zero.
    dark = state.burst == "DARK"
    magic = state.receive_ordinary(frame, source="nymph_s3", damage_type="arts",
                                   attack_q32=mul(attack, dark_magic if dark else normal),
                                   mitigation_q32=resistance, output_scale_q32=output_scale)
    element = None
    if dark:
        element = state.apply_elemental_hp(frame, mul(attack, dark_element), source="nymph_s3",
                                           output_scale_q32=output_scale)
    return magic, element

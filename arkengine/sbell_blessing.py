"""Resolve 圣山的祝福 at damage and HP-zero callbacks using V079's attached buff graphs."""
from .fixed import mul
from .phalanx_skills import SNOW, _talent
from .volley_damage import _raw_f32


def run_blessing(tables, card, event):
    if card["operator_id"] != SNOW:
        raise ValueError("圣山的祝福仅属于圣聆初雪")
    graph = tables.read("battle/buff_template_data.json")
    buffs = tables.read("buff_table.json")
    talent = _talent(card, 1, "2")
    cold = graph["sbell2_t2[hit_cold]"]["eventToActions"]["ON_TAKE_DAMAGE"]
    escape = graph["sbell2_t2[escape_dead]"]["eventToActions"]
    consume, = escape["ON_BEFORE_TRY_SET_HP_ZERO"]
    check, heal, area, self_freeze, finish = escape["ON_POST_TRY_SET_HP_ZERO"]
    heal_node = graph[heal["_buff"]["templateKey"]]["eventToActions"]["ON_BUFF_FINISH"][0]
    if (consume["_blockThisHpSet"] or not consume["_dontConsumeWhenUndeadable"]
            or not check["$type"].split(",")[0].endswith("+IsConsumerOfTrySetHpZeroModifier")
            or not finish["$type"].split(",")[0].endswith("+FinishBuff")
            or not heal_node["_ignoreHealFree"] or heal_node["_skipModifierEvent"]
            or cold[2]["_sideMask"] != "ENEMY" or cold[3]["_buffOwner"] != "MODIFIER_SOURCE"
            or not area["_useAttackRange"] or area["_targetOptions"]["targetSide"] != "ENEMY"):
        raise ValueError("圣山的祝福受击/濒死回调配置变化")
    available = event["blessing_available"]
    max_hp, hp = event["max_hp_q32"], event["hp_q32"]
    if type(available) is not bool or type(hp) is not int or type(max_hp) is not int or not 0 <= hp <= max_hp or max_hp <= 0:
        raise ValueError("圣山的祝福需要有效生命和一次性保命状态")
    callback = event["callback"]
    applications = []

    def apply(key, receiver):
        row = buffs[key]
        applications.append(dict(target=receiver, buff=key, duration_seconds=talent[row["durationKey"]],
                                 status_resistable=row["statusResistable"]))

    heal_amount = 0
    if callback == "ON_TAKE_DAMAGE":
        source = event.get("damage_source")
        if source and source["side"] == "ENEMY" and not set(cold[0]["_abnormalFlags"]).intersection(source["flags"]):
            apply(cold[3]["_buffKey"], source["id"])
    elif callback == "TRY_SET_HP_ZERO":
        # Inputs are states at the modifier, after other earlier consumers ran.
        if type(event["undeadable"]) is not bool or type(event["modifier_cancelled"]) is not bool:
            raise ValueError("濒死仲裁需要明确的不死与修改器取消状态")
        if available and not event["modifier_cancelled"] and not event["undeadable"]:
            available = False
            heal_amount = mul(max_hp, _raw_f32(talent["hp_ratio"]))
            hp = min(max_hp, hp + heal_amount)
            # The caller supplies the resolved native range selector's receivers.
            enemy_buff, = area["_buffs"]
            for receiver in event["selected_enemy_ids"]:
                apply(enemy_buff["buffKey"], receiver)
            apply(self_freeze["_buff"]["buffKey"], SNOW)
    else:
        raise ValueError("需要实际的受伤或设置零生命回调")
    return dict(blessing_available=available, hp_after_q32=hp, heal_amount_q32=heal_amount,
                consumed_hp_zero=bool(heal_amount), buff_applications=applications,
                scope="native_callbacks_without_external_heal_modifiers")

"""BLA-Δ's passive attachment precedes the attack, and magical output then feeds FIRE."""
from .damage import HP_EPSILON
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .module_effects import active_board
from .volley_damage import _raw_f32


def scorched_earth_debuff(evidence):
    ability,=(c["fields"] for c in evidence["skill"]["components"] if c["script"]=="ChannelingMeleeAttack")
    buff,=ability["_activeBuffs"]
    attr,=buff["attributes"]["attributeModifiers"]
    board={r["key"]:r["value"] for r in evidence["skill_blackboard"]}
    if ((buff["buffKey"],buff["templateKey"],buff["lifeTimeType"],buff["durationKey"])!=("ifrit_s_3","empty",1,"#duration")
            or buff["maxStackCnt"]!=1 or not buff["independentCharacterSource"] or buff["disableOverride"]
            or (attr["attributeType"],attr["formulaItem"],attr["loadFromBlackboard"])!=(3,0,1)):
        raise ValueError("灼地的受击减抗、同源覆盖或计时模板发生变化")
    return _raw_f32(board["magic_resistance"]),_raw_f32(board.get(buff["durationKey"],buff["lifeTime"]))


def ifrit_x_pulse(receiver,card,debuff,frame,owner,attack_q32,base_resistance,output_scale,resistance_scale):
    if receiver.hp<=HP_EPSILON:
        return None
    receiver.apply_scorched_earth(frame,owner,debuff)
    scale=next(r["value"] for r in card["skill_row"]["blackboard"] if r["key"]=="atk_scale")
    packet=receiver.receive_ordinary(frame,source=owner,source_card=card,damage_type="arts",
        attack_q32=mul(attack_q32,_raw_f32(scale)),mitigation_q32=base_resistance+receiver.res_add,
        output_scale_q32=output_scale,resistance_scale_q32=resistance_scale)
    packet["kind"]="arts_hp"
    return packet


def ifrit_delta_rules(card,evidence):
    if card["module_info"]["typeIcon"]!="bla-d":
        raise ValueError("灼燃损伤分支需要伊芙利特 BLA-Δ")
    rows=[r for p in card["module_phase"]["parts"] if p["target"]=="TALENT"
          for r in (p["addOrOverrideTalentDataBundle"]["candidates"] or ()) if r["prefabKey"]=="10"]
    scale=0
    if rows:
        board=active_board(rows,card["elite"],card["level"],card["potential"])
        aux,=(c for part in evidence["equipment"].values() for c in part["components"]
              if c["script"]=="PassiveAttachmentAbility" and c["enabled"])
        extra,=aux["fields"]["_additiveActiveBuffs"]
        branch,=evidence["templates"][extra["templateKey"]]["eventToActions"]["ON_BUFF_START"]
        action,=branch["_succeedNodes"]
        if (aux["fields"]["_targetFamilyMask"]!=31 or branch["_conditionNode"]["_elementType"]!="FIRE" or
                action["_damageType"]!="ELEMENT" or action["_attackType"]!="BUFF" or action["_baseOnHostAtk"]):
            raise ValueError("附加元素伤害的能力目标、爆发类型或倍率来源改变")
        scale=_raw_f32(board[action["_atkScaleVar"]])
    skill={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    factors={"ordinary_attack":_raw_f32(1)}
    if card["skill"] in (2,3):
        factors["attack_hit" if card["skill"]==2 else "channel_hit"]=_raw_f32(skill["atk_scale"])
    if card["skill"]==2:
        ability,=(c for c in evidence["skill"]["components"] if c["script"]=="MeleeAttack")
        buff,=ability["fields"]["_activeBuffs"]
        damage,=evidence["templates"][buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        factors["burn_tick"]=_raw_f32(skill[damage["_atkScaleVar"]])
    return scale,factors,scorched_earth_debuff(evidence) if card["skill"]==3 else None


def ifrit_delta_hit(receiver,card,rules,frame,source,attack_q32,arts_resistance_before_fire_q32,*,resistance_scale_q32=ONE,
                    owner=None):
    """Apply flat FIRE resistance before any supplied final aura factor."""
    scale,factors,debuff=rules
    if source not in factors:
        raise ValueError("该次法伤来源与所选技能不符")
    if receiver.hp<=HP_EPSILON:
        return None
    if source!="burn_tick" and receiver.burst=="FIRE" and scale:
        receiver.apply_elemental_hp(frame,mul(attack_q32,scale),source="ifrit_e_003_ele")
        if receiver.hp<=HP_EPSILON:
            return None
    if source=="channel_hit":
        receiver.apply_scorched_earth(frame,owner or card["operator_id"],debuff)
    effective_resistance=arts_resistance_before_fire_q32+receiver.res_add
    return receiver.hit_with_module_arts(frame,card,attack_q32=mul(attack_q32,factors[source]),
        effective_arts_resistance_q32=effective_resistance,resistance_scale_q32=resistance_scale_q32)


def run_ifrit_delta(tables,card,evidence,event):
    start,end=event["frame"],event["end_frame"]
    if type(end) is not int or end<start:
        raise ValueError("结束帧需要晚于开始帧")
    rules=ifrit_delta_rules(card,evidence)
    receiver=EnemyElements(tables,**event["enemy"])
    by_frame={}
    for hit in event["hits"]:
        if type(hit["frame"]) is not int or not start<=hit["frame"]<=end:
            raise ValueError("受击帧超出区间")
        by_frame.setdefault(hit["frame"],[]).append(hit)
    for frame in range(start,end+1):
        receiver.tick(frame)
        for hit in by_frame.get(frame,()):
            if hit["source"]=="prior_buildup":
                receiver.apply_buildup(frame,hit["element"],hit["amount_q32"])
            else:
                ifrit_delta_hit(receiver,card,rules,frame,hit["source"],hit["actual_attack_q32"],
                                hit["arts_resistance_before_fire_q32"])
    return dict(events=receiver.events,receiver=receiver.snapshot(),
                conditions=["receiver_object_tick_before_hits","accepted_damage_packets_in_list_order"])

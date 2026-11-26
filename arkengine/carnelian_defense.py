"""S1 defenses at a confirmed mode snapshot and HP settlement of an accepted ordinary hit."""
from .caster_prefabs import caster_prefab_evidence
from .damage import ordinary_arts_hit,ordinary_physical_hit
from .fixed import ONE,mul
from .module_effects import trait_board
from .phalanx_skills import CARN
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


def s1_defense(tables,card,*,active,charged,mode_age=0):
    if card["operator_id"]!=CARN or card["skill"]!=1 or type(charged) is not bool or type(active) is not bool:
        raise ValueError("需要卡涅利安一技能及确定的待机或蓄力状态")
    if type(mode_age) is not int or mode_age<0:
        raise ValueError("模式切入后的完整敌我对象更新次数不能为负数")
    unit=tables.read("excel/character_table.json")[CARN]
    trait=trait_board(unit,card["module_phase"],card)
    panel=card["panel_attributes"]
    board={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    ev=caster_prefab_evidence(tables,CARN,1,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    skill,=(b for r in ev["skill"]["components"] if r["script"]=="ActiveBuffAbility" for b in r["fields"]["_buffs"] if b["buffKey"]=="billro_s_1")
    overrides=[b for r in ev["character"]["components"] if r["script"]=="PassiveBuffAbility" and r["path"].startswith("Modes/S1/")
               for b in r["fields"]["_buffs"] if b["overrideKey"]=="billro_trait" and not b["attributes"]["attributeModifiers"]]
    if (len(overrides)!=1 or any(b["overrideKey"]=="billro_trait" for r in ev["character"]["components"]
            if r["script"]=="PassiveBuffAbility" and r["path"].startswith("Modes/S1_Enhance/") for b in r["fields"]["_buffs"]) or
            not any((m["attributeType"],m["formulaItem"],m["loadFromBlackboard"])==(2,1,1) for m in skill["attributes"]["attributeModifiers"])):
        raise ValueError("沙暴守卫的常态特性覆写或防御乘区发生变化")
    mods=[(trait["def"],trait["magic_resistance"])] if not active or charged else [(0,0)]
    if active and not charged and card["module_info"] and card["module_info"]["typeIcon"]=="plx-x":
        poll,=(b for obj in ev["equipment"].values() for r in obj["components"] if r["script"]=="PassiveBuffAbility"
               for b in r["fields"]["_buffs"] if b["templateKey"]=="billro_e_002[half_attribute]")
        graph,=tables.read("battle/buff_template_data.json")[poll["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
        after=graph["_succeedNodes"][0]["_failNodes"][0]["_buff"]
        if (graph["_conditionNode"]["_buffKeys"]!=[overrides[0]["buffKey"]] or after["buffKey"]!="billro_e_002[buff]" or
                {m["attributeType"] for m in after["attributes"]["attributeModifiers"]}!={"DEF","MAGIC_RESISTANCE"} or
                not poll["waitFirstTriggerInterval"] or poll["firstTriggerInterval"]!=-1 or poll["triggerInterval"]<=0):
            raise ValueError("X 模组的技能形态双抗轮询发生变化")
        half=trait[after["buffKey"]+".def"],trait[after["buffKey"]+".magic_resistance"]
        elapsed=mode_age*_raw_f32(tables.fixed_timestep())
        if elapsed+READY_EPSILON>=_raw_f32(poll["triggerInterval"]):
            mods=[half]
        elif mode_age:
            mods.append(half)
    max_ratio=trait.get("billro_e_002[max_hp_enhance_buff].max_hp" if charged else "billro_e_002[max_hp_buff].max_hp",0) if active else 0
    cap=mul(_raw_f32(panel["maxHp"]),ONE+_raw_f32(max_ratio))
    defense_skill=_raw_f32(board["def"]) if active else 0
    return [dict(defense_q32=mul(_raw_f32(panel["def"]),ONE+defense_skill+_raw_f32(bonus)),
                 resistance_q32=_raw_f32(panel["magicResistance"])+_raw_f32(arts),max_hp_q32=cap) for bonus,arts in mods]


def settle_s1_received_hit(tables,card,event):
    mode=event.get("mode_frame")
    if mode is not None and (type(mode) is not int or not 0<=mode<=event["frame"]):
        raise ValueError("活跃技能快照需要已经实际切入的模式帧")
    if mode is not None and (event["frame"]-mode)*_raw_f32(tables.fixed_timestep())+READY_EPSILON>_raw_f32(card["skill_row"]["duration"]):
        raise ValueError("该次一技能持续期已结束，需要本次受击时的待机或新开技快照")
    if mode is None and event.get("charged",False):
        raise ValueError("满蓄力技能形态需要已经切入的模式帧")
    defenses=s1_defense(tables,card,active=mode is not None,charged=event.get("charged",False),mode_age=event["frame"]-mode if mode is not None else 0)
    kind=event["damage_type"]
    hp=event["hp_before_q32"]
    if kind not in ("PHYSICAL","MAGICAL") or type(hp) is not int or not 0<hp<=defenses[0]["max_hp_q32"]:
        raise ValueError("需使用普通物理或法术伤害包以及实际受击前的存活血量")
    hits=[]
    for defense in defenses:
        args=dict(attack_raw=event["attack_q32"],hp_raw=hp)
        hit=(ordinary_physical_hit(**args,defense_raw=defense["defense_q32"]) if kind=="PHYSICAL" else
             ordinary_arts_hit(**args,resistance_raw=defense["resistance_q32"]))
        hits.append(dict(**defense,damage_q32=hit.damage_raw,hp_after_q32=hit.hp_after_raw,alive=hit.alive_after))
    return dict(status="resolved" if len({v["damage_q32"] for v in hits})==1 else "requires_module_poll_phase",
                scope="accepted_ordinary_incoming_hit_only",outcomes=hits)

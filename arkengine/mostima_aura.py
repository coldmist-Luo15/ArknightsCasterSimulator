"""Mostima's global spRecover buff and its effect on an active natural SP controller."""
from .caster_prefabs import _prefab
from .fixed import ONE
from .module_effects import active_board, equipped_phase, talent_rows
from .skill_recovery import RecoveryTimer, _divide
from .volley_damage import _raw_f32


MOSTIMA = "char_213_mostma"


def aura_profile(tables, card):
    if card["operator_id"] != MOSTIMA:
        raise ValueError("光环来源需要是莫斯提马")
    cache = ("mostima_sp_aura",card["module"],card["module_level"],card["elite"],card["level"],card["potential"])
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    phase = equipped_phase(tables,card)
    actor = tables.read("excel/character_table.json")[MOSTIMA]
    rows = talent_rows(phase,0,"1",actor["talents"][0]["candidates"])
    board = active_board(rows,card["elite"],card["level"],card["potential"])
    enhanced = "mostma_003_t[self].sp_recovery_per_sec" in board
    if enhanced:
        part, = [p for p in phase["parts"] if p["target"]=="TALENT" and any(
            r["talentIndex"]==0 and r["prefabKey"]=="1" for r in p["addOrOverrideTalentDataBundle"]["candidates"] or [])]
        asset = _prefab(tables,"[uc]equips",part["resKey"])
        path = ""
    else:
        asset = _prefab(tables,"characters",MOSTIMA)
        path = "Others/Direction/Talents/1"
    comps = [c for c in asset["components"] if c["enabled"] and c["active"] and c["path"]==path]
    aura, = [c["fields"] for c in comps if c["script"]=="GlobalAuraAbility"]
    validator, = [c["fields"]["_targetOptions"] for c in comps if c["id"]==aura["_targetValidator"]["m_PathID"]]
    buff = tables.read("buff_table.json")["spRecover"]
    modifier, = buff["attributes"]["attributeModifiers"]
    if (not aura["_removeBuffWhenAbilityDetached"] or aura["_onlyDetactTargetWhenStarted"] or aura["_interval"]
            or (validator["targetSide"],validator["professionMask"]) != (1,32)
            or [b["buffKey"] for b in aura["_buffs"]] != ["spRecover"]
            or (buff["maxStackCnt"],buff["priorityBBKeys"]) != (1,["sp_recovery_per_sec"])
            or (modifier["attributeType"],modifier["formulaItem"]) != ("SP_RECOVERY_PER_SEC","ADDITION")):
        raise ValueError("术师光环筛选或技力属性和已核对配置不符")
    if enhanced:
        passive, = aura["_passiveBuffs"]
        action, = tables.read("battle/buff_template_data.json")[passive["buffKey"]]["eventToActions"]["ON_BUFF_START"]
        if passive["buffKey"]!="mostma_003_t[self]" or action["_buff"]["buffKey"]!="spRecover":
            raise ValueError("莫斯提马自身光环引用不符")
    ally = board.get("sp_recovery_per_sec",0)
    result = dict(ally=ally,owner=board.get("mostma_003_t[self].sp_recovery_per_sec",ally),source=asset["source"])
    tables._prefab_data[cache]=result
    return result


def effective_period(profile, recipient, profession, active, other_same_group=0, *, base_rate=1):
    bonus = profile["owner" if recipient==MOSTIMA else "ally"] if active and profession=="CASTER" else 0
    # spRecover uses the largest priority blackboard value; other stacking groups
    # would be separate attributes of the recipient.
    strongest = max(bonus,other_same_group)
    rate = _raw_f32(base_rate) + _raw_f32(strongest)
    return _divide(ONE,rate) if rate>0 else None


def set_period(gauge, period, *, stopped):
    if (gauge.timer.period if gauge.timer is not None else None) == period:
        return
    # SpController.UpdateSpRecoveryPerSec calls OnTick(0) before resetting a valid timer.
    if gauge.timer is not None:
        gauge.advance(0,stopped=stopped)
    if period is None:
        gauge.timer = None
    elif gauge.timer is None:
        gauge.timer = RecoveryTimer(period)
    else:
        gauge.timer.change_period(period)


def apply_to_live_team(tables, caster, recoveries, frame, *, active):
    profile = aura_profile(tables,caster)
    units = tables.read("excel/character_table.json")
    for uid,replay in recoveries.items():
        if replay is None or replay.finished:
            continue
        period = effective_period(profile,uid,units[uid]["profession"],active,
                                  base_rate=replay.report.get("base_sp_recovery_per_sec",1))
        if (replay.state.timer.period if replay.state.timer is not None else None)==period:
            continue
        set_period(replay.state,period,stopped=replay.recovery_stopped)
        r = replay.report
        r.setdefault("period_changes",[]).append(dict(frame=frame,phase="INPUT",period_q32=period,source=MOSTIMA))
        if "base_rate_without_modifiers_or_external_sp" in r["conditions"]:
            r["conditions"].remove("base_rate_without_modifiers_or_external_sp")
        if "mostima_spRecover_and_listed_external_sp" not in r["conditions"]:
            r["conditions"].append("mostima_spRecover_and_listed_external_sp")

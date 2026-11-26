"""Qanipalaat's collision listener and receiver-side Arts Fragility buff."""
from .caster_prefabs import caster_prefab_evidence
from .five_star_skills import QANIK
from .fixed import ONE
from .module_effects import active_board, talent_rows
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def fragility_profile(tables, card, evidence=None):
    if card["operator_id"] != QANIK:
        raise ValueError("冰原生存需要绑定雪绒的实际装载")
    if evidence is None:
        evidence = caster_prefab_evidence(tables,QANIK,card["skill"],skill_level=card["skill_level"],
                                         module_id=card["module"],module_level=card["module_level"])
    parts = evidence["character"]["components"]
    aura, = [c for c in parts if c["script"] == "AuraAbility" and c["path"].endswith("/Talents/1")]
    fields = aura["fields"]
    buff, = fields["_buffs"]
    target, = [c["fields"]["_targetOptions"] for c in parts if c["id"] == fields["_targetValidator"]["m_PathID"]]
    box, = [c["fields"] for c in parts if c["path"] == aura["path"] and c["script"] == "AutoLoadBoxRange"]
    template = tables.read("battle/buff_template_data.json")
    check, = template[buff["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    motion = check["_conditionNode"]
    exists, = check["_succeedNodes"]
    weakness, mark = exists["_failNodes"]
    finish_weakness, finish_mark = check["_failNodes"]
    child = tables.read("buff_table.json")[weakness["_buffKey"]]
    scale, = template[child["templateKey"]]["eventToActions"]["ON_TAKE_DAMAGE"]
    if ((target["targetSide"],target["targetMotion"],target["targetCategory"]) != (2,2,1)
            or target["ignoreTargetFree"] or target["ignoreMotionMode"] or fields["_onlyAddBuffOnceForEachTarget"]
            or not fields["_removeBuffWhenTargetLeave"] or not fields["_removeBuffWhenAbilityDetached"]
            or box["_useBlackboardRangeId"] or box["_extendable"]
            or buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"] != 0
            or buff["buffKey"] != "qanik_t_1" or buff["triggerLifeType"] != 2
            or (motion["_targetType"],motion["_motionMask"],motion["_checkTargetFree"]) != ("BUFF_OWNER","FLY_ONLY",False)
            or not all(op["_isDerivedBuff"] and op["_finishDerivedBuffIfParentFinish"] for op in (weakness,mark))
            or exists["_conditionNode"]["_derviedBuffKey"] != mark["_buff"]["buffKey"]
            or finish_mark["_buffKey"] != mark["_buff"]["buffKey"]
            or finish_weakness["_buffKey"] != child["buffKey"] or not finish_weakness["_updateOverrideMap"]
            or child["overrideKey"] != "weak[magic][bonus]" or child["priorityBBKeys"] != ["damage_scale"]
            or not scale["_filterDamageType"] or scale["_damageMask"] != "MAGICAL" or scale["_isStackable"]):
        raise ValueError("冰原生存的目标验证、刷新或法术脆弱动作变化")
    unit = tables.read("excel/character_table.json")[QANIK]
    ranks = talent_rows(card["module_phase"] or {},0,"1",unit["talents"][0]["candidates"])
    board = active_board(ranks,card["elite"],card["level"],card["potential"])
    return _raw_f32(buff["triggerInterval"]),_raw_f32(board.get("damage_scale",1))


class QanikAura:
    """One collision pair. An admitted parent survives motion changes until collision exit."""
    def __init__(self, profile):
        self.period,self.scale = profile
        self.touching = False
        self.timer = None
        self.weak = False

    @property
    def multiplier(self):
        return self.scale if self.weak else ONE

    def contact(self, touching, airborne, target_free=False):
        self.touching = touching
        if not touching:
            self.timer = None
            self.weak = False
        else:
            self.retry(airborne,target_free)

    def retry(self, airborne, target_free=False):
        if self.touching and self.timer is None and airborne and not target_free and self.scale > ONE:
            self.timer = RecoveryTimer(self.period)
            self.timer.remaining = 0

    def tick(self, step, airborne):
        if self.timer is not None and self.timer.advance(step):
            self.weak = airborne

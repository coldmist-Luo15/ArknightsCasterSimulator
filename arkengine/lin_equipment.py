"""Lin's branch-specific trait effects from the selected phase and V079 buff graphs."""
from .fixed import ONE,mul
from .module_effects import active_board, selected_branch, talent_rows, trait_board
from .opening_scene import bundle_environment
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def lin_defense_profile(tables, unit, phase, card):
    board = trait_board(unit, phase, card)
    slot = card["skill"]
    idle = tables._component_fields("characters", card["operator_id"], ("_buffs",),
                                    "lin_base_trait", children=("Trait",))
    active = tables._component_fields("characters", card["operator_id"], ("_buffs",), f"lin_s{slot}_trait",
                                      children=("Modes", f"S{slot}", "Body", "GeneralAbilities", "Trait_override"))
    source, = idle["_buffs"]
    override, = active["_buffs"]
    if (source["templateKey"] != "empty" or override["overrideKey"] != source["buffKey"]
            or override["attributes"]["attributeModifiers"]):
        raise ValueError("林的技能态双抗覆写与预制体不符")
    idle_stats = board["def"], board["magic_resistance"]
    if selected_branch(tables, card) != "plx-x":
        return idle_stats, (0, 0), 0
    part, = (r for r in phase["parts"] if not r["isToken"] and r["target"]=="TRAIT")
    cache = ("lin_physical_trait",part["resKey"])
    if cache not in tables._prefab_data:
        raw, source = tables.verified_bundle("battle/prefabs/[uc]equips.ab")
        node = bundle_environment(raw).container[
            f"dyn/battle/prefabs/[uc]equips/{part['resKey']}.prefab"].deref()
        components = [node.assets_file.objects[r["component"]["m_PathID"]].read_typetree()
                      for r in node.read_typetree()["m_Component"]]
        buffs, = [r["_buffs"] for r in components if "_buffs" in r]
        tables._prefab_data[cache] = {b["buffKey"]:b for b in buffs},source
    buffs,source = tables._prefab_data[cache]
    tables.source["lin_defense_module"] = source
    buff, poll = buffs["lin_trait"], buffs["lin_trait[uniequip]"]
    modifiers = {(r["attributeType"],r["formulaItem"],r["loadFromBlackboard"],r["fetchBaseValueFromSourceEntity"])
                 for r in buff["attributes"]["attributeModifiers"]}
    if (buff["templateKey"]!="empty" or buff["overrideKey"]!=override["overrideKey"]
            or modifiers!={(2,1,1,0),(3,0,1,0)} or poll["templateKey"]!="lin_e_002[half_attribute]"
            or poll["triggerLifeType"]!=2 or poll["triggerInterval"]<=0 or not poll["waitFirstTriggerInterval"]
            or poll["firstTriggerInterval"]!=-1 or poll["overrideKey"]):
        raise ValueError("林 X 模组双抗预制体不符")
    trigger, = tables.read("battle/buff_template_data.json")[poll["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    add, = trigger["_succeedNodes"]
    remove, = trigger["_failNodes"]
    create, = add["_failNodes"]
    finish, = remove["_succeedNodes"]
    half = create["_buff"]
    half_key = half["buffKey"]
    modifiers = {(r["attributeType"],r["formulaItem"],r["loadFromBlackboard"],r["fetchBaseValueFromSourceEntity"])
                 for r in half["attributes"]["attributeModifiers"]}
    checks = [trigger["_conditionNode"],add["_conditionNode"],remove["_conditionNode"]]
    if ([r["_buffKeys"] for r in checks]!=[[override["buffKey"]],[half_key],[half_key]]
            or any(r["_targetType"]!="BUFF_OWNER" or r["_loadFromBlackboard"] or r["_checkBuffSource"] for r in checks)
            or create["_buffOwner"]!="BUFF_OWNER" or create["_isDerivedBuff"]
            or half["lifeTimeType"]!="INFINITY" or finish["_buffKey"]!=half_key
            or finish["_targetType"]!="BUFF_OWNER" or finish["_decCntIfStack"]
            or modifiers!={("DEF","MULTIPLIER",True,False),("MAGIC_RESISTANCE","ADDITION",True,False)}):
        raise ValueError("林 X 模组的切态双抗回调发生变化")
    return idle_stats, (board[f"{half_key}.def"],board[f"{half_key}.magic_resistance"]), poll["triggerInterval"]


def lin_defense_trait(tables, unit, phase, card):
    # Archived opening cards are S3-only and carry the skill ID without the slot.
    idle, half, interval = lin_defense_profile(tables, unit, phase, {"skill":3, **card})
    return idle[0], half[0], interval


class LinDefenseClock:
    """Independent V079 half-trait poll and the immediate mode override."""
    def __init__(self,tables,card):
        unit=tables.read("excel/character_table.json")[card["operator_id"]]
        idle,half,interval=lin_defense_profile(tables,unit,card["module_phase"] or {},card)
        panel=card["panel_attributes"]
        self.base_def,self.base_res=(_raw_f32(panel[key]) for key in ("def","magicResistance"))
        self.idle_def,self.idle_res=map(_raw_f32,idle)
        self.half_def,self.half_res=map(_raw_f32,half)
        self.poll=RecoveryTimer(_raw_f32(interval)) if interval else None
        self.half=False

    def advance(self, active, delta):
        if self.poll and self.poll.advance(delta) and self.half!=active:
            self.half=active
            return True
        return False

    def stats(self, active):
        ratio=ONE+(0 if active else self.idle_def)+(self.half_def if self.half else 0)
        resistance=self.base_res+(0 if active else self.idle_res)+(self.half_res if self.half else 0)
        return dict(defense_q32=mul(self.base_def,ratio),resistance_q32=resistance)


def lin_output_stacks(tables, phase, card):
    if selected_branch(tables, card) != "plx-y":
        return None
    rows = talent_rows(phase, -1, "10", ())
    board = active_board(rows, card["elite"], card["level"], card["potential"])
    data = tables.read("battle/buff_template_data.json")
    parent = data["lin_e_002_trait"]["eventToActions"]
    add, = parent["ON_BUFF_START"]
    remove, = parent["ON_BUFF_FINISH"]
    stack = add["_buff"]
    action, = data[stack["templateKey"]]["eventToActions"]["ON_OUTPUT_DAMAGE"]
    if (stack["overrideType"] != "STACK" or add["_buffOwner"] != "BUFF_SOURCE"
            or not remove["_decCntIfStack"] or remove["_buffKey"] != stack["buffKey"]
            or stack["maxValidStackCnt"] != board["max_valid_stack_cnt"] or not action["_isValidStackCnt"]
            or not action["_isStackable"] or action["_filterDamageType"]):
        raise ValueError("林 Y 模组伤害层数与预制体不符")
    return dict(per_enemy=board["damage_scale"], maximum=int(board["max_valid_stack_cnt"]))

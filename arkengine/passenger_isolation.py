"""Passenger's periodic CheckHasEnemyInRange on the owner's entity clock."""
from .attack_range import circle_in_range,range_cells
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def isolation_clock(tables,evidence,place,rows,cols):
    passive,=(c["fields"]["_buffs"][0] for c in evidence["character"]["components"]
              if c["script"]=="PassiveBuffAbility" and c["path"]=="Talents/2")
    check,=tables.read("battle/buff_template_data.json")[passive["templateKey"]]["eventToActions"]["ON_BUFF_TRIGGER"]
    condition=check["_conditionNode"]
    if (passive["templateKey"]!="pasngr_t_2" or not passive["waitFirstTriggerInterval"] or
            passive["firstTriggerInterval"]!=-1 or not condition["$type"].split(",")[0].endswith("+CheckHasEnemyInRange")
            or condition["_checkRadius"] or condition["_needCheckCount"] or condition["_checkId"]):
        raise ValueError("孤卒敌人列表、范围或轮询配置已变化")
    shape,=(b["valueStr"] for b in passive["blackboard"] if b["key"]=="range_id")
    return RecoveryTimer(_raw_f32(passive["triggerInterval"])),range_cells(tables.read("excel/range_table.json")[shape],place,rows,cols)


def isolated_in(cells,units):
    # UnitManager.enemies is the native collection. An allied summon or a device
    # with a targetable collider is not by itself a member of that collection.
    return not any(u.get("enemy_unit",True) and u.get("alive",True) and
                   circle_in_range(u["row"],u["col"],u["radius"],cells,box_query=True) for u in units)

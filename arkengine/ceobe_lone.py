"""Live same-side unit census and the Y2/Y3 deployment check for 独行长路."""
from .attack_range import circle_in_range, range_cells


def ceobe_alone(tables,card,event,units):
    """Units are a snapshot of UnitManager's same-side list on the current map."""
    owner=event["placement"]
    owner_id=event.get("owner_instance",card["operator_id"])
    locked=card["module"]=="uniequip_003_cerber" and card["module_level"]>=2
    if locked:
        check,_,_=tables.read("battle/buff_template_data.json")["cerber_e_003[t_2]"]["eventToActions"]["ON_BUFF_START"]
        cells=range_cells(tables.read("excel/range_table.json")[check["_rangeId"]],owner,event["rows"],event["cols"])
    for unit in units:
        if unit["id"]==owner_id or not unit["alive"]:
            continue
        if locked:
            if circle_in_range(unit["row"],unit["col"],unit["radius"],cells,box_query=True):
                return False
        elif abs(unit["grid_row"]-owner["row"])+abs(unit["grid_col"]-owner["col"])<2:
            return False
    return True

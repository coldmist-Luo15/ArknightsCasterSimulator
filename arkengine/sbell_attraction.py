"""圣聆初雪 S3 attraction selection on ordinary passable ground."""
from math import isfinite

from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .enemy_motion import ROUTE_REACH_DISTANCE, advance_direct_move
from .enemy_speed import move_attribute, movement_cap
from .phalanx_skills import SNOW
from .skill_recovery import _divide
from .volley_damage import _raw_f32


def _squared(a,b):
    r,c = (single(x-y) for x,y in zip(a,b))
    return single(single(r*r)+single(c*c))


def _straight(start,end,terrain):
    if start[0]!=end[0] and start[1]!=end[1]:
        return False
    dr,dc = ((v>u)-(v<u) for u,v in zip(start,end))
    return all((start[0]+n*dr,start[1]+n*dc) in terrain
               for n in range(abs(start[0]-end[0])+abs(start[1]-end[1])+1))


def run_s3_attraction(tables,card,event,*,mature_snow=(),navigation=None,random=None):
    if card["operator_id"]!=SNOW or card["skill"]!=3:
        raise ValueError("群山俯首的诱导须选择圣聆初雪三技能")
    ev = caster_prefab_evidence(tables,SNOW,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    parts = ev["character"]["components"]
    core, = (r["fields"] for r in parts if r["script"]=="ConstBoxRange" and "/AttractCore/" in r["path"])
    select, = (r["fields"] for r in parts if r["script"]=="TileSelector" and "/AttractCore/" in r["path"])
    people, = (r["fields"] for r in parts if r["script"]=="AdvancedSelectorWithEnemyOptions" and "/AttractSelector/" in r["path"])
    graph = tables.read("battle/buff_template_data.json")
    buff = graph["sbell2_s3[switch_mode]"]["eventToActions"]["ON_BUFF_START"][2]["_buff"]
    conditional, = graph[buff["templateKey"]]["eventToActions"]["ON_BUFF_START"]
    options = select["_options"]
    if (people["_postFilter"]!=4 or not people["_filterAbnormalImmune"] or people["_abnormalFlagImmune"]!=41 or
            (core["_rangeId"],options["tileTypesMask"],options["passableMask"],options["moveCostThreshold"])!=("x-5",6,1,1000) or
            buff["attributes"]["abnormalFlags"]!=["ATTRACTED"] or buff["statusResistable"]!="YES" or
            conditional["_conditionNode"]["_abilityName"]!="AttractCore" or not conditional["_failNodes"][0]["$type"].split(",")[0].endswith("+FinishBuff")):
        raise ValueError("群山俯首的诱导对象、地块选择或可抵抗属性发生变化")
    layout = event["attraction_terrain"]
    ground = {tuple(p) for p in layout["ordinary_ground"]}
    excluded = {tuple(p) for p in layout.get("end_tiles",[])}
    if any(len(p)!=2 or any(type(v) is not int for v in p) or not 0<=p[0]<event["rows"] or
           not 0<=p[1]<event["cols"] for p in ground|excluded):
        raise ValueError("诱导地形需要图内的普通低地格坐标")
    covered = skill_range_cells(tables,card,event)["cells"]
    anchors = ground.intersection(range_cells(tables.read("excel/range_table.json")[core["_rangeId"]],
                               event["placement"],event["rows"],event["cols"]))-excluded
    period = next(r["value"] for r in ev["skill_blackboard"] if r["key"]==buff["durationKey"])
    first = event["frame"]
    has_trace = "end_frame" in event
    end = event.get("end_frame",first)
    if type(first) is not int or type(end) is not int or not 0<=first<=end:
        raise ValueError("无效的诱导起止帧")
    answer = {}
    for key,target in event["targets"].items():
        pos = tuple(single(target[k]) for k in ("row","col"))
        if any(not isfinite(v) for v in pos) or type(target["eligible"]) is not bool or type(target["immune_to_induction"]) is not bool:
            raise ValueError("需要有限坐标及明确的可选性和诱导免疫")
        if not target["eligible"] or target["immune_to_induction"] or not circle_in_range(*pos,target["radius"],covered,box_query=True):
            answer[key] = dict(status="not_selected")
            continue
        cell = tuple(round(v) for v in pos)
        # A unique direct route through ordinary ground is reachable at these short distances.
        # The full native walk navigation (including path costs and diagonals) is needed for other legs.
        # V079 Execute (0x327ca98): reject an unreachable tile and continue enumerating.
        reachable=(p for p in anchors if navigation is None or not target.get("ground",True) or navigation.probe(cell,p)[0])
        ordered=sorted(reachable,key=lambda p:(_squared(pos,p),p))
        if not ordered:
            answer[key]=dict(status="no_reachable_anchor")
            continue
        shortest=_squared(pos,ordered[0])
        ties=[p for p in ordered if _squared(pos,p)==shortest]
        if random is None and (len(ties)>1 or navigation is None and not _straight(cell,ordered[0],ground)):
            answer[key]=dict(status="unresolved",reason="需要关卡随机流及导航",nearest=ties)
            continue
        goal=ties[random.range_int(0,len(ties))] if len(ties)>1 else ties[0]
        resistance = target.get("one_minus_status_resistance",1)
        if not isfinite(resistance) or not 0<=resistance<=1 or (target.get("status_resistance") and "one_minus_status_resistance" not in target):
            raise ValueError("需要实际的状态抗性剩余倍率")
        dt = tables.fixed_timestep()
        delta = _divide(_raw_f32(dt),max(_raw_f32(.001),_raw_f32(resistance)))
        duration = _raw_f32(period)
        expiry = first+(duration+delta-1)//delta
        row = answer[key] = dict(status="selected",destination=goal,expiry_frame=expiry,seconds=period)
        if len(ties)>1:
            row["tied_anchors"]=ties
        if not has_trace:
            continue
        velocity = tuple(single(x) for x in target["velocity"])
        if len(velocity)!=2 or not all(isfinite(v) for v in velocity):
            raise ValueError("需要诱导挂载时敌人的两个速度分量")
        track = {first:pos}
        row.update(positions=track,through_frame=first,tile_transitions=[])
        if pos==goal and velocity==(0,0):
            until = min(end,expiry)
            track.update((f,pos) for f in range(first+1,until+1))
            row.update(stop="at_destination",through_frame=until)
            continue
        if target.get("blocked") or target.get("motion_state","MOVE")!="MOVE" or (pos[0]!=goal[0] and pos[1]!=goal[1]):
            row.update(stop="requires_current_movement_state")
            continue
        if "prefab" not in target or "move_speed" not in target:
            row.update(stop="requires_enemy_steering_and_base_speed")
            continue
        steer = tables.enemy_steering(target["prefab"])
        attribute = target.get("move_speed_attribute_q32")
        if attribute is None:
            attribute = move_attribute(target["move_speed"],ratio_delta=target.get("move_ratio_delta",0))
        speed = movement_cap(attribute,layout.get("level_move_multiplier",1))
        visited = {cell}
        for frame in range(first+1,min(end,expiry)+1):
            if _squared(pos,goal)<=single(max(ROUTE_REACH_DISTANCE,single(speed*dt))**2):
                row["stop"] = "native_arrival_cursor_boundary"
                break
            pos,velocity = advance_direct_move(pos,velocity,goal,speed,steer,dt,True)
            track[frame] = pos
            row["through_frame"] = frame
            new = tuple(round(v) for v in pos)
            if new!=cell:
                if cell not in mature_snow or new not in mature_snow or new in visited:
                    row["stop"] = "tile_enter_and_leave_callbacks"
                    break
                row["tile_transitions"].append(dict(frame=frame,departed=cell,entered=new))
                cell = new
                visited.add(new)
        row.setdefault("stop","status_expired" if end>=expiry else "horizon")
    return dict(status="s3_attraction",receivers=answer,anchor_cells=sorted(anchors))

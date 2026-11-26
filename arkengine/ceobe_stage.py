"""Ceobe against an ordinary native ground wave."""
from .ceobe_battle import run_ceobe_battle
from .ranged_stage import prepare_ranged_stage
from .volley_damage import _raw_f32


def run_ceobe_stage(tables,request):
    buttons = [a for a in request["actions"] if a.get("kind")=="skill"]
    with_button = bool(buttons)
    base = dict(request, actions=[a for a in request["actions"] if a not in buttons[1:]])
    for key in ("units","units_at_deploy","owner_instance","buildup_before_attacks","root_clears_before_objects",
                "silence_checks_after_objects","silence_clears","ally_unit_events"):
        base.pop(key,None)
    card,stage,placed,event,world=prepare_ranged_stage(tables,base,operator="char_2013_cerber",
                                                       manual=with_button,portable=True,track_exits=True,
                                                       simulate_before_button=True)
    if len(buttons)>1:
        frames = [a["frame"] for a in buttons]
        if frames != sorted(set(frames)) or any(
                a != dict(kind="skill", operator_id="char_2013_cerber", frame=a["frame"], phase="after_tick")
                or not placed["frame"]<a["frame"]<=request["end_frame"] for a in buttons):
            raise ValueError("刻俄柏的连续开技需要依次送达的帧末按键")
        event["additional_presses"] = frames[1:]
    event["hold_manual_skill"]=card["skill"]!=1 and not with_button
    event["units"]=request.get("units",[])
    event["units_at_deploy"]=request.get("units_at_deploy",event["units"])
    if "owner_instance" in request:
        event["owner_instance"]=request["owner_instance"]
    if "buildup_before_attacks" in request:
        event["buildup_before_attacks"]=request["buildup_before_attacks"]
    if "root_clears_before_objects" in request:
        event["root_clears_before_objects"] = request["root_clears_before_objects"]
    if "silence_checks_after_objects" in request:
        event["silence_checks_after_objects"] = request["silence_checks_after_objects"]
    if "silence_clears" in request:
        event["silence_clears"] = request["silence_clears"]
    if "ally_unit_events" in request:
        event["ally_unit_events"] = request["ally_unit_events"]
    bb = {v["key"]:v["value"] for v in card["skill_row"]["blackboard"]}
    loss_before_deploy=world.settle_exits(placed["frame"]-1,lambda _:True)
    if loss_before_deploy is None:
        result=run_ceobe_battle(tables,card,event,bb,world=world)
    else:
        placed=None
        result=dict(status="stage_defeat",through_frame=loss_before_deploy,events=[],
                    remaining_hp_q32={k:_raw_f32(e["hp"]) for k,e in world.stats.items()})
    world.stage_result(result,fixed_timestep=tables.fixed_timestep(),target_field="target",hp_field="hp_after_q32",
                       projection_horizon=request["end_frame"])
    births = {k:dict(enemy=v["enemy_key"],frame=v["frame"],route=v["route_index"]) for k,v in world.births.items()}
    return dict(stage=stage["code"],deployment=placed,spawns=births,**result,source=tables.source)

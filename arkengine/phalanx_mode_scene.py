"""Repeated direct attacks starting at a verified active-mode entry (stationary receivers)."""
from fractions import Fraction
from .animation_assets import read_skin_actions
from .attack_range import circle_in_range
from .baked_mount import single
from .caster_ranges import skill_range_cells
from .caster_runtime import default_attack
from .damage import HP_EPSILON
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .phalanx_skills import CARN, SNOW, _controller, execute_phalanx_event
from .skill_recovery import READY_EPSILON
from .volley_damage import _raw_f32


def _active_mode(tables, card, charged):
    if card["operator_id"]==CARN:
        graph=tables.read("battle/buff_template_data.json")[f"billro_s_{card['skill']}"]["eventToActions"]["ON_BUFF_START"][0]
        if graph["_conditionNode"]["_buffKeys"]!=["billro_talent_enhance_mark"] or type(charged) is not bool:
            raise ValueError("需要明确的蓄力状态及对应的模式分支")
        rows=graph["_succeedNodes" if charged else "_failNodes"]
        node,=(row for row in rows if row["$type"].split(",")[0].endswith("+SwitchMode"))
        if not node["_restartFSM"] or node["_restoreDefault"] or node["_loadModeFromBlackboard"]:
            raise ValueError("卡涅利安的当前模式切换不符合已验证的逐帧入口")
        return node["_modeIndex"]
    if card["operator_id"]==SNOW and card["skill"] in (2,3):
        return _controller(tables,card)["attack_board_mode"]
    raise ValueError("当前配置未接入从技能模式进入的持续普攻事件")


def _timing(tables,card,index):
    key=card["operator_id"]
    cache=("phalanx_mode_action", key, index,card["skill_level"],card["module"],card["module_level"])
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    default=default_attack(tables,key,index)
    fields=default["fields"]
    if (default["ability"]!="MeleeAttack" or default["trigger"]!="SelectorTrigger" or not default["enabled"]
            or fields["_timeMode"]!=0 or fields["_resetCdStrategy"]!=1 or not fields["_waitForAttackEvent"]
            or fields["_maxAnimScale"]!=1 or fields["_preDelay"] or fields["_escapeTime"]):
        raise ValueError("所选模式的群攻动作包含尚未核实的特殊时序")
    action=fields["_animKey"]
    binding=read_skin_actions(tables,"chararts/"+key+".ab","defaultskin",(action,))
    bake=tables.read(f"bakemuzzledata/character/{key}_defaultskin.json")["bakedAnimDatas"]
    times=[]
    for view in ("front","back"):
        anim=binding["views"][view]["actions"][action]
        event=bake[view.capitalize()]["bakedEvents"][anim["clip"]]
        if anim["loop"] or anim["speed"]!=1 or event["events"]!=["OnAttack"] or len(event["eventTime"])!=1:
            raise ValueError("未解析的范围攻击动作关键帧")
        times.append((anim["duration"],event["eventTime"][0]))
    if times[0]!=times[1]:
        raise ValueError("不同朝向攻击时序有差异")
    board={r["key"]:r["value"] for r in card["skill_row"]["blackboard"]}
    panel=card["panel_attributes"]
    interval=single(single(panel["baseAttackTime"]+board.get("base_attack_time",0))*100 /
                    single(panel["attackSpeed"]+board.get("attack_speed",0)))
    duration,key_time=times[0]
    scale=min(1,max(single(.1),single(interval/duration)))
    step=tables.fixed_timestep()
    result=dict(interval_ticks=fixed_wait_frames(Fraction(interval)*30),
                ticks_to_key=baked_key_sample(key_time,single(1/scale),step)[0],key_seconds=key_time, mode_index=index,
                prefab_sha256=tables.source["caster_prefabs"][key]["sha256"],skin_sha256=binding["source"]["sha256"])
    tables._prefab_data[cache]=result
    return result


def run_phalanx_active_attacks(tables,card,event,*,ground_contact_handled=False,stationary_attract=False):
    """Stationary battle segment. Frame is the actual frame the skill mode replaced the old mode."""
    start,end=event["frame"],event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<=end:
        raise ValueError("无效的技能模式窗口")
    charged=event.get("charged")
    mode=_active_mode(tables,card,charged)
    timing=_timing(tables,card,mode)
    geometry=skill_range_cells(tables,card,event)
    targets={k:dict(v) for k,v in event["targets"].items()}
    if card["operator_id"]==SNOW and any((t.get("airborne") is not True and not ground_contact_handled) or (card["skill"]==3 and not stationary_attract and
            t.get("immune_to_induction") is not True) for t in targets.values()):
        return dict(status="unresolved",reason="圣聆初雪持续群攻片段只支持不触地面积雪、且不会被三技能移动的固定空中目标")
    duration=card["skill_row"]["duration"]
    step=_raw_f32(tables.fixed_timestep())
    stop=start+(_raw_f32(duration)-READY_EPSILON)//step+1 if duration>0 else None
    log=[]
    mark_y=(card["module_info"] or {}).get("typeIcon")=="plx-y"
    report=dict(status="active_mode_segment",skill_id=card["skill_id"],timing=timing,mode_frame=start,events=log)
    if mark_y and type(event.get("aura_count")) is not int:
        raise ValueError("Y 模组需要模式进入时已生效的增伤层数（范围内有效敌人数）")
    frame=start+1
    while frame<=end and (stop is None or frame<stop):
        hit_frame=frame+timing["ticks_to_key"]
        chosen={k:t for k,t in targets.items() if t["eligible"] and t["hp_q32"]>HP_EPSILON
                and circle_in_range(t["row"],t["col"],t["radius"],geometry["cells"],box_query=True)}
        if not chosen or hit_frame>end or stop is not None and hit_frame>=stop:
            break
        hit_event={**event,"kind":"phalanx_impact","frame":hit_frame,"targets":chosen}
        if card["operator_id"]==CARN and card["skill"]==3:
            hit_event["ticks_since_mode"]=hit_frame-start
        hits=execute_phalanx_event(tables,card,hit_event)["hits"]
        log.append(dict(start_frame=frame,impact_frame=hit_frame,hits=hits))
        for key,hit in hits.items():
            targets[key]["hp_q32"]=hit["hp_after_q32"]
            if "marks_after" in hit:
                targets[key]["prior_marks"]=hit["marks_after"]
                targets[key]["marks_by_source"]=hit["marks_by_source"]
        if mark_y and any(hit["hp_after_q32"]<=HP_EPSILON for hit in hits.values()):
            return dict(**report,stop="aura_needs_new_count_after_death",hp_after_q32={k:t["hp_q32"] for k,t in targets.items()})
        frame+=timing["interval_ticks"]
    ended=stop is not None and stop<=end
    if ended:
        report["mode_end_frame"]=stop
    if card["operator_id"]==CARN and card["skill"]==3 and charged:
        marked={k:t["marks_by_source"] for k,t in targets.items() if t.get("marks_by_source")}
        if ended:
            # GlobalMark.DoDetach finishes its mark_effect. Its ON_BUFF_FINISH removes
            # every charge stack on that receiver with _checkBuffSource false.
            report["mark_cleanup"]=dict(frame=stop,removed=marked)
        report["remaining_marks_by_target"]={} if ended else marked
    return dict(**report,stop="skill_ended" if ended else "horizon",
                hp_after_q32={k:t["hp_q32"] for k,t in targets.items()})

"""One automatic Blaze the Igniting Spark S1 ring, from deployment through enemy HP changes."""
from math import hypot, isfinite

from .attack_range import circle_in_range, range_cells
from .caster_equipment_traits import _trait_values
from .caster_prefabs import caster_prefab_evidence
from .caster_opening import deploy_to_first_cast
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import BLAZE, _board
from .enemy_elements import EnemyElements
from .fixed import ONE, mul
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def run_blaze_ring(tables,card,event):
    """One predeployed Guard host, one later-created stationary enemy outside Blaze's normal reach."""
    if card["operator_id"]!=BLAZE or card["skill"]!=1 or not card["module"]:
        raise ValueError("需要烛煌一技能和所选常规模组")
    start,end = event["frame"],event["end_frame"]
    if type(start) is not int or type(end) is not int or not 0<=start<end:
        raise ValueError("部署至结束的帧区间无效")
    host,enemy = event["guard"],event["enemy"]
    if (type(host["max_hp_q32"]) is not int or not isfinite(enemy["radius"]) or enemy["radius"]<=0
            or not 0<=enemy["row"]<event["rows"] or not 0<=enemy["col"]<event["cols"]):
        raise ValueError("火圈目标的坐标、半径或友方生命上限无效")
    if host["max_hp_q32"]<=_raw_f32(card["panel_attributes"]["maxHp"]):
        raise ValueError("该单宿主场景要求友方近卫生命上限严格高于烛煌")
    cells = range_cells(card["base_range"],event["placement"],event["rows"],event["cols"])
    if (host["row"],host["col"]) not in cells or circle_in_range(enemy["row"],enemy["col"],enemy["radius"],cells,box_query=True):
        raise ValueError("近卫必须在烛煌范围内，敌人应在她的普通攻击范围外")
    ev = caster_prefab_evidence(tables,BLAZE,1,skill_level=card["skill_level"],
                                module_id=card["module"],module_level=card["module_level"])
    skill = card["skill_row"]
    bb = {b["key"]:b["value"] for b in skill["blackboard"]}
    roots = ev["skill"]["components"]
    host_ability, = [r["fields"] for r in roots if r["script"]=="AnimatedActionToTargetAbility" and not r["path"]]
    source_buff, = host_ability["_activeBuffs"]
    selector, = [r["fields"] for r in roots if r["script"]=="AdvancedSelectorWithTagAndBuffKey"]
    graph = tables.read("battle/buff_template_data.json")
    query, = graph["blaze2_s_1"]["eventToActions"]["ON_BUFF_TRIGGER"]
    val, = graph["blaze2_s_1"]["eventToActions"]["ON_BUFF_START"]
    damage = graph["blaze2_s_1[aura]"]["eventToActions"]["ON_BUFF_START"][0]
    if (source_buff["templateKey"]!="blaze2_s_1" or not source_buff["waitFirstTriggerInterval"] or
            selector["_postFilter"]!=19 or selector["_maxNum"]!=1 or val["_blackboardKey"]!="cached_atk" or
            not damage["_noSourceDamage"] or not damage["_assignRealDamageToBB"]):
        raise ValueError("火圈选人、攻击快照或伤害回读与模板不符")
    distance = hypot(enemy["row"]-host["row"],enemy["col"]-host["col"])
    if distance>=query["_rangeRadius"]-1e-5:
        raise ValueError("该场景只接受圆心明确处于火圈内部的敌人，碰撞体边缘另行核实")
    state = EnemyElements.from_scene_enemy(tables,enemy)
    cost = skill["spData"]["spCost"]
    opening = deploy_to_first_cast(tables,card,ev,deploy_frame=start,end_frame=end,direction=event["placement"]["direction"],
                                   facing=event.get("view","Front"))
    rows = [dict(kind="deployment",frame=start,sp=opening.initial_sp)]
    cast = opening.cast
    if cast:
        if cast["status"]!="cast_and_first_ability_spell":
            raise ValueError("火圈施加动作未解析")
        rows.append(dict(frame=opening.through_frame,kind="auto_trigger",selected="guard",sp_before=opening.sp))
    source_atk = _raw_f32(card["panel_attributes"]["atk"])
    pulse = RecoveryTimer(_raw_f32(bb["interval"]))
    talent = _board(card,0,"1")
    module = _trait_values(card)
    stop = "horizon"
    for frame in range(start+1,end+1):
        if cast and frame==cast["spell_frame"]:
            rows.append(dict(frame=frame,kind="ring_attached",source_atk_snapshot_q32=source_atk))
        if cast and frame>cast["spell_frame"]:
            # Bounded first cast. A second auto-cast could refresh the host and its snapshot.
            if (frame-cast["cast_frame"])*state.step>=(cost-1)*ONE:
                stop = "next_cast_readiness_boundary"
                break
            if (frame-cast["spell_frame"])*state.step>=_raw_f32(bb["max_duration"]):
                stop = "ring_expired"
                break
            if pulse.advance(state.step):
                before = state.hp
                resistance = max(0,enemy["arts_resistance_q32"]+state.res_add)
                hit = ordinary_arts_hit(attack_raw=mul(source_atk,_raw_f32(bb["atk_scale"])),
                                        hp_raw=before,resistance_raw=resistance)
                state.hp = hit.hp_after_raw
                row = dict(kind="ring_hit",frame=frame,arts_damage_q32=hit.damage_raw,
                           hp_before_q32=before,hp_after_arts_q32=state.hp)
                rows.append(row)
                already = state.burst
                state.apply_buildup(frame,"FIRE",mul(before-state.hp,_raw_f32(bb["element_multiplier"])))
                if already is None and state.burst=="FIRE":
                    # X's attack buff listens BEFORE break. Her sourced talent damage is after
                    # the built-in no-source 7000 and therefore can use the X trait multiplier.
                    boosted = mul(source_atk,ONE+_raw_f32(module.get("atk",0)))
                    packet = state.apply_elemental_hp(frame,mul(boosted,_raw_f32(talent["ep_damage_scale"])),
                        source="blaze_burst_talent",output_scale_q32=_raw_f32(module.get("damage_scale",1)))
                    row.update(burst_talent=packet,heal_request_q32=mul(_raw_f32(card["panel_attributes"]["maxHp"]),
                                                                        _raw_f32(talent["hp_ratio"])))
                row["hp_after_q32"] = state.hp
                if state.hp<=HP_EPSILON:
                    stop = "enemy_defeated"
                    break
        # This fixture's Guard exists before this enemy. The receiver ticks after ring output.
        state.tick(frame)
    return dict(status="blaze_first_ring_scene",stop=stop,through_frame=(frame-1 if stop=="next_cast_readiness_boundary" else frame),
                cast=cast,events=rows,element_events=state.events,receiver=state.snapshot(),
                conditions=["stationary_guard_and_enemy","guard_precedes_enemy_object_order",
                            "guard_and_caster_survive_no_other_attacks_or_modifiers","first_ring_only"])

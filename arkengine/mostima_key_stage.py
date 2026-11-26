"""Mostima's S3 mode, expanded aura, timed melee waves and relative push requests."""
from .area_caster_skills import MOSTIMA,execute_area_skill
from .baked_mount import single
from .caster_first_spell import attack_key_clock
from .fixed import ONE,mul
from .mostima_slow import run_slow_channel
from .mystic_timing import fixed_wait_frames
from .stage_channel import prepare_channel,require_ordinary_highland
from .volley_damage import _raw_f32


def run_mostima_key_stage(tables,request):
    stage_input=dict(request)
    target_free_spans=stage_input.pop("enemy_target_free",())
    battle=prepare_channel(tables,stage_input,operator=MOSTIMA,skill=3,contact_checked_during_replay=True)
    require_ordinary_highland(battle.scene,[battle.deploy])
    card,ev=battle.card,battle.evidence
    cast,_=battle.cast()
    if cast["status"]!="cast_and_active_buff_spell":
        raise ValueError("时序之匙的施法未进入切换模式")
    ability,=(c["fields"] for c in ev["character"]["components"] if c["path"]=="Modes/S2/Body/Attack" and c["script"]=="MeleeAttack")
    buff,=ability["_activeBuffs"]
    if (ability["_selectTargetSource"],ability["_selectTargetTiming"],ability["_waitForAttackEvent"],
        ability["_timeMode"],ability["_maxAnimScale"],ability["_damageType"],buff["templateKey"])!=(1,1,1,0,1,2,"knockback[relative]"):
        raise ValueError("时序之匙的索敌时相、攻击轨道或相对击退定义发生变化")
    if not ability["_alwaysIncludeTarget"] or not ability["_allowNoTarget"] or ability["_interuptIfTargetDead"]:
        raise ValueError("时序之匙保留起手主靶的选项已变化")
    actions=ev["templates"][buff["templateKey"]]["eventToActions"]
    push,=actions["ON_BUFF_START"]
    if (set(actions)!={"ON_BUFF_START"} or push["$type"].split(",")[0]!="Torappu.Battle.Action.Nodes+Knockback"
            or (push["_source"],push["_target"],push["_useSourceDirection"])!=("SOURCE","TARGET",False)):
        raise ValueError("时序之匙的击退触发或方向源发生变化")
    board={r["key"]:r["value"] for r in ev["skill_blackboard"]}
    panel=card["panel_attributes"]
    frames=tables.read("excel/character_table.json")[card["operator_id"]]["phases"][card["elite"]]["attributesKeyFrames"]
    base_force,={f["data"]["baseForceLevel"] for f in frames}
    interval=single(single(panel["baseAttackTime"]+board["base_attack_time"])*100/single(panel["attackSpeed"]))
    dt=tables.fixed_timestep()
    mode=cast["spell_frame"]
    finish=mode+fixed_wait_frames(single(card["skill_row"]["duration"]/dt))
    sample,_=attack_key_clock(tables,ev,"Back" if battle.deploy["direction"]=="UP" else "Front",ability["_animKey"],interval)
    def slow(inside):
        return execute_area_skill(tables,card,dict(kind="slow_aura",frame=mode,in_range=inside,
                                 skill_affecting=True))["result"]["move_speed_ratio_delta"]
    settings=dict(spell=mode,sample=sample,period=fixed_wait_frames(single(interval/dt)),force=base_force+int(board["attack@force"]),
                  attack=mul(_raw_f32(panel["atk"]),ONE+_raw_f32(board["atk"])),local_delta=slow(True),
                  normal_cells=[(r["row"],r["col"]) for r in battle.opening["attack_range"]["cells"]])
    result=dict(stage=battle.opening["code"],squad=battle.config,deployment=battle.placement_event,cast=cast,
                source=tables.source,range_id=battle.geometry["range_id"],skill_end_frame=finish,
                attack_period=settings["period"],attack_key_delay=sample)
    return run_slow_channel(battle,result,[],request["end_frame"],finish,slow(False),mode=settings,target_free_spans=target_free_spans)

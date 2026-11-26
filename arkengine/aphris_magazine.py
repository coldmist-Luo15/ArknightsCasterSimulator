"""Replay Aphris's S3 against a stationary receiver from the first accepted attack in the active stance."""
from fractions import Fraction
from math import isfinite

from .animation_assets import read_skin_actions
from .baked_mount import single
from .caster_ammo import CasterAmmoCounter
from .caster_cast import _after_spell_clock
from .caster_prefabs import sample_clip_events
from .caster_ranges import skill_range_cells
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON,ordinary_arts_hit
from .fixed import ONE,mul
from .mystic_timing import fixed_wait_frames
from .marcil_deploy import _birth
from .skill_recovery import deploy_skill
from .volley_damage import _raw_f32


def _deployed_charge(tables,card,evidence,event,board,facing):
    deploy,press=event["frame"],event["skill_press_frame"]
    entry=event.get("target",{}).get("enter_frame")
    if any(type(f) is not int or f<0 for f in (press,) + ((entry,) if entry is not None else ())):
        raise ValueError("部署至开技场景需要整数输入帧和目标进场帧")
    if press<=deploy+_birth(tables,card,evidence,facing)["born_exit_update"]:
        raise ValueError("该开场要求谬因已经结束部署动作")
    sp,_=deploy_skill(tables,card,deploy)
    ready=deploy if sp.state.points==sp.state.cost else None
    for frame in range(deploy+1,press+1):
        sp.state.advance(sp.step)
        if ready is None and sp.state.points==sp.state.cost:
            ready=frame
    start=resolve_cast_start(tables,evidence,frame=press,phase="after_tick",facing=facing,
                             direction=event["placement"]["direction"],sp=sp.state.points)
    if start.get("cast_frame")!=press or start.get("spell_frame")!=press+1:
        raise ValueError("给定按键帧不满足空闲且技力充足时的充能入口")
    return charge_schedule(tables,evidence,event,board,start,ready,sp.step)


def charge_schedule(tables,evidence,event,board,start,ready,step):
    deploy,press=event["frame"],start["cast_frame"]
    entry=event.get("target",{}).get("enter_frame")
    nodes={c["script"]:c["fields"] for c in evidence["skill"]["components"]}
    wrapper=nodes["ActiveAbilityWrapper"]
    seq=nodes["AbilitySequenceGroup"]
    group_children={c["id"]:c["script"] for c in evidence["skill"]["components"]}
    selpath="Modes/"+("Sk3_Down" if event["placement"]["direction"]=="DOWN" else "Sk3")+"/Body/Attack/Selector"
    trigger,=(c["fields"] for c in evidence["character"]["components"] if c["path"]==selpath and c["script"]=="SelectorTrigger")
    if (not seq["_alwaysNext"] or seq["_enableLoop"] or seq["_checkCanUseSubAbility"] or
            [group_children[v["m_PathID"]] for v in seq["_abilities"]]!=["ActiveAbilityWrapper","ActiveBuffAbility"] or
            wrapper["_isInfinity"] or wrapper["_preDelay"] or nodes["ActiveBuffAbility"]["_preDelay"] or
            nodes["CastSkillWithLimitTimes"]["_switchToState"] or trigger["_overrideSearchTargetTick"]!=-1 or
            [b["templateKey"] for b in nodes["ActiveBuffAbility"]["_buffs"]].count("aphris_sk3_switch_mode")!=1):
        raise ValueError("充能或模式切换的技能对象变化")
    switch=tables.read("battle/buff_template_data.json")["aphris_sk3_switch_mode"]["eventToActions"]["ON_BUFF_START"][-1]
    if not (switch["$type"].split("+")[-1].startswith("SwitchMode,") and switch["_restartFSM"]):
        raise ValueError("技能模式不再重启原空闲状态")
    charge_end=press+fixed_wait_frames(Fraction(_raw_f32(board[wrapper["_durationKey"]]),step))
    mode_frame=charge_end+1
    if entry is not None and entry<=mode_frame:
        raise ValueError("该空闲开场要求三技能切换完毕后靶标才进入范围")
    first_idle=mode_frame+1
    attack=first_idle+3*((entry-first_idle+2)//3) if entry is not None else None
    return dict(deployment_frame=deploy,first_full_sp_frame=ready,cast=start,charge_end_frame=charge_end,
                mode_switch_frame=mode_frame,mode_index=int(board["mode_down" if event["placement"]["direction"]=="DOWN" else "mode_default"]),
                first_search_frame=first_idle,target_entry_frame=entry,accepted_attack_frame=attack)


def aphris_magazine_profile(tables,card,evidence,place,*,ordinary=False):
    down=place["direction"]=="DOWN"
    view="Back" if place["direction"]=="UP" else "Front"
    mode="Default" if ordinary else ("Sk3_Down" if down else "Sk3")
    path=f"Modes/{mode}/Body/Attack"
    a,=(c["fields"] for c in evidence["character"]["components"] if c["path"]==path and c["script"]=="AphrisMeleeAttack")
    if (a["_timeMode"]!=0 or a["_preDelay"] or a["_escapeTime"] or not a["_waitForAttackEvent"] or a["_resetCdStrategy"]!=1 or
            a["_interuptIfTargetDead"] or a["_damageType"]!=2 or a["_selectTargetTiming"]!=3 or a["_maxAnimScale"]!=1):
        raise ValueError("谬因三技能的命中或动作计时选项改变")
    board={r["key"]:r["value"] for r in evidence["skill_blackboard"]}
    scale=1 if ordinary else board["attack@"+a["_atkScaleKey"]]
    panel=card["panel_attributes"]
    attack=mul(_raw_f32(panel["atk"]),ONE+(0 if ordinary else _raw_f32(board["atk"])))
    interval=single(single(single(panel["baseAttackTime"])+(0 if ordinary else single(board["base_attack_time"])))*100/single(panel["attackSpeed"]))
    dt=tables.fixed_timestep()
    key=a["_downAnimKey"] if down and a["_downAnimKey"] else a["_animKey"]
    skin=read_skin_actions(tables,"chararts/"+card["operator_id"]+".ab","defaultskin",(key,),front_only=[key] if down else ())
    anim=skin["views"][view.lower()]["actions"][key]
    if anim["loop"] or anim["speed"]!=1:
        raise ValueError("三技能攻击动作不再是默认单次播放")
    anim_scale=min(a["_maxAnimScale"],max(single(.1),single(interval/anim["duration"])))
    baked=sample_clip_events(evidence["animations"],view,anim["clip"],playback_speed=single(1/anim_scale),timestep=dt)
    sample,=(k["clip_tick"] for k in baked if k["event"]=="OnAttack")
    period=fixed_wait_frames(single(interval/dt))
    # OBJECTS starts the main attack before the fixed coroutine update. The attack
    # has no additional bent segments. Its post-delay is the remaining clip budget.
    tail=_after_spell_clock(sample,single(anim["duration"]*anim_scale),int(dt*ONE),same_frame_resume=True)["end"]
    if tail>=period:
        raise ValueError("下一轮攻击的就绪与上一轮后摇发生重叠，需重新核对索敌时相")
    return board,view,attack,scale,sample,period,tail


def relay_beam_hit(board, attack, hp, resistance, extensions, *, ordinary=False):
    """Direct melee packet, followed by BuffToCastTargets and its ON_BUFF_START damage loop."""
    scale = 1 if ordinary else board["attack@atk_scale"]
    primary = ordinary_arts_hit(attack_raw=mul(attack,_raw_f32(scale)),hp_raw=hp,resistance_raw=resistance)
    packets = [("beam_impact", primary)]
    applied = 0
    if not ordinary and primary.hp_after_raw > HP_EPSILON:
        applied = min(extensions, int(board["attack@max_times"]))
        if applied:
            extra = ordinary_arts_hit(attack_raw=mul(attack,_raw_f32(board["attack@extra_atk_scale"])),
                                      hp_raw=primary.hp_after_raw,resistance_raw=resistance)
            packets.append(("relay_extra_impact", extra))
    slow = 0 if ordinary else _raw_f32(board["attack@origin_sluggish"]) + applied*_raw_f32(board["attack@extra_sluggish"])
    return packets, slow


def run_aphris_magazine(tables,card,evidence,event):
    from .aphris_relays import AphrisRelays
    if card["skill_id"]!="skchr_aphris_3" or not isinstance(event.get("relays"),list):
        raise ValueError("该弹药回放需要谬因三技能和技能态现有中继器")
    place,enemy=event["placement"],event["target"]
    field=skill_range_cells(tables,card,event)
    if ((enemy["row"],enemy["col"]) not in field["cells"] or type(enemy["resistance"]) not in (int,float) or
            not isfinite(enemy["resistance"]) or enemy["resistance"] < 0 or
            type(enemy["hp"]) not in (int,float) or not isfinite(enemy["hp"]) or _raw_f32(enemy["hp"])<=HP_EPSILON):
        raise ValueError("需要处于光束内的存活靶标和明确法抗")
    board,view,_,_,sample,period,tail=aphris_magazine_profile(tables,card,evidence,place)
    counter=CasterAmmoCounter(evidence)
    deployment=_deployed_charge(tables,card,evidence,event,board,view) if "skill_press_frame" in event else None
    if deployment and event["relays"]:
        raise ValueError("从部署开场时须用 caster_combat 的 relay_deploy 输入实际的中继器操作")
    begin=deployment["accepted_attack_frame"] if deployment else event["frame"]
    relays = AphrisRelays(tables,card,evidence)
    if deployment:
        relays.switch(deployment["cast"]["spell_frame"], True)
    else:
        relays.boosted = True
        relays.capacity += relays.extra
        relays.stock = relays.capacity
    for unit in event["relays"]:
        key = unit["relay"]
        if key in relays.active or not relays.ready or (unit["row"],unit["col"]) == (place["row"],place["col"]):
            raise ValueError("技能态中继器重复、超出上限或与宿主重叠")
        relays.stock -= 1
        relays.active[key] = dict(unit,expires_frame=None)
    target_index = field["cells"].index((enemy["row"],enemy["col"]))
    interval, = {c["fields"]["_hitInterval"] for c in evidence["character"]["components"] if c["script"]=="AphrisMeleeAttack"}
    delay = fixed_wait_frames(interval/tables.fixed_timestep()) * field["dir_change_times"][target_index]
    extensions = field["extend_times"][target_index]
    from .area_caster_skills import sampling_penetration
    res = _raw_f32(max(0,enemy["resistance"]-sampling_penetration(card,evidence)))
    attack = relays.attack(card["panel_attributes"]["atk"],board["atk"])
    hp=_raw_f32(enemy["hp"])
    hits=[]
    while counter.remaining and hp>HP_EPSILON:
        first_spell=begin+sample
        impact=first_spell+delay
        counter.ability_event(first_spell,"spell_on")
        packets,slow=relay_beam_hit(board,attack,hp,res,extensions)
        hp=packets[-1][1].hp_after_raw
        hits.append(dict(attack_frame=begin,baked_on_attack_frame=first_spell-1,first_spell_frame=first_spell,impact_frame=impact,
                         cast_end_frame=begin+tail,
                         damage_q32=sum(p.damage_raw for _,p in packets),hp_after_q32=hp,remaining_ammo=counter.remaining,
                         packets=[dict(kind=kind,damage_q32=p.damage_raw,hp_after_q32=p.hp_after_raw) for kind,p in packets],
                         extension_count=extensions,sluggish_seconds=slow/ONE if hp>HP_EPSILON else 0))
        counter.ability_event(begin+tail,"cast_end")
        if not counter.remaining:
            counter.owner_tick(begin+tail+1)
            relays.switch(begin+tail+1,False)
        begin+=period
    actual_end=event.get("final_attack_end_frame")
    if actual_end is not None and (type(actual_end) is not int or counter.remaining or actual_end!=hits[-1]["cast_end_frame"]):
        raise ValueError("给定的末发结束帧与当前动作或有效弹数不符")
    return dict(status="waiting_for_target" if counter.remaining else "primary_stop_requested",relay_card=relays.snapshot(),
                relay_events=relays.events,
                hits=hits,counter=counter.snapshot(),hp_after_q32=hp,attack_q32=attack,attack_period=period,
                range_cells=field["cells"],first_attack_frame=hits[0]["attack_frame"],through_frame=counter.frame,deployment=deployment,
                conditions=(["solo_idle_until_charge_completed","target_enters_before_objects"] if deployment else
                            ["active_s3_after_three_second_charge","first_idle_search_accepted_at_frame"])+["stable_relay_placement_during_magazine",
                            "single_stationary_receiver_continuously_in_range","uninterrupted_default_animation"])

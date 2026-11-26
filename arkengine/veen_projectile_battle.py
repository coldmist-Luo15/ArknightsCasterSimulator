"""One unstored Veen S1/S2 main bolt from the firing mount through the last periodic damage tick."""
from .attack_range import circle_in_range
from .caster_flight import projectile_reach
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .damage import HP_EPSILON, ordinary_arts_hit
from .five_star_skills import _flag, _integer, _number, _targets
from .fixed import ONE
from .module_effects import active_board, talent_rows, trait_board
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def run_veen_bolt(tables,card,scene):
    from .caster_drone_chain_mystic import VEEN, _veen
    slot=card["skill"]
    if card["operator_id"]==VEEN and slot==3:
        from .veen_bounce_battle import run_veen_bounce
        return run_veen_bounce(tables,card,scene)
    if card["operator_id"]!=VEEN or slot not in (1,2):
        raise ValueError("单发无储能主弹场景支持维伊一、二技能")
    frame,end=_integer(scene,"frame"),_integer(scene,"end_frame")
    if not frame<end<=frame+18000:
        raise ValueError("需要有效的单弹时间区间")
    cells=skill_range_cells(tables,card,scene)["cells"]
    choices=[r for r in _targets(scene) if not _flag(r,"has_veen_dot")
             and circle_in_range(_number(r,"row"),_number(r,"col"),_number(r,"radius"),cells,box_query=True)]
    if not choices:
        return dict(status="no_target_for_volley",events=[],through_frame=frame)
    priority=min(_number(r,"route_remaining") for r in choices)
    picked=[r for r in choices if r["route_remaining"]==priority]
    if len(picked)!=1:
        return dict(status="unresolved_ordinary_target_priority_tie",events=[],through_frame=frame)
    target,=picked
    hp=_raw_f32(_number(target,"hp"))
    if hp<=HP_EPSILON:
        raise ValueError("主弹目标必须存活")
    resistance=_raw_f32(_number(target,"res",minimum=-1000))
    data=caster_prefab_evidence(tables,VEEN,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    a,=[c for c in data["character"]["components"] if c["script"]=="MultiChargeRangedAttack" and
        c["path"]==f"Modes/S{slot}/Body/Attack/AttackBase"]
    key=a["fields"]["_projectileKey"]
    for p in data["projectiles"][key].values():
        proj,=[c["fields"] for c in p["components"] if c["script"]=="SimpleProjectile"]
        move,=[c["fields"] for c in p["components"] if c["script"]=="AdvancedMovement"]
        if (not proj["_alwaysHitTraceTargetInTheEnd"] or proj["_alwaysHitTraceTargetWhenReached"]
                or move["_delayAfterReached"] or move["_delayToReachAfterHit"] or move["_delayToStart"]):
            raise ValueError("主弹生命周期与已核实普通分支不符")
    phase=card["module_phase"] or {}
    unit=tables.read("excel/character_table.json")[VEEN]
    trait=trait_board(unit,phase,card)
    talents=[active_board(talent_rows(phase,i,str(i+1),t["candidates"]),card["elite"],card["level"],card["potential"])
             for i,t in enumerate(unit["talents"][:2])]
    board={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    release=_veen(slot,"projectile_release",dict(projectile_kind="primary",s2_stacks=scene.get("s2_stacks"),
                  has_merged_attack_buff=_flag(scene,"has_merged_attack_buff")),board,card["panel_attributes"],trait,talents)
    log=[dict(frame=frame,kind="main_projectile_release",projectile=key,target=target["id"],**release)]
    path=projectile_reach(tables,data,dict(projectile=key,frame=frame+1,origin=scene["origin"],round_position=True,
                           target_mounts=[target["hit_mount"]]*min(end-frame,300)))
    hit=path.get("reach_frame")
    if hit is None:
        return dict(status=path["status"],through_frame=min(end,frame+300),events=log,flight=path)
    packet=ordinary_arts_hit(attack_raw=release["direct_attack_raw"],resistance_raw=resistance,hp_raw=hp)
    hp=packet.hp_after_raw
    log.append(dict(frame=hit,kind="main_projectile_hit",target=target["id"],damage_q32=packet.damage_raw,hp_q32=hp))
    step=int(tables.fixed_timestep()*ONE)
    duration=_raw_f32(talents[1]["attack@duration"])
    timer=RecoveryTimer(ONE)
    tick=hit
    status="horizon"
    if hp>HP_EPSILON:
        log.append(dict(frame=hit,kind="apply_dot_and_exclude_from_veen_attacks",target=target["id"],stacks=1))
        while tick<end:
            tick+=1
            duration-=step
            if timer.advance(step):
                packet=ordinary_arts_hit(attack_raw=_raw_f32(talents[1]["attack@value"]),resistance_raw=resistance,hp_raw=hp)
                hp=packet.hp_after_raw
                log.append(dict(frame=tick,kind="warfare_dot",target=target["id"],damage_q32=packet.damage_raw,hp_q32=hp))
                if hp<=HP_EPSILON:
                    break
            if duration<=0:
                log.append(dict(frame=tick,kind="dot_expired",target=target["id"]))
                status="dot_expired"
                break
    if hp<=HP_EPSILON:
        status="target_defeated"
    return dict(status=status,through_frame=tick,events=log,hp_q32=hp,flight=path,
                conditions=["技能已开启，本轮没有积蓄能量，并提供脱手挂点及本帧合并能量加攻状态",
                            "等嘲讽、静止、持续可见靶；目标在飞行前已进场，本帧目标更新先于新弹体",
                            "追踪本轮主弹与其命中附着的五秒持续伤害，其余单位均不再干预受击者"])

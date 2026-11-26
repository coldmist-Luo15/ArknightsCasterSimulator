"""Advance already deployed skill drones from their first target-eligible tick."""
from hashlib import sha256
from math import hypot

from .attack_range import circle_in_range
from .battle_random import BattleRandom
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .damage import HP_EPSILON
from .five_star_skills import _integer, _number, _targets
from .fixed import ONE
from .module_effects import active_board, talent_rows, trait_board
from .mystic_timing import fixed_wait_frames
from .skill_recovery import _divide
from .volley_damage import _raw_f32


def _blast_profile(ev,behaviour,timestep):
    profiles=set()
    for key in behaviour["_projectileKeys"]:
        for p in ev["projectiles"][key].values():
            rows=p["components"]
            move,=[c["fields"] for c in rows if c["script"]=="AttachToTarget"]
            shape,=[c["fields"] for c in rows if c["script"]=="CircleCollider2D"]
            hit,=[c["fields"] for c in rows if c["script"]=="HitBehaviour"]
            if (not move["_immediatelyReach"] or not move["_delayAfterReached"] or move["_delayToStart"]
                    or move["_extraDelayTime"] or move["_followTarget"] or move["_attachToMountPoint"]
                    or not hit["_onlyCheckHitWhenStop"] or hit["_onlyCheckHitWhenReachTarget"]
                    or any(shape["m_Offset"].values()) or not shape["m_IsTrigger"]):
                raise ValueError("澄闪子弹体的停弹时钟或圆形范围不符")
            profiles.add((fixed_wait_frames(move["_delayTime"]/timestep),shape["m_Radius"]))
    profile,=profiles
    return profile


def _blast_receivers(targets,hp,center,radius):
    hits=[]
    for key,row in targets.items():
        if hp[key]<=HP_EPSILON:
            continue
        if key!=center:
            anchor=targets[center]
            if not all(axis in t for t in (anchor,row) for axis in ("row","col")) or "radius" not in row:
                return None,"requires_explosion_receiver_positions"
            distance=hypot(_number(anchor,"row")-_number(row,"row"),_number(anchor,"col")-_number(row,"col"))
            reach=radius+_number(row,"radius")
            if abs(distance-reach)<1e-5:
                return None,"unresolved_explosion_tangent"
            if distance>reach:
                continue
        hits.append(dict(row,hp=hp[key]/ONE))
    return hits,None


def run_attached_drones(tables,card,scene,*,stream=None):
    from .caster_drone_chain_mystic import GG, LAPPLAND, _drone, _lappland_weighted_target
    uid,slot=card["operator_id"],card["skill"]
    if uid!=GG and (uid!=LAPPLAND or slot!=2):
        raise ValueError("已附着单元时钟支持澄闪技能与荒芜拉普兰德二技能")
    start,end=(_integer(scene,k) for k in ("frame","end_frame"))
    shared=stream is not None
    seed=None
    if shared:
        if "native_seed" in scene or "seed" in scene or scene.get("native_prior_draws",0):
            raise ValueError("共享战斗流已经包含浮游单元的初始游标")
    elif "native_seed" in scene:
        if "seed" in scene:
            raise ValueError("浮游原生种子与旧例 seed 互斥")
        stream=BattleRandom(scene["native_seed"])
        prior=_integer(scene,"native_prior_draws") if "native_prior_draws" in scene else 0
        if prior>1000000:
            raise ValueError("初始原生随机游标超过范围")
        for _ in range(prior):
            stream.next_double()
    else:
        seed=_integer(scene,"seed")
    if not start<=end<=start+18000:
        raise ValueError("附着时钟区间无效")
    op=tables.read("excel/character_table.json")[uid]
    phase=card["module_phase"]
    traits=trait_board(op,phase,card)
    talents=[active_board(talent_rows(phase,i,str(i+1),t["candidates"]),card["elite"],card["level"],card["potential"])
             for i,t in enumerate(op["talents"][:2])]
    board={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    stats=card["panel_attributes"]
    stage=0 if uid==GG else _integer(scene,"talent_stage")
    profile=_drone(uid,slot,"drone_profile",dict(talent_stage=stage),board,stats,traits,talents)
    if uid==LAPPLAND and stage>3:
        raise ValueError("叙拉古的荒法只支持已有的四种层数")
    targets={t["id"]:t for t in _targets(scene)}
    hp={k:_raw_f32(_number(t,"hp")) for k,t in targets.items()}
    if any(v<=HP_EPSILON for v in hp.values()):
        raise ValueError("在场靶标需要正生命")
    ev=caster_prefab_evidence(tables,uid,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    components={c["id"]:c for c in ev["character"]["components"]}
    remote,=[r for r in components.values() if r["script"]=="MultiFunnelRemoteAttack" and r["path"].startswith(f"Modes/S{slot}/")]
    active=remote["fields"]["_funnelActions"][:profile["active_drones"]]
    keys=[components[r["m_PathID"]]["fields"]["_projectileKey"] for r in active]
    blast={}
    for key in keys:
        for p in ev["projectiles"][key].values():
            hit,=[r for r in p["components"] if r["script"] in ("GdglowHitBehaviour","MultiFunnelHitBehaviour")]
            if hit["fields"]["_waitFirstPeriod"]:
                raise ValueError("单元首击计时与已验证配置不符")
            if uid==GG:
                blast[key]=_blast_profile(ev,hit["fields"],tables.fixed_timestep())
    step=int(tables.fixed_timestep()*ONE)
    # CN V079 MultiFunnelHitBehaviour.Init reads the owner's current attack
    # time and subtracts one logic update before passing it to PeriodicTimer.Reset.
    speed=stats["attackSpeed"]+board.get("attack_speed",0)
    if uid==LAPPLAND:
        speed+=talents[1].get("attack_speed",0)
    interval_raw=_divide(_raw_f32(stats["baseAttackTime"]),_divide(_raw_f32(speed),100*ONE))
    period=max(0,interval_raw-step)
    if "drones" in scene:
        bound=_targets(scene,"drones")
    elif scene.get("entry")=="fresh_drone_group_reached":
        # The stationary fixture starts with all freshly dispatched drones at
        # their first reached update and performs the preceding acquisition on
        # that identical target set. No individual impact timing is supplied.
        cells=skill_range_cells(tables,card,scene)["cells"]
        pool=[t for t in targets.values() if circle_in_range(_number(t,"row"),_number(t,"col"),_number(t,"radius"),cells,box_query=True)]
        if not pool:
            return dict(status="no_target_for_drones",through_frame=start,events=[],hp_q32=hp)
        if uid==GG:
            order=min(_number(t,"route_remaining") for t in pool)
            top=[t for t in pool if t["route_remaining"]==order]
            if len(top)!=1:
                return dict(status="unresolved_target_priority_tie",through_frame=start,events=[],hp_q32=hp)
        elif stream is None:
            pool.sort(key=lambda t:t["id"])
        bound=[]
        for index,key in enumerate(keys):
            if uid==GG:
                target=top[0]
            elif stream is not None:
                picked=_lappland_weighted_target(pool,slot,stream)
                if "target" not in picked:
                    return dict(status=picked["reason"],through_frame=start,events=[],hp_q32=hp,native_draws=stream.draws)
                target=targets[picked["target"]]
            else:
                sample=int.from_bytes(sha256(f"{seed}:acquire:{index}".encode()).digest()[:8],"big")
                target=pool[(sample*len(pool))>>64]
            bound.append(dict(id=key,target=target["id"],projectile=key,first_reached_tick=start))
    else:
        raise ValueError("需要实际附着单元列表或 fresh_drone_group_reached 检查点")
    drones=[]
    for d in bound:
        if d["projectile"] not in keys or d["target"] not in targets:
            raise ValueError("附着单元的实包弹体或目标ID无效")
        tick=_integer(d,"first_reached_tick")
        if not start<=tick<=end:
            raise ValueError("先前已命中过的单元需要其余时钟状态")
        drones.append(dict(id=d["id"],projectile=d["projectile"],target=d["target"],tick=tick,remaining=0,
                           previous_target=None,prior_same_target_hits=0,explosion_stack=1,attempts=0,active=True))
    if len(drones)!=profile["active_drones"] or len({d["projectile"] for d in drones})!=len(drones):
        raise ValueError("需要技能实际释放的所有单元各一条，新附着的数组依游戏本帧更新顺序排列")
    log=[]
    pending=[]
    lost=[]
    status="horizon"
    for f in range(start,end+1):
        for d in drones:
            if not d["active"] or f<d["tick"]:
                continue
            k=d["target"]
            if hp[k]<=HP_EPSILON:
                lost.append(dict(drone=d["id"],frame=f,reason="locked_target_defeated"))
                d["active"]=False
                continue
            d["remaining"]-=step
            if d["remaining"]>0:
                continue
            d["remaining"]=period
            draw=None
            if stream is None:
                draw=int.from_bytes(sha256(f'{seed}:{d["id"]}:{d["attempts"]}'.encode()).digest()[:8],"big")/(1<<64)
            d["attempts"]+=1
            result=_drone(uid,slot,"drone_hit",dict(d,target=dict(targets[k],hp=hp[k]/ONE),roll=draw,
                         talent_stage=stage,explosion_targets=[]),board,stats,traits,talents,stream=stream)
            if result.get("exploded"):
                delay,radius=blast[d["projectile"]]
                # A child created inside the OBJECTS snapshot enters at F+1.
                # Its first fixed waiter resumes in that frame's COROUTINES.
                impact=f+delay
                pending.append(dict(d=d,reach=f+1,tick=impact,radius=radius))
                log.append(dict(frame=f,kind="explosion_projectile_emitted",drone=d["id"],target=k,
                                first_objects_frame=f+1,stop_frame=impact))
                lost.append(dict(drone=d["id"],frame=f,reason="self_destruct"))
                d["active"]=False
                continue
            damage=result["hit"]["damage_raw"]
            hp[k]=max(0,hp[k]-damage)
            if hp[k]<HP_EPSILON:
                hp[k]=0
            for field in ("previous_target","prior_same_target_hits","explosion_stack"):
                if field in result:
                    d[field]=result[field]
            log.append(dict(frame=f,kind="drone_hit",drone=d["id"],target=k,damage_q32=damage,hp_q32=hp[k],
                            attack_scale=result["damage_scale"],sluggish_seconds=result.get("sluggish_seconds",0),
                            fear_seconds=result.get("fear_seconds",0),silence_seconds=result.get("silence_seconds",0)))
            if stream is not None and result.get("fear_seconds"):
                status="requires_s2_fear_application_and_shared_random_state"
                break
        if status!="horizon":
            break
        for child in pending[:]:
            d=child["d"]
            k=d["target"]
            if child["reach"]==f:
                if hp[k]<=HP_EPSILON:
                    status="requires_child_origin_after_parent_target_death"
                    break
                log.append(dict(frame=f,kind="explosion_projectile_reach",drone=d["id"],target=k))
            if child["tick"]!=f:
                continue
            rows,missing=_blast_receivers(targets,hp,k,child["radius"])
            if missing:
                status=missing
                break
            resolved=_drone(uid,slot,"drone_hit",dict(d,target=targets[k],explosion_targets=rows,
                            explosion_stack=int(talents[0]["attack@max_stack_cnt"])),board,stats,traits,talents)
            hits=[]
            for packet in resolved["hits"]:
                receiver=packet["id"]
                remainder=hp[receiver]-packet["damage_raw"]
                hp[receiver]=0 if remainder<HP_EPSILON else remainder
                hits.append(dict(target=receiver,damage_q32=packet["damage_raw"],hp_q32=hp[receiver]))
            log.append(dict(frame=f,kind="explosion_projectile_stop_hit",drone=d["id"],center=k,hits=hits,
                            radius=child["radius"],sluggish_seconds=resolved["sluggish_seconds"]))
            pending.remove(child)
        if all(v==0 for v in hp.values()):
            status="all_targets_defeated"
        elif lost and not pending and status=="horizon":
            status="requires_drone_return_and_next_acquisition"
        if status!="horizon":
            break
    return dict(status=status,through_frame=f,hp_q32=hp,events=log,drones=drones,returning_drones=lost,
                rng_mode="shared_battle_random" if shared else "isolated_battle_random" if stream else "model_seed_per_drone",
                native_draws=stream.draws if stream else None,
                timer_period_q32=period,owner_attack_interval_q32=interval_raw,
                conditions=["场景起点为相应技能生效且各单元首次可检查已到达目标的更新帧",
                            "记录本组首次派出的单元和它们的自爆弹体；区间内技能、攻速、天赋阶段与目标圆心不变",
                            "各单元的叠层及自爆计数从本次部署首击开始；原生流按场内更新顺序共享，区间内其他事件不抽样"])

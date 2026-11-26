"""CN V079 single Veen S3 volley against stationary, ordinary enemies."""
from collections import Counter
from math import hypot

from .attack_range import circle_in_range
from .baked_mount import single
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .chain_targets import nearest_chain_target
from .damage import HP_EPSILON, ordinary_arts_hit
from .five_star_skills import _flag, _integer, _number, _targets
from .fixed import ONE, mul
from .module_effects import active_board, talent_rows, trait_board
from .skill_recovery import RecoveryTimer
from .stage_channel import UnsupportedChannelScene
from .trace_motion import ordinary_trace_step
from .volley_damage import _raw_f32


def _sort_bounce_hit_counts(ordered,hits):
    """The V079 mscorlib array sort can change the order of equal hit counts."""
    def swap_if_greater(a,b):
        if hits[ordered[a]]>hits[ordered[b]]:
            ordered[a],ordered[b]=ordered[b],ordered[a]

    def sift(lo,root,size):
        # These heap indices are one-based relative to lo, like ArraySortHelper.
        item=ordered[lo+root-1]
        while root<=size//2:
            child=2*root
            if child<size and hits[ordered[lo+child-1]]<hits[ordered[lo+child]]:
                child+=1
            if hits[item]>=hits[ordered[lo+child-1]]:
                break
            ordered[lo+root-1]=ordered[lo+child-1]
            root=child
        ordered[lo+root-1]=item

    def sort(lo,hi,depth):
        while hi>lo:
            size=hi-lo+1
            if size<=16:
                if size==3:
                    swap_if_greater(lo,lo+1)
                    swap_if_greater(lo,hi)
                    swap_if_greater(lo+1,hi)
                else:
                    # SwapIfGreater (size 2) and strict insertion (4..16) are stable.
                    ordered[lo:hi+1]=sorted(ordered[lo:hi+1],key=hits.__getitem__)
                return
            if depth==0:
                for root in range(size//2,0,-1):
                    sift(lo,root,size)
                for last in range(hi,lo,-1):
                    ordered[lo],ordered[last]=ordered[last],ordered[lo]
                    sift(lo,1,last-lo)
                return
            depth-=1
            mid=lo+(hi-lo)//2
            swap_if_greater(lo,mid)
            swap_if_greater(lo,hi)
            swap_if_greater(mid,hi)
            pivot=hits[ordered[mid]]
            ordered[mid],ordered[hi-1]=ordered[hi-1],ordered[mid]
            left,right=lo,hi-1
            while True:
                left+=1
                right-=1
                while hits[ordered[left]]<pivot:
                    left+=1
                while pivot<hits[ordered[right]]:
                    right-=1
                if left>=right:
                    break
                ordered[left],ordered[right]=ordered[right],ordered[left]
            ordered[left],ordered[hi-1]=ordered[hi-1],ordered[left]
            sort(left+1,hi,depth)
            hi=left-1

    sort(0,len(ordered)-1,2*len(ordered).bit_length())


def _bounce_pool(targets,hp,center,hits,radius,*,frame=0,nearest_tie=None):
    pool=[]
    for key,row in targets.items():
        if hp[key]<=HP_EPSILON:
            continue
        dist=hypot(row["row"]-center["row"],row["col"]-center["col"])
        if abs(dist-radius-_number(row,"radius"))<1e-5:
            return None,"unresolved_bounce_circle_tangent"
        if dist<radius+row["radius"]:
            pool.append(key)
    if not pool:
        return None,None
    eligible=[k for k in pool if len(pool)==1 or k!=center["id"]]
    lowest=min(hits[k] for k in eligible)
    choices=[k for k in eligible if hits[k]==lowest]
    if len(choices)==1:
        return choices[0],None
    # List.Sort starts with the NearestRangeSelector result and removes the current
    # target *after* sorting. The special three-element network can invert a tie.
    radii={k:_number(targets[k],"radius") for k in pool}
    pool_rows=[targets[k] for k in pool]
    ordered=[]
    while len(ordered)<len(pool):
        try:
            chosen=nearest_chain_target(frame,center,pool_rows,radii,ordered,radius,nearest_tie=nearest_tie)
        except UnsupportedChannelScene as boundary:
            return None,str(boundary)
        if chosen is None:
            return None,"unresolved_bounce_selector_contact"
        ordered.append(chosen)
    _sort_bounce_hit_counts(ordered,hits)
    return next(k for k in ordered if k in eligible),None


def run_veen_bounce(tables,card,scene):
    from .caster_drone_chain_mystic import VEEN, _veen
    start,end=_integer(scene,"frame"),_integer(scene,"end_frame")
    if not start<end<=start+18000:
        raise ValueError("无效的维伊三技能弹射区间")
    cells=skill_range_cells(tables,card,scene)["cells"]
    targets={r["id"]:r for r in _targets(scene)}
    hp={key:_raw_f32(_number(r,"hp")) for key,r in targets.items()}
    if any(raw<=HP_EPSILON for raw in hp.values()):
        raise ValueError("初始敌人必须存活")
    pool=[r for r in targets.values() if not _flag(r,"has_veen_dot")
          and circle_in_range(_number(r,"row"),_number(r,"col"),_number(r,"radius"),cells,box_query=True)]
    if not pool:
        return dict(status="no_target_for_volley",events=[],through_frame=start,hp_q32=hp)
    order=min(_number(r,"route_remaining") for r in pool)
    primary=[r for r in pool if r["route_remaining"]==order]
    if len(primary)!=1:
        return dict(status="unresolved_ordinary_target_priority_tie",events=[],through_frame=start,hp_q32=hp)
    phase=card["module_phase"] or {}
    op=tables.read("excel/character_table.json")[VEEN]
    trait=trait_board(op,phase,card)
    talents=[active_board(talent_rows(phase,i,str(i+1),t["candidates"]),card["elite"],card["level"],card["potential"])
             for i,t in enumerate(op["talents"][:2])]
    board={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    args=(board,card["panel_attributes"],trait,talents)
    volley=_veen(3,"volley",dict(scene,targets=primary),*args)
    cached=_veen(3,"projectile_release",dict(projectile_kind="primary",has_merged_attack_buff=_flag(scene,"has_merged_attack_buff")),*args)["cached_atk_raw"]
    evidence=caster_prefab_evidence(tables,VEEN,3,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    attack,=[a["fields"] for a in evidence["character"]["components"] if a["script"]=="MultiChargeRangedAttack" and a["path"]=="Modes/S3/Body/Attack"]
    key=attack["_projectileKey"]
    settings=set()
    for variant in evidence["projectiles"][key].values():
        rows=variant["components"]
        move,=[c["fields"] for c in rows if c["script"]=="BouncedAdvancedMovement"]
        grab,=[c["fields"] for c in rows if c["script"]=="BounceHitBehaviour"]
        circle,=[c["fields"] for c in rows if c["script"]=="CircleCollider2D"]
        if (not grab["_onlyCheckHitWhenReachTarget"] or not grab["_sortTargetsByHitCountAsc"] or grab["_bounceTimesAsDamageTimes"]
                or not grab["_notApplyBounceAtkScale"] or grab["_dealHitWithOtherSelector"] or move["_followLogicProjectile"]
                or move["_delayAfterReached"] or move["_delayToStart"] or any(circle["m_Offset"].values())):
            raise ValueError("维伊三技能弹射选项与已核实实包不符")
        settings.add((move["_speed"],move["_speedAfterFirstReach"],circle["m_Radius"]))
    (speed,bounce_speed,radius),=settings
    target=primary[0]["id"]
    remaining=volley["bounce_count"]
    pos=scene["origin"]
    delay=0.
    count=Counter()
    pending_dot={}
    dt=tables.fixed_timestep()
    step=int(dt*ONE)
    dot_duration=_raw_f32(talents[1]["attack@duration"])
    limit=int(talents[1]["attack@max_stack_cnt"])
    for k,row in targets.items():
        if not row["has_veen_dot"]:
            continue
        prior=row.get("initial_warfare")
        if prior is None:
            return dict(status="requires_existing_warfare_stack_and_refresh_state",through_frame=start,events=[],hp_q32=hp)
        layers,life,timer=(prior[key] for key in ("stacks","remaining_lifetime_q32","trigger_remaining_q32"))
        if any(type(value) is not int for value in (layers,life,timer)) or not(1<=layers<=limit and 0<life<=dot_duration and 0<timer<=ONE):
            raise ValueError("初始战争技艺需要本干员来源的实际层数、剩余 Q32.32 寿命及下一次触发时间")
        clock=RecoveryTimer(ONE)
        clock.remaining=timer
        pending_dot[k]=dict(clock=clock,life=life,layers=layers)
    log=[dict(frame=start,kind="main_projectile_release",target=target,projectile=key,cached_atk_q32=cached,
              ammo_after=volley["ammo"],remaining_bounces=remaining,ammo_exhausted=volley["stop_skill"])]
    status="horizon"
    for f in range(start+1,end+1):
        # Enemies already exist when the projectile is born, so their owner
        # tick (including BuffContainer) is earlier than this projectile tick.
        for k,mark in list(pending_dot.items()):
            mark["life"]-=step
            if mark["clock"].advance(step):
                packet=ordinary_arts_hit(attack_raw=_raw_f32(talents[1]["attack@value"]*mark["layers"]),
                                         resistance_raw=_raw_f32(_number(targets[k],"res",minimum=-1000)),hp_raw=hp[k])
                hp[k]=packet.hp_after_raw
                log.append(dict(frame=f,kind="warfare_dot",target=k,stacks=mark["layers"],damage_q32=packet.damage_raw,hp_q32=hp[k]))
            if hp[k]<=HP_EPSILON or mark["life"]<=0:
                del pending_dot[k]
                if hp[k]>HP_EPSILON:
                    log.append(dict(frame=f,kind="dot_expired",target=k))
        if target is None:
            if pending_dot:
                continue
            status="volley_and_dots_finished"
            break
        if hp[target]<=HP_EPSILON:
            status="requires_dead_trace_target_lifecycle"
            break
        if delay>single(1e-5):
            delay=single(delay-dt)
            continue
        r=targets[target]
        sample=ordinary_trace_step(pos,r["hit_mount"],effective_dt=dt,round_position=True,speed=speed)
        pos=sample.position
        if not sample.reached:
            continue
        scale=board["attack@bounce_atk_scale"] if count else board["attack@base_atk_scale"]
        packet=ordinary_arts_hit(attack_raw=mul(cached,_raw_f32(scale)),resistance_raw=_raw_f32(_number(r,"res",minimum=-1000)),hp_raw=hp[target])
        hp[target]=packet.hp_after_raw
        log.append(dict(frame=f,kind="bounce_hit" if count else "main_projectile_hit",target=target,attack_scale=scale,
                        damage_q32=packet.damage_raw,hp_q32=hp[target]))
        count[target]+=1
        if hp[target]>HP_EPSILON:
            mark=pending_dot.get(target)
            if mark is None:
                pending_dot[target]=dict(clock=RecoveryTimer(ONE),life=dot_duration,layers=1)
                log.append(dict(frame=f,kind="apply_dot_and_exclude_from_veen_attacks",target=target,stacks=1))
            else:
                # Incoming OnStart copies the capped damage into the first buff.
                # OverrideGroup changes that buff's stack and lifetime only.
                mark["layers"]=min(limit,mark["layers"]+1)
                mark["life"]=max(mark["life"],dot_duration)
                log.append(dict(frame=f,kind="warfare_stack_refreshed",target=target,stacks=mark["layers"],
                                next_trigger_remaining_q32=mark["clock"].remaining))
        target=None
        if remaining:
            target,unknown=_bounce_pool(targets,hp,r,count,radius)
            if unknown:
                status=unknown
                break
            if target is not None:
                remaining-=1
                speed=bounce_speed
                pos=r["hit_mount"]
                delay=single(board["attack@delay"])
                log.append(dict(frame=f,kind="change_bounce_target",target=target,remaining_bounces=remaining,speed=speed,delay=delay))
        if target is None and not pending_dot:
            status="volley_and_dots_finished"
            break
    return dict(status=status,through_frame=f,events=log,hp_q32=hp,ammo_after=volley["ammo"],remaining_bounces=remaining,
                conditions=["发射前的实际能量、弹药和转置加攻状态；本轮发射后其他攻击不再干预",
                            "等嘲讽静止普通靶，从发射至最终命中都可选，受击挂点按所给坐标"])

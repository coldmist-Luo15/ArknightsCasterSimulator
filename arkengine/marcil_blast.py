"""Marcille S3 explosion and high-ground shards after a completed V079 chant."""
from fractions import Fraction
import json

from .attack_range import circle_in_range,range_cells
from .caster_cast import _after_spell_clock
from .caster_ranges import skill_range_cells
from .damage import HP_EPSILON,ordinary_arts_hit
from .fixed import ONE,mul
from .mystic_timing import fixed_wait_frames
from .skill_recovery import RecoveryTimer
from .volley_damage import _raw_f32


def _fields(asset,script):
    c,=(c for c in asset["components"] if c["script"]==script and c["enabled"] and c["active"])
    return c["fields"]


def blast_clock(tables,evidence,chant,*,mana_before_first_payment=None):
    commit=chant.get("cast_frame")
    if commit is None:
        return dict(events=[],spell_frame=None)
    bb={r["key"]:r["value"] for r in evidence["skill_blackboard"]}
    raw=evidence["skill"]
    outer=_fields(raw,"EmptyAnimatedAbility")
    hook=_fields(raw,"BuffToOwnerDuringAbility")
    buff,=hook["_buffs"]
    logic=evidence["projectiles"]["projectile_chr_marcil_s3"]["logic"]
    movement=_fields(logic,"AttachToTarget")
    body=_fields(logic,"SimpleProjectile")
    child,=(c["fields"] for c in raw["components"] if c["script"]=="ProjectileToTileAbility" and c["path"]=="Boom")
    if (outer["_timeMode"]!=2 or outer["_animKey"] or outer["_waitForAttackEvent"] or
            (hook["_startEvent"],hook["_endEvent"])!=(4,3) or child["_waitForAttackEvent"] or child["_preDelay"] or
            child["_useCachedAtkOnly"] or child["_transferSource"] or child["_waitForProjectileInvalid"] or
            body["_randomDelayToBorn"] or body["_hitNumType"]!=2 or body["_lifeTimeType"]!=1 or body["_stopAfterFirstHit"] or
            not movement["_immediatelyReach"] or movement["_delayAfterReached"] or movement["_delayToStart"] or
            not _fields(logic,"HitBehaviour")["_onlyCheckHitWhenReachTarget"] or
            not _fields(logic,"TileSelectorHitBehaviour")["_onlyCheckHitWhenStop"]):
        raise ValueError("玛露西尔三技能的施法时序或弹体判定选项改变")
    template=evidence["templates"][buff["templateKey"]]["eventToActions"]
    trigger,cost=template["ON_BUFF_START"][:2]
    repeat=template["ON_BUFF_TRIGGER"][0]
    if (trigger["_abilityName"]!="Boom" or not trigger["_castDirectly"] or not cost["_isMinis"] or
            repeat["_conditionNode"]["_condType"]!="GE" or not buff["waitFirstTriggerInterval"] or buff["firstTriggerInterval"]!=-1):
        raise ValueError("玛露西尔三技能触发/扣除魔力顺序改变")
    _,_,no_extra,restore,finish=template["ON_BUFF_FINISH"]
    finish_buff=finish["_buff"]
    stop,=evidence["templates"][finish_buff["templateKey"]]["eventToActions"]["ON_BUFF_FINISH"]
    extend,=evidence["templates"]["marcil_3_extra_chant"]["eventToActions"]["ON_BUFF_START"]
    if (outer["_cooldownKey"]!="duration" or "duration" in bb or outer["_escapeTime"]
            or no_extra["_var"]!="no_extra" or restore["_value"]!=outer["_cooldown"]
            or extend["_value"]!=99 or extend["_useOtherAbility"] or restore["_useOtherAbility"]
            or finish_buff["lifeTimeType"]!="LIMITED" or finish_buff["durationKey"]!="none"
            or not stop["_useCurrentAbility"] or not stop["_stopAffect"] or stop["_emitAttackFinishOnly"]):
        raise ValueError("玛露西尔三技能的结束 Buff 或中止路径变化")
    first_cost=int(bb[cost["_spString"]])
    next_cost=int(bb[repeat["_conditionNode"]["_checkKey"]])
    step=int(tables.fixed_timestep()*ONE)
    budget=fixed_wait_frames(Fraction(_raw_f32(outer["_preDelay"]),step))
    life=_raw_f32(body["_lifeTime"])
    stop_ticks=(life+step-1)//step
    full=any(e["kind"]=="extra_chant_succeeded" for e in chant["events"])
    direct_input=any(e["kind"]=="additional_chant_stopped" for e in chant["events"])
    spell=commit+budget-int(direct_input)
    mana=chant.get("mana_at_commit") if mana_before_first_payment is None else mana_before_first_payment
    if mana is not None and (type(mana) is not int or not first_cost<=mana<=bb["mana_max"]):
        raise ValueError("施法时魔力需满足最低需求并位于上限之内")
    events=[]
    def request(frame,amount,from_objects):
        nonlocal mana
        events.append(dict(kind="boom_requested",frame=frame))
        if mana is not None:
            mana-=amount
        events.append(dict(kind="spend_mana",frame=frame,cost=amount,remaining=mana,
                           phase="OBJECTS" if from_objects else "COROUTINES"))
        # The first request runs inside the main ability's spell coroutine. Repeats are
        # dispatched in the owner's buff tick, before the same frame's fixed coroutines.
        launch=frame+int(not from_objects)
        events.extend((dict(kind="projectile_release",frame=launch,projectile=child["_projectileKey"]),
                       dict(kind="explosion_reach",frame=launch+1),
                       dict(kind="high_tile_shards",frame=launch+stop_ticks)))
    request(spell,first_cost,False)
    timer=RecoveryTimer(_raw_f32(bb["interval"]))
    f=spell
    if full and mana is not None:
        while True:
            f+=1
            if not timer.advance(step):
                continue
            if mana>=next_cost:
                request(f,next_cost,True)
            if mana<next_cost:
                break
    ability_finish=first_unblocked=None
    finish_phase=None
    if not full:
        post=_after_spell_clock(spell-commit,outer["_cooldown"],step,same_frame_resume=direct_input)
        ability_finish=commit+post["end"]
        finish_phase="COROUTINES"
        first_unblocked=ability_finish+2
    elif mana is not None:
        life=_raw_f32(finish_buff["lifeTime"])
        ability_finish=f+(life+step-1)//step
        finish_phase="OBJECTS"
        first_unblocked=ability_finish+1
    return dict(spell_frame=spell,events=sorted(events,key=lambda e:e["frame"]),mana_after=mana,
                extra_chant_completed=full,requires_mana_for_additional_count=full and mana is None,
                boom_buff_finish_frame=(f if mana is not None or not full else None),ability_finish_frame=ability_finish,
                ability_finish_phase=finish_phase,first_unblocked_recovery_tick=first_unblocked)


def blast_geometry(tables,card,evidence,event):
    field=skill_range_cells(tables,card,event)
    cells=set(field["cells"])
    highs={tuple(point) for point in event["high_tiles"]}
    if any(len(point)!=2 or any(type(v) is not int for v in point) or not 0<=point[0]<event["rows"]
           or not 0<=point[1]<event["cols"] for point in highs):
        raise ValueError("高台列表含无效坐标")
    logic=evidence["projectiles"]["projectile_chr_marcil_s3"]["logic"]
    effect=_fields(logic,"ProjectileActionBehaviour")
    action,visual=json.loads(effect["_actions"]["SerializedState"])
    if effect["_ev"]!=2 or not action["$type"].endswith("+CreateBuffOnTileInRange") or action["_buffs"][0]["buffKey"]!="stun":
        raise ValueError("爆炸高台的击中地块回调改变")
    around=tables.read("excel/range_table.json")[action["_rangeId"]]
    shard_cells=set()
    for r,c in cells & highs:
        shard_cells.update(range_cells(around,dict(row=r,col=c,direction="RIGHT"),event["rows"],event["cols"]))
    return field,cells,highs,shard_cells


def run_marcil_blast(tables,card,evidence,event,*,start=None,positions=None,horizon=None,on_shard=None,receiver_hp=None):
    from .caster_start import resolve_cast_start
    if start is None:
        start=resolve_cast_start(tables,evidence,frame=event["frame"],sp=event["mana"],
            facing=event.get("facing","Front"),direction=event["placement"]["direction"],
            chant_options={k:event[k] for k in ("repress_frame","interrupt_frame") if k in event})
    if start["cast_frame"] is None:
        return dict(start=start,hits=[],stuns=[])
    clock=start["blast_clock"]
    field,cells,highs,shard_cells=blast_geometry(tables,card,evidence,event)
    board={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    targets=event["stationary_targets"] if positions is None else event["targets"]
    if len({x["id"] for x in targets})!=len(targets) or any(type(x["eligible"]) is not bool or
            type(x["stun_immune"]) is not bool or (positions is None and (not 0<=x["row"]<event["rows"] or not 0<=x["col"]<event["cols"]))
            or x["radius"]<0 or type(x["hp_q32"]) is not int or x["hp_q32"]<=HP_EPSILON for x in targets):
        raise ValueError("需要唯一、位置和半径有效、存活且说明索敌及眩晕免疫的固定目标")
    hp={x["id"]:x["hp_q32"] for x in targets} if receiver_hp is None else receiver_hp
    scale=_raw_f32(board["atk_scale"])
    drops=[e for e in clock["events"] if e["kind"]=="explosion_reach"]
    values=event.get("attack_q32_by_explosion")
    check_window=None
    if values is None:
        talent=next(r for r in card["base_talents"] if r["prefabKey"]=="1")
        attrs={b["key"]:b["value"] for b in talent["blackboard"]}
        panel=_raw_f32(card["panel_attributes"]["atk"])
        powered=mul(panel,ONE+_raw_f32(attrs["atk"]))
        values=[powered]*len(drops)
        if clock["mana_after"] is None:
            raise ValueError("需要施法提交时的实际魔力")
        if clock["mana_after"]<attrs["sp"]:
            checker=evidence["templates"]["marcil_t[checker]"]["eventToActions"]["ON_BUFF_LATE_ENABLE"][0]
            gate=checker["_conditionNode"]
            if gate["_condType"]!="GE" or gate["_checkKey"]!="sp" or checker["_failNodes"][0]["_buffKey"]!="marcil_t[power]":
                raise ValueError("才女增攻的晚更新条件改变")
            pay=[e for e in clock["events"] if e["kind"]=="spend_mana" and e["remaining"]<attrs["sp"]]
            if len(pay)!=1:
                raise ValueError("多次低于天赋门槛的施放需要各发实时攻击力")
            # BuffContainer.Tick re-enumerates for OnLateTick after ordinary
            # buff ticks. This owner's OBJECTS pass precedes the newer projectile.
            first_check=pay[0]["frame"]+int(pay[0]["phase"]=="COROUTINES")
            check_window=[first_check,drops[-1]["frame"]]
            seen=event.get("power_checker_late_frames")
            first_tick=start.get("talent_first_update_frame",event.get("talent_first_update_frame"))
            if seen is None and first_tick is not None:
                parent,=(c["fields"]["_buffs"][0] for c in evidence["character"]["components"]
                         if c["path"]=="Others/Direction/Talents/1" and c["script"]=="PassiveBuffAbility")
                child_hook,=(c["fields"] for c in evidence["skill"]["components"] if c["path"]=="Boom" and c["script"]=="BuffToOwner")
                if (parent["buffKey"]!="marcil_t" or parent["triggerLifeType"]!=2 or not parent["waitFirstTriggerInterval"]
                        or parent["firstTriggerInterval"]!=0 or child_hook["_runActionOnEvent"]!=3
                        or child_hook["_buffs"][0]["buffKey"]!="marcil_t[checker]"):
                    raise ValueError("才女的首次或周期检查及爆炸收招检查发生变化")
                if type(first_tick) is not int or not 0<=first_tick<=pay[0]["frame"]:
                    raise ValueError("需要本次部署天赋首次对象更新的逻辑帧")
                timer=RecoveryTimer(_raw_f32(attrs["interval"]))
                timer.remaining=0
                step=_raw_f32(tables.fixed_timestep())
                # Boom's post-delay waits at least one fixed resume after release.
                # Its ON_CAST_END cannot remove the power buff before its own impact.
                seen=[f for f in range(first_tick,check_window[1]+1) if timer.advance(step) and f>=first_check]
            if seen is None:
                low=dict(event,attack_q32_by_explosion=[*values[:-1],panel])
                high=dict(event,attack_q32_by_explosion=values)
                return dict(status="requires_last_power_check_phase",power_check_late_frame_window=check_window,
                            start=start,clock=clock,scenarios={
                               "checked_after_spending":run_marcil_blast(tables,card,evidence,low,start=start,positions=positions),
                               "no_check_before_impact":run_marcil_blast(tables,card,evidence,high,start=start,positions=positions)})
            if not isinstance(seen,list) or any(type(f) is not int or f<0 for f in seen):
                raise ValueError("需给才女检查器在晚更新阶段实际生效的逻辑帧列表")
            if any(check_window[0]<=f<=check_window[1] for f in seen):
                values[-1]=panel
    if len(values)!=len(drops) or any(type(v) is not int or v<0 for v in values):
        raise ValueError("实际攻击力数量需对应每次爆炸")
    hits=[]
    stuns=[]
    index=0
    born=None
    interrupted=False
    for e in clock["events"]:
        kind=e["kind"]
        frame=e["frame"]
        if horizon is not None and frame>horizon:
            break
        if kind=="projectile_release":
            born=frame
        if kind not in ("explosion_reach","high_tile_shards"):
            continue
        current=positions(frame,born,hp) if callable(positions) else None
        if callable(positions) and current is None:
            break
        for x in targets:
            key=x["id"]
            if not x["eligible"] or hp[key]<=HP_EPSILON:
                continue
            if positions is None:
                p=x["row"],x["col"]
            else:
                p=(current if callable(positions) else positions[frame]).get(key)
            if p is None:
                continue
            if kind=="explosion_reach" and circle_in_range(*p,x["radius"],cells,box_query=True):
                packet=ordinary_arts_hit(attack_raw=mul(values[index],scale),hp_raw=hp[key],resistance_raw=x["res_q32"])
                hp[key]=packet.hp_after_raw
                hits.append(dict(frame=frame,id=key,damage_q32=packet.damage_raw,hp_after_q32=hp[key]))
            if kind=="high_tile_shards" and not x["stun_immune"] and circle_in_range(*p,x["radius"],shard_cells,box_query=True):
                stuns.append(dict(frame=frame,id=key,seconds=board["stun"]))
                if on_shard is not None and not on_shard(key,frame,born,board["stun"]):
                    interrupted=True
        if interrupted:
            break
        if kind=="explosion_reach":
            index+=1
    return dict(start=start,clock=clock,center=field["center"],high_tiles_hit=sorted(cells & highs),
                shard_cells=sorted(shard_cells),hits=hits,stuns=stuns,hp_after_q32=hp,power_check_late_frame_window=check_window,
                conditions=["stationary_recipients" if positions is None else "supplied_stage_motion",
                            "no_unlisted_buffs_or_external_damage","uninterrupted_after_commit"])

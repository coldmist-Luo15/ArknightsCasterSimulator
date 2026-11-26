"""Marcille's V079 suspendable chant and the ordinary/S1 attack groups."""
from collections import defaultdict
from fractions import Fraction

from .attack_range import circle_in_range,range_cells
from .baked_mount import single
from .caster_catalog import resolve_caster_loadout
from .caster_first_spell import _selected_clip, attack_key_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_ranges import skill_range_cells
from .caster_runtime import default_attack,phalanx_default
from .damage import HP_EPSILON, ordinary_arts_hit
from .eyja_ignition import ignition_circle_overlap
from .fixed import ONE, mul
from .marcil_chant import UID, resolve_chant_start
from .marcil_deploy import _birth,companion_deck_buff
from .marcil_mana import entry_mana
from .module_effects import active_board, equipped_phase, talent_rows
from .mystic_timing import fixed_wait_frames
from .phalanx_skills import CARN
from .continuous_ranged import prepare_continuous_ranged
from .phalanx_wave import run_with_phalanx_waves
from .marcil_blast import blast_geometry
from .caster_start import resolve_cast_start
from .skill_recovery import RecoveryTimer,NaturalSkillPoints
from .mostima_aura import MOSTIMA,aura_profile,effective_period,set_period
from .stage_deployment import RangedReentries
from .stage_channel import object_position_frame
from .volley_damage import _raw_f32


def _attack_groups(evidence):
    components = {c["id"]: c for c in evidence["character"]["components"]}
    groups = {}
    for mode, branches in (("Default", ("Power", "Normal")), ("S1", ("Power", "Normal", "Heal"))):
        path = f"Modes/{mode}/Body/Attack"
        group, = (c["fields"] for c in components.values()
                  if c["path"] == path and c["script"] == "AbilitySelectableGroup")
        if (not group["_resetSubAbilities"] or not group["_resetSubAbilityCooldown"] or
                not group["_clearInternalCooldownWhenFinish"] or group["_selectAbilitySequentially"]):
            raise ValueError("玛露西尔攻击组的切换或子能力冷却已变化")
        outputs = {}
        for cfg in group["_abilityConfigs"]:
            ability = components[cfg["_ability"]["m_PathID"]]
            trigger = components[cfg["_trigger"]["m_PathID"]]
            name = ability["path"].rsplit("/", 1)[-1]
            fields = ability["fields"]
            if (fields["_animKey"], fields["_waitForAttackEvent"], fields["_timeMode"],
                    fields["_resetCdStrategy"], fields["_preDelay"]) != ("Attack", 1, 0, 1, 0):
                raise ValueError("玛露西尔攻击动作的关键帧或冷却路径已变化")
            if name == "Heal":
                selector, = (c["fields"] for c in components.values()
                             if c["path"] == trigger["path"] and c["script"] == "AdvancedSelector")
                if (ability["script"], fields["_selectTargetSource"], fields["_selectTargetTiming"],
                        selector["_targetSide"], selector["_postFilter"], selector["_excludeOwner"]) != ("Heal", 2, 0, 1, 3, 0):
                    raise ValueError("才女的实力的治疗锁靶或受伤比例筛选已变化")
                outputs[name] = None
            else:
                selector = components[fields["_selector"]["m_PathID"]]
                collider, = (c["fields"] for c in components.values()
                             if c["path"] == selector["path"] and c["script"] == "CircleCollider2D")
                if (ability["script"], fields["_selectTargetSource"], fields["_selectTargetTiming"],
                        fields["_damageType"], fields["_atkScale"]) != ("MeleeAttack", 3, 1, 2, 1):
                    raise ValueError("玛露西尔的输入点溅射已变化")
                if name == "Power" and not trigger["fields"]["_checkHasSp"]:
                    raise ValueError("魔力增强攻击缺少魔力门槛")
                outputs[name] = collider["m_Radius"]
        if tuple(outputs) != branches:
            raise ValueError("玛露西尔的攻击与治疗优先级已变化")
        groups[mode] = outputs
    return groups


def run_marcil_toggle(tables, request):
    return run_with_phalanx_waves(_simulate,tables,request)


def _simulate(tables,request,releases):
    card,stage,place,event,world=prepare_continuous_ranged(tables,request,UID,releases)
    deploy=place
    slot=card["skill"]
    support=[a for a in request["actions"] if a["operator_id"]!=UID and a.get("kind","deploy")=="deploy"]
    start,end=place["frame"],request["end_frame"]
    inputs=defaultdict(list)
    for action in request["actions"]:
        if action["operator_id"]==UID and action.get("kind")=="skill":
            inputs[action["frame"],action.get("phase","after_tick")].append(action)
    withdrawals={a["frame"]:a.get("phase","after_tick") for a in request["actions"] if a.get("kind")=="withdraw" and a["operator_id"]==UID}
    stop_chants=sorted(a["frame"] for a in request["actions"] if a["operator_id"]==UID and a.get("kind") in ("chant_stop","skill"))
    repeats=RangedReentries.for_request(tables,stage,world,request,start)
    card_events=[dict(kind="deploy",frame=start)] if world.present else []
    support_withdrawals=defaultdict(list)
    for a in request["actions"]:
        if a["operator_id"]!=UID and a.get("kind")=="withdraw":
            support_withdrawals[a["frame"]].append(a["operator_id"])
    supporter=next((p for p in request["squad"] if p["operator_id"]==MOSTIMA),None)
    aura=None
    if supporter and any(a["operator_id"]==MOSTIMA for a in support):
        source=resolve_caster_loadout(tables,MOSTIMA,supporter["skill"],supporter["module"],skill_level=supporter["skill_level"],module_level=supporter["module_level"])
        aura=aura_profile(tables,source)
    allies=request.get("marcil_allies",())
    mana_input=request.get("marcil_entry_mana")
    facing=event["view"]
    evidence=caster_prefab_evidence(tables,UID,slot,skill_level=card["skill_level"],module_id=card["module"],module_level=card["module_level"])
    groups=_attack_groups(evidence)
    board={b["key"]:b["value"] for b in card["skill_row"]["blackboard"]}
    if slot==1:
        use=evidence["templates"]["marcil_s_1"]["eventToActions"]
        spend,_=use["ON_OUTPUT_ATK_OR_HEAL"]
        check,_=use["ON_BUFF_TRIGGER"]
        buff=next(b for c in evidence["skill"]["components"] if c["script"]=="ActiveBuffAbility" for b in c["fields"]["_buffs"] if b["buffKey"]=="marcil_s_1")
        cost,minimum=int(board[spend["_spString"]]),int(board[check["_checkKey"]])
    else:
        high=[(r,c) for r in range(event["rows"]) for c in range(event["cols"]) if world.scene.tile(r,c)["heightType"]=="HIGHLAND"]
        _,blast_cells,_,shard_cells=blast_geometry(tables,card,evidence,dict(event,high_tiles=high))
    candidates = [r for r in card["base_talents"] if r["prefabKey"] == "1"]
    talent = active_board(talent_rows(equipped_phase(tables, card), 0, "1", candidates),
                          card["elite"], card["level"], card["potential"])
    mana, mana_source, _ = entry_mana(tables, card, evidence,
        dict(frame=start) if mana_input is None else dict(frame=start, entry_mana=mana_input), None)
    initial_mana = mana
    dt = tables.fixed_timestep()
    step = int(dt * ONE)
    recovery=NaturalSkillPoints(mana,int(board["skill_cost_min_sp"]),int(board["mana_max"]),None)
    companion=companion_deck_buff(tables,card,request["squad"])
    for uid,bonus in companion["affected"].items():
        if uid in world.receiver.defenses:
            world.receiver.defenses[uid]=mul(world.receiver.defenses[uid],ONE+bonus["defense_multiplier_delta_q32"])
    panel=dict(card["panel_attributes"])
    if UID in companion["affected"]:
        panel["attackSpeed"]+=companion["affected"][UID]["attack_speed_add_q32"]/ONE
    interval = single(single(panel["baseAttackTime"]) * 100 / single(panel["attackSpeed"]))
    delay, _ = attack_key_clock(tables, evidence, facing, "Attack", interval)
    clip, _ = _selected_clip(tables, evidence, "Attack", facing)
    duration = fixed_wait_frames(single(min(interval, clip["duration"]) / dt))
    cooldown = max(duration, fixed_wait_frames(single(interval / dt)))
    grid_args = dict(placement=place, rows=event["rows"], cols=event["cols"])
    if slot==1:
        grids={power:skill_range_cells(tables,card,dict(grid_args,talent_powered=power))["cells"] for power in (False,True)}
    else:
        normal_area=range_cells(card["base_range"],place,event["rows"],event["cols"])
        grids={False:normal_area,True:normal_area}
    radii = {key: tables.enemy_circle(row["prefab"]) for key, row in world.stats.items()}
    hp = {key: _raw_f32(row["hp"]) for key, row in world.stats.items()}
    resistance = {key: _raw_f32(row["res"]) for key, row in world.stats.items()}
    self_max_hp = _raw_f32(panel["maxHp"])
    friends = {UID: dict(id=UID, row=place["row"], col=place["col"], hp_q32=self_max_hp,
                         max_hp_q32=self_max_hp, frame=start, healable=True)}
    for ally in support:
        key=ally["operator_id"]
        if not any(e["kind"]=="deployed" and e["operator_id"]==key for e in stage["events"]):
            continue
        partner=world.cards[key]
        max_hp=_raw_f32(partner["panel_attributes"]["maxHp"])
        friends[key]=dict(id=key,row=ally["row"],col=ally["col"],hp_q32=max_hp,max_hp_q32=max_hp,frame=ally["frame"],healable=True)
        if key==CARN:
            cfg=next(p for p in request["squad"] if p["operator_id"]==key)
            full=resolve_caster_loadout(tables,CARN,cfg["skill"],cfg["module"],skill_level=cfg["skill_level"],module_level=cfg["module_level"])
            standby=phalanx_default(tables,full,default_attack(tables,CARN),ally,end,[])
            bonus=standby["ordinary_trait_modifiers"]["defense_ratio_added"]
            world.receiver.defenses[key]=mul(world.receiver.defenses[key],ONE+_raw_f32(bonus))
    for ally in allies:
        key = ally["id"]
        if (key in friends or not 0 <= ally["row"] < event["rows"] or not 0 <= ally["col"] < event["cols"]
                or not 0 <= ally["hp_q32"] <= ally["max_hp_q32"] or not start <= ally["frame"] <= end):
            raise ValueError("治疗接收者需要唯一身份、地图格和有效的生命值快照")
        friends[key] = dict(ally, healable=ally.get("healable", True))

    log, hits, heals = [dict(kind="deploy", frame=start, mana=mana)], [], []
    incoming=world.incoming
    if incoming:
        incoming.live_projectiles=True
    blast_events=defaultdict(list)
    resume=last_clock=None
    stuns={}
    walks={}
    power_clock=RecoveryTimer(_raw_f32(talent["interval"]))
    power_clock.remaining=0
    born_end = start + _birth(tables, card, evidence, facing)["born_exit_update"]
    poll = born_end + 1
    mode, chant, pending = "Default", None, None
    mana_timer, checked_mana, check_at, attack_ends = None, mana, None, None

    def switch(frame, next_mode, reason):
        nonlocal mode, pending, poll, mana_timer, attack_ends
        mode, pending, attack_ends, poll = next_mode, None, None, frame + 1
        mana_timer = RecoveryTimer(_raw_f32(buff["triggerInterval"])) if next_mode == "S1" else None
        log.append(dict(kind="skill_mode", frame=frame, mode=mode, reason=reason, mana=mana))

    def button(frame, phase):
        nonlocal chant, pending, attack_ends, resume,last_clock
        if not world.present:
            return
        if slot==3:
            if chant is not None:
                return
            stop=next((f for f in stop_chants if f>frame),None)
            options={"repress_frame":stop} if stop is not None else {}
            attempt=resolve_cast_start(tables,evidence,frame=frame,phase=phase,sp=mana,facing=facing,direction=place["direction"],
                                       owner_state="born" if frame<born_end else "idle",chant_options=options)
            if attempt["cast_frame"] is None:
                log.append(dict(kind="skill_button_rejected",frame=frame,reason=attempt["status"]))
                return
            chant=attempt
            pending=None
            last_clock=attempt["blast_clock"]
            resume=last_clock["first_unblocked_recovery_tick"]
            projectile_born=None
            for e in attempt["events"]:
                if e["kind"]=="projectile_release":
                    projectile_born=e["frame"]
                if e["kind"] in ("explosion_reach","high_tile_shards"):
                    e=dict(e,projectile_born=projectile_born)
                blast_events[e["frame"]].append(e)
            log.append(dict(kind="chant_start",frame=frame,mana=mana))
            return
        if mode == "S1":
            switch(frame, "Default", "manual_suspend")
        elif chant is not None:
            log.append(dict(kind="skill_button_rejected", frame=frame, phase=phase, reason="main_chant"))
        else:
            result = resolve_chant_start(tables, evidence, frame=frame, phase=phase, facing=facing,
                                         owner_state="born" if frame < born_end else "idle", mana=mana)
            if result["cast_frame"] is None:
                log.append(dict(kind="skill_button_rejected", frame=frame, phase=phase, reason=result["status"]))
                return
            chant, pending, attack_ends = result, None, None
            log.append(dict(kind="chant_start", frame=frame, phase=phase, mana=mana))

    def release(frame):
        nonlocal pending, mana, check_at
        _, name, target, point = pending
        pending = None
        mode_now = mode
        attack = mul(_raw_f32(panel["atk"]), ONE + (_raw_f32(talent["atk"]) if powered else 0)
                     + (_raw_f32(board["atk"]) if mode_now == "S1" else 0))
        if name == "Heal":
            ally = friends[target]
            if ally["hp_q32"] <= HP_EPSILON:
                return
            amount = min(attack, ally["max_hp_q32"] - ally["hp_q32"])
            ally["hp_q32"] += amount
            if target==UID:
                world.hp=ally["hp_q32"]
            if incoming is not None and target in incoming.units:
                incoming.units[target]["hp"] = ally["hp_q32"]
            row = dict(kind="skill_heal", frame=frame, target=target, heal_q32=amount,
                       attack_q32=attack, ally_hp_q32=ally["hp_q32"])
            heals.append(row)
            log.append(row)
        else:
            present = {key: world.positions[frame][key] for key in world.active[frame] if hp[key] > HP_EPSILON}
            row, col = present.get(target, point)
            radius = groups[mode_now][name]
            for key, (r, c) in present.items():
                if not ignition_circle_overlap(dict(row=row, col=col), dict(row=r, col=c), radius, radii[key]):
                    continue
                hit = ordinary_arts_hit(attack_raw=attack, resistance_raw=resistance[key], hp_raw=hp[key])
                hp[key] = hit.hp_after_raw
                result = dict(kind="skill_hit" if mode_now == "S1" else "ordinary_hit", frame=frame,
                              target=key,id=key, attack_q32=attack, damage_q32=hit.damage_raw, hp_after_q32=hp[key], radius=radius)
                if slot==1:
                    hits.append(result)
                log.append(result)
        if mode_now == "S1":
            # The output event belongs to the committed ability, once per spell.
            mana = max(0, mana - cost)
            check_at = frame + 1
            log.append(dict(kind="mana_spent", frame=frame, ability=name, cost=cost, mana=mana))

    def leave(frame,manual):
        # ON_OWNER_FINISH copies mana before the pending chant buff writes clearSp.
        card_events.append(dict(kind="finish",frame=frame,mana=mana))
        log.append(dict(kind="mana_saved",frame=frame,mana=mana))
        if repeats:
            repeats.leave(frame,UID,manual=manual)

    through, stop = end, "horizon"
    for frame in range(start + 1, end + 1):
        a=repeats.advance(frame,log) if repeats else None
        if a:
            place=a
            facing="Back" if a["direction"]=="UP" else "Front"
            mana=entry_mana(tables,card,evidence,dict(frame=frame,card_events=card_events),None)[0]
            card_events.append(dict(kind="deploy",frame=frame))
            log.append(dict(kind="deploy",frame=frame,mana=mana))
            friends[UID].update(row=a["row"],col=a["col"],frame=frame,hp_q32=self_max_hp)
            delay,_=attack_key_clock(tables,evidence,facing,"Attack",interval)
            clip,_=_selected_clip(tables,evidence,"Attack",facing)
            duration=fixed_wait_frames(single(min(interval,clip["duration"])/dt))
            cooldown=max(duration,fixed_wait_frames(single(interval/dt)))
            born_end=frame+_birth(tables,card,evidence,facing)["born_exit_update"]
            mode,pending,chant,poll="Default",None,None,born_end+1
            mana_timer=resume=attack_ends=check_at=None
            checked_mana=mana
            power_clock.remaining=0
            recovery.timer=None
            event["placement"]=a
            if slot==1:
                grids={p:skill_range_cells(tables,card,dict(grid_args,placement=a,talent_powered=p))["cells"] for p in (False,True)}
            else:
                area=range_cells(card["base_range"],a,event["rows"],event["cols"])
                grids={False:area,True:area}
                _,blast_cells,_,shard_cells=blast_geometry(tables,card,evidence,dict(event,high_tiles=high))
        for _ in inputs[frame, "input"]:
            button(frame, "input")
        if withdrawals.get(frame) in ("input","before_coroutines") and world.present:
            leave(frame,True)
            world.remove_owner(frame,log,"withdraw")
        was_present=world.present
        world.receive(frame,hp,log,hit_label="incoming_hit")
        if was_present and not world.present:
            leave(frame,False)
        for uid in support_withdrawals.get(frame,()):
            if incoming:
                incoming.withdraw(uid)
            if uid in friends:
                friends[uid]["hp_q32"]=0
        friends[UID]["hp_q32"]=world.hp
        if incoming:
            for key in world.cards.keys() & friends.keys():
                friends[key]["hp_q32"]=incoming.units[key]["hp"] if key in incoming.units else 0
        for key,walk in tuple(walks.items()):
            if not hp[key]:
                del walks[key]
                continue
            if frame<=stuns[key]:
                world.positions[frame][key]=walk.position
                world.active[frame].add(key)
            else:
                resumed=world.step_walk(tables,walk,key,frame,0)
                if resumed is None:
                    del walks[key]
                else:
                    walks[key]=resumed
        if world.settle_exits(frame,lambda key:hp[key]>HP_EPSILON) is not None:
            through,stop=frame,"stage_defeat"
            break
        if world.settled(frame,hp,log):
            through,stop=frame,"stage_victory_projected"
            break
        if not world.present:
            chant=pending=None
            blast_events.clear()
            continue
        if aura:
            friend=friends.get(MOSTIMA)
            active=bool(friend and friend["frame"]<frame and friend["hp_q32"]>HP_EPSILON)
            stopped=(mode=="S1" or chant is not None and frame>=chant["cast_frame"]+2 and (resume is None or frame<resume))
            recovery.points=mana
            set_period(recovery,effective_period(aura,UID,"CASTER",active,base_rate=0),stopped=stopped)
            if recovery.advance(step,stopped=stopped):
                mana=recovery.points
                log.append(dict(kind="aura_mana",frame=frame,mana=mana))
        if slot==3:
            sample_power=power_clock.advance(step)
            if sample_power:
                checked_mana=mana
            for e in blast_events.pop(frame,()):
                kind=e["kind"]
                log.append(e)
                if kind=="spend_mana":
                    mana=max(0,mana-e["cost"])
                    e["remaining"]=mana
                    if sample_power and e["phase"]=="OBJECTS":
                        checked_mana=mana
                if kind not in ("high_tile_shards","explosion_reach"):
                    continue
                cells=shard_cells if kind=="high_tile_shards" else blast_cells
                for key in world.active[frame]:
                    tick=object_position_frame(world.births[key],frame,world.positions,world.active,key,
                                               observer_born=e["projectile_born"],observer_phase="COROUTINES",same_phase_rule=world.enemy_before_projectile)
                    pos=world.positions[tick].get(key)
                    if hp[key]<=HP_EPSILON or pos is None or not world.selectable(key,frame) or not circle_in_range(*pos,radii[key],cells,box_query=True):
                        continue
                    if kind=="explosion_reach":
                        atk=mul(_raw_f32(panel["atk"]),ONE+(_raw_f32(talent["atk"]) if checked_mana>=talent["sp"] else 0))
                        packet=ordinary_arts_hit(attack_raw=mul(atk,_raw_f32(board["atk_scale"])),hp_raw=hp[key],resistance_raw=resistance[key])
                        hp[key]=packet.hp_after_raw
                        hit=dict(kind="explosion_hit",frame=frame,target=key,id=key,hp_after_q32=hp[key],damage_q32=packet.damage_raw)
                        log.append(hit)
                        hits.append(hit)
                    elif not world.scene.enemies[world.stats[key]["enemy_key"]]["attributes"]["stunImmune"]["m_value"]:
                        ratio=.5 if "STATUS_RESISTANCE" in world.wave.flags.at(key,frame) else 1.
                        stuns[key]=tick+(_raw_f32(board["stun"]*ratio)+step-1)//step
                        if key not in walks:
                            walks[key]=world.start_walk(tables,key,frame,0)
                        if incoming and key in incoming.enemies:
                            incoming.stun(key,stuns[key])
                        log.append(dict(kind="shard_stun",frame=frame,target=key,id=key,until=stuns[key],
                                        frozen_through=stuns[key],position=pos))
            if frame==resume:
                chant=None
                poll=frame
        if frame == check_at:
            checked_mana = mana
        powered = checked_mana >= talent["sp"]
        if mode == "S1" and (mana_timer.advance(step) or frame == attack_ends) and mana < minimum:
            switch(frame, "Default", "mana_below_minimum")
        if slot==1 and chant is not None:
            if frame == chant["cast_frame"]:
                log.append(dict(kind="cast_commit", frame=frame, mana=mana))
            if frame == chant["spell_frame"]:
                chant = None
                switch(frame, "S1", "chant_complete")
        if pending is not None and pending[0] == frame:
            release(frame)
        if chant is None and pending is None and frame >= poll:
            opponents = [row for row in world.snapshot(frame) if hp[row["id"]] > HP_EPSILON
                         and circle_in_range(row["row"], row["col"], radii[row["id"]], grids[powered], box_query=True)]
            injured = [ally for ally in friends.values() if mode == "S1" and ally["frame"] <= frame
                       and ally["healable"] and HP_EPSILON < ally["hp_q32"] < ally["max_hp_q32"]
                       and (ally["row"], ally["col"]) in grids[powered]]
            poll = frame + 3
            if opponents or injured:
                if opponents:
                    target = opponents[0] if len(opponents) == 1 else world.select(opponents, frame)
                    name, point = ("Power" if mana else "Normal"), (target["row"], target["col"])
                else:
                    target = min(injured, key=lambda ally: Fraction(ally["hp_q32"], ally["max_hp_q32"]))
                    name, point = "Heal", None
                pending = frame + delay, name, target["id"], point
                attack_ends, poll = frame + duration, frame + cooldown
                log.append(dict(kind="attack_start", frame=frame, mode=mode, ability=name,
                                target=target["id"], mana=mana, output_frame=pending[0]))
        for _ in inputs[frame, "after_tick"]:
            button(frame, "after_tick")
        if withdrawals.get(frame)=="after_tick" and world.present:
            leave(frame,True)
            world.remove_owner(frame,log,"withdraw")

    result = dict(stage=stage["code"], operator_id=UID, skill=slot, mode=mode, start_mana=initial_mana, mana=mana,
                  companion_deck_buff=companion,mana_source=mana_source, through_frame=through, stop=stop, attack_cooldown_frames=cooldown,
                  events=log, hits=hits, heals=heals, ally_hp_q32={key: row["hp_q32"] for key, row in friends.items()})
    if slot==3:
        result["clock"]=last_clock
    saved=next((e for e in reversed(log) if e["kind"]=="mana_saved" and e["frame"] in withdrawals),None)
    if saved:
        result["withdrawal"]=dict(frame=saved["frame"],phase=withdrawals[saved["frame"]],mana_saved=saved["mana"])
    return world.stage_result(result, fixed_timestep=dt, target_field="target", hp_field="hp_after_q32")

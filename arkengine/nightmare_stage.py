"""Nightmare's direct attacks and movement trauma share one stage timeline."""
from .attack_range import circle_in_range, range_cells
from .baked_mount import single
from .bombardier_attack import _operator_hit_offset
from .caster_cast import run_committed_cast
from .caster_equipment_traits import _trait_values
from .caster_first_spell import attack_key_clock
from .caster_prefabs import caster_prefab_evidence
from .caster_start import resolve_cast_start
from .damage import HP_EPSILON, ordinary_arts_hit
from .elemental_casters import _board
from .five_star_skills import (nightmare_airborne, nightmare_heal_flight, nightmare_heal_impact,
                              nightmare_heal_targets)
from .fixed import ONE, mul
from .marcil_deploy import _birth
from .mystic_timing import fixed_wait_frames
from .nightmare_route import _map_distance, rupture_packet
from .phalanx_wave import ResumedGroundWalk
from .ranged_stage import prepare_ranged_stage
from .skill_recovery import RecoveryTimer, deploy_skill
from .volley_damage import _position_samples, _raw_f32


NIGHTMARE = "char_164_nightm"


def run_nightmare_stage(tables, request):
    dep, *buttons = request["actions"]
    slot = request["squad"][0]["skill"]
    end = request["end_frame"]
    presses = {b["frame"] for b in buttons}
    if (len(presses) != len(buttons) or any(
            b != dict(kind="skill", operator_id=NIGHTMARE, frame=b["frame"], phase="after_tick") or
            not dep["frame"] < b["frame"] <= end for b in buttons)):
        raise ValueError("夜魔的手动操作需要不同帧的 after_tick 按键")
    card, stage, placed, scene, world = prepare_ranged_stage(
        tables, dict(request, actions=[dep, *buttons[:1]]), operator=NIGHTMARE, manual=bool(buttons),
        portable=True, track_exits=True, simulate_before_button=True, native_first_walk=True, allow_flying=slot==2)
    ev = caster_prefab_evidence(tables, NIGHTMARE, slot, skill_level=card["skill_level"],
                               module_id=card["module"], module_level=card["module_level"])
    board = {b["key"]:b["value"] for b in ev["skill_blackboard"]}
    dt = tables.fixed_timestep()
    step = _raw_f32(dt)
    start, view = placed["frame"], scene["view"]
    panel = card["panel_attributes"]
    hp = {key:_raw_f32(row["hp"]) for key,row in world.stats.items()}
    res = {key:_raw_f32(row["res"]) for key,row in world.stats.items()}
    radii = {key:tables.enemy_circle(row["prefab"]) for key,row in world.stats.items()}
    power = _raw_f32(panel["atk"])
    if slot == 2:
        power = mul(power, ONE + _raw_f32(_board(card,0,"1")["atk"]))
    module_sp = int(_trait_values(card).get("sp",0))
    cells = range_cells(card["base_range"], placed, scene["rows"], scene["cols"])
    skill_cells = range_cells(card["skill_range"] or card["base_range"], placed, scene["rows"], scene["cols"])
    gauge, _ = deploy_skill(tables, card, start)
    sp = gauge.state
    period = single(panel["baseAttackTime"]*100/panel["attackSpeed"])
    normal_wait = fixed_wait_frames(single(period/dt))
    normal_key, _ = attack_key_clock(tables, ev, view, "Attack", period)
    native_clock = run_committed_cast(tables, ev, horizon=0, facing=view,
                                      direction=placed["direction"])["natural_end"]
    next_attack = start + _birth(tables,card,ev,view)["born_exit_update"] + 1
    cast = attack = None
    restore = unblock = None
    self_heals = []
    source_hp = max_hp = _raw_f32(panel["maxHp"])
    events = [dict(kind="deployed", frame=start)]
    walks, ruptures, odometers = {}, {}, {}

    if slot == 1:
        spell, = (c["fields"] for c in ev["character"]["components"]
                   if c["path"] == "Modes/S1/Body/Attack" and c["script"] == "NightmSkill_1")
        heal_selector, = (c["fields"] for c in ev["character"]["components"] if c["script"] == "AdvancedSelectorNightm")
        switch = ev["templates"]["switch_mode"]["eventToActions"]
        if (spell["_primaryOptions"]["projectileKey"] or spell["_secondaryOptions"]["mountPointType"] != 1
                or spell["_interruptAbilityOnDetach"] or heal_selector["_excludeOwner"]
                or switch["ON_BUFF_FINISH"][0]["_restartFSM"]):
            raise ValueError("夜魔的直击、附加治疗或退态配置变化")
        heal_key = spell["_secondaryOptions"]["projectileKey"]
        offset = _operator_hit_offset(tables,NIGHTMARE,"defaultskin")
        if offset is None:
            raise ValueError("夜魔自疗弹的起讫挂点或延时配置变化")
        self_mount = tuple(v+d for v,d in zip((placed["col"],placed["row"],scene["placement"]["tile_z"]),offset))
        self_heal_flight = nightmare_heal_flight(tables,ev,self_mount,[self_mount],release=0,round_position=True)["frame"]
    else:
        ability, = (c["fields"] for c in ev["skill"]["components"] if c["script"] == "AnimatedActionToTargetAbility")
        active, = ability["_activeBuffs"]
        if (active["templateKey"] != "rupture" or active["overrideKey"] != "rupture" or
                active["overrideType"] != 3 or not active["waitFirstTriggerInterval"]
                or ability["_selectTargetTiming"]):
            raise ValueError("夜魔创伤的施加或刷新配置变化")
        # The original projection keeps history before this operator is deployed.
        prefix = _position_samples(world.history, set(range(start+1)))
        for key, birth in world.births.items():
            origin = (birth["route_start"]["row"],birth["route_start"]["col"])
            total = 0.
            for f in range(birth["frame"]+5,start+1,5):
                seen = prefix[f].get(key,origin)
                total = single(total + _map_distance(seen,origin))
                origin = seen
            odometers[key] = total, origin

    def alive(key):
        return hp[key] > HP_EPSILON

    def choices(frame, area, count, *, air_first=False):
        pool = [row for row in world.snapshot(frame) if alive(row["id"])
                and circle_in_range(row["row"],row["col"],radii[row["id"]],area,box_query=True)]
        selected = []
        flyers = [r for r in pool if nightmare_airborne(dict(r,airborne=r.get("airborne",False)))] if air_first else []
        while pool and len(selected) < count:
            group = flyers or pool
            row = world.select(group,frame) if len(group)>1 else group[0]
            selected.append(row["id"])
            pool.remove(row)
            if flyers:
                flyers.remove(row)
        return selected

    def trigger(frame, targets=None):
        nonlocal cast, attack, restore, unblock
        auto = slot == 2
        cast = resolve_cast_start(tables,ev,frame=frame,facing=view,direction=placed["direction"],
                                  phase="before_coroutines" if auto else "after_tick",
                                  trigger="AUTO" if auto else "MANUAL", sp=sp.points,
                                  owner_state="attack" if frame<next_attack and attack else "idle")
        if cast["cast_frame"] is None:
            raise ValueError("夜魔已交付的技能未完成提交")
        cast["targets"] = targets
        sp.points = cast["sp_after"]
        attack = None
        restore = (frame + native_clock["end"] if auto else
                   cast["spell_frame"] + native_clock["end"] - native_clock["spell_frame"])
        unblock = restore + native_clock["first_recovery_tick"] - native_clock["end"]
        events.extend(cast["events"])

    def apply_rupture(frame, key):
        if key in ruptures:
            ruptures[key]["remaining"] = _raw_f32(board["duration"])
            events.append(dict(kind="rupture_refresh", frame=frame, target=key))
            return
        if key not in walks and frame < end:
            walks[key] = ResumedGroundWalk(tables, world, key, frame, board["move_speed"])
        current = odometers[key][0]
        ruptures[key] = dict(last=current, remaining=_raw_f32(board["duration"]),
                             pulse=RecoveryTimer(_raw_f32(board["interval"])))
        events.append(dict(kind="rupture_apply", frame=frame, target=key, total_move_distance=current))

    frame = start
    for frame in range(start+1,end+1):
        for key, walk in tuple(walks.items()):
            if not alive(key) or not walk.advance(world,frame,key in ruptures):
                walks.pop(key)
                ruptures.pop(key,None)
        if world.settle_exits(frame,alive) is not None:
            break
        for key,(total,last) in odometers.items():
            if not alive(key) or key not in world.active[frame]:
                continue
            mark = ruptures.get(key)
            if mark:
                mark["remaining"] -= step
                finished = mark["remaining"]<=0
                if mark["pulse"].advance(step) or finished:
                    delta, damage = rupture_packet(mark["last"],total,board["value"])
                    mark["last"] = total
                    hp[key] = max(0,hp[key]-damage)
                    if hp[key]<HP_EPSILON:
                        hp[key] = 0
                    if damage:
                        events.append(dict(kind="rupture_damage",frame=frame,target=key,damage_q32=damage,
                                           hp_after_q32=hp[key],distance_delta_q32=delta,position=world.positions[frame][key]))
                if finished:
                    del ruptures[key]
                    events.append(dict(kind="rupture_finish",frame=frame,target=key))
            if (frame-world.births[key]["frame"])%5 == 0:
                current = world.positions[frame][key]
                odometers[key] = single(total+_map_distance(current,last)),current
        if frame == restore:
            cast = None
            # S1 stops adding new healing attacks without disrupting an old attack.
            if slot == 2:
                next_attack = frame+1
            events.append(dict(kind="skill_end",frame=frame))
        if cast is None and (unblock is None or frame>=unblock):
            sp.advance(step)
        if slot == 2 and cast is None and sp.points>=sp.cost and frame>=next_attack:
            targets = choices(frame,skill_cells,int(board["max_target"]),air_first=True)
            if targets:
                trigger(frame,targets)
        if cast and frame == cast["spell_frame"]:
            if slot == 1:
                next_attack = frame+1
                events.append(dict(kind="healing_mode",frame=frame))
            else:
                for key in cast["targets"]:
                    if alive(key) and world.receiver_at(key,frame):
                        apply_rupture(frame,key)
        active = slot == 1 and cast is not None and frame>=cast["spell_frame"]
        if frame == next_attack and (cast is None or active):
            selected = choices(frame,cells,1)
            if selected:
                key, = selected
                attack = frame+normal_key,key,active
                next_attack = frame+normal_wait
                events.append(dict(kind="attack_start",frame=frame,target=key,healing=active,hit_frame=attack[0]))
            else:
                next_attack += 3
        if attack and frame == attack[0]:
            _,key,heal = attack
            if alive(key) and world.receiver_at(key,frame):
                previous = sp.points
                if not active and (unblock is None or frame>=unblock) and world.stats[key]["level"] in ("ELITE","BOSS"):
                    sp.points = min(sp.maximum,sp.points+module_sp)
                hit = ordinary_arts_hit(attack_raw=power,resistance_raw=res[key],hp_raw=hp[key])
                hp[key] = hit.hp_after_raw
                events.append(dict(kind="attack_hit",frame=frame,target=key,damage_q32=hit.damage_raw,
                                   hp_after_q32=hp[key],module_sp=sp.points-previous))
            if heal:
                # Self is selected by the V079 secondary ally selector even at full HP.
                # Both ends use the same hit mount, so this Paracurve reaches on its first update.
                allies = nightmare_heal_targets([dict(id=NIGHTMARE,hp_q32=source_hp,max_hp_q32=max_hp)],
                                               int(board["attack@max_target"]))
                for ally in allies:
                    self_heals.append(frame+self_heal_flight)
                    events.append(dict(kind="heal_projectile_release",frame=frame,target=ally["id"],projectile=heal_key))
            attack = None
        if frame in self_heals:
            requested = mul(power,_raw_f32(board["attack@heal_scale"]))
            hit = nightmare_heal_impact(dict(id=NIGHTMARE,hp_q32=source_hp,max_hp_q32=max_hp),requested)
            source_hp = hit["hp_after_q32"]
            self_heals.remove(frame)
            events.append(dict(kind="heal_projectile_hit",frame=frame,target=NIGHTMARE,requested_q32=requested,
                               restored_q32=hit["restored_q32"],source_hp_q32=source_hp))
        if frame in presses:
            if cast is None and sp.points>=sp.cost:
                trigger(frame)
            else:
                events.append(dict(kind="skill_rejected",frame=frame,sp=sp.points))
        if world.all_processed(hp) and not self_heals:
            break

    through = world.defeated_at or frame
    reason = "stage_defeat" if world.defeated_at else ("all_enemies_processed" if frame<end else "horizon")
    result = dict(status=reason, stop=reason, through_frame=through, stage=stage["code"], deployment=placed, skill=slot,
                  operator_id=NIGHTMARE, events=sorted([e for e in events if e["frame"]<=through],key=lambda e:e["frame"]),
                  source=tables.source, sp=sp.points, source_hp_q32=source_hp, remaining_hp_q32=hp)
    return world.stage_result(result,fixed_timestep=dt,target_field="target",hp_field="hp_after_q32",projection_horizon=end)

"""Resolve local battle callbacks for Nightmare, Qanipalaat and Harmonie.

A callback is an observed engine event, not a request to skip cast or travel time.
Selections consume *already in-range, eligible, ordinary hatred-ordered* candidates;
effects consume accepted triggers or impacts. HP calculations cover ordinary enemies
without other shields, vulnerabilities or damage changes. All coordinates are tiles.
"""
from math import isfinite

from .damage import HP_EPSILON, ordinary_arts_hit
from .fixed import ONE, mul
from .levitate import changeable_airborne, ordinary_levitate
from .module_effects import active_board, talent_rows, trait_board
from .skill_recovery import _divide
from .trace_motion import paracurve_horizontal_step
from .volley_damage import _raw_f32

NIGHTM, QANIK, HAMONI = "char_164_nightm", "char_466_qanik", "char_297_hamoni"


def _number(data, key, *, minimum=0):
    value = data[key]
    if type(value) not in (int, float) or not isfinite(value) or not minimum <= value < (1 << 30):
        raise ValueError(f"{key} 超过模型支持的有限数值范围")
    return value


def _flag(data, key):
    if type(data[key]) is not bool:
        raise ValueError(f"{key} 应明确给出 true 或 false")
    return data[key]


def _integer(data, key):
    value = _number(data, key)
    if type(value) is not int:
        raise ValueError(f"{key} 需要非负整数")
    return value


def _targets(event, field="targets"):
    rows = event[field]
    if not isinstance(rows, list) or any(not isinstance(row.get("id"), str) or not row["id"] for row in rows):
        raise ValueError("候选列表需要带非空 id 的单位")
    if len(set(row["id"] for row in rows)) != len(rows):
        raise ValueError("候选单位重复")
    return rows


def _arts(target, attack_raw, *, fragile=1):
    hp = _raw_f32(_number(target, "hp"))
    hit = ordinary_arts_hit(attack_raw=attack_raw, resistance_raw=_raw_f32(_number(target, "res", minimum=-1000)), hp_raw=hp)
    damage = mul(hit.damage_raw, _raw_f32(fragile))
    remainder = hp - damage
    return dict(id=target["id"], damage_raw=damage, hp_after_raw=0 if remainder < HP_EPSILON else remainder)


def nightmare_airborne(row):
    return changeable_airborne(_flag(row, "airborne"), row.get("levitate_sequence", 0),
                              row.get("ground_bound_sequence", 0))


def nightmare_heal_targets(allies, limit):
    """AdvancedSelectorNightm uses the heal purpose and HP_RATIO_ASC, including full HP."""
    if not any("hp_q32" in row or "max_hp_q32" in row for row in allies):
        return allies[:limit]
    eligible = []
    for index, ally in enumerate(allies):
        hp, maximum = ally["hp_q32"], ally["max_hp_q32"]
        if type(hp) is not int or type(maximum) is not int or not 0 <= hp <= maximum or maximum <= HP_EPSILON:
            raise ValueError("夜魔治疗对象需要有效的当前生命与生命上限")
        if hp > HP_EPSILON and ("healable" not in ally or _flag(ally, "healable")):
            eligible.append((_divide(hp, maximum), index, ally))
    eligible.sort()
    return [row for _, _, row in eligible[:limit]]


def nightmare_heal_impact(ally, requested):
    hp = ally["hp_q32"]
    restored = min(ally["max_hp_q32"] - hp, requested) if nightmare_heal_targets([ally], 1) else 0
    return dict(id=ally["id"], requested_q32=requested, restored_q32=restored, hp_after_q32=hp+restored)


def nightmare_heal_flight(tables, evidence, origin, mounts, *, release, round_position):
    """Trace the V079 healing packet from NightmSkill_1's secondary launch."""
    variants = evidence["projectiles"]["projectile_nightm_s1"]
    profiles = set()
    for variant in variants.values():
        parts = {r["script"]:r["fields"] for r in variant["components"]}
        move, body = parts["ParacurveMovement"], parts["SimpleProjectile"]
        if (move.get("_delayToStart") or move["_delayAfterReached"] or move["_useRandomHeight"]
                or body["_getLifeTimeFromBB"] or not body["_alwaysHitTraceTargetInTheEnd"]):
            raise ValueError("夜魔治疗弹的飞行配置变化")
        profiles.add((move["_speed"], body["_lifeTime"]))
    (speed, lifetime), = profiles
    dt = tables.fixed_timestep()
    expiry = (_raw_f32(lifetime) + _raw_f32(dt) - 1) // _raw_f32(dt)
    position = origin
    for elapsed, mount in enumerate(mounts, 1):
        if mount is None:
            return dict(status="receiver_left", frame=release+elapsed)
        position, reached = paracurve_horizontal_step(position, mount, speed, dt, round_position)
        if reached or elapsed == expiry:
            return dict(status="heal_reached", frame=release+elapsed)
    return dict(status="in_flight", position=position)


def qanik_priority_group(rows):
    """For a single target, OnPostFilter removes non-flyers only if at least one flyer exists."""
    flying = [r for r in rows if changeable_airborne(_flag(r, "airborne"), r.get("levitate_sequence", 0),
                                                     r.get("ground_bound_sequence", 0))]
    return flying or rows


def _nightmare(tables, event, slot, board, atk, loadout):
    kind = event["kind"]
    if slot == 2 and kind == "rupture_route":
        from .nightmare_route import run_rupture_route
        return run_rupture_route(tables, board, event)
    if slot == 1 and kind in ("heal_release", "heal_volley"):
        allies = nightmare_heal_targets(_targets(event), int(board["attack@max_target"]))
        amount = mul(atk, _raw_f32(board["attack@heal_scale"]))
        packets = [dict(id=r["id"], key="projectile_nightm_s1", heal_raw=amount) for r in allies]
        if kind == "heal_volley":
            from .caster_prefabs import caster_prefab_evidence
            ev = caster_prefab_evidence(tables, NIGHTM, slot, skill_level=loadout["skill_level"],
                                        module_id=loadout["module"], module_level=loadout["module_level"])
            for ally, packet in zip(allies, packets):
                mounts = ally["target_mounts"]
                flight = nightmare_heal_flight(tables, ev, event["origin"], mounts, release=event["frame"],
                                               round_position=event.get("round_position",True))
                packet.update(flight)
                if flight["status"] == "heal_reached":
                    current = dict(ally, **ally.get("impact_state", {}))
                    packet["healing"] = nightmare_heal_impact(current, amount)
        return dict(projectiles=packets)
    if slot == 1 and kind == "heal_impact":
        return nightmare_heal_impact(event["target"], mul(atk, _raw_f32(board["attack@heal_scale"])))
    if slot == 2 and kind == "rupture_apply":
        results = []
        # Reorder by air priority, preserving the preceding hatred order inside each group.
        ordered = sorted(_targets(event), key=nightmare_airborne, reverse=True)
        for row in ordered[:int(board["max_target"])]:
            current = row.get("rupture")
            rupture = (dict(current) if current is not None else
                       dict(source=NIGHTM, damage_per_tile=board["value"], last_distance=_number(row, "distance"),
                            move_speed_change=board["move_speed"], interval_seconds=board["interval"]))
            rupture["remaining_seconds"] = board["duration"]
            results.append(dict(id=row["id"], refreshed=current is not None, rupture=rupture))
        return dict(targets=results)
    if slot == 2 and kind in ("rupture_tick", "rupture_finish"):
        current = event["rupture"]
        distance = _number(event, "distance")
        from .nightmare_route import rupture_packet
        previous = _number(current, "last_distance")
        scale = _number(current, "damage_per_tile")
        delta, damage = rupture_packet(previous, distance, scale)
        return dict(source=current["source"], distance_delta=delta/ONE, pure_damage_raw=damage,
                    rupture=dict(current, last_distance=distance) if kind == "rupture_tick" else None)
    raise ValueError(f"夜魔 S{slot} 不支持事件 {kind}")


def _qanipalaat(tables, event, slot, board, atk, talent):
    kind = event["kind"]
    if kind == "select":
        rows = _targets(event)
        if slot == 1:
            return dict(targets=[r["id"] for r in qanik_priority_group(rows)[:1]])
        ground = [r for r in rows if not _flag(r, "airborne")]
        return dict(targets=[r["id"] for r in ground[:int(board["max_target"])]])
    if slot == 2 and kind == "link_hit":
        duration = board["levitate_duration"]
        fresh = "existing_link" not in event or not _flag(event,"existing_link")
        if "target_motion" in event:
            motion = ordinary_levitate(duration if fresh else 0,event["target_motion"])
        else:
            motion = dict(levitate_applied=fresh and not _flag(event,"levitate_immune"))
        return dict(target=event["target_id"],link=True,tick_interval_seconds=board["interval"],
                    base_levitate_seconds=duration if motion["levitate_applied"] else 0,**motion)
    if slot == 2 and kind == "link_break":
        remaining = _integer(event, "live_links_before")
        if remaining == 0 or remaining > board["max_target"]:
            raise ValueError("断链前的存活链接数越界")
        return dict(remaining_links=remaining-1, stop_skill=remaining == 1)
    if slot == 2 and kind == "link_tick":
        row = event["target"]
        multiplier = talent["damage_scale"] if _flag(row, "fragile_active") else 1
        return _arts(row, mul(atk, _raw_f32(board["trigger_atk_scale"])), fragile=multiplier)
    if slot == 2 and kind == "levitate_finish":
        # This handler belongs to ON_BUFF_FINISH of the derived levitation buff.
        # Immunity creates no such buff and therefore no finish event.
        action = tables.read("battle/buff_template_data.json")["qanik_s_2[lev]"]["eventToActions"]["ON_BUFF_FINISH"][0]
        if not action["_useRadius"] or action["_useDynamicTreeForRadius"] or action["_excludeTarget"]:
            raise ValueError("坠雪落地范围判定与已核对模板不符")
        hits = []
        for row in _targets(event):
            dr = _number(row, "row", minimum=-1000) - _number(event["center"], "row", minimum=-1000)
            dc = _number(row, "col", minimum=-1000) - _number(event["center"], "col", minimum=-1000)
            distance_squared = dr*dr + dc*dc
            # Equal at float32 precision: the native boundary predicate needs a separate trace.
            if abs(distance_squared - action["_radius"]**2) < 1e-7:
                raise ValueError("落地爆炸边界需要实包比较精度")
            if distance_squared < action["_radius"]**2:
                fragile = talent["damage_scale"] if _flag(row, "fragile_active") else 1
                hits.append(_arts(row, mul(atk, _raw_f32(board[action["_damageScale"]])), fragile=fragile))
        return dict(hits=hits)
    raise ValueError(f"雪绒 S{slot} 不支持事件 {kind}")


def _harmonie(event, card, board, atk, trait, talent):
    kind = event["kind"]
    slot = card["skill"]
    if kind == "attack_release":
        stored = _integer(event, "stored")
        cap = int(trait["times"])
        if stored > cap:
            raise ValueError("攻击能量超过当前模组的容量")
        rows = _targets(event)
        if slot == 2:
            rows = [r for r in rows if not _flag(r, "airborne") and
                    (_flag(r,"blocked") or ("is_blocking" in r and _flag(r,"is_blocking")))]
        if not rows:
            return dict(stored=min(stored + 1, cap), primary=None, stored_projectiles=0)
        return dict(stored=0, primary=rows[0]["id"], stored_projectiles=stored,
                    primary_key=f"projectile_chr_hamoni_s{slot}",
                    stored_key="projectile_chr_hamoni_s1_ball" if slot == 1 else "projectile_chr_hamoni_ball")
    if kind == "projectile_hit":
        row = event["target"]
        projectile_kind = event["projectile_kind"]
        if projectile_kind not in ("primary", "stored"):
            raise ValueError("必须区分主弹和储能弹")
        # OnAttached merges the main skill scale into ChargeAttackS1's base 1.0.
        # Both projectile paths use it; the blocked talent is evaluated again at impact.
        scale = board["attack@atk_scale"] if slot == 1 else 1
        power = mul(atk, _raw_f32(scale))
        if _flag(row,"blocked") or ("is_blocking" in row and _flag(row,"is_blocking")):
            power = mul(power, _raw_f32(talent["atk_scale"]))
        return _arts(row, power)
    if slot == 2 and kind == "water_tick":
        from .harmonie_battle import water_targets
        return dict(hits=[_arts(row,_raw_f32(board["damage_value"])) for row in water_targets(card,event,"damage")])
    if slot == 2 and kind == "water_slow":
        from .harmonie_battle import water_targets
        return dict(targets=[r["id"] for r in water_targets(card,event,"slow")],
                    move_speed_change=board["move_speed"])
    raise ValueError(f"和弦 S{slot} 不支持事件 {kind}")


def execute_five_star(tables, loadout, event):
    """Apply a local callback with the selected skill rank and module phase; inputs stay immutable."""
    if event["kind"] == "stationary_battle":
        if loadout["operator_id"]==QANIK and loadout["skill"]==2:
            from .qanik_link import stationary_link
            return stationary_link(tables,loadout,event)
        if loadout["operator_id"] == HAMONI:
            if loadout["skill"]==2:
                from .harmonie_water import run_harmonie_water_battle
                return run_harmonie_water_battle(tables,loadout,event)
            from .harmonie_battle import run_harmonie_active_battle
            return run_harmonie_active_battle(tables, loadout, event)
        from .qanik_battle import run_qanik_battle
        return run_qanik_battle(tables, loadout, event)
    frame = _integer(event, "frame")
    uid, slot = loadout["operator_id"], loadout["skill"]
    if uid not in (NIGHTM, QANIK, HAMONI):
        raise ValueError("该术师不使用此技能事件处理器")
    unit = tables.read("excel/character_table.json")[uid]
    phase = loadout["module_phase"] or {}
    rows = talent_rows(phase, 0, "1", unit["talents"][0]["candidates"])
    talent = active_board(rows, loadout["elite"], loadout["level"], loadout["potential"])
    panel = loadout["panel_attributes"]
    atk = _raw_f32(panel["atk"])
    board = {item["key"]: item["value"] for item in loadout["skill_row"]["blackboard"]}
    if uid == NIGHTM and slot == 2:
        atk = mul(atk, ONE + _raw_f32(talent["atk"]))
    kind = event["kind"]
    if kind == "profile":
        result = dict(attack_raw=atk, attack_speed=panel["attackSpeed"]+board.get("attack_speed", 0),
                      base_attack_seconds=panel["baseAttackTime"]*board.get("base_attack_time", 1))
        if uid == NIGHTM and slot == 1:
            result["physical_and_arts_dodge_probability"] = talent["prob"]
        if uid == QANIK:
            result["airborne_arts_fragile_multiplier"] = talent["damage_scale"]
        if uid == HAMONI:
            result["storage_capacity"] = int(trait_board(unit, phase, loadout)["times"])
            result["blocked_attack_scale"] = talent["atk_scale"]
    elif kind == "normal_hit" and uid in (NIGHTM, QANIK):
        row = event["target"]
        fragile = talent["damage_scale"] if uid == QANIK and _flag(row, "fragile_active") else 1
        result = _arts(row, atk, fragile=fragile)
        if phase:
            if row["level"] not in ("NORMAL", "ELITE", "BOSS"):
                raise ValueError("敌人等级未知")
            # SP gain attempt; SP lock and capacity are owned by the skill SP lifecycle.
            result["sp_gain_attempt"] = int(row["level"] in ("ELITE", "BOSS"))
    elif uid == NIGHTM:
        result = _nightmare(tables, event, slot, board, atk, loadout)
    elif uid == QANIK:
        result = _qanipalaat(tables, event, slot, board, atk, talent)
    else:
        result = _harmonie(event, loadout, board, atk, trait_board(unit, phase, loadout), talent)
    return dict(frame=frame, kind=kind, operator_id=uid, skill_id=loadout["skill_id"],
                module_id=loadout["module"], module_level=loadout["module_level"], result=result)

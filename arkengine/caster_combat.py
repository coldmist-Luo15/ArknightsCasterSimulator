"""Ordinary single-target caster spells with impact-time HP and a second attack."""
from .attack_range import range_cells
from .attributes import base_attack_at_level
from .caster_modules import core_penetration_module, splash_range_module
from .caster_runtime import _first_searches, current_talents, default_attack
from .caster_splash import direct_area_damage
from .damage import HP_EPSILON, ordinary_arts_hit
from .enemy_aura import DRONE
from .eyja_aura import EYJA, FlameBreath
from .fixed import ONE, mul
from .health_bounds import HealthBounds
from .mystic_projectiles import trace_projectile_releases
from .opening_scene import bundle_environment
from .skill_assets import _script_classes
from .volley_damage import _raw_f32


def _standard_projectile(tables, key):
    cache = ("single_caster_projectile",key)
    if cache in tables._prefab_data:
        return tables._prefab_data[cache]
    raw, _ = tables.verified_bundle("battle/prefabs/[uc]projectiles.ab")
    env = bundle_environment(raw)
    scripts = _script_classes(tables)
    for suffix in ("","_logic"):
        path = f"dyn/battle/prefabs/[uc]projectiles/{key}{suffix}.prefab"
        if path not in env.container:
            return False
        root = env.container[path].deref()
        names = []
        for row in root.read_typetree()["m_Component"]:
            obj = root.assets_file.objects[row["component"]["m_PathID"]]
            if obj.type.name == "MonoBehaviour" and obj.read_typetree()["m_Enabled"]:
                options = scripts.get(obj.serialized_type.old_type_hash.hex(),())
                names.append(options[0]["name"] if len(options) == 1 else "unidentified")
        if sorted(names) != ["AdvancedMovement","EffectBehaviour","SimpleProjectile"]:
            return False
    tables._prefab_data[cache] = True
    return True


def _attack_value(tables, card, profile,core_module=None, *, ally_atk_ratio=0):
    key = card["operator_id"]
    unit = tables.read("excel/character_table.json")[key]
    if unit["subProfessionId"] not in ("corecaster","splashcaster","blastcaster") or profile["common_abilities"] or profile["general_abilities"] or profile["trait"]:
        return None
    frames = unit["phases"][card["elite"]]["attributesKeyFrames"]
    base = base_attack_at_level(frames,card["level"])
    if base is None:
        return None
    total = base + unit["favorKeyFrames"][-1]["data"]["atk"]
    if core_module:
        total += core_module["attack_bonus"]
    elif card["module"] is not None:
        module = splash_range_module(tables,card)
        if not module or module["ordinary_attack_bonus"] is None:
            return None
        total += module["ordinary_attack_bonus"]
    for rank in unit["potentialRanks"][:card["potential"]-1]:
        for mod in (rank["buff"] or {}).get("attributes",{}).get("attributeModifiers",()):
            if mod["attributeType"] == "ATK":
                if mod["formulaItem"] != "ADDITION" or mod["loadFromBlackboard"] or mod["fetchBaseValueFromSourceEntity"]:
                    return None
                total += mod["value"]
    factor = ONE + ally_atk_ratio
    for candidate in current_talents(unit,card):
        talent = candidate["prefabKey"]
        if (key,talent) == ("char_210_stward","1") and profile["mode_index"] == 1:
            row = tables._component_fields("characters",key,("_buffs",),"caster_ordinary_talents",
                                          children=("Talents",talent))
            buff, = row["_buffs"]
            modifier, = buff["attributes"]["attributeModifiers"]
            if buff["templateKey"] != "empty" or modifier != dict(attributeType=1,formulaItem=1,value=0.0,
                    loadFromBlackboard=1,fetchBaseValueFromSourceEntity=0):
                return None
            factor += _raw_f32(next(bb["value"] for bb in candidate["blackboard"] if bb["key"]=="atk"))
        elif (key,talent) == ("char_180_amgoat","1"):
            factor += FlameBreath.from_card(tables,card).ratio
        elif (key,talent) not in {("char_501_durin","1"),("char_411_tomimi","1"),("char_009_12fce","1"),("char_180_amgoat","2"),
                                  ("char_121_lava","1"),("char_1011_lava2","1")}:
            return None
        # Durin's talent dodges incoming Arts. Tomimi's ATK talent is attached to S1/S2 only.
    return mul(_raw_f32(total),factor)


def _damage(scene, spawned, ledger, angles, source, aura=None, *, fixed_penetration=0, attack_at=None):
    if angles["status"] != "conditional_projectile_reach":
        return dict(status="unresolved",reason=angles.get("reason","missing_ordinary_projectile_reach"))
    scenarios = []
    for angle in angles["scenarios"]:
        target, launch = angle["target"], angle["launch_frame"]
        enemy = scene.enemies[spawned[target]["enemy"]]
        regen = enemy["attributes"]["hpRecoveryPerSec"]
        own_aura = aura is not None and spawned[target]["enemy"] == DRONE
        if (target not in ledger.maximum or (enemy["talentBlackboard"] and not own_aura) or enemy["skills"] or regen["m_value"]
                or len(ledger.resistances[target]) != 1):
            return dict(status="unresolved",reason="enemy_resistance_or_health_effect_unreviewed",target=target)
        res, = ledger.resistances[target]
        shot, = angle["shots"]
        for arrival in shot["arrivals"]:
            frame = arrival["frame"]
            frames, lower = [], []
            if frame is not None:
                lo = ledger.floor_at(target,frame-1)
                hi = ledger.before(frame).get(target,{}).get(res,ledger.maximum[target])
                if lo <= HP_EPSILON:
                    return dict(status="unresolved",reason="receiver_liveness_not_proven_at_impact",target=target)
                bonus = (aura.bonus_bounds(target,frame,dead_by=ledger.dead_by(frame),
                        source_alive=lambda unit,t: ledger.floor_at(unit,t-1) > HP_EPSILON) if aura else (0,0))
                power = attack_at(frame) if attack_at else source
                upper = ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(res+bonus[1]-fixed_penetration),
                                          hp_raw=hi)
                floor = ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(res+bonus[0]-fixed_penetration),
                                          hp_raw=lo)
                frames.append(dict(frame=frame,damage_q32=upper.damage_raw,hp_upper_before_q32=hi,
                                   hp_upper_after_q32=upper.hp_after_raw))
                if attack_at:
                    frames[-1]["attack_q32"] = power
                if bonus[1]:
                    frames[-1].update(effective_resistance_interval=[res+b for b in bonus],
                                      damage_interval_q32=[upper.damage_raw,floor.damage_raw])
                if fixed_penetration:
                    frames[-1]["resistance_after_penetration"] = [max(0,res+b-fixed_penetration) for b in bonus]
                lower.append(dict(instance=target,frame=frame,resistance=res,hp_lower_after_q32=floor.hp_after_raw))
            case = dict(resistance=res,frames=frames,receiver_lower_updates=lower,receiver_lower_coverage_complete=True,
                        not_alive_by_frame=frame if frames and frames[-1]["hp_upper_after_q32"] <= HP_EPSILON else None)
            scenarios.append(dict(target=target, launch_frame=launch, face=angle["face"], x_sign=angle["x_sign"],
                                  arrival_stop=arrival["stop"], resistance_cases=[case]))
    if attack_at:
        values = {hit["attack_q32"] for scenario in scenarios for case in scenario["resistance_cases"] for hit in case["frames"]}
        source = next(iter(values)) if len(values) == 1 else None
    return dict(status="conditional_ordinary_caster_damage",attack_q32=source,scenarios=scenarios,
                conditions=["maximum_trust","ordinary_receiver_without_shield_healing_or_interception",
                            "only_modelled_damage_applied"])


def caster_combat(tables, scene, card, placement, attack, spawned, history, events, unknown, *, round_position, aura=None,
                  priority=None, eyja_card=None):
    if attack.get("status") == "phalanx_default_disarmed":
        quiet = dict(status="no_ordinary_attack",through_frame=attack["through_frame"],scope=attack["scope"])
        return dict(quiet),dict(quiet),dict(quiet)
    line = attack.get("area_profile")
    splash = line or attack.get("splash_profile")
    flight = splash or attack.get("flight_profile")
    def pending(reason):
        return dict(status="unresolved",reason=reason)

    if not flight:
        gap = pending(attack.get("reason","caster_release_mount_unreviewed"))
        return gap,gap,gap
    profile = default_attack(tables,card["operator_id"],flight["mode_index"])
    cfg = profile["fields"]
    projectile = cfg.get("_projectileKey")
    if not splash and not _standard_projectile(tables,projectile):
        gap=pending("caster_custom_projectile_payload")
        return gap,gap,gap
    end = flight["horizon"]
    def fly(casts):
        if splash:
            return dict(status="direct_arts_impact",casts=casts)
        if not casts or any(row["stop"] != "first_projectile_released" for row in casts):
            return pending("not_all_search_branches_release_within_horizon")
        starts = {}
        for row in casts:
            starts.setdefault((row["target"],row["launch_frame"]),set()).add(row["start_frame"])
        releases = [(target,launch,0,attack["animation_seconds_at_key"]) for target,launch in starts]
        layout = dict(clip=flight["clip"],main=(projectile,[flight["mount"]]))
        return trace_projectile_releases(tables,scene,placement,flight,sorted(releases),spawned,history,end,
            layout=layout,mode="Default",round_position=round_position,cast_starts=starts)

    first = fly(attack["scenarios"])
    core = core_penetration_module(tables,card)
    value = _attack_value(tables,card,profile,core)
    checks = dict(_attackType=1,_elementDamageType=0,_extraDamageType=0,_useDynamicAttackType=0)
    if not splash:
        checks.update(_useCachedAtkOnly=0,_transferSource=0,_extraProjectileKeys=[])
    if value is None or any(cfg.get(k) != v for k,v in checks.items()):
        gap = pending("caster_outgoing_modifiers_unreviewed")
        return first,gap,gap
    value = mul(value,_raw_f32(cfg["_atkScale"]))
    attack_at = None
    if eyja_card is not None and card["operator_id"] != EYJA:
        if (any(eyja_card[k] is None for k in ("potential","elite","level")) or eyja_card["module"] == "unknown"
                or (eyja_card["module"] and not eyja_card["module_level"])):
            gap = pending("eyja_team_attack_modifier_loadout_unresolved")
            return first,gap,gap
        flame = FlameBreath.from_card(tables,eyja_card)
        ratio = flame.bonus(tables.read("excel/character_table.json")[card["operator_id"]]["profession"],owner_on_field=True)
        if ratio:
            with_aura = mul(_attack_value(tables,card,profile,core,ally_atk_ratio=ratio),_raw_f32(cfg["_atkScale"]))
            if flame.deck:
                value = with_aura
            else:
                attached = next((e for e in events if e["kind"] == "deployed" and e["operator_id"] == EYJA),None)
                if attached:
                    # Arrival from an ordinary projectile runs in OBJECTS; direct spell keys run in COROUTINES.
                    # INPUT and after-tick placements can therefore differ on the deployment frame itself.
                    after_objects = attached["phase"] == "INPUT" and bool(splash)
                    effective = attached["frame"] + int(not after_objects)
                    attack_at = lambda frame: with_aura if frame >= effective else value
    ledger = HealthBounds(scene,spawned)
    cells = None
    if line:
        unit = tables.read("excel/character_table.json")[card["operator_id"]]
        shape = tables.read("excel/range_table.json")[unit["phases"][card["elite"]]["rangeId"]]
        grid = scene.level["mapData"]["map"]
        cells = range_cells(shape,placement,len(grid),len(grid[0]))
    def damage(delivery):
        if splash:
            return direct_area_damage(tables,scene,spawned,history,delivery["casts"],ledger,value,
                                      radius=splash.get("radius"),cells=cells,aura=aura,attack_at=attack_at)
        return _damage(scene,spawned,ledger,delivery,value,aura,fixed_penetration=core["fixed_resistance_penetration"] if core else 0,attack_at=attack_at)
    first_damage = damage(first)
    if first_damage["status"] == "unresolved":
        return first,first_damage,pending(first_damage["reason"])
    starts = {row["start_frame"] for row in attack["scenarios"]}
    impacts = [row["resistance_cases"][0]["frames"] for row in first_damage["scenarios"]]
    if len(starts) != 1 or not all(impacts):
        return first,first_damage,pending("first_cast_or_impact_remains_branched")
    release_key = "start_frame" if splash else "launch_frame"
    ledger.apply(first_damage,release_frame=min(r[release_key] for r in first_damage["scenarios"]))
    follow = dict(status="conditional_repeat",hp_intervals=ledger.interval_snapshot())
    def stop(reason):
        follow.update(stop=reason)
        return first,first_damage,follow
    if cfg.get("_waitForProjectileInvalid") or cfg["_escapeTime"]:
        return stop("nonstandard_recovery_wait")
    if card["skill_id"]:
        skill = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][card["skill_level"]-1]
        sp = skill["spData"]
        if skill["skillType"] != "MANUAL" and sp["spType"] == "INCREASE_WHEN_ATTACK" and sp["initSp"] + sp["increment"] >= sp["spCost"]:
            return stop("second_attack_skill_replacement_possible")
    dt = flight["step_seconds"]
    interval = flight["base_interval_seconds"]
    frame_ratio = interval / dt
    period = round(frame_ratio)
    # Reviewed _resetCdStrategy=1 runs the half-frame cooldown correction at cast end.
    # No period change is admitted on this path. Keep the FP epsilon band open.
    if abs(abs(frame_ratio-period)-.5)<1e-3 or flight["nominal_cast_seconds"]>interval+1e-5:
        return stop("half_frame_boundary_or_extended_attack_period")
    first_ready = starts.pop()+period
    if ledger.last_projected_impact >= first_ready:
        return stop("first_impact_not_settled_before_next_search")
    casts = _first_searches(card["operator_id"],events,unknown,end,first_ready,attack["ticks_to_key"],projectile,
                            dead_by=ledger.dead_by(first_ready),
                            priority=priority if tables._prefab_data["caster_hatred_priority",card["operator_id"],profile["mode_index"]] else None,
                            direct_impact="direct_line_impact" if line else "direct_splash_impact")
    if any("target" in r and ledger.floor_at(r["target"],r["start_frame"]-1) <= HP_EPSILON for r in casts):
        return stop("second_target_liveness_unresolved")
    follow.update(first_ready_frame=first_ready,casts=casts)
    shots = fly(casts)
    follow["projectiles"] = shots
    if shots["status"] not in ("direct_arts_impact","conditional_projectile_reach"):
        return stop(shots["reason"])
    second_damage = damage(shots)
    follow["damage"] = second_damage
    if second_damage["status"] not in ("conditional_direct_splash_damage","conditional_direct_line_damage","conditional_ordinary_caster_damage"):
        return stop(second_damage["reason"])
    ledger.apply(second_damage,release_frame=min(r[release_key] for r in second_damage["scenarios"]))
    follow["hp_intervals"] = ledger.interval_snapshot()
    return stop("second_impact" if all(row["resistance_cases"][0]["frames"] for row in second_damage["scenarios"])
                else "before_second_impact")

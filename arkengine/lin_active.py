"""Lin's S3 area attack and the two outcomes of its delayed kill refresh."""
import json
from bisect import bisect_right
from copy import deepcopy
from fractions import Fraction
from functools import partial

from .attack_range import RangeContacts, circle_in_range, range_cells
from .attributes import base_attack_at_level
from .caster_runtime import default_attack
from .module_effects import active_board, equipped_phase, module_stat, selected_branch, talent_rows
from .lin_equipment import lin_output_stacks
from .lin_output_aura import LinAuraGeometry, count_bounds, device_contacts, verify_aura
from .damage import HP_EPSILON, ordinary_arts_hit
from .device_timeline import lin_joint_sp, vent_timeline
from .enemy_resistance import overload_resistance_bounds
from .fixed import ONE, mul
from .frames import FPS
from .health_bounds import HealthBounds, _reviewed_regular_enemy
from .lin_interactions import LIN, barrier_hit
from .mystic_cycle import baked_key_sample
from .mystic_timing import fixed_wait_frames
from .opening_scene import bundle_environment
from .skill_assets import _script_classes, lin_begin, suspendable_buff
from .target_priority import remaining_route_priority
from .unity_box_contact import centered_range_boxes,range_sensor_contact
from .volley_damage import _raw_f32


def _refresh_binding(tables):
    key = "lin_s3_kill_refresh"
    if key in tables._prefab_data:
        result, source = tables._prefab_data[key]
        tables.source[key] = source
        return result
    raw, source = tables.verified_bundle("config/buff_template_holder.ab")
    holder, = (o for o in bundle_environment(raw).objects if o.type.name == "MonoBehaviour")
    events = {t["templateKey"]: {e["key"]: json.loads(e["value"]["SerializedState"])
              for e in t["eventToActions"]["_items"]} for t in holder.read_typetree()["_templates"]
              if t["templateKey"].startswith("lin_t_1")
              or t["templateKey"] in ("trap_eradio_eradio_trait", "switch_mode_restart_fsm")}
    kill, trigger = events["lin_t_1_s_3"][9]
    refresh, finish = events["lin_t_1_s_3[refresh]"][1]
    disabled = "lin_t_1[disable]"
    enabled = refresh["_failNodes"][1]
    damage = events["lin_t_1[damage]"][32][0]
    mode_finish, = events["switch_mode_restart_fsm"][1]
    if (kill["_abilityName"] != "Attack" or trigger["_abilityName"] != "lin_t_1_s_3" or not trigger["_castDirectly"]
            or not mode_finish["$type"].endswith("+SwitchMode") or not mode_finish["_restoreDefault"]
            or not mode_finish["_restartFSM"] or mode_finish["_targetType"] != "TARGET"
            or not finish["$type"].endswith("+FinishBuff")
            or refresh["_conditionNode"]["_buffKeys"] != [disabled]
            or refresh["_succeedNodes"][0]["_buffKeys"] != [disabled]
            or enabled["_abilityName"] != "lin_t_1_s_3_selector" or enabled["_buff"]["buffKey"] != "lin_t_1[damage]"
            or events["lin_t_1[damage]"][0][0]["_buff"]["buffKey"] != "stun"
            or (damage["_damageType"], damage["_atkScaleVar"]) != ("MAGICAL", "atk_scale")):
        raise ValueError("林三技能击杀与护盾重建分支结构发生变化")
    actor = tables.source["caster_prefabs"][LIN]
    raw, _ = tables.verified_bundle(actor["member"])
    root = bundle_environment(raw).container[actor["container"]].deref()
    classes = _script_classes(tables)
    buffs = []
    for obj in root.assets_file.objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        names = classes.get(obj.serialized_type.old_type_hash.hex(), ())
        if len(names) == 1 and names[0]["name"] == "ActiveBuffAbility":
            fields = obj.read_typetree()
            if fields["_metadata"]["namedAsAlias"] == trigger["_abilityName"] and fields["m_Enabled"]:
                if (fields["_waitForCasting"] or fields["_waitForAttackEvent"] or fields["_preDelay"] or
                        fields["_interruptAbilityOnDetach"] or fields["_passiveBuffs"]):
                    raise ValueError("林的击杀刷新赋予能力含额外起手等待")
                buffs.extend(fields["_buffs"])
    buff, = buffs
    if buff["templateKey"] != "lin_t_1_s_3[refresh]" or buff["lifeTimeType"] != 1 or buff["lifeTime"] <= 0:
        raise ValueError("林三技能击杀触发器缺少有限期刷新标记")
    raw, skill_source = tables.verified_bundle("battle/prefabs/[uc]skills.ab")
    skill = bundle_environment(raw).container["dyn/battle/prefabs/[uc]skills/skchr_lin_3.prefab"].deref()
    objects = skill.assets_file.objects
    components = [objects[r["component"]["m_PathID"]].read_typetree() for r in skill.read_typetree()["m_Component"]]
    ctrl, = (f for f in components if "_extraAbilities" in f)
    extra, = ctrl["_extraAbilities"]
    spell = objects[extra["m_PathID"]].read_typetree()
    selector = objects[spell["_selector"]["m_PathID"]].read_typetree()
    selector_go = objects[selector["m_GameObject"]["m_PathID"]].read_typetree()
    grids = [objects[r["component"]["m_PathID"]].read_typetree() for r in selector_go["m_Component"]]
    grid, = (g for g in grids if "_useBlackboardRangeId" in g)
    attack_buff, = next(f for f in components if "_buffs" in f)["_buffs"]
    mod, = attack_buff["attributes"]["attributeModifiers"]
    if (spell["_metadata"]["namedAsAlias"] != enabled["_abilityName"] or grid["_useBlackboardRangeId"] or grid["_extendable"]
            or (selector["_targetSide"],selector["_targetCategory"],selector["_targetMotion"]) != (2,1,3)
            or selector["_limitTargetNum"] or selector["_ignoreTargetFree"] or selector["_ignoreHitRange"]
            or (mod["attributeType"],mod["formulaItem"],mod["loadFromBlackboard"],mod["fetchBaseValueFromSourceEntity"])
                != (1,1,1,0)):
        raise ValueError("林三技能的攻击力加算或破碎技能选择器结构发生变化")
    source = dict(templates=source, skill=skill_source, skill_selector_id=spell["_selector"]["m_PathID"],
                  skill_selector_name=spell["_metadata"]["namedAsAlias"])
    result = dict(refresh_seconds=buff["lifeTime"], max_refreshes_per_direct_attack=1,
                  when_active="area_stun_then_arts_and_retain_barrier", when_disabled="restore_without_area_hit",
                  shatter_selection="rescan_current_skill_range_at_shatter",
                  direct_kill_trigger="Attack", incoming_hit_priority=0,
                  incoming_break_order="after_incoming_damage", explosion_can_trigger_kill_refresh=False)
    vent = events["trap_eradio_eradio_trait"]
    if any(not vent[event][-1]["$type"].endswith("+CancelModifier") or
           vent[event][-1]["_reason"] != "UNHURTABLE" for event in (10, 41)):
        raise ValueError("废热喷口的伤害取消模板发生变化")
    tables.source[key] = source
    tables._prefab_data[key] = result, source
    return result


def lin_s3_profile(tables, card):
    if (card["operator_id"] != LIN or card["skill_id"] != "skchr_lin_3" or card["elite"] != 2
            or not card.get("skill_level") or not card["potential"] or not card["level"]):
        return dict(status="unresolved", reason="unknown_lin_s3_card")
    if card["module"] is not None and (selected_branch(tables, card) not in ("plx-x", "plx-y") or not card["module_level"]):
        return dict(status="unresolved", reason="unreviewed_lin_s3_module")
    binding = suspendable_buff(tables, card["skill_id"])
    if not binding["supported"]:
        return dict(status="unresolved", reason=binding["reason"])
    mode = default_attack(tables, LIN, binding["mode_buff"]["blackboard"]["mode"])
    ability, selector = mode["fields"], mode["selector"]
    if (mode["ability"] != "MeleeAttack" or mode["trigger"] != "SelectorTrigger" or not mode["enabled"]
            or not mode["base_range"] or mode["combat_override"] or selector["_limitTargetNum"]
            or (selector["_targetSide"], selector["_targetMotion"], selector["_targetCategory"]) != (2, 3, 1)
            or (ability["_attackType"], ability["_damageType"], ability["_atkScale"]) != (1, 2, 1)
            or ability["_timeMode"] or ability["_selectTargetTiming"] or not ability["_waitForAttackEvent"]
            or ability["_activeBuffs"] or ability["_escapeTime"] or ability["_allowNoTarget"]
            or mode["trigger_parameters"] != dict(_minTargetNum=1, _overrideSearchTargetTick=-1, _keepTarget=0)):
        return dict(status="unresolved", reason="unreviewed_lin_s3_attack_ability")
    if not tables._prefab_data.get(("caster_hatred_priority",LIN,mode["mode_index"])):
        return dict(status="unresolved", reason="unreviewed_lin_s3_target_order")
    raw, _ = tables.verified_bundle(tables.source["caster_prefabs"][LIN]["member"])
    actor = bundle_environment(raw).container[tables.source["caster_prefabs"][LIN]["container"]].deref()
    cast = actor.assets_file.objects[mode["attack_component"]].read_typetree()
    if cast["_alwaysPutIncludeTargetFirst"] or ability["_selectTargetSource"]!=1:
        return dict(status="unresolved", reason="unreviewed_lin_s3_primary_target_order")
    unit = tables.read("excel/character_table.json")[LIN]
    frames = unit["phases"][2]["attributesKeyFrames"]
    base = base_attack_at_level(frames,card["level"])
    timing = {(r["data"]["baseAttackTime"],r["data"]["attackSpeed"]) for r in frames}
    if base is None or len(timing) != 1:
        return dict(status="unresolved", reason="lin_level_attack_interpolation")
    favor = unit["favorKeyFrames"][-1]["data"]["atk"]
    potential = 0
    for rank in unit["potentialRanks"][:card["potential"] - 1]:
        for mod in (rank["buff"] or {}).get("attributes", {}).get("attributeModifiers", ()):
            if mod["attributeType"] == "ATK":
                if mod["formulaItem"] != "ADDITION" or mod["loadFromBlackboard"] or mod["fetchBaseValueFromSourceEntity"]:
                    return dict(status="unresolved", reason="unreviewed_lin_potential_modifier")
                potential += mod["value"]
    phase = equipped_phase(tables, card)
    module = module_stat(phase, "atk")
    skill = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][card["skill_level"]-1]
    board = {v["key"]: v["value"] for v in skill["blackboard"]}
    rows = talent_rows(phase, 0, "1", unit["talents"][0]["candidates"])
    shield = active_board(rows, card["elite"], card["level"], card["potential"])
    source = base + favor + potential + module
    attack = mul(_raw_f32(source), ONE + _raw_f32(board["atk"]))
    anim = lin_begin(tables, card)
    act = anim["actions"][ability["_animKey"]]
    bake = tables.read(f"bakemuzzledata/character/{LIN}_{anim['battle_skin']}.json")["bakedAnimDatas"]
    front, back = (bake[v]["bakedEvents"][act["clip"]] for v in ("Front", "Back"))
    base_interval,speed = timing.pop()
    interval = base_interval * 100 / speed
    if (front != back or front["events"] != ["OnAttack"] or len(front["eventTime"]) != 1 or act["loop"]
            or act["speed"] != 1 or interval != act["duration"]):
        return dict(status="unresolved", reason="unreviewed_lin_attack_animation_or_speed")
    ticks, _ = baked_key_sample(front["eventTime"][0], 1, tables.fixed_timestep())
    refresh = _refresh_binding(tables)
    step = _raw_f32(tables.fixed_timestep())
    lifetime = _raw_f32(refresh["refresh_seconds"])
    return dict(status="lin_s3_profile", ability=mode["ability"], range_id=skill["rangeId"],
                clip=act["clip"], baked_event_seconds=front["eventTime"][0], ticks_to_event=ticks,
                interval_frames=fixed_wait_frames(Fraction(interval) * FPS), target_snapshot="cast_start",
                output_stacks=lin_output_stacks(tables, phase, card),
                target_limit=None, target_motion=["ground","fly"], damage_type="MAGICAL", delivery="direct",
                attack_inputs=dict(base=base, max_trust=favor, potential=potential, module=module,
                                   skill_add_ratio=board["atk"]), direct_attack_q32=attack,
                shatter_attack_q32=mul(attack, _raw_f32(shield["atk_scale"])),
                default_shatter_attack_q32=mul(_raw_f32(source), _raw_f32(shield["atk_scale"])),
                barrier_threshold_q32=mul(_raw_f32(shield["value"]), _raw_f32(board["talent_scale"])),
                stun_seconds=shield["stun"], stun_object_ticks=(_raw_f32(shield["stun"])+step-1)//step,
                broken_barrier_recovery_seconds=shield["interval"],
                kill_refresh=dict(refresh, minimum_object_ticks_to_expiry=(lifetime+step-1)//step))


def lin_s3_impact(profile, receivers, *, source="Attack", shield_at_refresh=None, aura_count=None):
    """Apply one ordinary, unmodified simultaneous packet group to caller-supplied living targets."""
    if source not in ("Attack", "shatter") or (shield_at_refresh is not None and type(shield_at_refresh) is not bool):
        raise ValueError("林的伤害来源或刷新时护盾状态无效")
    attack = profile["direct_attack_q32" if source == "Attack" else "shatter_attack_q32"]
    stacks = profile.get("output_stacks")
    if stacks and (type(aura_count) is not int or aura_count < 0):
        raise ValueError("林 Y 模组结算需要伤害发生时光环内的有效敌人数")
    hits = {}
    for instance, state in receivers.items():
        if state.get("receiver_kind") == "heat_vent":
            hits[instance] = dict(damage_q32=0, killed=False, stun_applied=False,
                                  on_take_damage_sp_request=state["recovery_per_hit"])
            continue
        hit = ordinary_arts_hit(attack_raw=attack, resistance_raw=state["resistance_q32"], hp_raw=state["hp_q32"])
        damage, hp = hit.damage_raw, hit.hp_after_raw
        if stacks:
            scale = ONE + _raw_f32(stacks["per_enemy"]) * min(aura_count, stacks["maximum"])
            damage = mul(damage, scale)
            hp = max(0, state["hp_q32"] - damage)
            if hp < HP_EPSILON:
                hp = 0
        hits[instance] = dict(damage_q32=damage, hp_after_q32=hp, killed=hp <= HP_EPSILON)
    refresh = None
    if source == "Attack" and any(h["killed"] for h in hits.values()):
        rules = profile["kill_refresh"]
        outcomes = [rules["when_active"], rules["when_disabled"]] if shield_at_refresh is None else [
            rules["when_active" if shield_at_refresh else "when_disabled"]]
        refresh = dict(delay_seconds=rules["refresh_seconds"], outcomes=outcomes)
    return dict(hits=hits, applies_stun_first=source=="shatter",
                stun_seconds_before_resistance=profile["stun_seconds"] if source=="shatter" else None,
                kill_refresh=refresh)


def lin_s3_refresh(profile, shield_active, current_receivers, *, aura_count=None):
    """Resolve the existing mark once, with target HP and barrier state at its expiry."""
    if type(shield_active) is not bool:
        raise ValueError("需要刷新结束时的实际屏障状态")
    blast = lin_s3_impact(profile, current_receivers, source="shatter", aura_count=aura_count) if shield_active else None
    return dict(shield_active_after=True, blast=blast)


def lin_s3_received_hit(profile, damage_q32, *, shield_active, current_receivers, ignore_block=False, aura_count=None):
    """A nonlethal incoming hit, after defense. S3 applies its break after taking damage."""
    shield = barrier_hit(damage_q32, profile["barrier_threshold_q32"], active=shield_active, ignore_block=ignore_block)
    blast = lin_s3_impact(profile, current_receivers, source="shatter", aura_count=aura_count) if shield["breaks"] else None
    return dict(incoming=shield, shield_active_after=shield_active and not shield["breaks"], blast=blast,
                recovery_delay_seconds=profile["broken_barrier_recovery_seconds"] if shield["breaks"] else None)


def certify_joint_activation(active, ordinary, device, *, sp=None, protection_end=None):
    """Extend the shared combat timeline across consecutive S3 casts with guaranteed readiness."""
    if not active or active.get("status") != "conditional_lin_s3_attacks" or not ordinary or not device:
        return None
    starts = active["activations"]
    pending = ordinary.get("pending") or {}
    if (not starts or pending.get("reason") != "lin_skill_button_input" or not active.get("device_sp")
            or pending["frame"] != starts[0]["button_frame"]):
        return None
    if any(_raw_f32(v["maximum_pre_resistance_damage"]) > active["profile"]["barrier_threshold_q32"]
           for v in ordinary["covering_vents"]):
        return None
    environment_end = ordinary["reviewed_highland_environment_through"]
    buttons = (device.get("lin_skill_points") or {}).get("buttons", ())
    observed = {b["frame"] for b in (sp or {}).get("buttons", ()) if b.get("observed_outcome") == "begin_animation"}
    from .skill_recovery import LIN_DRAW_OPTIONS
    if sp is not None and (len(sp["buttons"]) > 1 or observed or any(k in sp for k in LIN_DRAW_OPTIONS)) and protection_end is not None:
        shells = (ordinary.get("bombardier_window") or {}).get("impacts", ())
        last_button = max((b["frame"] for b in sp["buttons"]),default=environment_end)
        gauge = lin_joint_sp(sp,device,shells,min(last_button,environment_end),(),protection_end)
        if gauge:
            buttons = gauge["buttons"]
            if gauge["pending"]:
                boundary = gauge["pending"]["frame"]
                active["sp_button_boundary"] = dict(gauge["pending"], attempt=buttons[-1])
                environment_end = min(environment_end, boundary-1)
            if observed:
                active["observed_activation_checks"] = dict(through_frame=gauge["through_frame"], pending=gauge["pending"],
                    checked=[b for b in buttons if b["frame"] in observed])
    guaranteed = {b["frame"] for b in buttons if b["possible_outcomes"] == ["begin_animation"]}
    through = starts[0]["button_frame"]-1
    windows = []
    for activation in starts:
        frame = activation["button_frame"]
        if frame > min(through+1,environment_end) or frame not in guaranteed:
            break
        # A persistent enemy-side vent guarantees a target even if all nearby enemies have died.
        if not all(a["persistent_device_targets"] for a in activation["conditional_attacks"]):
            break
        windows.append((activation["first_search_frame"],min(activation["through_frame"],environment_end)))
        default_end = activation.get("default_mode_through_frame", activation["through_frame"])
        through = min(default_end, environment_end)
    if not windows:
        return None
    for hit in active["device_sp"]["direct_hits"]:
        hit["guaranteed"] = any(a <= hit["frame"] <= b for a,b in windows)
    active["joint_coverage"] = dict(through_frame=through, button_frame=pending["frame"],
        button_frames=[a["button_frame"] for a in starts[:len(windows)]],
        conditions=["activation_agrees_across_all_lins_received_sp_branches", "persistent_targetable_heat_vent",
                    "no_blocking_and_only_reviewed_enemy_abilities_on_highland", "incoming_vents_do_not_break_barrier"])
    conditioned = [b["frame"] for b in buttons if b["frame"] in active["joint_coverage"]["button_frames"]
                   and len(b.get("outcomes_before_observation", ())) > 1]
    if conditioned:
        active["joint_coverage"]["conditioned_on_observed_activations"] = conditioned
        active["joint_coverage"]["conditions"][0] = "all_retained_sp_states_compatible_with_observed_skill_begin"
    return through


def apply_joint_lin_attack(bounds, scene, spawned, history, aura, base_resistance_through, profile, attack, energy_timeline=None,
                           *, priority=None):
    """Apply to the same HP histories as Mystic projectiles. Earlier OBJECTS hits have already landed."""
    frame = attack["impact_frame"]
    is_shatter = attack.get("source") == "shatter"
    phase = "OBJECTS" if is_shatter else "COROUTINES"
    known = set(attack["selected_if_eligible"])
    updates = {}
    if is_shatter and bounds.last_projected_impact == frame:
        # A surviving isolated primary cannot change Lin's targets or their
        # resistance. Shattering other non-aura enemies cannot add a neighbour
        # to that primary's isolation test, which covered both position samples.
        possible = known | set(attack["unlocated_possible_targets"])
        primary = bounds.last_isolated_projectile_target
        aura_hit = aura and possible.intersection(aura.sources) - bounds.dead_by(frame).keys()
        if (not primary or primary in possible or bounds.floor_at(primary, frame) <= HP_EPSILON
                or attack.get("persistent_device_targets") or aura_hit):
            return updates, "lin_shatter_and_projectile_object_order"
    preview = deepcopy(bounds)
    dead = bounds.dead_by(frame+1)
    receivers = sorted((known | set(attack["unlocated_possible_targets"])) - dead.keys())
    providers = set(aura.sources) & set(receivers) if aura else set()
    ordered = True
    stacks = profile.get("output_stacks")
    if stacks and "output_aura" not in attack:
        return updates, "lin_y_missing_receiver_contact_snapshot"
    ordinary = {k for k in attack["output_aura"]["possible_members"] if _reviewed_regular_enemy(scene,spawned[k])} if stacks else set()
    if (providers or stacks) and len(receivers)>1:
        if is_shatter:
            receivers.sort(key=lambda k: spawned[k]["spawn_frame"])
            ordered = len({spawned[k]["spawn_frame"] for k in receivers}) == len(receivers)
        else:
            choose = priority or remaining_route_priority(scene,spawned,history)
            remaining = set(receivers)
            sequence = []
            while len(remaining)>1:
                ranked = choose(remaining,attack["start_frame"])
                if ranked is None:
                    ordered = False
                    break
                sequence.append(ranked["target"])
                remaining.remove(ranked["target"])
            receivers = sequence + sorted(remaining)
    definitely_finished = set()
    possibly_finished = set() if ordered else providers.copy()
    for key in receivers:
        if key in bounds.dead_by(frame+1):
            continue
        if key not in bounds.resistances:
            return updates, "lin_target_damage_attributes_unknown"
        hidden = False
        for tick in range(attack["start_frame"], frame+1):
            for kind, row in history.get(tick, ()):
                if row["instance_id"] == key and (kind in ("range_motion_unresolved","route_end_in_range") or
                        kind == "checkpoint_begin" and row["disappeared"]):
                    hidden = True
        bases = bounds.resistances[key]
        enemy = spawned[key]["enemy"]
        energy = overload_resistance_bounds(energy_timeline,key,frame,phase=phase)
        if energy is not None:
            base = scene.enemies[enemy]["attributes"]["magicResistance"]["m_value"]
            bases = {base+b for b in energy}
        elif (base_resistance_through is not None and frame <= base_resistance_through and
                enemy in ("enemy_1353_esabr_2","enemy_1354_eghost_2")):
            bases = (scene.enemies[enemy]["attributes"]["magicResistance"]["m_value"],)
        boosts = aura.bonus_bounds(key, frame, phase=phase, dead_by=dead, finished_before_hit=definitely_finished,
                                   source_alive=lambda actor,tick: actor not in possibly_finished and
                                   bounds.floor_at(actor,tick)>HP_EPSILON) if aura else (0,0)
        if is_shatter:
            key_for_attack="default_shatter_attack_q32" if attack.get("source_mode")=="Default" else "shatter_attack_q32"
        else:
            key_for_attack="direct_attack_q32"
        attack_raw=profile[key_for_attack]
        amounts = [ordinary_arts_hit(attack_raw=attack_raw, resistance_raw=_raw_f32(r+boost),
                                     hp_raw=ONE).damage_raw for r in bases for boost in set(boosts)]
        if stacks:
            low,high = count_bounds(attack["output_aura"],stacks["maximum"],hp=preview,ordinary_finishes=ordinary)
            if not ordered:
                low = min(low,len(attack["output_aura"]["stable_devices"]))
                _,high = count_bounds(attack["output_aura"],stacks["maximum"],hp=bounds,ordinary_finishes=ordinary)
            amounts = [mul(d,ONE+_raw_f32(stacks["per_enemy"])*n) for n in (low,high) for d in amounts]
        minimum = min(amounts) if key in known and not hidden else 0
        maximum = max(amounts)
        outcome = preview.apply_arts_interval(key,frame,minimum,maximum)
        if outcome is None:
            return updates, "lin_hit_overlaps_projectile_with_possible_earlier_death"
        updates[key] = dict(damage_q32=[minimum,maximum], **outcome)
        if stacks:
            updates[key]["output_aura_counts"] = [low,high]
        if definitely_finished:
            updates[key]["aura_sources_finished_before_hit"] = sorted(definitely_finished)
        if key in providers and key in aura.finish_on_hp_zero:
            if ordered and outcome["after_q32"][1] <= HP_EPSILON:
                definitely_finished.add(key)
            if outcome["after_q32"][0] <= HP_EPSILON:
                possibly_finished.add(key)
    bounds.history, bounds.lower_history = preview.history, preview.lower_history
    return updates, None


class LinImpactClock:
    """Per-HP-branch events. A successful direct kill creates one mark on the next coroutine pass."""
    def __init__(self, attacks, apply, walking_control, applied_stuns):
        self.attacks = attacks
        self.apply = apply
        self.walking_control = walking_control
        self.applied_stuns = applied_stuns
        self.shatters = {}
        self.stop_at = {}
        self.events = []

    def finish_frame(self, frame, bounds, vent_hits):
        if frame in self.stop_at:
            return self.stop_at[frame]
        event = self.shatters.get(frame) or self.attacks.get(frame)
        if event is None:
            return None
        shatter = event.get("source") == "shatter"
        receivers = set(event["selected_if_eligible"]) | set(event["unlocated_possible_targets"])
        if any(h["instance_id"] in receivers for h in vent_hits):
            return "lin_and_vent_same_tick_damage_order"
        changes, failure = self.apply(bounds,event)
        if failure:
            return failure
        row = dict(frame=frame, phase="OBJECTS" if shatter else "COROUTINES",
                   source_mode=event.get("source_mode","S3"),
                   kind="lin_s3_shatter" if shatter else "lin_s3_direct_hit", start_frame=event["start_frame"],
                   receiver_updates=changes, device_hits=event.get("persistent_device_targets", []))
        self.events.append(row)
        if shatter:
            row["kill_refresh_possible"] = False
            alive = [k for k,v in changes.items() if v["after_q32"][1] > HP_EPSILON]
            row["stunned_survivors"] = alive
            controls = [self.walking_control(k,frame) for k in alive
                        if k in event["selected_if_eligible"] and changes[k]["after_q32"][0] > HP_EPSILON]
            row["confirmed_stuns"] = [c for c in controls if c is not None]
            if (len(row["confirmed_stuns"]) != len(alive)
                    or any((k,frame) not in self.applied_stuns for k in alive)):
                self.stop_at[frame+1] = "lin_shatter_stun_changes_route_or_cast"
            return None
        outcomes = {v["kill"] for v in changes.values()}
        refresh = event.get("refresh_if_direct_kill")
        row["kill_refresh_possible"] = bool(outcomes & {"certain","possible"})
        if not row["kill_refresh_possible"] or not refresh:
            return None
        row["mark_attachment_frame"] = frame+1
        row["refresh_frame"] = refresh["selection_frame"]
        if "certain" not in outcomes or refresh["unknown_birth_order"]:
            self.stop_at[refresh["selection_frame"]] = "lin_conditional_shatter_or_receiver_object_order"
            return None
        row["scheduled_shatter_device_hits"] = [
            dict(frame=i["impact_frame"], alias=alias, phase="before_objects")
            for i in refresh["impacts"] if i["impact_frame"] not in self.stop_at
            for alias in i["persistent_device_targets"]]
        for impact in refresh["impacts"]:
            self.shatters[impact["impact_frame"]] = impact
        return None


def _candidate_windows(cells, radii, spawned, history, placement, horizon, finished):
    contacts = RangeContacts()
    windows = []
    for frame in range(horizon + 1):
        # Inputs are projected root coordinates at the end of each enemy's object update.
        # A later-created enemy has not yet moved when this caster's selector runs.
        previous = contacts.positions.copy()
        previous_unknown = set(contacts.unknown)
        for kind, fields in history.get(frame, ()):
            contacts.update(frame, kind, fields)
            if kind == "route_end_in_range":
                contacts.positions.pop(fields["instance_id"], None)
        if frame < placement["frame"]:
            continue
        inside, unknown = [], []
        for key, birth in spawned.items():
            if key in finished and finished[key] < frame or birth["spawn_frame"] >= frame:
                continue
            older = birth["spawn_frame"] < placement["frame"]
            positions = contacts.positions if older else previous
            unresolved = contacts.unknown if older else previous_unknown
            pos = positions.get(key)
            if key in unresolved:
                unknown.append(key)
            elif pos and key in radii and circle_in_range(*pos, radii[key], cells, box_query=True):
                inside.append(key)
        signature = (tuple(sorted(inside)), tuple(sorted(unknown)))
        if not windows or signature != windows[-1][1]:
            windows.append((frame, signature))
    return windows


def _target_vents(tables, scene, cells):
    key = "trap_124_eradio"
    vents = [u for u in scene.predefines if u["inst"]["characterKey"] == key]
    if not vents:
        return []
    unit = tables._component_fields("[uc]tokens", key, ("_sideType", "_category"), "lin_target_devices")
    passive = tables._component_fields("[uc]tokens", key, ("_buffs",), "lin_target_device_talent",
                                       children=("Others","Direction","Talents","1"))
    shape = tables._component_fields("[uc]tokens", key, ("m_Radius","m_Offset","m_Enabled","m_IsTrigger"),
                                     "lin_target_device_radius", component_type="CircleCollider2D")
    if ((unit["_sideType"], unit["_category"]) != (2,1) or not shape["m_Enabled"] or not shape["m_IsTrigger"]
            or shape["m_Radius"] <= 0 or any(shape["m_Offset"].values())
            or [b["templateKey"] for b in passive["_buffs"]] != ["trap_eradio_eradio_trait"]):
        raise ValueError("废热喷口的实体阵营、碰撞体或受击天赋结构发生变化")
    return [u["alias"] for u in vents if circle_in_range(u["position"]["row"], u["position"]["col"],
                                                         shape["m_Radius"], cells, box_query=True)]


def lin_s3_replay(tables, scene, card, placement, sp, spawned, history, radii, horizon, releases, *, aura=None,
                  energy_timeline=None):
    buttons = sp.get("buttons", ())
    starts = [b for b in buttons if b["outcome"] == "begin_animation" and b["mode_buff_frame"] <= horizon]
    if not starts:
        return None
    profile = lin_s3_profile(tables, card)
    if profile["status"] == "unresolved":
        return profile
    grid = scene.level["mapData"]["map"]
    ranges=tables.read("excel/range_table.json")
    active_range=ranges[profile["range_id"]]
    cells = range_cells(active_range, placement, len(grid), len(grid[0]))
    windows = _candidate_windows(cells, radii, spawned, history, placement, horizon, releases)
    changes = [frame for frame,_ in windows]
    vents = _target_vents(tables, scene, cells)
    stacks = profile["output_stacks"]
    if stacks:
        profile["output_aura_prefab"] = verify_aura(tables,card)
        default_id=tables.read("excel/character_table.json")[LIN]["phases"][card["elite"]]["rangeId"]
        default_range=ranges[default_id]
        default_cells=range_cells(default_range,placement,len(grid),len(grid[0]))
        active_contact=partial(range_sensor_contact,tables,boxes=centered_range_boxes(active_range,placement))
        default_contact=partial(range_sensor_contact,tables,boxes=centered_range_boxes(default_range,placement))
        geometry=LinAuraGeometry(cells,radii,spawned,history,contact_solver=active_contact)
        aura_devices,other_devices=device_contacts(tables,scene,cells,contact_solver=active_contact)
        default_geometry=LinAuraGeometry(default_cells,radii,spawned,history,contact_solver=default_contact)
        default_devices,default_unknown=device_contacts(tables,scene,default_cells,contact_solver=default_contact)
    def output_aura(frame,phase,*,after_exit=False):
        sample=default_geometry if after_exit else geometry
        return sample.at(frame,phase=phase,entered_mode=stop-1 if after_exit else mode_frame,
                         devices=default_devices if after_exit else aura_devices,
                         unknown_devices=default_unknown if after_exit else other_devices,finished=releases)
    received = []
    ledger = HealthBounds(scene, spawned)
    activations = []
    for button in starts:
        mode_frame = button["mode_buff_frame"]
        # Normal input follows entity updates. A paused STOP follows coroutines as well.
        natural_end = button["scheduled_end"]
        manual_end = min((b["frame"] + int(b.get("input_phase") == "after_tick")
                          for b in buttons if b["outcome"] == "stopped" and b["frame"] >= button["frame"]),
                         default=horizon + 1)
        stop = min(natural_end, manual_end, horizon + 1)
        stopped_after_previous_tick = any(
            b["outcome"] == "stopped" and b["frame"] + 1 == stop and b.get("input_phase") == "after_tick"
            for b in buttons)
        first = mode_frame + 1
        activation = dict(button_frame=button["frame"], cast_frame=button["cast_frame"], mode_buff_frame=mode_frame,
                          first_search_frame=first, through_frame=stop-1, conditional_attacks=[])
        activations.append(activation)
        search = first
        for i, (start, (targets, unknown)) in enumerate(windows):
            if search >= stop:
                break
            end = min(stop, windows[i+1][0] if i+1 < len(windows) else stop)
            if search >= end:
                continue
            if unknown and not targets and not vents:
                activation["unresolved_positions_from_frame"] = search
                activation["unknown_instances"] = list(unknown)
                break
            if not targets and not vents:
                search += ((end - search + 2) // 3) * 3
                continue
            while search < end:
                impact = search + profile["ticks_to_event"]
                attack = dict(start_frame=search, selected_if_eligible=list(targets), baked_event_frame=impact-1,
                              unlocated_possible_targets=list(unknown), persistent_device_targets=list(vents),
                              impact_frame=impact if impact < stop else None)
                if impact < stop:
                    if stacks:
                        sample = output_aura(impact,"COROUTINES")
                        low,high = count_bounds(sample,stacks["maximum"])
                        attack["output_aura"] = sample
                        attack["output_aura_counts_if_members_alive"] = [low,high]
                        # Standalone activation reports do not know earlier enemy HP.
                        counts = range(min(len(sample["stable_devices"]),stacks["maximum"]),high+1)
                    else:
                        counts = [None]
                    received.extend(dict(frame=impact, alias=alias, phase="after_objects") for alias in vents)
                    attack["damage_if_alive_and_selectable"] = {}
                    for key in targets:
                        options = []
                        # This fan-out deliberately leaves prior enemy HP open. A provider can have
                        # died to an earlier attack or shatter, even with a known geometric contact.
                        bonuses = (aura.bonus_bounds(key, impact, dead_by=releases, source_alive=lambda *_:False,phase="COROUTINES")
                                   if aura else (0,0))
                        bases = ledger.resistances.get(key, ())
                        energy = overload_resistance_bounds(energy_timeline,key,impact,phase="COROUTINES")
                        if energy is not None and bases:
                            base = scene.enemies[spawned[key]["enemy"]]["attributes"]["magicResistance"]["m_value"]
                            bases = {base+b for b in energy}
                        resistances = {base+b for base in bases for b in bonuses}
                        for res in sorted(resistances):
                            for count in counts:
                                result = lin_s3_impact(profile,{key:dict(resistance_q32=_raw_f32(res),
                                                        hp_q32=ledger.maximum[key])},aura_count=count)
                                hit = result["hits"][key]
                                option = dict(resistance=res,damage_q32=hit["damage_q32"],
                                    hp_after_if_previously_full_q32=hit["hp_after_q32"],
                                    lethal_if_pre_hit_hp_at_most_q32=hit["damage_q32"]+HP_EPSILON)
                                if stacks:
                                    option["output_aura_count"] = count
                                options.append(option)
                        attack["damage_if_alive_and_selectable"][key] = options
                    if targets or unknown:
                        attack["on_any_direct_kill"] = dict(max_refreshes=1,
                            delay_seconds=profile["kill_refresh"]["refresh_seconds"],
                            on_active=profile["kill_refresh"]["when_active"], on_disabled=profile["kill_refresh"]["when_disabled"])
                        selection = impact+1+profile["kill_refresh"]["minimum_object_ticks_to_expiry"]
                        if selection <= horizon:
                            selected, possible = windows[bisect_right(changes,selection)-1][1]
                            old = {k for k in (*selected,*possible) if spawned[k]["spawn_frame"] < placement["frame"]}
                            tie = any(spawned[k]["spawn_frame"] == placement["frame"] for k in (*selected,*possible))
                            impacts = []
                            for ticks_before_lin in (False, True):
                                group = [k for k in selected if (k in old) == ticks_before_lin]
                                uncertain = [k for k in possible if (k in old) == ticks_before_lin]
                                devices = vents if ticks_before_lin else []
                                if group or uncertain or devices:
                                    tick = selection + int(ticks_before_lin)
                                    if tick > horizon:
                                        continue
                                    # The mode buff ends during Lin's own update at natural expiry.
                                    after_exit = (tick > stop or tick == stop and
                                                  (stopped_after_previous_tick or
                                                   stop == natural_end and not ticks_before_lin))
                                    impacts.append(dict(source="shatter", start_frame=selection, impact_frame=tick,
                                        source_mode="Default" if after_exit else "S3",
                                        selected_if_eligible=group, unlocated_possible_targets=uncertain,
                                        persistent_device_targets=list(devices)))
                                    if stacks:
                                        impacts[-1]["output_aura"] = output_aura(tick,"OBJECTS",after_exit=after_exit)
                            attack["refresh_if_direct_kill"] = dict(selection_frame=selection, unknown_birth_order=tie,
                                                                   impacts=impacts)
                activation["conditional_attacks"].append(attack)
                search += profile["interval_frames"]
        if stop <= horizon:
            default_end = min([horizon] + [b["frame"]-1 for b in buttons if b["frame"] > stop
                                            and b["outcome"] != "rejected"])
            activation["default_mode_from_frame"] = stop
            activation["default_mode_through_frame"] = default_end
    device_sp = None
    if received:
        clock = vent_timeline(tables, scene, horizon, received_hits=received)
        device_sp = dict(conditions=["direct_lin_attacks_only_no_other_hits_including_conditional_shatters",
                                     "unmodified_natural_recovery_uninterrupted_vent_channel"],
                         devices={alias:{k:clock["devices"][alias][k] for k in ("casts","hits","final_sp","recovery_per_hit")}
                                  for alias in vents}, direct_hits=received)
    return dict(status="conditional_lin_s3_attacks", profile=profile, cells=[list(c) for c in cells], device_sp=device_sp,
                activations=activations, conditions=["maximum_trust_no_external_attack_or_speed_modifiers",
                    "selected_geometric_candidates_are_still_alive_targetable_and_not_intercepted",
                    "free_motion_is_unchanged_by_unmodeled_vent_slow_blocking_and_previous_stuns",
                    "each_cast_requires_a_persistent_device_or_surviving_selectable_candidate_at_admission",
                    "mode_and_caster_survive_to_impact", "prior_hp_and_barrier_state_required_to_commit_kill_and_shatter"])

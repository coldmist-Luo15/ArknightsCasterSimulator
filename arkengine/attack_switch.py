"""Relate a successful skill input to the last modeled ordinary attack."""
from fractions import Fraction

from .animation_assets import read_skin_actions
from .fixed import ONE
from .frames import FPS
from .mystic_cycle import baked_key_sample
from .mystic_storage import charge_limits, redistribute_charges, target_level_masks
from .mystic_timing import fixed_wait_frames, storage_speed_trigger
from .projectiles import clear_on_ranged_cast_end
from .skill_recovery import READY_EPSILON


def _buff_check_window(period, step, first_tick, frame):
    """Bracket a frame on a continuously running V079 precise timer, one update per frame."""
    if not 0 < step < period or period <= READY_EPSILON or frame < first_tick - 1:
        raise ValueError("需要正周期、短于周期的帧步长，以及有效的首次更新时间")
    elapsed = (frame - first_tick + 1) * step
    # Update fires at remaining < epsilon; Next adds the period to the residual.
    count = (elapsed + READY_EPSILON - 1) // period
    def due(index):
        return first_tick + (index * period - READY_EPSILON) // step
    return dict(previous=due(count) if count else None, next=due(count + 1))


def _action_samples(tables, card, cycle, timing, modes):
    """Ticks from first animation sample, independent of when the new cast is admitted."""
    profile = cycle.get("profile")
    if not profile:
        return {}
    key = card["operator_id"]
    skin = profile["battle_skin"]
    member = ("chararts" if skin == "defaultskin" else "skinpack") + f"/{key}.ab"
    actions = [m[k] for m in modes.values() for k in ("_chargeAnimKey", "_animKey")]
    tracks = read_skin_actions(tables, member, skin, actions)
    tables.source["switch_animation"] = tracks["source"]
    baked = tables.read(profile["bake_file"])["bakedAnimDatas"]
    skill_speed = 0
    if "S3" in modes:
        level = tables.read("excel/skill_table.json")[card["skill_id"]]["levels"][card["skill_level"] - 1]
        skill_speed, = (v["value"] for v in level["blackboard"] if v["key"] == "attack_speed")
    samples = {}
    def rows_for(mode, action):
        track = tracks["views"]["front"]["actions"][action]
        clip = track["clip"]
        event = baked["Front"]["bakedEvents"][clip]
        if (track != tracks["views"]["back"]["actions"][action] or track["loop"] or track["speed"] != 1
                or event != baked["Back"]["bakedEvents"][clip] or event["events"] != ["OnAttack"]
                or len(event["eventTime"]) != 1):
            return []
        rows = []
        for state in ("empty", "stored"):
            if state == "stored" and not timing["stored_speed_bonus"]:
                continue
            normal = timing[state]
            speed = normal["attack_speed"] + (skill_speed if mode == "S3" else 0)
            period = Fraction(normal["nominal_frames"]) / FPS * normal["attack_speed"] / Fraction(str(speed))
            interval = float(period)
            ticks, anim_time = baked_key_sample(event["eventTime"][0], track["duration"] / interval,
                                        profile["step_seconds"])
            rows.append(dict(module_speed_bonus_active=state == "stored", attack_speed=speed, clip_seconds=track["duration"],
                             key_seconds=event["eventTime"][0], samples_to_key=ticks,
                             animation_seconds_at_key=anim_time, action=action, period_q32=round(period * ONE),
                             action_frames=fixed_wait_frames(Fraction(normal["nominal_frames"])
                                 * normal["attack_speed"] / Fraction(str(speed)))))
        return rows
    for mode, fields in modes.items():
        samples[mode] = {kind: rows_for(mode, fields[key]) for kind, key
                         in (("charge", "_chargeAnimKey"), ("attack", "_animKey"))}
    return samples


def _cast_at_input(schedule, frame):
    if not schedule["casts"]:
        return dict(phase="no_projected_cast")
    cast = schedule["casts"][-1]
    start = cast["start_frame"]
    key = start + cast["first_bake_tick_delay"] + cast["ticks_to_key"] - 1 + cast.get("apply_delay_after_bake", 0)
    end = start + cast["fixed_wait_frames"]
    if frame >= end:
        return dict(phase="outside_last_projected_action", nominal_end=end)
    phase = "before_attack_key" if frame < key else "after_attack_key"
    if frame == key:
        phase = "same_frame_as_attack_key_order_unresolved"
    return dict(phase=phase, action=cast["action"], start=start, nominal_key=key, nominal_end=end,
                nominal_frames_remaining=end - frame)


def _first_switch_storage(limits, cycle, buttons):
    """Follow the first short toggle pair, under a fresh eligible S3 charge."""
    if limits["status"] != "conditional_charge_limits" or not buttons:
        return None
    first = buttons[0]
    if first["input"] != "activated":
        return None
    modes = limits["modes"]
    skill = modes["S3"]
    if not skill["force_ex_charge"]:
        return None
    cases = []
    for scenario, timing in zip(cycle.get("scenarios", []), first["ordinary_cast_at_effect"]):
        phase = timing["phase"]
        if phase not in ("before_attack_key", "after_attack_key", "same_frame_as_attack_key_order_unresolved"):
            continue
        cast = scenario["casts"][-1]
        before = cast["ordinary_stored_at_start"]
        after = 0 if cast["target"] is not None else before + 1
        completed_key = phase.startswith("same_frame") and first["effect_phase"] == "unity_fixed_resume"
        counts = {after} if completed_key else {before, after} if phase.startswith("same_frame") else {
            before if phase == "before_attack_key" else after}
        for normal in sorted(counts):
            n, e = redistribute_charges(normal, 0, normal_limit=skill["normal_limit"], extra_limit=skill["extra_limit"])
            case = dict(ordinary_before_switch=normal, on_entry=dict(normal=n, extra=e))
            if case not in cases:
                cases.append(case)
    if len(buttons) >= 2 and buttons[1]["input"] == "stopped":
        default = modes["Default"]
        elapsed = buttons[1]["effect_frame"] - first["effect_frame"]
        for case in cases:
            initial = case["on_entry"]
            returns = []
            for sample in first["charge_if_first_sample_next_frame"]:
                if elapsed >= sample["action_frames"] or elapsed == sample["samples_to_key"]:
                    continue  # Another cast, or same-frame input/key ordering, needs the live scheduler.
                extra = min(skill["extra_limit"], initial["extra"] + int(elapsed > sample["samples_to_key"]))
                n, e = redistribute_charges(initial["normal"], extra,
                    normal_limit=default["normal_limit"], extra_limit=default["extra_limit"])
                returns.append(dict(module_speed_bonus_active=sample["module_speed_bonus_active"],
                                    normal=n, extra=e))
            case["on_manual_return_if_uninterrupted_charge"] = returns
    return dict(cases=cases, conditions=["ordinary_volley_releases_all_stored_on_key",
                "no_extra_charge_before_first_projected_target",
                "no_eligible_skill_target_and_charge_starts_on_switch_with_next_frame_first_sample",
                "no_other_charge_or_spend_during_short_toggle"])


def _locked_stored_attacks(tables, card, modes):
    fields=("_selectTargetSource","_selectTargetTiming","_alwaysIncludeTarget",
            "_allowNoTarget","_interuptIfTargetDead","_emitToInputPosWhenTargetIsInvalid",
            "_emitToInputRootTileWhenTargetIsInvalid")
    locked={}
    for mode in modes:
        cfg=tables._component_fields("characters",card["operator_id"],fields,"locked_storage_"+mode,
                                    children=("Trait","ChargeAttackGroup","ExChargeAttack"+mode))
        locked[mode]=tuple(cfg[k] for k in fields)==(2,0,1,0,0,0,0)
    return locked


def ordinary_attack_setup(tables, card, cycle, timing):
    fields = tables._component_fields("characters", card["operator_id"], ("_animKey","_chargeAnimKey","_escapeTime",
                                      "_selectTargetSource","_selectTargetTiming","_alwaysIncludeTarget",
                                      "_allowNoTarget","_interuptIfTargetDead","_emitToInputPosWhenTargetIsInvalid",
                                      "_emitToInputRootTileWhenTargetIsInvalid"),
                                      "ordinary_mode_attack", children=("Modes","Default","Body","Attack"))
    return dict(status="ordinary_mode", buttons=[], natural_returns_to_default=[], modes={"Default":fields},
                action_samples=_action_samples(tables, card, cycle, timing, {"Default":fields}),
                locked_stored_attacks=_locked_stored_attacks(tables,card,("Default",)),
                target_level_rules=target_level_masks(tables, card["operator_id"]),
                charge_limits=charge_limits(tables, card), module_speed_trigger=storage_speed_trigger(tables, card))


def attack_switches(tables, card, cycle, timing, sp, *, include_ordinary=False):
    buttons = [b for b in sp.get("buttons", []) if b["outcome"] in ("activated", "stopped")
               and not (b.get("mode_canceled_before_effect") or b.get("no_mode_effect"))]
    if not buttons:
        if not include_ordinary or card.get("skill_id") != "skchr_ebnhlz_3" or not cycle.get("profile"):
            return None
        return ordinary_attack_setup(tables, card, cycle, timing)
    payload = sp.get("button_profile", {}).get("mode_buff")
    if not payload or card["skill_id"] != "skchr_ebnhlz_3":
        return dict(status="unresolved", reason="unreviewed_skill_mode_effect")
    events = tables.read("battle/buff_template_data.json")[payload["template"]]["eventToActions"]
    for event, restore in (("ON_BUFF_START", False), ("ON_BUFF_FINISH", True)):
        actions = events[event]
        if len(actions) != 1 or actions[0] != {
                "$type": "Torappu.Battle.Action.Nodes+SwitchMode, Assembly-CSharp",
                "_modeIndex": 0 if restore else 1, "_loadModeFromBlackboard": not restore,
                "_restoreDefault": restore, "_restartFSM": True, "_targetType": "TARGET"}:
            return dict(status="unresolved", reason="unreviewed_switch_mode_actions")
    if payload["blackboard"].get("mode") != 2:
        return dict(status="unresolved", reason="unreviewed_attack_mode")
    modes = {}
    for name in ("Default", "S3"):
        modes[name] = tables._component_fields("characters", card["operator_id"],
            ("_projectileKey", "_animKey", "_chargeAnimKey", "_escapeTime", "_waitForAttackEvent", "_waitForProjectileInvalid",
             "_notClearProjectileWhenCastEnd", "_interruptAbilityOnDetach", "_selectTargetSource",
             "_selectTargetTiming", "_alwaysIncludeTarget", "_allowNoTarget", "_interuptIfTargetDead",
             "_emitToInputPosWhenTargetIsInvalid", "_emitToInputRootTileWhenTargetIsInvalid"), "switch_attack_" + name,
            children=("Modes", name, "Body", "Attack"))
    stored = tables._component_fields("characters", card["operator_id"],
        ("_waitForProjectileInvalid", "_notClearProjectileWhenCastEnd"), "switch_stored_attack",
        children=("Trait", "ChargeAttackGroup", "ExChargeAttackDefault"))
    cleared = {name: clear_on_ranged_cast_end(wait_for_projectile_invalid=bool(cfg["_waitForProjectileInvalid"]),
                    keep_on_cast_end=bool(cfg["_notClearProjectileWhenCastEnd"]))
               for name, cfg in (("main", modes["Default"]), ("stored", stored))}
    samples = _action_samples(tables, card, cycle, timing, modes)
    rows = []
    for i, b in enumerate(buttons):
        opening = b["outcome"] == "activated"
        frozen = b.get("input_phase") == "after_tick"
        effect = b.get("mode_effect_frame", b["frame"])
        effect_phase = ("unity_fixed_resume" if opening else "after_tick") if frozen else "input"
        row = dict(input_frame=b["frame"], input_phase=b.get("input_phase", "input"), effect_frame=effect,
                   effect_phase=effect_phase, first_animation_sample=effect + int(frozen),
                   source_attributes_from_objects=effect + 1, input=b["outcome"], effect_event="ON_BUFF_START" if opening
                   else "ON_BUFF_FINISH", destination_mode="S3" if opening else "Default",
                   restart_running_state_machine=True)
        row["charge_if_first_sample_next_frame"] = [dict(s, key_frame=row["first_animation_sample"] + s["samples_to_key"])
            for s in samples.get(row["destination_mode"], {}).get("charge", [])]
        if i == 0 and opening:
            row["ordinary_cast_at_input"] = [dict(first_bake_tick_delay=s["first_bake_tick_delay"],
                **_cast_at_input(s, b["frame"])) for s in cycle.get("scenarios", [])]
            row["ordinary_cast_at_effect"] = [dict(first_bake_tick_delay=s["first_bake_tick_delay"],
                **_cast_at_input(s, effect)) for s in cycle.get("scenarios", [])]
        rows.append(row)
    limits = charge_limits(tables, card)
    trigger = storage_speed_trigger(tables, card)
    if (cycle.get("profile") and trigger["status"] == "periodic_storage_speed"
            and trigger["wait_first_trigger_interval"] and trigger["first_trigger_interval_seconds"] < 0):
        first_tick = sp["deployment_frame"] + 1
        period = int(trigger["trigger_interval_seconds"] * ONE)
        for row in rows:
            row["module_checks_if_continuous_from_deploy_next_tick"] = _buff_check_window(
                period, cycle["profile"]["step_q32"], first_tick, row["input_frame"])
            next_tick = row["effect_frame"] + 1
            ticks = []
            cursor = row["effect_frame"]
            for _ in range(3):
                cursor = _buff_check_window(period, cycle["profile"]["step_q32"], next_tick, cursor)["next"]
                ticks.append(cursor)
            row["new_mode_module_clock"] = dict(first_tick=next_tick, first_three_checks=ticks)
    return dict(status="conditional_mode_switch", modes=modes, buttons=rows, action_samples=samples,
                locked_stored_attacks=_locked_stored_attacks(tables,card,modes),
                target_level_rules=target_level_masks(tables, card["operator_id"]),
                charge_limits=limits, first_switch_storage=_first_switch_storage(limits, cycle, rows),
                module_speed_trigger=trigger,
                clear_issued_on_ordinary_cast_end=cleared,
                natural_returns_to_default=sp.get("natural_ends", []),
                natural_after_fixed=sp.get("natural_after_fixed", []),
                reattached_cooldown=dict(remaining_q32_if_period_unchanged=0,
                    conditions=["previously_detached_ability", "normal_in_battle_attach_and_reset",
                                "same_period_at_reset_and_final_recalculation", "no_later_override"]),
                conditions=["successful_buff_start_or_finish", "normal_native_path_running_state_machine",
                            "charge_example_requires_eligible_charge_and_latched_listed_speed",
                            "first_animation_sample_one_frame_after_input_for_example_only"],
                next_attack_start_frame=None, stateful_replay_field="mode_replay")

"""Opening deployments, skill inputs and wave actions on the same battle clock."""
from collections import defaultdict
from copy import deepcopy
from .natural_cost import NaturalCostClock

from .deployment import ManualDeploymentProtection
from .attack_range import RangeContacts
from .attack_switch import attack_switches
from .caster_runtime import first_caster_attack
from .caster_combat import caster_combat
from .mode_replay import impact_mode_changes, mode_replay
from .battle_outcome import ordinary_battle_outcome
from .volley_damage import first_volley_damage
from .enemy_aura import resistance_aura
from .mystic_timing import ebenholz_timing
from .mystic_cycle import first_volley, ordinary_profile
from .mystic_projectiles import first_projectiles
from .frames import FPS, FrameLoop, Phase
from .health_bounds import HealthBounds, ordinary_enemy_finishes, ordinary_life_points
from .lin_interactions import inactive_window, lin_shell_window
from .lin_active import certify_joint_activation, lin_s3_replay
from .device_timeline import lin_joint_sp, opening_vents
from .vent_motion import first_vent_segment
from .opening_cards import resolve_card, tile_permits_card
from .opening_scene import PinnedTables, load_opening_scene
from .opening_spawns import native_wave_schedule, opening_spawns
from .routes import compile_route
from .enemy_motion import _last_velocity, apply_wait_coast, apply_walking_stun, project_after_wait, project_exit_leg, project_first_leg, project_route_tail
from .route_timing import ABSOLUTE_WAITS,TIMED_WAITS,birth_timer,checkpoint_sequence
from .skill_recovery import LIN_DRAW_OPTIONS, deploy_skill, lin_hit_draw, lin_native_stream
from .target_priority import first_leg_priority, remaining_route_priority


def _frame(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} 需要非负整数逻辑帧")
    return value


def _first_affordable_frame(cost, initial, cap, timer):
    """First funded frame before any deployment, with ordinary +1 natural income."""
    if cost is None or cost > cap:
        return None
    if cost <= initial:
        return 0
    return timer.credit_frame(cost - initial)


def _group_actions(actions, horizon, cards, *, ordered_inputs=False):
    grouped = defaultdict(list)
    waiting_for_cost = []
    for action in actions:
        if action.get("kind") == "skill":
            if (set(action) - {"phase","observed_outcome"} != {"kind", "frame", "operator_id"}
                    or action["operator_id"] not in cards or action.get("phase", "input") not in ("input", "after_tick")):
                raise ValueError("技能按钮指令需要编队内干员和明确的逻辑帧")
            if "observed_outcome" in action and (action["observed_outcome"] != "begin_animation"
                    or cards[action["operator_id"]]["skill_id"] != "skchr_lin_3"):
                raise ValueError("可观测的技力条件仅支持林三技能已开始施放")
            frame = _frame(action["frame"], "action.frame")
            if frame > horizon or grouped[frame]:
                raise ValueError("技能指令超出回放区间或与其他输入同帧")
            grouped[frame].append(action)
            continue
        timing = set(action) - {"operator_id", "row", "col", "direction"}
        if timing not in ({"frame"}, {"when"}) or len(action) != 5:
            raise ValueError(f"首部署指令字段不符 {action}")
        key = action["operator_id"]
        if key not in cards:
            raise ValueError("干员未编入队伍")
        if action["direction"] not in {"UP", "DOWN", "LEFT", "RIGHT"}:
            raise ValueError("部署方向无效")
        if type(action["row"]) is not int or type(action["col"]) is not int:
            raise ValueError("部署坐标必须是整数")
        if "frame" in timing:
            frame = _frame(action["frame"], "action.frame")
            if frame > horizon:
                raise ValueError("指令在运行区间以外")
        else:
            if action["when"] != "first_affordable":
                raise ValueError("未知的首部署触发条件")
            if cards[key]["cost"] is None:
                raise ValueError(f"{key} 的卡费尚有多个候选值，无法确定首次够费的部署指令")
            waiting_for_cost.append(action)
            continue
        if grouped[frame] and not ordered_inputs:
            raise ValueError("同帧多条玩家指令的提交顺序尚未核定")
        grouped[frame].append(action)
    return grouped, waiting_for_cost


def _motion_profiles(scene, tables, schedule, track_range=False, *, native_portals=False, round_position=False,
                     native_first_walk=False,route_clocks=None,born_delay=None):
    profiles = {}
    wait_step = tables.fixed_timestep() if native_portals else None
    bindings = {}
    pending_ends = {}
    for birth in schedule:
        if birth["kind"] != "spawn":
            continue
        enemy, index = birth["enemy_key"], birth["route_index"]
        label = f"{enemy}/route/{index}"
        clocks=None
        if route_clocks and any(c["type"] in ABSOLUTE_WAITS for c in (scene.level["routes"][index]["checkpoints"] or ())):
            clocks=route_clocks.get((birth["wave_index"],birth["fragment_index"]))
            if clocks:
                label=f"{label}/{birth['instance_id']}"
        if label not in profiles:
            route = compile_route(scene, index, enemy)
            prefab = scene.enemies[enemy]["prefabKey"]
            key = prefab["m_value"] if prefab["m_defined"] and prefab["m_value"] else enemy
            steering = tables.enemy_steering(key)
            birth_fields = tables.enemy_birth(key)
            period = 1 if track_range else FPS
            exact_walk=native_first_walk and route["motion_mode"]=="WALK"
            flight_step = tables.fixed_timestep() if exact_walk or route["motion_mode"] == "FLY" else None
            projection = project_first_leg(scene, route, steering, sample_period=period,
                                           timestep=flight_step, round_position=round_position)
            profile = dict(route=route, prefab=key, steering=steering, projection=projection,
                           birth_fields=birth_fields, birth_timer=birth_timer(birth_fields))
            if exact_walk:
                profile["native_first_walk"]=True
            clock_frame=None
            if clocks and born_delay is not None and profile["birth_timer"]["status"]=="conditional_projection":
                first_move=birth["frame"]+born_delay+profile["birth_timer"]["first_default_request_update"]
                if projection.get("arrival_ticks") is not None:
                    clock_frame=first_move+projection["arrival_ticks"]-1
            if projection["status"] == "conditional_projection" and projection["next_command"] is not None:
                last = projection["samples"][-1]
                position = dict(row=last["row"], col=last["col"])
                profile["checkpoints"] = dict(
                    origin="first_move_checkpoint_accepted",
                    **checkpoint_sequence(route["commands"],1,position,timestep=wait_step,clock_frame=clock_frame,clocks=clocks))
            elif projection.get("reason") == "first_command_is_not_move":
                profile["checkpoints"] = dict(
                    origin="first_route_command_begins",
                    **checkpoint_sequence(route["commands"], 0, route["start"], timestep=wait_step))
            sequence = profile.get("checkpoints")
            if native_portals and sequence and projection.get("arrival_ticks"):
                apply_wait_coast(route,sequence,_last_velocity(projection),steering,wait_step,period,round_position,
                                 consecutive=bool(clocks))
            if sequence and sequence["events"] and sequence["next_command"]["kind"] == "END":
                if sequence["events"][-1]["command"] == "APPEAR_AT_POS":
                    incoming = projection if native_portals and projection.get("arrival_ticks") else None
                    profile["exit_leg"] = project_exit_leg(scene, route, steering, sample_period=period,
                            incoming=incoming, sequence=sequence, timestep=tables.fixed_timestep() if incoming else None,
                            round_position=round_position)
            if track_range:
                profile["circle_radius"] = tables.enemy_circle(key)
            if (projection["status"] == "conditional_projection" and sequence and sequence["events"]
                    and sequence["events"][-1]["command"] in TIMED_WAITS
                    and not sequence["events"][-1]["disappeared"] and sequence["next_command"]["kind"] in ("MOVE", "END")):
                profile["post_wait_leg"] = project_after_wait(scene, route, steering, sequence, sample_period=period,
                    timestep=tables.fixed_timestep() if native_portals or flight_step is not None else None,
                    round_position=round_position)
            stop_at = sequence["next_command"] if sequence else route["commands"][0]
            last_leg = profile.get("post_wait_leg", profile.get("exit_leg", projection))
            if "post_wait_leg" in profile and last_leg.get("next_command"):
                stop_at = last_leg["next_command"]
            if (projection["status"] == "conditional_projection" and last_leg["status"] == "conditional_projection"
                    and last_leg["next_command"] is not None):
                tail_frame=clock_frame
                if tail_frame is not None and sequence:
                    tail_frame+=sequence["next_offset"]
                    if last_leg is not projection:
                        tail_frame+=last_leg["arrival_ticks"]
                tail = project_route_tail(scene, route, steering, last_leg, sequence if last_leg is projection else None,
                                          tables.fixed_timestep(), period, native_portals=native_portals,
                                          round_position=round_position,clock_frame=tail_frame,clocks=clocks)
                profile["route_tail"] = tail
                if tail["pending"]:
                    pending_ends[label] = tail["pending"]
            elif last_leg.get("status") != "conditional_projection" or last_leg["next_command"] is not None:
                reason = ("subsequent_leg_unmodeled" if sequence and "exit_leg" not in profile and
                          ("post_wait_leg" not in profile or last_leg["status"] == "conditional_projection")
                          else last_leg["reason"])
                pending_ends[label] = dict(command=stop_at["kind"], source=stop_at["source"],
                                           reason=reason)
            profiles[label] = profile
        bindings[birth["instance_id"]] = label
    unresolved = [label for label, p in profiles.items() if any(
        p.get(name, {}).get("status") == "unresolved" for name in ("projection", "exit_leg", "post_wait_leg"))]
    return dict(profiles=profiles, instance_profiles=bindings, unresolved_profiles=unresolved,
                pending_route_ends=pending_ends,
                timebase="consecutive_active_MOVE_ticks",
                projected_portal_velocity="preserved_across_hidden_wait" if native_portals else "legacy_from_rest",
                continued_move_position_rounding=round_position,
                checkpoint_conditions=["ordinary_sequential_cursor_without_skips",
                    "one_eligible_cursor_update_per_frame_at_nominal_30Hz",
                    "no_external_displacement_or_visibility_effects",
                    "DisappearState_active_until_first_state_update_after_flag_clears",
                    "anchored_replay_checks_first_move_on_arrival_frame",
                    "direct_exit_leg_first_moves_one_update_after_appear_reached",
                    "following_cardinal_corridors_select_direct_checkpoint_no_obstacle_steering",
                    "visible_wait_resumes_from_rest",
                    "hidden_portal_preserves_incoming_velocity" if native_portals else "legacy_exit_from_rest"])


def _instance_motion_events(profile, spawn_frame, move_delay, born_delay):
    projection = profile["projection"]
    if born_delay is not None:
        timer = profile["birth_timer"]
        if timer["status"] == "unresolved":
            yield spawn_frame, "birth_timer_unresolved", dict(reason=timer["reason"])
            return
        request_offset = born_delay + timer["first_default_request_update"] - 1
        yield spawn_frame + request_offset, "born_default_requested", dict(state="DEFAULT")
        move_delay = request_offset + 1
    if projection["status"] != "conditional_projection":
        return
    first_move = spawn_frame + move_delay
    for sample in projection["samples"]:
        ticks = sample["active_ticks"]
        frame = first_move + ticks - 1 if ticks else spawn_frame
        if profile.get("native_first_walk"):
            sample=dict(sample,leg_source=projection["checkpoint"],leg_end=ticks==projection["arrival_ticks"])
        yield frame, "free_motion_sample", sample
    arrival = first_move + projection["arrival_ticks"] - 1
    if projection["next_command"] is None:
        yield arrival, "route_end_in_range", dict(target=profile["route"]["commands"][-1]["target"],
                                                 source=projection["checkpoint"])
        return
    sequence = profile["checkpoints"]
    yield from _checkpoint_motion_events(sequence, arrival)
    origin = arrival + sequence["next_offset"]
    leg = profile.get("post_wait_leg", profile.get("exit_leg"))
    if leg and leg["status"] == "conditional_projection":
        for sample in leg["samples"]:
            kind = "post_wait_motion_sample" if "post_wait_leg" in profile else "exit_motion_sample"
            yield origin + sample["active_ticks"], kind, dict(sample, leg_source=leg["checkpoint"],
                                                             leg_end=sample["active_ticks"] == leg["arrival_ticks"])
        if leg["next_command"] is None:
            yield origin + leg["arrival_ticks"], "route_end_in_range", dict(
                target=sequence["next_command"]["target"], source=leg["checkpoint"])
            return
        origin += leg["arrival_ticks"]
    tail = profile.get("route_tail")
    if tail:
        for part in tail["parts"]:
            start = origin + part["offset"]
            if "events" in part:
                yield from _checkpoint_motion_events(part, start)
                continue
            step = part["leg"]
            for sample in step["samples"]:
                leg_end = sample["active_ticks"] == step["arrival_ticks"]
                yield start + sample["active_ticks"], "free_motion_sample", dict(sample, leg_source=step["checkpoint"],
                                                                                  leg_end=leg_end)
            if part["command"] == "END":
                yield start + step["arrival_ticks"], "route_end_in_range", dict(target=step["target"],
                                                                                 source=step["checkpoint"])
        if tail["pending"]:
            yield origin + tail["next_offset"], "range_motion_unresolved", dict(source=tail["pending"]["source"],
                                                                             reason=tail["pending"]["reason"])
    elif "circle_radius" in profile:
        yield arrival + sequence["next_offset"], "range_motion_unresolved", dict(source=sequence["next_command"]["source"])


def _checkpoint_motion_events(sequence, origin):
    coast = sequence.get("wait_coast")
    for event in sequence["events"]:
        if coast and event["event"] == "checkpoint_reached":
            for sample in coast["samples"]:
                if sample["active_ticks"]:
                    yield origin + sample["active_ticks"], "free_motion_sample", dict(sample, leg_source=coast["checkpoint"])
        fields = {k:v for k,v in event.items() if k not in ("event","offset")}
        yield origin + event["offset"], event["event"], fields


def run_opening(request, *, tables=None):
    lin_hits = {}
    selection = set(LIN_DRAW_OPTIONS).intersection(request)
    if selection:
        if (len(selection) != 1 or ("lin_hit_talent_seed" in selection and type(request["lin_hit_talent_seed"]) is not int)
                or ("lin_hit_talent_success" in selection and type(request["lin_hit_talent_success"]) is not bool)):
            raise ValueError("受击回技的抽样方式只能选择一种")
        lin_native_stream(request)
        if not request.get("track_damage") or any(
                "observed_outcome" in a for a in request.get("actions", ())):
            raise ValueError("指定林的受击天赋抽取时须启用战斗计算，按键不得带预设结果")
    tables = PinnedTables() if tables is None else tables
    while True:
        report = _run_with_wave_releases(request, tables=tables, lin_hits=lin_hits)
        if not selection:
            return report
        sp = report.get("skill_points_by_operator", {}).get("char_4080_lin")
        joint = (sp or {}).get("combined_incoming_sp")
        if not joint:
            report["lin_hit_talent_replay"] = dict(status="unresolved", reason="no_certified_incoming_hit_history")
            return report
        fresh = defaultdict(list)
        for row in joint["samples"]:
            for hit in row["incoming"]:
                fresh[row["frame"],hit["phase"]].append(hit["source"])
        boundary = report.get("lin_s3", {}).get("sp_button_boundary") or {}
        if boundary.get("frame") == joint["through_frame"] + 1:
            for hit in boundary.get("incoming_before_button", ()):
                fresh[boundary["frame"],hit["phase"]].append(hit["source"])
        if any(fresh.get(k) != v for k,v in lin_hits.items()):
            raise ValueError("林受击技力重放与已核定的先前受击不一致")
        if fresh == lin_hits:
            sp["hit_talent_replay_through_frame"] = joint["through_frame"]
            return report
        # Rebuild motion, damage and wave releases after admitting a new skill input.
        lin_hits = fresh


def _run_with_wave_releases(request, *, tables, lin_hits):
    releases = {}
    while True:
        report = _run_with_vent_removals(request, tables=tables, wave_releases=releases, lin_hits=lin_hits)
        pending = report.get("wave_schedule", {}).get("pending") or {}
        if pending.get("reason") != "wave_enemy_release_frames_unknown":
            return report
        common = report.get("scheduler_release_windows", {})
        updates = {k: common[k] for k in pending["instances"] if k in common and k not in releases}
        if (not updates and request.get("track_damage") and (report.get("stop") or {}).get("reason") == "wave_schedule_pending"
                and any(k not in releases for k in pending["instances"])):
            # Later waves cannot contribute damage before this gate opens. Run the
            # admitted actors alone to obtain their release bounds, then replay
            # normally if those bounds determine a new scheduling prefix.
            probe = _run_with_vent_removals(request, tables=tables, wave_releases=releases, lin_hits=lin_hits,
                                      hold_wave=pending["wave_index"])
            common = probe.get("scheduler_release_windows", {})
            updates = {k: common[k] for k in pending["instances"] if k in common and k not in releases}
        if not updates:
            return report
        releases.update(updates)


def _run_with_vent_removals(request, *, tables, wave_releases, hold_wave=None, lin_hits=None, combat_clock=False):
    removals = {}
    coroutine_deaths = set()
    incoming = {}
    stuns = {}
    while True:
        report = _run_opening_pass(request, tables=tables, wave_releases=wave_releases, hold_wave=hold_wave,
                                   vent_removals=removals, vent_received_hits=list(incoming.values()),
                                   motion_stuns=stuns.values(), coroutine_deaths=coroutine_deaths, lin_hits=lin_hits,combat_clock=combat_clock)
        device = report.get("device_timeline", {})
        if not device:
            return report
        cases = [s["replay"]["enemy_lifecycle"] for s in report.get("mode_replay", {}).get("scenarios", ())]
        finished = _shared_scheduler_releases(cases)
        updates = {r["target_id"]:finished[r["target_id"]][1] for unit in device["devices"].values()
                   for pulse in unit["pulses"] for r in pulse["contacts"] if r["target_id"] in finished
                   and finished[r["target_id"]][1] < pulse["frame"]
                   and finished[r["target_id"]][1] < removals.get(r["target_id"], pulse["frame"])}
        # A prior pass may already have dropped every later pulse contact.
        # Tighten its published removal time when the joint HP bound improves.
        updates.update({k:finished[k][1] for k, previous in removals.items()
                        if k in finished and finished[k][1] < previous})
        for source in (report.get("enemy_resistance_aura") or {}).get("sources", ()):
            if source in finished and (source not in removals or finished[source][1] < removals[source]):
                updates[source] = finished[source][1]
        pending = device.get("next_unresolved_enemy_interaction") or {}
        pending_units = (*pending.get("instances", ()),pending.get("instance_id"))
        updates.update({k:finished[k][1] for k in pending_units if k in finished
                        and finished[k][1] < min(pending["frame"], removals.get(k,pending["frame"]))})
        lin_device = report.get("lin_s3", {}).get("device_sp") or {}
        groups = defaultdict(list)
        for kind in ("direct_hits", "shatter_hits"):
            for hit in lin_device.get(kind, ()):
                if hit.get("guaranteed"):
                    groups[kind, hit["alias"], hit["frame"], hit["phase"]].append(hit)
        hits = {(key, i):h for key, group in groups.items() for i,h in enumerate(group)}
        by_case = [{(c["instance_id"],c["frame"],c["duration_ticks"],c["motion_source"])
                    for row in case["replay"].get("joint_operator_events", ()) for c in row.get("confirmed_stuns", ())}
                   for case in report.get("mode_replay", {}).get("scenarios", ())]
        common = set.intersection(*by_case) if by_case else set()
        deadlines = removals | updates
        kills_by_case = [{(k, e["frame"]) for e in scenario["replay"].get("joint_operator_events", ())
                          if e["phase"] == "COROUTINES" for k,v in e["receiver_updates"].items()
                          if v["kill"] == "certain" and deadlines.get(k) == e["frame"]}
                         for scenario in report.get("mode_replay", {}).get("scenarios", ())]
        known_deaths = set.intersection(*kills_by_case) if kills_by_case else set()
        if not updates and not (hits.keys() - incoming.keys()) and not (common - stuns.keys()) and not (
                known_deaths - coroutine_deaths):
            return report
        coroutine_deaths.update(known_deaths)
        removals.update(updates)
        incoming.update(hits)
        for k,f,d,source in common:
            stuns[k,f,d,source] = dict(instance_id=k, frame=f, duration_ticks=d,motion_source=source)


def _run_opening_pass(request, *, tables, wave_releases, hold_wave=None, vent_removals=None, vent_received_hits=(),
                      motion_stuns=(), coroutine_deaths=(), lin_hits=None,combat_clock=False):
    unknown = set(request) - {"stage_id", "squad", "description", "clock", "actions", "end_frame",
                              "movement", "cost_sample_frames", "track_attack_range", "track_ordinary_attack",
                              "track_skill_points", "sp_sample_frames", "track_damage", "eyja_deployment_seed", *LIN_DRAW_OPTIONS}
    if combat_clock:
        unknown.discard("map_random_seed")
    if unknown:
        raise ValueError(f"未支持的开局输入字段 {sorted(unknown)}")
    tables = PinnedTables() if tables is None else tables
    stage_id = request["stage_id"]
    scene = load_opening_scene(stage_id=stage_id, tables=tables)
    options = scene.level["options"]
    cards = {}
    from .caster_catalog import stage_loadout
    has_aphris=combat_clock and any(p.get("operator_id")=="char_4229_aphris" for p in request["squad"])
    for pick in request["squad"]:
        pick = stage_loadout(tables,pick)
        key = pick["operator_id"]
        if key in cards:
            raise ValueError(f"编队中重复的干员 {key}")
        beam_ally = has_aphris and key != "char_4229_aphris"
        cards[key] = resolve_card(tables, **pick, shared_barrier_recipient=combat_clock and key not in ("char_4080_lin","char_4046_ebnhlz"),
                                  beam_ally=beam_ally)

    grid = scene.level["mapData"]["map"]
    schedule = [] if combat_clock else opening_spawns(scene.level)
    if any(card["initial_cost_bonus"] is None for card in cards.values()):
        raise ValueError("未知编队配置会改变初始费用，需要补足相关精英或等级")
    bonus = sum(card["initial_cost_bonus"] for card in cards.values())
    dp = min(options["maxCost"], options["initialCost"] + bonus)
    free_slots = options["characterLimit"] - sum(scene.predefine_slot_costs.values())
    initial = dict(base_cost=options["initialCost"], squad_bonus=bonus, cost=dp,
                   cost_cap=options["maxCost"], cost_interval_seconds=options["costIncreaseTime"],
                   deployment_limit=options["characterLimit"], free_slots=free_slots,
                   life=options["maxLifePoint"],
                   map_rows=len(grid), map_cols=len(grid[0]), route_count=len(scene.level["routes"]),
                   enemy_types=list(scene.enemies), predefines=scene.predefines, cards=cards)
    report = dict(stage_id=stage_id, code=scene.stage["code"], fps=FPS, initial=initial,
                  relative_schedule=schedule, source=deepcopy(tables.source),
                  scope="opening_economy_first_manual_deploy_and_first_fragment_births")
    clock = request.get("clock")
    spawn_clock = (clock or {}).get("spawn_schedule", "first_fragment")
    if spawn_clock not in {"first_fragment", "native_wave"}:
        raise ValueError("spawn_schedule 需要 first_fragment 或 native_wave")
    movement_schedule = schedule
    if spawn_clock == "native_wave":
        origin = _frame(clock["fragment_start_frame"], "fragment_start_frame")
        horizon = _frame(request["end_frame"], "end_frame")
        delays = {}

        def born_delay(key,route=None):
            if key not in delays:
                prefab = scene.enemies[key]["prefabKey"]
                resource = prefab["m_value"] if prefab["m_defined"] and prefab["m_value"] else key
                fields = tables.enemy_birth(resource)
                delay = fields["_delayToBorn"]
                delays[key] = delay,fields["_onlyDelayToBornOnTileStart"]
            delay,start_only=delays[key]
            if delay and start_only:
                if route is None:
                    return None
                tile=scene.tile(**scene.level["routes"][route]["startPosition"])
                return delay if tile["tileKey"] in ("tile_start","tile_flystart") else 0
            return delay

        def counts_in_wave(key):
            excluded = scene.enemies[key]["notCountInTotal"]
            return not (excluded["m_defined"] and excluded["m_value"])

        played_states=("LOCKED","UNLOCKED","PLAYED","TWO_STAR","THREE_STAR","FOUR_STAR")
        played_state=clock.get("stage_played_state")
        if played_state is not None and played_state not in played_states:
            raise ValueError("关卡通关状态与原生 StagePlayedState 不符")
        def story_skipped(key):
            if played_state is None:
                return False
            row=tables.read("excel/story_table.json").get(key,{})
            rules=(row.get("condition") or {}).get("requiredStages") or []
            if len(rules)!=1 or rules[0]["stageId"]!=scene.stage["stageId"]:
                return False
            lo,hi=rules[0]["minState"],rules[0]["maxState"]
            return lo in played_states and hi in played_states and not (
                played_states.index(lo)<=played_states.index(played_state)<=played_states.index(hi))

        admitted,group_draws=tables.admit_combat_actions(scene.level) if combat_clock else (None,0)
        wave = native_wave_schedule(scene.level, born_delay, tables.fixed_timestep(),
                                    fragment_start_frame=origin, enemy_counts=counts_in_wave,
                                    wave_releases=wave_releases,story_skipped=(lambda key:True) if combat_clock else story_skipped,
                                    admitted=admitted)
        if combat_clock:
            wave.update(battle_random_seed=tables.battle_seed,spawn_group_draws=group_draws)
        if wave["fragments"] and wave["fragments"][0]["start_frame"] < 0:
            raise ValueError("普通波原生时钟需要首片段动作锚点覆盖片段 preDelay")
        report.update(wave_schedule=wave, scope="opening_economy_manual_inputs_and_native_wave_queue",
                      source=deepcopy(tables.source))
        movement_schedule = [e for e in wave["events"] if e["frame"] <= horizon]
    movement = request.get("movement")
    track_sp = request.get("track_skill_points", any(a.get("kind") == "skill" for a in request.get("actions", [])))
    if type(track_sp) is not bool or (track_sp and request.get("clock") is None):
        raise ValueError("track_skill_points 需要布尔值，启用时须指定开局时钟")
    if "sp_sample_frames" in request and not track_sp:
        raise ValueError("技力采样需要启用 track_skill_points")
    if "eyja_deployment_seed" in request:
        from .battle_random import BattleRandom
        BattleRandom(request["eyja_deployment_seed"])
        if not track_sp or "char_180_amgoat" not in cards:
            raise ValueError("艾雅法拉部署随机数需要在编队中选择她并开启技力计算")
    if not track_sp and any(a.get("kind") == "skill" for a in request.get("actions", [])):
        raise ValueError("提交技能按钮时不能关闭技力计算")
    track_range = request.get("track_attack_range", False)
    if type(track_range) is not bool or (track_range and not movement):
        raise ValueError("track_attack_range 需要布尔值，启用时须指定移动时钟锚点")
    track_attack = request.get("track_ordinary_attack", False)
    if type(track_attack) is not bool or (track_attack and not track_range):
        raise ValueError("track_ordinary_attack 需要布尔值，启用时须跟踪射程")
    track_damage = request.get("track_damage", False)
    if type(track_damage) is not bool or (track_damage and not track_attack):
        raise ValueError("track_damage 需要布尔值，启用时须跟踪普通攻击")
    if track_damage:
        report["volley_damage"] = dict(status="not_deployed")
    contacts = RangeContacts() if track_range else None
    first_move_delay = None
    first_born_delay = None
    if movement is not None:
        if type(movement) is not dict or set(movement) - {
                "first_move_delay_frames", "first_born_tick_delay_frames"} or len(movement) > 1:
            raise ValueError("movement 可选出生首次更新或首动的一个时钟锚点")
        if movement:
            if request.get("clock") is None:
                raise ValueError("将移动投影接入战斗帧需要提供开局时钟")
            if "first_born_tick_delay_frames" in movement:
                first_born_delay = _frame(movement["first_born_tick_delay_frames"], "first_born_tick_delay_frames")
            else:
                first_move_delay = _frame(movement["first_move_delay_frames"], "first_move_delay_frames")
        rounded = (clock or {}).get("position_rounding", spawn_clock == "native_wave")
        if type(rounded) is not bool or (rounded and spawn_clock != "native_wave"):
            raise ValueError("position_rounding 的四位坐标取整需要原生波次时钟")
        report["movement"] = _motion_profiles(scene, tables, movement_schedule, track_range,
                native_portals=spawn_clock == "native_wave", round_position=rounded)
        report["movement"]["first_move_delay_frames"] = first_move_delay
        if first_born_delay is not None:
            report["movement"].update(first_born_tick_delay_frames=first_born_delay,
                birth_clock_conditions=["ordinary_BORN_updates_each_frame",
                    "DEFAULT_request_accepted_as_MOVE", "free_walk_starts_on_next_update"])
            for label, profile in report["movement"]["profiles"].items():
                if (profile["birth_timer"]["status"] == "unresolved"
                        and label not in report["movement"]["unresolved_profiles"]):
                    report["movement"]["unresolved_profiles"].append(label)
        report["source"] = deepcopy(tables.source)
    if "description" in request:
        report["description"] = request["description"]
    clock = request.get("clock")
    if clock is None:
        if request.get("actions") or "end_frame" in request or "cost_sample_frames" in request:
            raise ValueError("按帧回放需要给出首片段的时钟锚点")
        return dict(report, status="relative_only")

    horizon = _frame(request["end_frame"], "end_frame")
    sp_samples = {_frame(f, "sp_sample_frames") for f in request.get("sp_sample_frames", [])}
    if any(f > horizon for f in sp_samples):
        raise ValueError("技力采样帧在运行区间以外")
    sample_frames = {_frame(f, "cost_sample_frames") for f in request.get("cost_sample_frames", [])}
    if any(f > horizon for f in sample_frames):
        raise ValueError("费用采样帧在运行区间以外")
    origin = _frame(clock["fragment_start_frame"], "fragment_start_frame")
    order = clock.get("cost_input_order", "unresolved")
    if set(clock) - {"fragment_start_frame", "first_cost_frame", "cost_input_order", "cost_model", "spawn_schedule",
                    "position_rounding","stage_played_state"}:
        raise ValueError("含未识别的时钟条件")
    if order not in {"unresolved", "cost_before_input", "input_before_cost"}:
        raise ValueError("费用到账与部署同帧顺序无效")
    model = clock.get("cost_model", "native_q32")
    anchor = _frame(clock["first_cost_frame"], "first_cost_frame") if "first_cost_frame" in clock else None
    timer = NaturalCostClock(options["costIncreaseTime"], tables.fixed_timestep(), model, anchor)
    if (options["functionDisableMask"] != "NONE" or options["configBlackBoard"]
            or scene.level["mapData"]["effects"] or scene.level["mapData"]["layerRects"]
            or scene.level["operaConfig"] or options["isTrainingLevel"] or options["isHardTrainingLevel"]):
        raise ValueError("关卡开局存在待解析的特殊规则或地图效果")
    first_affordable = {key: _first_affordable_frame(card["cost"], dp, options["maxCost"], timer)
                        for key, card in cards.items()}
    report["first_affordable_frames"] = first_affordable
    actions, waiting_for_cost = _group_actions(request.get("actions", []), horizon, cards,ordered_inputs=combat_clock)
    funded_since = {key: 0 if card["cost"] is not None and dp >= card["cost"] else None
                    for key, card in cards.items()}
    by_frame = defaultdict(list)
    projected_events = defaultdict(list)
    radii = {}
    final_motion_ticks = {}
    absolute_schedule = movement_schedule if spawn_clock == "native_wave" else opening_spawns(scene.level, fragment_start_frame=origin)
    for event in absolute_schedule:
        by_frame[event["frame"]].append(event)
        if (first_move_delay is not None or first_born_delay is not None) and event["kind"] == "spawn":
            instance = event["instance_id"]
            profile_id = report["movement"]["instance_profiles"][instance]
            profile = report["movement"]["profiles"][profile_id]
            for frame, kind, fields in _instance_motion_events(
                    profile, event["frame"], first_move_delay, first_born_delay):
                if frame <= horizon:
                    projected_events[frame].append((kind, dict(instance_id=instance, **fields)))
            if track_range:
                radii[instance] = profile["circle_radius"]
                for leg, sample_kind in (("projection", "free_motion_sample"), ("exit_leg", "exit_motion_sample"),
                                         ("post_wait_leg", "post_wait_motion_sample")):
                    final_motion_ticks[instance, sample_kind] = profile.get(leg, {}).get("arrival_ticks")
                if (profile["projection"]["status"] != "conditional_projection" or
                        (first_born_delay is not None and profile["birth_timer"]["status"] == "unresolved")):
                    projected_events[event["frame"]].append(("range_motion_unresolved", dict(instance_id=instance)))
    applied_stuns = []
    vent_stuns = []
    for c in sorted(motion_stuns, key=lambda c:c["frame"]):
        if c["motion_source"] in ("vent_adjusted","bombardier"):
            vent_stuns.append(c)
        elif apply_walking_stun(projected_events, c["instance_id"], c["frame"], c["duration_ticks"], horizon):
            applied_stuns.append(c)
    vent_segment = None
    if (track_damage and spawn_clock == "native_wave" and scene.stage["stageId"] == "act28side_ex07"
            and {a.get("operator_id") for a in request.get("actions", ()) if a.get("kind") != "skill"} <=
            {"char_4046_ebnhlz", "char_4080_lin"}):
        vent_segment = first_vent_segment(tables, scene, report["movement"], projected_events, radii,
                                          absolute_schedule, horizon, removed_by=vent_removals, received_hits=vent_received_hits,
                                          coroutine_deaths=coroutine_deaths,
                                          placements=[a for a in request.get("actions", ()) if "row" in a and "col" in a],
                                          operator_skins={key:card.get("battle_skin","defaultskin") for key,card in cards.items()},
                                          **({"motion_stuns":vent_stuns} if vent_stuns else {}))
        if vent_segment:
            applied_stuns.extend(vent_segment.get("applied_stuns", ()))
    if applied_stuns:
        report["movement"]["applied_stuns"] = applied_stuns
    if track_range:
        report["attack_range"] = contacts.report
        for profile in report["movement"]["profiles"].values():
            legs = [profile.get(name, {}) for name in ("projection", "exit_leg", "post_wait_leg")]
            legs.extend(part["leg"] for part in profile.get("route_tail", {}).get("parts", []) if "leg" in part)
            legs.extend(seq["wait_coast"] for seq in (profile.get("checkpoints", {}),
                        *profile.get("route_tail", {}).get("parts", ())) if "wait_coast" in seq)
            for leg in legs:
                if "samples" in leg:
                    leg["samples"] = [s for s in leg["samples"] if s["active_ticks"] in (0, 1, leg["arrival_ticks"])
                                      or s["active_ticks"] % FPS == 0]
    loop = FrameLoop()
    deployed = None
    placements = {}
    recoveries = {}
    ranges = {}
    if track_sp:
        report["skill_points"] = dict(status="not_deployed")
        report["skill_points_by_operator"] = {}
    stop = None
    spawned = {}
    predefine_positions = {tuple(u["position"][s] for s in ("row", "col")) for u in scene.predefines}
    cap = options["maxCost"]

    def cost_units():
        return timer.display_units(dp, cap)

    initial["cost_units"] = cost_units()
    initial["cost_bar"] = timer.bar(dp, cap)

    def halt(reason, **details):
        nonlocal stop
        stop = dict(frame=loop.frame, reason=reason, **details)
        loop.emit("opening_unresolved", reason=reason, **details)

    def update_funded_cards():
        for key, card in cards.items():
            if card["cost"] is None or dp < card["cost"]:
                funded_since[key] = None
            elif funded_since[key] is None:
                funded_since[key] = loop.frame

    def gain(_):
        nonlocal dp
        if stop or dp >= cap:
            return
        gained = timer.advance(loop.frame)
        if gained:
            before = dp
            dp = min(cap, dp + gained)
            update_funded_cards()
            loop.emit("natural_cost", before=before, after=dp, cost_bar=timer.bar(dp, cap))

    def fragment(_):
        if (spawn_clock == "native_wave" and wave["pending"] and loop.frame == wave["pending"]["frame"]
                and not combat_clock and hold_wave != wave["pending"]["wave_index"]):
            halt("wave_schedule_pending", wave_index=wave["pending"]["wave_index"],
                 fragment_index=wave["pending"]["fragment_index"],
                 detail=wave["pending"]["reason"])
            return
        for event in by_frame.get(loop.frame, []):
            fields = {k: v for k, v in event.items() if k not in ("kind", "frame")}
            if event["kind"] == "spawn":
                action = scene.level["waves"][event["wave_index"]]["fragments"][event["fragment_index"]]["actions"][event["action_index"]]
                spawned[event["instance_id"]] = dict(enemy=event["enemy_key"],
                                                     route=event["route_index"],
                                                     spawn_frame=loop.frame,
                                                     scheduler_regular_enemy=(action["managedByScheduler"] and
                                                        not action["isUnharmfulAndAlwaysCountAsKilled"] and
                                                        not action.get("notCountInTotal") and not action.get("useExtraRoute")),
                                                     spawn_position=event["route_start"])
            loop.emit(event["kind"], **fields)

    def submit(_, phase="input"):
        nonlocal dp, deployed, free_slots
        for action in actions.get(loop.frame, []):
            if action.get("phase", "input") != phase:
                continue
            if action.get("kind") == "skill":
                key = action["operator_id"]
                recovery = recoveries.get(key)
                if key not in placements:
                    loop.emit("skill_button", operator_id=key, outcome="rejected", cost_bar=timer.bar(dp, cap),
                              reason="not_deployed")
                elif recovery is None:
                    halt("skill_configuration_unresolved", operator_id=key,
                         detail=report["skill_points_by_operator"][key]["reason"])
                else:
                    recovery.press(tables, loop, phase=phase, observed_outcome=action.get("observed_outcome"),
                                   cost_bar=timer.bar(dp, cap))
                    if recovery.report["status"] == "unresolved":
                        halt("skill_button_unresolved", operator_id=key, detail=recovery.report["reason"])
                return
            key, row, col = action["operator_id"], action["row"], action["col"]
            card = cards[key]
            reason = None
            if key in placements:
                reason = "already_deployed"
            elif any(p["row"] == row and p["col"] == col for p in placements.values()):
                reason = "tile_occupied"
            elif not (0 <= row < len(grid) and 0 <= col < len(grid[0])):
                reason = "outside_map"
            elif key in (scene.level["excludeCharIdList"] or []):
                reason = "excluded_character"
            elif (row, col) in predefine_positions:
                halt("predefined_unit_tile_interaction", row=row, col=col)
                return
            elif (scene.tile(row, col)["effects"] or scene.tile(row, col)["blackboard"]
                  or scene.tile(row, col)["playerSideMask"] != "ALL"):
                halt("unresolved_tile_effect_or_side", row=row, col=col)
                return
            elif ({"row": row, "col": col} in (scene.level["tilesDisallowToLocate"] or [])
                    or not tile_permits_card(card, scene.tile(row, col))):
                reason = "illegal_tile"
            elif free_slots < card["slots"]:
                reason = "deployment_limit"
            elif dp < min(card["cost_options"]):
                reason = "insufficient_cost"
            elif card["cost"] is None:
                halt("unknown_card_cost", operator_id=key, costs=card["cost_options"], available=dp)
                return
            fields = dict(operator_id=key, row=row, col=col, direction=action["direction"])
            before_units = cost_units()
            if reason:
                loop.emit("deploy_rejected", **fields, reason=reason, cost_before=dp, cost_after=dp,
                          cost_units_before=before_units, cost_units_after=before_units, cost_bar=timer.bar(dp, cap))
                return
            protection = ManualDeploymentProtection(loop.frame)
            placement = dict(**fields, frame=loop.frame,
                            first_affordable_frame=first_affordable[key],
                            frames_after_first_affordable=loop.frame - first_affordable[key],
                            continuously_affordable_since_frame=funded_since[key],
                            protection_end_exclusive=protection.committed_frame + FPS)
            placements[key] = placement
            if deployed is None:
                deployed = placement
            if track_sp:
                recovery, row_sp = deploy_skill(tables, card, loop.frame,direction=action["direction"])
                if key == "char_180_amgoat" and recovery is not None:
                    from .core_caster_skills import apply_eyja_opening_talent
                    from .eyja_ignition import deployment_elite_query
                    def elite_query():
                        return deployment_elite_query(tables,scene,card,placement,spawned,projected_events,radii,
                                                      vent_removals or {},tracked=track_range,alone=len(placements)==1)
                    recovery = apply_eyja_opening_talent(tables,card,recovery,request.get("eyja_deployment_seed"),elite_query)
                if key == "char_4080_lin":
                    for choice in LIN_DRAW_OPTIONS:
                        if choice in request:
                            row_sp[choice] = request[choice]
                    if "lin_hit_native_seed" in row_sp or "lin_hit_talent_seed" in row_sp:
                        row_sp["rng_scope"]="isolated_Lin_eligible_hits_in_dispatch_order"
                recoveries[key] = recovery
                report["skill_points_by_operator"][key] = row_sp
                if "char_213_mostma" in placements:
                    from .mostima_aura import MOSTIMA, apply_to_live_team
                    apply_to_live_team(tables,cards[MOSTIMA],recoveries,loop.frame,active=True)
                if placement is deployed:
                    report["skill_points"] = row_sp
            if track_range:
                current_range = contacts if placement is deployed else RangeContacts()
                if current_range is not contacts:
                    current_range.positions = contacts.positions
                    current_range.unknown = contacts.unknown
                    current_range.report["unknown_instances"] = contacts.report["unknown_instances"]
                current_range.deploy(tables, card, placement, len(grid), len(grid[0]))
                ranges[key] = current_range
                if placement is deployed and key == "char_4046_ebnhlz":
                    report["mystic_attack"] = ebenholz_timing(tables, card)
                report["source"] = deepcopy(tables.source)
            before = dp
            dp -= card["cost"]
            update_funded_cards()
            free_slots -= card["slots"]
            loop.emit("deployed", **fields, cost_before=before, cost_after=dp, paid=card["cost"],
                      cost_units_before=before_units, cost_units_after=cost_units(),
                      cost_bar=timer.bar(dp, cap),
                      slots_before=free_slots + card["slots"], slots_after=free_slots)

    def before_tick(frame):
        # Evaluate a one-shot cost trigger with the actual prior purchases. The
        # chosen input phase still controls whether this frame's credit can be spent.
        credit = timer.due(frame)
        available = min(cap, dp + credit)
        ready = [a for a in waiting_for_cost if cards[a["operator_id"]]["cost"] <= available]
        if ready:
            if len(ready) != 1 or actions.get(frame):
                raise ValueError("同帧多条玩家指令的提交顺序尚未核定")
            actions[frame].extend(ready)
            waiting_for_cost.remove(ready[0])
        if order != "unresolved" or frame not in actions or not credit:
            return False
        if actions[frame][0].get("kind") == "skill":
            return False
        # A trigger asks to spend on the crediting frame. A fixed input alone
        # does not specify that ordering. An explicit clock overrides either.
        return "cost_input_order" in clock or "when" not in actions[frame][0]

    # The placement of hooks is a named scenario condition until verified in this release.
    def cost_phase(tick):
        if order != "input_before_cost":
            gain(tick)

    def input_phase(tick):
        submit(tick)
        if order == "input_before_cost":
            gain(tick)

    loop.register(Phase.COST, cost_phase)
    if spawn_clock == "first_fragment":
        loop.register(Phase.LEVEL_EVENTS, fragment)
    loop.register(Phase.INPUT, input_phase)
    hit_stream=lin_native_stream(request)
    def apply_lin_rolls(tick):
        if not lin_hits:
            return
        for i, source in enumerate(lin_hits.get((tick.frame,tick.phase.name), ())):
            recovery = recoveries["char_4080_lin"]
            recovery.receive_hit(tick, ignore_for_sp=False, source=source,
                dice_succeeded=lin_hit_draw(recovery.report,tick.frame,tick.phase.name,i,source,hit_stream))
    if any(a.get("phase") == "after_tick" for group in actions.values() for a in group):
        def finish_fixed(tick):
            if not stop:
                for recovery in recoveries.values():
                    if recovery is not None:
                        recovery.after_fixed(tick)
        loop.register(Phase.UNITY_FIXED_RESUME, finish_fixed)
        loop.register(Phase.AFTER_TICK, lambda tick: submit(tick, "after_tick") if not stop else None)
    if track_sp:
        def tick_skill(tick):
            if not stop:
                for recovery in recoveries.values():
                    if recovery is not None:
                        recovery.tick(tick)
                apply_lin_rolls(tick)
        loop.register(Phase.OBJECTS, tick_skill)
    if track_sp or spawn_clock == "native_wave":
        def after_input(tick):
            if spawn_clock == "native_wave" and not stop:
                fragment(tick)
            if track_sp and not stop:
                for recovery in recoveries.values():
                    if recovery is not None:
                        recovery.after_input(tick)
                apply_lin_rolls(tick)
        loop.register(Phase.COROUTINES, after_input)
    if sample_frames or track_sp:
        if sample_frames:
            report["cost_samples"] = []
        def sample_cost(tick):
            if not stop and tick.frame in sample_frames:
                report["cost_samples"].append(dict(frame=tick.frame, cost=dp, cost_units=cost_units(),
                                                   cost_bar=timer.bar(dp, cap)))
            if not stop:
                for recovery in recoveries.values():
                    if recovery is not None:
                        recovery.sample(tick.frame, sp_samples)
        loop.register(Phase.ENVIRONMENT, sample_cost)
    if first_move_delay is not None or first_born_delay is not None:
        def sample_motion(tick):
            if not stop:
                current = projected_events.get(tick.frame, [])
                for kind, fields in current:
                    if track_range:
                        contacts.update(tick.frame, kind, fields)
                        if kind == "range_motion_unresolved":
                            for current_range in ranges.values():
                                current_range.inside.discard(fields["instance_id"])
                        ticks = fields.get("active_ticks")
                        if (ticks is not None and ticks > 1 and ticks % FPS
                          and not fields.get("leg_end") and ticks != final_motion_ticks.get((fields["instance_id"], kind))):
                            continue
                    tick.emit(kind, **fields)
                if track_range:
                    for current_range in ranges.values():
                        current_range.evaluate(tick, radii)
                    for kind, fields in current:
                        if kind == "route_end_in_range":
                            contacts.positions.pop(fields["instance_id"], None)
                            for current_range in ranges.values():
                                current_range.inside.discard(fields["instance_id"])
        loop.register(Phase.LATE_OBJECTS, sample_motion)
    blocked_frame = None
    if before_tick(0):
        blocked_frame = 0
    else:
        loop.run_initial_frame()
    while not stop and blocked_frame is None and loop.frame < horizon:
        if before_tick(loop.frame + 1):
            blocked_frame = loop.frame + 1
        else:
            loop.step()
    if blocked_frame is not None:
        stop = dict(frame=blocked_frame, reason="cost_and_input_order_unresolved",
                    available_if_input_first=dp, available_if_cost_first=min(cap, dp+timer.due(blocked_frame)),
                    operator_id=actions[blocked_frame][0]["operator_id"])
    aura = resistance_aura(tables,scene,spawned,projected_events) if track_range else None
    if aura:
        report["enemy_resistance_aura"] = aura.report(loop.frame)
    lin_key = "char_4080_lin"
    if (track_damage and track_range and track_sp and lin_key in placements
            and cards[lin_key]["skill_id"] == "skchr_lin_3"):
        active = lin_s3_replay(tables, scene, cards[lin_key], placements[lin_key],
                               report["skill_points_by_operator"][lin_key], spawned, projected_events, radii,
                               loop.frame, vent_removals or {}, aura=aura, energy_timeline=vent_segment)
        if active is not None:
            if applied_stuns:
                active["applied_stuns"] = applied_stuns
            report["lin_s3"] = active
    if track_attack and deployed is not None:
        attack_horizon = loop.frame
        coverage = None
        if len(placements) > 1:
            bombardier = (vent_segment or {}).get("bombardier")
            if lin_key in placements:
                bombardier = lin_shell_window(tables, cards[lin_key], report.get("skill_points_by_operator", {}).get(lin_key, {}),
                                              bombardier)
            coverage = inactive_window(tables, scene, cards, placements, report.get("skill_points_by_operator"),
                                       spawned, loop.frame, wave_schedule=wave if spawn_clock=="native_wave" else None,
                                       bomber_window=bombardier)
            attack_horizon = coverage["through_frame"]
            report["attack_coverage"] = dict(coverage, operators=list(placements))
        if any(u["inst"]["characterKey"] == "trap_124_eradio" for u in scene.predefines):
            device = opening_vents(tables, scene, cards, placements, report.get("skill_points_by_operator", {}),
                                   spawned, projected_events, radii, loop.frame, sp_samples, coverage,
                                   removed_by=vent_removals, received_hits=vent_received_hits)
            report["device_timeline"] = device
            joint = certify_joint_activation(report.get("lin_s3"), coverage, device,
                sp=report.get("skill_points_by_operator",{}).get(lin_key),
                protection_end=placements.get(lin_key,{}).get("protection_end_exclusive"))
            if joint is not None:
                attack_horizon = max(attack_horizon, joint)
                report["attack_coverage"]["including_lin_s3_through_frame"] = joint
            pending_vent = device["first_possible_enemy_interaction"]
            if pending_vent is not None:
                report["movement"]["unmodified_motion_boundary"] = pending_vent
                applied = (vent_segment is not None and vent_segment["frame"] == pending_vent["frame"]
                           and vent_segment["instance_id"] == pending_vent.get("instance_id"))
                if applied:
                    device["first_enemy_effect"] = vent_segment
                    effects = {(e["alias"], e["frame"], e["instance_id"]):e for e in vent_segment["effects"]}
                    for alias, unit in device["devices"].items():
                        for pulse in unit["pulses"]:
                            for recipient in pulse["contacts"]:
                                hit = effects.get((alias, pulse["frame"], recipient["target_id"]))
                                if hit:
                                    recipient.update(damage_after_resistance_q32=hit["damage_q32"],
                                                     damage_bounds_q32=hit["damage_bounds_q32"], energy=dict(
                                        before=hit["energy_before"], after=hit["energy_after"], overload=hit["overload"]))
                    device["next_unresolved_enemy_interaction"] = vent_segment["pending"]
                attack_horizon = min(attack_horizon, vent_segment["through_frame"] if applied else pending_vent["frame"] - 1)
                if coverage is not None:
                    report["attack_coverage"]["unmodified_enemy_motion_through_frame"] = min(attack_horizon, pending_vent["frame"] - 1)
                    report["attack_coverage"]["including_device_effects_through_frame"] = attack_horizon
        if "mystic_attack" in report:
            timing = report["mystic_attack"]
            profile = ordinary_profile(tables, cards[deployed["operator_id"]], timing)
            sp = report.get("skill_points", {})
            sp = dict(sp, deployment_frame=deployed["frame"], buttons=[b for b in sp.get("buttons", []) if b["frame"] <= attack_horizon],
                      natural_ends=[f for f in sp.get("natural_ends", []) if f <= attack_horizon])
            attack_end = min([attack_horizon] + [
                b.get("mode_effect_frame", b["frame"]) - int(b.get("input_phase") != "after_tick")
                for b in sp["buttons"] if b["outcome"] in ("activated", "unresolved")
                and not b.get("mode_canceled_before_effect")])
            priority = (first_leg_priority(tables, scene, cards[deployed["operator_id"]], spawned, projected_events)
                        if profile["status"]=="ordinary_profile" else None)
            report["ordinary_attack"] = first_volley(profile, timing, deployed, loop.events,
                                                     contacts.report["unknown_instances"], attack_end, priority=priority)
            switches = attack_switches(tables, cards[deployed["operator_id"]], report["ordinary_attack"], timing, sp,
                                       include_ordinary=track_damage)
            flight_end = attack_end
            if switches is not None and switches.get("status") != "ordinary_mode":
                report["attack_mode_switch"] = switches
                if switches.get("clear_issued_on_ordinary_cast_end") == {"main": False, "stored": False}:
                    flight_end = attack_horizon
            projectiles = first_projectiles(tables, scene, deployed, report["ordinary_attack"],
                                            spawned, projected_events, flight_end,
                                            round_position=clock.get("position_rounding", True))
            report["ordinary_projectiles"] = projectiles
            if flight_end > attack_end and projectiles["status"] == "conditional_projectile_reach":
                projectiles["conditions"].append("already_issued_volley_no_other_projectile_disposal_on_mode_change")
            base_resistance_through = None
            device = report.get("device_timeline", {})
            scope = device.get("opening_enemy_input_scope", {})
            if (spawn_clock == "native_wave" and scene.stage["stageId"] == "act28side_ex07"
                    and scope.get("reason") == "unblocked_melee_only_or_no_attack_on_highland_predefined_vents"):
                # The two opening enemy types start at zero energy. Device pulses can extend this
                # interval when their energy accounting still excludes the overloaded state.
                boundaries = [attack_horizon+1]
                if scope["next_unreviewed_enemy"] is not None:
                    boundaries.append(scope["next_unreviewed_enemy"])
                device_limit = (device.get("first_enemy_effect") or {}).get("base_resistance_through_frame")
                if device_limit is None:
                    boundaries.extend(p["frame"] for v in device["devices"].values() for p in v["pulses"])
                else:
                    boundaries.append(device_limit + 1)
                base_resistance_through = min(boundaries)-1
                device["base_enemy_resistance_through_frame"] = base_resistance_through
            if track_damage:
                report["volley_damage"] = first_volley_damage(tables, scene, cards[deployed["operator_id"]],
                    spawned, projectiles, flight_end, history=projected_events,
                    source_mode_by_impact_frame=impact_mode_changes(switches or {}),
                    base_resistance_through=base_resistance_through, aura=aura, energy_timeline=device.get("first_enemy_effect"))
            if switches is not None:
                covered_exits = (track_damage and spawn_clock == "native_wave" and all(
                    scene.tile(p["row"], p["col"])["passableMask"] == "FLY_ONLY" for p in placements.values()))
                report["mode_replay"] = mode_replay(tables, scene, cards[deployed["operator_id"]], deployed,
                    switches, report["ordinary_attack"], sp, report.get("volley_damage"), loop.events,
                    contacts.report["unknown_instances"], spawned, projected_events, attack_horizon,
                    round_position=clock.get("position_rounding", True), priority=priority, covered_exits=covered_exits,
                    device_hit=(report.get("device_timeline") or {}).get("first_enemy_effect"),
                    base_resistance_through=base_resistance_through, aura=aura, lin_active=report.get("lin_s3"))
            if track_damage and spawn_clock == "native_wave":
                cases = [s.get("replay", {}).get("enemy_lifecycle", {"finishes":[]})
                         for s in report.get("mode_replay", {}).get("scenarios", ())]
                if not cases:
                    hp = HealthBounds(scene, spawned)
                    first_damage = report["volley_damage"]
                    arrivals = [s["launch_frame"] for s in first_damage.get("scenarios", ())]
                    if arrivals:
                        hp.apply(first_damage, release_frame=min(arrivals))
                    for hit in (report.get("device_timeline", {}).get("first_enemy_effect") or {}).get("effects", ()):
                        if hit["frame"] <= attack_horizon and not hp.apply_device_hit(hit):
                            attack_horizon = hit["frame"] - 1
                            break
                    ends = {e["instance_id"]:e["frame"] for e in loop.events if e["kind"] == "route_end_in_range"}
                    cases = [ordinary_enemy_finishes(scene, spawned, hp, ends, attack_horizon)]
                if len(scene.level["waves"]) > 1:
                    report["scheduler_release_windows"] = _shared_scheduler_releases(cases)
                motion = report["movement"]
                untracked = set(motion["unresolved_profiles"])
                route_limit = min([attack_horizon] + [v-1 for v in contacts.report["unknown_instances"].values()]
                    + [rec["spawn_frame"]-1 for k,rec in spawned.items() if motion["instance_profiles"][k] in untracked])
                life = ordinary_life_points(initial["life"], cases, route_limit)
                if life is not None:
                    report["life_points"] = life
                    report["battle_outcome"] = ordinary_battle_outcome(scene, wave, cases, life, tables.fixed_timestep())
            report["source"] = deepcopy(tables.source)
        else:
            card = cards[deployed["operator_id"]]
            caster_priority = remaining_route_priority(scene, spawned, projected_events)
            skill_buttons = list(report.get("skill_points",{}).get("buttons",()))
            if (stop and stop.get("reason")=="skill_configuration_unresolved" and
                    stop.get("operator_id")==deployed["operator_id"]):
                skill_buttons.append(dict(frame=stop["frame"],outcome="unresolved"))
            report["ordinary_attack"] = first_caster_attack(tables, card, deployed, loop.events,
                contacts.report["unknown_instances"], attack_horizon, skill_buttons,
                trace=track_damage,priority=caster_priority)
            if track_damage:
                report["ordinary_projectiles"], report["volley_damage"], report["caster_repeat"] = caster_combat(
                    tables, scene, card, deployed, report["ordinary_attack"], spawned, projected_events, loop.events,
                    contacts.report["unknown_instances"], round_position=clock.get("position_rounding",True),
                    aura=aura,priority=caster_priority,eyja_card=cards.get("char_180_amgoat"))
            report["source"] = deepcopy(tables.source)
    if report.get("lin_s3", {}).get("activations"):
        cases = [s["replay"] for s in report.get("mode_replay", {}).get("scenarios", ())]
        lifecycles = [case.get("enemy_lifecycle", {"finishes": []}) for case in cases]
        if cases:
            active = report["lin_s3"]
            active["shared_life_through_frame"] = min(case["last_frame"]-int(case["stop"]!="horizon") for case in cases)
            shell_window = (report.get("attack_coverage") or {}).get("bombardier_window") or {}
            if active.get("joint_coverage"):
                sp = report["skill_points_by_operator"][lin_key]
                certified = min(active["shared_life_through_frame"],active["joint_coverage"]["through_frame"])
                active["joint_coverage"]["executed_through_frame"] = certified
                report["attack_coverage"]["joint_combat_through_frame"] = certified
                sp["combined_incoming_sp"] = lin_joint_sp(sp, report["device_timeline"], shell_window.get("impacts", ()),
                    certified, sp_samples, placements[lin_key]["protection_end_exclusive"])
            hits = [{(h["frame"],h["alias"],h["phase"]) for row in case.get("joint_operator_events", ())
                     for h in row.get("scheduled_shatter_device_hits", ())} for case in cases]
            if active.get("device_sp"):
                active["device_sp"]["shatter_hits"] = [
                    dict(frame=f,alias=k,phase=p,guaranteed=True) for f,k,p in sorted(set.intersection(*hits))
                    if f<=loop.frame and f-1<=active["shared_life_through_frame"]]
        finished = _shared_scheduler_releases(lifecycles)
        for start in report["lin_s3"]["activations"]:
            for attack in start["conditional_attacks"]:
                for group in ("selected_if_eligible","unlocated_possible_targets"):
                    attack[group] = [k for k in attack[group] if k not in finished or finished[k][1] >= attack["start_frame"]]
                if "damage_if_alive_and_selectable" in attack:
                    attack["damage_if_alive_and_selectable"] = {k:v for k,v in attack["damage_if_alive_and_selectable"].items()
                                                               if k in attack["selected_if_eligible"]}
                if not attack["selected_if_eligible"] and not attack["unlocated_possible_targets"]:
                    attack.pop("on_any_direct_kill",None)
                    attack.pop("refresh_if_direct_kill",None)
    if track_sp:
        report["source"] = deepcopy(tables.source)
    if track_range:
        report["attack_ranges_by_operator"] = {key: item.report for key, item in ranges.items()}
    return dict(report, clock=dict(clock, cost_model=model, first_cost_frame=timer.first, cost_units_per_point=timer.units),
                status="unresolved" if stop else "conditional_projection",
                stop=stop, last_frame=loop.frame, events=loop.events,
                final=dict(cost=dp, cost_units=cost_units(), cost_bar=timer.bar(dp, cap), free_slots=free_slots, first_deployment=deployed,
                           deployments=list(placements.values()),
                           first_fragment_birth_records={k: v for k, v in spawned.items() if k.startswith("w0-f0-")},
                           **({"wave_birth_records": spawned} if spawn_clock == "native_wave" else {})))


def _shared_scheduler_releases(cases):
    by_case = []
    for case in cases:
        rows = {r["instance"]:(r["first_possible_frame"], r["scheduler_killed_by_frame"]) for r in case["finishes"]}
        rows.update({e["instance"]:e["scheduler_release_window"] for e in case.get("exits", ())})
        by_case.append(rows)
    if not by_case:
        return {}
    common = set.intersection(*(set(rows) for rows in by_case))
    return {k: [min(rows[k][0] for rows in by_case), max(rows[k][1] for rows in by_case)] for k in sorted(common)}

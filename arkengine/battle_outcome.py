"""Default ordinary-stage victory conditions, independent of the displayed kill counter."""
from .opening_spawns import _wait_ticks


def ordinary_battle_outcome(scene, schedule, cases, life, fixed_timestep, *, use_stage_objective=False):
    """Summarize complete, counted ordinary spawns across feasible combat branches."""
    through = life["through_frame"]
    result = dict(status="unresolved", through_frame=through, final_life_bounds=None,
                  projected_stars=None, finish_game_frame=None)
    options = scene.level["options"]
    if not use_stage_objective and (scene.stage["difficulty"] != "NORMAL" or scene.stage["stageType"] not in ("MAIN", "ACTIVITY", "DAILY")
            or scene.stage.get("isStoryOnly") or scene.level.get("branches") or scene.level.get("operaConfig")
            or options.get("maxPlayTime") != -1 or options.get("isTrainingLevel")
            or options.get("isHardTrainingLevel") or not cases):
        return dict(result, reason="ordinary_stage_rules_not_established")

    life_min, life_max = life["life_min"], life["life_max"]
    result.update(initial_life=life["initial"], life_bounds_at_horizon=[life_min, life_max])
    if life_min == 0:
        # The ordinary mode calls FinishGame(loss) on zero life, including
        # while the scheduler still has waves to spawn. A surviving branch
        # can continue after the shared life horizon ends.
        for hp_key, frame_key in (("life_min", "first_possible_defeat_frame"),
                                  ("life_max", "all_scenarios_defeated_by_frame")):
            result[frame_key] = next((e["frame"] for e in life["events"]
                                      if e["frame"] <= through and e[hp_key] == 0), None)
        if life_max == 0:
            result.update(status="defeat", final_life_bounds=[0, 0])
        else:
            result["reason"] = "defeat_possible"

    # The last fragment can be scheduled beyond the combat horizon. Never count
    # a partial queue as the stage total, or a future release as a current kill.
    waves = scene.level["waves"]
    if (not waves or schedule["pending"] or len(schedule["waves"]) != len(waves)
            or schedule["waves"][-1]["wave_index"] != len(waves)-1):
        return result if life_min == 0 else dict(result, reason="wave_schedule_incomplete")
    counted = set()
    final_wave = set()
    for e in schedule["events"]:
        if e["kind"] != "spawn":
            continue
        wave = waves[e["wave_index"]]
        action = wave["fragments"][e["fragment_index"]]["actions"][e["action_index"]]
        if not use_stage_objective and (action.get("isUnharmfulAndAlwaysCountAsKilled") or action.get("notCountInTotal")
                or scene.enemies[e["enemy_key"]]["notCountInTotal"]["m_value"]):
            return result if life_min == 0 else dict(result, reason="special_enemy_counting")
        counted.add(e["instance_id"])
        if e["wave_index"] == len(waves)-1 and not action["dontBlockWave"]:
            final_wave.add(e["instance_id"])
    result["total_targets"] = len(counted)
    releases = []
    for case in cases:
        rows = {r["instance"]: (r["first_possible_frame"], r["scheduler_killed_by_frame"])
                for r in case["finishes"] if r["finished_by_frame"] <= through}
        rows.update({e["instance"]: e["scheduler_release_window"]
                     for e in case["exits"] if e["frame"] <= through})
        releases.append(rows)
    counts = [len(counted.intersection(rows)) for rows in releases]
    result["confirmed_processed_by_scenario"] = counts
    if life_min == 0:
        return result
    if any(c["unresolved"] or rows.keys() != counted for c, rows in zip(cases, releases)):
        return dict(result, reason="target_lifecycle_incomplete")

    poll = schedule["waves"][-1]["fragments_complete_frame"] + 1
    # In the final wave _DealWave has no next-wave timeout. Its fixed post-
    # delay yields at least once, including a configured delay of zero.
    post_ticks = _wait_ticks(waves[-1]["postDelay"], fixed_timestep)
    def ready_frame(rows, end):
        wave_exit = max([poll] + [rows[key][end] for key in final_wave])
        return max([wave_exit + post_ticks] + [v[end] for v in rows.values()])

    window = [min(ready_frame(rows, 0) for rows in releases),
              max(ready_frame(rows, 1) for rows in releases)]
    result["last_wave_and_targets_ready_frames"] = window
    if counted:
        # V079 ordinary play uses Unity fixed coroutines. A wait started before
        # this tick's fixed resumption can advance once on the enemy's finish tick.
        ticks = _wait_ticks(1.0, fixed_timestep)
        recycle = [(max(v[0] for v in rows.values())+ticks-1,
                    max(v[1] for v in rows.values())+ticks) for rows in releases]
        clock_conditions = ["v079_default_enemy_delay_and_active_battle_pool", "continuous_fixed_battle_updates",
                            "no_enemy_recycle_or_scheduler_override"]
        result["last_enemy_recycle_projection"] = dict(
            fixed_wait_ticks=ticks, frames=[min(a for a, b in recycle), max(b for a, b in recycle)],
            conditions=clock_conditions)
        # WaitWhileFixed yields once before polling. If the scheduler polls just
        # before recycling on that fixed update, it finishes on the next poll.
        # Pair each scenario's wave and recycle gates before combining bounds.
        finish_min = min(max(ready_frame(rows, 0), a) for rows, (a, b) in zip(releases, recycle))
        finish_max = max(max(ready_frame(rows, 1), b)+1 for rows, (a, b) in zip(releases, recycle))
        result["finish_game_projection"] = dict(frames=[finish_min, finish_max],
            within_combat_horizon=finish_max <= through,
            conditions=clock_conditions + ["single_ordinary_scheduler_without_after_battle_wave_or_finish_blocker",
                "nested_coroutines_resume_parent_on_child_completion_without_an_additional_player_loop_delay"])
    elif use_stage_objective:
        result["finish_game_projection"] = dict(frames=window, within_combat_horizon=window[1]<=through)
    if window[1] > through:
        return dict(result, reason="final_wave_delay")
    # V079 can hold the final scheduler list until recycling, after HP-zero.
    # Report the result implied by these conditions, without dating FinishGame.
    stars = sorted({2 + int(hp >= options["maxLifePoint"]) for hp in (life_min, life_max)})
    return dict(result, status="victory_projected", final_life_bounds=[life_min, life_max],
                projected_stars=stars, conditions=["default_mode_no_external_finish_blocker",
                    "ordinary_final_enemies_eventually_recycled", "no_after_battle_plugin_wave"])

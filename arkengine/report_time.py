"""Label absolute stage timestamps for presentation after the simulation completes."""
from .frames import FPS


def _stamp(row, field="frame", label="logic", *, destination=None):
    if not isinstance(row, dict):
        return
    frame = row.get(field)
    if type(frame) is int and frame >= 0:
        target = row if destination is None else destination
        seconds, remainder = divmod(frame, FPS)
        minutes, seconds_in_minute = divmod(seconds, 60)
        target[f"{label}_timecode"] = f"{minutes:02}:{seconds_in_minute:02}:{remainder:02}"
        target[f"{label}_time"] = f"{seconds}秒{remainder}帧"


def _stage_events(section):
    # These fields hold stage events. Nested action clocks and video evidence
    # have their own origins, so do not walk the entire report recursively.
    for key in ("events", "cost_samples", "hits", "escapes", "allied_deployments",
                "received_hits", "buttons", "ready", "skill_point_checks", "attacks",
                "global_attachments", "local_aura_contacts", "pulse_targets", "joint_operator_events",
                "relay_events", "beam_changes"):
        rows = section.get(key)
        if isinstance(rows, list):
            for row in rows:
                _stamp(row)
    _stamp(section.get("deployment"))


def annotate_stage_times(report):
    """Preserve frame numbers, measured cost fields and simulator return contracts."""
    if report.get("status") == "relative_only":
        return
    _stage_events(report)
    for field in ("through", "last", "end"):
        _stamp(report, f"{field}_frame", field)
    for field in ("cast", "wave_schedule"):
        section = report.get(field)
        if isinstance(section, dict):
            _stage_events(section)
    elemental = report.get("element_events")
    if isinstance(elemental, dict):
        for rows in elemental.values():
            for row in rows:
                _stamp(row)
    elif isinstance(elemental, list):
        for row in elemental:
            _stamp(row)
    spawns = report.get("spawns")
    if isinstance(spawns, dict):
        for row in spawns.values():
            _stamp(row)
    elif isinstance(spawns, list):
        for row in spawns:
            _stamp(row)
    for scenario in (report.get("mode_replay") or {}).get("scenarios", ()):
        if isinstance(scenario.get("replay"), dict):
            _stage_events(scenario["replay"])
    for sp in (report.get("skill_points_by_operator") or {}).values():
        if isinstance(sp, dict):
            _stage_events(sp)
            sp["ready_times"] = [dict(frame=f) for f in sp.get("ready_frames", ())]
            for row in sp["ready_times"]:
                _stamp(row)


def annotate_caster_samples(report):
    """A stand-alone callback has the origin supplied by its caller."""
    _stamp(report, label="sample")
    for entry in report.get("events", ()):
        _stamp(entry.get("input"), label="sample", destination=entry)
        if entry.get("input",{}).get("kind") in ("sampling_ally_combat","mantra_s2_scene"):
            result=entry["result"]
            for row in [*result["events"],*result.get("relay_events",())]:
                _stamp(row,label="sample")
            _stamp(result,"through_frame","through")

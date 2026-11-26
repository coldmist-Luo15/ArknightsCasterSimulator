"""Resistance granted by the recorded energy overload of one ordinary Blacksteel enemy."""


def overload_resistance_bounds(timeline, instance, frame, *, phase="OBJECTS"):
    """Return the additive resistance interval or None outside the verified vent/energy history."""
    if phase not in ("OBJECTS", "COROUTINES"):
        raise ValueError("需要对象或协程阶段的法抗")
    if not timeline or "overloads" not in timeline or frame > timeline["through_frame"]:
        return None
    history = timeline.get("overload_history", {}).get(instance)
    if history is None:
        first = timeline["overloads"].get(instance)
        history = [first] if first else []
    for over in reversed(history):
        earliest = over.get("earliest_buff_frame", over["buff_frame"])
        if frame < earliest or (phase == "OBJECTS" and frame == earliest):
            continue
        end = over.get("expiry_object_frames", [over["through"] + 1] * 2)
        if frame > end[1]:
            break
        bonus = over["resistance_bonus"]
        if frame <= over["buff_frame"] or frame >= end[0]:
            return 0, bonus
        return bonus, bonus
    return 0, 0

"""Multi-target ordinary MeleeAttack spells from an input point or the owner's oriented cells."""
from math import dist

from .attack_range import circle_in_range
from .damage import HP_EPSILON, ordinary_arts_hit
from .enemy_aura import DRONE
from .volley_damage import _position_samples, _raw_f32


def direct_area_damage(tables, scene, spawned, history, casts, ledger, attack, *, radius=None, cells=None, aura=None, attack_at=None):
    """Evaluate a radial input-point attack or an owner-range reselect at the spell event."""
    line = cells is not None
    stop = "direct_line_impact" if line else "direct_splash_impact"
    if not casts or any(c["stop"] != stop for c in casts):
        return dict(status="unresolved",reason="direct_line_cast_or_impact_not_reached" if line else "direct_splash_cast_or_impact_not_reached")
    frames = {f for c in casts for f in range(c["start_frame"]-1,c["impact_frame"]+1)}
    active = {}
    samples = _position_samples(history,frames,active)
    exits = {row["instance_id"]:f for f,events in history.items() for kind,row in events if kind=="route_end_in_range"}
    scenarios = []
    for cast in casts:
        original, impact = cast["target"],cast["impact_frame"]
        power = attack_at(impact) if attack_at else attack
        centers = {samples[f].get(original) for f in range(cast["start_frame"]-1,impact+1)}
        if not line and (None in centers or ledger.floor_at(original,impact-1) <= HP_EPSILON or exits.get(original,impact+1) <= impact):
            return dict(status="unresolved",reason="splash_origin_or_main_receiver_unknown",target=original)
        upper = ledger.before(impact)
        hits, updates, lower = [], [], []
        for target,birth in spawned.items():
            if birth["spawn_frame"] > impact or exits.get(target,impact) < impact or target in ledger.dead_by(impact):
                continue
            enemy = scene.enemies[birth["enemy"]]
            if target == original and not line:
                guaranteed = True
            else:
                body = enemy["prefabKey"]
                root_radius = tables.enemy_circle(body["m_value"] if body["m_defined"] and body["m_value"] else birth["enemy"])
                options = {samples[f].get(target) for f in (impact-1,impact)}
                if line:
                    inside = {circle_in_range(*point,root_radius,cells,box_query=True) for point in options if point is not None}
                    if inside == {False} and None not in options:
                        continue
                    geometric_hit = inside == {True}
                else:
                    distances = [dist(center,point) for center in centers for point in options if point is not None]
                    if distances and None not in options and min(distances) > radius+root_radius+1e-5:
                        continue
                    # Input-point timing/edge uncertainty still allows every center strictly inside the radius.
                    geometric_hit = bool(distances) and max(distances) < radius-1e-5
                guaranteed = (geometric_hit and None not in options and target in active[impact-1] and target in active[impact]
                              and birth["spawn_frame"] < impact and exits.get(target) != impact)
            if (target not in ledger.maximum or len(ledger.resistances[target]) != 1 or enemy["skills"]
                    or (enemy["talentBlackboard"] and not (aura and birth["enemy"] == DRONE))
                    or enemy["attributes"]["hpRecoveryPerSec"]["m_value"]):
                return dict(status="unresolved",reason="splash_receiver_effects_unreviewed",target=target)
            base, = ledger.resistances[target]
            lo = ledger.floor_at(target,impact-1)
            hi = upper.get(target,{}).get(base,ledger.maximum[target])
            bonuses = (aura.bonus_bounds(target,impact,dead_by=ledger.dead_by(impact),phase="COROUTINES",
                        source_alive=lambda unit,f: ledger.floor_at(unit,f-1)-power>HP_EPSILON)
                       if aura else (0,0))
            maximum = ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(base+bonuses[0]),
                                        hp_raw=lo if lo > HP_EPSILON else hi)
            minimum = ordinary_arts_hit(attack_raw=power,resistance_raw=_raw_f32(base+bonuses[1]),hp_raw=hi)
            after = minimum.hp_after_raw if guaranteed else hi
            hp_lower = maximum.hp_after_raw if lo > HP_EPSILON else 0
            record = dict(instance=target,frame=impact,resistance=base,hp_lower_after_q32=hp_lower)
            lower.append(record)
            updates.append(dict(record,kind="line" if line else ("primary" if target == original else "splash"),hp_upper_after_q32=after,
                                coverage="all_feasible_within_case"))
            hits.append(dict(instance=target,guaranteed=guaranteed,damage_if_hit_q32=[minimum.damage_raw,maximum.damage_raw],
                             hp_upper_before_q32=hi,hp_upper_after_q32=after,hp_lower_after_q32=hp_lower))
        if original not in ledger.maximum or len(ledger.resistances[original])!=1:
            return dict(status="unresolved",reason="cast_trigger_health_unknown",target=original)
        base, = ledger.resistances[original]
        previous = upper.get(original,{}).get(base,ledger.maximum[original])
        main = next((h for h in hits if h["instance"] == original),dict(hp_upper_before_q32=previous,hp_upper_after_q32=previous))
        row = dict(frame=impact,hp_upper_before_q32=main["hp_upper_before_q32"],hp_upper_after_q32=main["hp_upper_after_q32"])
        if attack_at:
            row["attack_q32"] = power
        case = dict(resistance=base,frames=[row],receiver_updates=updates,receiver_lower_updates=lower,
                    receiver_lower_coverage_complete=True,not_alive_by_frame=impact if row["hp_upper_after_q32"]<=HP_EPSILON else None)
        scenarios.append(dict(target=original,start_frame=cast["start_frame"],impact_frame=impact,hits=hits,resistance_cases=[case]))
    if attack_at:
        powers = {attack_at(c["impact_frame"]) for c in casts}
        attack = next(iter(powers)) if len(powers) == 1 else None
    if line:
        return dict(status="conditional_direct_line_damage",attack_q32=attack,scenarios=scenarios,
                    cells=[dict(row=r,col=c) for r,c in cells],conditions=["owner_range_reselected_before_spell","normal_targetability_no_interception_or_healing",
                                            "physics_contact_bounded_over_adjacent_samples"])
    return dict(status="conditional_direct_splash_damage",radius=radius,attack_q32=attack,scenarios=scenarios,
                conditions=["normal_targetability_no_interception_or_healing","original_included_once",
                            "input_point_bounded_over_attack_windup_and_receiver_physics_sample"])

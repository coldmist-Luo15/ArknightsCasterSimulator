"""H5-1 policy trials using visible kills, enemy position and skill/deployment readiness."""
import argparse
from collections import OrderedDict
from dataclasses import dataclass
import json

from checkpoint import Checkpoint
from experiment_rows import run_rows
from pinned_inputs import warm
from rolling import ready
from study import B, E, J, L, M, S, T, W, RecalBattle, cast, here, load, out, plan, put, run_caster_stage, tables
from tools.run_caster_seeds import trial_seeds


FIRST_GUN = "w0-f0-a9-n0"
MID_GUN = "w0-f9-a1-n0"
SOUTH_GUN = "w0-f11-a6-n0"
NORTH_GUN = "w0-f11-a7-n0"
MID_A2 = "w0-f9-a7-n0"
WING = {"w0-f11-a9-n0", "w0-f11-a10-n0"}
ENDING_MAGES = {"w0-f13-a1-n0", "w0-f13-a2-n0", "w0-f13-a3-n0", "w0-f13-a4-n0"}
SPONSOR = "w0-f4-a0-n0"
ROUTE = "nov27-bait-clear-tail"
CACHE = OrderedDict()


@dataclass(frozen=True)
class RangeTrigger:
    operator: str
    original: int
    start: int
    end: int
    enemies: frozenset
    attribute: str
    minimum: int = 1
    fallback: bool = True


TRIGGERS = {
    "snow": RangeTrigger(S, 8637, 8580, 8830, frozenset({NORTH_GUN}), "snow"),
    "mostima": RangeTrigger(M, 8656, 8260, 8770, frozenset({MID_GUN}), "mostima"),
    "mantra1": RangeTrigger(T, 7480, 7290, 7640, frozenset({MID_GUN, MID_A2}), "wide"),
    "mantra2": RangeTrigger(T, 9550, 9270, 9700, frozenset(WING), "wide"),
    "volcano": RangeTrigger(E, 8708, 8540, 9000, frozenset(WING), "skill_cells"),
    "volcano_pair": RangeTrigger(E, 8708, 8540, 8980, frozenset(WING), "skill_cells", 2),
    "volcano_early": RangeTrigger(E, 8708, 8610, 8940, frozenset({MID_GUN, SOUTH_GUN, *WING}), "skill_cells"),
    "logos": RangeTrigger(L, 9876, 9840, 10090, frozenset(ENDING_MAGES), "skill_cells"),
}


def price(battle, operator):
    base = battle.cards[operator]["panel_attributes"]["cost"]
    return int(base * (1 + 0.5 * min(battle.paid_count[operator], 2)))


def install(battle, options):
    steps, ranges, *_ = options
    steps = set(steps)
    if not steps and not ranges:
        return []

    remove = {(TRIGGERS[key].operator, TRIGGERS[key].original) for key in ranges}
    if "cannon" in steps:
        remove.update({(B, 3054), (W, 3152)})
    if "bait" in steps:
        remove.add((E, 2953))
    if "sponsor" in steps:
        remove.add((J, 5415))
    if "middle" in steps:
        remove.add((L, 5754))
    if "handoff" in steps or "handoff_air" in steps:
        remove.update({(M, 9576), (L, 9576), (L, 9876)})
    if "volcano_deploy" in steps:
        remove.add((E, 8473))
    for operator, frame in remove:
        battle.actions[frame] = [a for a in battle.actions[frame] if a["operator_id"] != operator]

    watched = {FIRST_GUN, SPONSOR, MID_GUN, SOUTH_GUN, "w0-f8-a0-n0", "w0-f8-a1-n0", *WING}
    killed = {e["target"] for e in battle.events if e["kind"] == "enemy_killed" and e["target"] in watched}
    marks = [e for e in battle.events if e["kind"] == "artillery_mark" and e["source"] == FIRST_GUN]
    consumed = set()
    decisions = []
    seen = len(battle.events)
    old_tick = battle.hostile.coroutines
    next_press = None

    def enter(action, label, reason):
        battle.actions[action["frame"]].append(action)
        decisions.append(dict(rule=label, reason=reason, action=action))
        consumed.add(label)

    def deployable(operator, frame):
        return operator not in battle.operators and battle.ready_at[operator] <= frame and battle.dp >= price(battle, operator)

    def tick():
        nonlocal seen, next_press
        old_tick()
        if battle.life <= 0:
            return
        frame = battle.frame
        for event in battle.events[seen:]:
            if event["kind"] == "enemy_killed" and event["target"] in watched:
                killed.add(event["target"])
            if event["kind"] == "artillery_mark" and event["source"] == FIRST_GUN:
                marks.append(event)
        seen = len(battle.events)
        deployed = battle.operators

        if "bait" in steps and E in deployed and 2935 <= frame < 3190 and "bait" not in consumed:
            marked = next((e for e in reversed(marks) if e["target"] == E and e["impact_frame"] > frame + 3), None)
            if marked:
                enter(out(frame, E), "bait", "first_cannon_marked_eyja")
            elif FIRST_GUN in killed:
                enter(out(frame, E), "bait", "first_cannon_destroyed")

        if "cannon" in steps:
            if frame >= 3054 and "cannon" not in consumed and FIRST_GUN in killed:
                if B in deployed:
                    enter(out(frame, B), "cannon", "first_cannon_destroyed")
                else:
                    consumed.add("cannon")
            if frame >= 3152 and "first_wolves" not in consumed and deployable(W, frame):
                enter(put(frame, W, "F11", "l"), "first_wolves", "deployment_cost_ready")

        if "sponsor" in steps and frame >= options[2] and "sponsor" not in consumed and SPONSOR in killed and battle.inventory[J] and battle.dp >= 40:
            enter(put(frame, J, "E5", "l"), "sponsor", "sponsor_destroyed_and_device_affordable")

        if "middle" in steps and frame >= 5754 and L in deployed and "middle" not in consumed:
            if {"w0-f8-a0-n0", "w0-f8-a1-n0"} <= killed or frame >= 5898:
                reason = "middle_pair_destroyed" if frame < 5898 else "latest_return_window"
                enter(out(frame, L), "middle", reason)

        if "volcano_deploy" in steps and frame >= 7970 and "volcano_deploy" not in consumed and deployable(E, frame):
            cannon = battle.live.get(MID_GUN)
            if cannon is None or cannon.col >= 7.31 or frame >= 8473:
                enter(put(frame, E, "C6", "l"), "volcano_deploy", "middle_cannon_past_C6")

        if ({"handoff", "handoff_air"} & steps) and frame >= 9576 and "handoff" not in consumed and L not in deployed and battle.ready_at[L] <= frame:
            goals = WING if "handoff_air" in steps else {MID_GUN, SOUTH_GUN}
            arrived = all(enemy in killed for enemy in goals)
            fallback = M not in deployed or any(
                e.id in ENDING_MAGES and e.row >= 5.5 and e.col >= 5.5 for e in battle.live.values() if not e.cursor.hidden
            )
            if arrived or fallback:
                refund = min(int(battle.cards[M]["panel_attributes"]["cost"]), deployed[M].paid // 2) if M in deployed else 0
                if battle.dp + refund >= price(battle, L):
                    if M in deployed:
                        enter(out(frame, M), "mostima_withdraw", "coverage_target_destroyed" if arrived else "end_lane_needs_continuous_output")
                    enter(put(frame, L, "E9", "u"), "handoff", "empty_E9_and_logos_ready")
                    next_press = frame + 285

        if next_press is not None and frame >= next_press and L in deployed and "handoff_press" not in consumed and ready(deployed[L], frame):
            if "logos" not in ranges:
                enter(cast(frame, L), "handoff_press", "skill_ready")

        for key in ranges:
            if key in consumed:
                continue
            rule = TRIGGERS[key]
            actor = deployed.get(rule.operator)
            if not actor or not rule.start <= frame <= rule.end + 90 or not ready(actor, frame):
                continue
            handler = actor.handler
            if rule.attribute == "mostima":
                cells = handler.ranges[1]
            elif rule.attribute == "snow":
                cells = handler.skill
            else:
                cells = getattr(handler, rule.attribute)
            threats = [e.id for e in battle.targets(actor, cells) if e.id in rule.enemies]
            if len(threats) >= rule.minimum:
                enter(cast(frame, rule.operator), key, threats)
            elif rule.fallback and frame >= rule.end:
                enter(cast(frame, rule.operator), key, "end_of_skill_window")

    battle.hostile.coroutines = tick
    return decisions


# Keep the event policies small enough for paired tests and understandable in a manual axis.
OPTIONS = {
    "fixed": ((), (), 5415),
    "withdraw_first": (("bait", "cannon"), (), 5415),
    "withdraw_middle": (("middle",), (), 5415),
    "withdraw_end": (("handoff_air",), (), 5415),
    "withdraw_end_cannon": (("handoff",), (), 5415),
    "skill_volcano": ((), ("volcano",), 5415),
    "skill_volcano_pair": ((), ("volcano_pair",), 5415),
    "skill_volcano_early": (("volcano_deploy",), ("volcano_early",), 5415),
    "skill_mostima": ((), ("mostima",), 5415),
    "skill_mantra": ((), ("mantra1", "mantra2"), 5415),
    "skill_snow": ((), ("snow",), 5415),
    "skill_logos": ((), ("logos",), 5415),
    "withdraw_and_volcano": (("handoff_air",), ("volcano",), 5415),
    "withdraw_and_skills": (("handoff_air",), ("mostima", "volcano", "mantra2"), 5415),
    "middle_and_skills": (("middle", "handoff_air"), ("volcano", "mantra2"), 5415),
    "device_and_skills": (("sponsor", "volcano_deploy"), ("volcano_early", "mantra2"), 5100),
}


def valid_win(d):
    return (d["stop"] == "stage_victory_projected" and d["kills"] == d["scheduled"] == d["spawned"] == 64
            and d["remaining_life"] == 1 and not d["combat_unresolved"])


def same_inputs(d):
    return [e["action"] for e in d["events"] if e["kind"] == "player_input"]


def evaluate(job):
    name, kind, war, terrain = job
    root, full = kind
    key = root, war, terrain
    options = OPTIONS[name]
    if full:
        q = load(root)
        q.update(battle_random_seed=war, map_random_seed=terrain)
        battle = RecalBattle(tables, q)
        decisions = install(battle, options)
        d = battle.run()
    else:
        if key not in CACHE:
            q = load(root)
            q.update(battle_random_seed=war, map_random_seed=terrain)
            if len(CACHE) >= 3:
                CACHE.popitem(last=False)
            CACHE[key] = q, Checkpoint(q, 2820)
        q, checkpoint = CACHE[key]
        decisions = []
        if checkpoint.playable:
            battle = checkpoint.node(q["actions"])
            decisions = install(battle, options)
            d = battle.run()
        else:
            d = checkpoint.prefix_report
    if valid_win(d):
        result = here / f"observed-{root}-{name}-{war}-{terrain}-{'full' if full else 'screen'}.json"
        result.write_text(json.dumps(dict(request=plan(q, drop=[x["frame"] for x in q["actions"]], add=same_inputs(d)),
                                          decisions=decisions, result=d), ensure_ascii=False), encoding="utf8")
    es = d["events"]
    return dict(i=name,battle=war,map=terrain,k=d["kills"],f=d["through_frame"],stop=d["stop"],
                life=d["remaining_life"],spawned=d["spawned"],scheduled=d["scheduled"],win=valid_win(d),
                unresolved=d["combat_unresolved"],decisions=decisions,
                leak=next(((e["target"],round(e["hp"])) for e in es if e["kind"]=="enemy_escaped"),None),
                drop=next((e["frame"] for e in es if e["kind"]=="token_gained"),None))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("workers", type=int)
    p.add_argument("sample_seed", type=int)
    p.add_argument("count", type=int)
    p.add_argument("policies", nargs="*", choices=None)
    p.add_argument("--full", action="store_true")
    p.add_argument("--root", default=ROUTE)
    p.add_argument("--known", action="store_true")
    args = p.parse_args()
    names = args.policies or list(OPTIONS)
    if unknown := set(names) - OPTIONS.keys():
        p.error(f"unknown policies: {sorted(unknown)}")
    seeds=list(trial_seeds(args.count,args.sample_seed))
    if args.known:
        rows=[json.loads(line) for line in (here/"pairs-271354.jsonl").read_bytes().splitlines()]
        sample=[(v["battle"],v["map"]) for v in rows if v["route"]==ROUTE and v["f"]>=9876]
        seeds=list(dict.fromkeys([*sample,*seeds]))
    jobs=[(name,(args.root,args.full),war,terrain) for war,terrain in seeds for name in names]
    suffix="full" if args.full else "screen"
    run_rows(here/f"observed-{args.root}-{args.sample_seed}-{suffix}.jsonl",evaluate,jobs,args.workers)

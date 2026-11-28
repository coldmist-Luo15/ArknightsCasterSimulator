"""Check whether the first cannon's visible HP calls for an early Eyjafjalla drop."""
import json
from math import floor, hypot
import sys
from collections import OrderedDict

from experiment_rows import run_rows
from observed_timeline import (
    B, E, J, L, M, T, W, ROUTE, FIRST_GUN, NORTH_GUN, SPONSOR, WING, RecalBattle, cast, here,
    load, out, price, put, ready, run_caster_stage, same_inputs, tables, valid_win,
)
from study import plan
from tools.run_caster_seeds import trial_seeds
from checkpoint import Checkpoint


PREFIXES = OrderedDict()


def evaluate(job, extra=None):
    reported, stage, seed, map_seed = job
    name = "safehandoff" if extra else reported
    q = load(ROUTE)
    combined_chant = name in ("second-chant", "second-chant-fast")
    second_salvo = name in ("second-salvo", "second-salvo-hold", "second-late", "second-latest") or combined_chant
    swap_wing = name in ("wing-swap", "wing-swap-up")
    if swap_wing:
        q=plan(q,drop=[7020,8473],add=[put(7020,T,"C6","r" if name=="wing-swap" else "u"),
                                     put(8473,E,"D9","l")])
    forward_mantra = name in ("mantra-up-screen", "mantra-right-screen")
    if forward_mantra:
        q=plan(q,drop=[1938,2899],add=[put(1938,T,"D9","u" if name=="mantra-up-screen" else "r"),
                                     put(2899,E,"F11","l")])
    leader = name in ("leader", "leader-support")
    early_mostima = name in ("early-mostima", "early-mostima-jam")
    if leader:
        q = plan(q, drop=[2899, 2953, 8473], add=[put(3610, E, "C6", "r")],
                 runes={"emppnt_more_2": "lunmag_strong_2"})
    hold_mantra = name in ("mantra-hold", "mantra-hold-bait")
    chant = name in ("chant-advanced", "chant-weak")
    if name == "chant-advanced":
        q = plan(q, drop=[2365, 2455, 2485, 2576, 2856], add=[cast(2325, B), cast(2415, B)])
    decoy_shift = name in ("jam-south", "jam-cannons")
    if decoy_shift:
        runes = {"enemy_silenceImmue_a": "enemy_silenceImmue_b"} if name == "jam-cannons" else None
        q = plan(q, drop=[5415, 8473], add=[put(5415, J, "C6", "l"), put(8473, E, "E5", "l")], runes=runes)
    south_counter = name in ("south-lock", "south-lock-early")
    if name == "south-lock-early":
        q = plan(q, drop=[5754], add=[out(5560, L)])
    if extra and hasattr(extra,"prepare"):
        q=extra.prepare(q)
    q.update(battle_random_seed=seed, map_random_seed=map_seed)
    cut=getattr(extra,"checkpoint_frame",None)
    if cut:
        signature=json.dumps(dict(request={k:v for k,v in q.items() if k!="actions"},
                         inputs=[a for a in q["actions"] if a["frame"]<cut],cut=cut),sort_keys=True)
        if signature not in PREFIXES:
            if len(PREFIXES)>=3:
                PREFIXES.popitem(last=False)
            PREFIXES[signature]=Checkpoint(q,cut)
        point=PREFIXES[signature]
        battle=point.node(q["actions"]) if point.playable else RecalBattle(tables,q)
    else:
        battle = RecalBattle(tables, q)
    options = {
        "fixed": (None, 2899, "D9"),
        "volcano": (56000, 2701, "D9"),
        "latebait": (54500, 2791, "D9"),
        "adaptive": (56000, 2701, "D9"),
        "ready": (56000, 2701, "D9"),
        "preheat": (56000, 2701, "D9"),
        "bounded": (60000, 2701, "D9"),
        "handoff": (60000, 2701, "D9"),
        "safehandoff": (60000, 2701, "D9"),
        "south-lock": (60000, 2701, "D9"),
        "south-lock-early": (60000, 2701, "D9"),
        "jam-south": (60000, 2701, "D9"),
        "jam-cannons": (60000, 2701, "D9"),
        "mantra-hold": (None, 2899, "D9"),
        "mantra-hold-bait": (60000, 2701, "D9"),
        "chant-advanced": (60000, 2701, "D9"),
        "chant-weak": (60000, 2701, "D9"),
        "leader": (None, 2899, "D9"),
        "leader-support": (None, 2899, "D9"),
        "early-mostima": (60000, 2701, "D9"),
        "early-mostima-jam": (60000, 2701, "D9"),
        "mantra-up-screen": (None, 2899, "F11"),
        "mantra-right-screen": (None, 2899, "F11"),
        "wing-swap": (60000, 2701, "D9"),
        "wing-swap-up": (60000, 2701, "D9"),
        "second-salvo": (60000, 2701, "D9"),
        "second-salvo-hold": (60000, 2701, "D9"),
        "second-late": (60000, 2701, "D9"),
        "second-latest": (60000, 2701, "D9"),
        "second-chant": (60000, 2701, "D9"),
        "second-chant-fast": (60000, 2701, "D9"),
    }
    threshold, deployment, cell = options[name]
    if extra and getattr(extra,"alternate_opening",False):
        threshold = None
    events_seen = 0
    observations = {}
    marks = []
    actions = []
    old_tick = battle.hostile.coroutines
    relay_done = False
    previous_best = name == "safehandoff" or decoy_shift or south_counter or hold_mantra or chant or leader or early_mostima or forward_mantra or swap_wing or second_salvo
    relay = name in ("adaptive", "ready", "preheat", "bounded", "handoff") or previous_best
    retime_fire = name in ("ready", "preheat", "bounded", "handoff") or previous_best
    if relay:
        battle.actions[9876] = [a for a in battle.actions[9876] if a["operator_id"] != L]
    if retime_fire:
        battle.actions[8708] = [a for a in battle.actions[8708] if a["operator_id"] != E]
    fire_done = False
    central_gun = "w0-f9-a1-n0"
    lock_target = "w0-f13-a1-n0"
    use_south = False
    south_cast = False
    early_eyja = False
    kept_mantra = False
    mid_fire = False
    first_m = False
    early_jam = False
    arm_second = False
    most_bait = False
    most_return = False
    most_skill = False
    hold_marcil = False

    def act(action):
        battle.actions[action["frame"]].append(action)
        actions.append(action)

    def turn():
        nonlocal events_seen, relay_done, fire_done, use_south, south_cast, early_eyja, kept_mantra, mid_fire, first_m, early_jam
        nonlocal arm_second, most_bait, most_return, most_skill, hold_marcil
        old_tick()
        f = battle.frame
        if battle.life <= 0:
            return
        if extra and extra(battle, act, observations):
            fire_done = True
        if combined_chant and f == 2019:
            gun=battle.live.get(FIRST_GUN)
            if gun and gun.hp > 80000*2**32:
                for frame in (2365,2455,2485,2576,2856):
                    battle.actions[frame]=[a for a in battle.actions[frame] if a["operator_id"]!=B]
                commit=2325 if name=="second-chant" else 2305
                act(cast(commit,B))
                act(cast(commit+90,B))
        if second_salvo and 2790 <= f <= 3225:
            gun = battle.live.get(FIRST_GUN)
            aim = battle.hostile.states.get(FIRST_GUN)
            if arm_second and gun and aim and aim.release == f+5 and aim.targets and aim.targets[0].uid == L:
                affordable = battle.dp >= price(battle,M)
                close = hypot(gun.row-4,gun.col-8) < 2.0
                if not most_bait and not E in battle.operators and not M in battle.operators and affordable and close:
                    if gun.hp > 4500 * 2**32:
                        battle.actions[3296] = [a for a in battle.actions[3296] if a["operator_id"] != M]
                        act(put(f,M,"E9","l"))
                        most_bait=True
            if most_bait and M in battle.operators and any(e["target"]==M and f>=e["frame"]+14 for e in marks):
                act(out(f,M))
            if name=="second-salvo-hold" and arm_second and f==3054 and gun and gun.hp > 4500 * 2**32:
                battle.actions[3054] = [a for a in battle.actions[3054] if a["operator_id"] != B]
                battle.actions[6426] = [a for a in battle.actions[6426] if a["operator_id"] != B]
                hold_marcil=True
        if hold_marcil and f==3970 and B in battle.operators:
            act(out(f,B))
        if hold_marcil and 7250 <= f <= 7880 and B not in battle.operators and b_missing_b():
            act(put(f,B,"F7","l"))
            hold_marcil=False
        if most_bait and 6200 <= f <= 7700 and not most_return and M not in battle.operators:
            if battle.ready_at[M] <= f and battle.dp >= price(battle,M) and (4,8) not in battle.occupied():
                act(put(f,M,"E9","l"))
                most_return=True
        if most_return and not most_skill and 7200<=f<=8590 and M in battle.operators and ready(battle.operators[M],f):
            if battle.targets(battle.operators[M], battle.operators[M].handler.ranges[True]):
                act(cast(f,M))
                most_skill=True
        if early_mostima and f == 3160 and FIRST_GUN not in battle.live and battle.dp >= price(battle,M):
            battle.actions[3296] = [a for a in battle.actions[3296] if a["operator_id"] != M]
            act(put(f,M,"E9","l"))
        if early_mostima and 4120 <= f < 4900 and not first_m:
            mostima = battle.operators.get(M)
            if mostima and ready(mostima,f):
                eligible = battle.targets(mostima, mostima.handler.ranges[True])
                if any(e.id in ("w0-f5-a0-n0","w0-f5-a0-n1","w0-f5-a0-n2") and e.hp > 14000 * 2**32 for e in eligible):
                    battle.actions[4900] = [a for a in battle.actions[4900] if a["operator_id"] != M]
                    act(cast(f,M))
                    first_m = True
        if name == "early-mostima-jam" and first_m and 4550 <= f < 5415 and not early_jam and battle.inventory[J] and battle.dp >= 40:
            mostima = battle.operators.get(M)
            if mostima and mostima.handler.active:
                active = battle.targets(mostima, mostima.handler.ranges[True])
                if any(e.id in ("w0-f5-a0-n0", "w0-f5-a0-n1", "w0-f5-a0-n2") for e in active):
                    battle.actions[5415] = [a for a in battle.actions[5415] if a["operator_id"] != J]
                    act(put(f,J,"E5","l"))
                    early_jam = True
        if name == "chant-weak" and f == 2019:
            gun = battle.live.get(FIRST_GUN)
            observations["first-2019"] = round(gun.hp / 2**32) if gun else 0
            if gun and gun.hp > 80000 * 2**32:
                for frame in (2020, 2365, 2455, 2485, 2576, 2856):
                    battle.actions[frame] = [a for a in battle.actions[frame] if a["operator_id"] != B]
                act(cast(2320, B))
        if leader and not mid_fire and 3810 <= f <= 4830:
            eye = battle.operators.get(E)
            if eye and ready(eye, f):
                targets = {e.id for e in battle.targets(eye, eye.handler.skill_cells)}
                first = {"w0-f5-a0-n0", "w0-f5-a0-n1", "w0-f5-a0-n2"}
                should_cast = bool(targets & first) or name == "leader-support" and SPONSOR in targets
                if should_cast:
                    act(cast(f,E))
                    mid_fire = True
        if hold_mantra and f == 2700:
            gun = battle.live.get(FIRST_GUN)
            manta = battle.operators.get(T)
            kept_mantra = bool(gun and manta and gun.hp > 55000 * 2**32)
            if kept_mantra:
                battle.actions[2700] = [a for a in battle.actions[2700] if a["operator_id"] != T]
        if kept_mantra and 3000 <= f <= 3130 and T in battle.operators:
            if FIRST_GUN not in battle.live or f == 3130:
                act(out(f, T))
        if retime_fire and f in (8240, 8656, 8708):
            cannon = battle.live.get(central_gun)
            observations[f"central-{f}"] = round(cannon.hp / 2**32) if cannon else 0
            if name == "preheat" and f == 8240 and observations["central-8240"] >= 44000:
                if E not in battle.operators and battle.ready_at[E] <= f and battle.dp >= price(battle, E):
                    battle.actions[8473] = [a for a in battle.actions[8473] if a["operator_id"] != E]
                    act(put(f, E, "C6", "l"))
        if retime_fire and 8657 <= f <= 9020 and not fire_done:
            eyja = battle.operators.get(E)
            manta = battle.operators.get(T)
            high = observations["central-8656"] >= 18500
            proactive = not previous_best and high and manta and manta.handler.active
            if eyja and ready(eyja, f) and (f >= 8708 or proactive):
                targets = {enemy.id for enemy in battle.targets(eyja, eyja.handler.skill_cells)}
                if targets & {central_gun, "w0-f11-a9-n0", "w0-f11-a10-n0"}:
                    act(cast(f, E))
                    fire_done = True
        if south_counter and not use_south and 9230 <= f <= 9576:
            wings = [battle.live[i] for i in WING if i in battle.live and not battle.live[i].cursor.hidden]
            at_risk = T not in battle.operators or W not in battle.operators or sum(e.hp for e in wings) > 30000 * 2**32
            if at_risk and wings and battle.ready_at[L] <= f and L not in battle.operators and battle.dp >= price(battle, L) and (2, 5) not in battle.occupied():
                battle.actions[9576] = [a for a in battle.actions[9576] if a["operator_id"] not in (M, L)]
                act(put(f, L, "C6", "r"))
                use_south = True
        if use_south and not south_cast and 9230 <= f <= 10350:
            logos = battle.operators.get(L)
            if logos and ready(logos, f) and any(e.id in WING for e in battle.targets(logos, logos.handler.skill_cells)):
                act(cast(f, L))
                south_cast = True
        if relay and not use_south and 9876 <= f <= 10100 and not relay_done and not getattr(extra,"owns_late_logos",False):
            logos = battle.operators.get(L)
            if logos and ready(logos, f):
                mantra = battle.operators.get(T)
                mage = battle.live.get("w0-f13-a3-n0")
                inside = (mage and mantra and
                          (floor(mage.row + .5), floor(mage.col + .5)) in mantra.handler.wide)
                close = mage and not mage.cursor.hidden and mage.col >= 4.28
                if inside or not close or not mantra or not mantra.handler.active or f >= 9900:
                    act(cast(f, L))
                    relay_done = True
        if (name == "handoff" or previous_best) and not use_south and 10250 <= f <= 10850 and lock_target in battle.enemies and lock_target not in battle.live:
            logos = battle.operators.get(L)
            gun = battle.live.get(NORTH_GUN)
            if logos and gun and not gun.cursor.hidden and T in battle.operators and W in battle.operators:
                distance = hypot(gun.row - logos.row, gun.col - logos.col)
                remaining_targets = battle.targets(logos, logos.handler.skill_cells)
                others_in_reach = any(hypot(gun.row - battle.operators[u].row, gun.col - battle.operators[u].col) <= 2.35 for u in (T, W))
                if 2.28 <= distance <= 2.65 and not remaining_targets and not others_in_reach:
                    act(out(f, L))
        if f not in range(2701, 3130):
            return
        gun = battle.live.get(FIRST_GUN)
        for event in battle.events[events_seen:]:
            if event["kind"] == "artillery_mark" and event["source"] == FIRST_GUN:
                marks.append(event)
        events_seen = len(battle.events)
        if f in (2701, 2899, 2911, 2999, 3045):
            observations[str(f)] = round(gun.hp / 2**32) if gun else 0
        upper = 64000 if not second_salvo and (name in ("bounded", "handoff") or previous_best) else float("inf")
        planned = deployment
        if observations.get("2701", 0)>64000:
            planned = {"second-late":2770,"second-latest":2790,"second-chant":2790,"second-chant-fast":2770}.get(name,planned)
        if f == planned and gun and threshold and threshold < observations["2701"] <= upper and battle.dp >= price(battle, E):
            battle.actions[2899] = [a for a in battle.actions[2899] if a["operator_id"] != E]
            battle.actions[2953] = [a for a in battle.actions[2953] if a["operator_id"] != E]
            act(put(f, E, cell, "l"))
            early_eyja = True
            if second_salvo and observations["2701"]>64000:
                arm_second=True
        if not early_eyja or E not in battle.operators:
            return
        eyja = battle.operators[E]
        impact = next((m["impact_frame"] for m in marks if m["target"] == E), None)
        if impact is not None and f >= impact - 14:
            act(out(f, E))
            return
        if not gun or f == 3129:
            act(out(f, E))
            return
        # Keep a voluntary retreat available and only light the skill while it can actually hit the cannon.
        if ready(eyja, f) and (impact is None or f < impact - 32) and gun in battle.targets(eyja, eyja.handler.skill_cells):
            if not any(a.get("kind") == "skill" for a in actions):
                act(cast(f, E))

    def b_missing_b():
        return battle.ready_at[B]<=battle.frame and battle.dp>=price(battle,B) and (5,6) not in battle.occupied()

    battle.hostile.coroutines = turn
    d = battle.run()
    logs = d["events"]
    watch = (
        [e for e in logs if 2701 <= e["frame"] <= 3130 and
            (e["kind"] == "eyja_deployment_talent" or e["kind"] == "skill_pressed" and e.get("operator_id") == E)]
    )
    verdict = dict(
        i=reported, battle=seed, map=map_seed, kills=d["kills"], stop=d["stop"], frame=d["through_frame"],
        win=valid_win(d) or leader and d["score"]==22 and d["squad_size"]==7 and
             d["remaining_life"]==1 and d["stop"]=="stage_victory_projected" and
             d["kills"]==d["scheduled"]==d["spawned"] and not d["combat_unresolved"],
        hp=observations, actions=actions,
        first_gun=next((e["frame"] for e in logs if e["kind"]=="enemy_killed" and e["target"]==FIRST_GUN), None),
        cannon_marks=[(e["frame"], e["target"], e["impact_frame"]) for e in marks],
        first_loss=next((e["frame"] for e in logs if e["kind"]=="operator_defeated" and e.get("operator_id")==L), None),
        sponsor=next((e["frame"] for e in logs if e["kind"]=="enemy_killed" and e["target"]==SPONSOR), None),
        early_fire_damage=round(sum(e["damage"] for e in logs if e["kind"]=="volcano_hit" and e["target"]==FIRST_GUN)),
        fire_details=watch,
        leak=next(((e["target"], round(e["hp"])) for e in logs if e["kind"]=="enemy_escaped"), None),
    )
    if verdict["win"] or actions and stage != "sample":
        pending = [a for f, rows in battle.actions.items() if f > d["through_frame"] for a in rows] if extra else []
        request = plan(q, drop=[a["frame"] for a in q["actions"]], add=same_inputs(d)+pending)
        assert run_caster_stage(tables, request) == d
        (here / f"logos-response-{reported}-{seed}-{map_seed}.json").write_text(
            json.dumps(dict(request=request, result=d, verdict=verdict), ensure_ascii=False), encoding="utf8")
    return verdict


if __name__ == "__main__":
    probe = [
        (588925228, 398117685), (1935015943, 1434738493), (1601499280, 1104354708),
        (355894860, 488267033), (1727549616, 1664913877),
        (1361227615, 1311128542), (499045565, 1816409424),
    ]
    mode = sys.argv[1]
    tag, count = (map(int, sys.argv[2:4]) if mode == "sample" else (281132, len(probe)))
    seeds = list(trial_seeds(count, tag)) if mode == "sample" else probe
    if mode == "mid":
        seeds = [probe[0], probe[2], probe[4], probe[6]]
        tag = 281141
    if mode == "handoff":
        seeds = [probe[2], probe[4], probe[5], (589476853, 1527080212), (296761114, 1452984730)]
        tag = 281146
    if mode == "confirm":
        seeds = [probe[2], probe[4], probe[5], (589476853, 1527080212), (296761114, 1452984730),
                 (61129363, 1190947731), (551317787, 2086239378), (1402784213, 999166733)]
        tag = 281149
    if mode == "southern":
        seeds = [(628155088, 196929602), (1601499280, 1104354708), (1106834848, 1419395519),
                 (1257960646, 842931806), (605846201, 1997970174), (83424657, 1614110504)]
        tag = 281201
    if mode == "earlyhold":
        seeds = [*probe, (628155088, 196929602), (173282207, 1076612548), (630813133, 2140027558),
                 (178100010, 848248333), (1972121355, 1226177444)]
        tag = 281207
    if mode == "chant":
        seeds = [*probe, (628155088, 196929602), (173282207, 1076612548), (630813133, 2140027558),
                 (178100010, 848248333), (1972121355, 1226177444)]
        tag = 281214
    if mode == "leader":
        seeds = [probe[0], probe[1], probe[2], probe[4], (628155088, 196929602), (1972121355, 1226177444)]
        tag = 281226
    if mode == "early-mostima":
        seeds = [(1601499280,1104354708), (628155088,196929602), (1972121355,1226177444),
                 (173282207,1076612548),(178100010,848248333),(321162672,1857073305),
                 (1106834848,1419395519),(605846201,1997970174)]
        tag = 281228
    if mode == "focus":
        seeds = [probe[0],probe[1],probe[2],probe[4],(628155088,196929602)]
        tag = 281239
    if mode == "wing-swap":
        seeds = [(628155088,196929602), (1601499280,1104354708),(1106834848,1419395519),
                 (1257960646,842931806),(605846201,1997970174),(83424657,1614110504)]
        tag = 281242
    if mode == "second-salvo":
        seeds=[probe[1],probe[0],probe[2],(628155088,196929602),(355894860,488267033),
               (145633169,618891718),(724512627,1695716430)]
        tag=281250
    if mode == "third-window":
        seeds=[probe[1],(724512627,1695716430),(543987238,1218729475),(277783037,1017049738),
               (1294310857,738781988),(628155088,196929602)]
        tag=281254
    names = sys.argv[4:] or ["fixed", "volcano", "latebait"]
    jobs = [(name, mode, seed, map_seed) for seed, map_seed in seeds for name in names]
    run_rows(here / f"logos-response-{tag}-{mode}.jsonl", evaluate, jobs, 3)

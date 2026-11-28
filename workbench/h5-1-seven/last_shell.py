"""Protect the last two damage sources from one northern artillery shell."""
import json
import sys
from math import hypot

from checkpoint import Checkpoint
from experiment_rows import run_rows
from observed_timeline import L, NORTH_GUN, T, W, here, load, same_inputs, tables, valid_win
from study import out, plan, run_caster_stage


PAIRS=[(499045565,1816409424),(1601499280,1104354708),(628155088,196929602),(1361227615,1311128542)]
DOWN=(299196511,1672095373)


def repair(job):
    name, mode, seed, terrain=job
    if (seed,terrain)==PAIRS[0]:
        q=load("nov27-hold-mostima-63")
    elif (seed,terrain)==DOWN:
        q=dict(load("nov27-wolf-heading-d"),battle_random_seed=seed,map_random_seed=terrain)
    else:
        q=json.loads((here/f"logos-response-safehandoff-{seed}-{terrain}.json").read_bytes())["request"]
    cp=Checkpoint(q,10200)
    if not cp.playable:
        return dict(i=name,battle=seed,map=terrain,k=cp.prefix_report["kills"],win=False,loss="early")
    b=cp.node(q["actions"])
    old=b.hostile.coroutines
    actions=[]
    def observe():
        old()
        f=b.frame
        if actions or name=="baseline" or not 10200<=f<=10940 or b.life<=0:
            return
        enemy=b.live.get(NORTH_GUN)
        clock=b.hostile.states.get(NORTH_GUN)
        if not enemy or not clock or not clock.release or not 1<=clock.release-f<=5:
            return
        if b.mantra and b.mantra.can_interrupt(enemy) or b.disarmed(enemy):
            return
        victim=next(iter(clock.targets),None)
        if not victim or victim.uid!=L or T not in b.operators or W not in b.operators:
            return
        mantra=b.operators[T]
        if hypot(victim.row-mantra.row,victim.col-mantra.col)>1.35:
            return
        if name=="free-exit" and any(hypot(enemy.row-a.row,enemy.col-a.col)<=2.3
               for a in b.operators.values() if a.uid!=L):
            return
        for t in tuple(b.actions):
            if t>=f:
                b.actions[t]=[c for c in b.actions[t] if c["operator_id"]!=L]
        actions.append(out(f,L))
        b.actions[f].append(actions[-1])
    b.hostile.coroutines=observe
    d=b.run()
    info=dict(i=name,battle=seed,map=terrain,k=d["kills"],f=d["through_frame"],win=valid_win(d),actions=actions,
              loss=next(((e["target"],round(e["hp"])) for e in d["events"] if e["kind"]=="enemy_escaped"),None))
    if info["win"] or actions:
        future=[x for f,xs in b.actions.items() if f>d["through_frame"] for x in xs]
        expanded=plan(q,drop=[a["frame"] for a in q["actions"]],add=same_inputs(d)+future)
        assert run_caster_stage(tables,expanded)==d
        (here/f"last-shell-{name}-{seed}-{terrain}.json").write_text(json.dumps(dict(request=expanded,result=d,record=info)),encoding="utf8")
    return info


if __name__=="__main__":
    tag=sys.argv[1]
    cases=[DOWN] if tag=="281577" else PAIRS
    run_rows(here/f"last-shell-{tag}.jsonl",repair,
             [(name,"probe",s,t) for s,t in cases for name in sys.argv[2:]],4)

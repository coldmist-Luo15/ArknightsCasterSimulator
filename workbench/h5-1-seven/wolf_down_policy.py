"""Validate the proven north-shell maneuver on the alternative wolf heading."""
import json
import sys
from math import hypot

from checkpoint import Checkpoint
from experiment_rows import run_rows
from observed_timeline import B,E,L,M,S,T,W,here,load,price,ready,same_inputs,tables,valid_win
from replay_southern_policy import Tail
from study import cast,plan,pos,put,run_caster_stage
from arkengine.attack_range import circle_in_range,range_cells
from tools.run_caster_seeds import trial_seeds

DOWN_CASES=[(299196511,1672095373),(2128357856,220091668),(1727549616,1664913877),
            (1110444585,749805975),(435231796,17295063),(1153788717,1589023524),
            (144340163,704153387),(2095714327,2095106202)]
RIGHT_CASES=[(1372458416,839541491),(389790619,1883537366),(1086107031,1487397271),(2095714327,2095106202)]
SPOTS=[(name,pos(name)) for name in ("F7","F5","E5","C6","D9","E9","F11")]
HEAD={"r":"RIGHT","l":"LEFT","u":"UP","d":"DOWN"}


def play(job):
    name,mode,seed,terrain=job
    root="nov27-wolf-heading-r" if name.startswith("right-") else "nov27-wolf-heading-d"
    q=dict(load(root),battle_random_seed=seed,map_random_seed=terrain)
    if name in {"down-bank","down-bank-wing"}:
        q={**q,"actions":[a for a in q["actions"] if not (a["operator_id"]==T and a["frame"]>=9550)]}
    cp=Checkpoint(q,9240)
    if not cp.playable:
        result=cp.prefix_report
        return dict(i=name,battle=seed,map=terrain,k=result["kills"],f=result["through_frame"],win=False,loss=next(
             ((e["target"],round(e["hp"])) for e in result["events"] if e["kind"]=="enemy_escaped"),None),actions=[])
    b=cp.node(q["actions"])
    old=b.hostile.coroutines
    controller=Tail("tail-screen" if name in {"down-screen","down-bank","down-bank-wing","right-screen"} else "tail-safe")
    actions=[]
    tried=set()
    cache={}
    def act(action):
        actions.append(action)
        b.actions[action["frame"]].append(action)
    def tail():
        old()
        if name in {"down-fixed","right-fixed"} or b.life<=0:
            return
        f=b.frame
        controller(b,act,{})
        if name in {"down-bank","down-bank-wing"} and 9550<=f<=9760 and T in b.operators and ready(b.operators[T],f):
            mantra=b.operators[T]
            targets=b.targets(mantra,mantra.handler.wide)
            wing=[u for u in targets if u.id in {"w0-f11-a9-n0","w0-f11-a10-n0"}]
            ground=any(u.id in {"w0-f13-a2-n0","w0-f13-a3-n0","w0-f13-a4-n0"} for u in targets)
            delay=controller.screen and bool(wing) and max(u.hp for u in wing)<=13500*2**32
            now=(f>=9670 or ground) if name=="down-bank" else (f>=9760 or ground or not wing)
            if not delay or now:
                act(cast(f,T))
        if f<11200 or len(b.live)!=1 or b.frame<max(b.spawns):
            return
        enemy=next(iter(b.live.values()))
        if enemy.cursor.hidden or enemy.hp>11000*2**32:
            return
        for uid in tried.intersection(b.operators):
            a=b.operators[uid]
            if not ready(a,f) or any(c.get("kind")=="skill" and c["operator_id"]==uid for c in actions):
                continue
            rng=getattr(a.handler,"skill_cells",getattr(a.handler,"skill",None))
            if isinstance(rng,(list,tuple)) and circle_in_range(enemy.row,enemy.col,enemy.radius,rng,box_query=True):
                act(cast(f,uid))
        if tried.intersection(b.operators):
            return
        profile=b.hostile.profiles.get(enemy.key)
        limit=profile["radius"]+.4 if profile else .5
        used=b.occupied()
        for u in (E,B,L,S,M,T,W):
            if u in tried or u in b.operators or b.ready_at[u]>f or b.dp<price(b,u):
                continue
            for tile,loc in SPOTS:
                r,c=loc["row"],loc["col"]
                if (r,c) in used or hypot(enemy.row-r,enemy.col-c)<=limit:
                    continue
                for heading,direction in HEAD.items():
                    key=u,tile,heading
                    if key not in cache:
                        cache[key]=range_cells(b.cards[u]["base_range"],{**loc,"direction":direction},b.rows,b.cols)
                    if circle_in_range(enemy.row,enemy.col,enemy.radius,cache[key],box_query=True):
                        act(put(f,u,tile,heading))
                        tried.add(u)
                        return
    b.hostile.coroutines=tail
    d=b.run()
    leak=next(((e["target"],round(e["hp"])) for e in d["events"] if e["kind"]=="enemy_escaped"),None)
    result=dict(i=name,battle=seed,map=terrain,k=d["kills"],f=d["through_frame"],win=valid_win(d),loss=leak,actions=actions)
    if result["win"] or mode=="probe" and result["k"]>=60 and actions:
        future=[a for f,rows in b.actions.items() if f>d["through_frame"] for a in rows]
        replay=plan(q,drop=[a["frame"] for a in q["actions"]],add=same_inputs(d)+future)
        assert run_caster_stage(tables,replay)==d
        (here/f"wolf-down-{name}-{seed}-{terrain}.json").write_text(json.dumps(dict(request=replay,result=d,record=result)),encoding="utf8")
    return result


if __name__=="__main__":
    mode,tag,n=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
    seeds=list(trial_seeds(n,tag)) if mode=="sample" else RIGHT_CASES if sys.argv[4].startswith("right-") else DOWN_CASES
    run_rows(here/f"wolf-down-{tag}-{mode}.jsonl",play,[(name,mode,a,b) for a,b in seeds for name in sys.argv[4:]],4)

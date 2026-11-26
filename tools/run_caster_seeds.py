"""对固定的术师操作轴按种子独立运行整局，记录样本通过率和可复现的种子对。"""
import argparse
from copy import deepcopy
import json
from math import sqrt
from pathlib import Path
from random import Random

from arkengine.caster_stage import run_caster_stage
from arkengine.opening_scene import PinnedTables
from arkengine.skill_recovery import LIN_DRAW_OPTIONS


def trial_seeds(trials, sample_seed, fixed_map=None):
    # This generator selects inputs for the experiment. Battle draws run inside V079 BattleRandom.
    sampling=Random(sample_seed)
    for _ in range(trials):
        battle=sampling.randrange(1<<31)
        map_seed=sampling.randrange(1<<31)
        yield battle,map_seed if fixed_map is None else fixed_map


def evaluate(tables, request, seeds):
    runs=[]
    for battle,map_seed in seeds:
        scene=deepcopy(request)
        scene.pop("end_frame",None)
        for field in ("eyja_deployment_seed",*LIN_DRAW_OPTIONS):
            scene.pop(field,None)
        scene.update(scene_model="caster_combat",battle_random_seed=battle,map_random_seed=map_seed)
        tables.begin_scene()
        row=dict(battle_random_seed=battle,map_random_seed=map_seed)
        try:
            result=run_caster_stage(tables,scene)
        except ValueError as exc:
            row.update(outcome="unsettled",reason=str(exc))
        else:
            stop=result.get("stop",result.get("status"))
            outcome="unsettled"
            if not result.get("combat_unresolved"):
                if stop=="stage_victory_projected":
                    outcome="win"
                elif stop in ("stage_defeat","stage_defeated"):
                    outcome="loss"
            row.update(outcome=outcome,through_frame=result.get("through_frame"),remaining_life=result.get("remaining_life"))
            if outcome=="unsettled":
                row["reason"]=result.get("combat_unresolved") or stop or "no_stage_outcome"
        runs.append(row)
    total=len(runs)
    wins=sum(r["outcome"]=="win" for r in runs)
    losses=sum(r["outcome"]=="loss" for r in runs)
    unsettled=total-wins-losses
    return dict(trials=total,wins=wins,losses=losses,unsettled=unsettled,
                sample_win_rate=wins/total if total and not unsettled else None,
                unresolved_rate_bounds=[wins/total,(wins+unsettled)/total] if total and unsettled else None,runs=runs)


def wilson_95(wins,total):
    z=1.959963984540054
    p=wins/total
    center=p+z*z/(2*total)
    margin=z*sqrt(p*(1-p)/total+z*z/(4*total*total))
    divisor=1+z*z/total
    return [0. if wins==0 else (center-margin)/divisor,
            1. if wins==total else (center+margin)/divisor]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request",type=Path)
    parser.add_argument("--trials",type=int,required=True,help="整局样本数")
    parser.add_argument("--sample-seed",type=int,default=0,help="复现本批种子对的抽样编号")
    args=parser.parse_args()
    if args.trials<1:
        parser.error("--trials 需要正整数")
    request=json.loads(args.request.read_bytes())
    fixed_map=request.get("map_random_seed")
    result=evaluate(PinnedTables(),request,trial_seeds(args.trials,args.sample_seed,fixed_map))
    result.update(sample_seed=args.sample_seed,map_seed_mode="sampled" if fixed_map is None else "fixed")
    result["wilson_95"]=None if result["unsettled"] else wilson_95(result["wins"],args.trials)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()

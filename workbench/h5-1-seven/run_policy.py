"""用相同的战斗与地图种子配对比较全息 H5-1 的排轴规则。"""
import argparse
from pathlib import Path

from study import here
from experiment_rows import run_rows
from logos_damage_response import evaluate
from protected_bait import ProtectedBait
from protected_central_guard import CentralGuard
from protected_delayed_eligibility import DelayedEligibility
from tools.run_caster_seeds import trial_seeds


POLICIES = {
    "hold-grant": lambda: ProtectedBait("hold-grant"),
    "mature-windup": lambda: DelayedEligibility("mature-windup"),
    "central-guard": lambda: CentralGuard("threat-guard"),
    "central-guard-aim": lambda: CentralGuard("threat-guard-aim"),
}


def replay(job):
    return evaluate(job, POLICIES[job[0]]())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    seeds = parser.add_mutually_exclusive_group(required=True)
    seeds.add_argument("--trials", type=int, help="独立战斗和地图种子对的数量")
    seeds.add_argument("--seed-pair", nargs=2, type=int, metavar=("BATTLE", "MAP"), help="重放一对已有种子")
    parser.add_argument("--sample-seed", type=int, default=282293)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, help="指定已有批次文件即可继续补跑缺失的结果")
    parser.add_argument("policies", nargs="+", choices=POLICIES)
    args = parser.parse_args()
    if args.workers < 1 or args.trials is not None and args.trials < 1:
        parser.error("样本数和进程数须为正整数")
    if len(set(args.policies)) != len(args.policies):
        parser.error("存在重复的方案")
    if args.seed_pair:
        pair = tuple(args.seed_pair)
        pairs = [pair]
        mode = "probe"
        tag = f"{pair[0]}-{pair[1]}"
    else:
        pairs = list(trial_seeds(args.trials, args.sample_seed))
        mode = "sample"
        tag = f"{args.sample_seed}-{args.trials}"
    jobs = [(name, mode, battle, terrain) for battle, terrain in pairs for name in args.policies]
    path = args.output or here / f"compare-{tag}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    run_rows(path, replay, jobs, args.workers)
    print(path)


if __name__ == "__main__":
    main()

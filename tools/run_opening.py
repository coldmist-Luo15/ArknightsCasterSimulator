"""Compute a stage opening from the local release snapshot and a JSON request."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arkengine.opening_replay import run_opening
from arkengine.opening_scene import DEFAULT_SNAPSHOT, PinnedTables
from arkengine.report_time import annotate_stage_times


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="关卡、编队及可选的时钟和部署操作")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--cost-model", choices=("native_q32", "uniform_frames"),
                        help="仅覆盖已有 clock 的关卡输入的费用时钟，原始请求文件保留")
    parser.add_argument("--output", type=Path, help="保存完整报告，省略时写入标准输出")
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_bytes())
        if args.cost_model and isinstance(request.get("clock"), dict):
            request["clock"]["cost_model"] = args.cost_model
        report = run_opening(request, tables=PinnedTables(args.snapshot))
        annotate_stage_times(report)
    except (ValueError, KeyError, TypeError, IndexError, OSError) as exc:
        parser.exit(2, f"开局输入或依赖未获支持：{exc}\n")
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 2 if (report["status"] == "unresolved" or report.get("movement", {}).get("unresolved_profiles")
                 or report.get("volley_damage", {}).get("status") == "unresolved") else 0


if __name__ == "__main__":
    raise SystemExit(main())

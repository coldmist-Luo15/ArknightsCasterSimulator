"""Run a selected max-potential caster loadout from a UTF-8 JSON request."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arkengine.caster_catalog import caster_catalog, caster_loadout_options
from arkengine.caster_model import run_caster
from arkengine.caster_prefabs import _json_values
from arkengine.opening_scene import PinnedTables
from arkengine.caster_stage import run_caster_stage
from arkengine.report_time import annotate_caster_samples, annotate_stage_times


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--catalog", action="store_true", help="列出所有选定术师、技能与模组")
    source.add_argument("--loadout-options", action="store_true", help="组队前查看逐干员的技能、常规模组分支、完整 ID 和等级")
    source.add_argument("--request",type=Path,nargs="+",help="一个或多个 UTF-8 JSON，多个输入共享实包缓存并依次独立运行")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cost-model", choices=("native_q32", "uniform_frames"),
                        help="仅覆盖已有 clock 的关卡输入的费用时钟，原始请求文件保留")
    args = parser.parse_args()
    try:
        tables = PinnedTables()
        if args.catalog:
            data = caster_catalog(tables)
        elif args.loadout_options:
            data = caster_loadout_options(tables)
        else:
            results=[]
            for path in args.request:
                tables.begin_scene()
                request=json.loads(path.read_bytes())
                if "stage_id" in request:
                    if args.cost_model and isinstance(request.get("clock"), dict):
                        request["clock"]["cost_model"] = args.cost_model
                    report=run_caster_stage(tables,request)
                    annotate_stage_times(report)
                else:
                    report=run_caster(tables,request)
                    annotate_caster_samples(report)
                results.append(dict(request=str(path),result=report))
            data=results[0]["result"] if len(results)==1 else results
        payload = json.dumps(_json_values(data), ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"输入或配置未获支持：{exc}\n")
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")


if __name__ == "__main__":
    main()

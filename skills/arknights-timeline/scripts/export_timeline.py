"""把从模拟回放核实的操作按同一帧序导出给打轴器和玩家。"""
import argparse
from itertools import groupby
import json
from pathlib import Path


def integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} 必须为非负整数")
    return value


def position(row, col):
    index = integer(row, "row") + 1
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{integer(col, 'col') + 1}"


def cell(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render(axis):
    steps = []
    for step in axis["steps"]:
        frame = integer(step["frame"], "frame")
        seconds, ff = divmod(frame, 30)
        minutes, ss = divmod(seconds, 60)
        operation = step["operation"].strip()
        if not operation:
            raise ValueError("操作说明不能为空")
        if "row" in step or "col" in step:
            operation += " · " + position(step["row"], step["col"])
        if step.get("direction"):
            operation += " 朝" + {"UP": "上", "DOWN": "下", "LEFT": "左", "RIGHT": "右"}[step["direction"]]
        phase = "—"
        bar = step.get("cost_bar") or {}
        if "frame_in_cycle" in bar:
            k = integer(bar["frame_in_cycle"], "frame_in_cycle")
            length = integer(bar["cycle_length"], "cycle_length")
            if k >= length:
                raise ValueError("回费帧序超出本周期")
            phase = f"{k}/{length - 1}"
        elif bar.get("stopped_at_cap"):
            phase = "费用封顶"
        steps.append((frame, f"{minutes:02}:{ss:02}:{ff:02}", operation, phase, step.get("cost", "—")))
    if not steps:
        raise ValueError("没有已核实的操作可导出")
    steps.sort(key=lambda item: item[0])  # 保留输入中已核实的同帧执行先后。
    nodes = []
    for frame, group in groupby(steps, key=lambda item: item[0]):
        nodes.append({"frame": frame, "name": " → ".join(item[2] for item in group), "color": "#4682b4"})
    lines = [f"# {axis['title']}", "", axis["setup"], "", axis["result"], "",
             "| 分:秒:帧 | 逻辑帧 | 费用条 | 费用 | 具体操作 |",
             "|---|---:|---|---|---|"]
    lines.extend(f"| {stamp} | F{frame} | {phase} | {cell(cost)} | {cell(op)} |"
                 for frame, stamp, op, phase, cost in steps)
    lines.extend(["", "从开局 F0 计时，每秒 30 帧。费用条按本轮实际长度显示，31 帧周期的末格为 30/30。"])
    return json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("axis", type=Path, help="已核实的操作记录 JSON")
    parser.add_argument("--out", required=True, type=Path, help="本次打图的交付目录")
    args = parser.parse_args()
    ruler, table = render(json.loads(args.axis.read_bytes()))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "costbarruler.json").write_text(ruler, encoding="utf-8")
    (args.out / "操作轴.md").write_text(table, encoding="utf-8")


if __name__ == "__main__":
    main()

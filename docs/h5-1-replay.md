# 全息 H5-1 七术师 22 分续跑

先按[仓库安装说明](../README.md#首次安装)准备国服 Android 2.7.91 / V079 数据与 Python 环境。已有同版 APK 时可给 `tools.prepare_data` 传 `--apk`。要复用其他位置的数据，从准备到运行都使用同一个 `ARKNIGHTS_DATA_DIR`。Git 仓库提供入口、请求、排轴脚本和样本记录。官方资源与 Python、Node.js 依赖在各自设备准备。

## 复核一局

在仓库根目录运行。以下命令使用 PowerShell，其他系统可将 Python 路径换成虚拟环境的 `.venv/bin/python`。

```powershell
./.venv/Scripts/python.exe -m tools.run_caster_model --loadout-options
./.venv/Scripts/python.exe -m tools.run_caster_model --request workbench/h5-1-seven/inputs/marcille-clear-request.json --output h5-1-clear.json
./.venv/Scripts/python.exe -m unittest tools.test_manual_deployment tools.test_recal_fields
```

胜局请求固定战斗种子 `1407735752`、地图种子 `1982040983`。预期返回 `stage_victory_projected`，F10751 结算，64 敌生成并击破，剩 1 命，七人 22 分。词条由结果的 `rune_selection` 提供。修改操作前先确认本机这一局能复现。

| 请求 | 当前模拟器结果 | 用途 |
|---|---|---|
| [玛露西尔固定胜局](../workbench/h5-1-seven/inputs/marcille-clear-request.json) | F10751，64/64 | 检查安装、部署保护和撤退返费 |
| [玛露西尔独立样本胜局](../workbench/h5-1-seven/inputs/marcille-independent-clear-request.json) | F10765，64/64 | 检查保留南炮麻痹的方案 |
| [玛露西尔 61 杀](../workbench/h5-1-seven/inputs/marcille-61-request.json) | F11632，组长先漏，239 血 | 复盘 F10500 撤逻各斯后的阵线 |
| [玛露西尔 63 杀](../workbench/h5-1-seven/inputs/marcille-63-request.json) | F12776，A2 先漏，2042 血 | 复盘逻各斯留场后的补刀 |
| [中央炮对照](../workbench/h5-1-seven/inputs/central-guard-51-request.json) | 51/64，原轴 38/64 | F8260 莫斯提马开技，真言授层，F8270 打断炮机 |
| [谬因 60 杀](../workbench/h5-1-seven/inputs/aphris-60-request.json) | F11983，A2 先漏 | 核对谬因三技能内换中继和末段覆盖 |

## 继续试轴

从[排轴工作目录](../workbench/h5-1-seven/README.md)调用配对脚本。固定操作轴的批量复跑用 `python -m tools.run_caster_seeds`。请求保留 `map_random_seed` 时地图种子固定。要对固定轴同时重抽战斗与地图种子，在请求副本里删除这个字段。条件轴的配对脚本默认每局同时重抽两个种子。

用[排轴 skill](../skills/arknights-timeline/SKILL.md)定位关卡、读回放和导出打轴器文件。装到另一台机器时保留技能目录中的 `SKILL.md`、`references/`、`scripts/` 和 `agents/`。只想导出已核对的固定胜例，可以直接运行下列命令。

```powershell
./.venv/Scripts/python.exe skills/arknights-timeline/scripts/export_timeline.py workbench/h5-1-seven/inputs/marcille-verified-axis.json --out h5-1-axis
```

## 当前验收边界

现入口在部署后的 F 至 F+29 执行不可选中与免伤。指南旧引炮样本 2.5% 来自接线前。接线后，包含南炮麻痹接力的[独立样本](../workbench/h5-1-seven/inputs/protected-bait-282293-sample.jsonl)为 1/96。另一批[首炮配对样本](../workbench/h5-1-seven/inputs/protected-delayed-eligibility-fresh.jsonl)中，调整引炮让干扰装置获取从 23/64 增至 36/64，同批整局均无胜局。当前尚未验证 5% 或实机成功率。

火山个别伤害按落帧区间的较晚端计算，可在回放的 `rule_applied` 中查到。将固定胜局隔离改为较早端后仍为 64/64，结算帧改变。其他候选轴需单独复核有无胜负变化。

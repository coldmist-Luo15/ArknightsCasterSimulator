---
name: arknights-timeline
description: 使用 arkskill 仓库现有的明日方舟逐帧模拟器打图。按指定关卡、阵容、技能和模组试部署、开技与撤退，运行整局并验证操作窗口。适用于模拟通关、失败复盘和操作轴搜索。
---

# 明日方舟模拟打图

在当前 ArknightsCasterSimulator 仓库根目录运行项目的 Python。Windows 虚拟环境可用 `.venv/Scripts/python.exe`，Linux 和 macOS 虚拟环境可用 `.venv/bin/python`。用简体中文工作。战斗结果以项目模拟器的实际回放为准。已有的 H5-1 七术师排轴可从仓库的 `docs/h5-1-replay.md` 接续。

1. **确定本局。** 接受关卡代号或名称和难度，保留用户指定的模式、词条、编队、技能、模组与目标。选人前运行 `python -m tools.run_caster_model --loadout-options`，载入当前包体中每名候选干员的技能和常规模组。换人沿用这份目录。脚本用 `caster_loadout_options(tables)` 载入一次，再用 `pick_caster(options, operator_id, skill)` 生成配置。有多个模组时，用 `branch` 指定目录里的完整分支；用户委托配队时按本图需要选，否则询问其偏好。默认将所选配置提升到支持的满配。有地图但缺编队时可先读图。未指定特殊目标时以模拟通关为目标。
2. **读图。** 按[模拟器的关卡输入](references/data-sources.md)用锁定版本的关卡表定位 stage_id，读格网、路线、波次、敌人和装置，找出最先影响站位或时机的事件。关卡数据的来源与本局可执行的规则以模拟器实际加载结果为准。
3. **生成请求。** 需要技能参数时读 `python -m tools.run_caster_model --catalog`。从项目 `examples/` 取接近的请求格式，在本次运行目录保存 UTF-8 的 request.json。填写 `scene_model: caster_combat`、stage_id、squad 和 actions。全息 H5-1 的关卡 ID 为 `level_recalrune_01-03`，另须填写 `recal_rune_ids`。部署坐标以左下为第 0 行、第 0 列，时间单位为逻辑帧，每秒 30 帧。保留用户指定的种子和时钟设置。原图打图时核对示例是否人为推迟首波、锁定目标或改过敌人，清除专项实验条件。
4. **正式运行。** 在项目环境执行 `python -m tools.run_caster_model --request <请求路径> --output <结果路径>`。整局请求去掉示例的 end_frame，从开局沿同一状态跑到结算；用户点名查看某帧时才按该帧截取。查明 stop 或 status、combat_unresolved、剩余目标生命和关键事件。若配置被拒或只获得局部回放，记录入口返回的原因、受影响的操作与已计算范围。
5. **试轴与交付。** 按[搜索与复盘](references/simulation.md)提出候选、运行、调整并复跑。交付实际测试的阵容、位置朝向、操作帧、对应画面信号、结算结果、请求和回放路径。未找到解时说明试过哪些方案，以及最早在哪个事件失败。
6. **输出两份操作轴。** 按[费用尺文件和人工表](references/output.md)运行导出脚本，交付打轴器 JSON 和人工操作表。两者使用同一份已验证的操作、PRTS.Map 格位和从 F0 开始的时间。

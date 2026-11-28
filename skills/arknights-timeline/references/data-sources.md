# 用模拟器定位关卡

从仓库根目录、项目 Python 环境读取 `arkengine.opening_scene.PinnedTables`。它使用与正式运行相同的 V079 数据目录、版本和文件校验。缺少数据时走项目的 `python -m tools.prepare_data`。已准备好数据的环境可离线查表。

例如查询「普通 CV-EX-5」：

```python
from arkengine.opening_scene import PinnedTables

tables = PinnedTables()
stages = tables.read("excel/stage_table.json")["stages"]
for stage_id, row in stages.items():
    if row["code"] == "CV-EX-5":
        print(stage_id, row["difficulty"], row["diffGroup"], row["levelId"])
```

同一代号可以对应普通、突袭或不同环境。上例普通为 `act28side_ex05`，突袭与它共享 levelId。用 stageId、difficulty、diffGroup 和用户选择定位行；只有会改变本局且无法唯一确定时才询问。链接中的 ID 只作为定位线索。

全息 H5-1 使用 `excel/crisis_v2_table.json` 的 `recalRuneData.seasons.recalRune_season_1.stages.level_recalrune_01-03`。将词条 ID 作为 `recal_rune_ids` 传给正式整局入口，用结果的 `rune_selection` 与 `score` 核对选择和总分。

按选中行读取 `tables.read("levels/" + row["levelId"].lower() + ".json")`。该 JSON 来自与官方热更版本匹配的固定配置导出。正式运行还会按需加载官包资源。报告来源时写明实际读取的文件或资源。格网索引对应 mapData.tiles；换算操作坐标前核对引擎的行方向。routes、extraRoutes、检查点和停留决定候选交战位置。waves 中的 preDelay 相对于所属调度事件；实际出生帧看回放。核对 enemyDbRefs 的本关覆写、predefines 与难度词条，再判断候选部署是否合法。

查到关卡行后，仍要把用户选择的 stage_id 和编队送入正式入口。遇到未支持的词条、装置或混编，保留原请求与诊断。

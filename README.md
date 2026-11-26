# ArknightsCasterSimulator v0.1

《明日方舟》术师逐帧战斗模拟器。给定关卡、编队和操作帧，模拟已接入干员的部署、开技、选靶、弹道与伤害，以每秒 30 个逻辑帧推进至关卡结算或指定帧。可固定种子复盘一次战斗，也可用多组种子估计同一条操作轴的通关率。

## 准备运行环境

需要 Python、Node.js 和 UnityPy。开发环境使用 Python 3.14、UnityPy 1.25.4。安装依赖后，将 `ARKNIGHTS_DATA_DIR` 指向已准备好的本地数据目录。以下命令在仓库根目录的 PowerShell 中运行。

```powershell
python -m pip install UnityPy==1.25.4
$env:ARKNIGHTS_DATA_DIR = 'D:\ArknightsData'
```

v0.1 对应国服 Android 2.7.91 / V079，热更新版本为 `26-11-13-06-37-23-89286612`。模拟器按下列相对路径读取本地文件。

| 数据目录下的路径 | 用途 |
| --- | --- |
| `runs/2026-11-18-cn2791-gamedata/` | 同版 `zh_CN/gamedata/`、`manifest.json`、`files.jsonl` 和预制体定位索引 `official-prefab-roots.jsonl` |
| `runs/2026-11-18-core-cn2791/` | 官方 `client.apk`、`hot_update_list.json` 和 `hot-update/` 资源 |
| `runs/2026-11-18-cn2791-base-index/extracted/` | 包内原路径 `assets/bin/Data/globalgamemanagers` |
| `runs/2026-11-17-H9-6-independent/spine-runtime/spine-core.js` | 读取动作事件的 Spine 3.8 运行库 |
| `mechanics/caster-prts.json` | 对应版本的 PRTS 机制记录 |

## 运行一条操作轴

先运行 `python -m tools.run_caster_model --catalog` 查看已收录的干员、技能和模组。将关卡 ID、编队和操作写入 UTF-8 编码的 `request.json`。部署使用游戏坐标，左下角为第 0 行、第 0 列。下面是一条异客的操作轴。

```json
{
  "scene_model": "caster_combat",
  "stage_id": "main_01-09",
  "squad": [{"operator": "异客", "skill": 3, "module": "Y"}],
  "actions": [
    {"operator_id": "char_472_pasngr", "frame": 631, "row": 3, "col": 8, "direction": "RIGHT"},
    {"operator_id": "char_472_pasngr", "kind": "skill", "frame": 1532, "phase": "after_tick"}
  ],
  "clock": {"fragment_start_frame": 0, "cost_input_order": "cost_before_input", "spawn_schedule": "native_wave"},
  "movement": {"first_born_tick_delay_frames": 1},
  "battle_random_seed": 17
}
```

```powershell
python -m tools.run_caster_model --request request.json --output battle.json
python -m tools.run_caster_seeds request.json --trials 100 --sample-seed 2
```

第一条命令将逐帧事件、结算帧和剩余生命写入 `battle.json`。在输入中增加 `end_frame` 可查看某一帧的状态。第二条命令固定操作、逐局更换种子并运行到结算，输出每局种子、胜负与样本通关率。指定 `map_random_seed` 可固定地图随机性；有未结算样本时，报告单列数量并给出通过率范围。

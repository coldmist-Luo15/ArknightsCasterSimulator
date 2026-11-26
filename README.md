# ArknightsCasterSimulator v0.1

《明日方舟》术师逐帧战斗模拟器。给定关卡、编队和操作帧，模拟已接入干员的部署、开技、选靶、弹道与伤害，以每秒 30 个逻辑帧推进至关卡结算或指定帧。可固定种子复盘一次战斗，也可用多组种子估计同一条操作轴的通关率。

## 首次安装

本地计算需要游戏数据。准备好 Git、Python 3.14 和 Node.js 后，在 PowerShell 中依次运行以下命令。首次安装和首次使用新干员或关卡时需要联网，建议预留至少 2 GB 磁盘空间。

```powershell
git clone https://github.com/coldmist-Luo15/ArknightsCasterSimulator.git
cd ArknightsCasterSimulator
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install UnityPy==1.25.4 unicorn==2.1.4
.\.venv\Scripts\python.exe -m tools.prepare_data
.\.venv\Scripts\python.exe -m tools.run_caster_model --catalog
```

准备命令下载固定版本的国服配置，从对应官包提取时钟和脚本等运行文件，并安装 Spine 动作读取库。运行一条操作轴时，模拟器按需下载该关卡和编队用到的官方资源。默认缓存目录为仓库下的 `.arkdata`。一次整局跑通后，同一配置和操作可以离线复跑；更换种子时若遇到尚未缓存的敌人或效果，仍需联网获取对应资源。

版本固定为国服 Android 2.7.91 / V079。如果已有这一版官包，准备命令可加 `--apk "D:\Games\arknights.apk"` 读取本地文件。要把缓存放到其他磁盘，在准备和运行时都设置 `$env:ARKNIGHTS_DATA_DIR = 'D:\ArknightsData'`。干员详情报告附上 PRTS 对应修订的链接，整局计算可以独立运行。[版本配置来源](https://github.com/Kengxxiao/ArknightsGameData/tree/7e560050b03c34661db1d322d008733072352b2a/zh_CN) · [官方资源目录](https://ak.hycdn.cn/assetbundle/official/Android/assets/26-11-13-06-37-23-89286612/hot_update_list.json)

## 运行一条操作轴

安装最后一条命令会列出已收录的干员、技能和模组。将关卡 ID、编队和操作写入 UTF-8 编码的 `request.json`。部署使用游戏坐标，左下角为第 0 行、第 0 列。下面是一条异客的操作轴。

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
.\.venv\Scripts\python.exe -m tools.run_caster_model --request request.json --output battle.json
.\.venv\Scripts\python.exe -m tools.run_caster_seeds request.json --trials 100 --sample-seed 2
```

第一条命令将逐帧事件、结算帧和剩余生命写入 `battle.json`。在输入中增加 `end_frame` 可查看某一帧的状态。第二条命令固定操作、逐局更换种子并运行到结算，输出每局种子、胜负与样本通关率。指定 `map_random_seed` 可固定地图随机性；有未结算样本时，报告单列数量并给出通过率范围。

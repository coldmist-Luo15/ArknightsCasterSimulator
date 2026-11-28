# 七术师 H5-1 排轴工作目录

先用[安装与复跑说明](../../docs/h5-1-replay.md)确认正式整局入口、V079 数据和固定胜例。本目录的 `inputs/` 保留原始操作、已展开的代表局和配对样本。`out/` 存放本设备的新试验结果，重复执行相同命令会补跑尚未写入的方案与种子对。

```powershell
./.venv/Scripts/python.exe workbench/h5-1-seven/run_policy.py --trials 64 --sample-seed 282658 hold-grant mature-windup
./.venv/Scripts/python.exe workbench/h5-1-seven/run_policy.py --seed-pair 1628992184 818739297 hold-grant central-guard
```

| 方案 | 改动位置 |
|---|---|
| `hold-grant` | `protected_bait.py`。保留首炮引炮和南炮麻痹接力 |
| `mature-windup` | `protected_delayed_eligibility.py`。用首炮走位安排艾雅法拉部署和撤退 |
| `central-guard` | `protected_central_guard.py`。中央炮已起手且未带麻痹时，莫斯提马开技触发真言补层 |
| `central-guard-aim` | 同时使用后两项条件 |

开发对照可以从靠近改动的关键帧分叉。试出新胜局时，脚本展开实发操作，并从 F0 通过正式入口复跑以核对所有事件。样本不会因单个干员被击倒而提前结束。估计改动的通关概率要换用未参与开发的抽样编号。

第三轮狼群以 F9031 为参考。莫斯提马提前退场时，拉普兰德可能少几格技力。排轴会保留已经就绪的原操作，否则等她在场、技能已亮且仍有可见敌人时补按一次。手动复刻时，按键未生效就看技能图标，亮起后补按。此版默认批次文件以 `compare-wolf-ready-` 开头，旧批次保留原结果。自己指定 `--output` 时，为改过的规则选一个新文件。

继续攻克时先看中央炮和南炮能否连续渡过。前一炮改变仇恨或延迟后，要核对下一炮的索敌、真言授层和各输出技能是否就绪。末段需要在 `marcille-63-request.json` 对应的战况中，为剩 2042 血的 A2 找到有效补刀窗口。

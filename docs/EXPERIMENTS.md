# 已有资产与专用实验

实现统一位于 `src/roboenv_perception/experiments/`。以下路径相对于该目录；Python 命令在已安装项目的环境运行，专用构建也可能需要 Artisan 环境及其依赖。

| 实验 | 实现与入口 | 方法和已有记录 |
| --- | --- | --- |
| 木桌：瓶子、笔记本、鼠标 | `wood_table/build_assets.py`、`evaluate_assets.py`、`package_scene.py`；`isaac_asset_worker.py`、`isaac_hinge_worker.py`、`isaac_scene_check.py` | 多视角拟合、人工角点、Coding / Review、实际 USD 受力与重载；见 [视频资产](VIDEO_ASSETS.md) |
| 白桌：iPad、耳机、FreeClip | `white_table/build_assets.py`、`isaac_audio_scene_worker.py` | 固定尺寸／布局先验、Blender 耳机、Artisan 导出；见 [Blender 后端](BLENDER_BACKEND.md) |
| 自行车、砖地与传动 | `bicycle/build_asset.py`、`reconstruct_ground.py`、`isaac_bicycle_worker.py`、`isaac_drivetrain_worker.py` | Blender、Artisan、观测地面纹理和简化固定比传动；见 [地面重建](BICYCLE_GROUND_RECONSTRUCTION.md)、[传动实验](BICYCLE_DRIVETRAIN_EXPERIMENT.md) |
| 自行车爆炸图 | `bicycle/prepare_exploded_evidence.py`、`package_exploded_study.py`；建模程序在 `scripts/blender/bicycle_exploded_study.py` | 独立外观／结构探索，未做动力学；见 [爆炸图研究](BICYCLE_EXPLODED_STUDY.md) |
| 固定基座 G1 接触 | `g1/isaac_robot_worker.py`、`g1/report.py` | 预设路径的合盖／拖鼠标；见 [G1 接触](G1_CONTACT.md) |
| 独立桌面接触夹具 | 共享 `construction/isaac_table_worker.py`；`scripts/run_table_sim.py --worker table` | 桌面支撑与摩擦检查；见 [Isaac 桌面](ISAAC_TABLE.md) |

## 命令

```bash
.venv/bin/python -m roboenv_perception.experiments.wood_table.build_assets --help
.venv/bin/python -m roboenv_perception.experiments.bicycle.reconstruct_ground --help

# 仿真统一由启动器选择对应的实验 worker
.venv/bin/python scripts/run_table_sim.py --help
```

白桌／自行车建模示例使用提供了 trimesh 等依赖的 Artisan 环境，显式传入其解释器：

```bash
PYTHONPATH=src /absolute/path/Artisan/.artisan/venv/bin/python \
  -m roboenv_perception.experiments.white_table.build_assets \
  --root outputs/white-table-NEW \
  --artisan-python /absolute/path/Artisan/.artisan/venv/bin/python
```

木桌拟合仍需要指定视频的已有相机、手工角点与桌面文件；G1 演示仍依赖原场景和参考机器人文件。这些入口没有被改成通用自动重建，通用流程用 `scene-harness`。

## 旧入口与结果

原 `scripts/build_video_assets.py`、`build_ipad_audio_assets.py`、`build_bicycle_asset.py` 等保留转接；原 `construction/isaac_*` 专用模块也可继续导入或执行。`scripts/blender/` 保留真实 Blender 生成程序，避免破坏其复制到独立进程后的运行方式。

7 类物体及 G1 的详细结果见 [2026-10-26 盘点](history/CURRENT_ASSETS_AND_ROBOT_RUNTIME.md)。该文是当时主机及本地输出的快照，本次目录整理没有重跑仿真。FreeClip 受力开盖、耳机折叠、机器人抓取等仍未因此得到验证。

所有 `outputs/...` 场景、纹理、渲染、报告与输入均留在各自运行主机，默认未随 Git 提交。旧实验需要这些工件及对应环境，不能只 clone 后直接加载文档所列场景。

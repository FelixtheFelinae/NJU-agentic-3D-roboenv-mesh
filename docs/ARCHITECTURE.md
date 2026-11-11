# 代码结构与调用关系

## 通用主线

`scene-harness` → `harness/cli.py:main` → `harness/run.py:run_harness`：

1. `pipeline.py` / `video_pipeline.py` / `harness/media.py` 处理单图、视频或独立多图。
2. `construction/evidence.py` 选取对象视图；`harness/scope_plan.py` 判断构建、背景和排除范围。
3. `harness/research.py` 可选检索资料；`construction/design.py` 生成带来源的部件与关节蓝图。
4. `harness/geometry.py` 生成类型化几何配方，调用 `blender_recipe.py`、共享 Blender worker、Artisan worker 和 USD 适配层。
5. 视觉审查失败后有界重建；`harness/scene.py` 组装资产与暂定支撑面。
6. `harness/physics.py` 调用 `isaac_generic_worker.py` 运行预定受力协议，物理失败可进入有界修复。
7. `harness/task_scope.py` 复核原始任务覆盖，交付阶段汇总状态与引用。

背景分支通过 `construction/background.py`、`reconstruction.py` 和 `train_worker.py` 准备 mask、恢复相机并训练 3DGS；具体是否执行取决于配置、证据、运行时和预算。物理支撑另用碰撞几何建立。

## 目录职责

| 目录 | 内容 | 入口 |
| --- | --- | --- |
| `src/roboenv_perception/*.py` | 感知、跟踪、API、数据契约、标注与评估 | `scene-perception` |
| `construction/` | 共享证据、设计、Artisan / Blender 接口、纹理、COLMAP / 3DGS 与桌面支撑 | 感知 CLI 的构建子命令及公共模块 |
| `harness/` | 自动流程、几何配方、范围复核、场景与通用物理协议 | `scene-harness` |
| `experiments/` | 绑定特定对象、场景和路径假设的实验 | 见 [实验入口](EXPERIMENTS.md) |
| `scripts/` | 安装脚本、公共工具、受信任 Blender 程序及旧入口兼容层 | 从项目根目录运行 |
| `docs/history/` | 按日期保留的运行证据与需求快照 | [历史索引](history/README.md) |

## 两种资产作者路径

- **Harness：**模型生成 `GeometryRecipe` 数据；受信任程序生成 Blender / Artisan 代码。支持的形状由 `harness/geometry.py:Solid` 定义，不能把它描述为任意自由建模能力。
- **早期木桌 Coding / Review 实验：**`construction/asset_agents.py` 让模型编写 Artisan Python，再编译并独立复核；由 `experiments/wood_table/build_assets.py` 调用。该模块仍保留为旧实验和共享编译工具的依赖，未并入 Harness 的几何作者接口。

两者共享证据／蓝图和部分后端，各自的候选与审查不能相互替代。专用 Isaac worker 也不能替代 Harness 的通用协议。

## 兼容与整理范围

2026-10-31 将 Harness 分支并入 main，并把木桌、白桌、自行车、G1 专用实现迁入 `experiments/`。旧脚本和 worker 模块保留转接，新调用直接指向新位置；共享桌面导出 worker、运动学、铰链分析及 Blender 程序保留原位置。

这次整理没有更改几何算法、物理验收标准或历史通过记录。Artisan 路径改为显式参数：白桌／自行车构建传 `--artisan-python`，木桌 `prepare/code/review` 传 `--artisan`。

源码迁移会改变 Harness 指纹；旧运行不能直接在新源码下续跑，需使用新输出目录。已经保存的历史工件不被改写。旧源码快照和旧工件应一起用于复现历史结果。

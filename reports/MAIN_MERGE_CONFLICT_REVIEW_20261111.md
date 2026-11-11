# object-design-background 与 main 的冲突处理

日期：2026-11-11。合并方向为 `origin/main` → `codex/object-design-background`。
本地 `main` 与远端分支均未改写，也未执行 push。

## 双方修改与处理结果

- 共同祖先：`900e53d468593023611ae53da9b70ae941c82def`。
- 本分支原提交：`c848d6fd0b1c8c0478a770e335ad18bb2baa2f83`。
- `git fetch origin main` 获取的 main：`70694a32c3e0ff5e0510298ed45480a62053b519`。
- 合并提交：`053c3f0ed2c13d117ae6941dba4c8496b98791c4`，父提交为本分支原提交与上述 main。

开始检查时已有待完成的合并，Git 记录的 10 个冲突已暂存为解决状态。本次逐项比较共同祖先、本分支、main 与解决结果，确认以下处理同时保留双方有效修改。

| 冲突文件 | 双方意图与最终处理 |
| --- | --- |
| `README.md` | main 重组方法、入口、实验与历史资料；本分支补充 EnvCreator、Studio、ClawSpine 与工具工作流。保留重组结构及新增功能说明，并修正 ClawSpine 接入状态。 |
| `scripts/build_video_assets.py` | main 将木桌实验移至 `experiments/wood_table/build_assets.py` 并要求显式传 Artisan 路径；本分支将 `prepare`、`appearance` 提取到共享 `construction/video_asset_workflow.py`。新位置调用共享实现，保留 `--artisan` 检查，旧脚本继续转接。 |
| `scripts/evaluate_video_assets.py` | 保留 main 的实验迁移与旧脚本转接，新位置调用本分支共享 `evaluate`，从返回报告的 `assets` 字段输出摘要。 |
| `construction/isaac_asset_worker.py`、`isaac_hinge_worker.py`、`isaac_scene_check.py` | 实现迁至 `experiments/wood_table/`；保留本分支 PhysX 配置适配、四元数／状态顺序转换，以及资产 worker 的包内桌面来源检查和运行时版本记录。 |
| `construction/isaac_audio_scene_worker.py` | 实现迁至 `experiments/white_table/`；保留运行时兼容更新，内部 `adapt_asset` 导入指向迁移后的木桌模块。 |
| `construction/isaac_bicycle_worker.py`、`isaac_drivetrain_worker.py` | 实现迁至 `experiments/bicycle/`；保留本分支运行时更新，传动 worker 引用迁移后的自行车模块。 |
| `construction/isaac_robot_worker.py` | 实现迁至 `experiments/g1/`；保留本分支运行时配置及输出状态转换。 |

表中 `construction/` 与 `experiments/` 均相对于 `src/roboenv_perception/`。7 个旧 worker 模块保留延迟导入和脚本执行转接。`scripts/run_table_sim.py` 使用 main 的新 worker 路径；main 的其他实验迁移、显式 Artisan 参数与文档归档一并保留。

## 验证

将暂存树导出到独立临时目录，使用其 `src` 作为 `PYTHONPATH`，复用现有 Python 环境和 Studio 的 Node 依赖。这样测试不读取工作区中其他未提交的功能修改。导出实现与合并提交逐文件比较一致；补充测试随后单独验证。

| 检查 | 结果 |
| --- | --- |
| `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -ra` | 507 passed，19 skipped，32.72 秒。 |
| `python -m pytest -q tests/test_experiment_entrypoints.py` | 最终补充测试 16 passed；包含 9 对旧脚本／新模块帮助入口、6 个缺少 Artisan 参数的拒绝用例，以及共享评估报告摘要契约。 |
| Studio 目录下 `npm run build` | 通过。 |
| `node --test tests/*.mjs integrations/studio/test/*.mjs` | 7 passed。 |
| 对本次合并涉及的 41 个 Python 文件运行 Ruff | 全部通过。 |
| `ruff check src tests scripts` | 95 项既有问题，全部位于合并未修改的文件；未扩展为无关格式清理。 |

19 个跳过项分别为：未配置真实 OpenUSD 解释器的 8 项、未配置 Blender 的 3 项、未配置外部 ClawSpine 路径的 2 项、本地资产未随 Git 提交的 5 项，以及 SAM 已安装使“不存在后端”条件不适用的 1 项。此轮没有据此宣称真实 Isaac 物理实验、视觉重建或机器人任务通过，也没有发起模型请求。

## Git 状态与范围

`origin/main` 已是当前功能分支的祖先，未解决索引项为空，合并状态已结束。补充修改仅为入口回归测试和本文档。工作区原有的其他未暂存修改及未跟踪文件不纳入本次提交；不新增资产、临时查看器或独立仿真迁移包。

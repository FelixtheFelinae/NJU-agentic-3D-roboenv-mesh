# Agent Harness 清理记录

2026-11-12。清理以 `43d3c72` 为基线，已随 `87243e7` 发布到 `main`。下面的检查结果属于当时的清理阶段。 [清理提交](https://github.com/FelixtheFelinae/SceneCreator/commit/87243e7e10e04460de74dc356d9c251370477245)

## 保留的调用链

`scenecreator workflow / studio` → 原生 DSH 或 CLI 决策 → `harness.workflow` → `components` → 感知、装配蓝图、几何构建与审核。

结构前检、Critic、约束修订、资产物理验证、证据与哈希记录保留。Studio、Blender/Artisan 接口、可选背景重建、标准连接、工具资产生成与验证、场景依赖打包均为 Harness 的运行依赖。

已有 `generate` / `scene-harness` 保留多对象批处理与 `delivery.json` 输出。当前媒体 Workflow 默认单目标，物理与背景默认关闭。配置 `usd_python` 后自动组装场景并导出候选包。 [流程简图](WORKFLOW_OVERVIEW.md) · [架构](ARCHITECTURE.md)

## 删除与迁移

| 范围 | 处理 |
| --- | --- |
| Python/Node 自动化测试、fixtures、Studio 测试 | 删除测试目录、测试依赖及外部补丁中的测试段落 |
| 合成演示与回放 | 删除 demo、demo-video、demo-construction、VLM/SAM replay 后端和配置字段 |
| 独立准确率评估 | 删除真值模板、数据冻结、指标评估 CLI 与对应契约 |
| 单场景实验 | 删除 experiments 两处目录、笔记本参考复现、白模/木模、自行车、专用桌面与机器人 smoke 等入口 |
| 旧脚本与一次性工具 | 删除兼容包装、专用拟合/渲染、v9 迁移及相关旧操作注册 |
| 文档 | 改写主 README、架构、环境与流程说明；移除历史实验报告，必要引用指向清理前提交 |
| 标准交互台架 | 将验证器实际依赖的三份协议原样迁入 `standard_interactions/bench/`，保持冻结哈希 |
| 运行时连接 | 更新共享 Artisan 后端导入、组件注册、Studio 配置与入口；补充组件 export 所需的 scene_id |

`tool-test` 与 `standard_interactions.validation` 是资产功能验证入口，按确认范围保留。当前标准台架只支持注册的三紧固件布局；标准件 USD 与工具资产仍须外部提供。物理报告中的理想夹持、固定底座、真实接触和机器人任务验证状态分别记录。

合成回放配置与被移除的实验命令不再兼容。当前配置示例均可解析。被删除的源码与历史报告可从基线提交恢复；本地媒体、资产、权重、凭据及既有运行输出未作为清理目标。

## 本次实际检查

- 121 个 Python 模块完成语法解析与内部导入检查；核心 Workflow、组件、构建、交付与物理验证调度模块导入通过。
- 修改的 Python 源文件通过 Ruff F 类检查。全源码同类检查仍有两处清理前已有的未使用导入，位于冻结运行时 `native_screw_runtime.py` 和 `solver_thread.py`；为保持注册哈希，未改动这些文件。
- 标准目录和运行时代码哈希通过核对，三份迁移协议与基线字节一致。
- Python wheel 构建通过。隔离解包后检查 6 项 CLI 行为，成功导出 7 份 Schema；包内包含运行时协议，已删除的实验、演示和测试模块未打包。
- `npm run build --prefix integrations/studio` 通过；9 个 JavaScript 模块与保留的 shell 脚本通过语法检查。
- 两份 ClawSpine 补丁格式可解析，`git diff --check` 通过。

本次没有运行开发测试套件，未发起模型请求，未启动 Blender/Artisan、CUDA 推理或 Isaac 仿真。上述结果仅覆盖代码、打包与入口完整性，不构成场景重建质量或物理行为验证。

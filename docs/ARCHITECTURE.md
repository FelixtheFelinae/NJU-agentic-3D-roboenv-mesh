# Agent Harness 架构

维护基线为 PR #5 的 `43d3c72`，本地精简保留运行时调用链。

## 主入口

`envcreator` → `integration/cli.py`：

- `workflow` → `harness/workflow_cli.py` → `Workflow` → `components.operations.run`。
- `studio` → `workbench/cli.py` → 原生 DSH 与 `integrations/studio/host.mjs`。
- Studio 工具 → `workbench/server.py` → `Jobs` → `worker.py` → 同一个 `Workflow`。
- `generate` → `harness/cli.py` → `harness/run.py`，保留多对象批处理与 delivery 输出。
- `handoff` → `integration/bundle.py` → 独立 OpenUSD worker → 依赖包。

## 组件与领域实现

VLM 负责对象语义、蓝图与几何配方作者、结构和视觉复审。SAM 提供可见区域分割。
`construction/assembly_blueprint.py` 保存零件、接口、关节、连接关系与来源。
`assembly_readiness.py` 检查描述能否由后端表达，`constraint_repair.py` 执行有界关系修订。
`harness/geometric_verifier.py` 核对配方实体和功能接口，`assembly.py` 核对连接计划。
Blender 将类型化配方变成网格。配置 `usd_python` 后，普通资产由 OpenUSD worker 写入物理 USD。

`construction/artisan_backend.py` 继续服务旧构建与冻结 v9 路径。`harness/direct_usd.py` 和 `harness/usd_writer.py` 负责新路径，`harness/declared_scene.py` 提供已知规格的螺钉盖板和限位铰链入口。
历史 Coding/Review 案例和专用拟合已从当前源码移除。

## 状态与交付

Workflow 负责阶段、原生决策身份、文件锁、revision 和恢复。组件产物与调用事件由
`artifacts/` 和 `execution/` 保存。Studio 展示它们并提交原生会话请求。

`integration/bundle.py` 收集 USD 依赖并保存清单与哈希。配置 `usd_python` 的媒体 Workflow 在组装后调用 export，声明式场景同时附带连接规则。场景组装、物理验证与交付组件仍可单独调用。

## 运行时验证

结构前检、Critic、几何审核、资产物理验证和证据检查属于运行时能力。
`standard_interactions/bench/` 包含标准交互验证器实际调用的三个固定协议文件，
从原实验目录迁入时保持字节及原协议哈希。它仍只覆盖注册的三紧固件台架。
标准件原始 USD、工具资产仍需在外部提供，源码中保留其身份与来源。

开发测试、合成 fixture、专用场景实验、一次性 v9 迁移和旧脚本别名已移除。
历史案例、报告及原测试可从 `43d3c72` 查阅。

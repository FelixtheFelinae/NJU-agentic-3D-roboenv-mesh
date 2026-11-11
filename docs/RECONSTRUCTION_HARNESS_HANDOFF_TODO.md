# Reconstruction Harness：跨对话交接与当前 TODO

更新时间：2026-11-11（Asia/Shanghai）。
共享仓库：`/home/felixz/NJU-agentic-3D-roboenv-mesh`。
实现基线：`e46403bc18715f5bba908ef6fe86d19798fad1dd`，分支 `codex/object-design-background`，已同步 GitHub。

交接对象：[Reconstruction Harness：Part-Level Assembly & Physics Consistency 审查对话](codex://threads/01a1c954-418a-7f52-9379-e6e4e08bd27e?hostId=remote-ssh-discovered%3Aubuntu)。
本文保存本对话当前进度与未完成项，不替代该对话的新需求，也不代表授权实施全部建议。
本次交接只增加文档，不启动下一轮开发、模型请求或物理实验。

## 1. 另一对话从哪里继续

先读本文，再读：

1. [当前固定流程及 P0-2～P0-5 的接口说明](FIXED_RECONSTRUCTION_WORKFLOW.md)。
2. [P0-5 实现与真实验证记录](../reports/FIXED_WORKFLOW_P05_20261111.md)。
3. [正在进行的零件装配与物理一致性审查](../reports/2026-11-11-reconstruction-harness-audit/README.md)。

第三份文档目前写明的审计基线是 `31081d2` 加当时的未提交代码。
**P0-5 已在之后提交为 `e46403b`**：局部修订现在是窄 `ConstraintRebinding`，
只返回 id/interface_refs/description，已接入 Workflow、Critic、原生工具和 GUI。
审计对局部修订的实现状态应对照最终源码更新；不要再把它整体列作未实现。
审计中的完整 Part/body 映射、连接状态、物性和修订失效闭包等缺口仍未由 P0-5 解决。
审计报告/离线探针属于另一条工作的文件，本文未改写它们。

## 2. 已实现的工作流与职责

```text
ClawSpine 原生 DSH Agent
  └─ workflow_start / workflow_advance：选择证据、对象、结构假设和恢复方向
      └─ EnvCreator 持久化固定 Workflow
          感知/证据 → Intake → AssemblyBlueprint → 自检查/独立审核
          → 结构前检 → GeometryRecipe → 几何/装配审核
          → Blender/Artisan → 外观复审 → 可选物理验证
```

VLM 用于内部语义理解、描述/配方作者和视觉审核；SAM 用于可见区域分割。
COLMAP/3DGS 属于可选的相机与背景外观重建，不等于机械资产恢复。
Blender 根据类型化基元生成网格；Artisan 将零件、关节等编译导出。
Agent 不逐次监督每个底层调用，固定流程执行后把适合判断的结果交回原生会话。
当前主流程与旧 generate、专用参考实验的门禁覆盖不同，不宣称所有入口已经统一。

| 阶段 | 已提交内容 | 关键代码与证据 |
| --- | --- | --- |
| P0-3，`640d651` | 组织树/关节树与功能接口前检；保留旧约束要求；Schema 有界纠正 | `construction/assembly_readiness.py`；[记录](../reports/FIXED_WORKFLOW_P03_20261111.md) |
| P0-4，`31081d2` | plane/axis/edge；关节轴、圆柱轴、实际箱体边绑定；方向/位置与导出关节一致性审核 | `construction/feature_relations.py`、`harness/geometric_verifier.py`；[记录](../reports/FIXED_WORKFLOW_P04_20261111.md) |
| P0-5，`e46403b` | 选定 1–4 条几何关系的受限修订；冻结其余结构；失败保留与恢复；GUI 按钮 | `construction/constraint_repair.py`、`components/operations.py`、`harness/workflow.py`；[记录](../reports/FIXED_WORKFLOW_P05_20261111.md) |

P0-5 调用：`workflow_advance` 的 action 为 `repair_constraints`，提供当前 revision、reason、constraint_ids。
调试组件为 `blueprint_repair`，依赖 blueprint 与 evidence。
只允许改选定关系的绑定/说明，并新增必要的假设/推断 plane/axis/edge；
不修改 Intake、零件、关节、Mate、已有接口、未知尺寸或冻结参数。
类型、来源、零件范围与容差由程序从原描述复制。它不支持 Split/Merge 或拓扑修复。
协议错误、候选哈希错误或 VLM REDO 返回 needs_revision；网络失败传播。
每次组件调用只有一个修订提案，是否再尝试由原生 Agent 与预算决定。

## 3. 已验证的案例，以及仍未通过的部分

最新真实运行目录：`/var/tmp/envcreator-workflow-p05-xgyf_6t9/`，最终结果在 `final/`。

| 对象 | 本轮完成 | 仍未完成 |
| --- | --- | --- |
| 笔记本，原视频 11 帧 | `gc_base_orthogonality` 从错误的线面垂直，改为后边 X 与右侧边 Y 垂直；自检查与独立审核通过 | 铰链仍是未决 hinge_region；不能当作接触轴孔，因此结构前检继续阻塞 |
| 自行车前轮，原视频 6 帧 | `gc_rim_planar_face` 补上具体的假设 plane 绑定；自检查与独立审核通过 | 组织父节点与关节树冲突、未决接触接口；新增平面法线 Y 与原轮轴 Z 的机械关系仍可疑 |

两案例源文件哈希和未选字段已比对保持一致，请求图片哈希与实际原图一致。
没有搜索网络规格，没有重跑 SAM/COLMAP/3DGS，没有生成新 Mesh/Blender/Artisan 资产或运行物理仿真。
这是外层引导共享组件的真实内部 VLM 验证，不是原生 DSH 自主端到端成功。

本地路径：

- 笔记本证据：`/var/tmp/envcreator-workflow-p02-b5FcQU/fused-final/`。
- P0-4 失败及修订输入：`/var/tmp/envcreator-workflow-p04-r_xpjsz8/`。
- P0-5 最终笔记本：`/var/tmp/envcreator-workflow-p05-xgyf_6t9/final/laptop/blueprint/`。
- 自行车原证据：`outputs/bicycle-ground-20261026/evidence/`，asset-0003。
- P0-5 最终自行车：`/var/tmp/envcreator-workflow-p05-xgyf_6t9/final/bicycle_front_wheel/blueprint/`。

`/var/tmp` 记录不在 Git 中，路径依赖本机保留情况；后续使用前核对文件与 manifest。
旧失败提案和哈希错误 trace 均保留，不能重标为通过，也不能将历史真值当作新视频测量。

最后一次本对话完整验证：**602 passed、14 skipped、36.84 秒**；Node **7 passed**；前端构建成功。
命令和跳过原因见 P0-5 报告。未配置的真实 Blender/OpenUSD/ClawSpine 检查不算通过。
Studio 未重启、未做浏览器视觉验收，不代表用户当前页面已加载新按钮。

## 4. 当前 TODO：先合并两边规划，再实施

以下均为**未完成建议**。优先级是本次交接建议，可由新需求审查调整；不要与另一对话同时修改同一模块。

| 优先级 | 待办 | 代码起点 | 最小验收 |
| --- | --- | --- | --- |
| P0 | 把机械角色间关系变成显式要求，先检查轮圈侧面与轮轴 | `assembly_blueprint.py`、`feature_relations.py`、`assembly_design.py`、`geometric_verifier.py` | 明确区分轮圈侧面/中面与胎面等角色；侧面法线平行轮轴的正例通过，Y/Z 错配负例失败；未知角色/方向保持未决；来源与未选关系保留 |
| P0 | 声明方向/局部帧与实际绑定一致性 | `InterfaceBinding`、`verify_geometry`、`workflow_hypothesis.py` | 在当前平移参考树内检出声明法线/轴线与实际实体不符；不把文本标签当实际几何；不支持的姿态明确阻塞 |
| P0 | 已知质量、参考状态与构建消费的一致性 | `GeometryRecipe.check_against`、`geometry._model_source`、`AssemblyState` | 不能用 recipe 假设质量静默覆盖已知值；参考状态明确消费或拒绝；新增正反例并保存来源 |
| P0 | 区分运动学近似与接触级审核 | `PhysicsIntent`、`analyze_readiness`、构建和验证门禁 | 显式选择并记录保真度；理想铰链可否构建必须有对应编译/验证协议；不自动改 Mate、造隐藏轴孔或宣称接触已验证 |
| P1 | 精确连接身份及 Part→body/collider/Joint/runtime 映射 | `assembly_adapter.py`、`connection_compiler.py`、标准件导出与运行时 | 只替换明确授权的 closure；同端点永久连接保留；保存编译前后差分并在 USD 重载后核对 |
| P1 | 局部修订后的证据失效与回归范围 | 现有哈希、阶段收据、Critic 与修订事务 | 修改字段明确关联受影响检查；相关旧 PASS 作废，邻接未修改关系回归；当前窄接口继续冻结其他字段 |
| P1 | 连接维持/释放状态与两类依赖 | `AssemblyPlan`、`ConnectionBinding`、标准交互运行时 | 区分工具可达先后和连接释放依赖；单连接变化不误解除其他连接；状态/原生约束切换可追溯 |
| P1 | 导出物性、碰撞和约束清点 | USD 适配/导出、物理验证 | 实际重载核对 body、质量/惯量、孔洞碰撞、约束与路径；不能只检解析配方或渲染 |
| P2 | 单螺钉、三螺钉、铰链、多板箱体的分层物理回归 | 另一份审查报告 E/F 节及已有台架 | 正常操作与不应生效的对照都执行；标明理想夹持/真实抓持条件；保留已知小开角、导程和释放时序失败 |

前三项与另一份审查的“描述—构建一致性”重合，应合成一条演进路线。
本对话最后建议的最小下一任务是第一项，**不以立即跑通笔记本建模为理由放松接触门禁**。
另一份审查中有更高风险或更基础的反例时，由该对话按新需求调整顺序。

## 5. 继续工作时需保留的约束

- Agent 决策与固定 Workflow 分工保持；复用原生 ClawSpine 会话/历史/插件，不新增任务模型循环。
- Qwen/`VLM_API_KEY` 只用于 EnvCreator 内部 VLM；不能自动作为 DSH 或机器人推理凭据。
- Visual PASS、Schema/编译通过、仿真可加载与机械/物理功能通过分开记录。
- 视频未观测的尺寸、隐藏结构、运动参数保持 unknown 或显式 assumed，不冒充测量。
- 全系统多视频/多场景并发、不同渲染器并行、GPU 资源感知调度和容器封装属于长期规划；本轮未实施，不默认只面向 16 GB 显存，也不盲目加并行。
- 继续使用保留的笔记本/自行车证据；工具生成仍须满足 `docs/PHYSICAL_ASSET_REQUIREMENTS.md` 的来源、假设和正负功能验证要求。
- 本机存在其他对话的环境配置/报告改动。实施前重新检查 Git 状态，不能覆盖、回滚或顺手提交他人的改动。
- 只上传代码和相关文档，资产、媒体、权重、凭据与本地专用驾驶/查看器/迁移包不上传。

后续更新本文时，补充完成提交、实际产物、测试及剩余失败；不要仅把建议条目标成完成。

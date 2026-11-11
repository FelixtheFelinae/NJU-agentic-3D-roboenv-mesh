# P0-5：按约束 ID 的局部关系修订

日期：2026-11-11。基于 `31081d2`；保留固定 Workflow 和原生 DSH Agent 会话。

## 已实现的方法与入口

| 代码 | 已实现的职责 |
| --- | --- |
| `construction/constraint_repair.py` | ConstraintRebinding / ConstraintRepair 窄输出契约、选定 ID 校验、哈希绑定、受限应用、单次 VLM 提案、原有自检查及失败产物 |
| `components/catalog.py` / `operations.py` | 注册 blueprint_repair，读取原蓝图和同对象真实证据，输出 blueprint 与 constraint_repair |
| `harness/workflow.py` | repair_constraints 决策、修订预算、已完成阶段复用、独立审核、Critic 与 recovery；摘要附几何约束 ID |
| `harness/workflow_critic.py` | blueprint_repair 的失败归入结构/证据修订方向 |
| `workbench/observations.py` / `server.py` | 返回局部修订产物和参数说明 |
| `integrations/studio/host.mjs` | 原生 Workflow 决策 Schema 和工具说明；组件仅在调试工具面暴露 |
| `integrations/studio/src/workspace.jsx` | “局部修正关系”按钮，通过 DSH 原生自然语言会话提交要求 |

默认使用 `workflow_advance`，附当前 revision、reason 和 1–4 个 constraint_ids。
所选 ID 必须实际存在且互不重复。它消耗已有轮数预算，不引入新 Agent 或无限修复循环。
调试入口为 blueprint_repair；sources.blueprint / evidence，params.constraint_ids。

实际数据流：

```text
Agent 选择具体约束 → 固定 Workflow 保存决策
  → 原蓝图/哈希 + 原图 + 相关接口/约束 + 确定性反馈
  → 内部 VLM 单次窄提案
  → Schema / ID / 哈希 / 修改范围 / 来源 / 特征角色与方向检查
  → 原有描述自检查 → 原有独立审核 → 结构前检
  → 通过时回 structure；失败时保存候选和原因回 recovery
```

局部作者不再重新生成 Intake 或完整蓝图。模型只能返回约束 ID、接口引用与说明，
另可添加最多 8 个必要的 assumed/inferred plane/axis/edge。
程序从原描述复制要求类型、零件范围、参数、来源等冻结字段。
原有零件、实例、关节、Mate、已有接口、未知尺寸、状态和其他约束保持一致。
新增接口没有确定位置、尺寸参数或虚构接触硬件；不能覆盖已有接口。
已有轴线—边缘要求仍受原规则保护，不能改成辅助平面。

报告记录源文件哈希、选定 ID、结果哈希、是否应用候选及错误。
拒绝提案时输出原描述副本和 needs_revision，避免丢失上游记录。
已应用候选的自检查若 REDO、格式错误或哈希无效，也保存为 needs_revision，不能视为 PASS。
网络错误继续传播；阶段恢复复用完成步骤，未完成局部阶段仍可能重新请求模型。

## 真实案例与输出

记录目录：`/var/tmp/envcreator-workflow-p05-xgyf_6t9/`。
最终输出在 `final/`，包含两案例 result、repair-author 请求/响应、proposal、自检查、
独立审核、constraint_repair 报告和 geometry-preflight 结构门禁。
输入为 P0-4 保留的笔记本失败描述、自行车前轮描述；未手改这两份候选 JSON。

内部 VLM 使用已有 EnvCreator 配置，未用于 DSH 任务推理或机器人动作。
本轮由外层引导共享组件验证，不是原生 DSH 自主端到端运行。

| 案例 | 实际输入 | 最终局部变化 | 描述审核 | 实际构建前检查 |
| --- | --- | --- | --- | --- |
| 笔记本 | 原视频 11 帧；gc_base_orthogonality | 保留 orthogonal；原错误的顶面—后边改为后边 X 与新增右侧边 Y 垂直 | 自检查 ready_with_assumptions；独立 review_passed | 仍 blocked：铰链区域不等于实际轴孔表面；几何作者未调用 |
| 自行车前轮 | 原视频 6 帧；gc_rim_planar_face | 保留 planar；增加显式假设的轮圈侧面 plane 并绑定 | 自检查 ready_with_assumptions；独立 review_passed | 仍 blocked：组织父节点冲突、未决接触接口；几何作者未调用 |

程序逐项比对：除选定约束绑定/说明和新增接口外，所有字段一致；
原接口前缀与未选约束一致，源文件 SHA256 未改变。
实际模型请求图片哈希与保留的 11/6 张原图分别一致。
没有网络检索、SAM 重跑、COLMAP、3DGS、Blender/Artisan 建模或物理验证；
没有新增 Mesh 或其他资产。

### 失败记录与边界

首版方案要求模型返回整个 GeometricConstraint，并核对冻结字段。
笔记本增加了额外来源引用，因此被拒绝。随后缩成只允许 id/interface_refs/description，
冻结字段改为程序复制；此变化是本轮代码的最终设计。

第二轮笔记本把原含 edge 的要求改成全平面关系，触发已有特征替换保护；
自行车自检查返回缩略哈希，不能证明审核绑定当前候选。
最终局部作者提示明确优先相邻边，保留边缘角色；
自检查协议失败则保存候选和 needs_revision，不再使失败候选只留执行异常。
最终两案例重新请求后得到上述结果；前两轮失败 trace 仍保留，未把失败重标为成功。
每次组件调用仍只有一个提案，没有内部自动多轮修复。

自行车最终平面说明仍可疑：它给出法线 Y，同时原轮轴方向为 Z，
却用文字称符合轮圈平面垂直旋转轴的常识。这两条声明不足以证明它选择了正确侧面。
当前所选 planar 只检查明确绑定，没有新增“轮圈侧面法线平行轮轴”的要求，
所以此缺陷没有被本次确定性关系检查覆盖；VLM 的 PASS 也没有发现它。
必须保留这个限制，不能把局部绑定通过解释为自行车几何或语义整体正确。

## 验证

新增测试覆盖保护字段、禁止额外字段、伪造接口、哈希/身份、无变化提案、
方向缺失、轴线—边缘保护、真实组件事务拒绝、审核哈希错误/REDO、
继承显式测量、超时不重试、Workflow 中断恢复与失败返回 recovery。
使用明确标注的合成测试，不充当视频测量或物理实验证据。

实际检查结果：

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs --tb=short`：**602 passed，14 skipped，36.84 秒**。
- `node --test tests/test_studio_native.mjs tests/test_envcreator_dsh_plugin.mjs tests/test_clawspine_smoke.mjs integrations/studio/test/client.test.mjs`：**7 passed**。
- `integrations/studio` 中 `node scripts/build.mjs` 成功；变更 Python 文件 Ruff 与 `git diff --check` 通过。

跳过项涉及未指定真实 Blender/OpenUSD/ClawSpine 入口，以及本机已有 SAM 时不适用的缺失后端场景。
这些未运行的真实集成不计作通过。执行测试时工作区存在其他进行中的环境配置改动，
本轮未修改或提交它们。

前端已完成构建；原生工具测试检查新 action、constraint_ids 上限和调试组件注册。
本轮未重启活跃 Studio；构建成功不等于在用户当前浏览器里已加载新版本。

## 下一步

优先把**轮轴与轮圈侧面这类机械角色关系转成显式的可检查要求**，
避免只检查孤立 planar 绑定就遗漏它与旋转轴的矛盾。
再设计 physics_intent 对应的运动学近似/接触级构建分流，
在协议明确之前不放宽未决铰链接触门禁。

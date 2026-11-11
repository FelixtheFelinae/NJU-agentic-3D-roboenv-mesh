# P0-3：结构与构建边界诊断

2026-11-11。接续 P0-2；复用原生 DSH、固定 Workflow、结构作者、自检查、独立审核与原有几何门禁。
没有增加 Agent、模型聊天循环、CAD 后端或物理协议。

## 已实现的调用关系

`construction/assembly_readiness.py` 的 `analyze_readiness` 读取结构描述，输出：

- 原分组父关系和关节父关系；对应的具体冲突路径。
- 单根、唯一入关节、关节闭环、缺失机械定位、未被后端表示的 Mate 等问题。
- 几何要求所需接口数量和类别；单个平面、两个平面与实际圆柱类接口分别检查。
- `compatible` / `blocked`、问题代码、修订位置和修订对象。

这些是后端兼容检查，不是几何验证。`compatible` 也没有证明尺度、行程或接触正确。
不支持的关系仍阻塞，未知尺寸和模糊接口仍可作为合法描述保存。

`harness/workflow.py` 在结构独立审核后保存不可变 `structure_audit` 阶段，绑定上游蓝图和文件哈希。
确认结构后先检查该收据；阻塞时返回 recovery，不请求几何作者。
`workflow_critic.py` 将这些问题归到结构或不支持的实现，避免误发给局部几何修复。
已有故障恢复与修订预算继续使用，未删除旧阶段。

`components/operations.py` 修订结构时给作者传入原图、previous、previous_readiness 和既有反馈。
独立调用 geometry 也重新计算并保存检查报告，在调用模型前阻塞不兼容描述。
旧 `generate` 的独立编排没有在本轮全面改造，不能据此声称所有历史入口都已统一。

`assembly_design.check_assembly` 保持描述与构建状态分开；新增前一版约束比较：
删除原约束、改变关系类型、将轴线/铰链区域要求替换成全平面要求会触发 needs_revision。
它没有实现一般的自然语言语义等价判断，也未覆盖所有 Mate/拓扑变化。

实际错误促成的两个小范围修正：

1. 结构提示明确接口 literal 为 `plane`，`planar` 是约束类型。
2. Workflow 的结构 Schema 失败最多纠正一次，保存原 response、rejected.json 与 author-repair-1。
   仅捕获 Pydantic 结构错误；网络异常不走这个重试路径，语义失败仍返回修订决定。

GUI 沿用 DSH 的结构页面与按钮，新增简短的“建模前检查”卡片，最多直接列出四项问题。
原图、候选和完整内部记录仍分别保留。没有重启当前正在使用的 Studio；正在运行的服务需要正常重启才能加载新代码。

## 实际视频验证

目录：`/var/tmp/envcreator-workflow-p03-j0in1Z/`。

输入为 P0-2 保留的笔记本原视频 11 帧融合证据，以及旧自行车场景前轮对象的 6 张原始证据图。
两份 evidence 的原始产物清单均通过检查；图像哈希与实际作者请求中的图像清单逐项一致。
没有重跑 SAM、COLMAP、3DGS，没有搜索网络尺寸，没有读取旧产品型号规格。
外层通过共享组件分阶段执行本次验证；不是原生 DSH 任务模型自主完成的端到端实验。

| 环节 | 笔记本 | 自行车前轮 |
| --- | --- | --- |
| 首次实际作者 | 接口类型 `planar` 不合法 | 运动关节没有有效单位轴线 |
| 有界格式纠正 | 重新请求一次后通过 Schema | 重新请求一次后通过 Schema |
| 描述 | 两零件、一个铰链；作者明确修订 display_lid 分组父节点 | 轮体与安装参考部件，旋转关系仍有假设 |
| 未知量 | 真实尺寸、rest、travel 继续未知 | 真实尺寸、rest、travel 继续未知 |
| 当时模型自检查 | ready_with_assumptions | needs_revision，指出结构问题 |
| 独立模型审核 | review_passed | 自检查未过，未执行 |
| 最终确定性检查 | needs_revision，发现轴线要求被换成辅助平面要求 | 描述检查可保留假设，但构建前检查仍 blocked；不覆盖之前模型的 REDO |
| 实际 geometry 入口 | 在模型前阻塞 | 在模型前阻塞 |
| 新 Mesh / 仿真 | 均未执行 | 均未执行 |

格式纠正试验重放各自已保存的首次失败响应作为 attempt 0，attempt 1 才调用真实内部 VLM。
没有把重放当作新调用，也没有手工改写模型 JSON。已有 Intake、原图和失败记录保留。

笔记本出现了关键反例：作者保留原约束 ID，却把原“铰链轴线平行”改写为“辅助平面对齐，保证同轴”。
平面法线平行不能证明原来的轴线关系，更不能证明同轴。独立 VLM 未拒绝这次替换。
随后补充的确定性修订检查在同一真实候选上检出 `axis_replaced_by_planes`；
新报告独立保存为 `final-deterministic-check.json`，没有覆盖先前模型审核。
铰链仍只有 hinge_region，构建门禁也独立保持 blocked，没有通过候选偷换放行。

自行车的格式修正没有解决轮体分组与旋转关节父节点冲突，且部分轴线要求仍错误引用 plane/contact_region。
这些问题保存在结构检查中，没有自动加轴承、固定关节或接触几何。
这个试验只覆盖选定的前轮证据，不是整辆自行车重建成功。

实际产物与重现脚本：

- `validate.py`、`validation.log`：两个案例的初次组件执行及失败。
- `retry_schema.py`、`schema-repair.log`：复用真实失败响应，执行一次实际格式纠正及检查。
- `final_check.py`：最终确定性修订检查、原始图像清单匹配及实际 geometry 入口阻塞验证。
- `laptop-before.json`：P0-2 原描述的七条具体兼容问题。
- 各案例 `schema-repair/`：原失败、修正后的蓝图、模型自检查、独立审核（如有）。
- 各案例 `final-deterministic-check.json` 与 `geometry-preflight/construction_readiness.json`。
- `result.json`：完整结果及原规范蓝图、融合证据未变化检查。

在仓库 `.venv` 中运行上述脚本；首次与纠正脚本使用本机 `configs/local-studio.toml` 的内部 VLM 配置及独立环境凭据。
再次执行需换新输出目录，避免覆盖本轮证据。最终检查脚本不需要模型调用。

## 回归与范围

核心回归覆盖：前置诊断、合法未知量、错误接口数量/类别、环图、被篡改审核收据拒绝、
恢复后不重复感知、结构修订后进入几何、组件入口拒绝绕行、Schema 重试上限与网络异常传播，
以及删除要求、改变关系、用辅助平面替换轴线要求的负例。

执行 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs --tb=short`：
当时完整工作区结果为 **530 passed、14 skipped，33.35 秒**。
跳过项涉及未配置的真实 OpenUSD、Blender、ClawSpine 边界及一个不适用的 SAM 缺失情景，不算通过。
本轮新增的 `test_assembly_readiness.py` 包含 18 个用例（含参数化）。

执行 Node 测试 `tests/test_studio_native.mjs`、`tests/test_envcreator_dsh_plugin.mjs`、
`tests/test_clawspine_smoke.mjs`、`integrations/studio/test/client.test.mjs`：**7 passed**。
`integrations/studio` 下 `node scripts/build.mjs` 构建成功。
本轮 Python 修改的 Ruff 检查与 `git diff --check` 通过。

工作期间当前分支被其他工作合入了 origin/main，并出现与本轮无关的
`tests/test_experiment_entrypoints.py` 修改；本轮未编辑、回退或提交这些变更。
上述全量测试是当时整个工作区的结果，不将这些额外代码计为本轮实现。
本轮没有新资产和物理实验，没有浏览器现场验收，也没有用单元测试证明真实接触功能。

## 下一步

优先给几何要求增加明确的特征角色：面、轴线、边线及其关联的实际几何绑定。
补齐“轴线相对轮面”和“铰链轴线相对边线”的确定性审核，不能继续用全平面关系代替。
同时明确外形/运动学近似与接触级构建的能力边界，使模糊隐藏接口可以保留候选，
并避免把近似资产交付说成真实轴孔配合已恢复；不以继续增加 Agent 解决这些问题。

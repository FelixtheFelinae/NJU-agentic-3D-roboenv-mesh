# P0-2：跨轮原始证据融合与独立构建假设

2026-11-11。将用户“继续 P02”按上一轮报告的下一步实现为 P0 的第二轮。
本轮保留已有 Workflow、原生 DSH 会话及组件，不增加模型聊天循环。
没有安装依赖、删除旧记录、提交 Git 或重启当前 Studio。

## 已实现的方法与调用关系

### 原始观察融合

harness/workflow.py 在回访后的 select_target 与结构作者之间执行内部 evidence_fusion 阶段。
Agent 确认不同跟踪 ID 属于同一对象，并给出理由；不按类别自动合并。

harness/workflow_evidence.py：

- 保存各轮证据/感知来源、对象 ID 与轮次；检查完成状态和文件哈希。
- 只复制原始 frame、mask、rgba、context；视频 frame 副本须匹配解码清单中的图像哈希。
- 用原视频 SHA256 与原始 PTS 有理数时间去重。跨轮 view/frame ID 统一命名，原编号另存。
- 默认合并不同帧，最多 16 张；超出时 Agent 用实际观察 ticket 显式选择。
- 没有加入新的原始帧则拒绝重新建模。候选渲染不能作为 ticket 输入。
- 融合结果独立落盘，保留所有原始记录；中断在收据保存之前时只重做该阶段。

融合包保留稳定对象 ID。原始跟踪 ID、引用与身份判断进入 fusion.json 和 evidence.json。
新/旧图像共同提供给 Intake、结构作者和独立审核，不只给旧失败摘要。

### 独立的候选构建假设

harness/workflow_hypothesis.py 增加受限、类型化的 BuildHypothesis。
原生 workflow_advance 的 propose_build 携带：

- 当前规范蓝图的文件 SHA256；
- path、value、unit、rationale、evidence_refs 参数列表。

只填补未知的目标尺寸、零件定义尺寸/质量、有界关节参考约定和行程。
禁止覆盖已知量、改拓扑或接口、引用候选图、将相对单位直接改成米。
本轮需要改变已有假设值时，从原规范未知描述另提新候选，不原位覆盖。

Workflow 创建独立蓝图及 hypothesis.json，保存原/新哈希。
数字标成 assumed，新增 prior:build-* 来源；原图是选取假设的上下文，不能声称测得这些数值。
原规范描述、原始未知量、observed state、接口、Mate 和未决项均保持不变。

候选先做确定性描述自检查，再交现有 VLM 独立结构审核。
审核通过后返回结构决策点，确认后才运行几何作者。Agent 的决定及原生调用身份继续记录。
前端新增“尝试带假设的候选”按钮和假设摘要；按钮仍通过原生 DSH 对话调用。

### 配方审核与失败反馈

geometry 阶段显式获得已采纳的构建假设。
新增 build_hypothesis_audit：

- 检查盒状几何在当前参考构型的目标包络；
- 检查每个同类实例是否满足定义尺寸，检查采纳的质量与关节参数；
- 采用假设尺度的几何不能被标成 observed/reference；
- 当前不支持的几何类型或参考树保持 unresolved。

检查失败时最多局部修一次配方；之后仍执行原几何和装配门禁。
components.operations.geometry_operation 在实际 build 前再计算假设审核，不接受旧报告替代。
候选和资产记录附带构建假设及“原始未知量保留”标记。

真实实验发现几何作者误把普通铰链填成不完整标准紧固连接。
harness/geometry.py 因此明确 assembly 是紧固件 stack 计划，普通转动副不等于标准螺纹连接；
没有独立紧固件时 assembly=null，但保留 joints、接口和未决要求。
Workflow 的配方格式/身份校验失败最多根据真实错误重试一次，保存 rejected.json 与 trace；
旧调用默认仍不增加重试。这不是无限模型循环，也不放宽几何门禁。

## 实际笔记本验证

实验目录：/var/tmp/envcreator-workflow-p02-b5FcQU

本轮复用 P0 保存的两轮真实 SAM/视频感知产物，验证前检查其完整性。
没有重新运行 SAM、没有网络搜索规格、没有读取旧产品尺寸。
VLM 重新执行合并证据下的 Intake、结构作者、自检查和独立审核，以及假设审核、几何作者。

原视频哈希：
08be6039615c0b0807ddad022272f8cbd27204024e3d7cdd02604c516df9f34e

| 步骤 | 真实结果 |
| --- | --- |
| 融合 | 首轮正面 6 帧 + 回访侧后面 5 帧，合计 11 个不同原始帧 |
| 新规范描述 | 机身、显示屏两部件；1 个 revolute 候选。尺寸、行程、rest 和隐藏接口继续未知；ready_with_assumptions |
| 独立结构审核 | review_passed；这只是模型审核，不是机械正确证明 |
| 单独候选计划 | 0.30 m 试验宽度、centered 参考约定、2 rad 试验行程；均为显式假设，无型号或测量依据 |
| 假设审核 | 独立 VLM review_passed；原规范蓝图 SHA256 不变 |
| 首次几何作者 | 输出不完整标准连接，未通过配方类型校验；保留原 response 与错误 |
| 小范围提示修正后 | 新配方仅有两块 box、assembly=null；未擅自补螺丝或轴孔 |
| 确定性审核 | 配方假设审核、原几何审核均 unresolved：organizational parent differs from builder joint parent |
| 后续执行 | 没有进入 Blender、Artisan 或 Isaac；build_status=not_run，physics_status=not_run |

新语义中两零件的分组父关系与当前构建器的关节树不兼容；
部分正交/平面条款也仍没有足够具体的功能面绑定。
本轮记录这些问题，没有为了生成资产而手改模型产物或删除要求。
普通铰链不再冒充标准紧固件，但隐藏接口的算法推断仍未解决。

实际调用是通过共享组件入口进行的分阶段验证，由外层 Codex 提供参数；
不是一次原生 DSH 任务模型自主运行，也不是完整端到端生成成功。
控制器中的融合、中断恢复和假设门禁由独立回归测试覆盖。

实际产物：

- result.json：本轮状态和原蓝图未变化检查。
- fused/：实际交给 VLM 的 11 帧证据包及像素来源。
- description/blueprint/blueprint.json：新的规范语义描述，未知量保留。
- description-review/review/review.json：独立语义审核。
- candidate-plan.json、hypothesis/：明确构建假设、派生蓝图与原/新哈希。
- hypothesis-review/：候选独立审核。
- geometry/：首次失败的模型响应；candidate-error.log 保留错误。
- geometry-retry/geometry/recipe.json：修正提示后生成的新配方。
- candidate-conformance.json、geometry-audit/geometric_check/geometric_verification.json：实际未决审核。
- fused-final/、final-fusion-check.json：随后完成跨轮 frame_id 命名隔离的最终证据格式；
  11 帧、11 个唯一 frame_id，frame/mask/rgba/context 像素哈希与 VLM 输入包完全一致。
  这一步没有再次调用模型。

上述实验都是图像/结构与配方检查，没有新 Mesh、USD、渲染或物理实验。
图像来源是原视频；构建计划中的试验数值不代表恢复出了真实尺寸或真实行程。

## 实际测试与构建

执行：

~~~bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs   tests/test_workflow_p02.py tests/test_fixed_workflow.py   tests/test_assembly_blueprint.py tests/test_construction.py tests/test_components.py   tests/test_harness_geometry.py tests/test_harness_run.py   tests/test_assembly_build_gate.py tests/test_artisan_backend.py tests/test_asset_agents.py   tests/test_harness_physics.py tests/test_harness_assembly.py tests/test_geometric_verifier.py   tests/test_standard_export.py tests/test_clawspine_integration.py tests/test_studio.py   tests/test_reference_workflow.py tests/test_dsh_model.py
~~~

结果：258 passed，8 skipped，15.49 秒。非全仓库测试。
6 项跳过需要实际独立 OpenUSD worker，2 项需要显式设置真实 ClawSpine 路径，未计为通过。
最后精简 Agent 历史摘要后又执行三个相关测试文件：47 passed。

测试覆盖真实源帧去重、原记录不变、不同视频拒绝、伪造观察 ticket 拒绝、预算和新增帧要求；
融合阶段中断恢复、不重跑感知；规范未知量不变、过期假设/错误单位/布尔数值/假观测拒绝；
同定义多个实例的尺寸检查、尺寸/质量负例、未支持几何保持未决；
构建适配器再次计算审核，错误配方在 renderer 前被拦截；
显式假设仍不能绕过原先真实几何审核；配方类型错误重试有界且保留失败。

Node 三个相关测试文件：6 passed。工具参数包含 propose_build、观察 ticket 和蓝图哈希。
Studio 前端重新构建成功；相关 Python 文件 Ruff、git diff --check 通过。
没有本轮浏览器现场验收，也没有打断已有 Studio 服务；新工具需要正常重启后加载。

## 下一次应优先修改的内部边界

先处理 AssemblyBlueprint 的分组树与构建关节树的兼容诊断：
在结构进入几何作者前列出具体冲突，并让结构作者依据原图修订，不能由外层静默改 parent。
随后将正交、平面、同轴条款绑定到明确的接口/面；
对只能看到 hinge_region 的情况保留候选，不自动宣称恢复了轴孔或接触。

相对尺度构建、跨视频身份、多候选比较、物理参数修复、依赖安全清理和并行资源调度尚未实现。
本轮没有扩大这些范围。

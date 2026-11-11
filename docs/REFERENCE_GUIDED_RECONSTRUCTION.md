# 参考流程辅助重建

仅视频、排除产品尺度/比例先验的实验另见 [仅视频相对尺寸重建](VIDEO_ONLY_RECONSTRUCTION.md)。它通过 `reference_fit params.evidence_policy=video_only` 选择独立分支，不修改下文历史参考策略。

2026-11-09：在用户授权外层引导后，将此前人工监督电脑场景的领域方法接入 DSH 原生 EnvCreator 插件。DSH 继续持有唯一任务模型循环；外层提供目标、缺陷与工具修订。新增接口不是任意物体的通用自动重建能力。

## 代码与工具

`components/reference_workflow.py` 提供注册案例 `laptop-desktop-20261023`，调用复用的 `construction/video_asset_workflow.py`、`asset_fit.py`、`asset_agents.py` 和实际 Isaac workers。原 `scripts/build_video_assets.py`、`evaluate_video_assets.py` 仍可使用，公共准备/外观/轮廓方法已移入包内。

| 原生工具 | 输入 | 实际行为 |
| --- | --- | --- |
| `envcreator_reference_fit` | `sources.perception` | 严格核对同一原视频 SHA；重算两视角角点拟合和瓶子/鼠标轮廓拟合；生成具体冻结构建蓝图 |
| `envcreator_reference_code` | `sources.reference`，`params.asset_id`，可选 `feedback` | 内部 VLM 生成新的 Artisan `model.py`，真实编译并按反馈进行有界修订；不复制历史候选 |
| `envcreator_reference_validate` | `sources.reference`，`params.stage` | 分别执行照片投影、轮廓评估、蓝图 QA、实际场景仿真、独立重载、铰链主测试与补充测试 |
| `envcreator_reference_review` | `sources.reference`，`params.asset_id` | 对新候选进行独立 VLM 审核及可执行 QA；三个对象之后核对整场景 |

`asset_id` 为 `asset-0001`（瓶子）、`asset-0002`（笔记本）、`asset-0003`（鼠标）；场景审核为 `scene`。验证 `stage` 为 `appearance`、`geometry`、`qa`、`simulation`、`reload`、`hinge_primary`、`hinge_followup`。始终以最新的 `reference` 运行作为下一步来源。

本地设置需提供 `reference_case_root`，指向已审查 `outputs/user-scene-assets-20261023`；仿真阶段需配置独立 `isaac_python`。新增操作仍受现有队列、运行 ID、DSH call ID、取消、资源采样和产物哈希约束。没有提高并发，没有引入容器执行。

每次调用在独立目录创建新快照：`output/reference/reference.json` 保存阶段动态与历史证据绑定；`observation.json` 是提供给 Agent 的有界事实；`events.jsonl`、`resources.jsonl` 和领域原始日志保留实际执行情况。阶段失败仍可发布明确标记的未完成快照，便于后续修订；进程结束不等于该阶段或整体通过。

## 证据复用条件

只对完全相同的原视频使用历史人工角点、用户型号确认、相机/掩码和桌面配准。输入哈希不匹配时拒绝；来源在执行前后核对文件哈希。它们明确标为历史人工观测辅助和产品先验，不称为本轮新视觉识别、现场测量或策略训练。

新的候选代码、编译、照片投影、USD、物理轨迹、QA 与 Review 必须在本轮执行。旧模型、旧 USD、旧 PASS 不进入新结果。生成代码仍按原 Coding/Review 适配器进行能力筛查，不允许模型读取凭据或修改验收。

对象和场景独立审核自动接收已存在的 `hinge-primary`、`hinge-followup`，包括失败角度、漂移和报告哈希；审核前将原报告的场景及依赖哈希与当前快照逐项核对。失败项在对象观察和场景限制中保留，`PASS` 仅表示所声明的接触导向范围，不覆盖全部开角。每份审核还保存结构化 `supplementary_hinge`，避免只依赖模型的文字概括。旧案例没有专项报告时不虚构这些测试。

接入失败证据后的实际笔记本重审返回 **REDO**，场景审核因此受前置条件阻塞。此时不将旧场景 PASS 当作当前验收：对象重审和场景审核尝试都先在新快照中撤销旧 `scene-review.json`，历史目录保留；`assets[asset_id].review_details` 暴露有界问题、观察与专项报告哈希。最新实际执行结果见 `reports/LAPTOP_GUIDED_RECONSTRUCTION_20261109.md`。

## 构建和修订准则

1. Blueprint 要写清两刚体、单被动铰链、局部坐标、合盖零位、场景开角、尺寸/质量来源、独立支架及允许交互。照片中的打开姿态不能代替机构的闭合零位。
2. 先编译可用的主体机构，再处理可见外观。屏幕、键盘由真实照片透视校正，保留来源；贴图不用于证明隐藏结构、接触精度或独立可动按键。
3. 新 QA 除原有拓扑、铰链和运动避碰外，检查实际候选主体尺寸、局部中心和旋转是否遵守冻结蓝图；不能让候选决定自己的期望值。
4. 多视角轮廓评价是拟合外形的一致性检查。0.950 的笔记本轮廓 IoU 不等于真实米制精度，也不替代实际候选 QA 和接触验证。当前 0.94 下限仅适用于这一已注册案例，不是全物体通用标准。
5. 物理验证检查实际导出的 USD；机器人、支架固定条件、作用刚体、力矩与反力矩、驱动/释放阶段须按协议解释。独立重载不追加隐藏的姿态或关节状态修复。
6. 保留无驱动、小力矩不应运动、较大力矩应运动、释放、限位以及不同开角条件。旧版 15°/30°/35°悬停失败也要在新候选上复查，不能用初始角度保持通过宣称全角度悬停。
7. 候选变化使下游物理、轮廓判定与 Review 失效；重新投影外观也清除下游物理与 Review，防止交付旧纹理/旧证据。在新快照中清除旧判定，原历史目录保留。具体失败反馈到对应阶段，不改通过阈值，不将测试器异常当作物体缺陷，也不凭重复尝试宣称通过。

GUI 展示具体蓝图、最新真实渲染、验证条件和简短阻塞。机器事实与日志保存在内部路径，避免把完整英文提示或私有模型推理当作对人的进度说明。

后续将观测拟合、构建约束、照片投影、业务 QA 和独立重载逐步迁移为通用组件；当前注册案例是对现有有效流程的受限接入，不表示这些能力已经泛化到其他视频或机构。

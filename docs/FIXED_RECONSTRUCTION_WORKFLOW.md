# 固定重建工作流与决策边界

2026-11-11，P0-3 更新。在既有组件之上增加单目标工作流；旧 scenecreator generate 与组件调试入口保留。
没有新增模型聊天循环。DSH 的任务模型与 SceneCreator 内部 VLM 分开配置。

## 实际调用路径

~~~text
DSH 原生会话 → context / inspect / workflow_start / workflow_advance
  → workbench.server → Jobs → worker → harness.workflow.Workflow
    → components.operations.run(workflow=True)
CLI: scenecreator workflow start / advance → 同一个 Workflow 控制器
~~~

控制器固定运行感知、证据整理、目标范围、结构作者与自检查、独立结构审核、
几何配方、确定性几何检查、装配检查、构建、外观审核。
Agent 只选择采样、目标身份、结构假设或证据回访，不逐个调度底层算法。

视频先用 PyAV 生成有原始时间标记的 8 格预览，再等待时间窗、采样率、最多 64 帧及参考帧决策。
这里没有相机覆盖率估计。VLM 识别/解释对象，SAM 跟踪可见遮罩，证据组件选择建模视图。
跨轮跟踪 ID 会改变；Agent 必须明确选择本轮对象，并记录与原对象对应的理由，不按类别自动合并。

构建复用 Blender 的 Mesh 生成与 Artisan 的编译和导出，不增加 CAD 后端。
在几何作者前检查装配描述能否被旧后端表达；未知尺度、参考约定或行程不会静默变成默认值。

background=off 是新入口默认值；masks 仅整理背景；reconstruct 顺序执行
COLMAP 相机 → 3DGS → 一张实际相机视图。该图不是全景图，未补全未观察区域。
physics=false 是默认值；显式开启后沿用现有场景组装和 Isaac 协议。
本轮未增加物理协议或机器人动作。
新路径尚未输出旧全流程的 delivery bundle / 最终任务覆盖审核；完成状态表示所选步骤完成。

## Critic 与修复

workflow_critic.py 整理现有自检查、独立结构审核、几何报告、装配报告和外观/物理结果，
没有额外的泛化“评分 Agent”。每条记录保存候选文件树哈希、报告位置、原视频引用、实际问题和建议修订范围。

- 几何检查明确归因到 geometry：固定蓝图，至多重做一次配方并重新审核。
- 结构错误、外观异常或证据歧义：返回决策点，由原生 Agent 修订结构或回访原视频。
- 后端不支持、接口未决或缺少物理协议：保留阻塞，不靠 VLM PASS 放行。
- 编译/运行异常：保留阶段与错误，resume 复用已完成步骤；未实现通用异常自动诊断器。
- 物理失败目前只支持原视频回访或停止，未接通自动物理参数修复。

失败渲染明确为诊断，不加入真实观测列表。
DSH 将有界 JPEG 保存为原生图片附件；附件不可用时返回缺失列表，不宣称模型已看图。
完整报告留在历史产物中，不向每轮提示塞入整个执行 DAG。
以原视频哈希和原始 PTS 有理数时间识别真实帧，比较当前目标实际用于建模的帧，
而非本轮 frame-000001 等编号。没有新帧时不执行结构作者；新帧是否有用仍由 Agent 判断。
重推断融合经明确身份判断选中的旧/新原始观察，按源视频哈希和 PTS 去重，最多 16 帧；失败候选仍只提供诊断摘要。超过预算时由 Agent 用观察编号选择。

## 状态与产物

- 初始运行下 workflow/workflow.json：可变控制状态，含决策版本、轮次、收据及诊断。
- workflow/stages/<operation>-<id>/：不可变阶段产物；完成后记录文件哈希和上游依赖。
- 每次调用的 output/workflow.json 和预览：独立发布的快照，不将可变控制状态作为组件依赖。
- 决策保存原生 session/call/run 身份；同一个 call 复用已有调用。
- 决策携带 revision；旧版本或错误阶段的决策被拒绝。
- 原始输入、配置内容和依赖产物变动会拒绝复用；配置变更需新工作流。
- 原子替换状态，文件锁防止两个 worker 同时推进同一流程。
- 等待决策时 worker 退出并释放槽位，不嵌套排队或后台等待 Agent。
- 崩溃发生在收据保存前时，该阶段重做；不能保证外部模型请求只执行一次。
- 不删除历史结果；尚未实现依赖安全的垃圾回收器。

DSH 默认只注册 context、inspect、workflow_start、workflow_advance。
设置 debug_component_tools=true 才额外注册旧 generate 和全部组件。
前端展示视频/对象画面、简要结构、候选渲染、检查与回访按钮；
按钮向原生 DSH 提交自然语言要求，不在浏览器另建模型会话。
主机和会话作用域都使用原生附件存储传图。现有资源监控保持原实现。

## 使用方式

~~~bash
.venv/bin/scenecreator workflow start \
  --config configs/local-studio.toml \
  --video /absolute/path/source.mp4 \
  --task '只根据视频恢复笔记本的外形与机身/屏幕关系；未知量保留' \
  --output /var/tmp/my-laptop-workflow

.venv/bin/scenecreator workflow advance \
  --output /var/tmp/my-laptop-workflow \
  --decision /absolute/path/decision.json
~~~

视频首次决策：

~~~json
{"action":"sample_video","revision":0,"sampling":{"start_seconds":0,"end_seconds":8.5,"sample_fps":0.85,"max_frames":8,"reference_frame":3}}
~~~

之后读取 allowed_actions、revision 和实际 objects[].object_id。
选择目标需 object_id 与理由；确认/修订结构、停止需要理由；回访需新采样参数和理由。
恢复只传 {"action":"resume"}。

DSH workflow_start 接收 task、input_ids、可选 params；
workflow_advance 的 parent_id 始终是初始 workflow_id，params 为上述决策。
后端在当前任务内解析位置，不接受 Agent 自报目录。
启用新工具配置需正常重新启动 Studio；本轮不打断已有会话。

## 后续边界

P0-2 已接入跨轮原始证据融合、独立构建假设与配方一致性审核。
P0-3 增加结构图与构建树的兼容诊断，以及机械要求的接口类型/数量检查；实际功能面绑定仍由几何作者提供并由原几何审核检查。
依赖安全清理、多目标/资源并发调度、更完整物理修复和旧交付路径的一致性后续处理。


## P0-2：跨轮证据和独立构建假设

回访的 select_target 可带 evidence_refs：从 retained_evidence 及当前 objects[].views 的 ticket
中选取 1–16 个不同原始帧，至少加入一个未使用的新帧。未传时自动去重合并；超过 16 帧则要求明确选择。
工作流使用稳定物体 ID，另存各轮跟踪 ID 与身份判断。
融合结果独立落盘并复制所选原始 frame/mask/rgba/context；视图编号按源帧身份产生，
frame_id 也全局区分，另存原始 frame_id，避免各轮 frame-000000 冲突。
对原始证据和感知均检查哈希与完成状态，视频帧拷贝还要匹配实际解码图像哈希。
历史原始记录不变，不导入任何候选渲染。

propose_build 不修改规范蓝图。它生成独立的 build_hypothesis 阶段，
输入包含 source_blueprint_sha256 和 parameters：
path、value、unit、rationale、evidence_refs。
只允许填补未知的目标尺寸、零件定义尺寸/质量、有界关节 rest/travel。
禁止覆盖已知参数、修改拓扑、接口或单位；relative_width 仍不能直接改成米。
数值均保存为 assumed，机械先验另有 prior:build-* 来源；
引用原图仅表示假设所依赖的上下文，不表示图像测得了这个值。
rest 的来源在独立假设记录中保留，不改写原描述中的 null。

候选先做确定性描述自检查，再进行独立 VLM 结构审核；
审核通过后回到 structure，调用 accept_structure 才继续建模。
原始蓝图和候选的文件哈希、假设计划和未决后端要求都返回 DSH。
失败不自动补齐其他假设，Agent 只能明确修订或停止。

建模前检查有无尺度依据和有界关节参考/行程。配方生成后新增假设一致性审核：
检查盒状几何在参考构型的目标包络、每个同类实例的尺寸、质量和所采纳关节参数。
超出盒状配方的尺寸审核保持 unresolved。该审核不等于网格或物理正确。
失败时最多做一次局部配方修正，并仍须经过原几何和装配门禁；
build 适配器再次计算该审核，不能靠旧报告放行。
构建假设随 candidate 和 assets 记录传递，不把假设资产当作真实参数已恢复。

Workflow 内几何作者的格式/身份校验失败可按实际错误重试一次，保存两次 trace 和 rejected.json。
旧独立几何调用默认不增加重试。普通 revolute 铰链不再被提示为标准紧固件连接；
不凭铰链存在就添加 standard_ref、螺纹或工具啮合。

这轮不支持更改已知假设后局部搜索最优值；要换假设须从规范未知描述重新提出。
未实现相对尺度构建器、跨视频身份匹配、资源调度、旧产物回收及自动功能面推断。

## P0-3：建模前的结构诊断

`construction/assembly_readiness.py` 检查当前 AssemblyBlueprint 能否进入现有后端，
不修改蓝图，不证明真实机械关系正确。

- 分别输出组织父节点与关节父节点，定位具体冲突；检查缺失机械定位、多个入关节、非单根、闭环及未表示的 Mate。
- 检查平面要求引用一个 plane，正交/平行/共面引用两个不同 plane，轴线/间隙要求引用实际圆柱类接口。
- hinge_region/contact_region 保留为合法描述，但不能当作确定的轴孔功能面通过当前审核。
- 当前审核不支持的关系保持阻塞，不删除要求，不自动降为固定连接。
- 尺度、rest、travel 等未知数值仍由已有 backend_requirements 单独列出；结构相容不表示这些参数已经满足。

结构修订的确定性自检查还会比较前一版：已有约束不能省略或改成另一关系类型，
原先引用轴线/铰链区域的要求不能改成全平面要求。发现这种变化返回 needs_revision，
而不是仅依赖独立 VLM 的 PASS。该比较只覆盖标识、类型和已声明的接口类别；
不保证任意自然语言描述语义完全等价，也尚未覆盖所有 Mate/拓扑变更的意图审核。

自检查产物新增 construction_readiness，描述状态与构建相容状态分开。
Workflow 在独立结构审核后保存不可变 structure_audit 阶段及 Critic 记录，
GUI 的结构页展示简短诊断。确认结构进入 construction 后再次复用/检查收据，
若 blocked 则回到 recovery，不调用几何作者。
旧工作流没有收据时在下一次 construction 创建该检查；不追溯重标已完成实验。

revise_structure 仍由原生 Agent 决定。组件作者同时收到 previous、previous_readiness、
原图及已有反馈；可据此输出新的完整蓝图，外层不手改 parent 或补造固定关节。
直接调用组件 geometry 也会重新计算检查并先保存 construction_readiness.json，
阻止绕过 Workflow 消耗几何作者请求。旧 generate 的独立编排保持既有行为，
本轮没有宣称所有历史入口已统一采用该前置检查。

真实视频试验还发现结构作者混淆接口的 plane 与约束的 planar，以及输出无有效轴线的运动关节。
Workflow 的结构作者因此最多按真实 Schema 错误纠正一次；保留原 response、rejected.json
和独立 author-repair-1 记录。仅捕获结构 Schema 校验错误，不对网络失败、语义失败
或缺失证据无限重试；Intake 与原图不因此自动改写。非 Workflow 调用不增加重试。

该检查只判断接口声明能否被当前实现消费；通过后仍需 recipe.interface_bindings、
实际 primitive 几何检查、Blender/Artisan 构建、外观及可选物理审核。
它不提供通用 CAD、闭环机构求解、自动隐藏接口恢复或新的接触协议。

具体代码边界、笔记本/自行车实测、失败与回归结果见
[P0-3 记录](https://github.com/FelixtheFelinae/SceneCreator/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/reports/FIXED_WORKFLOW_P03_20261111.md)。

## P0-4：明确平面、轴线与边缘

AssemblyBlueprint 保持 `/1` 可读格式，Interface.kind 增加 `axis`、`edge`。
`axis_local` 明确表示所属零件坐标下的方向；对 plane 表示法线，未知时保持 null。
axis 是理想关节线或实体圆柱中心线，不表示已恢复的轴孔接触面。
原 hinge_region/contact_region 不自动升级，Mate 的接触要求不因新增 axis 而解除。

`construction/feature_relations.py` 由结构门禁和几何审核共同使用，规定关系语义：

| 关系 | 支持对象 | 检查含义 |
| --- | --- | --- |
| planar | 一个 plane | 实际解析平面 |
| parallel / orthogonal | 两个面、两条线、或线与面 | 面与面比较法线；线与面比较线和面本身，不能拿法线替代面 |
| coplanar | 两个 plane | 法线方向与平面间距 |
| coaxial | 两条 axis / edge / 圆柱中心线 | 方向与位置；有限几何范围内双向检查轴偏差 |
| clearance / revolute / cylindrical | 圆柱接触表面 | 保留原实际轴孔几何、间隙和重叠要求；理想 axis 不能替代 |

`GeometryRecipe.interface_bindings` 新增三个实际来源：

- `joint_axis`：指定已有运动关节 joint，不填 solid；方向和位置直接取构建器实际使用的 axis_child 与 joint_placements。接口必须属于该关节端点。
- `cylinder_axis`：指定已有 cylinder/annulus 实体 solid，取其中心线，不另外编写一条理想几何。
- `box_edge`：指定 box 实体、edge_axis=x/y/z 和另两个坐标的 edge_sides=[min/max,min/max]，定位实际 12 条边之一；随实体旋转和平移。

边不能绑定为面，接触面不能绑定为 joint_axis；重复接口、跨零件绑定、未知关节等保持拒绝。
几何审核另外检查 revolute/cylindrical Mate 的实际轴孔是否对齐导出的理想关节轴，
同时检查角度及有限表面范围内的轴偏差，避免轴孔匹配但绕错误轴转动。

建模前还比较同一零件内明确给出的 axis_local：若关系与已声明方向矛盾，
产生 declared_direction_conflict，描述自检查返回 needs_revision，先于模型自检查。
跨零件没有参考位姿、方向未知时不推算。此检查不证明声明方向符合真实视频，
也不处理任意自然语言描述等价性。

来源校验单独使用 evidence_issues；结构方向矛盾由 selfcheck 保存候选并返回
needs_revision，交给已有 Critic/修订路径，不作为来源错误提前抛弃候选。
未知或伪造的证据、Intake 不一致仍按原规则拒绝。

结构检查版本为 `envcreator.assembly-readiness/2`，几何审核为
`envcreator.geometric-verification/2`。Workflow 的结构/几何审核缓存包含对应版本，
旧收据保留，继续执行时不复用旧规则下的审核结果。构建入口仍重新执行几何审核；
历史 `/1` 反馈可作为哈希匹配的修订线索，不能替代当前审核。

本轮只审核实际解析配方及平移参考树；未验证导出网格、动态构型或物理接触。
外观/运动学近似与接触级构建的分流仍待实现，未决铰链接触配合仍会阻塞当前构建。

代码路径、真实笔记本/自行车输出及回归结果见
[P0-4 记录](https://github.com/FelixtheFelinae/SceneCreator/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/reports/FIXED_WORKFLOW_P04_20261111.md)。

## P0-5：按约束 ID 局部修订

原生 `workflow_advance` 增加 `repair_constraints`：

```json
{"action":"repair_constraints","revision":3,"reason":"修正机身正交关系的绑定", "constraint_ids":["gc_base_orthogonality"]}
```

revision 必须取当前状态，constraint_ids 为原描述中 1–4 个不同的几何约束。
该动作在 structure/recovery 阶段提供，消耗已有修订轮数预算；
旧 Blueprint、没有几何约束或预算耗尽时不提供。
GUI 增加“局部修正关系”，通过原生 DSH 提交自然语言要求，不直接修改描述。

`construction/constraint_repair.py` 使用独立的窄输出格式：
source_blueprint_sha256、replacements、add_interfaces、rationale。
每个 replacement 只能有 id、interface_refs、description；模型没有权限返回或改变
type、part_refs、parameters、basis、evidence_refs。这些字段由程序从原描述复制。
零件、Mate、关节、尺寸、Intake、状态、已有接口等其余内容同样保留。

新增接口仅允许相关零件的 plane/axis/edge，标明 assumed/inferred，
不允许接触硬件、已知位置、参数尺寸、覆盖旧接口或无关接口。
新来源必须已有记录，未知接触不升级为轴孔。已有轴线—边缘要求不能换成其他特征组合。
所选关系须通过相同角色、方向、引用审核；方向关系不能靠省略方向通过。

调用链为固定步骤：
Agent 选择 ID → blueprint_repair → 单次内部 VLM 提案 → 确定性应用检查
→ 原有描述自检查 → 原有独立结构审核 → 结构前检。
局部修订复用已接受 Intake 和原始证据，不再次生成完整描述或运行感知。
更改对象理解、连接拓扑或 Mate 时仍使用 revise_structure / revisit_video。

失败不在组件内无限重试。提案拒绝时保存 proposal/trace、原描述的完整副本、
selfcheck 与 constraint_repair 报告并返回 needs_revision。
若提案已应用但模型自检查拒绝或返回格式/哈希错误，保留未通过审核的候选，
同样返回 needs_revision。网络超时等执行失败传播，由原有阶段恢复机制处理。
Workflow 记录来源、原生调用关联、修订 ID 和 Critic，并在审核失败时返回 recovery。
中断恢复复用已完成的上游阶段；未完成的局部阶段仍可能重新请求模型。

调试组件名为 blueprint_repair，sources 为 blueprint 与 evidence，
params.constraint_ids 和可选 feedback。默认 Agent 工具面仍只有 Workflow 工具。
阶段缓存纳入 constraint-repair 版本；当前版本 `/2`。

这只修复具体关系的表达/绑定，不是几何生成成功或机械正确的证明。
真实案例、失败记录及测试见 [P0-5 记录](https://github.com/FelixtheFelinae/SceneCreator/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/reports/FIXED_WORKFLOW_P05_20261111.md)。

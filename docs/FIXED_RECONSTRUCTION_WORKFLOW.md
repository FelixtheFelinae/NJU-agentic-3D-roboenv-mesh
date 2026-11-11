# 固定重建工作流与决策边界

2026-11-11，P0-2 更新。在既有组件之上增加单目标工作流；旧 envcreator generate 与组件调试入口保留。
没有新增模型聊天循环。DSH 的任务模型与 EnvCreator 内部 VLM 分开配置。

## 实际调用路径

~~~text
DSH 原生会话 → context / inspect / workflow_start / workflow_advance
  → workbench.server → Jobs → worker → harness.workflow.Workflow
    → components.operations.run(workflow=True)
CLI: envcreator workflow start / advance → 同一个 Workflow 控制器
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
.venv/bin/envcreator workflow start \
  --config configs/local-studio.toml \
  --video /absolute/path/source.mp4 \
  --task '只根据视频恢复笔记本的外形与机身/屏幕关系；未知量保留' \
  --output /var/tmp/my-laptop-workflow

.venv/bin/envcreator workflow advance \
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
下一步优先处理结构图与构建树的兼容诊断，以及机械要求到具体功能面的绑定。
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

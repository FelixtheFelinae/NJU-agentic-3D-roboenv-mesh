# 三角盒三螺丝实验：资产与物理修订

本轮目标是让 G1 左手压住边长 30 cm 的等边三角盒，右腕固连电动螺丝刀依次卸下顶盖三颗螺丝。当前先验证实际导出的资产，再把自然语言任务交给新的 Astra 原生 DSH。旧成功录像和旧会话没有改动。

## 方法与入口

`src/roboenv_perception/tools/triangular_box.py` 使用 EnvCreator 的受信任 Blender backend 生成盒体、独立顶盖、三个角部座和开孔，直接装配 USD。本任务由明确尺寸驱动，没有调用 VLM 或伪称经过 Artisan。实验入口尚未注册为通用生成能力。

`fastener.py` 使用有界电机/刀头力矩、摩擦分支和后向欧拉联立求解；`native_screw_runtime.py` 在 PhysX 子步施加力，不写零件位姿。`AssemblyRuntime` 汇总三颗螺丝的反力与工具载荷，传到移动盒体及工具。螺纹内的横移/倾斜由原生 D6 导向约束，退出后解除。

`screwdriving.py` 判断尺寸、相位、轴线、插入深度；补齐单向端面/槽底接触和插入刀头的槽口/槽底两个侧壁作用点。侧壁力按作用点产生力矩，工具承受反向力和力矩。磁吸单独限制为 3 N，不使用磁吸角向对齐。这里仍是替代接触模型，没有求解真实六角面、倒角或螺牙接触。

M4×0.7、8 mm 有效长度对应 11.43 圈、导程角约 3.596°；松脱 2 N·m、运行 0.05 N·m、工具 3 N·m。来源和量级依据见相邻的螺丝审计报告；2 N·m 是假设，装配力矩资料不是松脱实测。螺丝质量/惯量沿用代理值，真实夹紧预载仍未实现。

## 已执行验证与失败

- `tests/test_tool_interaction.py` 与 `tests/test_screw_implicit.py`：19 项通过，含不足力矩、停机、未啮合、直接提拉及力矩搬运守恒对照。
- v6 实际盒体台架：50 N 理想压盖载荷下传递约 2.02 N·m，约 24.8 s 退出螺纹；不压盖时盒体转动并失啮。压盖载荷来自台架，不是机器人左手。
- `full-removal`、`full-removal-v2`、`full-removal-v3`：均未完成拔出，保留原始事件和轨迹。补齐侧壁力矩后依旧停滞，不能只凭 `thread_clear` 宣称拆卸成功。
- 拔出隔离试验：关闭休眠/稳定化或加载前去掉螺纹导向均未解决；只过滤顶盖碰撞则能拔出。过滤碰撞仅用于诊断，不作为最终资产。
- `collision-inspection-v2/cooked.json` 记录实际 PhysX 烘焙凸包。部分细长凸包偏离源网格；按停滞姿态计算，局部可用半径约 1.57 mm，小于 2 mm 螺杆半径，而源网格保留超过 2.12 mm。接触张量未取得有效接触记录，不将空记录宣称为无接触。
- 单独修改最小凸包厚度没有解决。将碰撞点移到局部原点、放大为毫米单位再以 0.001 缩放恢复米后，`extraction-local-scale/pressed.json` 成功拔出 25.49 mm。保留原孔径、碰撞和 3 N 保持力；该测试从已退出螺纹状态初始化，只验证拔出阶段。

证据位于 `outputs/triangular-box-20261030-v6/physics/`，索引见 `bench-index.json`。v7 使用同一局部单位烘焙修复，完整旋拧—拔出复测记录于 `outputs/triangular-box-20261030-v7/physics/full-removal/`。

## DSH 场景接入

首次启动在 USD stage 被替换后访问闭包中的旧 prim 导致失败；现改为加载前复制螺丝路径字符串。第二轮已加载并渲染，但发现停机初始螺丝异常运动，未提交模型任务。新版本使 TGS 每轮外力设置与台架一致；是否解决需以新场景实测为准。

新会话不复制旧程序、历史或总结，模型 Astra，声明上下文 500k。由原生 DSH 决定机器人动作，外层不代为操纵机器人。录像位于 `/var/tmp/envcreator-recordings/felixz/`，10 fps、CRF 21，留 5 GiB 空间保护。

## 仍待完成

完整 v7 物理台架、机器人初态稳定检查，然后执行左手压盒与右腕工具逐颗拆卸。机器人实际按压力、腕部反作用、三颗螺丝的取出及放置都需要独立轨迹证据。当前保留仿真真值观测，不把理想台架或上下文复用称为训练/真机成功。

## 后续修订：v7 初态失败与 v8

v7 全流程达到 `removed`，但停机初态螺丝已偏移约 2.3 mm，因此该次不作为合格完整拆卸。单独 callback 对照复现相同偏移，排除了机器人控制和 callback 时序差异作为该现象的解释。局部坐标缩放不改变 `physxConvexHullCollision:minThickness` 的物理单位；v7 误设 0.01 m，v8 更正为 0.00001 m。v8 callback 台架停机偏移约 0.004 mm、保持连接，随后开始完整 callback 旋拧与无压盖对照。

阶段性的“卡孔”最初只是根据姿态推测，之后由过滤顶盖碰撞和实际烘焙凸包几何定位；休眠、导向和纯最小厚度调整均未解决。所有这些分支保留，不把诊断工况当成最终正常操作证据。


## 原生执行与接触测量修复

v8 初态通过后，完整 callback 台架在解除导向时触发 Carb `Mutex.cpp: Recursion not allowed`。在 PhysX step-event 回调内修改 USD 关节状态不安全；本地后端改为包装每个 `SimulationContext.step`，在原生物理子步之前计算载荷并解除已退出导向，随后调用原有步进。未改机器人策略或直接写入零件位姿。失败日志保留在 v8 `physics/full-removal/runtime.log`；锁外版本为 `full-removal-prestep`。

初版接触张量全零并非无接触。Isaac SimulationContext 会重置 `/physics/disableContactProcessing`；在构造后重新启用，`contact-sensor-enabled-v2` 在 50 N 压盖台架测到约 53 N 盒体对盖的向上法向力。该量不包含摩擦、虚拟螺纹/刀头载荷。机器人交付加入声明式接触视图：顶盖对左手各刚体、顶盖对盒体。尚未接触时零力正常；机器人按压仍须实际运行验证。

接触版包第一次在启动前被完整性检查拒绝：manifest 与 delivery 的 scene ID 不一致。已修正一致性并通过检查；此时未创建模型会话或机器人动作。旧失败仍可从 r5 系统日志追溯。


## v8 当前结果

`physics/full-removal-prestep/evaluation.json` 全部通过：峰值 2.00652 N·m；24.854 s 达到 8.0000 mm、约 11.43 圈螺纹退出；30.297 s 达到 24.0029 mm 分离，最终约 37.73 mm。其余两颗螺丝保持连接，电机关闭、保持力仍有效。采用求解步前包装，不在 PhysX 事件锁内修改 USD。

同版本不压盖对照（6 s）峰值 1.867 N·m，盒体旋转约 2.88°、刀头失啮，未解除螺纹。两工况均为理想台架；这证明了该版本接触与载荷路径的可执行性，不是机器人协作成功。

机器人 r5 初态：三颗螺丝轴向误差约 0.0004–0.0042 mm，均为 clamped，G1 支承站稳。原生 session `dba5894f-9e11-451f-8644-77dcc38e0cec` 使用 `gpt-6-astra`、500k 声明上下文；已提交原自然语言目标，未复制旧程序/总结。默认 privilegedInfo 起初关闭，随后按用户保留理想观测的要求通过原生设置开启并发出设置变更通知；该变化有单独 receipt，不称纯视觉实验。机器人实际结果以本轮历史、接触力、紧固件状态和录像为准，仍在执行。


机器人实际执行截图（双臂接近/左手接触阶段，不是已完成拆卸）：

![G1 与三角盒](images/g1-triangle-wide.jpg)

![顶盖操作细节](images/g1-triangle-detail.jpg)


## 机器人首轮结果：未完成，保留急停

r5 实际建立过左手按压，采样峰值约 13.6 N；右手逐步接近，但未形成有效刀头啮合，三颗螺丝都没有取下。模型第 1 回合因请求 TIMEOUT 结束，同会话恢复后，Astra 读到断电主轴约 92→119 rpm，尝试主动制动后执行 agent-origin emergency_stop，第 2 回合自行结束。没有外层强行解除急停。状态名短暂出现 loosening，但位移只有微米、thread_active 全 true，不能称已拧松。

现场、程序和原生历史：`outputs/g1-astra-triangle-fresh-20261030-r5/`。广角与细节录像已正常封口，位于其 videos 链接目标 `/var/tmp/envcreator-recordings/felixz/g1-astra-triangle-fresh-20261030-r5/`。

原断电行为是完全无摩擦滑行，没有轴承/传动阻力；3.6e-7 kg·m² 转子在 119 rpm 时动能约 2.8e-5 J。仅凭转速不能证明主动电机供能，腕部运动耦合和数值误差仍需区别。新增可选资产参数 `passive_damping_Nm_s_rad` 与 `passive_torque_limit_Nm`，默认 0 保留旧资产语义；使用后向欧拉计算被动阻力，实际施加在转动关节，反扭矩由 articulation 传回外壳。新阻尼值 5e-5 N·m·s/rad、上限 0.01 N·m 为未标定假设。

新增能量耗散与双向停转测试后，相关测试共 21 项通过。v8 `physics/passive-spindle/evaluation.json` 在实际工具、120 rpm 初速、电机关闭的独立台架通过停转和零传扭对照。此项不代表机器人已经完成拆卸；未更改正在急停的冻结实验，也未把修订自动注入旧会话。


收尾：停止旧冻结服务前，断电主轴在后续测量中进一步漂移至约 955 rpm；尚不能仅用无摩擦惯性解释“增长”，仍须排查转子与腕部耦合及数值能量。原生急停没有解除，录像已封口，最终读数在 `final-status-before-stop.json`。新的 `triangle-bundle-v8-bearing` 是候选交付，加入显式被动阻尼参数并通过包完整性检查；联合机器人试验仍未运行，不称修复已在 G1 上验证。


## 单颗拆卸新任务

用户将下一轮收敛为“先左手持续压稳，再用右腕工具完整取下一颗”，多颗交替松半圈/半程暂不实现。新增阻尼联合台架 `full-removal-bearing/evaluation.json` 通过：峰值 2.00651 N·m、最终拔出 37.726 mm、其他两颗连接且电机关闭。

新运行 `outputs/g1-astra-one-screw-bearing-20261030/` 独立于旧急停现场，使用新工具参数、Astra 500k 和启动即启用的理想观测；不复制旧控制程序或总结。已通过原生 DSH 提交自然语言目标，录像仍在 `/var/tmp/envcreator-recordings/felixz/`，机器人任务结果尚待实际执行验证。

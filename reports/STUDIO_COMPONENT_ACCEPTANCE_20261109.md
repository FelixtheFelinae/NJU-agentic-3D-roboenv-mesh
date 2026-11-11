# EnvCreator Studio 组件化实现与验收记录

日期：2026-11-09。本轮在现有工作区实现，未提交或上传资产。后半程按用户要求改用此前的电脑、自行车原视频，由 DSH 原生任务 Agent 只通过 EnvCreator 插件自主决定工具调用；外层只上传输入、发起整体目标、等待整轮结束、记录结果。

## 实现与使用

入口为 `envcreator studio --config configs/local-studio.toml`。DSH 承担唯一任务模型循环，使用独立 `gpt-6.1-sol` 配置；Qwen 及 `VLM_API_KEY` 只用于 EnvCreator 内部 VLM。

主要代码与调用关系见 `docs/ENVCREATOR_STUDIO.md`。内部组件在 `components/catalog.py` 注册，`operations.py` 复用原领域方法，`jobs.py` 管理版本和进程，`presentation.py` 将同一真实产物投影为面向人的内容。

逐阶段查看：

| 阶段 | 已实现内容 | 验收操作与边界 |
| --- | --- | --- |
| 1：DSH 工作台 | 原生会话/模型/历史，EnvCreator 插件，独立凭据，输入上传与认证 | 打开本机 8890，创建任务并上传；工作台输入和原生对话属于同一会话。没有外层聊天循环 |
| 2：组件工具 | SAM/VLM、结构、几何、背景、验证等 26 个内部组件操作；旧完整 Harness 保留 | 检查组件来源和产物；每次执行保留原生调用 ID、源码、输入与输出哈希 |
| 3：人机交互页 | 输入分割、结构、模型、场景、验证五页；原始数据按需展开 | 实际掩码叠加与中文提示；COLMAP 简要状态；3DGS 一张真实视角图，未实现完整全景 |
| 4：修正与局部重跑 | 掩码并集/排除、语义修正、Blueprint/recipe 修订、重渲染、版本比较与过期标记 | 修正生成新目录；旧结果可回看，旧物理结论不自动继承。视频标注只在指定帧生效 |
| 5：执行与 Infra | SSE 续读、队列、进程取消、资源采样、完整 3DGS 检查点恢复 | CPU/GPU 为实际主机采样，区分作业和系统进程。异常退出不自动附着旧 worker |
| 6：实际验证 | 原生 DSH 组件链、原图/视频、CUDA 训练与渲染、领域回归 | 下面分别记录接口通过、模型审核、重建限制、物理未验证和浏览器未验收 |

工作台尚无可用自动化浏览器，不能宣称浏览器排版、分屏拖动、掩码鼠标命中和 WebGL 交互完成视觉验收。React 组件与前端构建检查不替代该验收。

## 已完成的真实资产与实验

### 单图组件与人工修正链路

该实验在用户要求改用电脑/自行车验证前启动。输入是此前桌面视频中的瓶子裁剪：

`outputs/user-video-build-20261022/retry-evidence/objects/asset-0001/views/view-001/context.png`

DSH 会话 `a449d460-c704-47a2-b70f-20f20e71eb0c`，任务 `b43f63939f124eba`。实际完成 13 个版本：输入、VLM 候选、SAM 分割、VLM 语义、证据、人工合并、重新整理证据、范围选择、Blueprint、结构审核、几何、构建、外观审核。

初始识别存在瓶身、整瓶、瓶盖重叠，人工入口将实际掩码合并，保留主体身份和原版本。之后 DSH 通过组件工具继续，未改写原审核判定：

- 结构版本：`07097ae6115244e1`；独立结构审核 `7c81a86d62284d5a` 为 PASS，绑定 Blueprint SHA-256。
- 几何版本：`ecb1189f59f24aa8`。
- 构建版本：`b057aa4046014293`，实际执行 Blender、Artisan 和碰撞处理。
- 外观审核：`23aa0f6e726c4328`，PASS。
- USD SHA-256：`0003da5b2c26afcd23b785b613b1b0300e57814ed4d0fcf49160104009565c9f`。
- 尺寸、质量为明确假设，物理验证 `not_run`，没有机器人实验。

证据：`outputs/studio/component-verification/native-smoke.json`；渲染：`outputs/studio/runs/b057aa4046014293/output/built/visual/preview.png`。这是外观模型与组件链路验证，不是容器密封、推力或抓持验证。

此前旧完整 Harness 运行 `3696ba34f6cb4906` 的范围审核失败仍保留；本次人类明确修正没有将它改成通过。

### 背景与恢复

真实 CUDA 短跑在 `outputs/studio/component-verification/gaussian-smoke/` 执行 4 次迭代，再从第 2 次的完整训练状态恢复到第 4 次，结果位于 `gaussian-resumed/`。包括模型、优化器、随机状态与输入/配置绑定，不是只加载 PLY。

恢复后的点坐标与连续运行一致；所有 PLY 属性最大差异约 `1.23e-7`，不能声称逐字节可复现。此数据集只有 9 个初始背景点，短跑图像不足以验收场景质量。

`overview-video/overview.png` 使用此前 `user-video-masked-20261023/adaptive` 的真实 3DGS 与原相机渲染，可看到桌面及明显伪影。不是新训练结果，也不是生成填补图；没有独立画质通过结论。

COLMAP 对 `cart-background-20261027/background` 的实际重跑产生部分注册，未满足现有“全部请求视图必须注册”约束，运行明确失败，未降级为完成。记录：`outputs/studio/component-verification/colmap-real/run.json`。

总览只支持一个已标定相机视角，`panorama=false`。背景 GS 没有物理碰撞能力。

## 回归检查

实际执行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_components.py tests/test_studio.py tests/test_dsh_model.py -q
# 32 passed, 1 skipped

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  tests/test_pipeline.py tests/test_harness_media.py tests/test_harness_geometry.py \
  tests/test_harness_assembly.py tests/test_harness_run.py tests/test_harness_physics.py \
  tests/test_harness_scope_plan.py tests/test_harness_scope_integration.py \
  tests/test_harness_scene.py tests/test_harness_task_scope.py tests/test_dsh_model.py \
  tests/test_clawspine_integration.py tests/test_background_generation.py -q
# 140 passed, 10 skipped

node --test tests/test_studio_native.mjs integrations/studio/test/client.test.mjs \
  tests/test_clawspine_smoke.mjs tests/test_envcreator_dsh_plugin.mjs
# 5 passed
```

跳过项按原测试的外部运行环境条件保留，不等同验证通过。Ruff、Python 编译、前端构建和 `git diff --check` 通过。系统 ROS pytest 插件与该 Python 环境不兼容，因此显式关闭自动插件加载，未安装或改动系统 ROS。

新增测试重点为实际掩码并集、视频修正后轨迹引用、传递依赖篡改拒绝、Blueprint 修改后的旧物理验证过期、PLY 类型区分、事件断点续读、启动前取消的持久化。测试夹具中的物理 passed 只用于检查过期逻辑，不是实际物理实验。

## 用户追加的长期要求

已单独记录 `docs/ENVCREATOR_INFRA_ROADMAP.md`：面向超过当前 16 GB 显存的高性能硬件、多场景并发、前景/背景不同渲染分支并行、重组件容器封装与测量驱动的资源准入。本轮不实施，也未提高当前并发数。

## 电脑原视频：DSH 自主验证首轮

使用此前原视频 `outputs/user-video-build-20261022/perception/inputs/source.mp4`，SHA-256 为 `08be6039615c0b0807ddad022272f8cbd27204024e3d7cdd02604c516df9f34e`。仅上传原视频并给出完整目标，没有外层逐次工具干预。会话 `89067cbf-7178-4309-ad8b-2f459bdda20c`，任务 `39d1e2a47081438d`。调用轨迹中所有工具均为 `envcreator_`，未使用机器人或外层代码执行工具完成重建。

Agent 整轮正常结束，但资产只完成部分：

| 环节 | 实际结果 |
| --- | --- |
| SAM/VLM 视频感知 | 69 帧，5 条对象轨迹；识别笔记本、瓶子、鼠标、桌面和书本 |
| 对象证据与范围 | 整理多视角证据，将笔记本作为主体；显式范围选择不是独立范围审核 |
| COLMAP | 注册 69 个视图，尺度仍为任意 SfM 单位 |
| 3DGS | 实际 CUDA 训练 3000 次，145,071 个高斯；生成实际相机视角总览 |
| Blueprint | 两次 schema 失败后自主修订，结构审核 PASS；绝对尺寸与隐藏结构不是实测 |
| 模型 | 首次 Artisan 构建报告屏幕/底座分离及铰链几何不连通；修订构建启动失败 |
| 验证/交付 | 没有完整资产、组合场景包或物理通过记录；中间 Blender 网格/渲染保留用于诊断 |

实际分割图：`outputs/studio/runs/2fb28537471e4fce/output/perception/frames/frame-000000/instances.png`。
实际新训练总览：`outputs/studio/runs/65f76f879c7e475b/output/overview/overview.png`。该图可辨认部分桌面环境，但存在明显模糊、漂浮与拉丝伪影，不能作为重建质量验收通过。

![电脑原视频的实际 SAM 分割](/home/felixz/NJU-agentic-3D-roboenv-mesh/outputs/studio/runs/2fb28537471e4fce/output/perception/frames/frame-000000/instances.png)

图：原视频第一帧的实际分割结果，用于观察对象范围，不是物理实验。

首轮发现的适配问题：

1. `80d1790c15b240b9/spec.json` 因重复嵌套依赖达到约 4.3 MB，超过通用展示 JSON 读取器上限，worker 获得空值后启动失败。这是任务传递数据缺陷，不能归咎于 VLM 或几何判定。
2. 独立 `render` 在宿主 Python 导入 Blender 脚本，触发 `ModuleNotFoundError: bpy`。正确入口应把脚本路径交给已有 Blender 后台进程。
3. Native 返回状态和索引，却缺少结构/外观审核的具体内容及无 state.json 时的错误摘要，降低了 Agent 的自修复能力。
4. Blueprint 的数值来源、有限关节 `rest` 约定在传入内部 VLM 的上下文里不够明确。schema 本身维持原约束。
5. GUI 仍可能显示失败构建留下的真实中间网格；需要明确标为中间预览，并在 recipe 修订后标出过期，不能暗示已经交付。

原始记录：`outputs/studio/component-verification/computer-autonomous.json`。本次验收报告只引用可见输出、工具和产物，不展示模型私有推理或加密回放字段。

## 自行车原视频：DSH 自主验证首轮

原视频 `outputs/bicycle-ground-20261026/perception/inputs/source.mp4`，SHA-256 为 `21d3860865d22df0bdd8b756c6885ecba048ad48cbb79b5b2a8e279898d12c97`。会话 `b94c7df4-e1f1-4397-8e78-c97c2a37c56b`，任务 `1347cfad864f4082`。外层同样只提交一次整体目标，所有记录到的工具均为 EnvCreator 插件工具。

实际完成视频感知、对象证据、范围选择和背景准备；Blueprint 因 15 项结构契约错误失败，主要是关节数值没有关联来源，以及将 `Quantity` 字段直接写成 null。随后 DSH 模型请求 TIMEOUT，未正常完成该轮总结。没有生成新的可交付自行车模型，没有继承此前链传动资产的物理成绩。

记录：`outputs/studio/component-verification/bicycle-autonomous.json`。这一失败与电脑的结构契约问题相互印证，适合修正传入内容，不适合放宽 schema。

## 根据真实结果完成的小范围修复

- `components/sources.py`：依赖 DAG 以唯一引用去重、保留实际文件哈希；私有执行记录使用独立读取上限，不沿用浏览器展示限制。已有电脑来源快照去重后约 346 KB；18 层重复依赖测试通过，传递依赖变更仍拒绝。
- `workbench/observations.py`、`server.py`、插件 `host.mjs`：补充面向 Agent 的对象 ID、输出角色/运行 ID、结构和外观审核、编译意见、无 state.json 时的脱敏错误尾部，以及各组件缺失运行环境。浏览器的人类投影继续单独输出。
- `components/operations.py`：给 Blueprint 上下文加入 Quantity、来源与有限关节 rest 约定；增加结构化 `params.feedback` 承接具体修订意见，原任务仍放在 task，不把症状当成新用户目标。
- 独立 render 把 Blender 脚本路径交给既有后台执行器，不在宿主 Python 导入 bpy。没有改变 Blender 几何算法、Artisan 检查或物理阈值。
- `presentation.py`：失败构建的真实网格明确标为中间预览；recipe 修改后其预览标记过期，新的独立渲染优先显示。补齐常见类别、任意 SfM 尺度与审核状态的中文说明。

修复后回归：`tests/test_components.py tests/test_studio.py tests/test_dsh_model.py` 为 **32 passed, 1 skipped**；上述 Node 组合仍为 **5 passed**。新增检查覆盖私有大规格任务、依赖去重、审核意见与日志脱敏、失败预览过期和工作台自然语言提交到原生 DSH。

两段场景各发起一次整轮自主续跑，保留原输入、原目标和前一轮失败；不逐工具干预，不重跑已兼容的感知/背景。

## 电脑自主续跑：修复效果与剩余问题

续跑仍由 DSH 自主决策。没有重新执行感知、SAM 或背景。原结构与背景来源保持不变，几何版本更新为 `e00a7aa48d094d7b`，独立真实渲染版本 `273e712175f948b6`。

- 任务传递恢复：新 build 的 spec 约 1.15 MB；该 worker 正常进入实际编译，不再出现读取规格为空的启动错误。
- 独立 render 实际完成，输出透视、正面、顶部和底部图。渲染路径：`outputs/studio/runs/273e712175f948b6/output/renders/preview.png`。
- Agent 根据编译意见局部修订，屏幕整体浮离及底座内不连通已不再列为当前阻塞；Artisan 当前仍拒绝左右铰链圆柱与屏幕主体的几何不连通。
- 该图是实际模型的中间预览，不是合格物理资产，也没有组合场景交付或物理通过记录。
- GUI 投影正确选择新的 render，并标记旧 assembly 审查过期；没有将旧失败预览当成当前成品。

具体剩余定位：新 recipe 中 screen_panel 的部件局部中心为 `(0,0,0.1)`，左右 hinge_cylinder 的部件局部 Y 仍为 `-0.105 m`，与父关节原点 Y `-0.1075 m` 接近。该偏移被重复带入子部件局部几何，是下一次内部几何调用应明确核对的坐标关系。圆柱目前旋转也需要与 Blueprint 的实际关节轴一起核对。后续应让几何 Agent 修正，不由外层直接移动网格或放宽连通检查。

记录：`outputs/studio/component-verification/computer-revalidation.json`。当前只剩部分缺陷，并不构成完整资产通过。

![电脑自主续跑的真实中间模型渲染](/home/felixz/NJU-agentic-3D-roboenv-mesh/outputs/studio/runs/273e712175f948b6/output/renders/preview.png)

图：Blender 实际渲染的中间模型；铰链检查未通过，不是已验收的物理资产。

## 自行车自主续跑：真实背景完成，结构仍受阻

续跑于北京时间 2026-11-09 04:12:51 至 04:22:37 执行，原生整轮状态为 `completed`。记录到的 14 次工具调用全部属于 EnvCreator；没有外层逐工具修改或控制机器人。复用原感知、对象证据、范围选择与背景准备，没有重做分割。

| 环节 | 新版本与实际结果 |
| --- | --- |
| 第一次结构修订 | `759bc43b9d91461e`：上轮 15 项 schema 错误不再出现，但 `couplings.0` 将未知传动比写成执行耦合，违反有限非零有符号比例约束，失败 |
| 相机恢复 | `5be001193f7d496f`：COLMAP 实际注册 59 个视图；实际米制尺度未知 |
| 第二次结构修订 | `a0d300c657b04939`：intake 将规范 `object_id=studio:run-3517297a7ee2:t0002` 改为 `asset-0002`；身份保护拒绝，未进入 Blueprint 作者与几何阶段 |
| 背景训练 | `f024c518aeec4043`：实际 CUDA 3DGS 训练 3000 次，最终 249,963 个高斯 |
| 背景总览 | `7bb241c889174dfa`：输出实际相机视角渲染；`panorama=false`，没有生成式填补 |
| 模型、物理与交付 | 没有新的自行车独立模型、通过审核的资产或完整场景包；物理未验证，未借用此前链传动资产成绩 |

第二次失败的通用提示为“结构设计改变了目标对象或用途”，实际可定位到身份字段混用，不能据此断言整车用途被改变。后续工具应分别返回规范对象 ID、资产索引 ID 和具体不匹配字段，而不是放松身份保护。

intake 可见输出还把转向、车轮与曲柄的可动结构写入 `observed_motion`。静态结构和多视角证据并不能自动证明已观测到运动；这份提案本轮被拒绝，不能作为新运动识别结论。

![自行车场景的真实 3DGS 背景总览](/home/felixz/NJU-agentic-3D-roboenv-mesh/outputs/studio/runs/7bb241c889174dfa/output/overview/overview.png)

图：实际重建的砖地背景，主体自行车已按前景路由隔离。砖缝大体可辨，边缘模糊，部分区域有漂浮和拉丝伪影；这不是整车模型，也没有画质验收通过。训练使用的留出视图同时用于检查点选择，不能当作独立最终测试。

记录：`outputs/studio/component-verification/bicycle-revalidation.json`。整轮完成表示 Agent 已结束本次尝试，不表示目标资产全部完成。

## 实际资源记录

`outputs/studio/component-verification/resource-observations.json` 保存首轮运行期间的主机采样：电脑场景整机 CPU 峰值约 99.1%、GPU 显存峰值 4007 MB；自行车已执行阶段的 GPU 显存峰值 4069 MB。这些数值不是作业独占用量，且不同阶段资源瓶颈不同，不能由此直接推算安全并发数。后续调度研究应继续记录阶段峰值、初始化和 I/O，再做串行/保守并行对照。本轮没有增加并发。

## 下一次内部工具调用的调整计划

下列是依据本轮实际失败提出的下一次调整，不在本轮继续扩大实现范围：

| 内部调用位置 | 应补充的内容 | 原因与边界 |
| --- | --- | --- |
| geometry → 内部 VLM | 父关节原点、子部件局部坐标、关节轴、各 solid 的局部包围范围；明确不能把父坐标偏移再次写进子 solid | 电脑剩余铰链 Y 偏移与关节原点重复，圆柱当前轴向还需对齐 X 关节轴。由 Agent 基于这些数据求解并重编译，保留连通检查 |
| 几何局部修订 | 将与旧 recipe SHA 匹配的实际前/侧/近景渲染作为诊断图，连同对应 QC 信号交给内部 VLM | 本轮反馈已传入，但修订主要依赖文字和数值。失败构建的图必须标为诊断材料，不能作为已通过资产依赖 |
| blueprint → 内部 VLM | 持续保留 Quantity/rest/来源提示；必要时按具体 schema 路径返回少量错误 | 两段视频均出现格式或来源错误。未知值使用结构化 unknown，不能自动给未观测的关节参数制造测量依据 |
| blueprint intake / identity | 显式区分规范 `object_id`、`asset_id` 与视图 ID；返回预期/实际身份及失败字段 | 自行车第二次续跑误用 `asset-0002`。要求内部输出回填规范身份，保留身份检查 |
| blueprint couplings / motion | 比例未知时仅在假设与未解决项中保留传动关系，不创建可执行固定比耦合；区分结构上可动与视频实际运动 | 自行车第一次续跑只剩未知传动比错误。不得杜撰比例，也不能用多视角静态图替代运动证据 |
| cameras / gaussian | 返回可用视图、保守背景观测范围、真实检查点及已有画质限制；再判断是否值得增加计算 | 电脑 GS 已实际渲染但伪影明显；不能仅凭注册数或增加迭代次数宣称质量改善。对未注册、低覆盖或错误路由先定位 |
| inspect / context | 进一步限制重复 provenance、产物索引和旧错误正文的上下文体积，优先发送来源 ID/哈希、精确 findings 和必要对象数据 | 避免把完整来源树再次塞给任务模型。详细来源仍保存在原始产物中，不删记录。DSH 请求 TIMEOUT 与组件验证失败分开记录 |
| physics / export | 保留每个受支持协议的环境条件、正/负对照与未支持状态 | 当前模型与观测不构成功能通过；不把理想台架、机器人抓持和真机接触混为一项 |

下一轮先在同一电脑/自行车任务中复用兼容结果验证局部调用内容，再考虑增加对象或场景规模。多场景高并行、容器与集群执行另见长期规划，未在本轮实施。

## 本轮验收状态

DSH 插件、组件执行、人类页面的数据投影、版本记录与资源采样已实现，接口与领域回归结果见前文；电脑和自行车已完成各一轮自主首跑与一次自主续跑。电脑有真实中间模型渲染，两段视频均有实际 3DGS 总览。

不能将本轮标为全部验收通过：浏览器视觉与鼠标交互尚未验收；完整全景未实现；电脑铰链连通和自行车结构身份/传动契约仍有阻塞；两者均无完整资产交付或物理功能通过。报告中的模型/背景图片均为展示或诊断产物，不是物理实验成绩。后续优先调整上表中的内部调用内容，不由外层代做几何或跳过检查。

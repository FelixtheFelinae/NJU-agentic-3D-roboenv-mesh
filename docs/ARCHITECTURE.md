# SceneCreator 架构与当前缺口

SceneCreator 从图片、离线视频或已知规格生成 Isaac Sim 资产。媒体入口保留可观察的外形、分件、运动与装配关系，再将这些证据转换成可建模的蓝图。已知规格入口适合生成尺寸、接口或初态明确的变体。输出包括 USD 场景、来源和检查记录，供 RoboHelix 等下游加载和采集机器人数据。

## 入口与调用关系

| 入口 | 调用链与用途 |
| --- | --- |
| `scenecreator workflow start / advance` | `integration/cli.py` → `harness/workflow_cli.py` → `Workflow`。从图片、视频或 `--design` 启动，提交决策或恢复。 |
| `scenecreator studio` | `workbench/cli.py` 启动原生 DSH 和工作台。`integrations/studio/host.mjs` → `workbench/server.py` → `Jobs` → `worker.py`，进入同一个 `Workflow`。 |
| `scenecreator generate` / `scene-harness` | `harness/cli.py` → `harness/run.py`，提供旧多对象批处理，输出 `delivery.json`。 |
| `scenecreator handoff / inspect` | `integration/bundle.py` 及 OpenUSD worker 收集场景依赖，生成或核对便携场景包。 |

## 构建与复审

**媒体路径。** DSH Agent 选择视频采样、对象身份、结构假设和修订方向。内部 VLM 提出对象语义、蓝图与几何配方，并参与结构和外观复审。SAM 分割或跟踪可见区域，证据组件整理原始帧、遮罩和对象视图。DSH 任务模型与内部 VLM 分别配置。

**装配与几何。** `construction/assembly_blueprint.py` 保存独立零件、局部接口、两端连接、初态和来源。`construction/assembly_readiness.py` 检查结构能否构建，`construction/part_rules.py` 解析已注册的配合规则。`harness/geometric_verifier.py` 检查 `GeometryRecipe` 的基元、绑定与间隙，`harness/assembly.py` 审核装配关系。`harness/workflow_critic.py` 汇总报告及哈希，供 Workflow 或 Agent 修订配方、修订结构或回访证据。配方检查的范围由报告列明。

**Blender 与 OpenUSD。** Blender 从配方生成分件网格和诊断预览。配置 `usd_python` 后，`harness/direct_usd.py` 和 `harness/usd_writer.py` 将网格、刚体、碰撞体和关节写入 USD。旧构建与冻结 v9 路径仍可使用 `construction/artisan_backend.py`。媒体 Workflow 在场景组装后调用 `export`，启用物理分支时还要满足所选协议。

**已知规格路径。** `harness/declared_scene.py` 根据输入规格创建同类蓝图和固定配方，先检查两端配合，再调用 Blender 与 OpenUSD。当前有 M3/M4/M5 螺钉盖板和限位铰链两种入口。包内附蓝图、`metadata/assembly_rules.json` 和哈希，物理状态为 `not_run`。螺钉盖板在 Isaac 中需要接收端挂载 `execution/isaac_thread.py` 提供的步前钩子。 [直接生成 Isaac USD](ISAAC_USD_WORKFLOW.md)

**背景分支。** 从感知遮罩整理背景观测，COLMAP 求相机位姿，3DGS 生成背景外观和已标定相机视角的预览。背景和物理分支在 Workflow 中默认关闭。当前背景产物单独记录。 [流程简图](WORKFLOW_OVERVIEW.md) · [背景重建](RECONSTRUCTION_RUNTIME.md)

## 状态和交付边界

`Workflow` 记录决策 revision、原生会话与调用 ID，并使用文件锁、产物哈希和阶段回执支持恢复。`artifacts/` 与 `execution/` 保存组件产物和调用事件，Studio 从这些实际结果呈现状态。

场景包保存 `scene/scene.usda`、依赖文件、`manifest.json` 与交付来源。结构审核、几何审核、外观复审、USD 打包和物理结果分别记录。`completed` 表示本轮选择的步骤完成。资产的可加载性、约束解除和独立零件运动以实际 Isaac 报告为准。`standard_interactions/bench/` 里的冻结协议只覆盖注册的三紧固件台架，所需标准件 USD 和工具资产由使用者提供。 [场景交付](SCENECREATOR_CLAWSPINE.md) · [冻结台架](STANDARD_INTERACTIONS.md)

## 尚待补齐

1. **七类机械结构规格。** 首版覆盖螺纹件、轴、轴承／轴套、插销、导轨／滑块、球头／球窝、旋转卡口。螺钉盖板已有声明式生成代码。其他结构的专用配对、几何及运行时规则需逐类接入。旋转卡口可作为杯身与杯盖等宿主上的配合结构。 [待复审草案](MECHANICAL_ASSET_STANDARD.md)
2. **重建件的局部替换。** 需要将重建证据中的孔、轴、槽和支承区域绑定到实际几何，换件后重新检查对齐、干涉和脱离路径。当前几何审核以参考姿态下的配方基元为主，媒体构建仍受后端投影所支持的连接图限制。
3. **Isaac 运行时与批量验收。** 新螺纹钩子尚未在 RoboHelix 中自动挂载。后续要批量生成不同杆长、孔径、啮合长度及限位角度的资产，保存配合、重置、约束解除和取出后的实际结果。
4. **场景级组合。** 当前固定 Workflow 以单前景目标为主。后续要统一前景资产与 3DGS 背景的米制尺度和坐标，明确支撑与碰撞几何，并保留多个资产的 ID 和装配状态。

以上状态基于源码与既有报告。新直接 USD 路径的螺钉盖板和限位铰链尚待实际生成和 Isaac 验收。此前的环境、实验和检查记录见 [清理记录](HARNESS_CLEANUP.md)。

# Assembly Blueprint P0 实现与验证（2026-11-09）

本轮合并 EnvCreator 结构作者输出与 Artisan 风格 Intake/自检查流程，规范新任务使用的装配描述。
保留 DSH 原生编排、旧记录读端、旧建模/导出基础设施。本轮没有生成新几何或运行物理实验。

## 已实现的方法与代码路径

1. `construction/assembly_blueprint.py`：新增 `AssemblyBlueprint`，包含嵌入 Intake、零件定义/实例、
   接口局部框架、Mate 图、关节、几何要求、状态、来源、候选解释和物理意图。
   机械配合与运动学关节分开表达，未知值与已知零值分开。
2. `construction/assembly_design.py`：统一作者指令、确定性描述检查和绑定文件哈希的 VLM 自检查。
   新状态为 ready / ready_with_assumptions / needs_revision，几何与物理始终单独记录 not_run。
3. `components/operations.py`：原 `blueprint` 工具执行 Intake → 新版作者输出 → 检查。
   新输出只发布一个规范蓝图；已有 `previous` 与 `feedback` 用于修订，历史候选不覆写。
   Intake 可按既有配置有限纠错；未建立新的整体模型聊天循环。
4. `construction/design.py`：完整旧 Harness 仍有 Intake、有限修订、自审核和 handoff，
   现在统一作者输出格式。独立 Intake 文件继续兼容旧 handoff。
5. `harness/run.py` / 组件读端：读取新版与历史旧版；`harness/geometry.py` 在构建前显式检查旧后端可表达性。
   未知 rest/travel、相对尺度、无关节表示的 Mate 不能静默变成旧默认模型。
6. `workbench/presentation.py`：向展示层提供新版部件和接口/配合/状态/自检查数据。
   本轮没有实现新的前端展示控件。

仍保留 `GeometryRecipe`、`AssemblyPlan`、专用连接运行时等结构；本轮未把它们全部收敛为完整 Mechanical IR。
新版字段不自动生成真实螺纹、轴孔、碰撞面或接触参数。

## 自动检查

实际执行：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs \
  tests/test_assembly_blueprint.py tests/test_construction.py \
  tests/test_components.py tests/test_harness_geometry.py tests/test_harness_run.py \
  tests/test_assembly_build_gate.py tests/test_artisan_backend.py \
  tests/test_asset_agents.py tests/test_harness_physics.py tests/test_harness_assembly.py \
  tests/test_standard_export.py tests/test_clawspine_integration.py \
  tests/test_studio.py tests/test_reference_workflow.py
```

结果：**170 passed, 7 skipped，11.56 s**。这不是整个仓库全量测试。
6 项跳过需要显式配置真实 OpenUSD worker，1 项需要真实 ClawSpine launcher 路径。
本轮未把这些跳过项目算作通过。关闭自动加载是为避免本地 ROS pytest 插件的依赖污染，没有安装新依赖。
本轮相关 Python 文件的 Ruff 检查通过。

新增回归覆盖：实际历史笔记本迁移及原文保留、零件定义复用、Mate 图不受旧树结构限制、
错误端点/类型/引用/来源/坐标单位、自检查 REDO 和错误哈希、Intake 身份纠错、
描述允许未知而后端禁止静默默认、离线 CLI 不覆盖旧目录、相对尺度隔离。

## 笔记本：真实 Harness 调用

通过运行中的 Studio `/api/native/invoke` 调用原 `blueprint` 操作，未新建 DSH 模型循环。
内部 VLM 为 `qwen3.7-plus`，读取既有对象证据的 6 个视频视图；未调用网络检索或旧产品尺寸资料。
对象：`studio:run-fd9c47641ac4:t0002`；证据版本 `99fcca40cd0b4bc0`，范围版本 `46821ca0eaaf4292`。
这些调用复用既有视频感知结果，没有重新执行 SAM、COLMAP 或 3DGS。

| 运行 ID | 实际结果 | 观察与调整 |
| --- | --- | --- |
| `6f78689e077740d8` | failed | Intake 将目录标签 asset-0002 当成 object_id；保留原响应，增加明确身份指令和有限纠错 |
| `e39d5fa8694f4430` | completed / ready_with_assumptions | 新格式成功输出；模型自检查 PASS，但外层复核发现世界 Z=0 假设与外部支架不一致、缺少矩形几何要求 |
| `103e002e3d724904` | failed / needs_revision | 自检查拦下世界位姿约束；同时错误要求为描述补充导出用参数，暴露旧提示混淆阶段。原 REDO 保留 |
| `9673a9e6665c447a` | completed / ready_with_assumptions | 移除世界 Z=0 约束；补充本体正交/平面/同轴要求；未知动力学参数继续保留；模型自检查 PASS |

最终规范输出：
`outputs/studio/runs/9673a9e6665c447a/output/blueprint/blueprint.json`。
自检查：同目录 `selfcheck.json`。
文件 SHA256：`487acebdde248d1224d1e564ad4f3d764a0c95c904a0c859832864378acf8ba3`。

输出有 2 个部件实例、2 个候选接口、1 个 revolute Mate、1 个 Joint、3 条几何要求、1 个观测状态。
尺寸、质量、关节范围/参考约定、动力学限制和接口位置为未知；观测状态是静态开盖，未声称看到开合运动。
模型调用原始请求、响应、耗时及来源版本均保留在运行目录。

外层仍发现的语义限制：

- 接口类型是 `hinge_region`，但自由文本仍用“continuous cylindrical interface”措辞，超出已确认的可见结构。
- 正交条款混用了“面内相邻边”和“垂直该面”的表述，尚未绑定具体边/面，不能直接当作可执行几何约束。
- 作者自检查的 PASS 未发现上述全部问题，因此它只能作为描述审核记录。

这些问题没有手工改写到模型产物里，也没有被标成几何已通过。
`geometry_status=not_run`，`physics_status=not_run`；本轮没有新笔记本 Mesh、仿真资产或性能基准结论。

## 历史与 Artisan 格式适配

- 历史笔记本蓝图可迁移，保留原两个部件、关节、未知量和原文。旧 `rest=at_max` 原样保留并标明历史假设，
  不因迁移伪装成新观测。离线模块 CLI 已实际运行，输出于 `/tmp/envcreator-p0-laptop-cli-20261109-final`。
- 实际读取 Artisan 本地 `pipeline/agents/prompts/design/blueprint.template.json` 并迁移木盒模板：
  2 部件、2 候选接口、1 Mate、1 Joint；动力学意图保存在原文，数字全部标为 assumed。
  输出于 `outputs/assembly-p0-artisan-template-20261109/`。未执行 CAD/仿真。
- 官方 `assets/examples/microwave.png` 已检查，可作为后续单图新案例；本轮未声称由它完成重建。

数据集调查、许可证和适用范围见 `docs/ASSEMBLY_TEST_DATA.md`。
已核实 RBO、IKEA ASM、Assembly101 官方网页；Artisan Benchmark 本地缺少数据目录，
远端数据/许可未成功读取。本轮没有下载完整数据集。

## 下一步

最优先增加一个可独立测试的几何约束检查器：从明确的轴孔或铰链描述出发，
绑定具体功能面/轴线，验证同轴与间隙，并以错轴、过盈及正确装配作为对照。
将结构化结果回传既有修订入口；同时细化自由文本里的面内边、面法向及候选接口语义。
无需先增加 Agent 数量或启动完整视频到物理仿真的端到端改造。

汇总运行证据与代码哈希：`outputs/assembly-p0-20261109/validation.json`。

# 标准连接候选套件与连接编译

2026-11-04 实现。当前为 `standard-interaction-kit-v1-candidate.1`，不是物理验收全部通过的版本。原 v9 与 11 月 3 日装配测试的失败继续有效。此处提取、编译和比较已有实现，没有新增模型推理循环，也没有发起模型请求。

## 代码与调用关系

```text
harness/assembly.py::AssemblyPlan（v2）
  └─ harness/connection_compiler.py::compile_plan
       └─ standard_interactions/compiler.py::compile_connections
            ├─ catalog.py / catalog_v1.json：展开标准引用、核对目录及运行时代码哈希
            └─ compiled_assembly.json：实例绑定、冻结参数、版本、来源、协议及哈希

standard_interactions/usd.py::author_connections
  └─ 根据已编译绑定写入原生 mimic 导程、可解除连接与摩擦驱动

standard_interactions/validation.py::run_validation
  └─ bench/validate_tool.py + assembly_protocol.py + evaluate_assembly.py
       └─ 独立 Isaac 台架与冻结验收协议
```

目录分别注册四类引用：

| 引用 | 负责的内容 |
|---|---|
| `M4x0.7_hex_socket@1` | 0.7 mm 导程、螺钉头/杆包络、3.1 mm 内六角槽及材料密度先验；这是已有试件假设，不是尺寸标准认证 |
| `electric_screwdriver_v9@1` | 3 mm 批头、TCP/候选抓持坐标、3 N·m 理想实验电机上限；原资产命令限速 30 rpm |
| `native-mimic-helical-v9@1` | 预载松弛、摩擦、槽口耦合参数、USD 约束配置、辅助刚体以及现有运行时代码哈希 |
| `assembly-v9@1` | 原装配验收协议、正负例清单、50 μm 导程残差等阈值和已知失败；生成清单不等于执行通过 |

`accelerated_bench_120rpm` 是显式台架配置，只展开成 120 rpm 命令限速配置，不自动修改已有工具资产或驱动电机。调用方必须在独立台架中应用并记录它。它不是机器人默认工具配置。

编译器只接收实例关联和装配条件，不接受螺距、力矩或摩擦覆盖字段。未注册规格、错误的关联、重复实例、循环依赖和非有限坐标均失败。目录带固定哈希；实际安装的四份力学代码必须与目录记录一致。修改标准件、运行时或协议需要新版本和明确的锁更新。

当前运行时限制也作为编译约束：世界 +Z、同一螺纹支承体、同一盖板、同一工具、单层夹持构件；USD 构建时支承体初态为单位变换，阶段使用米和 Z-up。任意方向、多个独立支承和通孔螺母尚未迁移。

## Harness 边界

`assembly-hypotheses/2` 的每个连接携带 `standard_ref`、`tool_ref`、`runtime_ref`、`protocol_ref`、`origin_m`、`engagement_m`、`removal_distance_m`，并保留实际零件、证据、备选解释与假设。螺距和工具通道包络从注册表展开，冲突覆盖会被拒绝。

`_build_assets` 在几何审计和导出前写出 `candidate-NNN/assembly/compiled_assembly.json`。检查标准头杆尺寸、连接原点与实际头部表面一致，并冻结选型、相对几何和质量。对象孔位或通道仍可修正；整体平移属于实例绑定。后续配方缩小螺丝、改变其质量或替换标准引用时，记录 `standard_defect.json` 并停止，不能靠再次生成标准件获得通过。

旧 `assembly-hypotheses/1` 保留读取和几何诊断能力，不能据此声称注册了新标准或通过拆卸物理验证。通孔螺母等未注册族不自动套用 M4 模型。

**通用 Harness 目前接入到配置编译与修复限制。** Blender/Artisan 的通用资产导出尚未自动安装这些螺纹约束，也没有自动绑定标准工具；实际 USD 约束写入由 `standard_interactions/usd.py` 提供，专用三角盒构建入口已删除。因此 `_verify` 对带装配语义的资产返回 `unsupported` / `not_run`，不会用普通刚体推拉或铰链测试冒充连接验证。通用资产的标准螺钉外形目前仍是受约束的配方包络；完整槽口网格复用还需导出适配。

`repair_policy.route_physics_failures` 只允许明确标注为 `object_geometry` 或 `instance_binding` 且带报告的失败回到对象修复。`standard_primitive`、`test_harness`、数值诊断和未归因失败保留缺陷。混合失败中含任一不可修复项，也停止自动重建。路由写在任务根目录的 `repair-routing-NN.json`，不修改已封存的物理阶段。现有通用物理报告尚未自动细分失败原因，默认停止，不按错误文本猜测归属。

## 运行资产验证

对已有标准交互包，使用安装了项目依赖和 USD Python bindings 的解释器调用：

```bash
python -m roboenv_perception.standard_interactions.validation \
  --bundle /absolute/path/to/bundle \
  --output /absolute/path/to/new-validation --cases preflight
```

该入口会启动 Linux/NVIDIA Docker 中的 Isaac 物理台架。当前只支持已注册的三紧固件布局；其他布局返回 `unsupported`。`preflight` 只检查启动条件，不能据此声明有动力拆卸通过。验证结果写入 `validation.json`、分项回执、原始观测和验收记录。

`bench/` 中三份文件从旧实验目录原样迁入，保持冻结协议的字节与哈希。旧迁移命令及专用构建脚本已删除，历史实现可从 Git 记录恢复。

## 来源、修订与证据范围

资料沿用并保留 v9 的 `delivery/sources.json`、试件和工具资产、旧构建脚本、实际运行时代码及 11 月 3 日实验报告的路径和哈希；旧构建脚本的原始副本在 `delivery/build_v9.py`。没有把外观参考或材料先验升级为硬件测量。

提取时发现两种数值必须分开：旧构建脚本的 `solver_thread.friction_damping_Nm_s_rad` 为 0.01，最终测试资产的相应元数据为 1000000；实际 USD `rotX` 驱动阻尼仍为 `0.01 × π/180`。`SolverThread.step` 不读取这项元数据阻尼。目录分别保留元数据与实际约束配置，不能凭字段名替换成另一组参数。

本轮没有修改原验收阈值，没有重跑长时 Isaac 台架，也没有修改已有物理失败。固定底座启动残差超限、释放瞬间与释放后回落的同步证据仍待处理。台架滑台、理想腕部固定、磁吸和等效预载不构成机器人真实抓持、左手稳定或真机接触验证。

历史迁移比较与开发检查见[清理前记录](https://github.com/FelixtheFelinae/NJU-agentic-3D-roboenv-mesh/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/STANDARD_INTERACTIONS.md)。

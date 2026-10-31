# 现有前轮资产与 Artisan 流程

审查日期：2026-10-27。以下是已有文件与代码的事实，不是新增实验结果。

## 1. 已做动力学测试的简化自行车

调用链为：

`scripts/blender/bicycle.py → scripts/build_bicycle_asset.py → Artisan compile_for_agent/export → construction/isaac_bicycle_worker.py`

| 位置 | 当前行为 | 对本研究的含义 |
| --- | --- | --- |
| [build_bicycle_asset.py](../../scripts/build_bicycle_asset.py)，约第 28 行 | 5 个刚体：`frame / steering / rear_wheel / front_wheel / crank` | 轴端螺母、垫圈没有独立刚体 |
| 同文件，约第 80 行 | `front_spin` 连接 `steering → front_wheel`，轴沿局部 Y | 它表示轮组转动，不能表示紧固件退出或轮轴从前叉脱离 |
| [bicycle.py](../../scripts/blender/bicycle.py)，约第 412 行 | 轮轴是归属于轮组的圆柱外观网格 | 在所选普通轴端螺母候选中，需要区分被夹持抗转的轮轴与绕轴转动的花鼓/轮组；不能让轮组自转代替螺母旋拧 |
| [isaac_bicycle_worker.py](../../src/roboenv_perception/construction/isaac_bicycle_worker.py)，约第 361 行 | 施力矩、读取前轮旋转响应 | 已有结果只支持该协议中的运动，不支持拆卸 |

详细实验来源为 [自行车与地面重建](../../docs/BICYCLE_GROUND_RECONSTRUCTION.md)。其尺度、质量和摩擦是估计或假设；本研究不继承其数值作为真实轮轴参数。

## 2. 独立爆炸图模型

调用链为：

`scripts/prepare_bicycle_exploded_evidence.py → scripts/blender/bicycle_exploded_study.py → scripts/blender/validate_bicycle_exploded.py`

它以 RGB 观察重新分件，输出 Blender、GLB、零件清单和预设爆炸位移；没有形成 Artisan 可释放连接，也没有拆卸动力学。见 [爆炸图记录](../../docs/BICYCLE_EXPLODED_STUDY.md)。675 个对象是模型数量，不是确认的实车 BOM。

前轮生成位置为 [bicycle_exploded_study.py:build_wheel](../../scripts/blender/bicycle_exploded_study.py)，约第 353–456 行。已有对象可作连接推断候选：

| 模型 ID | 模型内容 | 证据边界 |
| --- | --- | --- |
| `FW01 / FW02` | 轮胎包络、轮圈 | 轮胎内部形式未确认；首轮可保留为轮组外形 |
| `FWS* / FWN*` | 辐条及辐条帽 | 每轮 36 根为数量先验；不纳入首轮可拆件 |
| `FW03` | 花鼓壳和法兰 | 外轮廓有图像依据；内孔和台阶是假设 |
| `FW04` | 候选贯穿轴 | 名称中的“通轴”不代表已经确认现代贯通轴制式；轴肩、直径和轴端螺纹未知 |
| `FW05L/R` | 候选轴承包络 | 不能据此确认散珠、密封轴承或轴套 |
| `FW06L/R` | 候选垫圈 | 数量、止退形式和位置堆叠未确认 |
| `FW07L/R` | 六角外形近似的轴端紧固件 | 普通螺母、防盗端帽及工具接口制式未确认 |
| `FW08` | 轮毂侧盖或制动器外壳 | 未识别内部制动件、力矩反作用连接或线缆脱开接口 |

`L/R` 是现有脚本侧别命名，本研究应从实车朝向和局部坐标重新确认侧别，不凭名字确定旋向。

前叉脚本保留叉冠、内外管和密封边界，但没有可信的叉端槽口、止退唇或开闭口类型描述。前挡泥板支撑延伸到轴端附近，可能影响工具或退出路径；是否与轮轴共享紧固堆叠尚未确认。前刹车线管虽有可见走向，其机械端点和解除步骤未落实。

## 3. 可直接查看的输入证据

原始证据由 [observations.json](../../outputs/bicycle-exploded-study-20261026/delivery/observations.json) 记录，路径和时间戳没有在本轮重建或重标注。

| 证据 | 来源 | 可观察内容 | 仍不可判断 |
| --- | --- | --- | --- |
| [E02.png](../../outputs/bicycle-exploded-study-20261026/delivery/evidence/E02.png) | 原帧 `000000.png`，0 s | 轴端紧固外形、叉端附近、黑色侧盖 | 螺纹、紧固件制式、轴承内部、背侧堆叠 |
| [E06.png](../../outputs/bicycle-exploded-study-20261026/delivery/evidence/E06.png) | 原帧 `000015.png`，5.030811 s | 前叉及轴端区域、挡泥板与支撑关系 | 叉端开口、止退件、完整工具空间和解除路径 |
| [parts.json](../../outputs/bicycle-exploded-study-20261026/delivery/parts.json) | 已有模型清单 | 部件名称、模型分类、假设及爆炸位移 | 真实连接拓扑和仿真约束 |

以上 `outputs/` 链接为本地证据，在只上传代码的 GitHub 仓库中不可用。需要辨认实车结构时，优先补轴端两侧近拍、叉端槽口近拍和相关型号资料；规划可以在这些信息未补齐时使用显式假设，不把假设升级为实车结论。

## 4. 能复用的通用构建链

| 现有入口 | 可复用内容 | 需要新增的部分 |
| --- | --- | --- |
| [construction/evidence.py](../../src/roboenv_perception/construction/evidence.py) | 原始视图、对象身份、图像引用和哈希 | 接口近拍与连接候选的证据关联 |
| [construction/contracts.py](../../src/roboenv_perception/construction/contracts.py) 的 `Part/Joint/Blueprint` | 部件树、运动关节、来源分类 | 多零件紧固堆叠、可释放连接和工具操作语义 |
| [harness/geometry.py](../../src/roboenv_perception/harness/geometry.py) | 类型化 recipe、受信任建模、多视图复审、Artisan 模型生成 | 螺母对边/工具空间、叉端槽口与接口几何契约 |
| [artisan_asset_worker.py](../../src/roboenv_perception/construction/artisan_asset_worker.py) | `compile_for_agent`、装配检查、USD/URDF/MJCF 导出 | 初始几何通过后，另行检查运行时拆装配置 |
| [harness/usd_adapter.py](../../src/roboenv_perception/harness/usd_adapter.py) | 刚体层级展开、保持姿态、重映射关节目标 | 确认拆装零件的独立刚体身份及约束引用 |
| [harness/scene.py](../../src/roboenv_perception/harness/scene.py) | 打包依赖、建立支撑场景 | 前叉夹具、轮组承托及工具接近空间 |
| [harness/physics.py](../../src/roboenv_perception/harness/physics.py) | 加载实际 USD、协议冻结、输入哈希、独立报告核验 | `threaded_fastener` 与 `assembly_release` 独立协议及事件轨迹 |
| [harness/run.py](../../src/roboenv_perception/harness/run.py) | 有限修复、阶段恢复、失败候选保留 | 连接推断、交互编译、拆卸验证阶段 |

当前 `PhysicsProtocol` 只含 rigid、hinge、rotary、coupled；`task_scope.py` 也不支持工具拆卸要求。后续必须增加真实协议及证据绑定，不能把旋松任务改名为 rotary 就沿用通过结论。

## 5. Artisan 能力需分别核对

本机 Artisan 路径为 `/home/felixz/real2sim/R2S2B/sources/Artisan`。只读检查发现：

- `sdk/_core/v0/articulated_object.py:couple` 除同域比例关系外，也提供 `turns=N` 让旋转驱动覆盖有界从动行程的表示入口；跨旋转—直线的数值 `mapping` 被显式拒绝，避免混淆 m/rad 与无量纲比值。
- 本仓库 `Coupling` 当前只允许两个连续旋转关节，因而没有暴露上述能力。
- `sdk/_core/v0/_usd_export.py:_author_mimic_joints` 处理角度/线性尺度，但 `_PHYSX_MIMIC_INSTANCE` 当前固定为 `rotZ`。仅检查到数据导出路径，尚未证明旋转—直线组合在本机 Isaac 中按预期求解。

因此后续应先做最小跨域耦合与约束解除试验，再选择后端。不能从 `m.couple` 可调用或 USD 有属性推断已经具备螺纹仿真。外部 SDK 的版本/源码指纹也应在实现时固定；本轮不修改上游 SDK。

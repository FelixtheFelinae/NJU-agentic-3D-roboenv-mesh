# 装配关系推断与几何复审

当前新增的是紧固关系的候选推断、几何检查和修复反馈。它允许根据视频与机械常识补全隐藏螺母、垫圈或螺纹座，但输出保持为假设；几何通过不等于完成预紧、承载或拆卸验证。

## 代码调用关系

`scene-harness` 沿用原有视频/多图片证据、对象设计、公开资料检索、Blender/Artisan 导出和物理验证。此次新增环节位于 `harness/run.py::_build_assets`：

1. 任务包含紧固、螺丝、拆装等要求时，`assembly.infer_connections` 复用既有 VLM 客户端，对证据图提出连接族候选。记录可见事实、至少两个备选、选择依据、假设和未知项。公开资料仍走原有 `research.py`，资料中的推测不会转成实测值。
2. `geometry.infer_recipe` 接收上述候选与上一轮问题，生成几何和 `AssemblyPlan`。每个连接引用实际零件、螺钉头和杆部 solid，给出从头部到螺母的夹紧层次、反力保持方式和工具接近方向。
3. `assembly_adapter.recipe_parts` 将同一套待渲染几何转换到整装坐标；检查器不另建一份“看似有孔”的替代模型。新增 `annulus` 是贯通环体，供垫圈和简化螺母使用；原 `hollow_cylinder` 带底，不能作为贯通孔。
4. `assembly.audit_assembly` 检查杆件与全场景实体相交、头杆同轴及连续性、夹紧层叠间隙、螺杆可达、有效啮合长度、螺母孔径、头部/垫圈/螺母与周围实体相交，以及直线工具空间。
5. 检查失败时保存 `assembly_validation.json`，问题进入既有 geometry revision 循环，下一轮接收前一配方和具体失败部位；失败候选不进入 Blender/Artisan 导出。检查通过后仍执行原有视觉复核和物理流程。
6. `delivery.json` 附带装配报告路径和哈希；涉及装配的资产保留 `assembly_dynamics_unverified`。`task_scope.py` 不允许用推车、转轴或视觉通过来覆盖紧固、预紧和拆卸要求。

自动检查无需逐项等待人工确认。`assembly_overrides` 是可选修正接口，不是执行前的审批门槛。输入证据不足时继续构造有来源标签的候选；碰撞或不支持的几何不能靠修改声明来跳过。

## 输入与运行

原 `scene-harness` 命令保留。任务文本明确包含“螺丝/紧固/拆装”等内容时启用专项推断；即使任务未显式要求，几何中标识出的螺栓仍须有对应关系。角色标识为 `fastener_head` / `fastener_shaft`，并对常见螺丝命名补充检查，任意命名且未标角色的装饰细节仍可能漏检。

需要修正候选时，在既有命令后追加：

```bash
--assembly-overrides /absolute/path/assembly_overrides.json
```

该 JSON 为 `{ "object_id": { "schema_version": "assembly-hypotheses/1", "connections": [...], "unresolved_relations": [...] } }`。字段以 `AssemblyPlan` 为准；零件和 solid 名必须对应实际几何。覆盖文件参与输入指纹，修改后需新建运行目录。覆盖只替换关系语义，仍接受相同几何检查；改尺寸由下一轮配方修复完成。

记录保存在各对象的 `assembly_reasoning/` 和 `candidate-NNN/assembly/`，包含图像哈希、模型请求/回复、语义、实际检查几何和结果。记录的是证据、候选、决定及结果，便于复核与重放。

## 小车实际运行：2026-10-27

源资产为 `outputs/cart-full-20261027/assembly_spec_v4.json`。复查了顶视、10.0 s 正面和18.9 s 前梁近景，调用 Qwen3.7-plus 做专项复核。第一次返回 JSON schema 而非实例，解析失败；第二次获得三类候选。模型仍把部分梁方向和支架姿态描述错，候选未经直接照搬。原始提议、复核决定和失败记录保留在 `outputs/cart-fasteners-20261027/`。

修复入口 `experiments/cart_full/repair_fasteners.py` 是本车的显式规则适配器，使用通用 Harness 检查器。它不是通用 VLM 自动生成尺寸算法。最终配方为 `candidate-008/assembly_spec.json`：

- 四角梁连接：统一孔阵坐标，把螺丝放在对齐的内侧孔排，避开轮轴支座；补充头下垫圈、背面垫圈和螺母。
- 两个前支架：纠正原本够不到支架的短杆及错误的纵梁连接声明，改为“支架—前横梁”的穿透连接，并形成实际安装孔。
- 前梁两处：采用“长螺栓—横梁—下方黑块—垫圈—螺母”候选，保留螺纹座/嵌件解释。黑块功能、材料和内部仍未知，不能把该候选当作视频真值。
- 支架套接孔与支撑杆的连接仅为几何假设，防转、轴向保持及实际夹紧尚未解决。
- 螺钉质量与轴向惯量新增按几何和假设钢密度计算的先验，尚未编入新的拆卸运行时。

最终8处连接通过当前几何检查。前部4处的工具通道需要先移除上横梁，记录为 `requires_unverified_predecessor`，没有宣称拆卸依赖已完成。背面反扭矩使用“第二把扳手保持螺母”的假设，未验证扳手接触与可达。

`geometry/` 已用 Blender 从同一修正配方生成 OBJ、GLB、视觉 USD、Blender 文件和六张展示图。它们是修正资产的展示，不是新的受力拆卸回放。本轮没有重新运行感知、SAM、Artisan 整车导出或全流程长程任务；已实际执行专项 VLM 推断、规则修复、通用几何检查和 Blender 构建。

```bash
PYTHONPATH=src .venv/bin/python experiments/cart_full/repair_fasteners.py \
  --source outputs/cart-full-20261027/assembly_spec_v4.json \
  --output outputs/cart-fasteners-new

/path/to/blender -b --threads 6 \
  --python experiments/cart_full/blender_build.py -- \
  --spec outputs/cart-fasteners-new/assembly_spec.json \
  --output outputs/cart-fasteners-new/geometry

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  tests/test_harness_assembly.py tests/test_harness_run.py \
  tests/test_harness_geometry.py tests/test_harness_task_scope.py \
  tests/test_harness_scope_integration.py tests/test_cart_full.py
```

整仓回归实际通过229项、跳过4项；24个导出垫圈/螺母的OBJ内外孔径检查通过。上面这组专项测试实际通过58项，覆盖旧式穿板、杆长不足、支架漂空、遗漏连接、虚构证据、错误螺母孔、工具障碍、异常轴向、修改后的哈希，以及 Harness 在导出前拒绝错误装配并传递反馈。修改后的控制流程尚未跑过一次从新视频开始的完整真实模型任务。

## 当前限制与下一步

检查器精确处理轴向为 +Z 的圆柱杆、轴对齐盒体与贯通环体；其他形状作为保守包围盒障碍，无法解析的承压面或退出轴向保持失败。没有验证孔边承压强度、接触面积是否足够、完整工具几何、互相依赖的拆卸顺序、预紧力或承载。螺母外形仍是圆环，尚未模拟扳手六角配合。

后续先把通过几何检查的连接编译为独立被连接件之间的可释放约束，再进行保载、松开和连接解除对照；整车刚体合并只能用于外观/行驶近似，不能充当这一步的验证。国标型号、真实材料和紧固力矩继续作为可替换参数，不凭外观认定。

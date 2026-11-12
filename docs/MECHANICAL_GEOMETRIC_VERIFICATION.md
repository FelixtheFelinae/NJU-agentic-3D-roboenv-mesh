# 装配描述到解析几何检查

2026-11-09：在 AssemblyBlueprint P0 之后新增最小确定性检查器。它检查建模器实际使用的
`GeometryRecipe` 基元与参考装配位姿，不用 VLM 判断数值关系，不单独编造一套孔或轴的数据。

## 实现与接口

- `harness/geometry.py::InterfaceBinding`：将语义接口绑定到实际 solid 的具体表面。
- `harness/geometric_verifier.py::verify_geometry`：只读检查，返回结构化结果。
- `persist_verification`：保存 `geometric_verification.json`；可要求通过，否则抛出携带报告的异常。
- `bound_feedback`：核对报告内容哈希及输入候选哈希，拒绝过期或被修改的反馈。
- `components.catalog` 注册 `geometry_audit`，输入 blueprint、recipe，输出 geometric_verification。
  不需要模型、Blender、Artisan 或物理引擎配置。
- `components.operations`、`harness.geometry.build_recipe`：构建前重新检查新版蓝图，不能通过旧报告状态绕过。
- `harness.run`：几何数值错误返回原局部修订循环；描述缺项或未支持能力停止该局部循环并保留结构化修订请求。

`GeometryRecipe` 新增可选字段，旧配方默认空列表：

```json
{
  "interface_bindings": [
    {"interface_id": "bore", "solid": "bushing", "feature": "cylinder_inner"},
    {"interface_id": "journal", "solid": "journal_solid", "feature": "cylinder_outer"},
    {"interface_id": "housing_top", "solid": "datum", "feature": "box_z_max"}
  ]
}
```

支持 `cylinder_outer`、`cylinder_inner`、`box_x/y/z_min/max`、`box_edge`、`cylinder_axis` 与 `joint_axis`。`joint_axis` 绑定关节轴，`box_edge` 通过 `edge_axis` 和 `edge_sides` 选择盒体边。
内孔必须来自实际 annulus；普通 cylinder、带底 hollow_cylinder 或文字声称的孔不能代替贯通孔。
绑定必须属于接口对应零件，接口/solid 引用与绑定唯一性接受既有配方校验。
暂不支持任意 Mesh 的面拟合、CAD 面标识、螺纹、齿面或候选 hinge_region 的接触验证。

## 检查内容与数值含义

| 要求 | 实际计算 |
| --- | --- |
| coaxial | 有限柱段端点到另一轴线的偏差、无向轴夹角，允许相反轴方向 |
| revolute / cylindrical Mate | 实际轴外表面与孔内表面、径向间隙、有限轴向重叠；不代表关节动力学成立 |
| clearance | 径向间隙上下界及最小插入重叠；考虑偏心和小倾角下的保守椭圆截面外包络 |
| orthogonal / parallel | 两个绑定平面法向的夹角，不把“边垂直面”与“面内边正交”混用 |
| coplanar | 平面法向和法向间距 |
| planar | 当前为实际 box 面的解析平面性，不能推断导出 Mesh 未变形 |

基元的局部 RPY 按建模器的 XYZ 旋转约定变换；参考位姿沿当前建模后端的平移式关节树累加。
组织层级不能自行代替机械定位。未知装配位姿、旧后端无法表达的图和相对尺度不会被当作已知米制位姿。
检查仅覆盖参考姿态，不遍历关节运动范围。

可使用 `GeometricConstraint.parameters` 或 Mate.parameters 指定：

- `angular_tolerance`：rad。
- `axis_tolerance`：m。
- `minimum_radial_clearance` / `maximum_radial_clearance`：m；后者限制名义孔轴半径差。
- `minimum_axial_overlap`：m。

参数继续使用带来源的 `Parameter.quantity`；未知或错误单位标为未解决。
未显式指定时，数值策略默认轴偏差 0.1 mm、角偏差 0.001 rad、最小轴向重叠 0.01 mm、径向不穿透。
这是可配置的**检查策略**，不表示恢复了真实加工公差。
有非零功能间隙要求时必须显式提供，不能只满足“不穿透”。

## 状态、修订与已有流程

`passed`：本次所有已声明要求在支持范围内通过。
`failed`：存在确定的几何偏差。
`unresolved`：至少一项没有足够绑定、描述或实现支持，且未发现确定失败。
没有新版要求时为 `not_applicable`，不能解释为机械正确。
若同时有失败与未解决项，顶层状态为 failed，完整反馈仍保留两者。

每条反馈记录 requirement_id、source、type、status、metrics、message、repair_target、failure_domain。
`repair_target=geometry` 进入配方修订；`blueprint` 要求补充语义或接口；`verifier` 表示当前检查器不支持。
完整旧 Harness 对后两类输出 `repair_request.json` 并停止盲目几何重试，不自动改写 Stage 1。
原生 DSH 可据此选择下一步。

DSH 工具流程：

1. blueprint → blueprint_review → geometry。
2. geometry_audit，读取其报告状态和 feedback；工具进程 completed 不等于检查通过。
3. 修订 geometry 时提供 `sources.previous` 和 `sources.geometric_verification`；系统核对旧蓝图及旧配方哈希。
   修订 blueprint 时同样提供 previous 和报告来源，核对旧蓝图哈希。
4. 新候选重新检查；build 自行重算。已有紧固件 assembly_audit 继续保留，不被新检查器替代。

固定 Workflow 在构建前调用 `geometry_audit` 并汇总 Critic 反馈。Studio 可通过 `debug_component_tools` 显式调用独立组件。 [Studio 工具入口](SCENECREATOR_STUDIO.md)

## 独立复现

将输入换成同一候选输出的蓝图和配方。

```bash
.venv/bin/python -m roboenv_perception.harness.geometric_verifier \
  --blueprint /absolute/path/candidate/blueprint.json \
  --recipe /absolute/path/candidate/recipe.json \
  --output /absolute/path/new-mechanical-fit-check
```

输出目录必须是新目录。退出码 0 仅表示检查 passed，其余为 2。
清理前的人工轴孔夹具保存在 [历史夹具目录](https://github.com/FelixtheFelinae/SceneCreator/tree/43d3c72b3f7699e32eb297816828a5ed9874a232/tests/fixtures/mechanical_geometry)。

## 验证边界

报告绑定蓝图、配方和数值策略，保留内容哈希。普通哈希用于版本一致性，不是第三方签名或安全认证。
`exported_mesh_status=not_run`、`physics_status=not_run`。
不检查全场景碰撞、网格闭合、装配路径、摩擦、强度、预紧、运动稳定或真实参数准确性。
旧历史蓝图维持旧检查路径，不据此获得新的机械验证标签。

下一步可在当前基础上绑定导出 Mesh 的实际面/轴，比较配方与导出结果；不要把基元解析正确直接当成 Mesh 或物理功能通过。

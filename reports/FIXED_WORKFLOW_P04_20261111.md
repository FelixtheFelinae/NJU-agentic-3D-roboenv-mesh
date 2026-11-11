# P0-4：平面、轴线与边缘的表达及审核

日期：2026-11-11。基于 `640d651`，沿用原生 DSH 会话、EnvCreator 组件和固定 Workflow。
本轮新增确定性审核与结构化失败返回，没有新增 Agent 或独立模型聊天循环。

## 已实现的方法与调用关系

| 位置 | 主要变化 | 输入与产物 |
| --- | --- | --- |
| `construction/assembly_blueprint.py` 的 Interface | 增加 axis、edge；axis_local 明确为零件坐标下的方向或平面法线 | 原 AssemblyBlueprint `/1` 结构兼容；未知方向仍为 null |
| `construction/feature_relations.py` | 统一支持的角色组合与角度语义 | 结构前检和几何审核共享规则，避免各自解释线与面 |
| `construction/assembly_readiness.py` 的 analyze_readiness | 允许具体轴线、边缘关系；检查同一零件已声明方向的矛盾 | `/2` readiness 报告；仍不证明几何或物理正确 |
| `construction/assembly_design.py` | 更新作者指导；方向矛盾进入确定性自检查；保留轴/边要求的修订检查 | needs_revision、自检查、原始候选和修订依据 |
| `construction/design.py` 的 validate_blueprint_evidence | 区分来源错误与结构矛盾 | 来源错误仍拒绝；结构矛盾进入可保存的自检查/Critic 路径 |
| `harness/geometry.py` 的 InterfaceBinding / GeometryRecipe.check_against | 新增实际关节轴、圆柱中心线、箱体边缘绑定；检查来源和零件归属 | 绑定实际构建参数，不新增一份独立“正确几何” |
| `harness/geometric_verifier.py` 的 verify_geometry | 线/面关系、双向轴偏差；轴孔与导出关节轴一致性 | 哈希绑定的 `/2` 几何审核及结构化反馈 |
| `harness/workflow.py` 的 stage | 几何审核缓存加入审核器版本 | 继续旧工作流时重新审核，保留历史收据 |

实际调用链：

```text
原始视频证据 → Intake → 装配描述作者
  → 来源/Intake 校验
  → 确定性描述自检查（含同一零件的方向矛盾、已有要求保留）
  → 作者 VLM 自检查 → 独立 VLM 结构审核
  → 版本化结构前检
  → GeometryRecipe 作者 → 实际特征绑定审核
  → 几何审核 / Critic → 原有装配审核 → 原有构建入口再次审核
```

方向矛盾会在作者 VLM 自检查前返回 needs_revision。
未知尺寸、未知方向和未知隐藏硬件本身仍允许作为描述保存。
原来的连接树、尺度及后端能力门禁继续生效。

### 特征绑定与几何关系

- `joint_axis` 引用已有运动关节：位置取 joint_placements 对应的参考关节原点，方向取实际导出的 axis_child。只表示理想运动学轴，不表示实体轴孔。
- `cylinder_axis` 引用已有 cylinder/annulus：方向和位置随实体变换。
- `box_edge` 引用已有 box，edge_axis 选方向，edge_sides 选另两个坐标的正负边；实际定位 12 条边之一，随实体变换。
- 线与面平行表示线沿着面，线与面垂直表示线沿法线。不能把线与法线的关系直接作为线与面的关系。
- 同轴除方向外还检查位置，按有限实体范围双向计算，避免只看中心点或只看一边。
- revolute/cylindrical 配合保留实际轴孔间隙、重叠检查，并检查实体轴孔是否对齐导出的理想关节轴。长实体上的小角度偏差也可能产生不可接受的位置误差。

接口绑定不能把 edge 绑定为面、把 shaft/hole 绑定成 ideal joint_axis，
也不能引用其他零件的实体、未知关节或重复绑定。
hinge_region/contact_region 仍未解决；新增 axis 不会使这些接触配合自动通过。

## 实际案例与实验

记录目录：`/var/tmp/envcreator-workflow-p04-r_xpjsz8/`。
保留实际内部 VLM 请求、响应、自检查、独立审核、重放标记和最终门禁报告。
调用记录中的模型报告为 `qwen3.7-plus`，仅用于 EnvCreator 内部视觉环节。
本轮是外层引导下对共享组件的验证，不是原生 DSH 自主端到端实验。

输入与上一轮一致：

- 笔记本：`/var/tmp/envcreator-workflow-p02-b5FcQU/fused-final/` 的 11 张原始画面；以前一轮规范描述为 previous。
- 自行车前轮：`outputs/bicycle-ground-20261026/evidence/` 中 asset-0003 的 6 张画面。

请求图像 SHA256 与实际输入逐一一致，证据 manifest 校验成功；
未搜索网络规格，未重新运行 SAM、相机恢复、COLMAP 或 3DGS。
没有生成新 Mesh、Blender/Artisan 资产，也没有运行物理仿真。

| 案例 | 实际模型行为 | 最终确定性判断 | 实际几何入口 |
| --- | --- | --- | --- |
| 笔记本首次输出 | 保留原约束 ID，正确引入 ideal hinge axis 和 rear edge；修正组织父节点；VLM 自检查和独立审核均 PASS | needs_revision：把边与法线垂直误写成边与平面垂直；同时保留未决铰链区域配合 | blocked_before_model |
| 笔记本一次真实局部修订 | 接收了具体角度矛盾、原图及新指导，但仍重复同一错误关系 | 确定性方向检查拒绝；没有把模型重复输出当作修复成功 | 未调用几何作者 |
| 自行车前轮 | 输出独立理想轮轴与前叉平面关系；经过一次原有 Schema 纠正；VLM 审核 PASS | 描述可保存，但构建 blocked：父节点冲突、未决接触接口、planar 缺少明确面绑定 | blocked_before_model |

笔记本首次描述具体把 `gc_base_orthogonality` 绑定为顶面与后边，
同时声明顶面法线 Z、后边方向 X，却给出线面 orthogonal。
按所声明的方向，边与顶面应为 parallel。最终检查明确捕获此矛盾。
两次 VLM PASS 不能覆盖这个确定性结论。

真实修订失败还暴露了返回路径问题：结构方向矛盾原本会被通用来源校验提前抛出。
最终代码将来源错误与结构错误分开。随后把这次**真实失败响应原样重放**到共享组件，
未编辑输出 JSON、未再次调用模型，确认它保存 blueprint/selfcheck，正常返回 needs_revision，
不触发后续 VLM 自检查。重放不是新的模型成功试验。

可读结果：`final-result.json`；各案例 `final-selfcheck.json`、
`final-geometry-preflight/construction_readiness.json`；
笔记本 `direction-recheck.json`、`direction-correction/`、`direction-recovery-replay/`。

## 验证范围和限制

`tests/test_functional_features.py` 覆盖角色语义、线面正反例、全部 12 条箱体边的旋转定位、
偏轴/倾斜、轴孔匹配但关节轴错误、长实体小角度偏差、伪造绑定、结构矛盾反馈保存，
以及旧审核缓存不能按新规则复用。新增用例使用明确标注的合成几何假设，不充当视频真值。

执行结果：

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs --tb=short`：**573 passed，14 skipped，36.70 秒**。
- `node --test tests/test_studio_native.mjs tests/test_envcreator_dsh_plugin.mjs tests/test_clawspine_smoke.mjs integrations/studio/test/client.test.mjs`：**7 passed**。
- 变更 Python 文件的 Ruff 与 `git diff --check` 均通过。

跳过项主要是未配置真实 Blender、OpenUSD、ClawSpine 运行入口；另有一个
“SAM 不可用”场景因本机已有 SAM 而不适用。上述结果不代表这些真实集成已验证。
前端代码未变更，本轮没有重做前端构建或浏览器视觉验收。

目前审核对象仍是实际构建用的解析配方与平移参考树。
没有验证导出后的网格、动态全行程、真实尺寸、材料或实际接触功能。
同一零件方向自检查只覆盖显式 axis_local 和明确的方向关系；
不会推算跨零件未知姿态，也不能证明任意文字描述与视频等价。
旧独立 generate 编排仍未统一接入全部前检，不宣称所有历史入口已统一。
本轮没有重启 Studio 或改动活跃会话。

## 后续最值得实施的改动

先增加**按具体约束 ID 修订的局部关系编辑接口**：
输入对应的确定性反馈、原图和有限相关接口，只允许替换该约束及必要接口，
保留未受影响的零件、Mate、未知参数和要求 ID；输出仍走相同确定性检查。
本轮真实修订重复旧关系，说明仅对整份描述追加文字反馈还不足以可靠修复。
该接口不应由外层直接改答案，也不能自动把未决接触配合改成已验证轴孔。

之后再按 physics_intent 明确运动学近似与接触级构建的审核分流；
在分流及对应验证协议落地前，继续保留当前未决铰链接触的阻塞状态。

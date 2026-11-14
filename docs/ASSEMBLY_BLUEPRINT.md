# 装配蓝图与机械接口

`envcreator.assembly-blueprint/1` 是当前装配描述格式。媒体重建和已知规格生成共用零件、接口、配合、状态及来源字段。后端支持范围由结构前检和配合规则检查决定。 [AssemblyBlueprint 源码](../src/roboenv_perception/construction/assembly_blueprint.py)

原生 DSH 结构作者允许根据外观和机械常识生成未见内部，例如未抽动的闭合抽屉的侧装导向和滑动
关节。无需直接内部影像、运动证据或检索来源；用 `origin=prior`、`prior:` ID 和理由记录推断依据，
相应关系标记 `inferred/assumed`，猜测数字标记 `assumed`，不伪称观察或测量。
替代解释可以保留而不全部构建。蓝图接受、几何可构建性及物理功能分别检查。
原生提交与旧内部 VLM 路径的区别见 [Studio 原生结构作者](SCENECREATOR_STUDIO.md#原生结构作者与常识推断)。

## 实际入口与流程

```mermaid
flowchart LR
    D[DSH 原生工具调用] --> O[components.operations / blueprint]
    E[已有对象证据与范围选择] --> O
    O --> I[Intake / AssetBrief]
    I --> B[AssemblyBlueprint]
    B --> C[确定性引用和来源检查]
    C --> S[VLM 作者自检查]
    S --> R[blueprint.json + selfcheck.json]
    R --> P[结构前检与两端配合检查]
    P --> G[GeometryRecipe 与几何审核]
    G --> A[Blender 建模与 USD 导出]
```

`blueprint` 操作继续由 DSH 插件调用；请求仍使用已绑定的 `evidence`、`scope` 版本和
`object_id`，可提供 `previous` 及 `params.feedback` 修订。它没有新增整体任务聊天循环。
内部 VLM 继续使用 SceneCreator 的独立配置，未把 VLM 凭据用于 DSH。

- `components/operations.py`：Intake → 新版作者输出 → 来源检查 → 自检查。
  Intake 身份/引用校验失败时，按既有 `design.max_revisions` 有限重试；每次保留拒绝原因。
  作者/自检查失败则保留候选并返回失败，由既有 DSH 编排决定下一步。
- `construction/design.py`：原完整 Harness 的 Intake、有限修订、自审核及 handoff 保留，
  作者输出同样改为新版格式。
- `harness/run.py`、组件读端：通过 `parse_blueprint` 读取新旧格式。
- `workbench/presentation.py`：提供新版部件、接口、配合、约束、状态和自检查的展示数据。
  本轮未新增前端页面或把完整 JSON 默认呈现给用户。

组件成功时只发布一份规范 `blueprint.json`，Intake 嵌入其中。`selfcheck.json` 是审核记录，
`intake/`、`author/`、`self_review/` 是原始调用追踪。完整旧 Harness 仍保留单独 Intake
文件以兼容已有 handoff；它不是第二份结构蓝图。

## 数据边界

| 字段 | 含义 |
| --- | --- |
| `asset_brief` | 接受的 Intake，必须原样保留 |
| `part_definitions` / `part_instances` | 零件定义与实例；同类实例可以复用定义 |
| `part_definitions[].spec_ref` | 可选的版本化零件规格，例如 M4 螺钉 |
| `interfaces` | 所属零件、候选功能面、局部框架、方向及带单位参数 |
| `mates` | 两接口之间的机械关系图，独立于实例组织层级 |
| `mates[].rule_ref` | 可选的版本化配合规则，已注册规则由 `compile_mates` 解析和检查 |
| `joints` | 运动学关节，通过 `mate_id` 指向相应配合；关节不证明接触功能 |
| `geometric_constraints` | 正交、平面、同轴等待实现的要求；不是已求解结果 |
| `states` | 参考/观测/候选状态、关节位置、零件位姿和活动配合 |
| `evidence` / `hypotheses` | 来源、候选解释、替代解释 |
| `physics_intent` | 预期保真度与未解决问题；不代表物理实现 |
| `migration` | 旧格式原文、来源哈希和迁移警告；新作者输出通常为 null |

尺寸和质量复用既有 `Quantity`，未知为 `value:null, source:unknown`。
接口及状态参数使用 `Parameter(quantity, unit, confidence?, interval?)`；有界区间需明确估计与来源。
`LocalFrame.origin` / `rotation_rpy` 为 null 表示未知，与已知零原点/零旋转不同。
`coordinate_units=relative_width` 仅供相对尺度描述，不能同时声称已知米制尺寸。

来源分 observation / measurement / retrieval / prior / legacy。
机械先验使用 `prior:` 引用，不能冒充图像观测或测量。
实际工具路径进一步核对提供的图像、外部资料和用户测量，声明来源本身不证明来源真实。

确定性校验覆盖重复/悬空引用、组织层级循环、接口归属、单位轴、坐标单位、关节与配合类型及端点、
一配合多关节、理想耦合循环、状态引用与单位、观测/测量来源要求。
它不检查网格形状、正交误差、间隙、碰撞或动力学。

## 自检查结果

- `ready`：描述检查通过且未识别出待补充假设。
- `ready_with_assumptions`：允许继续解释/规划，但保留未知尺寸、接口框架或机械假设。
- `needs_revision`：存在具体描述矛盾、来源冲突或模型 REDO。

记录同时绑定规范内容哈希 `description_sha256` 和工具输出文件哈希 `blueprint_sha256`。
VLM 自检查必须回显当前文件哈希，不能拿旧版本评语通过新版本。
离线迁移只执行确定性检查，记录 `model_selfcheck_status=not_run`。
所有这些结果的 `geometry_status`、`physics_status` 均为 `not_run`。
作者自检查不是独立 QA，也不保证模型能识别全部语义错误。

## 旧格式兼容与迁移

用已有旧版蓝图与配套 Intake 替换下例路径。

```bash
.venv/bin/python -m roboenv_perception.construction.assembly_blueprint \
  --input /absolute/path/legacy-blueprint.json \
  --source-format envcreator \
  --intake /absolute/path/asset-brief.json \
  --output /absolute/path/new-migration
```

输出目录必须不存在，防止覆盖历史。
Artisan 原始数字 Blueprint 使用 `--source-format artisan --object-id <id> --evidence-ref <ref>`；
需由调用者提供身份和来源。导入数字均标为 assumed，关节端点仅生成候选接口，
原始 tasks / dynamics_intent / metadata 保存在迁移原文中。
这些旧动力学意图尚未转换成可执行物理参数；非空 Artisan couplings 当前明确拒绝导入，避免静默丢失符号语义。

`legacy_projection` 给出当前媒体构建支持的有限视图，直接 USD 与旧导出分支都受该前检约束。
相对坐标、无法转成关节的 Mate、未知的有界关节参考约定或行程，以及超出关节树支持范围的结构会阻塞构建。完整蓝图随候选保存。
已知规格螺钉盖板由专用路径构建，可使用等效螺纹规则。 [直接生成 Isaac USD](ISAAC_USD_WORKFLOW.md)

## 构建审核与扩展

解析几何检查已经覆盖配方中的功能面、轴线与部分装配关系，导出网格与物理行为另行验收。待复审的资产规格会沿用现有接口和规则引用。 [机械几何审核](MECHANICAL_GEOMETRIC_VERIFICATION.md) · [七类结构草案](MECHANICAL_ASSET_STANDARD.md)

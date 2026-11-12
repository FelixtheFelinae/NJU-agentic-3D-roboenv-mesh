# 当前流程简图

```text
图片 / 视频 + 资产构建需求        已知设计规格 JSON
             └───────────┬───────────┘
                         ↓
原生 DSH Agent（Studio 或原生插件） / CLI
  ↓
harness.workflow.Workflow
  ├─ 媒体 → 视频采样 → VLM + SAM → 对象与证据 → Agent 选择目标
  │   ├─ AssemblyBlueprint → 独立结构审核 → 结构前检与配合规则
  │   │   → GeometryRecipe → 几何与装配审核 → Blender → OpenUSD
  │   │   → 外观复审 → 场景组装 → 可选 Isaac 物理验证 → export
  │   ├─ Critic 汇总检查 → 修订配方 / 结构 / 回访证据
  │   └─ 可选背景 → masks → COLMAP → 3DGS → 实际相机视图（单独输出）
  └─ 已知规格 → AssemblyBlueprint → 配合规则 → 固定 GeometryRecipe
      → Blender → OpenUSD → 候选场景包与 assembly_rules.json
  ↓
对象资产、阶段产物、来源、假设与实际验证状态
```

## 场景交付

```text
资产组件 + 证据
  → scene 组件
  → export 组件
  → integration.bundle.export_bundle
  → USD 依赖包 + manifest + delivery 来源
  → 下游 RoboHelix / ClawSpine
```

媒体 Workflow 默认单目标，背景与物理默认关闭。配置 `usd_python` 时，普通资产走直接 USD 路径，并在外观复审后组装和打包候选场景。旧构建与冻结 v9 路径继续支持 Artisan。
已知规格输入走 [直接 Isaac USD 路径](ISAAC_USD_WORKFLOW.md)。
已有 `generate` 批处理路径可生成 `delivery.json`，随后使用 `scenecreator handoff`。
组件 export 当前发布为 `unverified`，具体可用范围由绑定报告给出。

## 保留的资产能力

- 通用刚体、铰链、旋转轴与理想固定比传动的已有物理协议。
- 已知规格螺钉盖板与限位铰链的构建代码。新螺纹步前钩子由 Isaac 接收端显式挂载，待物理验收。
- 结构前检、实际几何接口与关系审核、有界约束修订。
- 电动螺丝刀工具生成与运行时功能验证、标准连接编译。
- 原始证据、未知量、建模假设、失败记录和文件哈希。

运行入口与配置见 [固定 Workflow](FIXED_RECONSTRUCTION_WORKFLOW.md)、
[Studio](SCENECREATOR_STUDIO.md)、[场景交付](SCENECREATOR_CLAWSPINE.md)。

# 当前流程简图

```text
输入图片 / 视频 + 资产构建需求
  ↓
原生 DSH Agent（Studio 或原生插件） / CLI
  ↓
harness.workflow.Workflow
  ├─ 预览视频 → Agent 选择采样窗口
  ├─ VLM + SAM → 对象识别、遮罩、多视角证据
  ├─ Agent 选择对象与结构假设
  ├─ AssemblyBlueprint → 自检查 → 独立结构审核
  ├─ 结构前检 → GeometryRecipe
  ├─ 几何关系检查 → 装配检查
  ├─ Blender 分件网格 → OpenUSD 写入（配置 usd_python）
  ├─ 渲染 → VLM 外观复审
  ├─ Critic → 修订结构 / 局部约束 / 回访原始证据
  ├─ 可选物理分支 → 场景组装 → Isaac 资产行为验证
  └─ 可选背景分支 → masks → COLMAP → 3DGS → 实际相机视图
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

Workflow 默认单目标，背景与物理均按配置选择。配置 `usd_python` 时，Workflow 会组装并打包候选场景。
已知规格输入走 [直接 Isaac USD 路径](ISAAC_USD_WORKFLOW.md)。
已有 `generate` 批处理路径可生成 `delivery.json`，随后使用 `scenecreator handoff`。
组件 export 当前发布为 `unverified`，具体可用范围由绑定报告给出。

## 保留的资产能力

- 通用刚体、铰链、旋转轴与理想固定比传动物理协议。
- 结构前检、实际几何接口与关系审核、有界约束修订。
- 电动螺丝刀工具生成与运行时功能验证、标准连接编译。
- 原始证据、未知量、建模假设、失败记录和文件哈希。

运行入口与配置见 [固定 Workflow](FIXED_RECONSTRUCTION_WORKFLOW.md)、
[Studio](SCENECREATOR_STUDIO.md)、[场景交付](SCENECREATOR_CLAWSPINE.md)。

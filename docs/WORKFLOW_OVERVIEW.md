# 仓库工作流与调用边界

更新：2026-10-28。按当前代码组织；不把独立实验当作默认入口已具备的能力。

仓库主体是 EnvCreator，负责从视觉证据构建和验证资产。ClawSpine 是外部机器人运行框架，本仓库用适配层调用它。RoboHelix 的外层进化调度尚未接入。

## 当前工作流树

```text
本仓库：EnvCreator
│
├─ A. 视频／多图 → 场景资产
│  入口：envcreator generate（等同 scene-harness）
│  调度：harness/run.py，固定阶段与有限修订
│  │
│  ├─ 1. 感知
│  │  ├─ VLM：提出物体候选、解释实例与任务语义
│  │  └─ SAM3 或 Grounding DINO + SAM2.1：分割与视频跟踪
│  ├─ 2. 整理多视角证据，确定构建／背景／排除／待确认范围
│  ├─ 3. 可选联网检索：可辨别型号／系列 → 来源与候选事实
│  ├─ 4. VLM：部件、关节与交互蓝图 → 类型化几何配方
│  │  └─ 拆装任务：连接假设、证据与备选 → 几何审查／人工覆盖接口
│  ├─ 5. Blender 生成网格 → Artisan 编译 → USD／URDF／MJCF
│  │  └─ 渲染 → 独立 VLM 复审 → 有限修订几何配方
│  ├─ 6. 组装 USD 场景：近似摆位、支撑面、碰撞与接触材质
│  ├─ 7. Isaac 重载实物文件，执行推碰／铰链／旋转／理想固定比测试
│  │  └─ 失败反馈 → 有限重建 → 沿用原协议复测
│  ├─ 可选背景分支：背景 mask → COLMAP 相机 → 3DGS 外观
│  │  └─ 独立背景产物；不自动成为主场景中的碰撞或机器人交互资产
│  │     可选本地扩图：background_generation_config → SD1.5 Inpainting → 背景图片
│  │     独立入口 envcreator background 可另选固定相机背景板，不增加环境碰撞
│  └─ 8. 原始任务覆盖复核 → delivery.json、资产、场景、报告与未知项
│
├─ B. 专用工具生成与验证
│  入口：envcreator tool / envcreator tool-test
│  │
│  ├─ 当前只注册电动螺丝刀，使用固定配方及显式尺寸／力学假设
│  ├─ 获取 Bosch 官方参考 → Blender／Artisan → 螺丝刀 USD
│  ├─ tools/：独立主轴、电机、六角啮合、简化螺纹与连接状态
│  ├─ Isaac 理想台架：对中、插入、旋松 + 五组不应生效的对照
│  └─ 证据复核 → 准备 G1 抓取／对准场景包；不自动执行机器人
│
├─ C. 对外集成
│  │
│  ├─ 场景交付方向
│  │  └─ delivery → envcreator handoff → USD 依赖包
│  │     → envcreator clawspine → 外部 ClawSpine 原生 G1／Arena 启动器
│  │
│  └─ DSH 调用 EnvCreator 的方向
│     └─ 原生 DSH → integration/tool_plugin.mjs → tool_backend.py
│        ├─ inspect / research：检查绑定资产、读取工具参考资料
│        ├─ build / validate / review：生成候选、实际验证、复核已有证据
│        └─ prepare_g1：准备新场景包
│        当前六个插件接口服务于螺丝刀，不是完整视频生成入口
│
└─ D. 实验、研究及历史记录
   ├─ experiments/cart_full：全车几何、紧固件修订及专用验证
   ├─ experiments/cart_assembly：单枚紧固件的理想旋松／取出实验
   ├─ experiments/cart_background：地板外观与本地场景实验
   ├─ experiments/electric_screwdriver：工具实验与原生 DSH 预检
   ├─ research/front_wheel_assembly：自行车前轮拆装调研与规划
   └─ outputs：输入快照、候选、失败、模型调用、物理轨迹与哈希
```

## 两个运行层的区别

`src/roboenv_perception/harness/run.py` 是 EnvCreator 的阶段运行器。它按代码定义的顺序调用感知、设计、生成和验证，可以依据复审或物理失败做有限次数的修订，并保存恢复状态。这里的模型调用发生在 EnvCreator 内部，不能把它称为 ClawSpine 的原生 DSH。

ClawSpine 的 DSH 则拥有模型会话、插件选择、历史和取消。资产开发会话使用 `envcreator-asset` preset，仅提供六个相关插件工具；机器人会话沿用 ClawSpine 的原生机器人 preset 和控制工具。当前还没有把整个 `envcreator generate` 包装为 DSH 插件，也没有把上述阶段运行器替换成通用 DSH 自主构建循环。

Qwen 和 `VLM_API_KEY` 的后续使用范围是 **EnvCreator 内部的 VLM 模块**。该模块除看图识别外，还参与构建范围、蓝图、几何参数、装配假设和复审。ClawSpine 自身的推理与机器人观察不统一转发给 Qwen。用户计划提供的中转 6.1-sol 用于 DSH，准确模型 ID、协议和独立凭据尚未接入。历史预检确实曾直接用 Qwen 运行 DSH，原记录保留，不改标为新模型实验。

## 代码位置与日常入口

| 位置／入口 | 职责 |
| --- | --- |
| `pipeline.py`、`video_pipeline.py`；`scene-perception run / run-video` | 单独执行图像／视频感知，是生成流程的前端，也可独立使用 |
| `harness/run.py`；`envcreator generate` / `scene-harness` | 完整资产构建阶段调度、断点恢复与有限修订 |
| `construction/` | 对象证据、VLM 设计、Blender／Artisan 及背景重建等可复用模块；包含较早专用实验的 worker |
| `construction/background_generation.py`；`envcreator background` | 可选本地 SD1.5 扩图、来源 mask 和独立进程；可显式指定 USD 与相机加载显示背景 |
| `harness/assembly.py`、`assembly_adapter.py` | 连接语义及几何审查，不自动证明连接受力或拆卸动力学 |
| `harness/tooling.py`、`tools/`；`envcreator tool / tool-test` | 专用电动工具生成、参考收集和功能验证 |
| `integration/`；`envcreator handoff / clawspine / smoke / dsh-tool-profile` | 场景打包、原生启动与任务提交、DSH 插件绑定 |
| `experiments/`、`research/` | 专项实验和调研；文件存在不代表默认入口已自动调用 |
| `configs/`、`tests/`、`docs/`、`outputs/` | 配置、回归检查、说明、逐次运行产物；本地密钥与资产不上传 GitHub |

上述代码位置均相对 `src/roboenv_perception/`，仓库级目录除外。`envcreator` 是统一命令入口；`scene-perception` 和 `scene-harness` 是保留的分层入口，不是三个独立框架。

## 已执行到哪里

- 通用生成链已运行真实视频输入、Blender／Artisan 导出和 Isaac 检查；不同资产和运行版本的结论分别保存。视觉复审发生过误判，输出中的 `verified` 仅代表相应程序检查通过。
- 装配语义和几何审查已进入通用生成流程，支持保留假设、备选及人工修正。受支持的轴向和形状仍有限；通用交付明确保留装配动力学未验证标记。
- 小车单螺钉的简化旋松／取出在独立实验中运行过，采用理想传矩与拉力。电动螺丝刀也已独立生成，并通过六组理想台架实验的 32 项检查；这两条结果不能合并成机器人已经拆卸成功。
- 原生 DSH 已实际调用五个 EnvCreator 插件完成资产预检和新一轮台架验证。G1 有站立／抬手等原生运行记录；当前螺丝刀场景只完成抓取／对准准备。

完整任务“让机器人抓握这把螺丝刀，尝试拧松并取下螺丝”尚未提交执行。除 DSH 模型待接入外，原生机器人环境还缺电机命令、逐物理步执行的啮合／螺纹运行时、动态螺丝及解除连接后的完整取出行为。真实抓持和对准精度也尚未验证。RoboHelix 的外层进化、机器人反馈自动修复资产，以及任意工具生成均未完成。

上述阶段的实验结论沿用各自原始记录。本地背景扩图是后续新增的可选入口，单独保留其模型、图片和加载验证记录。

周边环境采用 [可选的本地背景外观补全](GENERATIVE_ENVIRONMENT_PLAN.md) 的范围：默认关闭，只生成背景图片，不新增房间碰撞或 GS 训练。入口及使用方式见 [本地背景扩图](LOCAL_BACKGROUND.md)；普通透视扩图仍不具备全景或近景视差。
论文和实现的具体能力、限制及本机可部署范围见 [生成式环境调研](GENERATIVE_ENVIRONMENT_RESEARCH_20261028.md)。

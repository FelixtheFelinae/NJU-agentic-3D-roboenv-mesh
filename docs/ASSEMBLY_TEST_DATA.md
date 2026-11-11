# 装配重建测试素材调查（2026-11-09）

后续更新：用户已授权完整下载，RBO 完整 CSV/图像版正在系统分区下载；
具体目录、校验方法、状态及其他数据集网络阻碍见 [DATASET_DOWNLOADS.md](DATASET_DOWNLOADS.md)。
下文“未下载”描述的是最初 P0 调查时点，不代表后续下载状态。

本次阅读本地 Artisan 源码和可访问的官方数据集网页，没有下载完整数据集。
网页快照保留在本地 `research/assembly-p0-20261109/`。未能访问的服务不视为已验证。

## Artisan 附带什么

本地源码：`/home/felixz/real2sim/R2S2B/sources/Artisan`。
README 指向[官方仓库](https://github.com/physicalvision/Artisan)和
[项目页](https://physicalvision.github.io/artisan)。本地目录没有 Git 元数据，
因此本次证据不能说明它是否与远端最新提交完全一致。

- 实际附带输入图：`assets/examples/microwave.png`。
  README 快速示例以“左铰链微波炉门 + 可转玻璃盘”文字和该图作为输入，运行 `./run.sh run ... --image ...`。
  适合测部件分解、门与转盘关节，以及“图像可见/文本先验/未知”区分。
- 本地示例媒体目录未找到重建输入视频；teaser/wordmark 不是重建测试视频。
- 附带 `pipeline/agents/prompts/design/blueprint.template.json`，为木盒与铰链盖板的描述模板。
  本轮已实际迁移它并检查：2 部件、2 候选接口、1 Mate、1 Joint；原 dynamics_intent 保留，数字标为假设。
  这是格式适配测试，不是用微波炉图片完成重建。
- [Artisan-Benchmark](https://huggingface.co/datasets/mastercai/Artisan-Benchmark)
  需单独下载；本地不存在 `benchmark/data/`。本地 benchmark README 说明：
  ArtVIP/LightWheel 为文字 + 一张打开状态图，Stability 为纯文字，ABO-Mass 为文字 + 参考图。
  脚本区分外观/几何/材料、碰撞、稳定和质量评估。
  本次 Hugging Face 端点未成功取得数据或许可证，不能把仓库代码许可证等同于数据许可证。

## 可访问并核实的候选数据集

| 数据集 | 官方页面说明 | 最适合当前哪一步 | 使用限制 |
| --- | --- | --- | --- |
| [RBO Articulated Objects and Interactions](https://tu-rbo.github.io/articulated-objects/) | 358 段 RGB-D、14 类可动物体；刚体位姿、关节状态、形状模型；78 段有接触力/力矩 | 先用单路 RGB 测 E1 结构/运动推断，再做 E4；深度及真值只用于离线评估 | 不是螺纹/公差真值；官方页标 CC BY 4.0 |
| [IKEA ASM](https://ikeaasm.github.io/) | 371 个家具装配样本；3 路 RGB、1 深度流，动作、人体姿态、部件分割/追踪及外参 | 零件身份保持、装配状态变化及遮挡；适合 Stage 1 | 家具装配不提供精密轴孔/螺纹功能真值；数据 CC BY-NC 4.0，代码 MIT |
| [Assembly101](https://assembly-101.github.io/) | 101 种儿童玩具的多视角装拆；官方页有装配示例视频；2026-05 公告迁往 Hugging Face | 多帧零件发现、身份、操作序列与状态变化 | 数据 CC BY-NC 4.0；不是精确接口尺寸/接触真值；本次只读取项目页，未下载视频/标注 |

原始数据可能含多视角、深度、动捕或力信号。若实验声明单目 RGB 输入，须固定一条 RGB 流，
将其余模态隔离到评估侧，不能把真值回灌给推断阶段。

## 建议顺序

1. 继续用既有笔记本视频验证描述稳定性；使用官方微波炉图片补一个不同关节组合的单图案例。
2. 从 RBO 选一个简单铰链序列，保存单路 RGB 的输入清单与真值隔离规则，建立 E1 对照。
3. E2/E3 先用人工明确的轴孔/铰链最小装配描述与故障注入资产验证；这些视频数据不能替代机械接口真值。
4. 部件跨帧与状态变化链路完善后，再引入 IKEA ASM 或 Assembly101。

本次没有运行上述公共数据集基准，也未声称取得端到端成功率。

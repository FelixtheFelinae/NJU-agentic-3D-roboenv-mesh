# EnvCreator

EnvCreator 是当前视觉重建与交互资产生成框架的暂定名称，仓库名及 Python 模块名保持兼容。
ClawSpine 的原生机器人能力通过独立适配层接入，见 [接入方式与实际验证](docs/ENVCREATOR_CLAWSPINE.md)。
当前各入口、模型边界和已接通范围见 [仓库工作流树状图](docs/WORKFLOW_OVERVIEW.md)。
可选的本地背景扩图入口为 `envcreator background`，沿用现有素材与 mask，生成外观图片；安装、固定相机加载及范围见 [本地背景扩图](docs/LOCAL_BACKGROUND.md)。

A trial in NJU-@SOARLAB

这是一个Real2Sim的尝试，工作开始于10月11日，期望最后能通过视频和图像流完成一个物理仿真场景的重构

DATA is All you Need！

## 当前实现：单图、离线视频与基线评估

固定流程：图片 → VLM 候选清单 → SAM 3 分割 → 规则整理 → VLM 实例语义 → 用途分流 → 对象包、背景包、检查报告。

固定流水线已支持单张图片、离线视频和独立标注评估。正式运行统一使用 **Linux + NVIDIA CUDA**；Mac 使用你现有的 SSH 方式连接服务器，不提供单独启动器。自动构建入口见下方 Harness；跨独立输入的可靠身份融合及机器人 Benchmark 仍未完成。

### 从描述和视频／多图自动构建

新增 `scene-harness`：任务描述和媒体输入 → VLM/SAM → 对象证据与可选公开资料检索
→ 部件／关节蓝图 → Blender 网格与 Artisan 导出 → 独立视觉审查与有限重建
→ 场景组装 → Isaac 受力测试 → 原始任务覆盖审查与交付。
所有候选、失败、来源和阶段产物均记录哈希；支持相同输入的断点恢复。

```bash
.venv/bin/python -m roboenv_perception.harness.cli --help
```

配置、实际入口、运行命令和验证记录见 [Agentic Harness](docs/AGENTIC_HARNESS.md)。
`envcreator generate` 复用同一生成入口；`envcreator handoff` 打包 USD 场景，
`envcreator clawspine` 调用外部 ClawSpine 的原生 G1 启动器。当前先复用机器人运行能力，
RoboHelix 的进化调度暂不接入。
`envcreator tool electric-screwdriver` 复用 Blender/Artisan 生成直柄电动螺丝刀，
收集官方参考并保留建模假设，带抓持/TCP 坐标和独立主轴速度驱动；传入 `--isaac-python` 后执行功能验证。
`envcreator tool-test` 可验证已有工具：理想导轨对准、六角啮合、旋松及偏心/倾斜/间隙/错规格/停机对照。
已有工具通过理想台架的 32 项检查，旋转约 5 圈、退出约 4.10 mm；G1 抓取与对准场景已打包，机器人实验尚未运行。
后续 Agent 工具流程沿用原生 DSH：`envcreator dsh-tool-profile` 生成插件配置，通过 ClawSpine 既有的
`--native-extra-patch` 加载；资料、生成、验证和交付工具均保存调用前后记录，不另建模型循环。
生成命令、物理实验和修复记录见 [电动螺丝刀试制](experiments/electric_screwdriver/README.md)。
通用 worker 已在真实 Isaac 中验证刚体推碰、被动铰链和理想固定比传动；
完整图片重建的交付仍逐次接受视觉与物理审查，失败或证据不足保留 `unverified`。
当前视觉复审已出现误判；`verified` 表示程序门禁通过，仍需结合实际渲染和实验范围判断。
这次运行的成功、失败及复审反例见 [运行记录](docs/AGENTIC_HARNESS.md)。

### CUDA 服务器准备

以下命令全部在服务器的项目目录执行。需要 NVIDIA 驱动、Python 3.12、官方 SAM 3 源码及本地 checkpoint：

```bash
bash scripts/bootstrap_cuda.sh /path/to/official-sam3-checkout
.venv/bin/scene-perception doctor --require-cuda
```

准备脚本不下载 checkpoint，也不配置 API。没有 CUDA 时正式推理/评估会报错，不自动转 CPU 或 MPS。所有输出目录应使用新路径，已有结果不会被覆盖。

当前开发主机的环境位置、固定依赖和日常使用方式见 [本机开发环境](docs/HOST_SETUP.md)。
已获批 SAM3 后，在其他电脑下载并迁移权重的完整命令见 [SAM3 权重迁移](docs/SAM3_TRANSFER.md)。

### 测试素材与真实推理

图片放到 `test_media/images/`，视频放到 `test_media/videos/`。这是主仓库内的目录，未创建嵌套 Git 仓库；素材默认忽略提交，见 [素材说明](test_media/README.md)。

复制 [配置模板](configs/api.example.toml) 为 `configs/local.toml`，之后填写完整请求 URL、模型名和 SAM checkpoint。没有默认供应商、模型或隐式测试后端。

```bash
cp configs/api.example.toml configs/local.toml
# 编辑 configs/local.toml
export VLM_API_KEY='在你的终端填写密钥'
.venv/bin/scene-perception doctor
.venv/bin/scene-perception run \
  --image test_media/images/scene.jpg \
  --config configs/local.toml \
  --scene-id desk-001 \
  --output outputs/desk-001
```

可选 `--task '把杯子放进盒子'`；不提供任务时保留潜在用途。API 支持 `chat_completions`、`responses` 和自定义 JSON HTTP 模板，详见 [API 文档](docs/API.md)及[通用模板](configs/generic.example.toml)。

可选替代后端已支持 **Grounding DINO + SAM 2.1**，同时覆盖单图与离线视频。配置、权重准备及限制见 [替代后端说明](docs/GROUNDED_SAM2.md)和[配置模板](configs/grounded-sam2.example.toml)。默认仍使用 SAM 3，已有本地配置不会自动切换。

真实 SAM 后端使用[官方 SAM 3](https://github.com/facebookresearch/sam3)。`sam.checkpoint` 指向本地 SAM 3 checkpoint；视频也使用官方 SAM 3 视频接口。当前没有实现 SAM 3.1 Multiplex 专用适配，不能直接假设可更换为任意 3.1 checkpoint。

当前测试视频已增加三物体物理资产构建实验：独立 Coding/Review 上下文、Artisan SDK 编译、Isaac Lab 接触与被动铰链验证，见 [视频物理资产实验](docs/VIDEO_ASSETS.md)。

已增加可选的 [Blender 曲面建模后端](docs/BLENDER_BACKEND.md)：后台生成网格与多角度预览，
复用 Artisan/Isaac 导出和接触验证；首个试点为白桌视频中的头戴耳机。

### 离线视频与独立评估

```bash
.venv/bin/scene-perception run-video \
  --video test_media/videos/scene.mp4 \
  --config configs/local.toml \
  --scene-id desk-video \
  --output outputs/desk-video-001
.venv/bin/scene-perception validate outputs/desk-video-001

# 仅复制原图并生成空标注模板，不会把预测 mask 当作真值
.venv/bin/scene-perception prepare-truth \
  --bundle outputs/desk-video-001 \
  --output test_media/annotations/desk-video
# 独立完成 truth.json 与可见 mask 标注，然后冻结数据集
.venv/bin/scene-perception freeze \
  --manifest test_media/annotations/desk-video/dataset.json \
  --output test_media/annotations/desk-video/dataset.lock.json
# 按 docs/STEP23.md 填写 outputs/predictions.json 的样本 → 结果目录映射
.venv/bin/scene-perception evaluate \
  --manifest test_media/annotations/desk-video/dataset.json \
  --lock test_media/annotations/desk-video/dataset.lock.json \
  --predictions outputs/predictions.json \
  --output outputs/evaluation-001
```

视频保留原始 PTS、处理帧映射、逐帧对象/背景包和片段内轨迹。后续新出现而缺少参考语义的物体保持待确认。评估计算召回率、漏检记零的 IoU、背景泄漏、ID 切换及显式遮挡后的恢复率；失败样本保留在分母中。详见 [第二、三步说明](docs/STEP23.md)。

### 输出

```text
outputs/desk-001/
  inputs/           # 原文件副本、EXIF 归一化图片与来源记录
  stages/           # 模型输入/响应、原始 mask、规则处理结果
  objects/          # 每个对象的 mask、RGBA/上下文裁剪、坐标映射、语义
  background/       # mesh / background / transient / unknown 四类用途 mask
  previews/         # 实例编号图、用途分流图
  scene.json        # 场景入口
  quality.json      # 程序检查、未知项；真实准确率留空
  run.json          # 阶段状态、耗时、模型是否执行、版本
  artifacts.json    # 最终工件哈希索引
  report.md
```

任务角色、视觉路线、物理角色分别保存。桌面可以进入 3DGS 外观分支，同时保留支撑面需求。背景不取前景补集，未分配像素保持未知。物理参数、相机、深度、尺度和不可见几何由后续模块处理。

- `run.status=completed`：程序执行完成。
- `quality.checks_status=pass/review/fail`：坐标、引用、用途冲突等规则检查结果。
- `ground_truth_metrics.status=not_evaluated`：未使用独立标注，召回率、IoU、泄漏率为 `null`。
- `reconstruction_ready=false`：本步骤未完成三维重建条件验证。

`run` 退出码：`0` 完成（仍可能待复核）；`1` 配置/执行错误；`2` 完成但关键检查失败。`validate` 返回 `0` 只代表工件、坐标与分区检查通过。

### 开发检查

```bash
unset PYTHONPATH PYTHONHOME # 避免继承主机 ROS 等其他 Python 环境的包路径
.venv/bin/pytest -q
.venv/bin/ruff check src tests
.venv/bin/scene-perception schemas --output outputs/schemas
.venv/bin/scene-perception demo-video --output outputs/synthetic-check
```

`demo` / `demo-video` 只使用合成图像和回放输出，供契约/指标测试；即使指标为 1，也不会生成正式通过结论。它们不是 CUDA 部署验证。正式环境验证记录见 [VALIDATION](docs/VALIDATION.md)，单图接口见 [STEP1](docs/STEP1.md)。

### 对象设计与背景构建（A–C）

现有 VLM + SAM3 后新增对象多视角证据、理解/蓝图/哈希自检与 Coding 交接、
背景监督 mask、统一桌面纹理融合/补全、COLMAP 相机和区域加权 3DGS 入口。
命令、配置模板、工件契约与验证边界见 [构建说明](docs/CONSTRUCTION.md)。
新的自动构建与复审循环见 [Agentic Harness](docs/AGENTIC_HARNESS.md)；
RoboHelix 接入和机器人任务验收尚未完成。

## 许可证

本项目的一手代码采用 [GNU General Public License v3.0 only](LICENSE)
(`GPL-3.0-only`)。第三方软件、模型权重、数据集、机器人资产和仿真器保留各自
的许可证与使用条款。

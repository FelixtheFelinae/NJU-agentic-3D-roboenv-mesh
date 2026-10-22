# NJU-agentic-3D-roboenv-mesh

A trial in NJU-@SOARLAB

这是一个Real2Sim的尝试，工作开始于10月11日，期望最后能通过视频和图像流完成一个物理仿真场景的重构

DATA is All you Need！

## 当前实现：单图、离线视频与基线评估

固定流程：图片 → VLM 候选清单 → SAM 3 分割 → 规则整理 → VLM 实例语义 → 用途分流 → 对象包、背景包、检查报告。

固定流水线已支持单张图片、离线视频和独立标注评估。正式运行统一使用 **Linux + NVIDIA CUDA**；Mac 使用你现有的 SSH 方式连接服务器，不提供单独启动器。Agent、跨独立输入身份融合、mesh/3DGS 生成和最终机器人 Benchmark 留到后续阶段。

### CUDA 服务器准备

以下命令全部在服务器的项目目录执行。需要 NVIDIA 驱动、Python 3.12、官方 SAM 3 源码及本地 checkpoint：

```bash
bash scripts/bootstrap_cuda.sh /path/to/official-sam3-checkout
.venv/bin/scene-perception doctor --require-cuda
```

准备脚本不下载 checkpoint，也不配置 API。没有 CUDA 时正式推理/评估会报错，不自动转 CPU 或 MPS。所有输出目录应使用新路径，已有结果不会被覆盖。

当前开发主机的环境位置、固定依赖和日常使用方式见 [本机开发环境](docs/HOST_SETUP.md)。

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

真实 SAM 后端使用[官方 SAM 3](https://github.com/facebookresearch/sam3)。`sam.checkpoint` 指向本地 SAM 3 checkpoint；视频也使用官方 SAM 3 视频接口。当前没有实现 SAM 3.1 Multiplex 专用适配，不能直接假设可更换为任意 3.1 checkpoint。

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

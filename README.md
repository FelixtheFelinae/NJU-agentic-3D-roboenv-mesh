# NJU-agentic-3D-roboenv-mesh

NJU-SOARLAB 的 Real2Sim 实验：从图像或离线视频提取物体与背景，构建近似外形、碰撞和关节，再在 Isaac 中检查受力响应。

## 从哪里开始

| 你要做什么 | 入口 | 说明 |
| --- | --- | --- |
| 自动构建与复审 | `scene-harness` | 感知 → 证据 → 任务范围 → 蓝图 → Blender / Artisan → 视觉复审 → 场景 → Isaac → 交付；实验性 |
| 只做感知或分阶段构建 | `scene-perception` | 图像／视频分割、对象证据、蓝图、背景纹理、COLMAP、3DGS、独立标注评估 |
| 复现已有物体实验 | `roboenv_perception.experiments` | 木桌、白桌、自行车、固定基座 G1 的专用实现，与通用 Harness 分开 |

[文档索引](docs/README.md) · [代码结构与调用关系](docs/ARCHITECTURE.md) · [已有实验](docs/EXPERIMENTS.md) · [历史记录](docs/history/README.md)

## 运行 Harness

正式推理和物理实验在 **Linux + NVIDIA** 主机执行。项目环境、SAM 权重、VLM 服务、Artisan、Blender、Isaac 分别按配置准备；源码仓库不附带这些运行时。

```bash
# 在已准备好的项目环境中安装／更新命令行入口
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/scene-harness --help

.venv/bin/scene-harness \
  --task '重建桌上的杯子，验证水平推碰' \
  --video /absolute/path/clip.mp4 \
  --perception-config /absolute/path/perception.toml \
  --construction-config /absolute/path/construction.toml \
  --artisan-python /absolute/path/artisan-python \
  --blender /absolute/path/blender \
  --isaac-python /absolute/path/isaac-python \
  --output /absolute/path/new-run
```

图片输入将 `--video` 换为 `--images image1.jpg image2.jpg`。入口也可用 `.venv/bin/python -m roboenv_perception.harness.cli`。
配置可从 `configs/api.example.toml`、`configs/grounded-sam2.example.toml` 和 `configs/construction.example.toml` 开始；实际地址、模型、权重及工具路径由本地配置指定。详见 [Harness 用法](docs/AGENTIC_HARNESS.md)、[API](docs/API.md)和[主机准备](docs/HOST_SETUP.md)。

输出根目录的 `state.json` 保存阶段状态，`delivery/delivery.json` 汇总资产、场景、物理检查和任务覆盖；候选、原始响应、失败与哈希一并保留。相同输入和源码可恢复已有阶段；源码或输入变化后需要新输出目录。

### 当前边界

- 几何通过类型化配方生成，能表达的形状受接口约束；视觉审查已有误拒和误放行记录，自动修复尚不稳定。
- 尺度、摆位及物理参数可含估计或假设。图像框到支撑面的布局映射不等于相机标定。
- 通用物理协议覆盖水平刚体推碰、被动铰链、旋转轴及理想固定比传动。抓取、拿起、骑行、机器人任务需要另外验证。
- `verified` 表示该次程序门禁通过。3DGS 外观、文件检查、外力实验和机器人任务分别保留自己的证据范围。
- 多张独立图片的身份融合仍可能不确定；RoboHelix / ClawSpine 和最终机器人 Benchmark 尚未接入完成。

具体成功、失败和审查反例见 [Harness 运行记录](docs/history/HARNESS_RUNS_20261027.md)。

## 分阶段使用

```bash
.venv/bin/scene-perception --help
.venv/bin/scene-perception run-video \
  --video test_media/videos/scene.mp4 \
  --config configs/local.toml \
  --scene-id desk-video --output outputs/desk-video-NEW
```

`--task` 为感知／设计提供用途描述，不直接启动机器人。`demo`、`demo-video` 和 `demo-construction` 使用合成或回放数据，供接口检查。分割后端与 CUDA 准备见 [第一步](docs/STEP1.md)、[视频与评估](docs/STEP23.md)、[替代分割后端](docs/GROUNDED_SAM2.md)；对象设计、背景与训练命令见 [构建说明](docs/CONSTRUCTION.md)。

## 代码与产物的位置

```text
src/roboenv_perception/
  *.py             图像／视频感知、数据契约、评估
  construction/    共享证据、蓝图、建模后端、纹理与背景训练
  harness/         通用构建控制器、几何配方、场景与物理协议
  experiments/     wood_table / white_table / bicycle / g1 专用实验
scripts/           环境准备、公共工具、Blender 建模程序、旧命令转接
configs/           不含凭据的配置模板
schemas/           感知与评估 JSON 契约
tests/            代码与接口回归测试
docs/             当前方法和用法；history/ 保存历史运行与规划
```

原图、视频、模型权重、凭据和 `outputs/` 默认不提交。文档中的 `outputs/...` 是运行主机上的工件路径；clone 仓库不会取得这些 USD、纹理、报告或视频，也不会自动获得历史实验结果。复制场景必须保留其资产和纹理依赖。

旧 `scripts/build_*.py` 等命令及 `construction/isaac_*` 专用 worker 路径保留兼容转接；新代码使用 `experiments` 下的实现。两种资产作者路径的区别见 [代码结构](docs/ARCHITECTURE.md)。

## 开发检查

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
.venv/bin/ruff check src tests scripts
```

真实 Blender 测试需 `BLENDER_BIN` 或 PATH 中的 Blender，其他模型／仿真运行仍需各自环境。单元测试通过不代表真实重建或机器人任务通过。

## 许可证

一手代码采用 [GPL-3.0-only](LICENSE)。第三方软件、权重、数据、机器人资产和仿真器保留各自许可。

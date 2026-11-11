# EnvCreator

EnvCreator 是当前视觉重建与交互资产生成框架的暂定名称，仓库名及 Python 模块名保持兼容。
ClawSpine 的原生机器人能力通过独立适配层接入，见 [接入方式与实际验证](docs/ENVCREATOR_CLAWSPINE.md)。
当前各入口、模型边界和已接通范围见 [仓库工作流树状图](docs/WORKFLOW_OVERVIEW.md)。
可选的本地背景扩图入口为 `envcreator background`，沿用现有素材与 mask，生成外观图片；安装、固定相机加载及范围见 [本地背景扩图](docs/LOCAL_BACKGROUND.md)。
可视化入口为 `envcreator studio`：复用原生 DSH 自然语言会话，浏览输入、产物、调用时间线与 CPU/GPU/进程监控，见 [Studio 安装与使用](docs/ENVCREATOR_STUDIO.md)。

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

输出根目录的 `state.json` 保存阶段状态，`delivery/delivery.json` 汇总资产、场景、物理检查和任务覆盖；候选、原始响应、失败与哈希一并保留。相同输入和源码可恢复已有阶段；源码或输入变化后需要新输出目录。

### 当前边界

- 几何通过类型化配方生成，能表达的形状受接口约束；视觉审查已有误拒和误放行记录，自动修复尚不稳定。
- 尺度、摆位及物理参数可含估计或假设。图像框到支撑面的布局映射不等于相机标定。
- 通用物理协议覆盖水平刚体推碰、被动铰链、旋转轴及理想固定比传动。抓取、拿起、骑行、机器人任务需要另外验证。
- `verified` 表示该次程序门禁通过。3DGS 外观、文件检查、外力实验和机器人任务分别保留自己的证据范围。
- 多张独立图片的身份融合仍可能不确定；ClawSpine 已接通原生启动与插件接口，RoboHelix 进化调度和最终机器人 Benchmark 尚未接入完成。

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

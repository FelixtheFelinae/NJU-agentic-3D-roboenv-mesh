# 视频到物理资产：仓库方法、使用流程与当前结果

2026 年 10 月 26 日

## 1. 仓库在做什么

这个仓库从真实视频或图像中提取物体和背景，分别构建可交互物体与场景外观，再放入物理仿真器中检查接触和运动。语言任务用于说明物体的用途，以及需要保留哪些功能。

代码分为两条重建分支：

- **物体分支：**VLM 理解与 SAM 分割 → 多视角证据 → 部件和关节设计 → Blender / Artisan 建模 → USD → Isaac 物理验证。
- **背景分支：**背景掩码 → COLMAP 相机恢复 → 平面纹理融合或 3DGS 训练。用于接触的桌面、地面另外建立碰撞几何。

感知、证据整理和背景训练已有命令行入口。物体构建仍包含针对具体视频的脚本、人工角点标注和参数设置，各阶段尚未合成一个通用自动重建入口。

## 2. 主要方法与对应代码

| 方法 / 模型 | 在仓库中的用途 | 主要代码 |
| --- | --- | --- |
| VLM | 识别场景中的物体概念、绑定实例语义；根据图像证据设计部件、关节和交互需求。已有实验使用 Qwen 接口，模型与服务通过配置指定 | `vlm.py`、`prompts.py`、`construction/design.py` |
| SAM 3 | 根据对象概念分割图像，并在离线视频中传播掩码和保留片段内轨迹 | `segmentation.py`、`video_backend.py`、`video_pipeline.py` |
| Grounding DINO + SAM 2.1 | 可选感知后端：先做文本目标检测，再分割和跟踪。部分桌面视频实验使用这一路线 | `grounded_sam2.py` |
| COLMAP | 从多帧图像恢复相机位姿与稀疏点，用于多视角拟合、纹理投影和 3DGS 输入 | `construction/reconstruction.py` |
| 3D Gaussian Splatting | 用背景区域监督训练场景外观，排除前景物体与未知区域；保留观测和补全来源 | `construction/train_worker.py`、`training_loss.py` |
| 平面纹理融合 | 将真实图像投影到桌面或地面，形成纹理、观测掩码和来源记录；自行车砖地使用这一路线 | `construction/table.py`、`scripts/reconstruct_bicycle_ground.py` |
| Blender | 通过后台 Python 脚本生成曲面网格、材质和预览；已用于耳机与自行车 | `construction/blender_backend.py`、`scripts/blender/` |
| Artisan | 定义刚体、质量、碰撞、关节及传动关系，编译并导出 USD、URDF、MJCF | `construction/artisan_asset_worker.py`、各资产构建脚本 |
| Isaac Sim / Isaac Lab | 加载实际导出的 USD，检查落稳、接触、推动、关节响应，并执行 G1 接触操作 | `construction/isaac_*_worker.py` |

VLM 还用于物体构建的 Coding / Review：Coding 编写 Artisan 模型并接收编译反馈，Review 使用独立上下文选择结构、关节和间隙检查，结果绑定候选文件和物理证据。实现位于 `construction/asset_agents.py`。它目前主要服务于资产构建，还不是机器人执行语言任务的决策循环。

3DGS 输出的是外观表示，当前结果仍未统一到可直接使用的物理尺度和碰撞模型。桌面和地面的接触由独立碰撞几何提供。

## 3. 代码如何使用

### 3.1 视频感知

正式推理在 Linux / NVIDIA CUDA 环境运行，使用本地模型权重和已配置的 VLM 服务。以下命令在仓库根目录执行，输出目录需要使用新路径。

```bash
.venv/bin/scene-perception run-video \
  --video test_media/videos/scene.mp4 \
  --config configs/local.toml \
  --scene-id scene-demo \
  --task '合上笔记本并推动鼠标' \
  --output outputs/scene-demo-perception
```

本地配置可参考 `configs/api.example.toml`；替代感知后端参考 `configs/grounded-sam2.example.toml`。运行结果包含采样帧、分割、对象语义、视频轨迹和背景用途掩码。`--task` 提供用途描述，不会直接启动机器人。

### 3.2 整理物体证据和设计

```bash
.venv/bin/scene-perception prepare-objects \
  --bundle outputs/scene-demo-perception \
  --output outputs/scene-demo-evidence

.venv/bin/scene-perception design-objects \
  --evidence outputs/scene-demo-evidence \
  --config configs/local-construction.toml \
  --output outputs/scene-demo-design
```

`prepare-objects` 按对象选择可见面积、清晰度和时间间隔合适的视图，保存裁剪、掩码与来源。`design-objects` 调用 VLM 生成部件和关节蓝图，并执行结构校验与有限次数的修订。

当前几组实物实验分别通过以下脚本继续构建：

- `scripts/build_video_assets.py`：木桌视频中的瓶子、笔记本和鼠标，多视角拟合及 Coding / Review。
- `scripts/build_ipad_audio_assets.py`：平板、头戴耳机和 FreeClip，支持 Blender 耳机后端。
- `scripts/build_blender_mesh.py` + `scripts/build_bicycle_asset.py`：自行车网格和物理机构。

这些脚本仍需各实验的布局、尺度和证据配置；通用 `design-objects` 的输出尚未自动适配所有构建脚本。

### 3.3 背景重建

```bash
.venv/bin/scene-perception prepare-background \
  --bundle outputs/scene-demo-perception \
  --designs outputs/scene-demo-design \
  --config configs/local-construction.toml \
  --output outputs/scene-demo-background

.venv/bin/scene-perception estimate-cameras \
  --background outputs/scene-demo-background \
  --config configs/local-construction.toml \
  --output outputs/scene-demo-cameras

.venv/bin/scene-perception train-background \
  --cameras outputs/scene-demo-cameras \
  --config configs/local-construction.toml \
  --output outputs/scene-demo-gaussians
```

这条分支会生成保留原始 RGB 的背景监督、COLMAP 相机和高斯模型。配置需要指向已安装的 COLMAP、3DGS 源码及独立训练环境。桌面纹理融合另有 `complete-table` 命令，需要提供平面标注和桌面配置。

### 3.4 进入物理仿真和机器人操作

资产导出后，由 `scripts/run_table_sim.py` 调用独立 Isaac 环境。不同 worker 分别执行桌面接触、物体交互、铰链、自行车传动和重载检查，保存场景、报告与轨迹。

已有 G1 演示可从已构建场景复现：

```bash
.venv/bin/python scripts/run_table_sim.py \
  --worker robot \
  --input outputs/user-scene-assets-20261023/simulation \
  --output outputs/g1-contact-replay
```

该命令依赖本机已有 Isaac 环境和官方 G1 参考资产。控制代码读取 URDF，使用 SciPy 求手臂 IK，通过关节 PD 和重力补偿执行合盖、拖鼠标的脚本路径，再记录手部接触与物体运动。当前固定骨盆、关闭机器人自碰撞，尚未根据相机或接触力在线重规划。

## 4. 目前完成的资产和实验

### 桌面物体

| 资产 | 已构建的动态内容 | 当前结果 |
| --- | --- | --- |
| 封盖瓶子 | 单刚体，可整体移动和倾倒 | 落稳、接触、外力倾倒通过；未做机器人抓取 |
| 14 英寸 MacBook Pro | 底座与屏幕两个刚体，约 0–137.5° 的被动铰链 | 外力开合、限位和 G1 合盖已验证；部分小开角会自行下落 |
| 鼠标 | 单刚体，可滑动和受力抬起 | 外力响应已验证；G1 有效拖动约 4.51 cm；按键和滚轮固定 |
| iPad Pro 12.9 + 保护套 | 单刚体，保护套折页固定 | 落稳、外力推动约 2.03 cm、重载通过；未做机器人拿取 |
| Sony WH-1000XM6 | Blender 曲面网格组成单刚体 | 外力推动约 1.97 cm、重载通过；没有独立伸缩或折叠机构 |
| Huawei FreeClip 充电盒 | 盒体、盒盖两个刚体，约 0–126.1° 的铰链 | 结构和重载通过；旧测试直接设角，尚未验证受力开盖；耳机固定于盒体 |

木桌场景包含瓶子、笔记本、鼠标和独立支架；白桌场景包含平板、耳机和充电盒。两组场景均已保存可加载的 USD。

G1 演示通过实际手部接触将笔记本从约 128.8° 合到约 0°，并通过摩擦拖动鼠标约 4.51 cm。操作使用预设路径；物体没有接收直接位姿或外力控制。瓶子、平板、耳机和充电盒尚未完成同等级机器人操作验证。

### 自行车与链传动

自行车由车架、转向组件、前后轮和曲柄五个刚体构成，共四个关节。后轮与曲柄使用 2.5∶1 的双向传动约束，链条采用静态外形，省略逐节碰撞、松弛和脱链。

验证包含踩踏驱动、断开传动对照、后轮阻力反传，以及地面接触下的推进。4 Nm 曲柄输入持续 1 秒、随后释放 2 秒，车辆在被动导轨上前进约 0.877 m，前后轮滚动误差约 1.2 cm；独立重载的 15 项检查通过。

推进试验限制了侧倾和转向。当前没有飞轮、刹车和平衡控制，也未验证机器人骑行。

### 当前可加载的场景

| 场景 | 仓库内入口 |
| --- | --- |
| 木桌与三物体 | `outputs/user-scene-assets-20261023/simulation/scene.usda` |
| 白桌与平板、耳机、充电盒 | `outputs/white-table-redo-20261024/simulation-blender-20261026/scene.usda` |
| 链传动自行车与砖地 | `outputs/bicycle-chain-20261026/delivery-v1/scene.usda` |

复制场景时需要保留相邻资产和纹理文件。质量、摩擦、隐藏结构和部分尺寸使用建模先验，尚未进行完整实物标定。USD 已做实际仿真，URDF / MJCF 导出不代表其他后端已通过同等验证。

## 5. 后续计划

1. 把视频证据、物体功能分解、建模脚本和审核报告接成统一工作流。按语言任务决定简化范围，让系统能够发现部件缺失、形态失真和受力异常，并调用 Blender / Artisan 局部修复；先补充充电盒受力开盖等已有缺口。
2. 评估接入 RoboHelix 的 ClawSpine，用现有观测与动作接口操作重建场景。先复现合盖、推鼠标，再扩展拿取与搬运；处理当前 29 自由度 G1 与其 43 自由度后端的构型差异。
3. 在通过验证的资产上开展质量、摩擦、摆位等有约束的随机化，批量记录机器人观测、动作和结果，供后续训练与评测使用。

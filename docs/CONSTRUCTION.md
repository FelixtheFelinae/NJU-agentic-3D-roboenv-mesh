# 对象证据、设计与背景构建

本阶段保留 VLM + SAM3 感知，新增六个显式 CLI 阶段。每阶段使用新输出目录，
保留原图、输入来源、SHA-256 索引和失败记录；不会覆盖已有运行。
可接受单图证据，但单图不能恢复相机或证明多视角覆盖。

## 安装与运行

使用 Linux 项目虚拟环境；额外依赖为 `.[construction]`。已有 SAM 环境应遵守
`requirements/cuda-dev.txt` 的 NumPy/OpenCV 约束，避免直接升级依赖。
相机阶段要求已安装 COLMAP。训练要求单独安装的官方 classic 3DGS checkout、
Python >=3.12、匹配的 CUDA rasterizer、simple-knn、plyfile，
以及本项目的 numpy/Pillow/pydantic 依赖；本项目不自动下载或编译这些依赖。

```bash
scene-perception prepare-objects --bundle outputs/perception --output outputs/evidence
scene-perception design-objects --evidence outputs/evidence \
  --config configs/local-construction.toml --output outputs/design
scene-perception prepare-background --bundle outputs/perception --designs outputs/design \
  --config configs/local-construction.toml --output outputs/background
scene-perception complete-table --background outputs/background \
  --spec configs/local-table.json --output outputs/table
scene-perception estimate-cameras --background outputs/background --completion outputs/table \
  --config configs/local-construction.toml --output outputs/cameras
scene-perception train-background --cameras outputs/cameras \
  --config configs/local-construction.toml --output outputs/gaussians
```

从 `configs/construction.example.toml` 复制本地配置，填写完整 VLM 请求 URL、model，
通过环境变量提供认证。程序不读取 `.env`。前四阶段可分别执行；桌面补全是可选阶段，
不需要时省略相机命令的 `--completion`。`scene-perception schemas` 可导出配置、
蓝图和桌面规范 JSON Schema。退出码 1 表示执行错误，设计有 blocked/failed 对象时为 2；
成功退出只表示阶段执行完成，所有输出均不声称独立验收通过。

## A：对象证据

`objects.json` 按感知 run 内 object_id 聚合原始帧、可见 mask、透明裁剪、上下文图和
reference board。按可见面积、纹理清晰度代理和时间间隔选择；不把时间差称为相机角度差。
类别冲突、ambiguous identity 或缺少可见证据会阻止设计。
感知的父子实例关系写入 `parent_object_ids`；子实例路由到父对象协调，不自动变成重复资产。
蓝图的内部功能部件是另一层次，不直接复制感知实例列表。

## B：对象设计与交接

参考本机 Artisan 的 `pipeline/agents/prompts/design/system.md` 和
`pipeline/agents/design_harness.py`：理解对象 → 保存蓝图 → 对精确蓝图哈希提交自检。
当前实现用结构化 JSON 调用复用现有 VLM HTTP/replay 适配器，不启动 Artisan SDK。
保留 support mode、功能分解、空心容器、child-local 关节轴和 travel/rest 约定；
刚体允许单部件、零关节。部件树、关节连接、证据引用和人类测量由程序校验。
自检 PASS 绑定保存文件的 SHA-256，REDO 或结构错误触发有上限的修订。

尺寸与行程区分 measured / estimated / assumed / unknown；未知为 null。
数值必须引用视图或用户测量，measured 必须引用实际提供的测量记录。整体 XYZ 测量
必须原样保留；示例见 `configs/measurements.example.json`。图像无法直接测量绝对尺度，
不可见结构和推测须放在 assumptions/unresolved 中。外观不等于摩擦、质量或密度。

`coding_handoff.json` 携带蓝图哈希、理解记录、测量、全部图像/mask 与坐标映射。
`design_ready` 仅指自检通过；独立 Review、Coding、物理验证、场景放置、
完整构建循环和 RoboHelix 接入尚未执行。

## C：背景、桌面与 3DGS

背景准备保留 RGB 原图，只输出监督 mask；独立 mesh、待解决设计、临时对象、未知区域
和人工排除不进入真实背景监督。边缘可膨胀，阴影和反射须人工检查。`--exclusions`
接收 frame_id → 二值 PNG 路径的 JSON，相对路径相对于 JSON 文件。
静态物体可辅助相机特征提取，但不作为背景 Gaussian 种子。

桌面规范示例为 `configs/table.example.json`，数值必须人工替换。同一物理平面区域在各帧的
四角按 (0,0)、(W,0)、(W,H)、(0,H) 排列，坐标属于原始归一化感知图。
仅训练帧可靠像素投影到统一 atlas，先融合，再选择 `fill="telea"` 做有限面积的
外观补全（默认 none）。补全不是语义恢复。atlas 的 provenance 值 0/1/2 分别代表
未知/真实训练观测/合成；再投回各视角，保留原始可靠像素。
真实 texel 投到另一个被遮挡视角时也只给予弱监督。holdout RGB 不参与 atlas 融合。
手工桌面尺寸不会自动标定 SfM 尺度。

相机阶段至少需三个视图，要求一个连通 COLMAP 模型且全部图像注册。
导出 `cameras.json` 的 pinhole 内参、world_to_camera / camera_to_world、原图与监督图。
mask 与 RGB 一起去畸变并保守处理边界。坐标为 COLMAP world，camera X-right/Y-down/Z-forward；
SfM 尺度仍任意，需后续与对象 Z-up 局部系、真实碰撞几何统一对齐。
只保留在至少两个训练视图中得到可靠背景支持、且未落入其他训练视图排除区的稀疏种子。
该保守策略可能丢弃被遮挡的真实背景；种子不足会明确失败。

训练 worker 使用固定稀疏种子拓扑的 classic 3DGS 优化基线，不做 densification/pruning。
L1 区域权重为真实背景 1、补全 `synthetic_weight`、未知和其他排除 0；不使用会跨越
mask 边界的全图 SSIM。保留未涂黑原图，排除区的 RGB 不提供梯度。
holdout 仅在真实可见区域计算 MSE；相机恢复可使用全部图像，因此这是外观 holdout，
不是独立相机测试。worker 兼容旧版与带 cam_infos 的初始化接口，实际兼容性须在
用户安装的具体上游提交与 CUDA 扩展上验证；保存 backend revision 与 worker 哈希。
命令失败、超时均保留日志和返回码，超时清理进程组。

设计借鉴 RE3SIM 的外观 3DGS、物理碰撞几何和统一坐标分工；本实现没有把遮挡补全
归因于其原生功能。合成图不能作为独立真值；优先补拍真实视图。

## 当前验证边界

Linux 合成夹具测试覆盖证据完整性、未知/前景排除、刚体零关节、来源校验、哈希自检与
交接、桌面融合 holdout 隔离、投影及区域权重、子进程失败记录。
这些测试不证明真实物体语义、多视角几何、SAM 准确率或 3DGS 训练质量。
真实素材、API 配置、合法 checkpoint、COLMAP 与可用 3DGS 环境齐备后才能执行实际闭环。

3DGS 接口依据官方 [renderer](https://github.com/graphdeco-inria/gaussian-splatting/blob/main/gaussian_renderer/__init__.py)
和 [GaussianModel](https://github.com/graphdeco-inria/gaussian-splatting/blob/main/scene/gaussian_model.py)，
查阅日期 2026-10-22；使用上游代码需遵守其许可证。

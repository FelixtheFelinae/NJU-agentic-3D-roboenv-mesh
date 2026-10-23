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
通过环境变量提供认证。程序不自动读取 `.env`；可用全局参数 `--env-file .env.local` 显式加载密钥。前四阶段可分别执行；桌面补全是可选阶段，
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

桌面资产的米制任务布局、USD 导出与 Isaac Lab 接触验证见 [ISAAC_TABLE](ISAAC_TABLE.md)。

背景准备保留 RGB 原图，只输出监督 mask；独立 mesh、待解决设计、临时对象、未知区域
和人工排除不进入真实背景监督。边缘可膨胀，阴影和反射须人工检查。`--exclusions`
接收 frame_id → 二值 PNG 路径的 JSON，相对路径相对于 JSON 文件。
静态物体可辅助相机特征提取，但不作为背景 Gaussian 种子。

桌面规范示例为 `configs/table.example.json`，数值必须人工替换。同一物理平面区域在各帧的
四角按 (0,0)、(W,0)、(W,H)、(0,H) 排列，坐标属于原始归一化感知图。
仅训练帧可靠像素投影到统一 atlas，先融合，再选择 `fill="telea"` 或 `fill="patch"`
做有限面积的外观补全（默认 none，受 `max_fill_fraction` 限制）。`fusion="median"`
可抑制偶发高光，默认 mean 保留历史行为。patch 只从完全由真实观测组成的纹理块取样，
按已知边界匹配并平移、不旋转木纹；`patch_size` 是奇数像素边长，默认 41。
补洞边界仅在缺失区域内部融合，已有真实 RGB 不变。`atlas-observed.png` 保存补洞前结果。
补全不是语义恢复，也不会去除持久阴影或恢复真实反射材质。atlas 的 provenance 值 0/1/2 分别代表
未知/真实训练观测/合成；再投回各视角，保留原始可靠像素。
真实 texel 投到另一个被遮挡视角时也只给予弱监督。holdout RGB 不参与 atlas 融合。
手工桌面尺寸不会自动标定 SfM 尺度。

若只需简化显示，`construction.texture.repeat_texture_material` 可将一块完全观测到的
干净木纹镜像铺设到整个桌面，避免保留原物体的阴影；重复花纹是预期近似。
这种材质的所有位置分配均标为合成，不能因源颜色来自照片而当成对应位置的真实观测。
材质和视觉平面不提供碰撞体，也不证明实际桌面高度或 contact 准确。

相机阶段至少需三个视图，要求一个连通 COLMAP 模型且全部图像注册。
导出 `cameras.json` 的 pinhole 内参、world_to_camera / camera_to_world、原图与监督图。
mask 与 RGB 一起去畸变并保守处理边界。坐标为 COLMAP world，camera X-right/Y-down/Z-forward；
SfM 尺度仍任意，需后续与对象 Z-up 局部系、真实碰撞几何统一对齐。
只保留至少有两个真实训练轨迹观测、且这些观测都落在可靠背景内的稀疏种子。
没有轨迹观测的投影不证明可见性，不会因为投到遮挡物上就拒绝种子；种子不足会明确失败。

训练 worker 默认 `training_mode="adaptive"`，使用 classic 3DGS 的增密、分裂、剪枝与
透明度重置。增密统计只累计收到有效监督梯度的高斯，分裂/克隆候选在分配内存前按
梯度排序限制，每次密度调整完成后的点数不超过 `max_gaussians`。分裂仍需临时缓冲，
这个设置不是显存硬上限。默认训练预算 10000 步，增密在
500～7000 步之间运行，每 100 步调整一次；`training_mode="fixed"` 保留旧 L1 固定点数基线。
自适应模式的学习率尺度及大高斯剪枝尺度采用训练相机中心半径；固定模式保留种子半径。

L1 区域权重为真实背景 1、补全 `synthetic_weight`、未知和其他排除 0。SSIM 只在
完整 11×11 窗口属于同一来源（真实或合成）的中心计算，不跨越排除边界、图像边缘或
真实/合成来源边界；合成窗口仍以弱权重计算。缺少有效窗口时退回纯 L1。
保留未涂黑原图，未知和排除区的 RGB 不提供损失梯度，也不被当成黑色背景真值。

默认保存第 3000、7000 步和最终步的 PLY、预览及验证误差，存于 `model/checkpoints/`。
`model/point_cloud.ply` 指向真实可见区域平均 MSE 最低的检查点副本，实际步数记录在
`model/result.json` 的 `selected_iteration`；训练预算与被选中的步数分别保留。
没有验证视角时选最终步。相机恢复可使用全部图像，且这些外观验证视角用于模型选择，
所以不构成独立的相机、几何或最终测试。遮挡区域仍可能存在高斯溢出，不代表已经补全。
worker 兼容旧版与带 cam_infos 的初始化接口，实际兼容性须在
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

## 配置检查与可运行示例（2026-10-22）

```bash
# 不调用模型，不下载权重；缺项时退出 2，并输出缺失字段及错误层级
scene-perception construction-doctor --config configs/local-construction.toml \
  --perception-config configs/local.toml --media /absolute/path/to/desk.mp4

# 运行合成视频 → 对象证据 → replay 蓝图自检交接 → 背景 → 桌面融合/补全
scene-perception demo-construction --output outputs/construction-demo-NEW
```

合成示例不执行真实模型、相机恢复或 3DGS 训练。检查命令不发送网络请求，
不输出 header 内容或环境变量值；检查通过仍不保证第三方二进制实际兼容。
`background.colmap`、`background.gaussian_python` 若包含相对目录，按配置文件所在目录解析；
只有程序名时沿用 PATH 查找。

建议初次云端联调使用 `qwen3.7-plus`；配置模板见
[`qwen-construction.example.toml`](../configs/qwen-construction.example.toml)。
该模板是北京地域示例，`enable_thinking=false`、JSON Object 输出，尚未发起真实 API 请求。
使用前确认账号可访问模型与所选地域；可替换为控制台提供的业务空间专属 endpoint。
感知和构建目前分别读自己的配置，需要把相同 `[vlm]`、`[vlm.headers]`、
`[vlm.extra_body]` 设置放入 `configs/local.toml` 与 `configs/local-construction.toml`。
不要把构建专用 `[evidence]` 等节放进感知配置。

需要用户填写/提供：

| 项目 | 位置 | 何时需要 |
| --- | --- | --- |
| API 完整请求 URL、模型、地域 | 两份本地配置的 `[vlm]` | 真实感知/对象设计 |
| API 密钥 | 当前运行进程的 `VLM_API_KEY` 环境变量 | 真实 API；不要写入受控文件或聊天 |
| 原版 SAM3 checkpoint 本地路径 | 感知配置 `sam.checkpoint` | 真实分割 |
| 真实图片/视频在此 Linux 主机上的路径 | `--image` / `--video` / doctor 的 `--media` | 真实运行 |
| 对象真实 XYZ 尺寸及测量备注 | `design.measurements` 指向的 JSON | 可暂缺，尺寸保持 unknown |
| 同一桌面区域的真实宽深、各帧四角 | `--spec` JSON | 真实桌面融合；不能沿用示例数字 |

程序不自动读取 `.env`；可用 `scene-perception --env-file .env.local ...` 显式加载。
如需在远程终端临时输入密钥，可以执行
`read -rsp 'VLM API key: ' VLM_API_KEY`，随后 `export VLM_API_KEY`；
终端输入不会回显，命令历史也不包含密钥。其他终端设置的环境变量不会自动进入已启动的
Codex 进程，请在配置完成后告知运行方式，以便从正确环境启动。

本节早期检查时 COLMAP/3DGS 尚未准备；随后已安装并完成合成三维场景的
COLMAP 和真实 GPU 短程训练，真实 VLM API 也已接通。当前路径、复现脚本、
剩余 SAM 权限/素材及验证边界见 [重建运行环境](RECONSTRUCTION_RUNTIME.md)。

官方接口依据（查阅日期 2026-10-22）：
[视觉理解与结构化输出](https://help.aliyun.com/en/model-studio/vision-model/)、
[地域、业务空间域名与兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。
模型推荐尚未在用户素材上做过比较。

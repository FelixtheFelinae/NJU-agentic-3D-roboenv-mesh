# 本机重建运行环境与复现

2026-10-22 在远程 Ubuntu 完成用户目录安装；未使用 sudo，未升级系统包，
未下载模型权重，也未修改其他项目的 Conda 环境。

## 已配置路径

| 项目 | 本机路径/版本 |
| --- | --- |
| COLMAP | `/home/felixz/.local/share/roboenv/colmap/bin/colmap`，Ubuntu 3.7-2，CPU 构建 |
| 3DGS 源码 | `/home/felixz/.local/share/roboenv/gaussian-splatting` |
| 3DGS commit | `54c035f7834b564019656c3e3fcc3646292f727d` |
| Rasterizer commit | `9c5c2028f6fbee2be239bc4c9421ff894fe4fbe0` |
| simple-knn commit | `86710c2d4b46680c02301765dd79e465819c8f19` |
| Worker Python | `/home/felixz/.local/share/roboenv/gaussian-env/bin/python` |
| CUDA 编译器/目标 | CUDA Toolkit 12.4，compute capability 8.9，MAX_JOBS=2 |
| PyTorch | 本项目固定的 `2.10.0+cu128`，CUDA runtime 12.8 |

COLMAP 的 deb 和缺失的运行库下载并解到用户目录，由 wrapper 设置该命令需要的
`LD_LIBRARY_PATH` 和无显示器 Qt 模式。因为此发行包无 CUDA，
`background.use_gpu_features=false`；这不影响 GPU 上的 SAM/3DGS。

3DGS 环境独立安装扩展和构建工具，通过一个 `.pth` 文件只读引用本项目 `.venv`
中的固定 PyTorch/NumPy/Pillow 等包，避免再复制数 GB 运行库。它不是完全独立的依赖副本：
移动或删除项目 `.venv` 后必须重新执行安装脚本。新 CUDA 扩展没有写入项目 `.venv`。
实际 CUDA rasterizer 训练和 simple-knn GPU 运算均通过；Toolkit 12.4 与 torch runtime
12.8 的组合仅在记录的这台主机上实测，不保证其他系统的编译兼容性。

上述路径已填入被 Git 忽略的 `configs/local-construction.toml`。
本机完整安装 provenance 见 `~/.local/share/roboenv/construction-runtime.json`，
包含 deb 哈希和源码提交；运行输出中另存副本。

## 复现安装

在已准备本项目 CUDA `.venv` 的 Ubuntu 主机执行：

```bash
uv pip install --python .venv/bin/python -c requirements/cuda-dev.txt -e '.[construction]'
bash scripts/bootstrap_construction.sh
```

脚本不加载 API Key、不下载模型、不安装 SIBR viewer 或 fused-ssim，
仅下载固定的 3DGS 源码及两个必要 CUDA 子模块，保留已有不同版本的源码目录并报错。
脚本已在本机现有安装上复跑通过；未在另一台干净主机验证。
默认路径可通过 `ROBOENV_RUNTIME_ROOT` 修改，CUDA/架构可通过
`CUDA_HOME`、`TORCH_CUDA_ARCH_LIST`、`MAX_JOBS` 调整。默认架构 8.9 专用于本机显卡。

## 合成三维集成测试

`scripts/smoke_reconstruction.py` 用解析射线交点生成一致的三维视差、纹理地面/墙面、
一个静态背景台座和一个独立前景立方体；导出原图、mask、已知相机真值。
COLMAP 不读取相机真值。它不是用户视频、SAM 预测或正式质量验收。

```bash
.venv/bin/python scripts/smoke_reconstruction.py --output outputs/room-NEW/background
.venv/bin/scene-perception estimate-cameras --background outputs/room-NEW/background \
  --config configs/local-construction.toml --output outputs/room-NEW/cameras
# 短程验证可使用配置副本将 iterations 设为 200；当前模板为 10000。
.venv/bin/scene-perception train-background --cameras outputs/room-NEW/cameras \
  --config configs/local-construction.toml --output outputs/room-NEW/gaussians
```

实际运行目录为 `outputs/reconstruction-integration-20261022/`：

以下是历史固定点数训练器的记录。当前默认自适应训练已加入增密/剪枝与不跨掩码边界的
SSIM；需要重现历史行为时显式设置 `training_mode="fixed"`。默认保留中间检查点，
以可见背景验证 MSE 选择输出模型。点数上限、来源边界与损失梯度另有针对性测试。

- `cameras/`：12/12 视图注册，3437 个保守背景种子。事后与合成相机真值做相似变换对齐，
  相机中心 RMSE 约 0.000296 m。此尺度只用于诊断，未注入 COLMAP，也不代表真实场景尺度已恢复。
- `gaussians-with-previews/`：真实 GPU 训练 200 步，输出 Gaussian PLY、两个 holdout 渲染和
  仅真实可见区域上的 MSE（约 0.02560、0.01806）。预览仍明显模糊：固定稀疏种子、
  无 densification/pruning 的短程基线不具备高质量场景重建的证据。
- `floor-completion/`：用合成真值投影得到的平面四角模拟人工标注，融合并补全统一 atlas；
  atlas 融合前未知比例约 19.88%，holdout RGB 没有参与融合。真实视频仍需实际标注/测量，
  不能套用这里的坐标或尺寸。
- `cameras-with-completion/` 和 `gaussians-with-completion/`：再次恢复相机，得到 3435 个种子，
  训练视图合计 84434 个补全像素使用权重 0.15。holdout 的 synthetic mask 像素数为 0。
  真实 GPU 训练 200 步完成，holdout MSE 约 0.02588、0.01815；不据此声称补全提高了质量。
- `completion-training-summary.json`、`camera-comparison.json` 和 `runtime-installation.json`：
  对应数值、来源和版本记录；`holdout-comparison.png` 是无补全训练的对照预览。

补全仍是弱监督假设，前景原始 RGB 没有被当成背景真值，未知/排除区域不监督。
3DGS 没有物理碰撞几何、真实尺度或 RoboHelix 接入。

## API 与剩余输入

两份本地配置已填写北京 endpoint、`qwen3.7-plus`、非思考模式及 JSON 输出。
密钥放项目根目录 `.env.local`，格式为 `VLM_API_KEY=...`，权限 600，Git 忽略。
只有显式指定参数才加载，不执行 shell，不进行变量插值，不打印值；已有进程环境变量优先。

```bash
.venv/bin/scene-perception --env-file .env.local construction-doctor \
  --config configs/local-construction.toml --perception-config configs/local.toml
```

真实 API 小规模图像请求已成功，证明当前 Key 能调用所配置模型。
合成证据上的真实 Design API 也已运行：旧 intake 曾把 `asset_id` 错当 `object_id`，
程序拒绝该输出；现已明确提示原始 ID 并加入有界纠错，保留全部拒绝轨迹。
对只有二维矩形的“cup”夹具，自检拒绝无依据的空心圆柱蓝图；不能把 replay 测试的
PASS 当作真实模型质量结论，也不会为了跑通而放宽自检。

SAM3 的只读访问检查仍返回 `403/GatedRepoError`，未下载 checkpoint。
剩余真实运行输入为获授权的 `sam3.pt` 本地路径，以及用户桌面视频/图片的服务器路径。

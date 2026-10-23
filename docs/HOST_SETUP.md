# 本机开发环境

本项目后续在 `/home/felixz/NJU-agentic-3D-roboenv-mesh` 开发，使用仓库内独立
`.venv`，不将 `openvla-oft`、`env_isaaclab`、`env_phc` 等 Conda 环境作为感知开发环境，
也不修改它们的包。后续桌面仿真 worker 使用独立 Python 3.11 解释器读取既有 Isaac Sim
安装，与感知 Python 3.12 分开；运行数据也分开，详见 [ISAAC_TABLE](ISAAC_TABLE.md)。

## 固定位置与依赖

- Python：3.12，版本选择记录在 `.python-version`。
- PyTorch：`2.10.0+cu128`；torchvision：`0.25.0+cu128`。
- 额外运行依赖与兼容性约束：`requirements/cuda-dev.txt`。
- 本机 Linux / Python 3.12 的完整第三方依赖锁：`requirements/cuda-dev.lock.txt`。
- 官方 SAM 3 源码：`/home/felixz/.local/share/roboenv/sam3`。
- SAM 3 源码提交：`7f473cfd983d5eb43040ff608f66bff0d7bdcb53`。
  本机使用官方 GitHub 源码压缩包安装；这是可编辑源码目录，不是 Git checkout。
  来源、压缩包哈希和提交记录在其上级目录 `sam3-source.json`。
- 本地 API 配置：`configs/local.toml`，已忽略提交；初始为待填写模板。
- 安装日志、依赖快照与本机验证工件：`outputs/host-setup-20261020/`，已忽略提交。

SAM 3 该版本要求 NumPy 1.x，并导入 `pkg_resources`，因此固定
`numpy==1.26.4`、`setuptools==80.9.0`。OpenCV 使用兼容 NumPy 1.x 的 headless
版本，用于服务器视频读取；补充 einops、psutil 和 pycocotools。

本机系统 CUDA Toolkit 为 12.4；PyTorch 使用 wheel 自带的 CUDA 12.8 运行库。
运行预编译 wheel 不要求升级系统 Toolkit。若未来需要编译自定义 CUDA 扩展，
应另行检查编译工具链，不能把现有 `nvcc` 当作 CUDA 12.8。

## 日常使用

在项目目录执行：

```bash
source .venv/bin/activate
unset PYTHONPATH PYTHONHOME
scene-perception doctor --require-cuda
pytest -q
ruff check src tests scripts
```

本机 shell 的 ROS 配置会继承 Python 3.10 的 `PYTHONPATH`；上述清理只作用于当前
项目终端，不修改主机 ROS 或其他项目的配置。新终端中需再次清理。

也可用 `env -u PYTHONPATH -u PYTHONHOME .venv/bin/python` 或相同前缀的
`.venv/bin/scene-perception`，无需激活环境。
已有 `.venv` 时不要重复运行创建环境的步骤。

## 重建环境

先准备上述固定提交的官方 SAM 3 源码，再在新环境中执行：

```bash
uv python install 3.12
uv venv --python 3.12 --seed .venv
unset PYTHONPATH PYTHONHOME
uv pip install --python .venv/bin/python \
  --no-deps torch==2.10.0+cu128 torchvision==0.25.0+cu128 \
  --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python \
  -r requirements/cuda-dev.lock.txt \
  -e /home/felixz/.local/share/roboenv/sam3 -e '.[server,dev]'
```

`requirements/cuda-dev.txt` 固定关键兼容版本，`cuda-dev.lock.txt` 进一步固定传递
依赖，适用于本机 Linux / Python 3.12。完整已安装版本另存为本机验证目录中的
`installed.txt`。源码路径是本机路径，迁移主机时需相应调整。

`uv.toml` 将项目的普通 Python 依赖下载源设为阿里云 PyPI 镜像。上述重建命令
使用官方 CUDA 12.8 下载站取得 PyTorch wheel；本次本机安装为加速下载，也使用
阿里云的 PyTorch wheel 镜像。所有大型依赖均按 PyPI/官方 PyTorch 发布的
SHA-256 校验；下载来源和哈希记录在本机验证目录 `wheel-sources.json`。

本机代理监听 `127.0.0.1:7890`。若命令行下载没有使用已开启的代理，可仅对该次
安装命令设置 `HTTPS_PROXY=http://127.0.0.1:7890` 和
`HTTP_PROXY=http://127.0.0.1:7890`；不改全局 shell 或其他项目的设置。

## 真实推理前的配置

1. 用已获授权的 Hugging Face 账号取得原版 `facebook/sam3` 的 `sam3.pt`，
   或使用用户已有的合法本地 checkpoint。不能用 SAM 3.1 权重替代验证。
2. 在 `configs/local.toml` 填写 `sam.checkpoint`。相对路径以配置文件所在目录为基准。
3. 填写 VLM 完整请求地址、模型和认证 header，并在自己的终端设置所需环境变量。
   程序不自动读取 `.env`；不要把密钥写进受版本控制的文件。
4. 图片、视频分别放到 `test_media/images/`、`test_media/videos/`，输出使用新目录。

2026-10-20 检查时，本机已有 Hugging Face 登录，官方身份检查返回 200，
但 `facebook/sam3` 的 checkpoint 请求返回 403 / `GatedRepo`。用户已提交申请，
官方响应确认仍在等待仓库作者审核，审核通过后才能下载。
API 配置暂留空，尚未执行真实 VLM/SAM 推理或人工数据评估。

## 安装后检查

开发依赖、官方 SAM 3 源码、CUDA 运算、项目 CUDA IoU、SAM 3 Triton 连通域和
NVENC 编码/解码均已在本机验证通过。完整环境中的测试为 41 项通过、1 项按定义
跳过（本机已安装 SAM，不适用“未安装 SAM”测试），`pip check` 无依赖冲突。
详见 [VALIDATION](VALIDATION.md) 的 2026-10-20 记录。

# 运行环境

Mac 用于代码维护、CLI 与 Studio 构建。真实感知流程要求 Linux/NVIDIA CUDA；Blender、Artisan、COLMAP/3DGS、OpenUSD 和 Isaac 由各自配置的可执行程序或独立解释器调用。

## Mac 维护入口

已有 `.venv` 时复用它。需要新建环境时使用 Python 3.12+，在项目目录执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,studio]'
.venv/bin/envcreator --help
.venv/bin/envcreator workflow start --help
npm ci --prefix integrations/studio
npm run build --prefix integrations/studio
```

Studio 的原生 Agent 会话还需配置外部 ClawSpine checkout 和独立 DSH 模型，见 [Studio](ENVCREATOR_STUDIO.md) 与 [DSH 模型配置](DSH_MODEL.md)。安装前端依赖不能证明机器人运行环境已就绪。

## Linux 感知与构建

- 感知使用 Python 3.12，依赖见 `requirements/cuda-dev.txt` 与 `requirements/cuda-dev.lock.txt`。后者基于原 Linux 主机的版本快照，已剔除开发测试依赖。
- 官方 SAM 3 源码版本为 `7f473cfd983d5eb43040ff608f66bff0d7bdcb53`，权重由使用者放在推理机器上并配置 `sam.checkpoint`。
- CUDA 安装脚本为 `scripts/bootstrap_cuda.sh`，仅在准备好的 Linux 环境运行，并传入官方 SAM 3 源码目录。已有环境先检查配置，不重复创建。
- Blender 与 Artisan 的路径见构建配置；Isaac 使用独立解释器或既有容器，不使用感知环境替代。
- 可选背景环境由 `scripts/bootstrap_construction.sh` 准备。脚本中的 Ubuntu、CUDA 与 GPU 架构默认值来自原执行主机，需要在目标 Linux 上核对。
- OpenUSD 场景打包通过 `envcreator handoff --usd-python /absolute/path/to/python` 指定安装了 `usd-core` 的解释器。

运行前将本地 VLM 配置、SAM 权重和用户素材路径填入配置文件。输入使用绝对路径，输出使用新目录。密钥通过环境变量或入口显式支持的凭据文件传入，不写入 Git。

```bash
.venv/bin/scene-perception doctor --require-cuda
.venv/bin/envcreator workflow start --help
.venv/bin/envcreator generate --help
```

环境检查只证明其列出的依赖条件。实际模型调用、几何导出、物理验证与机器人任务分别以运行产物为准。原 Linux 主机安装和验证记录见[清理前环境文档](https://github.com/FelixtheFelinae/NJU-agentic-3D-roboenv-mesh/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/HOST_SETUP.md)。

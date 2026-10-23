# 在另一台电脑下载 SAM3，再迁移到本机

官方权重：<https://huggingface.co/facebook/sam3>，文件名 `sam3.pt`。
使用已经获批的同一个 Hugging Face 账号；仅登录网页不会自动登录终端。

## 下载电脑：Windows / macOS / Linux

在希望保存权重的目录打开终端。Windows 可使用 PowerShell：

```bash
python -m pip install --upgrade huggingface_hub
hf auth login
hf download facebook/sam3 sam3.pt --local-dir ./sam3-weights
```

登录时粘贴 <https://huggingface.co/settings/tokens> 创建的 Read Token。
如使用细粒度 Token，允许读取该受限模型仓库。询问是否加入 Git 凭据时可选 `n`。
Token 不需要发给他人，也不需要随权重迁移。

如果终端找不到 `hf`，直接通过 Python 完成相同操作：

```bash
python -c "from huggingface_hub import login; login()"
python -c "from huggingface_hub import hf_hub_download; print(hf_hub_download(repo_id='facebook/sam3', filename='sam3.pt', local_dir='./sam3-weights'))"
```

Windows 若只有 `py` 命令，将上述 `python` 改成 `py`；macOS/Linux 若只有 `python3`，改成 `python3`。
完成后确认 `sam3-weights/sam3.pt` 存在。中断后重跑同一下载命令即可。
若 403：检查登录账号是否获批、Token 是否属于该账号、受限仓库读取权限是否打开。

## 迁移

本机项目中的目标目录已准备为：

```text
/home/felixz/NJU-agentic-3D-roboenv-mesh/checkpoints/sam3/
```

通过 U 盘、SFTP 或现有文件传输工具，只复制 `sam3.pt` 到该目录。

两机 SSH 可达时，也可在下载电脑执行：

```bash
scp ./sam3-weights/sam3.pt felixz@本机IP:/home/felixz/NJU-agentic-3D-roboenv-mesh/checkpoints/sam3/sam3.pt
```

`本机IP` 替换为这台 Linux 机器可访问的地址。在本机运行 `hostname -I` 查看地址；
SSH 还要求本机 SSH 服务可用、两台电脑之间网络可达。

## 校验

Windows 下载电脑：

```powershell
Get-FileHash ./sam3-weights/sam3.pt -Algorithm SHA256
```

macOS 下载电脑：

```bash
shasum -a 256 ./sam3-weights/sam3.pt
```

Linux 下载电脑：

```bash
sha256sum ./sam3-weights/sam3.pt
```

本机：

```bash
sha256sum /home/felixz/NJU-agentic-3D-roboenv-mesh/checkpoints/sam3/sam3.pt
```

两边哈希应完全一致。迁移完成后，修改已有 `configs/local.toml` 的 `[sam]` 段：

```toml
[sam]
backend = "sam3"
checkpoint = "../checkpoints/sam3/sam3.pt"
```

不要重复添加第二个 `[sam]` 段。该相对路径以配置文件所在目录为准。
检查运行环境：

```bash
cd /home/felixz/NJU-agentic-3D-roboenv-mesh
.venv/bin/scene-perception doctor --require-cuda
```

`doctor` 是就绪检查；真正的权重加载还需运行单帧推理。迁移权重本身不会自动切换正在使用
Grounding DINO + SAM2 的视频配置，也不会改变本轮已经构建的三个物理资产。

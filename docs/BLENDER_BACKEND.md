# Blender 曲面建模后端

Blender 在后台将 `GeometryRecipe` 转换为分件网格、碰撞几何和诊断渲染。Workflow 负责 Agent 决策和修订。普通资产配置 `usd_python` 后由 OpenUSD 写入物理资产，旧构建与冻结 v9 路径可使用 Artisan。实际 USD 的物理行为由 Isaac 协议验证。 [直接生成 Isaac USD](ISAAC_USD_WORKFLOW.md)

## 运行环境

历史 Linux 主机使用官方 Blender 4.5.9 LTS 可移植包，记录路径如下。

```text
/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender
```

当时核对的 SHA-256 为 `dcdc3eca6c9825bb35a8033b689c053f3cb5a9b0cd2a61b2eac2a49436b4ad3d`。Blender 使用自身 Python 环境。 [Blender 官方下载](https://download.blender.org/release/Blender4.5/)

构建入口优先使用显式 Blender 路径，再读取 `BLENDER_BIN`，最后在 PATH 查找 `blender`。Studio 和固定 Workflow 可在 TOML 设置 `blender`。单独运行生成器时也可设置环境变量。

```bash
export BLENDER_BIN=/absolute/path/to/blender
```

## 通用后端接口

`construction/blender_backend.py` 的 `build_blender_meshes()` 运行受信任的 Blender Python 脚本。
生成脚本可读取 `parameters`。可将下例保存为本地 `box-generator.py`，再通过独立入口调用。

```python
import bpy

bpy.ops.mesh.primitive_cube_add(size=1)
obj = bpy.context.object
obj.scale = (0.10, 0.20, 0.04)
export_mesh("body", obj, rgba=(0.4, 0.4, 0.4, 1), material_class="matte_plastic")
```

`export_mesh` 输出一个部件网格，多个视觉网格可归属同一刚体。坐标以米、Z-up、部件局部系表达，物体变换和修改器烘焙到顶点。生成脚本可使用 bpy 曲线、细分和倒角等功能。

```bash
.venv/bin/python scripts/build_blender_mesh.py \
  --generator ./box-generator.py \
  --output outputs/blender-box-NEW
```

可选 `--parameters config.json`、`--no-render`、`--timeout 600`。
所有输出目录必须是新目录，以保留已有结果和失败记录。

生成产物包括 OBJ、可继续编辑的 `asset.blend`、三角度 PNG、源脚本、参数、日志、
`result.json`（版本、边界、体积、连通分量、三角形数、文件哈希）。
导出只合并 0.1 微米内的重复顶点以焊接 Blender 曲线端盖，不封补真实孔洞。
开放表面、多个不连接组件和非正体积会失败并保留日志。直接 USD 路径使用分段凸碰撞体保留 `annulus` 的贯穿孔。网格检查与实际 PhysX 配合分别记录。

构建运行使用工厂启动配置、限定线程数和超时，不继承模型/API 密钥环境变量。
生成脚本是本地受信任代码；此进程隔离不是操作系统级安全沙箱。

## 历史白桌场景

2026-10-26 的白桌实验使用 Blender 生成耳机网格，经 Artisan 写 USD，随后在当时的 Isaac 环境中重载并验证外力推动。充电盒通过直接设角检查关节状态，外观环绕视频记录静态场景。专项脚本已从当前分支移除，命令、构建目录和证据保存在 [清理前的 Blender 记录](https://github.com/FelixtheFelinae/SceneCreator/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/BLENDER_BACKEND.md)。

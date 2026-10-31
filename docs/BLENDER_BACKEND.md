# Blender 曲面建模后端

2026-10-26 接入。当前提供确定性后台构建，不运行 agent 决策或自动修复，也未注册 Blender MCP 服务。
Blender 负责曲面网格与资产特写；Artisan 继续定义刚体、质量、材质、关节并导出物理资产；
Isaac 验证实际 USD。耳机是首个接入对象，平板和充电盒继续使用现有几何构建。

## 本机环境

官方 Blender 4.5.9 LTS 的 Linux 可移植包位于：

```text
/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender
```

下载来源为 https://download.blender.org/release/Blender4.5/ ，已核对官方 SHA-256：
`dcdc3eca6c9825bb35a8033b689c053f3cb5a9b0cd2a61b2eac2a49436b4ad3d`。
不向项目 Python 环境安装 bpy，不更改现有 CUDA 或 Isaac 依赖。

`--blender` 优先，其次 `BLENDER_BIN`，最后在 PATH 查找 `blender`。
可在当前终端设置：

```bash
export BLENDER_BIN=/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender
```

## 通用后端接口

`construction/blender_backend.py` 的 `build_blender_meshes()` 运行受信任的 Blender Python 脚本。
生成脚本可读取 `parameters`，并调用：

```python
import bpy

bpy.ops.mesh.primitive_cube_add(size=1)
obj = bpy.context.object
obj.scale = (0.10, 0.20, 0.04)
export_mesh("body", obj, rgba=(0.4, 0.4, 0.4, 1), material_class="matte_plastic")
```

每次 `export_mesh` 生成一个视觉部件，不自动创建一个刚体。多个视觉部件可以属于同一刚体。
坐标以米、Z-up、部件局部系表达，物体变换与修改器会烘焙到顶点，不自动居中或归一化。
生成脚本可自由使用 bpy 曲线、细分、倒角等工具；不是仅限耳机的特殊生成 API。

```bash
.venv/bin/python scripts/build_blender_mesh.py \
  --generator scripts/blender/headphones.py \
  --output outputs/blender-headphones-NEW
```

可选 `--parameters config.json`、`--no-render`、`--timeout 600`。
所有输出目录必须是新目录，以保留已有结果和失败记录。

生成产物包括 OBJ、可继续编辑的 `asset.blend`、三角度 PNG、源脚本、参数、日志、
`result.json`（版本、边界、体积、连通分量、三角形数、文件哈希）。
导出只合并 0.1 微米内的重复顶点以焊接 Blender 曲线端盖，不封补真实孔洞。
开放表面、多个不连接组件、非正体积会失败并保留日志。每个视觉部件须单独封闭，
耳垫的贯穿内孔不因此被填平。

构建运行使用工厂启动配置、限定线程数和超时，不继承模型/API 密钥环境变量。
生成脚本是本地受信任代码；此进程隔离不是操作系统级安全沙箱。

## 接入现有白桌场景

下面的构建命令使用已有 Artisan 环境；项目 `src` 通过 PYTHONPATH 提供给父进程，
不会传入独立 Blender 子进程。

```bash
PYTHONPATH=src /absolute/path/Artisan/.artisan/venv/bin/python \
  -m roboenv_perception.experiments.white_table.build_assets \
  --root outputs/white-table-redo-20261024/assets-blender-NEW \
  --artisan-python /absolute/path/Artisan/.artisan/venv/bin/python \
  --mesh-backend blender \
  --tablet-texture outputs/white-table-redo-20261024/tablet_top.png \
  --scene-layout outputs/white-table-redo-20261024/assets-v2-20261026/scene-build-spec.json

.venv/bin/python scripts/run_table_sim.py \
  --worker audio-assets \
  --input outputs/white-table-redo-20261024/assets-blender-NEW \
  --output outputs/white-table-redo-20261024/simulation-blender-NEW

.venv/bin/python scripts/run_table_sim.py \
  --worker audio-scene-check \
  --input outputs/white-table-redo-20261024/simulation-blender-NEW \
  --output outputs/white-table-redo-20261024/scene-blender-NEW \
  --record-orbit-seconds 4
```

不传 `--mesh-backend` 时保持 legacy 几何。Blender 耳机由 8 个视觉网格组成一个刚体，
材质经 Artisan 保留，不再套用旧耳机按固定高度选择顶面的外观修补。
尺寸约 19.3 × 19.1 × 4.4 cm，来自当前建模参数与视频形状估计，并非实测或产品 CAD。
当前不提供耳罩转轴、伸缩和折叠自由度。

## 本次产物与验证范围

- `outputs/white-table-redo-20261024/assets-blender-v3-20261026/`：三个编译资产，耳机新增 Blender 建模。
- `outputs/white-table-redo-20261024/simulation-blender-20261026/`：实际 Isaac 场景与物理报告。
- `outputs/white-table-redo-20261024/scene-blender-video-v2-20261026/`：独立重载、截图和环绕视频。

测试通过可证明后台 Blender 执行、单位/局部坐标导出、封闭性拒绝、文件变更识别，
以及本机 Artisan/Isaac 的实际兼容性。耳机推动采用真实外力；充电盒旧的关节状态检查仍然
通过直接设角开始，不能作为力驱动开合的证明。本次没有扩展其交互验证。
环绕视频展示静态场景外观，不是交互动作录制。
没有运行 VLM 独立外观审查，不能以编译或物理通过声称与原视频精确一致。

```bash
BLENDER_BIN=/path/to/blender PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  .venv/bin/python -m pytest tests/test_blender_backend.py -q
```

无 Blender 时跳过真实运行测试；本机已用上列安装版本执行实际测试。
设计遵循本机 Artisan `sdk/_docs/base/50_blender_freeform.md` 的分工：
Blender 输出曲面，SDK 保持骨架、关节和坐标定义。

# SceneCreator × ClawSpine：场景与机器人运行能力的接入

原生接入记录始于 2026-10-28。当前导出入口见下文，历史机器人运行结果保留原适用条件。

当前仓库名为 **SceneCreator**，继续维护视觉重建、资产生成、装配语义及验证。
这一阶段接入 ClawSpine 的原生机器人运行入口。代码保留在当前仓库，不迁入 RoboHelix；
RoboHelix 的候选进化、研究记忆、调度和评测数据库尚未接入。

## 从现有生成流程到机器人运行

```text
描述 + 视频／多图片
  → SceneCreator：感知 → 证据 → 蓝图 → 几何 → 审查／修复 → 场景与物理检查
  → delivery.json + scene.usda
  → integration/bundle.py：场景交付包
  → integration/clawspine.mjs：ClawSpine 原生启动入口
  → 原生 DSH 会话、G1 观测、工具、控制与 Dashboard
```

配置 `usd_python` 时，固定 Workflow 会组装并导出候选场景包。`scenecreator generate` 仍可通过 `harness/cli.py` 调用多对象 `run_harness`，再使用 `handoff` 打包。已知规格场景由专用入口直接打包，其新螺纹钩子尚待接收端挂载。 [直接生成 Isaac USD](ISAAC_USD_WORKFLOW.md)

| 已有方法 | 在 SceneCreator 中的环节 | 代码入口 |
| --- | --- | --- |
| VLM | 对象候选、实例语义、部件／关节蓝图、预览复审与修复建议 | `pipeline.py`、`construction/design.py`、`harness/geometry.py` |
| SAM3，或配置的 Grounding DINO + SAM2.1 | 对象可见区域的分割与证据裁剪 | `pipeline.py`、`video_pipeline.py` |
| Blender | 按几何 recipe 生成部件网格及多视角预览 | `harness/blender_recipe.py` |
| OpenUSD | 写入普通物理资产和场景，收集交付依赖 | `harness/usd_writer.py`、`integration/bundle.py` |
| Artisan | 旧资产导出和冻结 v9 兼容构建 | `harness/geometry.py`、`construction/artisan_backend.py` |
| Isaac | 在独立运行环境执行已有受力、关节及传动检查 | `harness/physics.py` |
| COLMAP、3DGS | 可选的相机估计与背景训练；视觉背景与物理碰撞的验证状态分别记录 | `construction/reconstruction.py`、`train_worker.py` |

新适配层只消费已经生成的交付结果，不重新运行上述模型。ClawSpine 继续拥有
原生模型会话、观测和工具循环、G1 控制、人工停止及取消。当前接入后仍是
SceneCreator 生成阶段与 ClawSpine 机器人会话两个阶段；机器人失败还不会自动触发 SceneCreator 重建。

## 交付与启动代码

| 文件 | 行为 |
| --- | --- |
| `src/roboenv_perception/integration/cli.py` | 提供 `scenecreator generate/handoff/inspect/robot-scene/clawspine`；原来的 `scene-harness`、`scene-perception` 保留 |
| `integration/bundle.py` | 读取 `delivery.json`，调用独立 OpenUSD worker，保存资源清单与 SHA-256，校验交付包 |
| `integration/usd_worker.py` | 用 OpenUSD 查找本地依赖，按每个原始 layer 的锚点重写引用和贴图路径，重新加载并检查依赖与关节目标 |
| `integration/robot_scene.py` | 在原场景之外派生带静态地板的机器人测试场景，记录地板尺寸、接触参数假设和原交付哈希；新交付仍为 `unverified` |
| `integration/clawspine.mjs` | 调用外部 checkout 的 `scripts/launch_dsh_rsi.mjs`，复用真实 `rsiOptions` 和 `launchRsi` |
| `integration/runtime.mjs` | `--doctor` 检查实际构建产物、原生 DSH、指定镜像身份、GPU 与所选模型的凭据条件 |

交付包格式为 `envcreator.scene-bundle.v1`：

```text
bundle/
  manifest.json           # 文件哈希、原交付状态、USD 检查与源资源哈希
  metadata/delivery.json  # 原交付文件的完整字节副本
  metadata/assembly_rules.json  # 已知规格场景随包保存的蓝图和规则
  scene/
    scene.usda
    deps/                 # 实际引用的 USD layers、贴图等
```

只收集实际引用的资源，邻近的日志、配置或密钥文件不会随目录整包复制。所有 USD
layers 输出为文本 USDA，避免写入比仿真端更新的二进制 crate 格式；不转换单位，
只接受米制、Z-up、有有效 default prim 的场景。场景引用之外的刚体、碰撞或关节会被拒绝，
避免 ClawSpine 只引用 default prim 时漏掉实体。

原有部件与关节的组成关系保留，不做展平或额外的物理结构修复。缺失依赖、悬空关节目标、
被修改的资源、额外文件及符号链接会阻止交付。文本导出也不能保证 Isaac 支持所有源 USD schema；
仿真端的真实加载仍需单独执行。

`metadata/` 留在机器人运行之外。ClawSpine 现有本地 USD 入口只挂载 `scene/`，
因此原始任务审查、私有评估记录不随场景目录进入机器人运行容器。

## 使用

在当前仓库更新可编辑安装，Python 需要 3.12+：

```bash
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/scenecreator generate --help
```

多对象生成可用 `scenecreator generate` 或 `scene-harness`，所需配置见 [批处理 Harness](AGENTIC_HARNESS.md)。

交付包生成需要独立的 OpenUSD Python。将下例的 `--delivery` 和 `--usd-python` 换成实际交付记录和能导入 `pxr` 的解释器。

```bash
.venv/bin/scenecreator handoff \
  --delivery /absolute/path/delivery/delivery.json \
  --output outputs/new-clawspine-bundle \
  --usd-python /absolute/path/openusd-python

.venv/bin/scenecreator inspect --bundle outputs/new-clawspine-bundle
```

输出路径必须不存在。`delivery.json` 没有有效 `scene_usd` 时，会要求先完成场景生成。
原始交付是 `unverified` 的，可以打包作探索使用，但该状态会原样传递。

接入 ClawSpine 时，显式指定它的源代码 checkout，并使用原启动器要求的出生点与相机参数：

```bash
.venv/bin/scenecreator clawspine \
  --bundle outputs/new-clawspine-bundle \
  --clawspine-root /absolute/path/ClawSpine \
  --spawn 1,0,0,0 \
  --observer-eye 1,-4,2 \
  --observer-target 0,0,0.8 \
  --dry-run \
  --receipt outputs/new-clawspine-plan.json
```

原 Linux 主机 ClawSpine 源码位于 `/home/felixz/RoboHelix` 的现有 checkout 内；这里只引用其中
的原生启动器，不启动 RoboHelix campaign，也不导入其 evolution controller。
原接入记录绑定提交 `02cad34a60871d9b1a9d0a1916b10eb5e69ad098`。运行回执记录
checkout 提交、启动文件哈希、交付包 manifest 哈希、场景与指定坐标。

`--dry-run` 检查文件与真实启动器的参数解析，不启动 Isaac、DSH 模型或机器人。
`--doctor` 进一步检查当前主机的运行依赖，模型鉴权以实际调用结果为准。
上面的出生点与相机仅用于接口检查，尚未做防穿透、站立及可达性验证。

准备好 ClawSpine 自身所需的 Node 24、锁定的依赖、Isaac 容器和控制器权重后，
去掉 `--dry-run` 才会调用原生运行。Isaac 的使用条款及运行环境变量沿用外部启动器的要求。
`--model-provider`、`--model`、`--settings-file` 等配置原样交给 ClawSpine，适配层不另选模型。
新会话就绪后，在它的 run 目录写入 `envcreator-handoff.json`。启动不会自动提交机器人任务，
也没有添加一个代替原生 DSH 的聊天或控制循环。

现在也可追加 `--dsh-model-config configs/local-dsh-model.toml`，把独立中转配置传给原生 `llm-pi-ai`。原 Linux 主机 `gpt-6.1-sol` 已通过 Responses 协议完成一次 SceneCreator 插件调用；配置与验证范围见 [DSH 模型配置](DSH_MODEL.md)。后文 Qwen 运行属于历史实验，不是当前模型绑定。

## 机器人测试场景与历史运行

已有交付若缺少机器人站立平面，可通过独立命令派生带地板的测试场景。

```bash
.venv/bin/scenecreator robot-scene \
  --bundle outputs/new-clawspine-bundle \
  --output outputs/new-g1-test-scene \
  --usd-python /absolute/path/openusd-python \
  --floor-z 0 --floor-size 6 6
```

该命令保留原资源与世界坐标，仅新增静态测试地板。地板厚度 0.1 m、静／动摩擦
0.8／0.6、恢复系数 0 是测试假设，不是从视频估计的材质参数。
原交付的验证结论保留在 `upstream` 中，不会转为新场景的机器人验证结论。

ClawSpine 原启动器的镜像 ID 来自原部署机器，fresh clone 不包含该镜像。
若原 Linux 主机按它的 Docker 配方独立构建运行环境，可通过 `--arena-image-id sha256:...`
显式绑定新镜像。启动器逐字比较 Docker 返回的实际 ID，回执同时记录源代码和模型选择。
对应的外部源码补丁保存在 `integrations/clawspine/local-image-id.patch`；不会把仿真镜像纳入 Git。

原 Linux 主机新镜像标记为 `envcreator/arena-runtime:isaac5.1-aad4f25-rsl3.1.2`，实际 ID：

```text
sha256:d08db932be1f34d1b69162aef98845ed53197ed535fbc42b59d2e43a59bf24c6
```

它的父 Arena 镜像是 `sha256:784d1b8a6b67a1c9b005891f0ffa03ef6890baaf83da881fc310ca0613483a12`。
新镜像保留 26 层父文件系统前缀，并通过 Torch 2.7.0+cu128、NumPy 1.26.0、
RSL 3.1.2、TensorDict 0.14.2 的实际导入检查。基础镜像配置和全部压缩层均已按
NVIDIA 发布的 SHA-256 校验。源码固定不等于传递依赖全部固定，本轮实际 freeze
已存入实验目录；后续部署需记录自己构建的镜像身份。

本轮使用原生 `whole_body` 控制入口：G1 的具名上身关节目标加 Homie V2 平衡控制器，
不需要 GR00T 服务器与权重。物理后端沿用 ClawSpine 的 CPU PhysX，图像由 GPU 渲染。

## 历史运行记录

独立机器人 smoke 入口及专用实验已移除。历史镜像、模型和机器人运行结果见[清理前记录](https://github.com/FelixtheFelinae/SceneCreator/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/ENVCREATOR_CLAWSPINE.md)。这些记录属于原 Linux 主机，不能作为当前 Mac 环境已部署或通过物理验证的证明。

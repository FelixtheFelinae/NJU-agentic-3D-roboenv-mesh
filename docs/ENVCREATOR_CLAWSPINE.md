# EnvCreator × ClawSpine：场景与机器人运行能力的接入

记录日期：2026-10-28。

当前仓库暂定名为 **EnvCreator**，继续维护视觉重建、资产生成、装配语义及验证。
这一阶段接入 ClawSpine 的原生机器人运行入口。代码保留在当前仓库，不迁入 RoboHelix；
RoboHelix 的候选进化、研究记忆、调度和评测数据库尚未接入。

## 从现有生成流程到机器人运行

```text
描述 + 视频／多图片
  → EnvCreator：感知 → 证据 → 蓝图 → 几何 → 审查／修复 → 场景与物理检查
  → delivery.json + scene.usda
  → integration/bundle.py：场景交付包
  → integration/clawspine.mjs：ClawSpine 原生启动入口
  → 原生 DSH 会话、G1 观测、工具、控制与 Dashboard
```

`envcreator generate` 直接调用 `harness/cli.py:main`，执行的仍是
`harness/run.py:run_harness`，没有复制一套生成流水线。

| 已有方法 | 在 EnvCreator 中的环节 | 代码入口 |
| --- | --- | --- |
| VLM | 对象候选、实例语义、部件／关节蓝图、预览复审与修复建议 | `pipeline.py`、`construction/design.py`、`harness/geometry.py` |
| SAM3，或配置的 Grounding DINO + SAM2.1 | 对象可见区域的分割与证据裁剪 | `pipeline.py`、`video_pipeline.py` |
| Blender | 按几何 recipe 生成部件网格及多视角预览 | `harness/blender_recipe.py` |
| Artisan | 把部件与关节蓝图编译为 USD、URDF、MJCF | `harness/geometry.py` |
| Isaac | 在独立运行环境执行已有受力、关节及传动检查 | `harness/physics.py` |
| COLMAP、3DGS | 可选的相机估计与背景训练；视觉背景与物理碰撞的验证状态分别记录 | `construction/reconstruction.py`、`train_worker.py` |

新适配层只消费已经生成的交付结果，不重新运行上述模型。ClawSpine 继续拥有
原生模型会话、观测和工具循环、G1 控制、人工停止及取消。当前接入后仍是
EnvCreator 生成阶段与 ClawSpine 机器人会话两个阶段；机器人失败还不会自动触发 EnvCreator 重建。

## 新增代码

| 文件 | 行为 |
| --- | --- |
| `src/roboenv_perception/integration/cli.py` | 提供 `envcreator generate/handoff/inspect/robot-scene/clawspine/smoke`；原来的 `scene-harness`、`scene-perception` 保留 |
| `integration/bundle.py` | 读取 `delivery.json`，调用独立 OpenUSD worker，保存资源清单与 SHA-256，校验交付包 |
| `integration/usd_worker.py` | 用 OpenUSD 查找本地依赖，按每个原始 layer 的锚点重写引用和贴图路径，重新加载并检查依赖与关节目标 |
| `integration/robot_scene.py` | 在原场景之外派生带静态地板的机器人测试场景，记录地板尺寸、接触参数假设和原交付哈希；新交付仍为 `unverified` |
| `integration/clawspine.mjs` | 调用外部 checkout 的 `scripts/launch_dsh_rsi.mjs`，复用真实 `rsiOptions` 和 `launchRsi` |
| `integration/runtime.mjs` | `--doctor` 检查实际构建产物、原生 DSH、指定镜像身份、GPU 与所选模型的凭据条件 |
| `integration/smoke.mjs` | 给已就绪的原生会话提交一个自然语言任务，保存画面、原生轨迹和只读物理测量，结束时取消会话并调用独立停止入口 |

交付包格式为 `envcreator.scene-bundle.v1`：

```text
bundle/
  manifest.json           # 文件哈希、原交付状态、USD 检查与源资源哈希
  metadata/delivery.json  # 原交付文件的完整字节副本
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
.venv/bin/envcreator generate --help
```

生成命令与 [现有 Harness](AGENTIC_HARNESS.md) 相同，仅将 `scene-harness` 换成
`envcreator generate`。不提供隐式 VLM 模型、SAM 权重或 Artisan 环境。

交付包生成需要一个安装 `usd-core` 的独立 Python。当前主机已准备：

```text
/home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python
```

其他机器可单独创建环境并安装 `usd-core==26.11`；这只是 OpenUSD 文件处理环境，
不包含 Isaac、机器人控制器或模型。

以已有茶饮料瓶视频场景为例：

```bash
.venv/bin/envcreator handoff \
  --delivery outputs/harness-live-video-20261027-v5/delivery/delivery.json \
  --output outputs/new-clawspine-bundle \
  --usd-python /home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python

.venv/bin/envcreator inspect --bundle outputs/new-clawspine-bundle
```

输出路径必须不存在。`delivery.json` 没有有效 `scene_usd` 时，会要求先完成场景生成。
原始交付是 `unverified` 的，可以打包作探索使用，但该状态会原样传递。

接入 ClawSpine 时，显式指定它的源代码 checkout，并使用原启动器要求的出生点与相机参数：

```bash
.venv/bin/envcreator clawspine \
  --bundle outputs/new-clawspine-bundle \
  --clawspine-root /home/felixz/RoboHelix \
  --spawn 1,0,0,0 \
  --observer-eye 1,-4,2 \
  --observer-target 0,0,0.8 \
  --dry-run \
  --receipt outputs/new-clawspine-plan.json
```

本机 ClawSpine 源码位于 `/home/felixz/RoboHelix` 的现有 checkout 内；这里只引用其中
的原生启动器，不启动 RoboHelix campaign，也不导入其 evolution controller。
当前绑定的提交为 `02cad34a60871d9b1a9d0a1916b10eb5e69ad098`。运行回执记录
checkout 提交、启动文件哈希、交付包 manifest 哈希、场景与指定坐标。

`--dry-run` 检查文件与真实启动器的参数解析，不启动 Isaac、DSH 模型或机器人。
改用 `--doctor` 会进一步检查本机运行依赖，模型鉴权仍以真实调用为准。
上面的出生点与相机仅用于接口检查，尚未做防穿透、站立及可达性验证。

准备好 ClawSpine 自身所需的 Node 24、锁定的依赖、Isaac 容器和控制器权重后，
去掉 `--dry-run` 才会调用原生运行。Isaac 的使用条款及运行环境变量沿用外部启动器的要求。
`--model-provider`、`--model`、`--settings-file` 等配置原样交给 ClawSpine，适配层不另选模型。
新会话就绪后，在它的 run 目录写入 `envcreator-handoff.json`。启动不会自动提交机器人任务，
也没有添加一个代替原生 DSH 的聊天或控制循环。

现在也可追加 `--dsh-model-config configs/local-dsh-model.toml`，把独立中转配置传给原生 `llm-pi-ai`。本机 `gpt-6.1-sol` 已通过 Responses 协议完成一次 EnvCreator 插件调用；配置与验证范围见 [DSH 模型配置](DSH_MODEL.md)。后文 Qwen 运行属于历史实验，不是当前模型绑定。

## 机器人测试场景与原生任务

已有视频交付包含桌面和瓶子，没有供人形机器人站立的地板。用单独命令派生测试场景：

```bash
.venv/bin/envcreator robot-scene \
  --bundle outputs/new-clawspine-bundle \
  --output outputs/new-g1-test-scene \
  --usd-python /home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python \
  --floor-z 0 --floor-size 6 6
```

该命令保留原资源与世界坐标，仅新增静态测试地板。地板厚度 0.1 m、静／动摩擦
0.8／0.6、恢复系数 0 是测试假设，不是从视频估计的材质参数。
原交付的验证结论保留在 `upstream` 中，不会转为新场景的机器人验证结论。

ClawSpine 原启动器的镜像 ID 来自原部署机器，fresh clone 不包含该镜像。
若本机按它的 Docker 配方独立构建运行环境，可通过 `--arena-image-id sha256:...`
显式绑定新镜像。启动器逐字比较 Docker 返回的实际 ID，回执同时记录源代码和模型选择。
对应的外部源码补丁保存在 `integrations/clawspine/local-image-id.patch`；不会把仿真镜像纳入 Git。

本机新镜像标记为 `envcreator/arena-runtime:isaac5.1-aad4f25-rsl3.1.2`，实际 ID：

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

原生会话就绪后可独立提交测试任务：

```bash
.venv/bin/envcreator smoke \
  --clawspine-root /home/felixz/RoboHelix \
  --run-dir outputs/new-g1-run \
  --output outputs/new-g1-evidence \
  --task '先观察桌面并描述看到的物体，再保持站立缓慢抬一下右手，最后停下。'
```

`smoke` 只提交用户任务，通过 ClawSpine 自身的模型与机器人工具执行。
外层读取相机、关节和控制回执，不写机器人目标、场景物体位姿或环境关节。
输出包含 `definition.json`、`native-trajectory.json`、动作前后 PNG、audit 测量和
`result.json`。`turn_completed` 表示模型回合结束，`physical_quiescence` 表示原生回执／
实际速度满足停止条件；两者均不能单独作为自然语言任务完成的结论。

## 本轮实际检查

针对依赖打包与原生启动器边界的命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
ENVCREATOR_USD_PYTHON=/home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python \
ENVCREATOR_CLAWSPINE_ROOT=/home/felixz/RoboHelix \
.venv/bin/pytest tests/test_clawspine_integration.py \
  tests/test_harness_scene.py tests/test_harness_run.py -q
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 用于避开本机 ROS 的无关 pytest 插件，不改变产品运行。
测试用真实 OpenUSD worker 检查文本／二进制 USD、贴图和关节在搬移且删除原场景后仍可解析；
并检查缺失资源、错误单位、悬空关节、状态被擅自改写和文件被修改等失败情况。
原生边界测试实际调用外部 `rsiOptions`，检查场景、坐标及显式模型选择的传递。
上述命令本轮结果为 **29 passed**，包括派生地板后原始几何变换及关节目标保持一致的检查。
`node --test tests/test_clawspine_smoke.mjs` 的两个测试另检查缺失测量或未释放的控制器不能被记为已停止。新增模块 Ruff 检查通过；构建 wheel 后确认
`envcreator` 入口、JavaScript 适配器及独立 USD worker 都进入安装包。

真实生成结果 `outputs/harness-live-video-20261027-v5/` 已用于交付 smoke：
一个刚体、零个关节，任务是重建封闭茶饮料瓶并验证桌面推碰。交付包及启动参数回执位于
`outputs/clawspine-handoff-20261027/video-v5-portable/`，启动参数回执为同目录上一级的
`portable-plan.json`，不进入 Git。原结果的 `verified` 仅表示那一轮
生成流程的门禁通过，本次打包不会追加物理或机器人成功结论。

原生 DSH 已用本项目既有的 `qwen3.7-plus` 路由完成一次真实模型连通性检查，
证据位于 `outputs/clawspine-g1-smoke-preparation/model-only/`。ClawSpine 的默认订阅模型
在本机缺少其原生登录，因此本轮显式选择现有 Qwen 配置；不是静默回退，也未借用外层
Codex 的凭据。该检查没有连接机器人，不能作为 G1 工具或物理动作成功的依据。

已用上述茶饮料瓶场景完成两轮原生 G1 探索测试。运行环境为本机新构建的 Isaac Sim 5.1、
锁定源码版本的 Arena／Isaac Lab、43 自由度 G1、Homie V2，原生模型是 `qwen3.7-plus`。

| 实际记录 | 第一轮 `native-v1` | 第二轮 `native-v2` |
| --- | --- | --- |
| 指令 | 观察桌面、缓慢抬右手、停止 | 观察桌面、举右手到肩部附近、动作后重新观察确认、停止 |
| 原生工具 | 相机、第三视角、身体状态、一次 whole-body 动作、idle | 相机、第三视角、身体状态、两次 whole-body 动作、动作后复查、idle |
| 右腕世界高度 | 0.816 → 0.806 m | 0.816 → 1.084 m |
| 物理结论 | 关节动作执行，但右臂向后摆，未完成抬手 | 实际画面与 0.267 m 高度增加支持右手抬高；骨盆高度保持约 0.756 m |
| 结束 | 实测静止、Core 释放、容器与 DSH 关闭 | 实测静止、Core 释放、容器与 DSH 关闭 |

第一轮模型根据关节跟踪声称抬手成功，外层查看真实图像与腕部测量后没有采纳该结论。
第二轮保留相同模型、场景、出生点、控制器和源码，只修改公开语言任务，要求执行后复查。
没有向模型指定关节符号或具体动作解法。两轮是不同指令的探索记录，不计算成功率，
也不作为机器人操作 benchmark 的准入结论。

实际记录位于 `outputs/clawspine-g1-smoke-20261028/`：

```text
preparation-v2.json / preparation-v3.json  # 两轮任务与代码／模型／场景条件
runtime/                                 # 镜像身份、构建日志、实际 Python freeze 和依赖检查
native-v1/ / native-v2/                   # 原生 DSH、仿真事件／audit、运行与释放回执
smoke-v1/ / smoke-v2/                     # 前后相机、轨迹、测量、独立 review.json
g1-before-after.png                      # 第二轮实际前后帧的并排图
```

执行结束后，两个运行目录的 `cleanup` 均记录 `ownedContainersCleaned=true`、
`physicalDrainObserved=true`、`dshStopped=true`，没有强制杀死 DSH。
Docker 中两个运行身份对应的容器均已移除，GPU 恢复约 453 MiB、0% 利用率。
保留已安装镜像及缓存便于重用，没有让主机休眠或关机。

当前相机画面对比度较低，瓶子仅能支持粗略类别描述；尚未验证品牌、材质或精细视觉识别。
没有执行接触抓取、工具使用或小车拆螺丝。自行车／小车的装配几何与简化紧固件状态，
不会因这个适配层而自动具备机器人可执行的松脱行为。

后续给小车选择一个固定连接，准备螺丝刀及可达的操作位姿，补齐受力、松开及分离逻辑。
EnvCreator 与机器人运行之间的反馈修复、RoboHelix 外层进化后续再接。

### 电动螺丝刀的下一阶段场景（2026-10-28）

EnvCreator 已生成电动螺丝刀，并用同一 USD 完成已知坐标、理想 XYZ 导轨下的对中、六角啮合与旋松实验；
32 项物理检查通过，含偏心、倾斜、间隙、错规格和停机对照。记录和命令见
[电动螺丝刀实验](../experiments/electric_screwdriver/README.md)。该实验独立于原生机器人会话。

`outputs/screwdriver-feasibility-20261028-v4/g1-bundle/` 已准备桌面、工具支架及金色六角目标，
依赖和原生启动参数 dry-run 通过。工具电机初始关闭，目标暂为运动学刚体，用于抓取和对准；
没有启动新的 G1 任务。原生环境尚未托管本轮啮合/螺纹运行循环，也没有暴露电动工具开关。
先验证 G1 的抓取和对准，再接通上述环境运行接口并换用动态螺丝后，才进入机器人旋松实验。

工具资产开发同样沿用 DSH，使用 `envcreator dsh-tool-profile` 生成普通插件配置，
通过原生 `--native-extra-patch` 加载。`envcreator_*` 工具复用现有资料、生成、理想验证与打包，
保存调用前后状态、native 调用 ID 及失败；模型、历史和取消仍归 DSH。
资产开发可用仅注册相关工具的 `envcreator-asset` preset；机器人会话继续用原生机器人 preset。
后续已完成一次原生 DSH 资产预检：`qwen3.7-plus` 实际调用检查、官方资料读取、理想台架验证、证据复核及 G1 场景准备五个插件工具；32 项物理检查通过。记录位于 `outputs/screwdriver-dsh-preflight-20261028-v1/`，未执行 G1 或提交完整机器人拆卸任务。模型最终陈述中的产品参数与资产能力混淆另存复核，详细接口和限制见上述实验文档。

这次 Qwen 路由复用了项目 `.env.local` 的视觉 `VLM_API_KEY`，不是 ClawSpine 自带的模型凭据。后续按用户要求将 DSH 任务推理与视觉模型分开，待接入中转 6.1-sol 的准确模型 ID、接口协议及独立凭据；不继续复用视觉密钥，也不自动回退到 Qwen。已有模型和运行记录保留。

调用边界为：ClawSpine 原生 DSH 使用独立任务模型，调用 EnvCreator 插件；只有 EnvCreator 内部需要视觉理解时，才由其 VLM 模块调用 Qwen。Qwen 不作为 ClawSpine 的通用视觉或控制后端。这里明确的是后续配置约定，历史预检曾将 Qwen 直接用于 DSH，记录保留；待接入的中转模型尚未配置或验证。

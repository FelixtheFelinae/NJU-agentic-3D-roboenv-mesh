# 电动螺丝刀：EnvCreator 的第一个机器人工具资产

本次生成一把直柄电动螺丝刀，外形参考 [Bosch GO 官方产品页](https://www.bosch-professional.com/gb/en/products/bosch-go-gen3-06019H2201)。蓝色机壳、深色握柄、开关、调节环、前端照明环、夹头和批头保留可辨识外形。电池、电机和减速器合并在机身内，主轴和已安装批头作为一个可连续旋转的刚体。

这是根据语言要求和制造商资料制作的近似工具，尺寸、质量分配、摩擦和电机参数均包含明确假设。没有将它认定为 Bosch 实物复刻，也没有将其批头认定为已经匹配小车螺丝。

## 代码与生成流程

```text
envcreator tool electric-screwdriver
  → harness/tooling.py:collect_tool_reference：获取官方页面，记录来源和接口证据
  → tools/electric_screwdriver.py：参考资料、Blueprint、GeometryRecipe、工具语义
  → harness/geometry.py:build_recipe
  → Blender：封闭网格、材质、多视图
  → Artisan：部件树、连续关节、碰撞分解、USD/URDF/MJCF 导出
  → harness/usd_adapter.py：保持静止位姿，规范化刚体层级
  → tools/power_tool_usd.py：工具驱动、摩擦、惯量、抓持/TCP 坐标
  → harness/tooling.py:test_tool：对准、旋松和五组反例，绑定资产及代码哈希
  → envcreator handoff：检查并打包自包含 USD
```

这一轮由 Codex 根据用户要求和官方资料编写声明式配方，再实际调用现有 EnvCreator 的 Blender/Artisan 流程。没有另外运行 VLM API、SAM、COLMAP 或 3DGS；本次输入也不涉及视频分割或场景背景。通用视频 Harness 尚未自动具备任意工具的生成能力。

新增入口在 `src/roboenv_perception/integration/cli.py`。制作配方在 `src/roboenv_perception/tools/electric_screwdriver.py`，USD 工具运行接口在同目录的 `power_tool_usd.py`。本目录的 `validate_isaac.py` 负责电机台架；产品代码中的 `tools/isaac_screwdriver_worker.py` 负责啮合与旋松验证。`render.py` 绘制资产展示图，`render_interaction.py` 重放实际 PhysX 位姿。

工具专用 Harness 目前只注册了电动螺丝刀，资料获取是已知官方 URL 的受限读取，检查型号与 1/4 英寸接口。抓取文本不作为指令执行，也没有自动从页面反演尺寸或动力学参数；数值仍来自配方中的显式假设。其他工具需提供自己的参考、生成配方与功能测试，不能直接继承本工具的验证结论。

本轮同时补齐了两个通用接口：

- Blueprint 的关节可声明带来源的 `effort_limit` 和 `velocity_limit`，连续旋转关节向 Artisan 传递力矩/速度上限，不设置角度止挡。未给出上限时明确报错。
- GeometryRecipe 的几何体可声明 `matte_plastic`、`rubber`、`steel`，传入 Blender 和 Artisan。材质类别不等于实测摩擦；本工具的物理摩擦仍单独记录为先验。

## 资产内容和简化

| 内容 | 本次实现 |
| --- | --- |
| 外形 | 直柄工具，含批头全长约 220 mm，最大直径约 43 mm，均为估计 |
| 物理部件 | `housing` 机身与 `spindle` 主轴；28 个闭合几何体构成外形 |
| 质量 | 机身 0.300 kg、主轴及批头 0.020 kg；分配和惯量是建模假设 |
| 批头 | 6.35 mm 对边六角柄、3 mm 对边六角工作端；尺寸暂未匹配小车 |
| 轴承 | 假设的简化滑动轴套，局部径向几何间隙 50 μm；由理想关节承担导向 |
| 运动 | 一个无角度限位的转动关节，工具前方为 +Y |
| 电机 | 理想速度驱动，默认 30 rpm，可反转，命令范围 ±180 rpm，力矩上限 0.20 N·m |
| 抓持 | 机身上的候选抓持坐标和轴向区域；未验证 G1 手指包络、握力和滑移 |
| TCP | 刀尖位于主轴坐标 `(0, 0.050, 0)`，静止时机身坐标 `(0, 0.130, 0)` |

制造商页面给出的空载转速为 306–396 rpm，最大硬/软连接力矩为 5/2.5 N·m，接口为 1/4 英寸内六角。本次低速、低力矩设置用于第一轮操作实验，不是对该产品电机性能的辨识。页面上的 122 × 240 × 89 mm 是包装尺寸，没有用于模型尺寸。

机壳没有固定到世界。电机通过两刚体间的 USD angular drive 施力，机身承受反作用。主轴与机身相互碰撞关闭，由关节和轴套几何表达支撑；两者对外部手指、桌面、螺丝仍保留碰撞。Artisan 分解出的凸碰撞块被保留，轴套和鼻端的通孔没有整体改成实心凸包。

按钮、照明环及充电口目前是固定外观细节。机器人通过明确的电机命令启动工具，跳过按按钮这一动作。模型不包含真实电磁、减速器、离合器、热或电池行为，也不会对准后自动吸附到螺丝。

## 使用

新建输出目录，复用本机已经安装的环境：

```bash
.venv/bin/envcreator tool electric-screwdriver \
  --output outputs/electric-screwdriver-new \
  --artisan-python /home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python \
  --blender /home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender \
  --isaac-python /home/felixz/.local/share/roboenv/isaac-env/bin/python

/home/felixz/.local/share/roboenv/isaac-env/bin/python \
  experiments/electric_screwdriver/validate_isaac.py \
  --asset outputs/electric-screwdriver-new/build/asset/electric_screwdriver.usda \
  --output outputs/electric-screwdriver-new/motor-physics

.venv/bin/envcreator handoff \
  --delivery outputs/electric-screwdriver-new/delivery.json \
  --output outputs/electric-screwdriver-new/bundle \
  --usd-python /home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python
```

新的组合入口将资产写到 `build/asset/electric_screwdriver.usda`，语义在 `build/asset/tool.json`，同时嵌入 USD 的 `envcreator:toolSpec` 属性；资料在 `research/`，功能验证在 `feasibility/`。不传 `--isaac-python` 时不运行功能实验，状态保留 `unverified`。请求了验证但未通过时命令返回非零状态。

早期直接调用 `build_tool` 的 `electric-screwdriver-20261028-v4` 保持原来的 `asset/` 和 `geometry/` 层级。本次功能实验复用了这份未修改的 USD，没有重新生成候选资产。打包后几何、物理和语义均在 `bundle/scene/scene.usda` 中，不依赖本机外部网格路径。Artisan 原始 URDF/MJCF 仍在 `build/geometry/export/`，本轮只给最终 USD 增加了电机与工具语义，不能把原始导出物当成等价的电动工具运行包。

将工具引用到仿真场景的 `/World/Tool` 后，环境中的工具控制适配器可以调用：

```python
from roboenv_perception.tools.power_tool_usd import set_motor

# stage 是仿真正在使用的 USD stage；仿真步推进实际运动。
set_motor(stage, "/World/Tool", rpm=30, enabled=True)
set_motor(stage, "/World/Tool", rpm=-30, enabled=True)
set_motor(stage, "/World/Tool", rpm=0, enabled=True)  # 有限力矩主动制动
set_motor(stage, "/World/Tool", enabled=False)       # 断驱动，允许惯性滑转
```

该模块仅依赖标准库与 OpenUSD，也可单独复制到独立 Isaac Python 环境中导入。RPM 转换为 USD 的度/秒，30 rpm 对应 180 deg/s；状态读取仍采用弧度。正方向遵循朝刀尖 +Y 轴的右手规则，是否对应某个螺丝的旋松方向需要在连接语义中确定。

接口只改变关节驱动参数，没有直接写主轴姿态或螺丝状态。后续 ClawSpine 需要增加对应的工具命令，以及抓持/对准条件；目前原生 Agent 工具列表中还没有电动螺丝刀开关。

## 资产与电机台架结果

最终候选：`outputs/electric-screwdriver-20261028-v4/`。制造商页面、图片、文本和哈希在 `evidence/`；原始配方、编译报告和展示文件在 `geometry/`。

Isaac CPU PhysX 使用 1/480 s 步长，加载实际交付 USD。实验包含固定机身的电机台架、关闭重力的自由工具反力检查和正常重力下的桌面落放。没有运行时写入刚体或关节姿态。

| 实验 | 实测结果 |
| --- | --- |
| 连续正转 2.5 s | 约 7.855 rad，超过一整圈；稳定转速约 30.000 rpm |
| 反转 1 s | 约 −3.142 rad，稳定转速约 −30.000 rpm |
| 施加 0.05 N·m 反向负载 | 仍正转，约 29.833 rpm |
| 主动制动 | 测得角速度接近零；断驱动时保留惯性滑转 |
| 自由工具反作用 | 主轴约 +3.121 rad/s，机身约 −0.0208 rad/s；轴向角动量平衡检查通过 |
| 桌面落放 | 机身原点最终高度约 20.24 mm，稳定性检查通过 |

14 项检查全部通过。原始转角和展开后的累计转角均保存，并用角速度积分交叉检查。力矩上限已在驱动中设置，本轮反向负载实验低于该上限，尚未专门测量堵转或离合打滑。

展示图来自生成网格的 Blender 渲染；`previews/exploded.png` 将主轴平移 55 mm 以展示两个刚体，不能作为可拆卸路径证据。

## 理想对准与旋松实验

本次输出为 `outputs/screwdriver-feasibility-20261028-v4/`。使用原工具 USD，SHA-256 为 `22bb962f1c8cc5442a2d3ab6ad6f32df31428ca39d4b539402829b298be752fa`。实际运行命令：

```bash
.venv/bin/envcreator tool-test \
  --asset outputs/electric-screwdriver-20261028-v4/asset/electric_screwdriver.usda \
  --output outputs/screwdriver-feasibility-20261028-v4 \
  --isaac-python /home/felixz/.local/share/roboenv/isaac-env/bin/python
```

Isaac CPU PhysX 步长 0.5 ms，运行 12.5 s。工具装在受力驱动的 XYZ 导轨上，姿态与目标轴向预先给定，从横向误差 7.21 mm、刀尖高于孔口 12 mm 开始；导轨使用目标坐标对中、插入并跟随螺丝退出。环境关闭重力以隔离啮合和螺纹动力学。仿真过程中没有直接改写刚体位姿、主轴转角或螺丝状态；它验证的是已知目标坐标下的功能可行性，不包含视觉定位或机器人对准策略。

连接由两个独立模块执行：

- `tools/screwdriving.py`：根据批头/孔尺寸、径向误差、轴线、插入深度和初始六角相位决定是否啮合；啮合后用有上限的扭转弹簧和阻尼传递等大反向力矩。3 mm 批头配 3.1 mm 六角孔，实际径向门限为尺寸间隙的一半，即 50 μm；轴线和初始相位各允许 3°，插入深度为 0.5–2.5 mm。
- `tools/fastener.py`：从已有小车台架的简化力学代码提取，按螺距约束转角与轴向退出，包含旋松阻力及解除螺纹的状态转换。螺距 0.8 mm、啮合长度 4 mm、起动阻力 0.002 N·m、运行阻力 0.0008 N·m 均为实验假设。`clamped` 仅表示初始就位与旋松阻力，并未计算实际预紧力。

目标为本轮选定的理想六角螺丝，不是从小车视频确认的紧固件。工具与目标螺丝这一对的实体碰撞明确关闭，由上述啮合模型替代，避免实体接触与虚拟传动重复施力；与支座等其他物体的碰撞保留。实验没有解析真实六角接触、螺纹接触或摩擦自锁，也未验证最终 18 mm 的完全移除状态。

| 用例 | 结果 |
| --- | --- |
| 已知坐标自动对中、插入，−30 rpm 驱动 | 0.558 s 啮合；约 5.007 圈，轴向退出 4.104 mm；11.007 s 解除螺纹约束 |
| 保持 4 mm 偏心 | 无啮合、无传递扭矩、无旋松 |
| 轴线倾斜 12° | 无啮合、无传递扭矩、无旋松 |
| 保持 6 mm 间隙 | 无啮合、无传递扭矩、无旋松 |
| 孔对边改成 4 mm | 规格门限拒绝啮合，无传递扭矩、无旋松 |
| 对中并插入，电机关闭 | 仍啮合约 11.94 s，螺丝无旋松 |

32 项检查全部通过，包含反例确实施加了偏移/倾角/间隙/错规格的检查。最大传递力矩约 0.00275 N·m；螺纹有效阶段的最大转角—位移耦合残差约 4.67 μm。`interaction/report.json`、`trace.json`、`request.json` 和 `initial_scene.usda` 保存数值、初始条件、假设及代码/资产哈希，`validation.json` 绑定报告哈希并记录进程正常退出。

本地状态为 `ideal_tool_verified`。这一状态只覆盖资料与理想工具台架；组合交付的整体状态仍为 `unverified`。门禁核对必需用例、检查结果及源资产哈希，不是另一套独立物理求解器。

`replay-v3/` 使用生成网格和实际 PhysX 轨迹重放刀尖及螺丝位姿，支持重现对中、旋转和退出画面；它是 Blender 轨迹重放，不是机器人运行录像。灰色支座仅在显示网格中做并集，以去除重叠静态表面的渲染瑕疵。

可视化入口：

```bash
/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender \
  -b --threads 4 --python experiments/electric_screwdriver/render_interaction.py -- \
  --visual outputs/electric-screwdriver-20261028-v4/geometry/visual \
  --experiment outputs/screwdriver-feasibility-20261028-v4/interaction \
  --output outputs/screwdriver-feasibility-20261028-v4/replay-new

.venv/bin/python experiments/electric_screwdriver/compose_replay.py \
  --replay outputs/screwdriver-feasibility-20261028-v4/replay-new
```

`summary.png` 为前/中/后截图，`interaction.gif` 为带实测数值的重放，`recording.json` 记录轨迹、报告和可视化代码哈希。组合画面左侧保留工具初始全貌，右侧随轨迹更新。渲染脚本不参与物理门禁判定。

## G1 工作台准备

`tools/prepare_g1_workcell.py` 校验上述验证与报告哈希，复制同一工具，在 0.75 m 桌面上布置两端支架与金色六角目标。工具电机初始关闭，目标暂设为运动学刚体，供下一阶段抓取和对准使用。没有生成机器人、绑定手掌或预置任务成功。

```bash
/home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python \
  src/roboenv_perception/tools/prepare_g1_workcell.py \
  --validation outputs/screwdriver-feasibility-20261028-v4/validation.json \
  --output outputs/screwdriver-feasibility-20261028-v4/g1-workcell

.venv/bin/envcreator handoff \
  --delivery outputs/screwdriver-feasibility-20261028-v4/g1-workcell/delivery.json \
  --output outputs/screwdriver-feasibility-20261028-v4/g1-bundle \
  --usd-python /home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python
```

以上目录已生成。`g1-bundle` 的 USD 依赖闭包、哈希和原生 ClawSpine 启动参数检查通过，记录为 `g1-launch-check.json`；只进行了 dry-run，没有启动机器人任务。公开任务为“请用右手拿起桌上的电动螺丝刀，将刀尖对准金色螺丝的六角孔，保持对准。”

上句是既有场景包中保存的准备任务，原记录保留。后续完整实验采用用户确定的原始任务“让机器人抓握这把螺丝刀 尝试拧松并取下螺丝”，正文见 [public-task.md](public-task.md)，规划见 [EXPERIMENT_PLAN.md](EXPERIMENT_PLAN.md)。这条任务需原生电机、动态紧固件和取出行为；当前场景包尚未具备完整执行条件。

下一轮先验证 G1 的接近、抓持和对准，再将电机命令与本轮啮合/螺纹运行循环接入原生环境，替换运动学目标后执行旋松。若先采用手掌固定夹持，需单列为理想持工具实验；当前尚未启用该选项。真实握持稳定性、接触压紧和小车螺丝兼容性都保留为待验证。

## 沿用 ClawSpine 的 DSH 插件流程

后续 Agent 的模型调用、会话、轨迹和取消都由原生 DSH 执行。EnvCreator 只通过 `integration/tool_plugin.mjs` 注册工具接口，`tool_backend.py` 调用上文已有资料、生成、验证和打包模块。没有另建 Agent 聊天循环。

| DSH 插件工具 | 实际调用及产物 |
| --- | --- |
| `envcreator_inspect` | 读取绑定资产哈希、现有证据位置及已配置能力 |
| `envcreator_research` | 受限读取官方页面，保存来源和接口证据 |
| `envcreator_review` | 核对已有验证、报告、代码、初始场景和资产哈希；明确返回本次未运行仿真 |
| `envcreator_validate` | 调用同一理想台架 worker，执行正反例；保存完整实验 |
| `envcreator_build` | 通过 Blender/Artisan 创建新候选，收集资料并按配置验证；不替换绑定资产或正在运行的场景 |
| `envcreator_prepare_g1` | 核对可复用证据后准备新的抓取/对准场景和包；不启动或操控机器人 |

用专用入口生成 DSH 插件配置：

```bash
.venv/bin/envcreator dsh-tool-profile \
  --asset outputs/electric-screwdriver-20261028-v4/asset/electric_screwdriver.usda \
  --validation outputs/screwdriver-feasibility-20261028-v4/validation.json \
  --output outputs/new-dsh-tool-profile \
  --usd-python /home/felixz/.local/share/roboenv/envcreator-usd-env/bin/python \
  --isaac-python /home/felixz/.local/share/roboenv/isaac-env/bin/python \
  --artisan-python /home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python \
  --blender /home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender
```

将生成的 `envcreator.patch.yml` 传给已有 `envcreator clawspine ... --native-extra-patch /绝对路径/envcreator.patch.yml`，沿用原生启动器。工具资产开发也可在同一 DSH 中用 `envcreator-asset` preset 建普通会话；该 preset 只注册上述插件工具，不注册 shell、文件写入或机器人工具。原生机器人会话继续使用 ClawSpine 的观察、控制、停止及设置，不切换成资产开发 preset。

每次插件调用在 `calls/plugin-events.jsonl` 记录 native `call_id`、`agent_id`、开始/完成/失败及结果哈希；具体目录保存 `before.json`、`after.json`、`result.json` 或 `failure.json`、运行回执和实验报告。DSH 自己保存模型请求、插件调用与返回；这些本地记录是补充证据，不是伪造的 DSH 历史。取消向本次 worker 进程组传递，保留部分记录，不自动重试。

最初使用固定的 DSH 0.2.1-alpha.1 完成插件加载、Web 认证和 `envcreator-asset` 会话创建，见 `dsh-profile-check/native-load-v3.json`。首次 preset 验证因临时工具层没有全局工具而失败；保留失败日志，改为在该 preset 内注册局部工具后创建成功。真实 Python 后端的调用关联、失败保留及证据复核分别通过回归测试。该阶段没有派发模型回合；后续原生执行另存记录如下。

插件的 `--isaac-python` 绑定用于 EnvCreator 独立工具台架；G1/Arena 仍沿用原生启动器的固定容器。资产开发报告不进入正式机器人评测的私有判定接口。当前插件没有实时电机控制或原生环境螺纹运行接口，因此仍不能宣称 G1 已能完成旋松。

后续实际执行前发现，早期 `dsh-plugin-ready` 将虚拟环境 Python 启动路径解析到了基础解释器，导致配置中的环境找不到项目、OpenUSD 和 Isaac 模块。已修正 `prepare_profile`，保留传入的虚拟环境启动路径；旧记录保留。当前可用配置为 `dsh-plugin-runtime-v2/envcreator.patch.yml`，`runtime-check.json` 记录四个环境的模块发现检查，以及真实后端 `inspect`、`review` 调用通过。该检查没有启动物理实验或 DSH 模型回合。`task-request.json` 保存用户完整任务及尚缺的原生执行能力。

2026-10-28 已通过 `run_native_asset.mjs` 提交一次真实资产预检，由原生 DSH 自行调用 `inspect`、`research`、`validate`、`review` 和 `prepare_g1`。实际模型为 `envcreator-qwen/qwen3.7-plus`，使用原项目 `.env.local` 的 `VLM_API_KEY` 连接 DashScope；这是接入时复用的视觉模型凭据，不是 DSH 自带密钥。新运行的六组理想台架实验通过 32 项检查，worker 和 DSH 正常退出；原生轨迹和逐次调用证据位于 `outputs/screwdriver-dsh-preflight-20261028-v1/` 及其引用的插件调用目录。G1 未运行，完整机器人任务未提交。

复核未采纳模型回复中的部分能力描述：制造商的七档机械离合并未在资产中实现；1/4 英寸是批头柄接口，实际工作端为 3 mm 六角；解除螺纹不等于完整取下。详见该运行的 `review.json`。API、插件和物理验证跑通，不代表模型最终解释全部准确。

后续 DSH 任务推理改接用户计划提供的中转 6.1-sol，准确模型 ID、地址、协议及独立凭据待提供。视觉 VLM 保持单独配置；不再用 `VLM_API_KEY` 发起 DSH 推理，也不自动回退到 Qwen。

逐次修复记录见 [WORKFLOW.md](WORKFLOW.md)。

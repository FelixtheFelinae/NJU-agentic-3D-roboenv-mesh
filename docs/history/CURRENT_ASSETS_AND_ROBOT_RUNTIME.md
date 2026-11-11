# 当前资产与机器人运行框架盘点

盘点日期：2026-10-26。检查对象为当前工作区（含未提交代码与本地 `outputs/`），不是仅检查 Git HEAD。
本次读取代码、蓝图、实际 USD 结构、已保存验收与失败记录；未重新运行物理仿真。
排除 Kit 缓存、依赖与模型权重的内容扫描。历史目录和网格部件不按独立资产重复计数。

结论：已有 **7 类可交互物体**，另有桌面、笔记本支架、砖地等静态设施，以及引入的官方 G1 机器人。
其中笔记本合盖与鼠标拖动经过 G1 实际接触验证；其余对象的机器人任务尚未验证。
本项目有一套固定基座 G1 脚本控制链，尚未集成 ClawSpine 类通用机器人 harness。

## 1. 物体、动态内容与可用程度

自由刚体可整体平移/旋转；“零内部关节”不表示物体不能动。这里的关节数只计内部活动机构。
质量、摩擦、不可见几何等大量参数为先验，不能把仿真通过当成真实参数辨识。

| 物体 | 实际物理结构与动态内容 | 已有证据 | 机器人使用结论与限制 |
| --- | --- | --- | --- |
| 封盖瓶子 | 1 刚体，0 内部关节，质量约 0.060 kg；整体接触、移动、倾倒 | 落稳、支撑接触、外力倾倒超过 45° | 可作为推/碰/拿取任务的物理候选；没有机器人抓取成功证据；没有液体、旋盖或可拆瓶盖 |
| 14 英寸 MacBook Pro | base/lid 两刚体，约 1.15/0.45 kg；1 被动铰链，0–2.4 rad（约 137.5°）；有摩擦、阻尼、限位，整机基座未焊接到支架 | 外力矩开合、释放、限位；G1 从约 128.8° 合到约 0°，右手—屏幕峰值接触力约 9.55 N | 可用于合盖接触实验。小开角约 15°/30°/35° 保持测试失败，会因重力继续下落；不能宣称全角度悬停 |
| 鼠标 | 1 刚体，0 内部关节，约 0.141 kg；整体接触、滑动、受向上外力抬起 | 推动约 15.8 mm；G1 实际拖动阶段约 45.1 mm、持续接触约 2.69 s，仍在桌面 | 已有 G1 推拖交互证据；按键/滚轮固定，没有灵巧抓取或点击功能验证 |
| iPad Pro 12.9 2021 + 保护套 | 1 刚体，0 内部关节，约 0.85 kg；保护套与折页结构固定，整体可动 | 白桌新版外力推动约 20.3 mm、落稳、独立重载 | 可作为平板状可搬运对象；没有机器人拿取/翻盖/触控验证 |
| Sony WH-1000XM6 头戴耳机 | 1 刚体，0 内部关节，约 0.254 kg；Blender 新版 8 个视觉网格仍属于一个刚体 | 新版实际外力推动约 19.7 mm、落稳、独立重载 | 可作为推/抓取的物理候选；头梁、耳罩、耳垫不会单独伸缩、旋转或折叠，没有机器人抓取验证 |
| Huawei FreeClip 充电盒 | base/lid 两刚体，约 0.04/0.01 kg；1 被动铰链，0–2.2 rad（约 126.1°），场景初始约 1.75 rad；耳机固定于 base | USD 结构、落稳、重载通过；旧测试直接把关节设为 1.5 rad 后检查有限/范围 | 有可动盒盖结构，但测试不是力驱动开合，尚未证明手部能够开盖；耳机不可单独取出 |
| 黄色自行车（链传动版） | 5 刚体，约 22.3 kg；转向、前轮、后轮、曲柄共 4 关节。后轮与曲柄原生双向耦合，后轮角 = 2.5 × 曲柄角，内部独立运动自由度为 3；转向 ±0.65 rad | 台架驱动/断开/负载反传；4 Nm 输入 1 s 后释放 2 s，在被动导轨上前进约 0.8767 m，前后滚动误差约 1.2 cm；独立重载 15 项检查通过 | 可作为传动和受力响应资产；没有机器人踩踏或自由骑行验证。链环是静态视觉包络，传力由原生约束承担；固定齿比，无飞轮/逐节碰撞/刹车/平衡控制。曲柄及后轮运行边界 ±1000 rad；8 Nm 未通过原定滚动验收 |

实际 USD 的 joint axis 属性可能是 Z：导出时轴向通过 `localRot0/localRot1` 转换。
不能脱离 joint frame 直接将其解释为世界 Z 轴，蓝图中的 X/Y 轴与导出的局部 Z 表示可以等价。

## 2. 推荐加载入口与版本

| 场景/资产组 | 入口 | 采用原因 |
| --- | --- | --- |
| 木桌 + 瓶子 + 笔记本 + 鼠标 + 支架 | `outputs/user-scene-assets-20261023/simulation/scene.usda` | 实际 USD 物理检查 11/11，新进程重载 5/5，独立对象/场景 Review；G1 操作使用的源场景 |
| 白桌 + 平板 + Blender 耳机 + FreeClip | `outputs/white-table-redo-20261024/simulation-blender-20261026/scene.usda` | 最新耳机曲面版本；物理 8/8、重载 3/3，但其中 FreeClip 判据仅证实直接设角后的状态 |
| 自行车 + 砖地（无测试夹具） | `outputs/bicycle-chain-20261026/delivery-v1/scene.usda` | 最新链传动资产；静态组合包装检查通过。动态证据来自下面的夹具场景 |
| 自行车传动诊断夹具 | `outputs/bicycle-chain-20261026/simulation-v6/scene.usda` | 包含台架与被动导轨；`reload-v3/validation.json` 是有效独立重载验收 |
| 旧版白桌场景 | `outputs/ipad-audio-20261023/simulation-v8/scene.usda` | 保留的旧版本，耳机几何较粗；不是额外三类新物体 |

每个场景的 `asset/`、`assets/`、贴图等依赖须随入口保留。单物体可从对应场景的
`assets/<物体名>/object.usd` 加载；优先使用经 Isaac 适配与验证的 USD，不能仅因同时导出
URDF/MJCF，就宣称其他后端已通过同等动力学验收。

## 3. 静态设施、背景及机器人模型

- **木桌：** 约 0.7004 × 0.6944 m，高 0.75 m；静态桌板/腿/碰撞支撑，不带活动关节。
  桌腿和高度含任务设计假设。早期独立桌面还做过 9 个测试方块接触/摩擦检查；方块是夹具，不是视频重建资产。
- **白桌：** 当前新版约 0.65 × 0.62 m，高 0.75 m；旧 iPad 场景约 0.80 × 0.70 m，属于不同估计布局。
  都是静态支撑，不是有抽屉、可搬动桌体的机构模型。
- **笔记本支架：** 独立静态承托轨及简化支撑，不与笔记本焊接，不可折叠。
- **砖地：** 3.6 × 3.0 m 的静态碰撞平面与真实观测纹理；约 99.67% 纹理覆盖，含未知掩码与每 texel 来源。
  不是逐砖凹凸或可变形路面。推进测试另把隐形支撑碰撞扩展到 12 × 3 m，属于夹具。
- **3DGS：** 原木桌视频及白桌等有 PLY/高斯训练结果；`background_model.json` 仍声明
  `scale_status=arbitrary_SfM_units`、`physics_status=not_built`。这是外观分支，不是现成的机器人碰撞资产；合成重建 smoke 也不是实物资产。
- **Unitree G1：** 官方引入的 29 运动自由度机器人，非从视频重建。实际演示 USD 有 30 个刚体、
  29 个活动关节和 1 个固定根关节。双臂各 7、腿部共 12、腰部 3；橡胶手没有独立可控手指。
  手部派生副本补齐碰撞。另有早期静态 G1 视觉参考，它不包含机器人控制证据。

## 4. 本仓库已有的机器人控制链

目前只有一套实际运行过的 G1 控制链，下列组件不能当成几套独立通用框架重复计数。

| 组件 | 当前实现 | 代码/证据 |
| --- | --- | --- |
| 仿真执行 | Isaac Sim 5.1 / Isaac Lab 2.3.2，CPU PhysX TGS，dt=1/240 s；机器人 articulation、真实接触、相机、传感器 | `construction/isaac_robot_worker.py`、`scripts/run_table_sim.py --worker robot` |
| 运动学 | 读取实际 URDF 计算 FK；SciPy `least_squares`，受关节上下限约束的单臂 IK | `construction/robot_kinematics.py` |
| 轨迹 | 针对合盖/拖鼠标的脚本目标与路径，关节平滑插值，部分段笛卡尔插值/Slerp | `isaac_robot_worker.py` 中 `add_segment`、`solve`、`cartesian_segment` |
| 执行器 | Isaac 隐式关节 PD + 显式重力补偿；腿腰、手臂、腕部分组增益与力矩上限 | `ImplicitActuatorCfg`、`set_joint_position_target`、`set_joint_effort_target` |
| 反馈/验收 | 记录关节位置、物体位移、手—物体接触力和任务结果 | `outputs/user-g1-contact-20261023-v5/validation.json`，7/7 通过 |

G1 固定骨盆，关闭机器人自碰撞，利用已知场景坐标制定路径。关节 PD 有状态反馈，
但当前任务路径没有根据相机或接触力在线重规划，不能称为视觉自主操作或力控闭环。
物体本身不接收直接位姿或外力指令，G1 演示中的物体运动来自机器人接触。

没有发现本仓库已接入的机器人 ROS 2 控制桥、MoveIt/cuRobo 全身碰撞规划、
SONIC/Homie 平衡控制、VLA/π0.5/GR00T 策略、遥操作/示教训练管线，或标准机器人 RL task 环境。
主机上存在 ROS 或外部 IsaacLab 不等于项目已经使用这些控制能力。

Artisan 是资产物理语义/编译/导出 SDK；Blender 是几何/渲染后端；COLMAP/3DGS 是视觉重建后端。
`asset_agents.py` 的 Coding/Review 与 `design.py` 的理解/蓝图/哈希自检用于**资产构建**，
不是机器人收到目标后进行观测与动作决策的运行框架。

## 5. 与 RoboHelix / ClawSpine 的核对

通过本机 GitHub SSH 认证只读取得 `NJU-SOARLAB/RoboHelix`，检查提交：
`02cad34a60871d9b1a9d0a1916b10eb5e69ad098`。本执行主机没有可用 gh，但现有 GitHub SSH 权限可读该私有仓库。
没有安装/启动其运行时、模拟器、策略或模型。

用户提到的名称在源码中为 **ClawSpine**。RoboHelix 是外围研究/实现/独立评测/候选进化基础设施；
ClawSpine 是被运行和评测的机器人 harness，复用 DSH 原生模型/工具会话。

| 能力 | 本仓库 | RoboHelix 中检查到的 ClawSpine 接口/实现 |
| --- | --- | --- |
| 目标到行动 | 单次固定脚本 G1 路径 | DSH 模型/工具循环 → 机器人工具或生成模块 → 平台控制 → 新观测 |
| 观测工具 | worker 内部状态/接触/相机记录 | `read_camera`、`read_depth`、`read_pose`、`read_whole_body_state`、`read_health_metrics`；`read_scene` 是默认关闭的特权目录 |
| 动作接口 | Python 内直接设置机器人关节目标 | `execute_navigation`、`execute_whole_body`、`execute_low_reflex`、`idle_robot`；另有配置相关的 manipulation 接口 |
| 本地行为模块 | 没有通用注册/版本调用协议 | `register_low_reflex` / `list_low_reflexes`，Python `step(observation, elapsed_s, parameters)`，动作准入绑定版本 |
| 低层身体控制 | 固定骨盆 PD + 重力补偿 | 当前统一 Isaac/Arena 后端使用 Homie V2；可选 SONIC 部署，以实际控制器能力为准 |
| 机器人构型 | 29 自由度，固定橡胶手 | 当前原生 Arena 后端是 43 自由度 G1，包含 14 个实际手指关节；不应直接套用本仓库 29 关节顺序 |
| 策略技能 | 未接入 | 基础技能目录有 SONIC、π0.5；π0.5 输出不直接执行，目录存在不等于后端在线或任务成功 |
| 执行与证据 | 进程/物理报告、资产哈希与本地记录 | 另有共享执行契约、幂等、取消、资源归属、实测停止/释放和独立任务评测 |

参考源码：

- [ClawSpine 定位与包边界](https://github.com/NJU-SOARLAB/RoboHelix/blob/02cad34a60871d9b1a9d0a1916b10eb5e69ad098/systems/clawspine/README.zh-CN.md)
- [原生 G1 工具定义](https://github.com/NJU-SOARLAB/RoboHelix/blob/02cad34a60871d9b1a9d0a1916b10eb5e69ad098/playground/packages/platform-g1-arena-dsh/src/native.ts)
- [统一场景后端及控制范围](https://github.com/NJU-SOARLAB/RoboHelix/blob/02cad34a60871d9b1a9d0a1916b10eb5e69ad098/playground/tools/vla_isaac/UNIFIED_SCENE_BACKEND.md)
- [基础技能及验证限制](https://github.com/NJU-SOARLAB/RoboHelix/blob/02cad34a60871d9b1a9d0a1916b10eb5e69ad098/basic-skills/README.zh-CN.md)

**本仓库没有相同级别的 ClawSpine，也没有完成与它的集成。** 已有资产构建与证据部分可供上层使用，
但传感、动作、任务运行和独立任务评测之间仍需接通。

从已有接口看，最直接的后续接点是“重建 USD 场景 → RoboHelix 本地 USD 场景加载器”，
保留其原有 G1 构型和控制器，再为合盖/推鼠标等任务建立独立验收。
这属于基于源码的集成建议，尚未验证本仓库 USD 在其具体后端的加载/关节/摩擦兼容性，
也不能将已有箱子任务策略的能力扩展宣称为能操作这些新物体。

## 6. 审核注意事项

1. 最新有效资产依赖版本与证据绑定；历史重复目录不是新增能力。
2. `passed` 只适用于报告内测试范围。FreeClip 直接设角、自行车导轨推进、G1 固定骨盆各有不同边界。
3. 笔记本专项失败仍有效；自行车 v5/reload-v1 中被撤销的通过记录不纳入有效验收。
4. 资产能受力运动、机器人能完成指定动作、真机可部署，必须分别记为不同状态。
5. 机器可读清单与本次读取证据哈希见 `outputs/repository-audit-20261026/asset-inventory.json`。

# 桌面导入 Isaac Lab

本阶段将已有视觉桌面变成可导入的 USD 静态支撑资产，并测量落块、支撑、滑动行为。
外观已满足当前需求，纹理开源工具仅记录在 [后续规划](history/TEXTURE_ROADMAP.md)，本阶段不试跑。

## 与 Artisan 的关系

参照 [Artisan](https://physicalvision.github.io/artisan/) 的设计规范、构建、物理反馈、审查/修复分工：

1. `blueprint.json` 固定静态桌板、参数来源、零关节、验收要求。
2. 程序构建视觉网格、独立盒状碰撞体、接触材质，导出 USD。
3. 在 Isaac Lab 里加载实际导出的文件，用九个方块测试接触及小/大推力响应。
4. 保存轨迹、接触力、视频和逐项结果；失败保留记录后修复，再用新目录重跑。

这是对该流程的桌面实现，未调用 Artisan SDK，也没有运行独立 ReviewAgent。
静态资产接触通过，不代表原视频的真实尺寸、机器人控制或真实材料参数通过验证。

## 坐标与尺寸

输入来自已完成的桌面实验 `table-plane.json`。保持重建长宽比例，用一个显式尺度锚点
做统一缩放，转为米制、Z-up、X 朝机器人前方、Y 朝机器人左侧。桌面中心和原始相机世界
之间的变换保存在 `geometry.json` 的 `sfm_to_sim`。

这是任务场景布局变换；桌高按机器人任务重新选择，因此不是完整房间与实机的外参标定。
不能把该变换应用到原房间后，就声称地面、所有对象和机器人都已物理对齐。

用户选择先适配 G1 操作高度。默认宽 0.80m 是假设；深由重建比例得到，当前约 0.793m。
桌高 0.75m 是任务设计值，厚度 0.03m 和四条简化桌腿是假设。没有把这些数字标为实测。
G1 官方参数为站立外形 1320×450×200mm，见 [官方页面](https://www.unitree.com/g1/)。
具体可达性必须由所选关节模型检查，不能仅靠机身总高度判断。

## 初始接触参数

| 参数 | 初始值 | 含义与来源 |
|---|---|---|
| 桌板 | 静态盒状碰撞体 | 上表面与视觉桌面重合；厚度向下延伸 |
| 桌子质量、惯量 | 不设置 | 静态支撑，不是假装通过巨大质量固定 |
| 静摩擦 / 动摩擦 | 0.6 / 0.5 | 初始假设；不是根据木纹识别出的实测值 |
| 摩擦组合 | average | 配对双方初始均设为相同参数，便于诊断 |
| 回弹系数 | 0 | 非弹跳接触的初始假设 |
| contact offset | 0.002m | 提前生成接触；不等于静止悬空距离 |
| rest offset | 0m | 静止时不人为留空隙 |
| 重力 | -9.81m/s² | Z 向下 |
| 步长 | 1/240s | 低速小物体的初始接触配置 |
| 求解器 | PhysX TGS | CPU 物理用于单场景验证，RTX 用于渲染 |
| 位置 / 速度迭代 | 8 / 2 | 动态测试物体设置；不是机器人 PD 增益 |

[Isaac Lab 接触材质配置](https://isaac-sim.github.io/IsaacLab/v2.3.0/_modules/isaaclab/sim/spawners/materials/physics_materials_cfg.html)
描述参数接口；数值是本项目的工程起点，需要真实推滑数据才能标定材料。

## 命令与运行环境

感知/输入准备继续使用项目 Python 3.12 环境。Isaac Sim 5.1 worker 使用独立 Python 3.11；
不要把 Isaac Sim 安装到当前感知 `.venv`，也不修改既有 Conda 环境的包。

当前本机 worker 解释器位于 `~/.local/share/roboenv/isaac-env/bin/python`，通过 `.pth`
读取已安装的 Isaac Sim 5.1 包和 Isaac Lab 源码。未激活旧 Conda 环境，未安装或升级其依赖。
正式 worker 使用 `--portable-root` 将 Kit 的日志和运行数据放在本次输出旁的 `kit-runtime`。
该方式依赖本机安装位置，不是可移植的依赖锁；迁移主机时应单独安装兼容的 Isaac Sim/Lab。

```bash
.venv/bin/scene-perception prepare-table-sim \
  --table outputs/user-video-table-20261023 \
  --spec configs/isaac-table.example.json \
  --output outputs/table-sim-input

.venv/bin/python scripts/run_table_sim.py \
  --input outputs/table-sim-input \
  --output outputs/table-contact-check
```

`--python` 可指定兼容的仿真解释器。`--gui` 用于在有图形会话时运行并保留窗口。
输出必须是新目录；启动器检查 `validation.json`，不会仅凭 Kit 进程退出码 0 宣称通过。
本机 Kit 有时在测试完成后的退出阶段持续等待；无界面启动器仅在成功/失败报告已落盘后，
给清理阶段 15 秒，再终止该 worker，并在 `launcher.json` 记录退出情况。GUI 不采用此规则。
`scene.usda` 是场景入口；`asset/table.usda`、`table.png` 构成可单独复用的桌面资产。
资产局部坐标的桌面上表面为 Z=0，加载时应给根节点设置期望的桌高，例如在已经启动的
Isaac Lab 仿真中：

```python
import isaaclab.sim as sim_utils

table = sim_utils.UsdFileCfg(usd_path="/absolute/path/asset/table.usda")
table.func("/World/Table", table, translation=(0.5966, 0.0, 0.75))
```

改变桌高后，当前示例的固定长度桌腿也需要重新生成；输入准备和 worker 会按新高度生成。

九个 4cm、0.1kg 的方块分别落在桌面网格位置，验证最终底部偏差小于 2mm、存在持续接触、
速度收敛；中心方块先受 0.3mg 水平力，再受 0.9mg 水平力，分别检查保持和滑动。
测试是配置碰撞体的功能验证，不是独立实测精度或完整机器人任务验收。

## G1 操作空间与后续随机化

本次参照模型选官方 `unitree_ros` 的 G1 29DOF URDF，具体提交与哈希保存在实验 references。
位置 IK 使用该模型的关节轴、限位和脚底碰撞球，固定一个站姿，检查手部参考点。
它尚未检查末端朝向、双臂碰撞、力矩、平衡及控制跟踪。可选 G1 场景资产仅为该姿态的
静态视觉参照，没有机器人刚体/关节仿真，不能把截图视为机器人操作成功。

后续随机化先考虑桌高 0.70–0.80m、近侧桌边 X=0.18–0.24m、静摩擦 0.45–0.80、
动摩擦 0.35–0.65。当前不启用这些随机化。每次采样必须保持动摩擦不高于静摩擦，
同步更新桌腿、物体摆放及目标高度，并重新检查关节限位、末端朝向、碰撞与任务可达性。
远侧区域不可达时，应限制任务区或引入移动/躯干控制，不能假设整张桌子双臂都可操作。

## 用参照物估计尺度的规划

后续 VLM 识别参照物类型及可能型号，检索可信尺寸先验，再由几何计算尺度。
同一模型身份不明确时保存多个尺寸假设和尺度范围，输出来源为 estimated，不能变成 measured。
优先使用多个互相独立的参照物交叉检查。若尺寸先验完全未知，单目透视仍不能唯一确定绝对尺度。

当前视频的笔记本放在支架上，不能把键盘角点直接当成桌面上的点做单应性缩放。
应估计该参考面的三维位置或高度，或者选择确实在桌面上的参照物。
桌高继续由 G1 任务设计决定，不必从该视频恢复。此参照物估计流程尚未实现，也未调用 VLM。

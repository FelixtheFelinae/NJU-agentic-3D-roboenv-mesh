# 视频中的三个物体：Coding / Review 实验

当前视频的结果目录为 `outputs/user-scene-assets-20261023/`。最终场景是
`simulation/scene.usda`，包含桌面、原视频中的瓶子、14 英寸 MacBook Pro、鼠标及必要的静态支架。
没有机器人或新增测试方块。打开 USD 后播放物理即可；复制场景时须保留同目录的 `asset/` 和 `assets/`。

## 实现与来源

- `construction/asset_fit.py`：同一 COLMAP 标定坐标系中的多视角拟合。
  笔记本使用两幅图中人工标注的外框角点；瓶子和鼠标使用六幅分割轮廓及类别形状先验。
  `fit/annotations.json` 保存原始像素标注，不伪装为真实长度测量。
- 用户确认笔记本为 14 英寸 MBP，以 Apple 的 31.26 × 22.12 cm 规格作为尺度参考。
  来源：<https://support.apple.com/en-hk/111902>。具体代际和重量未确认。
  拟合给出一个统一比例，桌面约 70.0 × 69.4 cm；高度 75 cm 是面向 G1 的任务设计。
- `construction/asset_agents.py`：通过已有 Qwen API 实现独立 Coding 和 Review 上下文。
  Coding 写 `model.py`，接收真实编译反馈；Review 独立选择有类型约束的 `qa_plan`，
  执行器依据冻结蓝图将其编译成 `qa_tests.py`，Review 不能修改候选或证据。
  该方式防止审查模型猜测 SDK 方法、将归一化参数当字符串，或用常量 `True` 冒充测试。
  这是本项目的适配器，使用 Artisan SDK/编译器，不等同于直接运行 Artisan 原生 provider harness。
- 独立的 Artisan Python 执行模型、基线 QC，并导出 URDF、MJCF 和 USD。
  Coding 的 `run_tests()` 不放业务验收断言；业务验收归 Review。
- `construction/isaac_asset_worker.py`：导入实际 USD，在 Isaac Lab 中验证三个真实资产。
  USD 适配保留 SDK 的几何、碰撞分解和惯量，展开嵌套刚体层级，隐藏碰撞代理，启用真实视觉网格。
- `construction/isaac_scene_check.py`：另一个进程重新打开最终场景，不写入姿态或关节角，再运行物理。
  这一检查用于捕获“脚本中正确、导出场景中丢失初始关节状态”的问题。
- Review 核对模型、输入网格、USD、依赖文件与物理报告哈希。QA 中有效失败反馈候选缺陷；
  QA 解释器异常反馈测试脚本错误。协议错误和物理失败不能变成 PASS。

Artisan 参考：本机 `/home/felixz/real2sim/R2S2B/sources/Artisan/sdk/_docs/model_contract.md`
以及 `sdk/_docs/common/80_testing.md`；项目：<https://github.com/physicalvision/Artisan>。

## 首版物理参数

所有未测参数均为假设或产品系列先验，不能当作实测值。

| 资产 | 几何约束 | 质量 | 动作 |
| --- | --- | --- | --- |
| 瓶子 | 封盖外形，一个刚体，约 6.2 × 6.0 × 20.4 cm | 0.060 kg，假设空瓶 | 推动、倾倒、拿起 |
| 鼠标 | 曲面主体与固定拇指托，一个刚体，约 8.8 × 10.7 × 4.2 cm | 0.141 kg，系列先验 | 推动、拿起 |
| 笔记本 | 31.26 cm 宽，约 22.16 cm 深；底座与屏幕两个刚体 | 1.60 kg，底座 1.15 / 屏幕 0.45 | 整机移动、被动开合 |
| 支架 | 两条承托轨及简化立柱，独立静态设施 | 静态无质量 | 托住笔记本，未焊接 |

鼠标的精确型号未确认，拟合尺寸与 MX Master 系列的规格存在差异；形状约束和分割误差仍需改善。
共享尺度的 6% 是暂定不确定性先验，并非经真实测量证明的误差界；物体补全误差还需单独计入。

铰链：闭合 `q=0`，假设工作范围 `[0, 2.40]` rad；场景初始开角约 128.6°，
底座受支架支撑而倾斜约 22.4°。使用 PhysX 5.1 原生关节轴摩擦，静摩擦力矩 0.55 Nm、
动摩擦力矩 0.42 Nm、黏性系数 0.03 Nm·s/rad。USD 边界将角速度系数转换为每度单位。
没有位置伺服或锁死关节；验证通过两片刚体上的等大反向力矩施加外部作用。

桌面摩擦 `(0.6, 0.5)`，鼠标 `(0.35, 0.25)`，瓶子和笔记本 `(0.6, 0.5)`，
支架 `(0.9, 0.7)`；静/动摩擦采用平均组合。恢复系数 0，物体接触偏移 1 mm、静止偏移 0。
步长 1/240 秒，TGS，物体求解迭代 16/4。

## 复现

在项目根目录运行。拟合阶段需要原视频的已有感知、COLMAP 和桌面输出，以及已保存的像素角点标注。
输出目录中已有候选时先备份；不要用准备步骤覆盖已审查资产。

```bash
.venv/bin/python scripts/build_video_assets.py fit --root outputs/user-scene-assets-20261023
.venv/bin/python scripts/build_video_assets.py prepare --root outputs/user-scene-assets-20261023

for asset_id in asset-0003 asset-0001 asset-0002; do
  .venv/bin/python scripts/build_video_assets.py code \
    --root outputs/user-scene-assets-20261023 --asset "$asset_id"
done

.venv/bin/python scripts/build_video_assets.py appearance --root outputs/user-scene-assets-20261023
.venv/bin/python scripts/evaluate_video_assets.py --root outputs/user-scene-assets-20261023

# 以下输出目录必须不存在；旧运行保留到其他名称。
.venv/bin/python scripts/run_table_sim.py --worker assets \
  --input outputs/user-scene-assets-20261023 \
  --output outputs/user-scene-assets-20261023/simulation
.venv/bin/python scripts/run_table_sim.py --worker scene-check \
  --input outputs/user-scene-assets-20261023/simulation \
  --output outputs/user-scene-assets-20261023/scene-reload

for asset_id in asset-0003 asset-0001 asset-0002; do
  .venv/bin/python scripts/build_video_assets.py review \
    --root outputs/user-scene-assets-20261023 --asset "$asset_id"
done
.venv/bin/python scripts/build_video_assets.py scene-review --root outputs/user-scene-assets-20261023
```

API 配置沿用本地设计配置，凭据仅从 `.env.local` 读取。子进程移除 API key/token 等环境变量。
生成代码有语法/能力筛查，但这不是操作系统沙箱；只在可信开发环境运行。

## 证据与限制

- `simulation/validation.json`、`trajectory.json` 和 `interaction-preview.mp4`：接触、推动、倾倒、离桌、铰链停留/转动证据。
- `scene-reload/validation.json`：最终 USD 在新进程中的运行验证。
- `objects/*/review.json`、`qa_tests.py`、`qa.json`：独立审查与可重跑测试；`scene-review.json` 为整场景审查。
- `fit/geometry-validation.json`：41 视角轮廓对比，并单列未参与物体拟合的视角。
  所有视角仍参与 SfM 标定，不能宣称独立几何测试或真实尺寸精度。
- `simulation-v*`、`scene-reload-v*` 和日志保留调试失败；以最终 `simulation/` 和审查报告为准。

这套场景还没有真实机器人抓取控制验证。瓶子不含液体或瓶盖螺纹机制，鼠标按键和滚轮固定。
外观只用照片投影、简单材料和桌面重复纹理；透明塑料暂用不透明外表面代理。
隐藏支架结构、底部形状和摩擦/惯量分配均含假设。下一步应优先改善鼠标几何及尺度证据，
再将当前视频专用输入/角点配置纳入通用场景构建命令。

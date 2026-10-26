# 自行车与砖地重建实验（2026-10-26）

输入为用户提供的黄色低跨式共享自行车视频。场景仅保留主自行车及其承托地面，
排除远处车辆、行人、建筑。当前是由视频证据指导的程序化几何实验；agent 自动决策与修复闭环未实施。

## 可查看结果

根目录：`outputs/bicycle-ground-20261026/`（本地实验产物，未纳入 Git）。

- `simulation-v3/scene.usda`：可加载的最终物理场景，附属文件必须一起保留。
- `simulation-v3/interaction.mp4`：9 秒 Isaac 实际渲染；前 6 秒依次为后轮、前轮、转向、曲柄的外力矩响应，后 3 秒为重置到停车状态后的环绕。
- `simulation-v3/preview.png`：经过重力与接触求解的停车状态。
- `preview-v2/scene.blend`：含打包砖地纹理的可编辑 Blender 场景。它是几何展示，物理验证以 USD 为准。
- `preview-v2/{preview,opposite,side}.png`：多角度几何展示。
- `comparison.png`：源视频与重建对照，非严格相机配准图。
- `artisan-v6/export/object.{usd,urdf,xml}`：Artisan 导出的自行车。
- `ground-final/`：真实地面纹理、观测掩码、每 texel 的来源帧、相机/地面对齐记录。
- `simulation-v3/validation.json`、`trajectory.json`：物理检查、输入力矩和真实状态轨迹。
- `reload-v3/validation.json`：独立进程重载验证。
- `delivery-manifest.json`：最终交付文件哈希及验证绑定。

## 几何与证据

复用 3 fps 的感知结果及 CPU COLMAP 相机恢复，36/36 帧注册。
从真实背景掩码筛选出的 11511 个稀疏候选中，9632 个落入主地面平面。
假设轮半径 0.30 m 后，平面点到面 RMSE 约 4.39 mm；这是内部拟合残差，不是实物测量精度。

`reconstruct_bicycle_ground.py` 用明确记录的人工轮心标注估计坐标轴与尺度，再把可见砖地反投影到
3.6 × 3.0 m 的地面纹理。前景/未知掩码不供纹理采样，背景掩码腐蚀 15 px，并排除每帧顶部 38%。
使用最近有效视图选择纹理源，纹理有接缝、原视频阴影及遮挡带来的局部缺失。
当前观测覆盖率为 99.67%；未知 texel 使用中性灰色，观测掩码仍为零。
帧 5、17、29 的 RGB 未用于纹理融合，但相机恢复使用了全部帧；未报告独立 holdout 图像质量分数。
未进行生成式补全或 3DGS 训练，本次背景采用可碰撞平面与真实观测纹理。

Blender 生成 130 个封闭视觉网格，组成 5 个刚体：车架、转向组件、前轮、后轮、曲柄。
包括车篮、低跨车架、车座、挡泥板、支撑脚、辐条、杯架及车把主要细节。
轮子保留中空结构，Artisan 使用凸分解作为碰撞表示。
法线分缝的 OBJ 在送入 Artisan 前转换为共享位置索引，避免 SDK 把法线接缝当成分离面片；原始 Blender 文件保留。

所有尺寸均为估计：轮径 0.60 m、模型轴距 1.08 m、总包围尺寸约 1.73 × 0.60 × 1.15 m。
人工标注三角化得到的轴距约 1.258 m，与模板有差异；模型与拟合轮心每端约 9.1 cm 的偏差已记录。
地面与资产使用相同米制、Z-up 和轮半径先验，但原视频的精确摆位/轮廓配准尚未通过验收。
15% 尺度不确定性是建模假设，不是统计置信区间。后轮毂部分被遮挡，前轮标注有约 5–15 px 的重投影误差。
不可将这个近似结果称为实测 CAD 或自动高精度重建。

## 实际物理验证

Artisan `artisan-v6/build.json` 为 success，导出 USD、URDF、MJCF。
Isaac CPU PhysX 以 1/240 s 积分，自由基座、重力、地面接触和 articulation self-collision 开启。
质量总计 22.3 kg；摩擦、阻尼与质量均为假设参数，未从视频辨识。
支撑脚固定为展开状态，初态向脚侧倾约 10.3°，由接触求解器落稳。

最终构建检查 12/12 通过，包含刚体/关节数量、状态有限、停车速度、轮心高度、地面接触和四关节力矩响应。
每项关节测试先从相同停车状态运行 1.5 s 零力矩对照，再施力矩 0.5 s 并释放 1.0 s。
只在实验之间重置状态；被测期间没有写入关节角或速度，也没有位置伺服。

| 关节 | 测试力矩 | 相对初态最大变化 | 与零力矩对照最大差异 |
| --- | ---: | ---: | ---: |
| 后轮 | 12 Nm | 2.018 rad | 2.018 rad |
| 前轮 | 12 Nm | 0.325 rad | 0.325 rad |
| 转向 | 5 Nm | 0.732 rad | 0.732 rad |
| 曲柄 | 2 Nm | 6.225 rad | 6.225 rad |

地面摩擦和展开支撑脚使 1 Nm 的车轮测试无明显响应；12 Nm 测试说明关节可受力运动，不代表真实骑行阻力已标定。
转向限位 ±0.65 rad；停车时车把因重力转到一侧限位。未实现链条传动、刹车、独立脚踏轴承或骑行控制。
曲柄短时最高角速度约 35 rad/s，说明当前弱阻尼参数仍需要按下游任务调整；不能把有限状态检查当作物理参数拟合。
未完成所有关节组合的全行程间隙扫描、载人骑行或不同摩擦/质量分布下的鲁棒性验收。

独立重载 7/7 检查通过，重载源为 `simulation-v2/scene.usda`；其场景文件与 v3 的 SHA-256 完全相同，
附属资产逐文件哈希等价性见 delivery manifest。重载检查验证停车稳定性和结构，未重复全部力矩实验。
Kit 在报告落盘后可能不退出，launcher 记录有界清理；失败时不会仅凭进程 exit 0 判成功。

保留失败记录：初始支撑脚几何使车向另一侧倒下；首次重载测试相机重名；低轮力矩不足。
未放宽验收阈值来隐藏这些失败。

## 复现

需要本机已安装的 Blender、Artisan 和独立 Isaac 环境；不要把它们安装进项目 Python。
输出目录必须使用新名字。先复用本次感知、COLMAP 和 `alignment-input/wheel-landmarks.json`。

```bash
.venv/bin/python scripts/reconstruct_bicycle_ground.py \
  --root outputs/bicycle-ground-20261026 --output outputs/bicycle-ground-NEW/ground

.venv/bin/python scripts/build_blender_mesh.py \
  --generator scripts/blender/bicycle.py --output outputs/bicycle-ground-NEW/blender \
  --blender /home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender

PYTHONPATH=src OPENBLAS_NUM_THREADS=2 \
  /home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python \
  scripts/build_bicycle_asset.py --meshes outputs/bicycle-ground-NEW/blender \
  --output outputs/bicycle-ground-NEW/artisan

.venv/bin/python scripts/run_table_sim.py --worker bicycle-build \
  --input outputs/bicycle-ground-NEW/artisan --ground outputs/bicycle-ground-NEW/ground \
  --output outputs/bicycle-ground-NEW/simulation --timeout 600

.venv/bin/python scripts/run_table_sim.py --worker bicycle-check \
  --input outputs/bicycle-ground-NEW/simulation --output outputs/bicycle-ground-NEW/reload \
  --timeout 600
```

验证代码：真实 Blender 后端测试 4 项通过（含部件局部坐标与装配边界），construction 回归 17 项通过。
新增脚本和 worker 的 Ruff 检查通过。上述软件检查不能代替源视频外观验收。

# 小车地板 3DGS → MuJoCo

键盘驾驶版入口为 `keyboard_viewer.py outputs/cart-background-20261027/mujoco_keyboard/scene.xml`。WASD/方向键驾驶，松键制动；左键拖动旋转、右键平移、滚轮缩放，F 切换跟车（默认关闭）。窗口默认隐藏本窗口的系统光标，将箭头绘制进 OpenGL 画面，并限制到最高 30 fps，用于绕开远程桌面的光标叠加闪烁；未捕获或重定位鼠标。传入 `--native-cursor` 可恢复系统光标。该兼容方案已验证能渲染光标，远程端闪烁是否消失仍需实际观察。

本次实际运行了既有 COLMAP 相机恢复、遮罩监督 3DGS 训练和高斯渲染；MuJoCo 加载高斯烘焙后的二维外观贴图及小车 MJCF，地面接触由独立平面负责。没有宣称 MuJoCo 原生渲染高斯或从图像识别真实摩擦。

## 数据与流程

输出目录：`outputs/cart-background-20261027/`。

1. `prepare.py` 从原始 20.119 s 视频的前 15 s 抽取 30 帧，缩放至 640×360。人工检查接触表后，用保守矩形排除小车及阴影，并排除顶部四分之一。保留 RGB 原图，遮罩只控制监督，不把黑块训练进背景。相机恢复允许使用静止小车特征；背景高斯不使用车体像素。
2. 复用 `construction.reconstruction.estimate_background_cameras`。第一次有 13 帧未注册，被既有严格检查拒绝。显式选择 17 个已注册视角建立 `registered_subset_v2/`，重新得到 `cameras_subset/`；没有把未注册帧算进结果。
3. 保守背景轨迹只有 9 个稀疏点。第一版 `gaussians/` 沿用原框架训练 3000 次，选中 1000 次检查点、112 个高斯。另用 `plane_seed.py` 拟合地面平面，将真实训练帧的有效像素反投影到该平面并去重，生成 25314 个初始化点。此处为显式平面先验，不是新增的三角化证据。
4. `gaussians_plane/` 使用既有 `train_background` / `train_worker.py`，训练 5000 次。按既有验证 MSE 选择 2000 次检查点、100000 个高斯。没有使用扩散补图、黑块填洞或真实图像以外的监督。
5. `bake.py` 对训练所得高斯做俯视渲染，保留假设地面附近 ±35 mm 高斯，输出 `bake_v1/floor_gaussians.ply`、`floor_3dgs.png` 和 `coverage_mask.png`。2.4×2.4 m 假设范围内，至少两次训练观察且高斯不透明度达标的面积占 15.5%。空白区域保留中性灰。
6. `tile.py` 从全观测覆盖、无明显彩色胶带/暗斑的高斯贴图中截取 160×160 像素，镜像拼成可重复材质。它是外观延展，不是未观测区域的重建。所选像素、覆盖率和假设物理尺寸记录在 `material_tile.json`。
7. `mujoco_scene.py` 复制 Artisan MJCF 及网格依赖，加入材质和地面，实际运行 MuJoCo 3.9.0，输出原始轨迹、截图、回放和物理报告。`viewer.py` 用于交互查看。

## 质量限制

更密初始化保留了较多局部细节，但**验证误差更差**：稀疏版平均遮罩 MSE 约 0.000958；平面初始化版选中检查点约 0.003943，两个验证视角分别约 0.000227 和 0.007659。验证帧已用于选检查点，不是独立测试集。这不支持高精度背景重建结论；地面重复纹理、相机/平面误差和视角外推仍导致模糊与瑕疵。

米制尺度来自首帧约 0.40 m 车体投影宽度假设，不是实测。小车与原地面坐标仅近似对齐，车头朝向是展示选择。高斯 RGB 包含原照明，烘焙后在 MuJoCo 再次受光，不能称作估计出的 PBR 材质。地面的细纹没有生成物理高度图。

## MuJoCo 实际验证

`mujoco_delivery/scene.xml` 为重复材质场景；`scene_observed.xml` 保留原始高斯覆盖范围和中性灰未知区域。两者使用相同物理配置。

原始 Artisan 网格碰撞版虽然稳定接地，但 5 N 推动约 1 s 只移动 9 mm、轮子几乎不转，未通过。当前使用声明的车架盒体、轮胎圆柱和父子刚体接触过滤来表示理想轮轴，保留原始视觉网格、质量、惯量和四轮关节。此修改单独记录，不能声称原始导出直接通过。

最终 8 项运行检查、7 项独立保存轨迹/资源检查通过：5 N 推动约 1 s，移动 0.881 m，轮组约转动 17.63 rad；稳定阶段至少 8 个地面接触。未在运行中写入车体姿态。3 项贴图选择/边界测试通过。

现有螺钉运行时仍在 Isaac 实验目录，**未移植到 MuJoCo**；本场景验证的是车与地面共同运行，不包含拆卸。滚子接触仍简化为圆柱轮胎。

## 打开场景

```bash
/home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python \
  experiments/cart_background/viewer.py \
  outputs/cart-background-20261027/mujoco_delivery/scene.xml
```

空格切换 5 N 推力，R 重置。需要桌面显示环境；无桌面时可查看 `scene.png` 或 `simulation.mp4`。

## 训练与烘焙入口

`prepare.py --stage prepare/cameras/train` 是初始全帧路线，保留其严格失败行为。17 帧选择版本和稠密平面初始化不伪装成自动全视频通过；它们通过既有哈希绑定目录交给 `estimate_background_cameras` / `train_background`。初始化与烘焙使用已有 Gaussian 环境：

```bash
PYTHONPATH=src /home/felixz/.local/share/roboenv/gaussian-env/bin/python \
  experiments/cart_background/plane_seed.py \
  --cameras outputs/cart-background-20261027/cameras_subset --output outputs/new-plane-input
PYTHONPATH=src /home/felixz/.local/share/roboenv/gaussian-env/bin/python \
  experiments/cart_background/bake.py \
  --model outputs/cart-background-20261027/gaussians_plane/model \
  --cameras outputs/cart-background-20261027/cameras_plane_v2 --output outputs/new-bake
.venv/bin/python experiments/cart_background/tile.py --bake outputs/new-bake
MUJOCO_GL=egl /home/felixz/real2sim/R2S2B/sources/Artisan/.artisan/venv/bin/python \
  experiments/cart_background/mujoco_scene.py \
  --asset outputs/cart-full-20261027/artisan_final/export \
  --spec outputs/cart-full-20261027/assembly_spec_v4.json \
  --bake outputs/new-bake --output outputs/new-mujoco-scene --tile
```

所有原始失败日志和版本保留。资产输出不提交 Git，已有生产模块未修改。

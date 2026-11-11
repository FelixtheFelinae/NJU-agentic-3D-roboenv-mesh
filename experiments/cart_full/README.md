# 视频小车：全车装配重建与交互实验

2026-10-27 紧固件复审产生独立新版本 `outputs/cart-fasteners-20261027/candidate-008/` 与 `geometry/`，入口 `repair_fasteners.py`。该版本改用显式穿透螺栓/垫圈/螺母候选，并通过新增 Harness 几何检查；旧单螺钉动力学结果不适用于修改后的连接。完整调用关系、失败记录及限制见 [装配关系 Harness](../../docs/HARNESS_ASSEMBLY.md)。

输入是 20.119 s 的小车环绕视频。最终版本在 `outputs/cart-full-20261027/`，采用 `assembly_spec_v4.json`、`geometry_v4/`、`artisan_final/`、`physics_final/` 和 `connection_final/`。当前是有显式假设的近似资产，不是制造 CAD 或实车完整 BOM。

## 实现和调用关系

- `spec.py`：将原始视频观察与 Codex 复核整理为参数化配方。生成 46 个稳定编号对象、8 个紧固关系候选和 4 个轮轴转动关系。每个对象有证据引用、几何状态和尺寸来源。所有米制尺寸都是假设，0.0127 m 孔距和约 0.1016 m 轮径没有获得本车规格确认。
- `blender_build.py`：解释配方，生成带实际通孔的槽形梁、轮组、盒体、支撑和简化紧固件；输出每件 OBJ、整车 GLB、视觉 USD、Blender 文件及六张展示图。爆炸图使用展示位移，不代表已验证拆卸路径。
- `artisan_build.py`：复用仓库 `run_artisan` 和 `artisan_asset_worker.py`，汇入零件网格，编译为底盘加四轮的五刚体资产，导出 USD、URDF、MJCF。细分装配对象归并到对应物理刚体，可拆语义仍在独立连接表中。
- `physics.py`：重新加载同一详细视觉 USD，独立构造五刚体滚动实验。碰撞使用保守车架盒体和轮胎凸网格；四轮可绕 X 轴转动，滚子没有独立运动。测试落地、零输入、外力推动和架空单轮力矩响应。这一物理场景不是 Artisan 原始导出的物理验证，两者不能混称。
- `connection.py`：把既有 `cart_assembly/mechanics.py` 的力反馈螺钉模型放回整车结构。车体作为固定夹具，右侧前梁螺钉独立运动。用全车盒体/圆柱保守包围盒检查工具通道，用实际结构盒体保留通孔碰撞。非盒体零件未纳入完整接触求解，因此不宣称任意退出路径已验证。
- `validate.py`：独立核对零件引用、初始杆件间隙及轨迹；重新计算转角和退扣量，不读取 `removed` 作为充分条件。

VLM 使用仓库现有 Qwen3.7-plus 客户端做全车清单提议和另一次视觉复核。两次回复都有前后方向、轮型和细节遗漏误判，因此不作为自动验收。原始回复保留在输出目录。Codex 根据真实原帧修正：外伸横梁一端为滚子轮、另一端为普通轮；黄块用途不确定；轮轴伸出部分不直接判成螺钉。

本次没有运行 SAM、COLMAP 或 3DGS。主路线是多视图观察后的参数化部件重建，没有标定相机或估计真实尺度。尝试读取 VEX 官方候选商品页均返回 403，未获得可采用的型号/尺寸证据，URL 和错误保留在 `references/sources.json`。

## 已验证内容

- Artisan 第三轮修正后及最终版编译、三种格式导出成功；最终导出记录仍有物理 warnings，不能据此保证 URDF/MJCF 在各引擎中可直接训练。
- 最终整车实验 8 项运行检查通过，独立验证 18 项通过。
- 5 N 外力作用约 1 s 后，车体沿 Y 前进约 0.374 m；轮组最大转角变化约 5.31 rad。位移与转角不严格对应纯滚动，存在滑移/接触近似，未验证轮胎参数。
- 架空单轮在 0.002 N·m 输入下转动约 5.46 rad。未推断真实电机参数或传动比。
- 最终全车夹具内单枚螺钉，初态保持、工具空间、退扣耦合和超过 25 mm 的分离通过；没有运行时姿态写入。
- 单枚螺钉取出后，另一侧紧固件及其余安装关系仍保留。整根横梁拆卸没有通过验证，当前代码也未实现其完整释放。

## 失败与修复

1. Artisan v1/v2 报告上层横梁、安装块等悬空。调整安装块高度并增加显式假设的上梁垫块，保持原 QC 检查，v3 通过。
2. 初次轮胎圆柱碰撞表现为落地不稳；改用明确顶点的轴向凸网格后稳定。保留失败轨迹，未把原始圆柱表现泛化为引擎已确认缺陷。
3. 1.5 N 推力试验基本不动；另设 5 N 输入试验后响应明显。后者不能抹去低推力失败，也不证明真实滚动阻力。
4. 全车螺钉初始被顶出。几何审计发现前梁下方黑块被简化成实心，挡住杆件；为其加入显式假设的安装孔后通过。新增测试会拒绝再次填死此孔。
5. 视觉复核指出屏幕、黄块和连接件细节简化，这是有效限制。它同时声称轮轴、螺钉和线缆完全缺失，与现有模型不符；这些条目未采纳。没有把 VLM 复核当作通过证明。

## 重现

所有输出目录需尚不存在。沿用已有 Blender、Artisan 和 Isaac 环境，不下载模型。

```bash
.venv/bin/python experiments/cart_full/spec.py --output outputs/cart-new/spec.json
/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender \
  -b --threads 6 --python experiments/cart_full/blender_build.py -- \
  --spec outputs/cart-new/spec.json --output outputs/cart-new/geometry
.venv/bin/python experiments/cart_full/artisan_build.py \
  --spec outputs/cart-new/spec.json --geometry outputs/cart-new/geometry \
  --output outputs/cart-new/artisan
/home/felixz/.local/share/roboenv/isaac-env/bin/python experiments/cart_full/physics.py \
  --spec outputs/cart-new/spec.json --visual outputs/cart-new/geometry/cart_visual.usdc \
  --output outputs/cart-new/physics
/home/felixz/.local/share/roboenv/isaac-env/bin/python experiments/cart_full/connection.py \
  --spec outputs/cart-new/spec.json --visual outputs/cart-new/geometry/cart_visual.usdc \
  --output outputs/cart-new/connection
.venv/bin/python experiments/cart_full/validate.py \
  --spec outputs/cart-new/spec.json --physics outputs/cart-new/physics \
  --connection outputs/cart-new/connection --output outputs/cart-new/validation.json
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  tests/test_cart_full.py tests/test_cart_assembly.py tests/test_harness_physics.py
```

## 未完成范围

底部和隐藏紧固件、轮轴保持、真实传动、线缆端点仍未知；普通轮和滚子轮细节、屏幕及黄块外形仍需细化。尚未做全车完整零件穿插检测、接触摩擦标定、工具咬合或整梁拆卸。下一轮应先补背面和底部证据，完善连接图，再将候选修订及验证接入默认 Harness；当前流程仍由 Codex 组织执行，不能声称任意视频全自动完成。

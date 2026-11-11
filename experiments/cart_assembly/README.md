# 小车单枚紧固件拆卸实验

本目录实现上传小车视频的一处候选连接实验，独立于默认 `scene-harness`。原始视频约 20.119 秒、1280×720。选择 18.9 秒画面中前横梁与后方支撑交会处的圆头紧固件，验证一枚螺钉的旋松、脱扣和受力取出。没有重建整车，也没有证明真实硬件制式。

## 实际调用关系

1. `intake.py` 用 FFmpeg 抽取指定时刻的原始帧，复用仓库 `load_construction_config`、`make_vlm`、请求追踪和文件哈希。配置中的 Qwen3.7-plus 提出零件及连接候选，不执行模型生成的代码。首次实验保存的原始提议有滚子轮、轮轴端和背面配合误判；复核修正保存在 `semantics.json`。
2. `semantics.json` 选择“螺钉穿过横梁、旋入固定支撑”的假设，保留穿透螺栓加背面螺母等备选。记录工具、夹具、未知参数及横梁释放尚未解决的依赖。可复制 JSON 修改并运行到新目录；不覆盖原提议和旧结果。当前转换由本次 Codex 执行及复核，尚未实现通用 VLM 提议自动编译器。
3. `isaac_worker.py` 将已选假设编译为 USD：带实际方孔的槽形梁、固定支撑和独立螺钉刚体。重载 USD 后，从静态碰撞几何的包围盒检查工具直线接近空间。物理场景修改放在 session layer，保留源文件不变。
4. `mechanics.py` 根据实测相对姿态计算连续转角和轴向位移，施加 `F=-k(z-hθ)-d(v-hω)` 与共轭力矩 `-hF`。加入声明的松脱阈值、运行阻力、阻尼和初始座面单向止挡。脱扣时撤销螺旋、横向和倾斜导向，随后由拉力驱动自由分离。运动过程中不写入刚体位置或角度。
5. `validate.py` 从四元数重新计算连续转角，检查耦合残差、脱扣时刻、分离距离、轴线稳定、座面穿透、自由加速度、对照条件与文件哈希。它不以状态机的 `removed` 字段作为成功依据。
6. `render.py` 在 Blender 中导入实际 USD，用 PhysX 记录姿态生成三张图、GLB 和 Blender 回放文件。展示回放不是新的物理实验；螺钉头部深色线是旋转标记，不是识别出的工具凹槽。

本次未调用 SAM、COLMAP、3DGS 或 Artisan 导出。原因是先验证新连接运行时，输入只有少数局部证据且无尺度标定；直接编译独立 USD 刚体避免把可拆连接塞入既有关节树。已有 VLM 客户端、配置及证据记录被复用，Artisan 和默认 Harness 的自动接入仍待完成。

## 已运行结果

最终结果位于 `outputs/cart-assembly-20261027/physics_v9` 和 `physics_v10_half_dt`，分别使用 1 ms、0.5 ms 步长；每组 8 个对照、80 项检查全部通过。1 ms 下，正常旋松在 2.223 s、约 5 圈、退让约 4 mm 时脱扣，2.288 s 达到 18 mm 分离距离。半步长脱扣为 2.2235 s。正常旋松最大耦合残差约 1.07 μm；这是所选模型数值误差，不是真实重建精度。

对照覆盖零输入、工具未啮合、低扭矩、错误旋向、脱扣前拉力、正常旋松、工具障碍、脱扣后横向自由度。错误旋向采用声明的初始座面止挡，不模拟继续拧紧或损伤。工具是理想传矩接口；没有真实工具接触或机器人控制。

轨迹继续施加恒定拉力到 5.2 s，因此末端会远离局部装置约 29.5 m；这是无重力、无抓持停止的加速度检查，不能作为实际取件行程。展示图选择刚分离约 25 mm 的时刻。实际使用需增加工具携持与停止策略。

`tests/test_cart_assembly.py` 与既有 `tests/test_harness_physics.py` 共 26 项测试通过。新增实验文件通过 Ruff。没有改动现有生产代码。

## 使用

环境沿用仓库已有 VLM 配置、Isaac Lab 和 Blender。密钥通过环境变量提供，不写进实验文件。示例运行目录必须尚不存在。

```bash
.venv/bin/python experiments/cart_assembly/intake.py \
  --video /absolute/path/cart.mp4 \
  --config configs/local-construction.toml \
  --times 10 17.1 18.9 --output outputs/cart-intake-new

/home/felixz/.local/share/roboenv/isaac-env/bin/python \
  experiments/cart_assembly/isaac_worker.py \
  --semantics experiments/cart_assembly/semantics.json \
  --output outputs/cart-physics-new --dt 0.001

.venv/bin/python experiments/cart_assembly/validate.py outputs/cart-physics-new

/home/felixz/.local/share/roboenv/blender/blender-4.5.9-linux-x64/blender \
  -b --threads 4 --python experiments/cart_assembly/render.py -- \
  --run outputs/cart-physics-new --output outputs/cart-render-new

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  tests/test_cart_assembly.py tests/test_harness_physics.py
```

复核、失败原因及修复记录见 `EXPERIMENT_LOG.md`。下一步应将几何参数也纳入类型契约，补充背面与工具接口证据，再连接 Artisan 资产部件和 Harness 的候选修复循环；当前不能宣称任意视频已能自动生成可拆装资产。

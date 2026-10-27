# 自行车零件识别与爆炸图探索

2026-10-26 开始，2026-10-27 整理。使用原视频 RGB 帧重新识别，未调用原有自行车生成器、网格、零件数或传动参数。

`scripts/prepare_bicycle_exploded_evidence.py` 从原始帧提取 12 个观察区域，保存时间戳、裁剪坐标和哈希。识别由本次多模态图像审阅完成；未运行专门训练的螺钉检测器，也未用 SAM 或图像增强生成不可见细节。`scripts/blender/bicycle_exploded_study.py` 按这些观察重新建立零件和总成，用 Blender 输出装配图、分层爆炸图和局部图。

模型拆分到轮轴端紧固件、候选轴承及垫圈、中轴轴芯及碗组、曲柄中心螺钉、脚踏轴、刹把枢轴、座管快拆、前叉内外管及密封边界、挡泥板支撑、链罩螺钉等。车架焊管和车篮网格按总成保留。链条按销轴、滚子和侧板展开，但侧板采用圆边杆状近似，未建真实板孔和压装配合。

共 675 个独立模型对象：85 个依照可见特征近似的对象、46 个隐藏结构候选对象，以及 544 个按暂定数量展开的重复对象。重复对象包括每轮暂定 36 根辐条及对应辐条帽，以及暂定 100 个链节位置的侧板、滚子和销轴。**这些是模型数量，不是已经确认的实车 BOM；可见特征也不代表其背侧实例、规格和配合全部被观察。**

蓝色表示内部候选件。轮毂和中轴暂以轴承包络表达，不能据此判断实车使用散珠、轴套还是密封轴承。前叉内部只放置未知弹性元件的包络，不假设它必然是弹簧。轮锁、制动器及终端只恢复外壳，内部锁舌、电路和制动蹄未识别。完整性仍受拍摄遮挡限制。

尺寸没有实测；0.61 m 轮胎外径只是此次新的尺度假设。螺纹省略，螺钉杆长和孔型为近似；未做制造公差、配合、整体干涉检查、可拆卸路径规划或物理验证。这里的爆炸动画是零件平移展示，不是求解出的拆装顺序。与此前已做动力学测试的简化自行车是独立资产，不能继承其物理结论。

交付在 `outputs/bicycle-exploded-study-20261026/delivery/`：

| 文件 | 用法 |
| --- | --- |
| `整车爆炸图.png` | 带总成编号和证据颜色说明的总览 |
| `*-annotated.png` | 轮毂、前叉头碗、传动、车把、鞍座五张局部图 |
| `assembly.blend` | Blender 打开后，时间轴第 1 帧为装配态，第 90 帧为爆炸态；按集合隐藏总成，选中零件查看自定义属性 |
| `assembled.glb` / `exploded.glb` | 两种姿态的独立网格，保留零件名称和证据属性；不是 STEP 或 SolidWorks 原生装配文件 |
| `assembly-explosion.mp4` | 从装配到爆炸的展示动画 |
| `parts.csv` / `parts.json` | 对象编号、名称、证据来源、推定状态和位移 |
| `observations.json` / `evidence/` | 原视频观察区域和来源 |
| `validation.json` / `engineering-ledger.json` | 文件检查结果、修复记录和未确定事项 |

独立重载通过 11 项文件与网格检查，包括零件清单对应、证据哈希、网格边界闭合、装配/爆炸姿态往返、GLB 名称及姿态保存。姿态最大误差约 1.33e-7 m，属于文件数值误差，并非实车精度。封闭网格可以包含重叠的实体片段，不等于通过 CAD 实体并集或干涉验证。

第一轮视觉检查修正了头管与舵管的轴线偏差；第二轮文件检查发现曲线封口没有焊接、GLB 导出器回到了动画起始帧。修复后产生 `model-v3`，保留 `model-v2/validation.json` 的失败记录。

复现：

```bash
.venv/bin/python scripts/prepare_bicycle_exploded_evidence.py \
  --source outputs/bicycle-ground-20261026/perception/inputs \
  --output outputs/bicycle-exploded-study-20261026

blender --background --python-exit-code 1 \
  --python scripts/blender/bicycle_exploded_study.py -- \
  --output outputs/bicycle-exploded-study-20261026/new-model

blender --background --python-exit-code 1 \
  --python scripts/blender/validate_bicycle_exploded.py -- \
  --model outputs/bicycle-exploded-study-20261026/new-model --animation
```

进一步提高实车零件准确性，需要型号及维修资料、轴端/连接点近拍、尺寸测量或拆解证据。现有视频能够支持外部结构探索，不能保证隐藏零件的准确性和完整性。

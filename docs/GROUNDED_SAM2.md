# Grounding DINO + SAM 2.1 替代后端

保留 `sam.backend = "sam3"` 默认路径；显式配置 `grounded_sam2` 可启用替代后端。SAM 3 的 checkpoint 不能用于此后端，SAM 2 的 checkpoint 也不能用于 SAM 3。

实现支持单图和离线视频：对每个 VLM 候选的英文 prompt 单独运行 Grounding DINO，将定位框交给 SAM 2 生成掩码。未定位到目标且允许 `box_fallback` 时使用 VLM 提供的框；没有框就不生成该候选的实例，不制造空目标。输出沿用原来的对象包、背景路由和检查流程。

## 安装与权重

需要现有 Linux/CUDA PyTorch 环境。以下命令需要在项目根目录执行；SAM 2 源码路径由你实际下载的位置决定。不会自动修改现有 SAM 3 配置。

```bash
uv pip install --python .venv/bin/python -e '.[grounded-sam2]'
git clone https://github.com/facebookresearch/sam2.git /your/path/sam2
uv pip install --python .venv/bin/python --no-build-isolation --no-deps -e /your/path/sam2
```

官方资料：

- [SAM 2 官方代码和公开 checkpoint 下载链接](https://github.com/facebookresearch/sam2#download-checkpoints)
- [Grounding DINO Tiny 官方模型文件](https://huggingface.co/IDEA-Research/grounding-dino-tiny/tree/main)
- [Grounded SAM 2 官方组合示例](https://github.com/IDEA-Research/Grounded-SAM-2)

先准备 SAM 2.1 Small 的 `sam2.1_hiera_small.pt`，搭配配置 `configs/sam2.1/sam2.1_hiera_s.yaml`。Grounding DINO 必须下载完整的 Transformers snapshot，包括权重、模型配置、processor 和 tokenizer 文件，仅有一个权重文件不够。

例如，手动执行以下命令下载 Grounding DINO（会下载模型文件）：

```bash
.venv/bin/hf download IDEA-Research/grounding-dino-tiny --local-dir models/grounding-dino-tiny
```

推理时使用 `local_files_only=True`，不会隐式下载任何模型。首次准备需要显式安装依赖和下载权重；这些操作不由推理命令触发。

## 配置与运行

复制 `configs/grounded-sam2.example.toml` 到一个 gitignored 的本地配置，例如 `configs/local-grounded-sam2.toml`。保留现有 Qwen endpoint/model/API header 设置，并填写：

- `sam.checkpoint`：本地 SAM 2.1 checkpoint。
- `sam.grounding_model_path`：本地 Grounding DINO 模型目录。
- `sam.sam2_model_config`：官方 SAM 2 包内的 Hydra 配置名称，需匹配 checkpoint；它不是相对本地 TOML 的文件路径。

checkpoint、Grounding DINO 目录的相对路径均相对 TOML 所在目录解析。配置模板默认为 SAM 2.1 Small。

```bash
.venv/bin/scene-perception --env-file .env.local run \
  --image test_media/images/scene.jpg \
  --config configs/local-grounded-sam2.toml \
  --output outputs/grounded-sam2-image-001

.venv/bin/scene-perception --env-file .env.local run-video \
  --video test_media/videos/scene.mp4 \
  --config configs/local-grounded-sam2.toml \
  --output outputs/grounded-sam2-video-001
```

每次使用新的输出目录。已有 `configs/local.toml` 未被切换；获得 SAM 3 权限后仍可使用原后端。

## 行为与限制

图片分数取检测分数和 SAM 预测 mask IoU 的较小值；VLM 框回退仅使用 SAM 的预测 IoU。分数不是校准概率，低分 mask 交给已有 uncertainty 路由规则处理。`grounding_threshold` 和 `grounding_text_threshold` 控制文字定位，`confidence_threshold` 控制后续 mask 整理。

视频在所选参考帧完成文字定位和 mask 分割，再用官方 SAM 2 视频 predictor 的 memory 传播实例。对象有独立 native ID，向前/向后覆盖所有选定帧；缺帧、身份列表异常、非有限值或尺寸错误会使阶段失败，并清理 predictor 状态。没有参考帧种子时输出无实例记录，由下游质量检查标记待审。全空的传播 mask 表示不可见，不等同于缺失帧。

视频不在后续帧重新检测新物体，因此参考帧未出现的对象可能漏检。跟踪记录的 `score` 始终是参考帧种子分数，不是每帧跟踪置信度；manifest 明确标记此语义。跟踪 ID 仅限当前片段，不证明跨遮挡物理身份。仍需独立标注评估。

`run.json` 使用 `grounded_sam2` 记录模型执行；tracking manifest 记录 `grounded_sam2_video`。保存 SAM checkpoint 和 Grounding DINO snapshot 文件 SHA256；不会将替代模型记录为 SAM 3 执行。建议另行记录 SAM 2 源码 git revision。

## 当前验证

本次新增测试覆盖文字 query 与候选对应、框回退开关、非零参考帧双向传播、原生 ID 返回顺序变化、缺帧失败与状态清理，以及路径解析。测试使用接口 stub，不代表真实模型推理或精度验证。当前全套测试 61 通过、1 跳过。下述真实 CUDA 测试独立于接口 stub 测试。


## 本机真实 CUDA 测试（2026-10-22）

经用户授权安装依赖并下载公开权重后，完成真实模型测试；不需要 SAM 3 的 gated 授权。保留原有 SAM 3 本地配置，另建 gitignored `configs/local-grounded-sam2.toml`；视频冒烟测试配置 `configs/local-grounded-sam2-smoke.toml` 限制为 6 帧、参考帧索引 2。

- SAM 2 源码：`/home/felixz/.local/share/roboenv/sam2`，commit `2b90b9f5ceec907a1c18123530e92e794ad901a4`。CUDA 扩展已用本机 CUDA 12.4 工具链编译并验证导入。
- SAM 2.1 Small：`models/sam2.1_hiera_small.pt`，由官方公开 URL 下载。
- Grounding DINO Tiny：`models/grounding-dino-tiny`，官方 revision `a2bb814dd30d776dcf7e30523b00659f4f141c71`。权重 689359096 字节，SHA256 `1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3` 与官方 Hub 元数据一致。网络超时后续传成功。
- 运行环境：Torch 2.10.0+cu128、torchvision 0.25.0+cu128、Transformers 4.57.6、huggingface-hub 0.36.2。为兼容 Transformers 4.x，本次依赖安装把此前的 huggingface-hub 2.2.0 调整为 0.36.2；SAM 3 权限仍需独立申请。
- 素材来自官方 SAM 2 示例：truck 图片缩放到 960×640；bedroom 的前 6 张 JPEG 编为 768×432、5fps 短视频。项目素材目录当时没有用户真实素材。

结果位于 gitignored `outputs/grounded-sam2-smoke-20261022/`，含安装来源、权重哈希、逐次 run、逐帧 mask、summary 和预览。

| 测试 | 模型与流程执行 | 质量结论 |
| --- | --- | --- |
| SAM 2 人工框图片测试 | 真实 GPU；约 1.03 秒含模型加载，峰值 allocated 显存约 624MB | 有效车辆 mask；没有 DINO，不代表组合测试 |
| SAM 2 人工框视频测试 | 6 帧，参考帧 2，双向全部覆盖；约 0.66 秒传播与初始化，峰值 allocated 显存约 740MB | 只验证 SAM 2 原生视频接口 |
| 首轮完整图片 | Qwen + DINO + SAM 2 执行、工件包有效 | 长关系提示词定位到整车，轮子计数检查失败 |
| 首轮完整视频 | 6 帧真实传播完成 | VLM 属性值 bool 不符合字符串 schema，语义阶段失败并保留记录 |
| `live-image-v2` | 完整执行，66 个工件校验通过；识别整车、两个车轮、道路 | `checks_status=fail`：墙面未通过置信度过滤，另有 curb/balustrade 漏检；没有放宽阈值 |
| `live-video-v2` | 完整执行、6 帧、7 条保留轨迹，398 个工件校验通过，NVENC 预览成功；总阶段耗时约 43 秒，tracking 约 4.87 秒 | `checks_status=review`：低分过滤、缺少可见关键候选及 unknown 区域，需复核 |

根据首轮实测，候选提示增加了简短通用名词、同类实例归并要求；语义提示明确要求 attributes 值使用字符串。第二轮保留严格 schema、同一置信度阈值和质量检查规则。图片与视频预览均已人工查看，主体掩码有明显有效结果，但没有独立真值，未计算 IoU、召回率或 ID 准确率，也没有宣称可直接用于重建验收。上述速度仅来自极短官方样例，不能外推到长视频吞吐。

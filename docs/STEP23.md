# 第二步离线视频与第三步基线评估

正式运行统一在 Linux/NVIDIA CUDA 服务器；没有 Mac/MPS 推理路径，也没有单独的 SSH 启动器。仍为固定程序调用，不包含 Agent。

## 环境与配置

在服务器安装官方 SAM 3 源码、CUDA PyTorch 和 `.[server]`。`scripts/bootstrap_cuda.sh` 可辅助创建 Python 3.12 venv 并安装依赖；它不会下载权重或设置凭据。安装后执行 `scene-perception doctor --require-cuda`，还需真实模型推理才能验证整个依赖组合。

推理与评估的 IoU 矩阵使用 CUDA。PyAV 解码、文件 I/O、规则处理和 SciPy 一对一匹配在服务器 CPU 上执行。视频预览优先请求 `h264_nvenc`，需要服务器 PyAV/FFmpeg 构建支持；编码器不可用时记录问题并保留逐帧 PNG，不伪装成 NVENC 成功。

`configs/api.example.toml` 的 `[video]`：

| 参数 | 默认值 | 语义 |
|---|---|---|
| `sample_fps` | 5 | 采样目标；低帧率源不补帧 |
| `max_frames` | 300 | 最多处理帧数；截断会报告 |
| `start_seconds` | 0 | 处理窗口起点 |
| `end_seconds` | 不设置 | 可选终点，不包含该时刻 |
| `reference_frame` | 0 | 处理帧集合中的索引 |
| `preview_fps` | 5 | 预览播放速率，与源 PTS 分开记录 |

必须有有效、递增的 PTS。保留源帧编号、PTS/time_base、时间戳、处理帧编号和图像哈希。当前拒绝未归一化显示旋转、动态尺寸或无时间戳输入，避免隐式改变 mask 坐标。未采样帧不会生成伪造 mask。

## 固定视频流程

1. CUDA/权重/API 配置检查；解码并采样。
2. 参考帧执行一次 VLM 对象概念解析。
3. 每个概念独立建立 SAM 3 视频会话，前后传播到选定处理帧；会话结束后释放。
4. 对最终参考帧分割执行一次 VLM 实例语义绑定。
5. 逐帧规则整理、项目对象 ID 分配、对象/背景包导出及预览。

SAM 原生 ID 使用概念命名空间，不直接充当全局物体身份。参考帧之外首次出现的新轨迹保存观测，但语义/重建路线保持未知；不自动追加 VLM 调用。别名合并冲突或一帧重复归属会生成歧义状态，不静默合并原有对象。

视频内 ID 是模型跟踪证据，仍可能交换。原生 ID 变更后的重识别、跨片段身份和在线无限流不在这一版内。

输出增加 `inputs/video.json`、`frames/<frame_id>/`、`tracks/index.json`、`stages/tracking/manifest.json` 和 `previews/tracking.mp4`。所有路径通过 `scene.json` 索引。`validate` 同时检查图像与视频工件，包括轨迹—观测链接。

## 独立标注格式

`prepare-truth` 仅复制归一化输入帧和空 `objects`；不会复制预测语义或 mask。独立标注后再冻结。一个最小可见对象条目：

```json
{
  "gt_id": "red-cup",
  "category": "cup",
  "mask": "masks/frame-000000-red-cup.png",
  "visible": true,
  "critical": true,
  "exclude_from_background": true
}
```

- mask 使用对应原图尺寸，单通道 PNG，值为 0/255，只标可见区域。
- `gt_id` 在同一视频中稳定；`category` 与预测类别按去空格/大小写归一后精确比较，不调用 VLM 判同义词。
- 支撑桌面可设置 `exclude_from_background=false`，杯子等独立物体为 true。
- 完全遮挡时显式记录同一 `gt_id`、`visible=false`、`mask=null`。只有显式遮挡才计算恢复事件，未标注帧间隙不当作遮挡。
- 每帧应完整标注参与评估的实例；不评分区域可以提供 `ignore_mask`。可见 GT 被 ignore 全部覆盖会报错，不悄悄缩小分母。
- 图像路径和 mask 路径相对 `truth.json`；不能越出该标注包。图片哈希由模板填写并在冻结/评估时验证。

`dataset.json` 包含 `dataset_id`、`annotation_source="human"`、`split`、`samples` 及门槛。每个样本有 `sample_id/kind/truth`。可以将不同标注包作为一个清单下的相对子目录。完整结构见 `schemas/evaluation.schema.json`、`schemas/truth.schema.json`。

预测映射单独保存，便于同一冻结数据集比较不同配置。示例（路径相对这个映射文件）：

```json
{
  "scene-001": "desk-video-001",
  "scene-002": null
}
```

放在 `outputs/predictions.json` 时，第一个路径就是 `outputs/desk-video-001`。null 表示失败/尚无预测，仍计入统计。也支持绝对目录。运行输出目录必须是新目录，避免覆盖旧基线。

## 冻结与指标定义

`freeze` 绑定清单、门槛、真值文件、图片和 mask 的 SHA-256。改动后评估拒绝继续，需形成新版本和新锁。预测结果在评估时另外绑定 run/config/artifact 索引哈希。报告记录阶段耗时、GPU 信息和 VLM usage（如果服务返回），费用未知保持 null。

实例采用类别约束的一对一最大匹配：先最大化达到 IoU 门槛的匹配数量，再最大化总 IoU。

| 指标 | 分母/规则 |
|---|---|
| 对象召回率 | 匹配可见 GT 数 / 全部可见 GT 数 |
| 关键对象召回率 | 匹配关键 GT 数 / 全部关键可见 GT 数 |
| precision | 匹配数 / 保留且非 ignore 的预测数 |
| 平均 IoU | 按可见 GT 等权，未匹配/失败样本记 0 |
| 前景泄漏 | GT 待排除前景中仍落入预测背景的像素 / 可测量的 GT 待排除前景像素 |
| ID 切换 | 同一 GT 的匹配预测 ID 发生改变，包括漏检间隔后的改变 |
| 遮挡恢复率 | 显式不可见后第一帧恢复可见时，恢复原预测 ID 的次数 / 有历史匹配的恢复事件数 |

缺失或损坏预测仍计入 GT 分母。缺失背景不能测量泄漏时单独记录 `foreground_pixels_unavailable`，并使门槛失败；不把缺失背景记为零泄漏。无对应分母的指标返回 null。

默认门槛：关键召回率 0.95、平均 IoU 0.85、泄漏率 0.02、ID 切换 0；另要求全部样本/帧可用。这是待真实素材校准的初拟值，应在正式评估前冻结。

`metric_gates_passed` 表示数值门槛。正式 `acceptance_passed` 另外要求 human 标注、真实执行的模型输出和 CUDA 评估。合成夹具或回放输出即使指标完美也不正式接纳。这里的接纳仅指感知基线，不是机器人任务成功或完整 Benchmark 评分。

## 测试夹具

`demo-video --output <新目录>` 生成 6 帧无损合成片段、显式遮挡、回放 mask 和独立几何真值，运行导出、冻结与诊断评估。夹具中的 NumPy 计算路径只用于验证指标定义，`--fixture-oracle` 对 human 数据集会被拒绝。不能把夹具通过称为 GPU 部署或真实精度验证。

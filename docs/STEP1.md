# 第一步：单图感知基线

本文记录第一步单图接口。离线视频和独立基线评估已在后续 [STEP23](STEP23.md) 中扩展；Agent、跨独立输入身份融合、深度恢复、mesh/3DGS 和最终机器人 Benchmark 仍不在当前范围。正式运行统一使用 Linux/NVIDIA CUDA。

## 实现约束

- VLM 两个预定语义阶段，SAM 一个分割阶段；没有自主工具选择或语义修复循环。
- 官方 SAM 图像接口按概念清除提示、复用图像特征。文本无结果时可固定尝试一次归一化中心格式的框提示。
- 阈值筛选和重复 mask 合并保留原始来源与原因；触及图像边缘的对象保留并标注。
- 任务角色、视觉用途和物理角色独立；路线冲突保留未知。
- ID 只保证同一图片运行内唯一，不声称跨图或跨运行身份稳定。
- 原文件保存副本。EXIF 方向归一化后，mask 坐标基准是 `inputs/frame.png`；xyxy 框右下坐标不包含。两种裁剪分别保存回到 frame 的平移矩阵。
- 整体/部件原始 mask 可以重叠；独立 mesh 实例重叠时，用途像素转为未知。
- 输出目录必须不存在。每次记录新 run_id、权重哈希、模型记录和最终工件索引。

## 阶段接口与来源

`pipeline.py` 提供 `stage_inventory`、`stage_segment`、`stage_labels` 和 `run_image` Python 接口。当前由固定程序调度，未来 Agent 可以调用同一套阶段。暂未实现命令行单阶段 resume、跨运行自动缓存或复杂标注 UI。

参考 SimFoundry 的[阶段产物](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/README.md)和[对象裁剪设计](https://github.com/NVlabs/SimFoundry/blob/main/scripts/pipeline/A_reconstruction/stages/5_decompose_scene.py)。本步骤不加入其生成式图像/深度补绘或完整重建依赖。

SAM 调用依据[官方图像处理器](https://github.com/facebookresearch/sam3/blob/main/sam3/model/sam3_image_processor.py)，区分输入归一化 cxcywh 框、输出像素 xyxy 框，并在不同概念间重置提示。

## 验证层级

1. 自动化测试：坐标、分区、失败状态、API 请求/响应和认证信息不进入调用日志。
2. 合成回放 demo：已知图像、人工语义和人工 mask，检查最终落盘工件。
3. 真实模型推理：等待用户素材、API 配置及 CUDA/SAM 环境；当前未运行。
4. 独立准确率：等待真实样本及人工真值；当前召回率、IoU、泄漏率留空。

程序检查通过不表示语义正确、三维可还原或机器人任务成功。

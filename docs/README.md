# 文档索引

当前主线是 `scene-harness` 的实验性自动构建；感知和背景阶段也可单独调用。先看 [仓库首页](../README.md) 和 [代码结构](ARCHITECTURE.md)。

## 当前方法与使用

| 环节 | 文档 |
| --- | --- |
| 自动构建、修复、复审与交付 | [Harness](AGENTIC_HARNESS.md) |
| 多图片输入与身份关联 | [媒体输入](HARNESS_MEDIA.md) |
| 可选网页检索与证据 | [联网路径](HARNESS_WEB.md) |
| VLM API 请求与配置 | [API](API.md) |
| 图像感知 | [第一步](STEP1.md) |
| 视频、独立标注与评估 | [第二、三步](STEP23.md) |
| Grounding DINO + SAM2.1 | [替代分割后端](GROUNDED_SAM2.md) |
| 对象证据、蓝图、背景 mask、桌面纹理 | [构建说明](CONSTRUCTION.md) |
| COLMAP / 3DGS 环境与训练 | [重建运行时](RECONSTRUCTION_RUNTIME.md) |
| Blender 后台建模与 Artisan 导出 | [建模后端](BLENDER_BACKEND.md) |
| 开发主机与依赖 | [主机准备](HOST_SETUP.md) |
| SAM3 权重准备与迁移 | [权重迁移](SAM3_TRANSFER.md) |

## 已有资产与专用实验

统一入口为 [实验目录](EXPERIMENTS.md)。各实验的输入假设、实际结果和限制仍保留在其原始文档中；它们不代表任意输入都可自动复现相同效果。

## 历史记录

验证日志、2026-10-26 资产盘点、阶段规划和 2026-10-27 Harness 运行记录统一放在 [history/](history/README.md)。历史文档中的待实现项和测试数量按记录日期理解；当前调用关系以源码和上述使用文档为准。

`outputs/` 和真实测试素材未提交，文档路径需要在保存了对应工件的运行主机上打开。

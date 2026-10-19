# 独立标注

在 CUDA 服务器上对图像/视频结果执行：

```bash
scene-perception prepare-truth --bundle outputs/desk-video-001 --output test_media/annotations/desk-video
```

该命令只导出输入图片、空 `truth.json`、数据集清单；不会把 SAM 输出当作真值。请独立标注可见 mask、类别、跨帧稳定的 gt_id、遮挡状态和是否应从背景排除，然后 freeze。

空模板不能冻结为有效评测数据。字段和指标定义见 [STEP23](../../docs/STEP23.md)。

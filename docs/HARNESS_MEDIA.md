# Harness 的视频与多图片输入

入口是 `scene-harness`，实现位于 `src/roboenv_perception/harness/cli.py` 和
`src/roboenv_perception/harness/run.py`。请求必须给一段任务描述，并选择一个视频或一组图片。
多图片模式的适配器是 `src/roboenv_perception/harness/media.py:run_images`；单图片直接调用
现有 `pipeline.run_image`，视频调用 `video_pipeline.run_video`。

```bash
.venv/bin/scene-harness \
  --task '重建桌上的杯子和盒子，并验证推碰与开合' \
  --images /absolute/path/front.jpg /absolute/path/side.jpg \
  --perception-config /absolute/path/perception.toml \
  --construction-config /absolute/path/construction.toml \
  --artisan-python /absolute/path/artisan-python \
  --blender /absolute/path/blender \
  --isaac-python /absolute/path/isaac-python \
  --output /absolute/path/new-run
```

`--video /absolute/path/clip.mp4` 与 `--images` 二选一。现有 VLM 负责逐张候选清单和实例语义，
SAM3 或配置的 Grounding DINO + SAM2.1 负责可见 mask。多图片输入逐张运行同一单图感知流程，
再把它们整理成下游 `prepare_objects` 可读取的 `scene.json`、帧记录、对象观察和证据图。
这一层没有估计相机位姿、深度或实物运动。

图片按命令行顺序编号 `image-0000` 等。`timestamp_seconds` 只保存排序代理，
`timestamp_semantics=image_order_proxy_not_elapsed_time`；`temporal_semantics=none`。
跨图片对象身份仅根据类别及品牌、型号、序列号、颜色等稳定属性提出候选关联，
同一张图片中无法唯一辨认的同类对象保持分开。代码 API 可传入 `identity_links` 提议关联，
但相互矛盾的类别、用途或身份线索会拆开并标成 `ambiguous`。这些关联都不是已验证的
物理身份，也不能把两张照片解释为运动前后状态。

运行目录保留每张输入图片的原文件、归一化图、原单图 run、mask、裁剪和质量报告。
`inputs/images.json` 记录顺序、文件名、SHA-256 与对应的文件引用；所有最终文件被
`artifacts.json` 绑定。原图在处理过程中改变会使本次运行失败，完成后改变包内副本也会使
`verify_run` 和 `validate_bundle` 失败。输出的 `quality.json` 对身份、重复图片和视角解释
保留 `review` 问题；下游交付必须人工复核这些问题，不能把视觉预览当成时间连续性证据。

`prepare_objects` 从这些帧中选取可见、清晰的对象视图并生成 reference board。
它使用共同参考帧帮助后续场景摆放，但图片序号之间的距离不是相机角度或秒数。
多视角几何、真实尺度和相机外参仍需额外标定或恢复。

离线检查：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q tests/test_harness_media.py
```

测试使用 replay 夹具，只验证文件契约、保守关联与来源校验，不代表真实 VLM、SAM3 或
跨视角识别准确率。

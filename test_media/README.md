# 测试素材入口

把你提供的图片放在 `images/`，视频放在 `videos/`。这是主仓库内的素材目录，未创建嵌套 Git 仓库。

单图使用 `run --image ...`；离线视频使用 `run-video --video ...`。正式流程全部在 Linux/NVIDIA CUDA 服务器上运行。

原始图片/视频默认被 `.gitignore` 排除，可留在本地。需要共享时显式选择文件提交；较大的视频建议之后配置 Git LFS，当前未安装或配置 LFS。

示例：

```bash
scene-perception run --image test_media/images/scene.jpg --config configs/local.toml --output outputs/scene-001
scene-perception run-video --video test_media/videos/scene.mp4 --config configs/local.toml --output outputs/video-001
```

请不要把 API 密钥、个人敏感文件或模型权重放进这个目录。

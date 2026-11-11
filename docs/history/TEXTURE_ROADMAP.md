# 背景材质工具的后续规划

此文件仅记录候选；当前不安装、不试跑、不替换已生成的桌面纹理。

| 开源候选 | 可借鉴能力 | 接入方向 |
|---|---|---|
| [bmquilting](https://github.com/bmad4ever/bmquilting) | 纹理块合成、接缝处理、缺损纹理输入、可平铺纹理生成；MIT | 优先评估为 Python 材质工具，README 要求 Python 3.12+ |
| [GIMP Resynthesizer](https://github.com/bootchk/resynthesizer) | 纹理转移、图像补全的库及 GIMP 插件；GPL-3.0 | 可作为 PS 内容识别处理的开源参照；GIMP 3 对应 resynthesizer3 分支 |

上述能力依据项目说明，未在本项目验证效果、速度或兼容性。候选不等同于 Photoshop 的完整
实现，也不等同于单张照片恢复真实 PBR 材质。算法基础可参考
[Image Quilting](https://people.eecs.berkeley.edu/~efros/research/quilting.html)。

后续工具输入应为多块可靠的表面纹理及来源、有效 mask、透视/方向信息，先校正到表面坐标，
剔除物体和持久阴影、减弱拍摄高光，再合成低重复感的大贴图。无缝边界与不显著重复是两个
不同目标；不能仅用同一小块镜像平铺作为完整材质提取方案。

Agent 根据材质类型、任务敏感度和观测质量，在简单颜色、程序纹理、样本合成、补拍之间选择。
合成纹理只进入外观证据，不反过来作为尺寸、支撑面或摩擦的测量证据。优先完成接触与几何。

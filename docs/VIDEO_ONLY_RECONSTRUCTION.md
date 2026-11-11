# 仅视频的相对尺寸重建

本实验通过原生 EnvCreator `reference_*` 工具的 `video_only` 策略执行。DSH 仍为唯一任务模型循环，内部 VLM 负责新候选 Coding 和独立视频外观/几何 Review，没有增加独立聊天循环。

## 方法与入口

```text
同视频 SHA → 原始 COLMAP 相机 + 视频像素角点
 → construction/video_relative_fit.py：三角化初始化、矩形面板重投影拟合
 → 机身宽度=1 的相对构建约束
 → 新内部 Coding → Artisan 编译
 → 原视频屏幕/键盘透视校正
 → 蓝图 QA + 实际导出网格投回视频掩码
 → Blender 实际候选渲染 → 独立视频外观/几何审核
```

原生调用：`envcreator_reference_fit(sources.perception=<同视频感知运行>, params.evidence_policy=video_only)`。后续以最新 `sources.reference` 调用 `reference_code(params.asset_id=asset-0002)`；验证阶段为 `appearance`、`qa`、`geometry`、`render`，最后 `reference_review(params.asset_id=asset-0002)`。本策略仅注册当前电脑视频，不支持瓶子/鼠标或场景物理审核；错用米制仿真阶段会明确返回未验证。

`components/video_only_workflow.py` 提供受限领域适配，产物继续保存为版本化 `reference` 快照：`fit/relative-fit.json`、`objects/asset-0002/build-spec.json`、新 `model.py` 与编译/导出、`appearance/`、`fit/geometry-validation.json`、`renders/`、`review.json`、`reference.json` 和 `observation.json`。运行事件、call ID 和 CPU/GPU/进程资源采样沿用既有调度器。

## 来源边界

允许原视频帧、视频 SAM 掩码、原始 COLMAP 内外参和同视频已保存的人工像素角点。明确标记人工辅助和视频派生缓存，不能称为全自动新识别或全流程重新运行。

不读取旧产品规格、旧 Blueprint、旧尺度拟合、米制 `sfm_to_sim`、桌面宽度/高度或旧模型。拟合残差只有像素投影，没有产品长宽比惩罚；初值由视频角点三角化形成。网络搜索和研究工具不进入本轮调用链。内部模型 API 推理仍是新 Coding/Review 的运行依赖，不是网络资料检索。

矩形面板、共享宽度与铰链关系是明确的几何假设。无已知长度、真实深度或米制相机运动时，单目视频及 SfM 只能确定相似变换意义下的形状，绝对米制尺度不可辨识。将物体、相机平移同时放大两倍不改变投影；验证报告保存该对照。

SDK 目前要求米制命名字段，适配层将宽度=1 的示意坐标传入这些旧字段，同时标记 `absolute_scale_observable=false` 和 `coordinate_units`。这不构成米制恢复。UI 使用“×机身宽度”，不把该值转换为厘米。

厚度、质量、阻尼/摩擦、关节行程不能由该静态开合视频确定，均为明确示意占位，只用于生成可编译模型；没有动态物理、真实接触、实物手感或全角度悬停验收。下载 USD 也须保留此限制，不能直接用于真实尺寸机器人任务。

## 验证与性能解释

几何报告分别给出零厚度拟合面板与**实际导出 URDF 视觉网格**在视频相机下的掩码 IoU；实际网格按导出的局部原点/旋转与铰链开角变换，不能用拟合面板替代候选评价。两者沿用该案例 0.94 的平均轮廓阈值，并报告未用于面板拟合的其他视角。所有视角仍参与了相机估计，不能称为独立计量验证。

独立 Review 接收视频图像、候选渲染、实际几何指标、当前代码与 QA，判定只限相对比例、外观和构建约束；必须说明缺少绝对尺度和物理证据。Review 保存候选与导出依赖绑定，避免使用旧候选图像。

性能报告区分领域调用内部耗时、整次工具运行耗时、原生会话总耗时；分开记录 API 等待和本地 CPU/GPU 渲染。该实验复用了视频感知/相机缓存，只重建笔记本，没有本轮物理仿真，不能用其总耗时直接宣称快于上轮三对象+物理流程，更不能外推多任务并行能力。

本轮实际暴露并修复了几处边界：冻结示意模板的铰链间隙导致编译失败；零原点在 URDF 中可省略，读取器需按零处理；Blender 脚本异常须使用 `--python-exit-code 1` 并检查实际结果/图像；DSH 工具输出必须为无损 JSON，缺失字段使用显式 null。独立视觉审核曾漏报照片方向旋转 180°，外层实际看图后修正 UV 并补角点映射回归；重渲染撤销旧审核，新 Review 绑定真实渲染图哈希。

原生 `context` 现仅回传近期 12 个运行身份及总数，`inspect` 返回来源调用摘要，完整依赖哈希仍在原产物中。该变化针对真实发生的模型上下文溢出；不会删除历史或降低来源核对。长期会话需要显式有界交接，不能把重复完整清单当成必要上下文。

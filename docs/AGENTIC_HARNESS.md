# 从视频或图片构建交互场景：当前 Harness

记录日期：2026-10-27。入口是 `src/roboenv_perception/harness/cli.py` 的 `scene-harness`
（或 `python -m roboenv_perception.harness.cli`）。给一段明确物体与交互的任务描述，
再给一个视频或一组图片；输出目录必须是新路径。`run.py:run_harness` 按阶段保存状态，
相同输入可从完成的阶段恢复；输入、配置、回放响应、控制器或 worker 代码指纹发生变化，
以及已完成阶段的任一已索引产物缺失或变化时，拒绝沿用旧结果。

```bash
.venv/bin/python -m roboenv_perception.harness.cli \
  --task '重建桌上的杯子和带铰链盒子，验证推碰与开合' \
  --video /absolute/path/clip.mp4 \
  --perception-config /absolute/path/perception.toml \
  --construction-config /absolute/path/construction.toml \
  --artisan-python /absolute/path/artisan-python \
  --blender /absolute/path/blender \
  --isaac-python /absolute/path/isaac-python \
  --output /absolute/path/new-run
```

多图片时将 `--video` 换成 `--images image1.jpg image2.jpg ...`。VLM/SAM、Artisan、
Blender 和 Isaac 各自使用配置指定的本机环境；公开网页搜索可用 `--no-web-search` 关闭，
物理测试可用 `--no-physics` 关闭。关闭物理测试的交付结果不会成为物理通过。

## 代码调用链与产物

1. `run.py:_perception` 调用现有 `pipeline.run_image` 或 `video_pipeline.run_video`。
   VLM 提出对象候选、整理实例语义，SAM3（或配置的 Grounding DINO + SAM2.1）输出
   可见区域 mask。多张独立照片由 `harness/media.py:run_images` 逐张处理，保留原图和
   哈希；跨图身份只是候选关联，没有相机轨迹或运动时间。产物为 `perception/scene.json`、
   mask、裁剪和 `quality.json`。细节见 [多图片输入](HARNESS_MEDIA.md)。
2. `construction/evidence.py:prepare_objects` 从对象观察中选参考视图和证据板。
   随后 `harness/scope_plan.py:plan_object_scope` 根据原始任务和图像证据，先由独立
   作者提出每个对象的 `build/background/exclude/unresolved` 范围，再由另一轮模型调用
   复核；最多两轮作者/复核，保存 `scope/scope_plan.json`、输入快照及逐轮 trace。
   范围未独立通过时保留 unresolved，并阻断完整交付。
   `harness/research.py:research_objects` 仅对已接受范围中的 `build` 对象、且 VLM 能
   区分系列或型号时调用
   `harness/web.py`；网页正文引文进入候选事实，不直接成为测量或物理参数。
   搜索服务、来源校验和网络限制见 [联网证据](HARNESS_WEB.md)。
3. `construction/design.py:design_objects` 对已接受范围中的 background/exclude 对象
   直接保存 route，不再为背景对象生成机械蓝图；对 build 对象从图像证据、任务及
   候选网页事实生成部件、关节和交互蓝图，并把自检绑定蓝图文件的 SHA-256。理想固定比传动由
   `construction/contracts.py:Coupling` 明确记录驱动/从动关节、有符号比例和证据来源；
   它不宣称看到了实体链条、齿轮齿形或制造结构。
4. `harness/geometry.py` 接收类型化几何 recipe，不执行模型生成的 Python。
   受信任的 `blender_recipe.py` 生成部件 mesh 和多角度预览。通用 `lathe` 用
   2–32 个严格递增的局部 `(z_m, radius_m)` 剖面点及固定 3–64 边环，构造共顶点
   连续侧面和顶底封口，适用于封闭容器外轮廓、灯座、轴等；它不表示真实内腔或
   壁厚，开放容器仍用 hollow 几何。所有尺寸仍需记录观察、估计或假设来源。
   该实现扩展通用形状接口，不硬编码瓶子模板。`_model_source` 把蓝图
   变成 Artisan 模型并导出 USD、URDF、MJCF。独立 VLM 将预览与原始证据对照；
   `REDO` 将具体问题送回下一次候选。几何修订有次数和总时间上限，保留每次 recipe、
   模型、编译结果与审查记录。预览按关节树累加 child origin，避免部件局部坐标被当作
   装配坐标；供碰撞编译使用的 OBJ 只保留位置索引面，避免法线/UV 分裂把封闭壳变成
   开放网格。`harness/usd_adapter.py` 将嵌套刚体层级展开为同级，保持静止装配姿态，并重映射
   关节及 mimic 的目标关系，适配后重载检查引用。
   `construction/blender_worker.py` 另输出水平正交 `front.png`，与斜上、顶视、底视图
   一同提供；真实 smoke `outputs/harness-review-views-20261027/` 已成功生成四视图。
   `geometry.py` 的作者与复核输入均带 1-based `image_manifest`，明确原始证据/候选
   渲染角色、引用、SHA-256，以及渲染视角，降低原图与展示图或不同投影之间的混淆。
   顶视或底视投影可遮住肩部，不能单独据此判为缺肩；真实缝隙、缺件仍是阻断问题。
   几何修复将 `previous_recipe` 与具体反馈传给 `infer_recipe`，要求保留已有合理部分，
   在 notes 写明改动的 solid、尺寸或剖面；不接受把原 recipe 原样返回当作修复。
   输入标注和新视图只减少歧义，不能保证模型复核判断可靠。
   修复次数耗尽时保留先前视觉通过的候选及独立 `repair_attempts`、失败理由；这不会
   让旧候选变成物理通过。视觉通过仅表示该轮外观审查通过。
5. `harness/scene.py:assemble_scene` 复制 USD 及依赖，建立带静态碰撞和接触材质的
   支撑面，把各资产按同一帧中的框底中心暂时摆到支撑面上。`scene.usda` 和
   `layout.json` 记录尺度、摆位与来源；图片框到世界坐标是近似映射，不能当作相机标定。
6. `harness/physics.py` 启动 `isaac_generic_worker.py` 重载实际导出的场景，
   施加外力或力矩并检查释放前后的仿真状态；报告必须绑定输入哈希和预定协议。
   刚体、单铰链、旋转及固定比耦合的协议各有不同检查。耦合测试要求从动响应比例，
   同时在匿名 USD session layer 中生成沿 +Y 偏移 20 m、移除 `PhysxMimicJointAPI`
   的配对副本。测试临时固定两组基座，并施加同样的外部力矩，对比驱动、从动转角；
   原始交付 USD 不被改写。直接写入关节角不能计为动力学通过。刚体推力取
   `2 × 初始 recipe 总假设质量 × 9.81` N，驱动 0.05 s、释放 0.1 s；质量是模型假设，
   不是图像测量。首轮将完整 `protocol` 写入逐项测试，后续 repair 沿用最初推力、
   时程和阈值，即使 recipe 质量改变也不重调测试条件。
7. `run.py:_background` 可调用背景 mask、COLMAP 相机恢复和 3DGS 训练。
   3DGS 只供背景外观，物体交互来自 mesh、碰撞体及关节。
8. `harness/task_scope.py:audit_task_scope` 以独立 VLM 比较原始任务、资产 brief、最终
   Blueprint 及逐项实际检查，保留输入快照与 trace。每项要求列出资产、family、测试
   引用、理由和 `covered/unsupported/unverified`；确定性校验要求 `covered` 引用同一
   资产、同一 family 的 passed 检查，且实际文件哈希匹配。外观、路由与布局分别有
   以下受信任引用，不能把它们当作动力学证据：
   - `asset_id/visual-review`（family=`visual`）：资产标记为视觉通过，选中 attempt 为
     `PASS`，磁盘审查与 attempt 一致；以实际 model、recipe、USD、预览重算
     `candidate_sha256`，并核对所选资产、候选及审查绑定。只支持已审查的主要可见外形。
   - `asset_id/background-route`（family=`scene`）：资产明确 routed 为 background 或
     exclude，且与 `designs.json` 的资产、对象身份、状态和 route 一致，记录文件哈希。
     这表示按任务保留背景/排除对象，不表示已重建该对象。
   - `scene/layout`（family=`scene`，无 asset_id）：可选 `scene` 参数提供组装结果与
     layout；要求 assembled、无 issues、场景引用一致、实际 USD 哈希匹配，并保存
     结果、布局和 USD 哈希。它只覆盖近似组装，不证明绝对尺度或相机标定。
   当前物理协议只覆盖水平刚体推碰、被动铰链、旋转轴响应和理想固定比耦合；抓取、
   拿起、骑行、制动、机器人任务和真实机械设计不能用泛化推碰或视觉通过替代。
   所有要求 covered 且审查明确无遗漏，任务覆盖才通过；缺失、未运行或审查失败均
   不通过交付门禁。最终 `delivery/delivery.json` 汇总资产、场景、物理报告、任务覆盖、
   网页来源和待复核问题；交付资产行的 `physics_status` 与最终物理报告同步。

运行根目录还有 `state.json`，资产阶段保存每个候选与日志。每个已完成阶段以
`.harness-artifacts.json` 绑定该目录全部文件及 SHA-256，恢复时逐项检查；场景阶段另用
`verify_run(path, "layout.json")` 核对哈希索引；早期把 `scene.usda` 当作 JSON 读取的错误已修复。
`delivery.status=verified` 需要设计、视觉审查、场景组装、所需物理检查和原始任务覆盖全部通过，
且没有身份冲突等复核标记；否则明确输出 `unverified` 或阶段失败。该字段只表示程序
门禁通过；模型视觉 PASS 可能误判，不能单独等同于外形已验收或稳定重建成功。

## 已有实验与限制

完整视频回放、真实图片与视频、物理协议回归及失败案例见 [2026-10-27 运行记录](history/HARNESS_RUNS_20261027.md)。这些记录包含视觉误拒和误放行；当前模型复核仍不可靠，不能宣称稳定自动重建成功。

同一份代码和输入的恢复会复用已有阶段，不是重新运行模型或独立复现实验。调整本仓库源码后，旧运行会因指纹变化拒绝恢复，需使用新输出目录；历史产物仍保留。

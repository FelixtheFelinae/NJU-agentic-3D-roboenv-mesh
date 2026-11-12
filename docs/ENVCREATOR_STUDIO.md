# EnvCreator Studio

Studio 复用 ClawSpine 使用的 DSH 原生会话、模型请求、插件、历史和取消机制。SAM、内部 VLM、COLMAP、3DGS、Blender、Artisan 和验证实现均属于 EnvCreator。DSH 任务 Agent 通过 EnvCreator 插件调用这些组件，工作台不另建模型聊天循环。

## 已实现的入口与调用关系

```text
envcreator studio
  → workbench/cli.py + launcher.mjs
      独立 DSH profile、任务模型选择、私有后端生命周期
  → integrations/studio/host.mjs + scope.mjs
      原生 EnvCreator 工具、会话/调用 ID、取消、认证后的页面代理
  → workbench/server.py → jobs.py → worker.py
      上传、队列、子进程、组件来源绑定、不可变新版本
  → components/operations.py
      ├─ perception.py → pipeline.py / video_pipeline.py / harness/media.py
      ├─ construction/design.py / harness/geometry.py / assembly.py
      ├─ harness/run.py 的场景组装、物理验证与交付实现
      └─ construction/reconstruction.py / train_worker.py → overview.py
  → execution/context.py + artifacts/registry.py
      内部事件、产物类型、来源及文件哈希
  → workbench/presentation.py
      从实际产物投影出适合人观察的数据，不添加总结模型调用
  → integrations/studio/src/workspace.jsx + model.jsx
      分割交互、结构、模型、场景、验证；详细记录按需展开
```

`envcreator_generate` 保留完整旧 Harness。组件操作只执行明确的一步；DSH Agent 决定下一步和修订，组件 worker 不启动 Agent 循环。已有领域方法继续负责判定，执行完成不等于独立审核或物理通过。

## 使用

Python 3.12+、Node 24；外部 ClawSpine checkout 提供锁定的 **DSH 0.2.1-alpha.1** CLI 和协议辅助模块，尚未实现完全独立安装。

```bash
.venv/bin/python -m pip install -e '.[studio]'
npm --prefix integrations/studio ci
npm --prefix integrations/studio run build
cp configs/studio.example.toml configs/local-studio.toml
.venv/bin/envcreator studio --config configs/local-studio.toml --doctor
.venv/bin/envcreator studio --config configs/local-studio.toml --open
```

配置相对路径按启动工作目录解析。启动器给出本机 DSH 认证链接，首次访问交换 Cookie。默认端口 8890。任务历史保存在 Studio state 目录，重启后继续使用。

在原生会话标题栏点击 **EnvCreator**，或在会话视图列表选择 **EnvCreator**。可拖动 DSH 原生分隔线或全屏查看。

1. 在“实验记录”创建或打开任务，加入图片或一段视频。
2. 在工作台输入需求，或使用 DSH 原生对话。两处都提交给同一个原生会话。
3. Agent 自主选择 EnvCreator 工具。单图可拆开执行识别、SAM 与语义；视频沿用现有跟踪入口。
4. 从图像中点选区域；Shift 多选后可合并真实掩码或排除区域。人工操作有独立来源记录。
5. 在结构、模型、场景、验证页观察当前结果，继续用自然语言要求局部修改。
6. 可选择旧版本、比较真实渲染、下载已有资产；原始事件、JSON、错误和哈希放在“技术详情”。

## 页面与产物

| 页面 | 实际显示 | 交互 |
| --- | --- | --- |
| 输入与分割 | 原始帧、实际二值掩码叠加、用途与路由 | 选择帧、调透明度、点选、合并、排除、自然语言修正 |
| 结构 | Blueprint 部件层级、关节关系、尺寸来源和假设数量 | 选择部件、说明固定/可动要求、补充尺寸 |
| 模型 | Blender 实际渲染、真实分件网格 | 旋转缩放、部件高亮、重渲染、版本对比、下载 |
| 场景 | COLMAP 状态、注册视图数、尺度状态；一张实际 3DGS 渲染 | 自主重建请求、从完整检查点恢复 |
| 验证 | 实际状态、条件、协议项、已有图片/视频和报告 | 请求验证、准备交付、下载 |
| 运行监控 | CPU/GPU/内存/磁盘、作业和进程关联 | 独立 Infra 观察页 |

“重建链路”按需展开主要组件节点及版本，可回看某一步。它展示可审阅的步骤和产物关系，不输出模型私有推理。页面没有把内部日志逐条当成用户消息。

场景总览目前使用已标定的真实相机视角，明确 `panorama=false`；没有实现任意完整全景。原始高斯 PLY、点云和三角网格有不同类型，高斯文件不当作普通三角网格展示。OBJ 支持已有颜色与部件变换，不自动加载相邻 MTL/贴图；USD 原文件可下载，尚无通用 USD→GLB 转换。对比相机独立，尚无同步视角或几何差分。

## 原生工具

`envcreator_context` 返回当前会话任务、上传 ID、当前对象、兼容来源、能力和缺失环境；`envcreator_inspect` 查看实际错误与产物；`envcreator_generate` 保留完整 Harness。组件工具统一加 `envcreator_` 前缀：

| 组件操作 | 使用的领域方法与产物 |
| --- | --- |
| ingest | 规范化真实图片，保留原图与来源哈希 |
| inventory | 内部 VLM 提出候选对象 |
| segment | 真实 GroundingDINO/SAM 分割，输出掩码与叠加 |
| label | 内部 VLM 判断类别、用途、物理/外观路由，写规范感知包 |
| perceive | 现有视频解码/跟踪或多图身份假设工作流 |
| perception_edit | 合并真实掩码、排除、有限语义标注；不伪造新视觉识别 |
| evidence | 提取独立对象的原始帧、裁剪、掩码与证据板 |
| scope / scope_apply | 独立范围规划，或记录用户明确选择；两种来源分别标记 |
| research | 已有资料检索与来源适用性核对，不把搜索结果直接当实测 |
| blueprint / blueprint_review | 结构提案与绑定文件哈希的独立审核 |
| geometry | 几何 recipe，可绑定旧 recipe 局部修订；标准组件签名保持冻结 |
| assembly_audit | 连接编译与装配几何审查 |
| build / render | Blender + Artisan 构建物理资产，或单独重渲染 |
| geometry_review | 原图与实际渲染对比，记录视觉审核；不等同物理通过 |
| assets_merge / scene | 合并不同对象的已记录资产，再组装场景 |
| physics | 现有受支持验证协议；保留失败与未支持状态 |
| background / cameras | 背景观测准备与 COLMAP，相机未注册完整时保留失败 |
| gaussian / overview | 真实 CUDA 3DGS、检查点恢复、真实相机视角渲染 |
| task_review / export | 任务范围核对与带实际验证状态的打包 |
| reference_fit / reference_code | 对同一已注册电脑原视频复算历史人工观测拟合，以冻结蓝图生成新候选；不复制旧模型 |
| reference_validate / reference_review | 参考流程的照片投影、实际 QA/物理/重载/反例复查与独立审核；明确条件与失败 |

工具返回 `agent_observation`，包含受限的结构化对象、输出角色/运行 ID、审核意见和编译错误；这一路径供 DSH Agent 自修复，和人类页面的简短投影分开。修订可把具体问题放在 `params.feedback`，保留完整原目标在 `task`。

调用 `sources` 是“角色 → 已记录运行 ID”，不是任意磁盘路径。来源须属于同一任务并完成；恢复只接受完整、哈希绑定的检查点。工具保存来源快照，在执行前后验证实际文件和传递依赖。不能把不同上游版本静默拼接。

局部修订生成新目录，旧模型、报告和验证保留。人类投影按依赖判断过期结果，过期物理结论不会出现在推荐的当前工具来源中。历史数据缺少原生调用时不补造。

视频/多图的直接修正按指定帧生效，不自动声称跨帧传播；跨帧身份仍是既有模型假设。逐步 `label` 当前限单图，多图使用 `perceive`。用户尺寸通过 Blueprint 参数 `measurements` 的 `measurement:x/y/z` 提供，单位米；自然语言中的模糊尺寸不能自动冒充实测。

## 模型、执行与记录

DSH 任务模型与 EnvCreator VLM 分开配置：`model_config_path` 指向独立 DSH TOML；`env_file` 只供 EnvCreator worker 读取内部凭据。不向 DSH 转交 `VLM_API_KEY`，不静默回退到 Qwen。

私有后端只监听 loopback，浏览器经 DSH 认证代理访问。浏览器不能访问原生调用入口来伪造调用 ID；有限的人工作业入口标记 `origin=user`。文本查看器脱敏，配置和私有日志不作为普通资产公开。

```text
state_root/
  history.sqlite3          任务、绑定、输入、运行、选择与取消记录
  inputs/                  输入文件与哈希
  runs/<run_id>/
    spec.json              私有执行配置
    provenance.json        原生调用、源码/输入哈希与来源
    events.jsonl           共享执行上下文的内部事件
    resources.jsonl        活跃期间实际资源采样
    worker.log             原始日志
    output/
      component.json       组件、导出角色、依赖及产物哈希
      state.json           执行结果与验证状态
  dsh-home/                DSH 原生会话与模型/工具轨迹
```

SSE 支持按事件字节偏移续读，浏览器另有低频刷新兜底。浏览器断开不取消运行；DSH 原生取消和停止按钮会停止本工作台管理的作业及记录到的子进程。取消先于开始的调用保留于 SQLite，重启后不能重放启动。进程异常退出后标为中断，尚无自动重新附着存活 worker；可从已完成组件或完整训练检查点继续新版本。

## 资源监控

约每 2 秒通过 psutil 与 nvidia-smi 采样，页面约每 2.5 秒更新。CPU/GPU 曲线明确为整机占用；进程 CPU 按单核心计，可超过 100%。作业数、受管理进程数、系统总进程数分别显示。缺失计数为未知。

活跃作业保存资源记录，当前页面曲线为最近约 10 分钟，尚无持久曲线回放、NVML、远程计算节点或动态 GPU 分配。部署设置只读，修改 TOML 后重启生效；并发默认 1。

## 验证入口

[历史检查记录](https://github.com/FelixtheFelinae/NJU-agentic-3D-roboenv-mesh/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/ENVCREATOR_STUDIO.md)

测试夹具覆盖接口、生命周期、真实掩码并集、视频修正后的轨迹引用、依赖篡改拒绝、过期验证、事件续读、取消持久化、原生身份与前端状态清理。夹具不作为真实 VLM、GPU 重建、物理功能或浏览器视觉验收。实际运行与逐阶段验收记录见 `reports/STUDIO_COMPONENT_ACCEPTANCE_20261109.md`。

多场景、前景/背景分支并行、容器与资源调度的长期要求见 [Infra 长期规划](ENVCREATOR_INFRA_ROADMAP.md)。该规划本轮只记录，不实施。

人工监督参考流程的受限工具接入、证据复用条件与执行准则见 [参考流程辅助重建](https://github.com/FelixtheFelinae/NJU-agentic-3D-roboenv-mesh/blob/43d3c72b3f7699e32eb297816828a5ed9874a232/docs/REFERENCE_GUIDED_RECONSTRUCTION.md)。当前组件目录共 30 项；4 个 reference 操作只支持注册的同视频电脑场景，不表示通用组件已经具备自动角点标定、任意物体照片投影和通用独立重载能力。

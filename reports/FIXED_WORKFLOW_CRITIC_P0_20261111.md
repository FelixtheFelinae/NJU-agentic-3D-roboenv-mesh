# 固定重建 Workflow 与 Critic：第一轮修改和验证

2026-11-11。保留已有 Agent、组件、Artisan/Blender 与验证基础设施。
本轮交付单目标 P0。没有安装依赖，没有提交 Git，没有删除旧产物或重启正在使用的 Studio。
工作目录原有的大量改动保持原状。

## 已实现的执行路径

| 位置 | 本轮变化 |
| --- | --- |
| harness/workflow.py | 固定执行顺序；采样、对象、结构与失败回访决策；版本检查；阶段收据、依赖哈希、锁、原子状态；中断恢复；最多一次局部几何修复 |
| harness/workflow_evidence.py | PyAV 原视频时间预览；有界采样参数；源视频哈希 + 原始 PTS 标识；检测当前建模参考是否增加原始帧 |
| harness/workflow_critic.py | 整理现有检查与审核结果，绑定候选、真实观测与报告；确定性几何失败不能被模型 PASS 覆盖 |
| components/operations.py | 工作流调用可传有界视频采样；结构自检查和装配检查失败可落盘返回控制器；默认旧调用仍维持原有阻塞行为 |
| harness/assembly.py | 显式控制失败审核是抛出异常还是返回失败报告；构建门禁不变 |
| harness/workflow_cli.py、integration/cli.py | 新增 envcreator workflow start/advance，与 DSH 调用同一控制器 |
| workbench/jobs.py、worker.py、server.py | 原生会话/调用身份、当前任务内解析初始 workflow ID、独立发布每次快照；等待决策时释放 worker |
| Studio host.mjs、scope.mjs | 默认仅 context / inspect / workflow_start / workflow_advance；旧组件由 debug_component_tools 开启；使用 DSH 原生图片附件 |
| Studio workspace.jsx、workbench/presentation.py | 视频与对象画面、简要结构、渲染和检查；继续、修订、回访、停止按钮通过原生 DSH 对话发起决策 |

正常顺序：VLM/SAM 感知 → 对象证据 → 目标选择 → Intake/结构作者/自检查 →
独立结构审核 → 结构决策 → 后端可表达性检查 → 几何配方 → 确定性几何检查 →
装配检查 → Blender/Artisan 构建 → 独立外观审核。

几何检查明确归因于配方时只修配方，并重新执行门禁。
结构与证据歧义交回原生 Agent。失败渲染保留为诊断，不能变成真实观察。
后台 COLMAP/3DGS 与 Isaac 物理验证是显式可选分支，默认关闭。
既有资源监控沿用原实现，没有增加资源调度器。

## 真实笔记本视频验证

工作目录：
/var/tmp/envcreator-workflow-laptop-fPnkRz

原视频：
outputs/studio/runs/2fb28537471e4fce/output/perception/inputs/source.mp4

视频 SHA256：
08be6039615c0b0807ddad022272f8cbd27204024e3d7cdd02604c516df9f34e

实际时长 13.6649 秒，1920×1080。
此次重新解码、调用内部 VLM 与 Grounding DINO/SAM2，不复用旧 SAM 遮罩或旧蓝图。
内部 VLM 使用本地感知/构建配置；没有 DSH 任务模型请求，没有网络搜索规格。
决策由本次外层 Codex 根据原始预览作出，经共享 CLI 控制器执行；
这是实际 Workflow/内部模型验证，不是 DSH 任务模型的自主运行实验。

| 步骤 | 实际输入与结果 |
| --- | --- |
| 时间预览 | 原视频 8 格预览，展示正面、侧面和背面；不是相机覆盖度测量 |
| 首轮采样 | 0–8.5 秒，0.85 fps，最多 8 帧，参考帧 3；实际 8 帧，选 6 张笔记本参考 |
| 首轮结构 | 新对象 studio:run-38d6507b7967:t0002；屏幕、键盘与触控板有机身父对象关联。输出机身与屏幕两个部件、1 个 revolute 候选；独立结构审核通过 |
| 首轮构建检查 | rest 未知，后端拒绝；行程和绝对尺寸仍未知。没有调用几何作者或构建器 |
| 回访决策 | 针对机身/屏幕关系及铰链区域，选择 8.5–13.66 秒，1 fps，最多 6 帧，参考帧 3 |
| 新观测 | 实际 6 帧；选入侧面/背面 5 张。新对象 studio:run-af28a49013c7:t0005 经外层明确确认为同一主体，稳定目标保留首次 ID |
| 第二轮结构 | 使用新观察及旧失败摘要，仍输出两个部件、1 个 revolute 候选；独立审核通过；尺度、rest、行程与内部接口仍未确定 |
| 再次构建检查 | 明确阻塞，未补造导出参数；该视频没有开合过程或已知尺度参照，停止并保留未知项 |

两轮感知阶段耗时分别约 77.1 秒与 53.9 秒（各子阶段耗时求和，不含结构推理）。
这些数字不是新旧 Harness 性能对照或通用吞吐基准。
两轮累计选入 11 个不同原始视频帧；重推断尚未将两轮全部图像自动融合为一个证据包。
输入和生成记录占用约 286 MiB，写入系统分区 /var/tmp，没有增加 /home 中的重资产副本。

最终 phase=stopped、revision=7、physics_status=not_run。
共有 11 个完成阶段收据、9 次调用快照；最后一次 resume 仅重新生成快照，没有新模型调用。

真实记录：

- 最终摘要：/var/tmp/envcreator-workflow-laptop-fPnkRz/final-result.json
- 最终调用快照：/var/tmp/envcreator-workflow-laptop-fPnkRz/calls/d37379530658/workflow.json
- 完整状态与历史：/var/tmp/envcreator-workflow-laptop-fPnkRz/workflow/workflow.json
- 首轮蓝图：workflow/stages/blueprint-f56b89d85e6b/blueprint/blueprint.json
- 回访蓝图：workflow/stages/blueprint-dbd2e0d8c01f/blueprint/blueprint.json
- 首轮原图：calls/d5a0e1aef46c/source-video.jpg
- 最终侧后参考：calls/d37379530658/object-evidence.jpg

![原视频时间预览](/var/tmp/envcreator-workflow-laptop-fPnkRz/calls/d5a0e1aef46c/source-video.jpg)

![本轮侧面和背面真实观察](/var/tmp/envcreator-workflow-laptop-fPnkRz/calls/d37379530658/object-evidence.jpg)

以上都是原始视频画面和对象上下文，不是新 Mesh 渲染或物理实验。
本轮没有新建笔记本 Mesh/USD，没有运行 COLMAP、3DGS 或 Isaac，没有声称机械功能通过。
结构自由文本仍可能把“边正交”写成含糊的面/边关系；本轮并未消除这类算法和描述问题。

## 自动检查

实际执行：

~~~bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q -rs   tests/test_fixed_workflow.py tests/test_assembly_blueprint.py   tests/test_construction.py tests/test_components.py tests/test_harness_geometry.py   tests/test_harness_run.py tests/test_assembly_build_gate.py tests/test_artisan_backend.py   tests/test_asset_agents.py tests/test_harness_physics.py tests/test_harness_assembly.py   tests/test_geometric_verifier.py tests/test_standard_export.py   tests/test_clawspine_integration.py tests/test_studio.py   tests/test_reference_workflow.py tests/test_dsh_model.py
~~~

结果：234 passed，8 skipped，13.28 秒。
6 项跳过需要真实独立 OpenUSD worker，2 项需要显式设置原生 ClawSpine 路径。
没有把跳过算作通过，没有运行全仓库测试。

新增验证包括：几何错误局部修复后重检、中断构建恢复、旧决策拒绝、传递依赖变化拒绝、
结构/外观失败回访、自检查失败报告、重试预算、物理失败状态隔离、
配置变动拒绝、采样参数边界、没有新真实帧时禁止重新建模、历史快照不被推进操作修改。

原生 API 集成测试使用真实 worker 子进程和 PyAV 小视频预览，
验证调用幂等、任务隔离、发布快照、停止、拒绝旧 revision；不调用真实模型。
新增装配测试使用真实确定性几何审核，验证失败报告能返回 Workflow 且仍不能进入构建。

另执行：
node --test tests/test_studio_native.mjs tests/test_envcreator_dsh_plugin.mjs tests/test_clawspine_smoke.mjs
结果 6 passed。包含新默认工具集合、图片附件和取消传播；这是协议测试，不是实际 DSH 模型看图证明。

Studio 执行 node scripts/build.mjs 成功；相关 Python 文件 Ruff 通过；git diff --check 通过。
前端没有本轮浏览器现场验收；已有 Studio 服务未重启，新工具需下次正常启动加载。

## 仍未完成与下一步

- 暂不清理旧阶段；没有依赖安全回收器、磁盘配额或资源自适应并行调度。
- 新控制器先支持单个目标；不会自动合并多对象或跨轮图像证据。
- 只在明确的几何配方错误上自动局部修复；未实现通用 CAD 后端或物理参数修复。
- 没有把新工作流结果接回旧 bundle 导出和最终任务覆盖审核，completed 不是通用交付认证。
- 下次优先解决“保持未知的结构描述如何进入有明确假设的候选几何”，并融合原始证据；
  区分可执行的面/边绑定、相对尺度和真实尺寸，不通过默认参数绕过阻塞。

接口与使用方法见 docs/FIXED_RECONSTRUCTION_WORKFLOW.md。

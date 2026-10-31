# Harness 运行记录（2026-10-27）

本文保留当时运行、失败和修订记录，不代表新的运行验证。当前用法见 [Harness](../AGENTIC_HARNESS.md)。

## 已运行的完整视频回放链路

`outputs/harness-replay-e2e-20261027/` 使用仓库的合成桌面视频和固定 replay 响应，
从感知、证据、设计运行到真实 Blender 网格、Artisan 导出、场景组装及交付，
耗时约 25.5 秒。`asset-0001` 被路由为背景；两个杯子 `asset-0002`、`asset-0003`
各生成 USD，并记录 `visual_review_passed`；`scene/scene.usda` 已组装。
本次没有配置 Isaac，`physics.status=not_run`，最终 `delivery.status=unverified`。
交付报告带有 `replayed_perception` 和 `replayed_design` 标记。
该目录生成于碰撞警告交付门禁加入之前，因此旧 `delivery.json` 中没有
`collision_geometry_review`；新运行会按下述规则记录。

回放中的 `PASS` 是固定夹具给出的响应，`review.observations` 也明确写着
“synthetic replay accepts a generated candidate; not a live visual judgement”。
它证明阶段调用、文件契约与 Blender/Artisan 运行路径可以贯通，不证明真实 VLM 的识别、
视觉审查、几何质量或物理可用性。

## 已运行的固定比传动贯通检查

这次检查使用一个三部件、两旋转轴的合成蓝图，验证代码从 Blender 建模、Artisan 编译
到场景 USD 组装的路径；它不是从真实视频识别传动结构的证据。

| 运行目录 | 实际结果 |
| --- | --- |
| `outputs/harness-coupling-smoke-20261027/` | 首轮 Artisan `check_model_valid` 拒绝模型：比例 2.5、驱动轴 ±1000 rad 会让从动轴达到 ±2500 rad，超出其 ±1000 rad 限位。`compile-report.json` 保留原始阻断信号。 |
| `outputs/harness-coupling-smoke-20261027-v2/` | `geometry.py` 按比例反推连续轴的有限仿真限位：驱动轴 ±400 rad、从动轴 ±1000 rad；真实 Blender 生成部件网格和预览，Artisan 编译通过并导出 `object.usd`、URDF、MJCF。URDF 保存 `spin_wheel` 对 `spin_crank` 的 2.5 mimic。 |
| `outputs/harness-coupling-smoke-20261027-v3/` | 编译器发现底座与旋转件真实重叠，阻断导出；未绕过 `QC_REAL_OVERLAP`。 |
| `outputs/harness-coupling-smoke-20261027-v4/`、`-v5/` | 调整位置后编译器仍发现运动姿态下浮空断联，分别保留间隙和姿态证据；未将关节声明等同于几何连接。 |
| `outputs/harness-coupling-smoke-20261027-v6/` | 修正装配与封闭碰撞网格后，编译结果为 failures=0、warnings=0、notes=0；导出 `physics_notes=[]`，记录 5 个 closed meshes。 |
| `outputs/harness-coupling-scene-smoke-20261027/` | `scene.py` 将通过编译的 USD 引入 `scene.usda`，输出支撑碰撞体和 `layout.json`。PXR 解析结果包含 native mimic `gearing=-2.5`；从动关节在 USD 中的限位为约 ±57295.78°，对应 ±1000 rad。 |

对早期组装产物，使用 Artisan 环境的 `pxr.Usd.Stage.Open` 打开 `scene.usda`，读取
`/World/Assets/Asset_asset_0002/body/wheel/joint_spin_wheel` 的属性得到
`physxMimicJoint:rotZ:gearing=-2.5`、`physics:lowerLimit=-57295.78125`、
`physics:upperLimit=57295.78125`；驱动关节的限位为 ±22918.3125°。
这组早期结果只检查 USD 组合与属性。第二轮曾出现 6 条碰撞警告：开放网格修复及
CoACD `PermissionError` 后的凸包回退；这些历史产物不能因后续修复而追认为物理通过。
v3–v5 的重叠与浮空失败、v6 的干净编译分别保存在各轮 `compile-report.json`。

随后已在宿主真实 NVIDIA GeForce RTX 4070 Ti SUPER、驱动 580.178.04 上运行 Isaac。
早期“机器没有可用 NVIDIA 驱动”的归因不成立；此前受限执行环境中的探测结果不能
代表宿主 GPU 状态。最终测试使用 v6 场景，产物为
`outputs/harness-coupling-physics-61-v6b-20261027/run/validation.json` 和 `run-audit.json`。

| 实测项 | 结果 |
| --- | --- |
| 验证检查 | 8 项全部 true：导出 USD 重载、有限状态、基线稳定、驱动响应、源文件不变、比例一致、断联对照、断联驱动仍响应 |
| 基线角变化 | 2.86193 × 10⁻⁷ rad |
| 外力矩驱动 / 从动响应 | 0.180819 / 0.452048 rad |
| 目标比例 / 比例残差 | 2.5 / 1.49012 × 10⁻⁸ rad |
| 移除 mimic 后从动响应 | 1.38844 × 10⁻⁷ rad；对应控制驱动响应 4.04748 rad |
| 报告 SHA-256 | `321e894a3a0855b951008da402f4d3ad3dd63b022938246e38c145c6129bcef4` |

测试采用 0.02 N·m 外部力矩、临时固定基座和断联副本，测量期未写入关节状态，
读取的是仿真状态。场景及依赖源文件哈希保持不变。worker 写出有效报告后，父进程在
有界等待后结束退出阶段，`shutdown_status=terminated_after_report`；报告检查通过
与退出清理状态分别保存，没有把超时或空报告视为通过。
该结果验证的是合成资产在测试夹具下的理想固定比响应，不包含真实链条、齿轮接触、
载荷标定、自行车骑行、制动或机器人操作，也不是整条真实图像构建链路的完成证明。

## 已运行的刚体与铰链 Isaac 回归

以下使用分别构造的测试资产、真实 Blender/Artisan 导出场景与 Isaac 动力学，验证
协议及重载路径；不等同于真实图像中的瓶子或盒子已经通过。

| 协议与产物 | 实际输入与结果 |
| --- | --- |
| 刚体：`outputs/harness-rigid-physics-61-v2-20261027/` | 沿 +X 施加 3 N，持续 0.15 s，水平响应 0.113634 m；8 项检查全部通过，包括基线垂直位移、垂直速度与姿态稳定。 |
| 铰链：`outputs/harness-hinge-physics-61-v3-20261027/` | 施加 0.005 N·m 外力矩，持续 0.25 s，响应 1.228327 rad；5 项检查全部通过，基线变化 5.89873 × 10⁻¹² rad。 |

刚体基线水平偏移为 2.31461 × 10⁻⁸ m，Z 跨度 5.21541 × 10⁻⁸ m，最大绝对垂直
速度 0.000517346 m/s，姿态变化 3.47552 × 10⁻⁷ rad。支撑稳定性依据完整零输入
根节点轨迹检查，`support_evidence=root_trajectory_proxy`，没有测量接触力；因此不能
据此声称摩擦或恢复系数已标定。两次报告均记录导出 USD 重载、有限状态、源文件不变，
测量期未写入状态，退出清理为 `terminated_after_report`。
详细证据分别位于各目录的 `audit.json` 和 `physics/validation.json`。

## 真实图片端到端：瓶子与背景笔记本

真实输入使用 `qwen3.7-plus`，检测分割使用 Grounding DINO + SAM2.1。v8 已完成
live VLM、Blender、Artisan、Isaac 与任务覆盖链路；以下保留各轮失败及验收边界：

| 运行目录 | 结果与暴露的问题 |
| --- | --- |
| `outputs/harness-live-bottle-20261027/` | `unverified`：设计审查对静态外形任务提出不适当的瓶盖拆分、纹理及液体/物性标定要求，瓶子设计耗尽修订；同时出现背景对象与目标身份的审查混淆。 |
| `outputs/harness-live-bottle-20261027-v2/` | `unverified`：几何 recipe 的 `hollow_prism` 与实际受信任建模接口不一致，构建失败；原始 recipe 与错误日志保留。 |
| `outputs/harness-live-bottle-20261027-v3/` | `unverified`：审查哈希元数据绑定和任务范围判断仍有混乱；外观重建及背景路由不能只按物理 family 判定，失败产物保留。 |
| `outputs/harness-live-bottle-20261027-v4/` | 现存交付报告记录 `status=verified`：瓶子设计及视觉审查通过，笔记本 routed 为 background。但复查完整轨迹发现 3 N、0.15 s 驱动后的释放段出桌，旧物理规则漏检；该轮保留为历史门禁缺陷证据，不作为最终通过依据。 |
| `outputs/harness-live-bottle-20261027-v5/` | `unverified`：物理响应不足后进入自动 repair；修复候选的几何断连及视觉 gaps 阻断接受，失败及修复记录均保留。 |
| `outputs/harness-live-bottle-20261027-v6/` | 倾角 0.636 rad、Z 下降 0 m，却被当时默认的 0.5 rad 直立约束判失败；对于未要求保持直立的推碰任务，该额外条件不合理，已修正协议默认值。该旧运行不追认为通过。 |
| `outputs/harness-live-bottle-20261027-v7/` | 最终 `unverified`：三轮独立视觉审查均为 `REDO`，指出肩部缺失或接缝、错误开放底部等问题；笔记本背景路由已保存。未通过放宽缺口审查接受资产。 |
| `outputs/harness-live-bottle-20261027-v8/` | `delivery.status=verified`，首次几何候选即视觉 `PASS`，刚体 Isaac 九项检查全部通过，任务范围审查通过，笔记本 routed 为 background。此处 verified 是本轮近似外形与既定水平推碰协议通过，不表示真实尺寸、材料或瓶内结构已恢复。 |
| `outputs/harness-live-video-20261027-v2/` | 1.5 秒、3 帧真实视频的视觉与物理检查通过，最终 `unverified`：任务审查将多个 background-route 合并成 `asset_id=null` 的单项，触发 same-asset/family 证据校验；未把已有视觉/物理通过代替最终范围通过。 |
| `outputs/harness-live-bottle-20261027-v9/` | `verified`，首个几何候选视觉 PASS；固定推力 5.4936 N、0.05 s，驱动响应 0.014421880 m，最大下降 0 m、最大倾角 0.4049669 rad；同配置恢复约 0.282 s。该任务不要求保持直立，倾角如实记录。 |
| `outputs/harness-live-video-20261027-v3/` | 三轮视觉 `REDO`，最终 `unverified`，没有组装 scene。第三轮实际预览可见斜肩，审查仍声称肩部缺失，暴露复审输入歧义和模型判断局限；没有绕过拒绝结果。 |
| `outputs/harness-live-bottle-20261027-v11/` | 最新单图最终 `unverified`；三个候选均因瓶盖几何断连被 Artisan 阻断，无 scene，physics=blocked。保留失败，不以反复重跑挑选成功替代本轮结果。 |
| `outputs/harness-live-video-20261027-v5/` | 1.5 秒、3 帧视频自动 `delivery=verified`、physics/task_scope 通过、review_flags 为空；但交付前主 agent 独立看图抽查发现肩部明显台阶，与原图斜肩不符，外形抽查为 `unverified`。自动 verified 不作为外形已验收。 |

v4 暴露了“基线稳定且产生水平响应”不足以保证驱动后仍在支撑面上的问题。
当前对驱动和释放两段完整轨迹检查最大 Z 下降，默认上限为 0.02 m；worker 记录测量，
父进程再次按冻结协议核验。倾角仍记录，但 `max_driven_tilt_rad` 默认 `None`，只有
显式要求保持直立时才增加倾角门禁，不能把允许倾倒的刚体推碰自动变成直立任务。
因此保留出桌检测，同时修正 v6 暴露的默认条件过严。现存 v4 报告及其 `verified`
字段未被改写；新规则不能追认旧运行通过。v7 的三轮视觉失败已成为保留的反例，
不因新增几何类型而改写；v8 已完成独立结果核对，视频 v2 的任务范围失败继续保留。

`outputs/harness-lathe-smoke-20261027/` 已通过真实 Blender 与 Artisan：一个连续八边
剖面连接 body/shoulder/neck，另加白色 cap，均属于同一刚体。`candidate.json` 的
`physics_notes` 仅记录 `already_watertight`（open_edges=0→0）和 CoACD 分解为两个
凸块，无碰撞回退。这验证连续剖面的网格及导出路径，不是输入图片视觉通过或 Isaac
推碰通过；后两者仍由各自独立检查决定。

v8 的 `recipe.json` 实际使用八边 lathe 生成收腰瓶身，瓶颈与白色瓶盖分别为圆柱，
同属一个质量假设为 0.15 kg 的刚体。该 lathe 不同于上述 smoke 的连续肩颈剖面：
渲染仍有宽水平肩台，不能据模型 `PASS` 宣称肩部细节或真实轮廓已精确恢复。
标签、透明塑料、液体和内腔均未重建。`candidate-000/review/review.json` 的 PASS
支持本轮主要可见外形近似；产物渲染是模型展示图，不是物理实验截图。

实际物理证据为 `physics/asset-0001/rigid/validation.json`：沿 +X 施加 2.943 N，
持续 0.05 s，释放 0.1 s，驱动响应 0.0184873 m，完整驱动/释放轨迹最大 Z 下降
约 5.96 × 10⁻⁸ m；九项检查均 true。质量和推力来自假设及固定协议，不是现实瓶子
标定；根轨迹稳定性也不代表接触力测量。网页研究没有取得可用候选事实，背景重建
为 not_run，不能将本轮成功写成外部型号验证或 3DGS 背景完成。

同配置恢复审计 `outputs/harness-acceptance-20261027/single-image-resume-audit.json`
记录 exit_code=0、耗时 0.289976 s、unchanged_stages=true、delivery_status=verified。
这验证已有哈希绑定阶段的恢复复用，不是第二次重跑模型与物理的独立实验。
视频 v2 的多个背景资产各有路由引用，但旧范围审查将它们合并到无 asset_id 的要求，
确定性校验因此拒绝覆盖；修订须保留同资产/同 family 证据约束，并在新目录实测。
随后 v9 保留为真实历史近似轮廓与物理通过，视频 v3 暴露视觉复核误判。
最新冻结源码下单图 v11 三次 Artisan 瓶盖断连，最终无 scene、物理阻断；视频 v5
自动通过程序门禁，但交付前独立看图抽查发现明显肩部差异，不计为稳定重建成功。

视频 v5 的 recipe 只有六边 prism 直筒、圆柱直颈和圆柱盖，没有肩部渐缩实体；
`front.png` 显示宽水平肩台与细长直颈，原始 context 则可见向颈部收缩的斜肩。
自动复核声称肩部连续，与这些可见证据不符。这是 Codex 主 agent 及文档复核 agent
的看图抽查，不是人类或用户验收；原始 delivery 与模型状态均未修改。
独立记录位于 `outputs/harness-acceptance-20261027/video-v5-independent-visual-check.json`，
对照为 `video-v5-comparison.png`：左侧是真实视频对象裁剪，右侧为 Blender 展示图，
不是 Isaac 实验截图。视觉复核仍可能把存在的肩部误判缺失（视频 v3），也可能把
缺失的斜肩误判为存在（视频 v5），当前修复/复核不能保证外形可靠。

视频 v5 的生成资产在 Isaac 中九项检查均 true：假设质量 0.18 kg，水平推力
3.5316 N、驱动 0.05 s、释放 0.1 s，响应 0.0176355839 m，最大下降 0 m，最大
倾角 0.0123649101 rad。该物理结果对实际生成的资产及既定协议有效，不能证明
该资产与原图形状一致，更不表示现实参数标定或机器人操作完成。

同配置恢复审计记录：单图 v11 为 0.280835 s、exit_code=2、仍 unverified；视频
v5 为 0.337708 s、exit_code=0、仍自动 verified。两者 stages 均完全不变，没有重新
执行模型或仿真。首版验收汇总及证据索引为 `outputs/harness-acceptance-20261027/report.md`
和 `acceptance-summary.json`；没有遗留运行进程。


当前完整测试记录为 209 passed、1 skipped，ruff 通过。设置 `BLENDER_BIN` 启用了
三项真实 Blender 测试；唯一跳过项因未安装 torch，跳过项不作已验证能力。真实图像构建、合成协议回归和 replay 链路分别保留自己的证据范围。

## 已验证的联网路径

`outputs/harness_web_public_probe_20261027/` 已通过 Google DoH 和 Wikipedia API
真实检索并抓取 Shimano Deore XT 页面，保留 7297 字正文及哈希。系统 DNS 返回
非公网地址时默认拒绝连接；DuckDuckGo HTTP 202 仍记录为 error。详情和复现配置见
[联网证据](../HARNESS_WEB.md)。这只证明检索与正文取证路径可运行，尚不能证明网页
型号适用于某张输入图片；后续仍需原图与来源正文的独立适用性核对。

## 目前需要人复核的内容

程序自动检查输入引用、哈希、蓝图结构、Artisan 编译、场景引用与已运行的动力学协议。
多图片身份与视角关系不明、网页资料冲突、不可见关键结构、无法标定的绝对尺度、
外观审查持续失败、Isaac 不支持的交互或动力学失败，会在产物中留下具体问题。
身份/来源冲突、视觉和物理失败会阻断 `verified`。Artisan 碰撞回退目前记录在
`build.json` 的 `physics_notes`；`geometry.build_recipe` 把它带入候选资产，
`run.py:_package` 遇到 `convex_hull_fallback` 或 `open mesh collider` 会生成
`collision_geometry_review` 并阻断 `verified`，等待人工检查实际碰撞代理。
当前已实测合成资产的刚体、铰链及固定比 Isaac 协议；真实图片 `live-bottle` 前三轮
仍为 unverified，v4 原报告 verified 存在已确认的轨迹漏检，v5 修复未通过，v6 暴露的
默认直立条件过严已修正；v7 因三轮几何视觉审查失败最终 unverified。v8 在既定
近似外形与刚体协议范围内 verified，视频 v2 因任务覆盖证据关联失败 unverified；
v9 保留为同类协议的历史通过，视频 v3 因视觉复核拒绝保持 unverified。最新单图
v11 因瓶盖断连失败；视频 v5 虽自动 verified，但主 agent 外形抽查 unverified。
现有首版已贯通图像/视频、建模、导出和有限物理协议，VLM 修复与复核仍不可靠，
不能宣称稳定自动重建成功。多图片、绝对尺度、真实机构参数
及机器人操作仍需各自的输入证据与实验；现有协议不覆盖的原始任务要求继续保留为 unsupported。

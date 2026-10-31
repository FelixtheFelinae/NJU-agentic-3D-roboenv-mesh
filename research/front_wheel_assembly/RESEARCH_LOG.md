# 调研记录

日期：2026-10-27。范围是自行车前轮外部紧固连接；本轮只新增本目录文档。没有修改现有源码、配置、模型或运行报告，没有启动生成、仿真或策略训练。

## 仓库核对

- 核对五刚体自行车的 Blender → Artisan → Isaac 入口，确认 `front_spin` 只表示轮组旋转。
- 核对独立爆炸图的 `FW03–FW08`、前叉、挡泥板支撑、E02/E06 原始观察和已有未确定项。
- 实际查看 E02/E06；未据小尺寸图像补认不可见螺纹、叉端开口或制动内部结构。
- 只读查看 Artisan 的 `couple`、`turns_to_multiplier` 和 USD mimic 导出。SDK 源目录不是 Git checkout，因此保存关键文件哈希，不填写未知版本。
- 本仓库基线提交及输入/源码指纹记入 [sources.json](sources.json)。本轮没有改写这两套资产，旧实验仍保留各自结论。

## 检索路线与原始资料

搜索围绕以下问题展开。表中按主题整理关键词和查询示例，供重查使用，不是逐条查询日志：

| 问题 | 查询示例 | 最终采用的资料 |
| --- | --- | --- |
| 配合与拆卸路径 | `JoinABLe CAD assembly`、`Assemble Them All`、`ASAP physical feasibility` | L01–L03；核对输入几何、路径、重力和操作约束的区别 |
| 旋松与仿真 | `robotic screw unscrewing simulation simplified thread torque axial coupling nut bolt benchmark` | L04、L06；区分详细螺纹接触、实物松卸和拟建等效模型 |
| 状态和关系 | `screw disassembly simulation constraint`、`unscrewing virtual model torque disassembly simulation` | L07、L09；区分连接、接触、阻挡和产品状态 |
| 近年直接工作 | mesh/assembly agent/tool planning、fastener disassembly | L08 为直接相关预印本；L10 为摘要级规划补充 |
| 前轮结构 | `site:si.shimano.com front wheel axle nut removal dealer manual`、`front wheel axle nuts retaining` | T01，仅作型号未匹配的结构参考 |
| 后端表示 | `screw joint pitch force`、`PhysX rack pinion joint revolute prismatic`、`UsdPhysics jointEnabled BreakTorque` | T02–T04，未做运行可用性测试 |

引用以论文原文、正式会议页、作者项目、大学论文存档及厂商/SDK 文档为准。没有引用未经本轮核验的性能数字，也没有把最新预印本写成已发表会议论文。

## 影响方案的结论

| 依据 | 方案中的处理 |
| --- | --- |
| E02/E06 与模型清单不能确定紧固制式或叉端槽口 | 普通螺母仅为实验候选；保留备选及人工覆盖 |
| 单侧螺母取出不代表所有轮组保持关系解除 | 一侧旋松/取出与整轮退出分成两步 |
| 有紧固图分析和一般拆卸路径，不代表螺纹动力学 | 分别保存连接假设、运行规则及独立运动证据 |
| L08 已将 mesh、VLM、工具和仿真反馈组合 | 研究重点放到可执行紧固连接及实际修复，不笼统声称总体流程新颖 |
| L06 在重复旋松后做轴向探测 | 设计脱扣前后拉离对照，不只读状态标签 |
| 螺旋关系可以被轴向拉力回驱 | 冻结转动保持/抗回驱条件，不能无条件要求拉力下绝不旋出 |
| 第一阶段夹具可能阻挡第二阶段退出 | 整轮实验显式切换抗转装置和承托方式 |
| 人工无限 guide 与真实光轴接触不同 | 释放模拟螺纹的人工约束，同时保留仍存在的有限几何接触 |
| Artisan 有跨域表示入口，但当前包装与导出未验证 | 实现前先做最小后端试验；不从属性存在推断功能通过 |

本表是决策及证据摘要，不是声称以上拟议实验已经完成。

## 读取限制

- MDPI 的 L06 页面返回 429，改读 Birmingham 存档的出版版本 PDF。
- Shimano revision 28 PDF 超过网页读取大小限制，成功打开 revision 26。只引用已读取版本，不宣称它是最新或匹配本车。
- JoinABLe 的 CVPR 页面有 403，方法细节依据可读 arXiv 原文。
- SBDP 只读官方摘要，未用它支持细节结论。
- *Digital Twin with Model Predictive Control for Screw Unfastening by Robots* 的页面受限；本轮仅留搜索线索，没有使用其结果、成功率或模型方程。
- NIST 小零件装配基准的 PMC 页面触发访问验证，未作为已读全文证据。

本轮未下载/安装外部研究项目、机器人控制器或模型权重，未复制论文全文；本地图片仍在 `outputs/`。仅检查研究文档链接、来源清单及修改范围，不重跑实现测试。

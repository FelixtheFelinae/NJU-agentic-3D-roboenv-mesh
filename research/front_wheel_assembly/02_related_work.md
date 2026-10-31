# 文献与技术资料：连接推断、旋松和拆卸验证

检索日期：2026-10-27。围绕“给定前轮几何和观察，构造可执行的外部紧固连接”重新检索原始资料；没有复现论文实验。以下结论区分作者已展示的方法、本项目拟借用的概念及尚未验证的适用性。旧仓库的综述不是本轮文献事实的唯一来源。

## 1. 主要论文

### L01 — JoinABLe: Learning Bottom-up Assembly of Parametric CAD Joints

Karl D. D. Willis 等，**CVPR 2022**。[原文](https://arxiv.org/html/2111.12772v2)；[会议记录](https://openaccess.thecvf.com/content/CVPR2022/html/Willis_JoinABLe_Learning_Bottom-Up_Assembly_of_Parametric_CAD_Joints_CVPR_2022_paper.html)。会议页本轮读取被拒，方法依据可读原文核对。

- **方法与输入输出**：两个 B-Rep 零件，预测局部连接轴，再搜索相对放置；参考 §3.2、§3.3、§3.5。
- **可借用**：轮轴、螺母和叉端各自保存局部接口坐标，几何候选包含轴线、偏移和翻转。
- **限制**：原问题限定刚性配合，不是螺纹运动或释放；重建 mesh 也不具备其 B-Rep 输入。

本项目先借接口表示和候选搜索思路，不以调用该模型作为首例前提。圆柱同轴只能产生连接候选，不能确定预紧力和紧固制式。

### L02 — Assemble Them All: Physics-Based Planning for Generalizable Assembly by Disassembly

Yunsheng Tian 等，**ACM TOG / SIGGRAPH Asia 2022**。[项目](https://assembly.csail.mit.edu/)；[论文 PDF](https://people.csail.mit.edu/yunsheng/Assemble-Them-All/paper.pdf)。

- **方法与输入输出**：给定装配态零件，借物理反馈搜索拆卸路径及顺序，再反向得到装配方案；参考 §3、§4。
- **可借用**：解除连接后另检退出路径与完全分离，不能拿爆炸位移代替求解。
- **限制**：基本设定忽略重力、摩擦和操作约束；旋转/螺钉案例不证明松脱扭矩或预紧正确。

前轮首例的退出路径很短，可先验证给定路径和碰撞；复杂搜索不必在第一步引入。轮胎通过前叉、挡泥板和制动结构的通道，应保留为整轮扩展问题。

### L03 — ASAP: Automated Sequence Planning for Complex Robotic Assembly with Physical Feasibility

Yunsheng Tian 等，**ICRA 2024**；2023 年首次预印本。[项目](https://asap.csail.mit.edu/)；[预印本](https://arxiv.org/abs/2309.16909)。本轮重点核对作者项目的路径、稳定和执行约束描述。

- **方法与输入输出**：给定分件装配，通过拆卸树搜索安排序列，并考虑重力和有限支撑。
- **可借用**：路径可行、剩余结构稳定、工具可操作分别验证。
- **限制**：不能把该规划框架视为已恢复前轮紧固语义，或已验证扳手旋松。

本项目借其支撑条件的区分：前叉固定、轮组承托、轮轴抗转各是独立夹具条件。夹具存在与否应进入实验报告，不能隐藏在“拆卸成功”中。

作者项目页同时说明其定量规划比较未施加机器人操作约束，不能把框架列出的执行条件理解为所有比较都已经验证机器人可执行。

### L04 — Factory: Fast Contact for Robotic Assembly

Yashraj Narang、Kier Storey 等，**RSS 2022**。[会议论文](https://www.roboticsproceedings.org/rss18/p035.pdf)；[作者入口](https://research.nvidia.com/publication/2022-05_factory-fast-contact-robotic-assembly)。

- **方法与输入输出**：SDF 接触、接触缩减及求解器支持详细零件接触；明确包含 Pick、Place、Screw 阶段。参考 §I、§III–V。
- **可借用**：区分对轴、啮合和旋拧，记录轴向进给。
- **限制**：其螺纹接触路线超出本轮；现成资产和接触算法不解决从视频生成连接语义。

它证明旋拧可以进入接触仿真研究，但本项目的研究变量应先放在连接推断及简化交互编译。暂不移植其完整接触或机器人学习系统。

可读 [HTML 原文](https://arxiv.org/html/2205.03532) 的 §V-F 以人类操作力分布作对照，不等于真实机器人旋拧迁移；论文当时在 §VII-A 将真实迁移列为后续工作。

### L05 — AutoMate: Specialist and Generalist Assembly Policies over Diverse Geometries

Bingjie Tang 等，**RSS 2024**。[会议论文](https://www.roboticsproceedings.org/rss20/p064.pdf)；[项目](https://bingjietang718.github.io/automate/)。

- **方法与输入输出**：两件式 plug/socket 资产及拆卸轨迹，用于学习装配策略；参考 §IV–V、§VIII。
- **可借用**：资产需有合理间隙和无初始穿透，几何与策略验证应分开。
- **限制**：论文的两件插装和轨迹匹配不能直接作为已支持螺母旋拧的证据。

首轮不训练策略，只借其对“可仿真的装配几何”的要求。工具对齐、旋松和连接解除仍由本项目另建协议。

### L06 — Nut Unfastening by Robotic Surface Exploration

Alireza Rastegarpanah、Rohit Ner、Rustam Stolkin、Naresh Marturi，**Robotics 2021, 10(3), 107**。DOI：[10.3390/robotics10030107](https://doi.org/10.3390/robotics10030107)；[大学存档全文](https://pure-oai.bham.ac.uk/ws/files/159052372/robotics_10_00107.pdf)。出版商页面限流，全文以大学存档核对。

- **方法与输入输出**：真实机器人用接触探索定位六角螺母，再重复转动并轴向探测脱扣；见 §4.3、Figure 9。
- **可借用**：旋转后还需探测能否取出，不能只数圈。
- **限制**：它是实物松卸研究，不是生成资产或本项目等效阻力模型的参数来源。

本项目可采用解除前后的同轴拉离对照。该实验只验证假设模型中的保持与释放，不继承实物实验的成功率或力矩值。

### L07 — Automated disassembly of e-waste—requirements on modeling of processes and product states

José Saenz、Torsten Felsch 等，**Frontiers in Robotics and AI 2024, 11:1303279**。[全文与 DOI](https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2024.1303279/full)。

- **方法与输入输出**：建立连接、产品状态、拆卸过程和工具的数据表示；见 §2.4.1、Table 1。
- **可借用**：连接应包含位置、可接近性、工具和状态，而非只有零件名称。
- **限制**：这是拆卸信息建模研究，不提供照片到可执行资产的完整生成器。

前轮的紧固边、轴端工具接口、制动连接、支撑和退出路径应分别表达。本文引用标准作为分类背景，本项目不因此承担制造标准符合性结论。

### L08 — End-to-End Autonomous Generation of Human Assembly Plans

Faustin Arion von Arx、Millicent Schlafly、Mark D. Fuge，**2026-10-07 预印本**。[摘要](https://arxiv.org/abs/2610.09781)；[22 页原文](https://arxiv.org/pdf/2610.09781)。这是本轮补查中与 Agent 结构推断和工具规划最接近的工作。

- **方法与输入输出**：分件装配 mesh 和可用工具 mesh，无需 joint/fastener 标注，输出工具、序列、手册或失败报告；见 Figure 1、§3。
- **可借用**：几何轴候选、VLM 工具选择、仿真可行性反馈。
- **限制**：§5 的工具接近只检直线通道，刚体模型排除依赖摩擦/柔顺性保持的结构；没有建立本项目要求的松脱阻力与脱扣验证。

该工作说明“mesh → 工具推断 → 仿真检查 → 反馈”已有直接研究，不能笼统宣称为空白。原文把下游 Agent 实际采用反馈完成设计修订留待后续；本项目应进一步检验执行反馈是否真的修好了连接，而不只生成建议。

### L09 — Component Influence-Driven Fastener Reduction for Robotic Disassemblability-Aware Design Simplification

Takuya Kiyokawa 等，**2026-05-20 预印本**。[原文](https://arxiv.org/html/2605.21026v1)。

- **方法与输入输出**：已有 CAD、CCC 图、矩阵和拆卸序列，产生紧固件影响与减少建议；见 §III-A、Algorithm 1。
- **可借用**：Figure 1 区分 contact、connection、constraint 三种边。
- **限制**：使用已有 fastener labels；§V-C 明确未做动态机器人仿真。图上移除紧固件不等于已经旋松取出。

本项目应借其关系区分，同时让可执行的旋拧与解除验证补上图分析没有覆盖的过程。

### L10 — State-Based Disassembly Planning

Chao Lei、Nir Lipovetzky、Krista A. Ehinger，**AAAI 2025, 39(14):14602–14609**。[正式摘要与 DOI](https://ojs.aaai.org/index.php/AAAI/article/view/33600)。本轮仅核对官方摘要，未读全文。

该工作保存中间运动状态，并用带状态的 Directional Blocking Graphs 引导物理拆卸搜索，优先平移后考虑旋转。可作为后续序列搜索线索；本轮证据不支持其具有螺纹力矩、连接状态机或 Agent 生成能力的判断。

## 2. 与前轮和简化约束直接相关的技术资料

### T01 — Shimano General Operations, DM-GN0001-26

[厂商原始手册](https://si.shimano.com/en/pdfs/dm/GN0001/DM-GN0001-26-ENG.pdf)，印刷页 171–174（PDF 索引页 170–173），HUB DYNAMO / Installation of the front wheel。

手册区分快拆、螺母和 E-THRU，并展示部分型号的垫圈、支架、止退件及连接端子。它支持“不同前轮具有不同拆卸前提”，**不支持认定本车是 Shimano、发电花鼓或该型号**。手册中的 M9 和安装扭矩不移植到本车，也不能当作松脱扭矩。

文档还提示轮轴可能随单侧操作转动。规划因此单独记录轴的抗转条件，但不把任何一种型号的操作顺序照搬到未知实车。

### T02 — COMSOL 6.3, Screw Joint

[官方定义](https://doc.comsol.com/6.3/doc/com.comsol.help.mbd/mbd_ug_multibody.5.13.html)。它将同轴相对转动和平移通过螺距及头数关联，可配置力、力矩和摩擦。这里仅借用螺旋约束概念，不使用 COMSOL 运行实验。

### T03 — PhysX Joints / Rack-and-Pinion

[PhysX 5.4.0 Joints](https://nvidia-omniverse.github.io/PhysX/physx/5.4.0/docs/Joints.html)；[5.5.1 RackAndPinion API](https://nvidia-omniverse.github.io/PhysX/physx/5.5.1/_api_build/classPxRackAndPinionJoint.html)。

SDK 文档描述旋转—直线耦合和可解除约束的相关机制，但不是本机 Isaac/Artisan 组合的可用性证明。API 存在不等于已经通过 USD 暴露；是否适合共轴旋拧仍待最小实验。

### T04 — OpenUSD UsdPhysicsJoint

[官方 API](https://openusd.org/release/api/class_usd_physics_joint.html) 包含关节启用、连接刚体、局部坐标和破断阈值等属性。`breakTorque` 描述受载破断，不能拿来替代“旋松后退出螺纹”。运行时是否可以按计划禁用约束，仍需在本机后端验证。

## 3. 本轮研究的定位

文献中已经分别存在配合推断、拆卸搜索、螺纹仿真和松卸观测，也有 L08 这样结合 VLM、工具和物理规划的直接工作。本研究拟重点检验：**Agent 根据有证据的前轮几何提出带来源的紧固模型，将阻力、啮合和释放规则编译为可执行交互，再由独立检查定位错误并实际修复。**

首例完成只能说明这条链在特定夹具和参数下可行；不能直接称为通用自动机械设计。后续若做论文研究，需要另外检验：未手填连接的输入上能否推断正确、错误候选能否被拒绝、人工覆盖了多少关键字段，以及结果能否迁移到其他连接。这里不先宣称新颖性已经建立。

没有将搜索摘要中出现、但未取得可读全文的最新工作作为关键技术结论。读取状态和遗漏线索保存在 [sources.json](sources.json) 与 [调研记录](RESEARCH_LOG.md)。

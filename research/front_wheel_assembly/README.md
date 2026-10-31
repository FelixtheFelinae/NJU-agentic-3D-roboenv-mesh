# 自行车前轮：面向工具拆装的资产生成研究

2026-10-27。本目录只保存规划、仓库审查和文献调研；没有新增实现代码，没有生成新资产，也没有运行 Artisan 编译或 Isaac 仿真。

研究对象限定为**前轮轮轴与前叉的外部保持连接**。首先研究一个候选轴端螺母的工具旋松与取出，再扩展到两侧保持件解除后的前轮脱离。轴承拆解、悬挂内部、整车机构、真实机器人控制和策略训练不进入首轮。

## 当前代码能提供什么

现有主链是 `Blueprint → 几何 recipe → Blender 网格 → Artisan 编译与导出 → Isaac 加载和受力测试 → 有限修复`。可以复用几何生成、编译检查、USD 依赖处理、施力和轨迹记录、哈希绑定及失败保留机制。

目前前轮动力学资产只有旋转关节；爆炸图资产虽然分出候选轴、垫圈和轴端紧固件，但没有可释放连接语义。**它们都还不是可拆装前轮。** 两套资产的实际内容及证据见 [现有资产审查](01_existing_assets.md)。

## 拟增加的工作流

`前轮局部证据和已生成几何 → Agent 提出连接候选 → 几何检查及假设修正 → Assembly Semantics → Interaction Model → 仿真执行 → 独立拆卸验证 → 定位结构或参数问题`

- **Assembly Semantics**：记录连接哪些零件、紧固件和夹持堆叠、局部轴线、工具接口、依赖、证据与未知项。装配连接图与运动关节树分别保存。
- **Interaction Model**：将已选连接假设编译成工具啮合、阻力、旋转—轴向耦合和约束释放规则。模型输出类型化数据，受信任代码解释执行。
- **Validation**：分别检查工具空间、旋松响应、剩余啮合、约束解除和实际分离；保留低扭矩、工具未啮合、错误旋向及仍有保持件的对照。

首轮使用**带明确假设的普通外六角轴端螺母夹具**。这是假设性的研究实例，不能宣称已识别实车制式。前叉、轮组和轮轴的支撑/抗转夹具必须单独声明；螺母取出不等于整只前轮已经可以卸下。[实验规划](03_experiment_plan.md)给出两步范围和具体实验。

## 调研结论

已有研究分别提供 CAD 配合关系推断、基于物理的拆卸路径搜索、螺纹接触仿真和真实螺母松卸的测量方法。尚未在本轮检索中核验到一个工作，能够直接从这类未拆解的前轮视频自动生成完整连接语义、可信松卸参数和可执行拆装逻辑。

最值得先读的工作是：

1. [End-to-End Autonomous Generation of Human Assembly Plans，2026-10 预印本](https://arxiv.org/abs/2610.09781)：直接从分件 mesh 和工具几何生成工具、序列及反馈，与方向接近；但未覆盖本项目的摩擦保持、松脱阻力和脱扣验证。
2. [JoinABLe，CVPR 2022](https://arxiv.org/html/2111.12772v2)：参考局部连接原点、轴线和相对放置的表示；原方法针对 B-Rep 零件的刚性配合，不能直接当作网格螺纹动力学。
3. [Assemble Them All，SIGGRAPH Asia 2022](https://assembly.csail.mit.edu/)：参考拆卸路径搜索与几何分离检查；不继承其未包含的摩擦、预紧和操作条件。
4. [Factory，RSS 2022](https://www.roboticsproceedings.org/rss18/p035.pdf)：明确区分对轴放置和啮合旋拧；其详细螺纹接触路线超出本轮简化范围。
5. [Nut Unfastening by Robotic Surface Exploration，2021](https://pure-oai.bham.ac.uk/ws/files/159052372/robotics_10_00107.pdf)：参考旋松后的轴向探测，避免仅凭转角或状态标签声称取出。

完整比较、原文定位和技术资料见 [文献调研](02_related_work.md)；Agent 的输出、人工修正字段和现有接口限制见 [语义与接口规划](04_semantics_and_interfaces.md)。

## 文档与来源

| 文件 | 内容 |
| --- | --- |
| [01_existing_assets.md](01_existing_assets.md) | 已有代码调用关系、前轮对象、输入证据和未确定结构 |
| [02_related_work.md](02_related_work.md) | 9 篇原文/项目核验论文、1 篇摘要级补充，以及厂商资料和仿真 API |
| [03_experiment_plan.md](03_experiment_plan.md) | 轴端螺母首例、整轮扩展、简化力学与具体验证实验 |
| [04_semantics_and_interfaces.md](04_semantics_and_interfaces.md) | 拟新增数据语义、Agent 分工、人工修正和模块接入位置 |
| [sources.json](sources.json) | 文献 URL、核验位置、读取范围与适用性说明 |
| [RESEARCH_LOG.md](RESEARCH_LOG.md) | 本轮查询、资料读取限制和决策依据 |

本目录没有下载外部模型、引入新依赖或复制论文全文。引用的原始视频裁剪仍位于本地 `outputs/`，未复制进研究目录。后续实现也应在独立实验中验证，不改写既有自行车物理结论。

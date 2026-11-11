# 本地保留示例与清理记录

2026-11-11 保存核心代码后，清理了约 8.39 GiB 的安装下载包与闲置 Kit 缓存。
未改动模型权重、感知环境、运行中的 Studio 或 RBO 下载数据。
部分旧运行仍被 Studio 历史或资产索引引用，因此保留；没有直接删除这些记录。

## 保留示例

路径相对于仓库；`/var/tmp` 路径为本机临时实验目录，迁移机器或系统清理后不可假设存在。
资产、输入视频和运行记录只保留本地，不随代码上传。

| 示例 | 本地位置 | 用途及限制 |
| --- | --- | --- |
| 笔记本人工监督参考 | `outputs/user-scene-assets-20261023/` | 保留 Blueprint、拟合、历史构建与验证；不是新一轮工作流已成功的证明 |
| 笔记本原始证据和相机 | `outputs/user-video-build-20261022/`、`outputs/user-video-isaac-20261023/`、`outputs/user-video-table-20261023/` | 参考重放所需依赖，完整保留 |
| 笔记本原视频 | `outputs/studio/runs/2fb28537471e4fce/output/perception/inputs/source.mp4` | 原始输入，SHA256 在 `components/reference_workflow.py` 固定 |
| 固定流程 P0 | `/var/tmp/envcreator-workflow-laptop-fPnkRz/` | 决策、恢复和 Critic 的实际记录 |
| 固定流程 P0-2 | `/var/tmp/envcreator-workflow-p02-b5FcQU/` | 跨轮证据和构建假设；几何检查尚未全部通过，不代表生成新资产成功 |
| 自行车输入与重建 | `outputs/bicycle-ground-20261026/` | 保留视频、感知、建模与展示；仅删除独立渲染缓存 |
| 自行车传动验证 | `outputs/bicycle-chain-20261026/delivery-v1/`、`outputs/bicycle-chain-20261026/reload-v3/` | 历史资产及独立重载记录；实验含理想夹具，条件见自行车实验文档 |
| 几何正反夹具 | `tests/fixtures/mechanical_geometry/` | 代码内的小型轴孔夹具及负例测试，不依赖大资产 |
| 电动螺丝刀 | `outputs/electric-screwdriver-20261028-v4/`、`outputs/screwdriver-feasibility-20261028-v4/` | 生成与理想台架功能记录，不能替代机器人真实抓持验证 |
| 已注册装配实例 | `outputs/triangular-box-20261030-v9/`、`outputs/standard-interactions-20261104/` | 标准组件目录引用的资产和验证输入，完整保留 |

## 清理与恢复依据

本机维护目录：`/home/felixz/.local/share/roboenv/maintenance-20261111-Jvk0lo/`。

- `tracked.before.patch`、`untracked-source.tar.gz`：清理前的代码与文档备份。
- `cleanup-manifest.json`：候选目录、大小及阻止删除的依赖。
- `cleanup-result.json`：实际删除清单及空间变化。
- `example-hashes.json`：清理前后核对的 17 个关键文件哈希。
- `legacy-test-sources/`：移出的三个旧推车实验测试文件；对应实验源代码仍保留。

核心工作流、恢复、证据、结构描述、几何、工具接触及 DSH 回归测试保留。
测试通过只说明其覆盖范围；本轮没有重新生成资产或运行 GPU 物理验证。

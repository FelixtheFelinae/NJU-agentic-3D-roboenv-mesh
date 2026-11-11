# 装配数据集下载

## RBO 完整 CSV / 图像版

用户已指定使用系统分区。保存目录：`/var/tmp/envcreator-datasets/rbo`。
该目录在系统分区，不占 `/home` 的剩余约 15 GiB。

官方来源：<https://zenodo.org/records/1036660>。
完整清单为 732 个文件、约 114.06 GiB，包括重复的 ROS bag 表示。
本次选择**完整 CSV/图像表示**：374 文件、66,460,012,851 bytes（约 61.90 GiB），
包含全部 358 个交互序列、物体模型、力传感器模型和交互索引。没有只选少量案例。
另 358 个 ROS bag 是同数据的另一种封装，本轮不重复下载；需要时脚本支持显式选择。

启动时系统分区约 79.97 GiB 可用，足以容纳该压缩表示并保留 12 GiB。
不会整体解压；解压会额外占用大量空间，需要按实验选择或更大存储。
`/var/tmp` 仍可能受系统清理策略影响，长期保管建议迁往专用数据盘。

```bash
.venv/bin/python scripts/download_rbo_dataset.py \
  --destination /var/tmp/envcreator-datasets/rbo --workers 4

# 只读取本地状态，不发起网络请求
.venv/bin/python scripts/download_rbo_dataset.py \
  --destination /var/tmp/envcreator-datasets/rbo --status
```

本轮任务已作为独立后台进程运行。状态和日志在保存目录：

- `official-record.json`：官方原始清单、文件大小和 MD5。
- `download-manifest.json`：本轮完整文件选择。
- `download-status.json`：已校验、失败、完成状态；只有全部校验成功才会写 complete。
- `download.pid`、`download-resume.log`：当前任务 PID 和续传日志。
- `download-status.<timestamp>.json`：重新启动时保存的上轮状态。
- `*.part`：未校验完成的部分数据；不会被改名成正式归档。

重复执行会校验并复用已完成文件、续传部分文件；独占锁防止同一目录多个写入任务。
HTTP Range 被忽略时重新写入，错误续传区间会被拒绝。
每文件核对官方大小和 MD5；连接提前结束可继续续传，有进展时继续，无进展连续三次则记录失败。
下载前核对完整选择的剩余容量，下载中也检查预留容量。
没有安装外部下载工具或改变网络配置。

旧 `/record/...` 和 API 文件 content 入口在本机网络下不可达，改用同官方记录的
`/records/1036660/files/<name>?download=1`，校验值仍来自官方清单。
本次已实际成功取得多份归档并校验，证明不需要登录；不是所有文件已下载完成。

## 其他已调查的数据集

| 数据集 | 完整入口 | 本次阻碍 |
| --- | --- | --- |
| Artisan Benchmark | <https://huggingface.co/datasets/mastercai/Artisan-Benchmark> | Hugging Face 网络不可达；未取得完整清单、大小或确认数据许可/是否 gated |
| IKEA ASM | [官方 Google Drive](https://drive.google.com/drive/folders/1xkDp--QuUVxgl4oJjhCDb2FWNZTkYANq) | Drive 网络不可达；未取得全文件清单与大小 |
| Assembly101 | <https://huggingface.co/datasets/cvml-nus/assembly101>，另有官方旧 Drive 链接 | Hugging Face/Drive 网络不可达；未取得全文件清单与大小 |

直接 HTTP、IPv4 连接均尝试过，返回网络不可达或连接超时，未遇到可确认的登录/申请授权页面。
本会话也没有可用浏览器连接，无法利用已登录浏览器复核。
因此当前不应要求用户提供账号、Token，或把网络问题说成权限问题。
网络恢复后再获取正式清单；若返回 gated/401/403、登录或申请页面，再告知用户完成授权。

现有系统分区的空间不能保证容纳这几个完整数据集及解压副本。
RBO 下载之外的数据在明确体积前不向此分区追加大包。
素材用途及已核实许可见 [ASSEMBLY_TEST_DATA.md](ASSEMBLY_TEST_DATA.md)。

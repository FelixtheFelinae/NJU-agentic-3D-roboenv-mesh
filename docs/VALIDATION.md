# 第一步验证记录

日期：2026-10-19。执行环境：macOS arm64，Python 3.12.14，项目内 `.venv`。

已执行：

- `pytest -q`：23 项通过。
- `ruff check src tests`：通过。
- `scene-perception demo --output outputs/step1-final`：合成回放完成。
- `scene-perception validate outputs/step1-final/run`：61 个落盘工件通过哈希、mask 几何、RGBA alpha 和用途分区检查。
- 已查看实例编号图和用途分流图；未分割墙面保留为未知。
- `git diff --check`：通过。

测试覆盖：同类多个实例、重复 mask 来源保留、低分检测转未知、独立物体重叠、父子关系、交互对象误入背景、关键对象漏检、缺失/虚构标签、无效框和分数、EXIF、最终文件被修改，以及三种 API 风格的本地 HTTP 请求/响应、认证错误和日志检查。

**尚未执行：真实图片的 VLM/SAM 推理、CUDA 兼容性验证、人工真值准确率评估。** 用户将在之后提供素材并填写 API；当前环境未安装 PyTorch/SAM，也没有 CUDA。

示例的 `checks_status=review` 是预期结果：含未知区域，并且明确使用回放数据。真实 IoU、召回率、背景泄漏率和费用均未推测。

## 第二、三步追加验证（2026-10-19）

正式运行目标统一为 Linux/NVIDIA CUDA。没有单独的 SSH 启动入口，Mac 使用已有连接方式；此次本地仅执行代码、接口与合成夹具检查。

- 最终 `pytest -q`：**42 项通过**；`ruff check src tests scripts`、`git diff --check` 与 CUDA 安装脚本的 `bash -n` 检查通过。
- `demo-video --output outputs/step23-final`：6 帧无损合成视频及参考语义回放完成；`validate` 重新读取 **187 个工件**，检查通过。
- 单图回归 `outputs/image-v02-regression`：**61 个工件**检查通过。
- 合成真值诊断：召回率与 IoU 为 1、前景泄漏 0、ID 切换 0、遮挡恢复率 1。这些只验证已知夹具和指标实现；`acceptance_passed=false`，没有生成真实模型性能结论。
- 反例测试：同类物体交换 ID 时，mask 即使完美也检出 2 次 ID 切换；漏掉物体并把其像素并入背景时，正确计入泄漏。缺失预测、损坏元数据和未处理真值帧保留在分母中。
- 其他新增检查：PTS 采样量化、帧预算、原生 ID 命名空间、会话清理、输入哈希、参考帧外新物体、数据冻结后变更、空标注模板，以及 human 数据禁止使用夹具 CPU 路径。

**仍未执行：真实 CUDA/SAM 视频推理、CUDA IoU 路径实测、真实 VLM API 调用、NVENC 编码以及人工标注集上的正式评估。** CUDA 安装脚本只完成静态语法检查，尚未在 NVIDIA 服务器安装运行；不据此声称部署完成。

## 本机 Linux 环境验证（2026-10-20）

主机为 Ubuntu 22.04.5 / x86_64，NVIDIA RTX 4070 Ti SUPER（16 GB），驱动
580.178.04。项目独立环境使用 Python 3.12.13，配置和使用方式见
[HOST_SETUP](HOST_SETUP.md)。保留上述 Mac 验证记录作为历史证据。

已完成：

- 清理当前项目进程继承的 ROS `PYTHONPATH` 后，基础依赖安装阶段的
  `pytest -q`：**42 项通过**；完整 SAM 3 环境安装后的复查为 **41 项通过、1 项跳过**。
  跳过项专门测试“未安装 SAM”场景，因本机已安装而按测试定义跳过。
  `ruff check src tests scripts`、`pip check`、安装脚本语法和 `git diff --check`
  通过。未修改主机 ROS 或其他 Conda 环境。
- PyTorch `2.10.0+cu128` / torchvision `0.25.0+cu128`，CUDA 运行库 12.8；
  `scene-perception doctor --require-cuda` 通过。实际执行 GPU 矩阵乘法、卷积、
  torchvision CUDA NMS 和项目 `pairwise_iou` CUDA 路径，结果均符合预期。
- 官方 SAM 3 源码提交 `7f473cfd983d5eb43040ff608f66bff0d7bdcb53` 可编辑安装完成。
  图像处理器、图像模型构建函数、视频预测器均可导入，项目使用的方法及参数签名
  检查通过，本地 tokenizer 词表存在；未加载 checkpoint。
- SAM 3 的 CUDA 连通域运算实测通过，使用官方内置 Triton 路径，正确区分
  16 像素与 9 像素的两个区域；未安装可选 `cc_torch`、FlashAttention 3。
- 单图合成流程：**61 个工件**检查通过。
- 视频合成流程：**187 个工件**检查通过；诊断指标符合已知夹具预期，
  `acceptance_passed=false`。
- 通过项目 `encode_preview(..., fixture=False)` 实际调用 `h264_nvenc` 编码
  3 帧 256×256 合成图，再用 PyAV 成功解码全部 3 帧。PyAV 19.0.1，
  使用其 wheel 自带的 FFmpeg 库（libavcodec 63.1.102）。这验证了本机 NVENC
  编码路径，不是 SAM 视频推理或语义准确率验证。

本次工件、日志和版本快照保存在 `outputs/host-setup-20261020/`。
SAM 3 权重访问申请已提交，官方返回等待作者审核；VLM API 配置和真实素材尚待提供。

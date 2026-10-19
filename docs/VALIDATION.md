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

# JSON 契约

对应 `src/roboenv_perception/contracts.py` 中的 `SCHEMA_VERSION = 0.2.0`。

- `config.schema.json`：配置结构，不包含实际 API 配置或密钥。
- `inventory.schema.json`：第一次 VLM 调用返回的候选概念。
- `labels.schema.json`：第二次 VLM 调用返回的实例语义。
- `evaluation.schema.json`：数据集清单、样本及验收门槛。
- `truth.schema.json`：独立逐帧标注（可见性、稳定真值 ID、mask、原图哈希）。

生成命令：`scene-perception schemas --output <不存在的新目录>`。请修改 Python 契约后重新生成，不手改生成文件。

最终场景、对象和背景包使用同一版本标记，字段说明见 README、API 文档和第一步说明。`validate` 会重新读取落盘工件检查引用、哈希、坐标和用途分区。

# VLM API 配置

正式流程中的 API 调用也由 CUDA 服务器上的程序执行。Mac 使用现有 SSH 连接方式即可，本项目不提供独立 SSH 启动器。

## 基本设置

配置使用 TOML。复制 `configs/api.example.toml` 到 `configs/local.toml`，后者已忽略提交。`endpoint` 必须填写完整 POST 请求地址，不自动拼接路径；URL 不接受认证信息、query 或 fragment。

Header 支持 `${ENV_NAME}` 替换，变量未设置时在请求前报错：

```toml
[vlm.headers]
Authorization = "Bearer ${VLM_API_KEY}"
# 也可以改为 "X-API-Key" = "${MY_API_KEY}"
```

无认证本地服务：保留空 `[vlm.headers]` 表并删除 Authorization 行。程序不读取 `.env`、不保存展开后的认证 header、不回显服务端错误体，也不跟随重定向转发认证。

## 常见兼容接口

| `vlm.style` | endpoint 示例后缀 | 默认响应字段 |
|---|---|---|
| `chat_completions` | `/v1/chat/completions` | `choices.0.message.content` |
| `responses` | `/v1/responses` | `output` 中的 `output_text` |

使用 `model` 指定你选择的视觉模型。图片以归一化 PNG 的 base64 data URL 发送，两种模式均不绑定特定供应商。模型必须支持多图输入及结构化 JSON 输出。

`json_mode=true` 仅适用于服务明确支持 JSON object 模式时；默认关闭。提示始终包含完整 schema，返回内容也会在本地验证。不支持模式时不会自动切换服务、格式或模型。

供应商特有参数放到 `[vlm.extra_body]`，例如 `max_tokens`、`max_completion_tokens`、`temperature`，按模型实际能力填写。禁止覆盖 `model/messages/input/instructions/tools/stream`。

## 自定义 JSON HTTP 服务

使用 `configs/generic.example.toml`，设置 `style="generic"`、`request_template` 和 `response_path`。模板只替换以下**完整字符串值**，不执行代码、不在字符串内部插值：

| 占位符 | JSON 替换值 |
|---|---|
| `{{system}}` | 固定系统说明 |
| `{{prompt}}` | 当前阶段提示，包含输出 schema |
| `{{images}}` | 所有输入图片的 data URL 数组 |
| `{{image}}` | 第一张图片的 data URL |
| `{{schema}}` | JSON schema 对象 |
| `{{model}}` | 配置中的模型名 |
| `{{stage}}` | `inventory` 或 `labels` |

例如返回 `{"result":{"items":[{"objects":[],"scene_description":"..."}]}}`，设置 `response_path="result.items.0"`。提取值可以是 JSON 对象或 JSON 文本；空路径表示整个响应就是结果。常见兼容接口也可用 `response_path` 覆盖默认提取路径。

当前支持同步 JSON HTTP POST 和 header 认证。multipart、SSE、异步轮询、OAuth 自动续期、专有二进制图像协议需要未来增加适配器，不能直接兼容所有服务。

## 固定调用与输出契约

1. `inventory`：原图 → 候选概念清单。ID 为 `c001` 等，每个短英文概念提示唯一；相同概念的多个实例由 SAM 返回。可选归一化 xyxy 框只圈一个示例实例。
2. `labels`：原图、编号 mask 叠加图、上下文裁剪 → 对实际 `i0001` 等实例标注类别、属性、任务角色、视觉路线、物理角色与可见性。无实例时跳过第二次调用并记录原因。

完整契约在 `contracts.py`；`scene-perception schemas --output <新目录>` 可导出 JSON schema。缺失语义标签转为未知；虚构 ID、重复 ID、错误父子引用和循环关系报错。

仅对超时、连接失败和 HTTP 408/429/500/502/503/504 进行配置次数内的同请求重试。无法解析内容或语义问题不触发自主提示修正。调整后请显式重跑到新输出目录。

日志保留提示、图片名称/哈希、原始模型响应、usage（若返回）和请求耗时。费用未知时 `provider_cost=null`，不记为 0；不要将密钥写入 prompt、extra_body 或自定义请求模板。

参数格式依据 [OpenAI 官方图像说明](https://developers.openai.com/api/docs/guides/images-vision)和 [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)。第三方兼容程度需在填写接口后实测。

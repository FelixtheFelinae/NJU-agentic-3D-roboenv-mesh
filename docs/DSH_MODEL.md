# ClawSpine 原生 DSH 模型配置

DSH 的任务模型与 EnvCreator 内部 VLM 分开。`integration/dsh_model.py` 读取本机 TOML，生成原生 `llm-pi-ai` 的 provider patch；密钥仅经 `ENVCREATOR_DSH_API_KEY` 传给 DSH 进程。没有额外模型聊天循环，不读取 `VLM_API_KEY`，不自动回退模型。

## 填写和检查

复制 `configs/dsh-model.example.toml` 到 `configs/local-dsh-model.toml`，设为 `chmod 600`，填写服务地址、准确模型 ID、协议及独立密钥。`base_url` 填 API 根路径，不附加 `/responses` 或 `/chat/completions`。

```toml
base_url = "https://your-gateway.example/codex/v1"
model = "your-exact-model-id"
api_protocol = "responses"
api_key = "" # 只在本机填写
```

`api_protocol` 支持 `responses` 或 `chat_completions`，按服务商实际协议选择。可选的 `context_window`、`max_tokens` 是本地容量声明；未填写时使用适配器保守默认值，不能视为服务商已验证的容量。

```bash
.venv/bin/envcreator dsh-model --config configs/local-dsh-model.toml
```

该命令只检查配置，输出不含密钥，不请求模型。`configs/local*.toml` 已被 Git 忽略。

## 原生机器人入口

原有 `envcreator clawspine` 命令追加：

```bash
  --dsh-model-config configs/local-dsh-model.toml
```

适配器将模型选择、provider patch 和独立凭据名称交给原生 `launch_dsh_rsi.mjs`，关闭其他 provider 的自动继承。已有 EnvCreator 插件 patch 可以继续通过 `--native-extra-patch` 加载，两者合并后由原生启动器留存。模型配置不可与其他原生模型参数同时指定。

`--dry-run` 检查原生参数和场景绑定；真实启动仍使用锁定的 Isaac 容器，`--dry-run` 不能证明模型或机器人已运行。

## 2026-10-28 实测

用户更新服务地址后，以 `gpt-6.1-sol`、Responses 协议完成原生 DSH 会话测试：模型调用 `envcreator_inspect`，插件退出码为 0，模型读取结果后正常回复，回合以 `completed` 结束。没有发送机器人命令、生成资产或运行仿真。

记录在 `outputs/dsh-gateway-20261028-v3/`：`result.json`、`native-trajectory.json`、原生私有日志及不含密钥的 provider patch。插件前后记录在 `outputs/dsh-gateway-20261028-v1-bindings/calls/`。旧域名连接失败的 v1/v2 原始记录保留；v1 的 `turn_completed` 仅表示错误回合结束，其 `native_end` 为 `TRANSPORT` 错误，不能视为成功。runner 已改为对该错误返回非零状态。

这次验证了文本、原生工具调用和工具结果回传；尚未验证中转端图像输入、完整容量或 G1 抓持／拧螺丝。

### 同一网关的模型备选

私有 TOML 可添加 `models = ["gpt-6.1-sol", "gpt-6-astra"]`，`model` 继续指定默认项。
两项共享该文件的 API 地址、Responses 协议和独立 DSH 凭据，不读取 VLM 密钥。
使用 `envcreator clawspine ... --dsh-model-config configs/local-dsh-model.toml --dsh-model gpt-6-astra`
在启动时显式选择。未注册的模型会报错，不回退。

托管机器人会话的模型由启动配置固定；新增选项不热切换正在运行的会话。对比测试应分别启动、
固定相同场景和任务，并保留各自轨迹。Astra 已通过配置和原生启动参数检查，尚未验证网关实际推理。

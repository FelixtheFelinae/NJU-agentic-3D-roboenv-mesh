# 外部 ClawSpine 启动器补丁

EnvCreator 通过 `src/roboenv_perception/integration/` 调用外部 ClawSpine checkout，
不复制机器人运行循环，也不依赖 RoboHelix 的 evolution controller。

`local-image-id.patch` 对应本轮外部源码基线
`02cad34a60871d9b1a9d0a1916b10eb5e69ad098`，为原生启动器增加
`--arena-image-id sha256:...`。它接受本机实际存在的不可变镜像 ID，使用 Docker 检查
真实身份并写入运行记录；省略该选项时保留外部项目原来的默认行为。

本机 `/home/felixz/RoboHelix` 已应用该修改，无需重复应用。迁移到同一基线的新 checkout 时：

```bash
git -C /path/to/RoboHelix apply --check /path/to/EnvCreator/integrations/clawspine/local-image-id.patch
git -C /path/to/RoboHelix apply /path/to/EnvCreator/integrations/clawspine/local-image-id.patch
```

补丁附带对应的启动器测试与中英文说明，不包含镜像、模型权重、场景或密钥。
完整调用方式和本轮实际 G1 运行结果见 [接入文档](../../docs/ENVCREATOR_CLAWSPINE.md)。

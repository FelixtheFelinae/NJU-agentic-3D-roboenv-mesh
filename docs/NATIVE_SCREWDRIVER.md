# ClawSpine 原生电动螺丝刀接口

EnvCreator 提供工具资产和简化连接模型；ClawSpine 的原生 DSH 负责观测、生成 Low Reflex、执行 G1 动作及记录会话。没有新增外层模型控制循环。

## 代码调用关系

- `src/roboenv_perception/tools/native_screw_runtime.py`：在每个 PhysX 子步读取批头和螺丝的实际姿态、速度，施加受限电机驱动和连接力。
- `tools/screwdriving.py`：按轴向、横向偏差、六角相位、插入深度与尺寸匹配判定啮合，生成作用于批头及螺丝的相反扭矩。
- `tools/fastener.py`：将累计旋转与轴向退出量通过弹簧、阻尼耦合，并模拟静态起拧阻力和运行阻力；达到有效啮合长度后解除螺纹约束。
- 本机 RoboHelix 的 `playground/tools/vla_isaac/docker/serve_unified_scene.py` 加载场景显式声明的运行组件；三个力学模块随原生 body 源码快照保存。物理步长 1 ms，G1 控制仍为 50 Hz。
- `playground/tools/vla_isaac/vla_isaac/unified_transport.py` 提供经过认证的电机命令与状态接口；HTTP 线程只写入命令，实际力学应用在仿真线程。
- `playground/packages/platform-g1-arena-dsh/src/native.ts` 注册原生 `command_power_tool`、`read_power_tool`；机器人运动继续使用原来的 whole-body / Low Reflex 工具链。

`command_power_tool` 接受 `enabled`、`rpm`、`durationMs`、`release`。本次场景限制在 ±30 rpm、0.2 N·m，单次命令有效期最长 30 秒。负转速在批头朝下时对应拧松；关闭电机执行受限主动制动。每次命令带原生调用 ID，接受命令不代表已实际执行。

`read_power_tool` 返回实测转速、TCP 和轴向、几何啮合、累计旋转、退出量及连接状态。这些属于明确开放的仿真仪表读数，不能称作视觉识别结果。人工停止、任务恢复及服务退出均请求关闭电机。

## 本次物理验证

证据目录：`outputs/g1-screw-mechanics-20261028/`。

`bench.py` 在固定版本 Isaac 容器里测试上述同一份运行组件，采用正常重力和理想 Z 轴移动台架，未使用机器人。六组用例：

| 情况 | 实测结果 |
| --- | --- |
| 对准、匹配批头、开启反转 | 螺纹脱扣，提起至退出约 32 mm，状态为 removed |
| 横向偏移 4 mm | 不啮合，未拧松 |
| 倾斜 8° | 不啮合，未拧松 |
| 批头悬空 | 不啮合，未拧松 |
| 批头与槽尺寸参数不匹配 | 拒绝啮合，未拧松 |
| 对准但电机关闭 | 有几何啮合，未拧松 |

结果见 `bench/result.json`。错尺寸用例检验的是尺寸语义门限，未另建不同尺寸的槽网格。原生传输测试 37 项通过，DSH 平台包 TypeScript 构建通过。

修订记录保留在同目录：显式电机速度反馈因转子惯量小而振荡，改用 PhysX 隐式限矩速度驱动；台架的移动目标改为实时关节目标接口；接触排除提前到物理场景初始化之前。失败目录保留，未将失败记录改写为通过。

第一次原生会话实际调用 read_power_tool 后，DSH 拒收返回值中的 IEEE 负零（INVALID_TOOL_OUTPUT）。读取原始状态确认包含负零后，在原生工具边界沿用已有 JSON 规范化函数处理；该会话停止，修订后另开会话。

## 模型假设与限制

G1 右腕通过固定关节安装螺丝刀，保留负载传递，不是手指抓持。螺丝的真实规格尚未识别：当前采用估计的 M4 类轴、0.8 mm 螺距、4 mm 有效啮合长度，以及 3 mm 六角批头与 3.1 mm 槽。匹配判定的径向间隙仅 50 μm，机器人能否达到仍须实际测试。

螺丝连接是力学替代模型，不使用真实螺纹接触；螺丝和批头的对应接触由几何门限与相反作用力矩替代。固定母件提供支撑反力。没有每步直接写入螺丝姿态或角度。

提起采用显式理想磁性批头：只有先正确啮合才能获得保留能力；螺纹脱扣后以最大 3 N 的弹簧阻尼力吸附，间距超过 15 mm 失效，可通过 release 主动解除。这不是对真实磁铁或真实批头摩擦的验证。

台架通过只证明简化组件可以执行拧松、脱扣和提起；G1 的自主对准和完成情况以新 DSH 会话记录为准。

## 重新打开与本次会话

本机入口仍为 `outputs/g1-wrist-screwdriver-20261028/desktop/start.sh astra`，已指向包含螺丝力学的新场景包；启动后打开认证页面。模型使用独立的 DSH 配置，不使用 EnvCreator 的 VLM 凭据。

修订后的原生会话为 `617763fc-af1b-4ee2-beda-bf28c7b7df93`，证据目录 `outputs/g1-wrist-screwdriver-20261028/native-ui-20261028-203618-724287/`。已提交“右腕固定螺丝刀对准、反转、拧松并取下，最后停止电机”的任务。实际模型为 `gpt-6-astra`，原生日志已记录工具状态读取成功，以及后续全身状态读取。机器人任务尚未宣称成功。

外部 RoboHelix 修改保存为 `integrations/clawspine/native-screwdriver.patch`，包含本次所需的 wrist-tool 场景加载、原生电机接口、运行组件及快照清单；基于 RoboHelix `02cad34a60871d9b1a9d0a1916b10eb5e69ad098` 的本机修改。它与此前的本地镜像补丁有交叠，应用前检查差异，不应无条件叠加。

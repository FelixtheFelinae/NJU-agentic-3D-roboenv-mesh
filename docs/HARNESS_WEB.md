# Harness 联网证据模块

实现：[`src/roboenv_perception/harness/web.py`](../src/roboenv_perception/harness/web.py)。

`WebEvidenceClient.search(identity, cache_dir)` 接收明确识别的物体，返回 `WebSearchResult`。输入包含类别，以及型号或系列；默认只有类别时跳过检索。默认提供无需密钥的 DuckDuckGo HTML 搜索；可切换 Wikipedia API，或配置公开 HTTPS SearXNG JSON 端点。公开搜索服务可能限流、更改页面格式或在运行环境中不可达；此时返回 `status="error"` 和原因，不把空结果解释为没有资料。

```python
from pathlib import Path
from roboenv_perception.harness.web import ObjectIdentity, WebEvidenceClient, WebSearchPolicy

policy = WebSearchPolicy(
    official_domains=("example.com",),
    max_results=5,
    max_pages=3,
)
identity = ObjectIdentity("asset-001", "tablet", "Example", model="Model X")
result = WebEvidenceClient(policy).search(identity, Path("outputs/run-001/web-cache"))
```

每条 `SourceRecord` 保存网页 URL、标题、搜索摘要、抓取正文、正文 SHA-256、抓取时间、官网域名匹配结果及型号/系列提及状态。官网身份只由预先配置的 `official_domains` 判断；网页标题里的“official”不算依据。`model_mentioned` 只是待核对来源，不保证代次、地区版本或零件兼容。搜索摘要只用于发现页面，不能用于建立尺寸或机构结论。

在 Harness 中，`evidence.py:prepare_objects` 后先执行 `scope_plan.py:plan_object_scope`：作者与独立复核最多两轮，将对象分为 `build/background/exclude/unresolved`。只有范围规划已接受且 action 为 `build` 的对象进入 `research_objects`；背景/排除对象不检索型号，设计阶段直接保存 route。范围未接受时不会把任何对象默认为 build。逐轮计划、复核及输入快照保存在 `scope/`。

`research_objects` 对每条提到候选型号的网页执行独立图文核对：输入原始帧、对应物体裁剪图及该页正文。核对结果保存为 `source_image_reviews`，分别为 `match`、`uncertain`、`conflict`。`match` 必须列出可见的区别特征、原图引用及网页正文中的准确引文，并校验引用属于当前输入。仅 `match` 的资料参与后续事实提取。无法辨别具体代次或图文相冲突时，来源和理由仍保留在研究记录中，相关资料不会成为设计可用引用。

`assess_claims(result, claims)` 要求候选结论引用抓取正文中的文字。正文没有该引文时标为 `unverified`；同一字段出现不同候选值时标为 `conflict`。即使得到 `candidate`，也只是有来源文字支撑的候选，仍需与原始图像、型号和实验核对。

```python
from roboenv_perception.harness.web import ClaimCandidate, assess_claims

claims = [ClaimCandidate("width_mm", "123", result.sources[0].source_id, "width is 123 mm")]
assessments = assess_claims(result, claims)
```

请求只使用 HTTPS 文本 GET；不上传图片、视频或本地文件，不带凭据，不跟随重定向。连接前检查 DNS 解析出的全部 IP 是否为公网地址，实际连接固定到已检查的地址；TLS 证书继续按目标域名验证。页面大小、数量、超时和缓存寿命由策略限制。网页正文始终作为不可信数据，不执行其中脚本或指令。当前版本对 PDF、图片和 CAD 下载标为未验证来源，未提供正文取证。

默认 `dns_resolver="system"`。若宿主把公开域名解析为 `198.18.0.0/15` 等非公网地址，仍会拒绝连接。可以明确设置 `WebSearchPolicy(dns_resolver="google")`：通过固定公网地址 `8.8.8.8` 访问 `https://dns.google/resolve`，TLS 验证 `dns.google`，分别查询 A/AAAA，拒绝非公网地址、失败或截断响应。随后连接已检查的目标公网 IP，继续验证目标域名的 TLS 证书。该选项只向 Google 公共 DNS 发送域名，不向其发送搜索词、图像或本地文件；不支持自定义解析器地址、系统代理或关闭证书验证。每次解析最多两个 64 KB DNS 响应，每次请求使用策略超时。

离线验证：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/pytest -q tests/test_harness_web.py tests/test_harness_research.py`，2026-10-27 实测 26 项通过。覆盖非公网 DNS、固定连接、正文引用、来源冲突、型号名称不能充当图像匹配、无法匹配的来源不成为 candidate 等。

2026-10-27 真实公网验证：宿主系统将 `html.duckduckgo.com` 解析为 `198.18.0.74`，默认路径正确拒绝；Google DoH 返回公网地址。设置 `provider="wikipedia", dns_resolver="google", max_results=2, max_pages=1`，检索 `Shimano Deore XT bicycle` 得到 `status="ok"`，抓取 `https://en.wikipedia.org/wiki/Shimano_Deore_XT` 的 7297 字正文，SHA-256 为 `60c9dd283fc90f57ab695b8965cd5fb0231182933fd708663f0305cdb3f036dd`。实际研究记录保存在 `outputs/harness_web_public_probe_20261027/`。这是搜索/抓取连通性验证，未执行真实图像适用性核对，不能据此声称该来源支持当前场景设计。相同 DNS 配置下 DuckDuckGo 返回 HTTP 202，模块保留 `error`，没有将挑战页面作为检索成功。

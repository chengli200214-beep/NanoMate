# 研究来源

研究来源适配器读取用户登记的公开 URL，不克隆仓库，不执行仓库代码，
不跟随文档内的链接，也不接受模型自行添加来源。研究工具边界与哨兵
（Sentinel）决定可以读取哪个已登记来源。适配器实现
`nanomuse/research/contracts.py` 中的 `SourceFetcher` 接口。

## 实时读取

`HttpSourceFetcher` 支持以下来源类型：

| 类型 | 输入 | 保存的正文与版本 |
|---|---|---|
| `readme` | 公开 GitHub 仓库 URL | 固定到完整 commit 的 README |
| `file` | 明确指定的 GitHub `blob` 或 `raw` 文件 URL | 固定到完整 commit 的 UTF-8 文件 |
| `metadata` | 公开 GitHub 仓库 URL | 筛选后的元信息 JSON 与正文哈希 |
| `releases` | 公开 GitHub 仓库 URL | 最多 20 条公开版本记录与正文哈希 |
| `document` | 用户明确添加的文档 URL | 清洗后的 Markdown、纯文本或 JSON 与正文哈希 |
| `manual` | 粘贴的 Markdown 或纯文本，可附原始 URL | 原始文本与正文哈希 |

文件 URL 中的分支名含斜杠时，把斜杠编码成 `%2F`，或者直接使用完整 commit。
文件来源必须指定文件 URL。登记仓库不代表允许自动发现和读取任意文件。

读取 GitHub 文件时先解析 ref，再把解析出的 commit 传给文件接口。返回的
出处 URL 包含完整 commit。保存的 ETag 属于 ref 解析接口，后续收到 `304`
时直接复用已存文件。元信息、Releases 和文档使用各自接口的 ETag。
收到 `304` 时必须已有成功快照。读取失败会返回错误，旧正文不会被改成刚刚核验。

HTML 清洗移除导航、页眉、页脚、脚本、表单、侧栏、隐藏元素和注释，
保留正文中的版本、依赖和接口说明。文档和手动文本的版本是保存正文的
SHA-256 哈希。页面外框改变但清洗后正文相同时，标记为未变化。
GitHub 星数等字段只是维护信号，不能作为质量结论。

默认每次请求限时 30 秒，包括 DNS、重定向和正文读取，最多跟随 5 次重定向。
单次响应上限为 2 MiB，保存的正文最多 200,000 个字符。超过限制时拒绝来源，
不会静默截断证据。适配器请求未压缩正文，服务忽略该要求时会返回错误。

## 网络边界

仅接受不含凭据的 HTTP 和 HTTPS URL。拒绝本机名称、私有地址、保留地址、
回环地址、链路本地地址和 IP 过渡地址。DNS 返回的全部地址都必须是公网地址。
每次请求与每一跳重定向都会重新检查。实际连接固定到已检查的 IP，并保留原始
Host 和 TLS 服务器名称，避免再次解析 DNS 时切换目的地址。关闭环境代理，
拒绝 HTTPS 降级到 HTTP，跨来源重定向移除认证和条件请求头。

可选构造参数 `github_token` 只发送给 HTTPS GitHub API，不进入返回正文和错误消息。
省略时读取 `NANOMUSE_RESEARCH_GITHUB_TOKEN`，显式传入空字符串时不使用令牌。
可注入的传输层是受信任的测试接口，收到固定后的 IP URL、原始 Host 请求头和
`sni_hostname` 扩展。可注入的异步解析器签名为
`resolver(host: str, port: int) -> Sequence[str]`。

`SourceError` 提供 `code`、`retryable` 和可选的 `retry_after` 秒数。
认证及权限错误需要用户处理。超时、DNS 错误、暂时性 HTTP 错误和限流由编排器
安排重试。存在 `Retry-After` 或 GitHub 限流重置时间时保留等待时间。
适配器本身不等待、不重试。

## 显式固定资料回放

`FixtureFetcher()` 默认读取包内 nanoMuse 与 Open Dots 的 README 摘录和
公开版本订阅源摘录。README 固定到 commit。每项记录公开出处、获取时间、
内容版本与选取方式，README 还记录原文件哈希和所选行号。
这些是注明出处的固定资料，不代表实时能力核验或允许移植上游代码。

也可传入字典或 JSON 文件。JSON 必须声明 `"fixture": true` 并包含 `sources`
映射。查询键可以是来源 ID、`kind:url` 或精确 URL。类型前缀区分同一仓库的
README 与 Releases。未登记的键返回 `fixture_missing`，实时读取失败时不会自动
切换到回放资料。

默认回放不发送 HTTP 或 DNS 请求，仍检查输入与已存 URL 的语法、本机名称和
IP 字面量边界，并且只能读取登记的条目。传入解析器时可额外校验主机名的解析结果。
不能通过本机 HTTP 服务绕过来源限制。应用需要标注固定资料模式和脚本化模型结果。

运行适配器测试：`python -m pytest -q tests/research/test_sources.py`。

# Research reasoning

The research reasoner uses the existing `BaseLLM` and Sentinel. It receives a frozen
project specification, registered snapshots, evidence, decisions, and source changes.
`extract`, `compare`, and `impact` return the strict models in
`nanomuse/research/contracts.py`. The orchestrator owns persistence and publication.

Only four tools are registered: `read_source`, `query_evidence`, `submit_evidence`, and
`propose_comparison`. A source read takes a snapshot ID, not an arbitrary URL or path.
The two submission tools return validated drafts held in memory. They do not write the
database, publish a report, or notify anyone. Every model tool call passes through
`Sentinel.guard`. A standalone reasoner without an injected Sentinel uses the upstream
Sentinel with an in-memory audit and declines interactions that require approval.

Quotes must be exact substrings of the identified snapshot. A decision can cite only
active evidence for the same candidate. A documentation page cannot be labelled a code
observation. A hard requirement with no direct current evidence remains unknown.
Missing hard requirements are added to the result as unknown for every candidate.
These checks establish reference integrity; whether a quote semantically supports a
claim still requires evaluation and human review. Documentation claims and source
observations are never labelled as runtime tests.

Comparisons can return structured `conflicts`. Each conflict requires at least two
different current evidence IDs for the same project and candidate, an affected
dimension, and a reason. The reasoner verifies both quotes against their registered
snapshots and derives the displayed version scope from those bindings. A model's
free-text version claim does not replace the source version. Existing open conflicts
remain in subsequent comparisons until superseded in the store. A related `meets` or
`fails` verdict is rejected, with the usual bounded repair allowance. Weak preferences
are conservatively marked unknown. Reports keep both sides in the evidence section
and display the reason and bound versions under uncertainties. Identifying a semantic
contradiction remains a model judgment, not proof that either source is correct.

Each stage allows two repairs for malformed JSON or invalid references, and has a
separate model-call ceiling that also includes tool turns. The `calls` list records
stage, model, request attempt, reported input and output tokens, elapsed milliseconds,
and outcome. Missing provider token usage stays `None`; costs are not estimated.
Provider errors, format errors, reference errors, and call-budget exhaustion have
separate error codes. No failed provider request is retried by the reasoner itself.
Numeric provider `Retry-After` headers are forwarded to the durable retry scheduler.

`set_call_budget(remaining)` sets a shared allowance across extraction, comparison,
and every impact call in a run. Every actual provider request consumes one allowance
before it starts, including failed requests, tool turns, and repairs. Exhausting the
shared allowance raises `ResearchCallLimitError` without making or logging an invented
request. The stage-specific ceiling still applies. The orchestrator must reset the
remaining allowance from persisted call records when resuming a step or a run.

`set_call_observer(callback)` lets the orchestrator persist a `running` reservation with
a unique `call_` UUID before the provider is asked. The final callback uses the same ID
and reports success, failure, or an unknown interrupted outcome. The in-memory list
still contains one entry per actual request. A reservation write failure blocks the
request; a reservation that committed before an error can conservatively remain counted.
Final storage failures propagate while retaining the actual request in `calls`. An
interrupted process can leave a `running` reservation, which must count against the
persisted run budget rather than being treated as an unused allowance.

Context selection retains the complete project specification. It records omitted
records in `context_gaps` when the character budget is exhausted. Registered snapshots
remain readable through `read_source`. This budget describes characters, not billable
tokens. Reports contain six fixed sections, quoted evidence, source URLs, snapshot IDs,
versions, and uncertainty. Report differences use a plain unified text diff.

`FixtureReasoner` requires `spec.mode = "fixture"` and uses the upstream `MockLLM` for
deterministic offline replay. It quotes saved snapshots and presents observations as
preferences; unverified hard requirements stay unknown. Content changes are marked
uncertain until checked. Reports display the offline label. To evaluate annotated
positive and negative verdicts, provide a fixed `MockLLM` script to `ResearchReasoner`.
Fixture output is not a live model evaluation.
The fixture reasoner does not read evaluation answer labels or fabricate conflict
discovery. It can faithfully replay already registered open conflicts as unknown.
Its excerpt formatting filter skips headings, decorative tables, image badges, HTML
tag lines, comments, and fenced code. It keeps the first readable contiguous paragraph
as an exact source substring with its original line breaks, capped at 1,000 characters.
A source with no readable paragraph contributes no fixture evidence. This filter does
not change live-model extraction.

## 中文

提供方返回数值型 `Retry-After` 时，等待秒数传给持久调度器；重试仍受次数上限约束。

研究推理沿用 `BaseLLM` 和 Sentinel，接收冻结的项目条件、来源快照、证据、结论和来源变化。
`extract`、`compare`、`impact` 返回契约定义的结构化结果，正式保存和发布由编排器完成。

只注册读取来源、查询证据、提交证据草稿和提出比较四个工具。来源通过快照 ID 读取，不接收
任意网址或文件路径。提交工具只返回内存中的已校验草稿，不写数据库、不发布报告，也不发送
提醒。每次模型工具调用都经过 Sentinel。独立使用时，默认沿用上游 Sentinel 并保存内存审计，
需要交互批准的调用会被拒绝。

原文引用必须存在于绑定快照中。结论只引用同候选的 active 证据。文档声明与源码观察分开，
缺少直接当前证据的硬条件保持未知，遗漏的硬条件会逐候选补为未知。引用校验不能证明语义
支持关系，仍需评测和人工审查。文档声明和源码观察都不能写成运行验证。

比较结果可以声明结构化冲突，至少绑定两个不同的当前证据 ID、同一项目与候选、受影响维度
及原因。原文逐条核对快照，展示的版本范围从真实绑定派生，不采用模型自报的版本作为事实。
已登记且未被替代的冲突会保留到后续比较。相关结论声称满足或不满足时会被拒绝，并允许
有限次数修复；偏好结论保守降为未知。报告在证据部分保留双方来源，在不确定项展示原因和
版本范围。是否构成语义矛盾仍是模型判断，不能证明任一方正确。

每个阶段最多修复两次格式或引用错误，工具轮次也受模型调用上限约束。调用记录保存阶段、
模型、次数、提供方报告的 token 和耗时；提供方未返回 token 时保留空值，不估算费用。
上下文预算保留完整条件，省略的资料会列出缺口，完整快照仍可通过受限工具读取。

`set_call_budget(remaining)` 设置贯穿提取、比较和多次影响判断的共享调用余额。请求发起前
扣除一次额度，失败、工具轮次和修复均计数。余额不足时抛出 `ResearchCallLimitError`，不会
发送请求或生成虚假调用记录。每阶段原有上限继续生效，编排器恢复时依据已持久化调用重设
余额。离线脚本不读取评测答案，也不人为制造冲突识别成功，只忠实保留已登记冲突的未知状态。

`set_call_observer(callback)` 在模型请求前用唯一 `call_` UUID 预留 running 记录，完成后
用同一 ID 更新成功、失败或中断后未知的状态。内存调用列表仍按实际请求逐次记录。预留写入
失败时不发请求；若已写入后才报错，允许保守计入该预留。最终状态写入失败会向上传播，实际
调用仍保留在 `calls`。进程崩溃留下的 running 记录必须计入持久预算，不能当成未使用额度。

报告固定包含当前推荐、硬条件检查、候选对照、推荐依据、不确定项和下一步，并列出来源、
快照及版本。离线 `FixtureReasoner` 只允许 fixture 模式，通过上游 `MockLLM` 重放当前快照。
未核验的硬条件保持未知，内容变化在核验前保持不确定，报告明确标注离线脚本回放。
离线片段筛选跳过标题、装饰表格、图片徽章、HTML 标签、注释和代码围栏，选取首个可读的
连续段落，保留原文换行并限制在 1,000 字符内。没有可读段落时跳过该来源，不改动真实模型
的提取结果。

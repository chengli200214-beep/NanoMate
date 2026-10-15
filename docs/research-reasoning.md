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

Each stage allows two repairs for malformed JSON or invalid references, and has a
separate model-call ceiling that also includes tool turns. The `calls` list records
stage, model, request attempt, reported input and output tokens, elapsed milliseconds,
and outcome. Missing provider token usage stays `None`; costs are not estimated.
Provider errors, format errors, reference errors, and call-budget exhaustion have
separate error codes. No failed provider request is retried by the reasoner itself.

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

## 中文

研究推理沿用 `BaseLLM` 和 Sentinel，接收冻结的项目条件、来源快照、证据、结论和来源变化。
`extract`、`compare`、`impact` 返回契约定义的结构化结果，正式保存和发布由编排器完成。

只注册读取来源、查询证据、提交证据草稿和提出比较四个工具。来源通过快照 ID 读取，不接收
任意网址或文件路径。提交工具只返回内存中的已校验草稿，不写数据库、不发布报告，也不发送
提醒。每次模型工具调用都经过 Sentinel。独立使用时，默认沿用上游 Sentinel 并保存内存审计，
需要交互批准的调用会被拒绝。

原文引用必须存在于绑定快照中。结论只引用同候选的 active 证据。文档声明与源码观察分开，
缺少直接当前证据的硬条件保持未知，遗漏的硬条件会逐候选补为未知。引用校验不能证明语义
支持关系，仍需评测和人工审查。文档声明和源码观察都不能写成运行验证。

每个阶段最多修复两次格式或引用错误，工具轮次也受模型调用上限约束。调用记录保存阶段、
模型、次数、提供方报告的 token 和耗时；提供方未返回 token 时保留空值，不估算费用。
上下文预算保留完整条件，省略的资料会列出缺口，完整快照仍可通过受限工具读取。

报告固定包含当前推荐、硬条件检查、候选对照、推荐依据、不确定项和下一步，并列出来源、
快照及版本。离线 `FixtureReasoner` 只允许 fixture 模式，通过上游 `MockLLM` 重放当前快照。
未核验的硬条件保持未知，内容变化在核验前保持不确定，报告明确标注离线脚本回放。

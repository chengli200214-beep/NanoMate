# Research scenario runner

These are fictional sources at reserved `.example` hosts. They do not describe Open
Dots, nanoMuse, or any actual repository. The source text, requirements, annotations,
and rerun choices are fixed in `manifest.json`. Annotations are agent-authored drafts
awaiting human review. No independent annotation review has taken place.

Run from the repository root:

```sh
python scripts/evaluate_research.py
python scripts/evaluate_research.py --mode fixture --pilot
python scripts/evaluate_research.py --mode fixture --first-round-only
python scripts/evaluate_research.py --mode fixture
python scripts/evaluate_research.py --mode live --pilot --review-receipt PATH --scoring-rules PATH
python scripts/evaluate_research.py --mode live --review-receipt PATH --scoring-rules PATH
```

The default is `dry-run`: it freezes input hashes and writes a plan, without a model
request or an execution result. `fixture` actually runs the store and orchestrator
against the upstream `MockLLM` through `FixtureReasoner`. Results are labelled
`offline_scripted_workflow_verification`. A successful script is not evidence of model
quality. `live` uses the existing configured `BaseLLM` with exactly the same synthetic
snapshots. The runner does not fetch `.example` URLs and never writes credentials.

The first round has 32 executions: 12 baseline, 12 full, four without decision version
invalidation, and four without notification impact filtering. Two scenarios have two
additional full-version reruns each. The default complete plan therefore has 36
executions, with first-round and preregistered-rerun results identified separately.
`--pilot` runs the two predetermined full-version pilot cases. Use their actual live
usage and the provider's verified prices to estimate spending before the full matrix.
Live batches require hash-bound owner approval and the verified pricing policy in
`review.py`. The shared SQLite ledger reserves the entire vendor context ceiling before
each request at peak cache-miss rates, then settles using returned token counts. It records
a conservative spending upper bound, not a verified bill. Setup, failed attempts and
repairs share the same cap. Unknown usage stops new requests and keeps its reservation.
Transport retries and tool fallback are disabled for all variants. Up to four workers
can share the ledger; `--workers 1` is the default. Model configuration and concurrency
are frozen before execution. Annotations can be approved without claiming independent
annotation review or semantic review of the eventual outputs.

Every execution has a separate SQLite database and artifact directory. A full update
scenario first runs its old state, then applies the new conditions or sources. Setup
calls are retained separately from the target-stage usage. The recompute baseline
receives the latest full conditions and the same complete latest source snapshots;
it is not deprived of new information. Raw target costs can be compared after review.

Two experimental interventions are confined to this runner:

- For the four condition revisions, `no_version_invalidation` restores old decisions
  to `active` and supplies previous-version decisions, evidence and snapshots to the
  model alongside current requirements. Results retain the IDs selected for context.
  Execution fencing and source safety remain enabled. The earlier status-only
  intervention did not change model input and is invalid for evaluating this mechanism.
- For the four source updates, `no_impact_filter` still obtains the normal impact
  judgment, then forces the downstream notification level for each detected content
  change. The forced level is an experimental intervention, not the model's judgment.
  Unchanged source replays still use the ordinary deduplication path.

`freeze.json` records SHA-256 hashes, Git commit, dirty research-tree status, and the
exact matrix before any execution. Copies of the hashed source inputs and research
modules are retained under `frozen-inputs`. Changing frozen inputs or runtime files
mid-run stops the suite. Each execution preserves the actual run state, steps, source
snapshots, reports, model-call records, and any failure. Reusing an output directory
is refused, so later runs cannot overwrite earlier failures.

The results contain mechanical observations and the draft expected annotations.
They do not automatically score semantic support, recommendation correctness, or
notification accuracy. `model_quality_metrics` and `improvement_claim` remain null.
Provider token usage that is absent stays null. Notifications must be inspected by
event rather than conflating report-ready notices with source-change reminders.
The 12 scenarios do not establish statistical significance or broad task quality.

## 中文

这 12 个场景使用虚构资料和保留的 `.example` 域名，不代表任何真实仓库的能力。场景覆盖
首次选型、条件变更和来源更新，每类四个。标注是代理草稿，尚待开发者审定，未经过独立审查。

默认 dry-run 只冻结输入和运行计划，不产生执行结果。fixture 会真实驱动存储和编排器，
使用上游 `MockLLM` 回放，输出明确标记为离线流程验证。live 沿用已有模型配置，输入仍是
同一组固定合成资料。离线脚本通过不能写成真实模型的质量或改善结果。

首轮为 32 次运行，另保留预先指定的两个场景各两次复跑，共 36 次。先运行两个 pilot，
用真实返回的 token 和核实后的单价估算开支。每次运行使用独立目录，保存数据库、报告、
调用和失败；旧版准备调用与目标更新调用分别记录。输出目录不能覆盖已有结果。

基线获得最新条件和完整相同资料。版本消融恢复旧结论的 active 状态，并将旧版本决策、
证据与快照送入当前条件的模型上下文，记录选入的 ID，仍保留执行围栏与来源安全。此前
只改状态的消融未改变模型输入，不能证明该机制的作用。影响消融在正常判断后强制通知，
均明确标为实验干预，不能描述为关闭应用内所有版本校验。

执行前保存文件 SHA-256、commit、工作树状态及代码副本，执行途中输入改变则停止。
结果保留每例实际行为，语义支持关系、推荐合理性和提醒质量等待人工审查，不自动产出改善
数字。提供方未返回的 token 保持空值，未核实价格时不估算费用。

真实模型批次必须通过 `--review-receipt PATH --scoring-rules PATH` 提供批准全部标注、
评分规则、合成范围与新增费用上限的回执。冻结绑定回执和规则哈希；批次共享持久费用账本，
按高峰缓存未命中单价为每次请求预留上下文上限与输出额度，准备、失败、修复和在途请求
均计入。结算值是保守上界，不是服务商账单。用量缺失或即将达到停止阈值则停下，未核对的
中断请求在重启后继续占用预留。统一关闭隐藏重试与工具回退；可用 `--workers` 选择一至四个
独立执行并发，所有变体配置相同。标注获批与输出语义复核分别记录。

# Evaluating research workflows

The reproducible runner is `scripts/evaluate_research.py`; its input matrix and usage
are documented in `evals/research/README.md` in the source repository.

By default it freezes the 12 synthetic scenarios and the 32-execution first-round
matrix, plus four preregistered reruns, without calling a model. Explicit fixture
execution drives the real store and orchestrator with scripted `MockLLM` responses.
Each attempt retains its database, reports, source snapshots, calls, and failures in
a separate directory. Setup usage is separate from target-update usage. Existing
result directories cannot be overwritten.

Fixture traces verify workflow execution only. They do not establish semantic evidence
support, model accuracy, useful reminders, or improvement over a baseline. Live mode
uses the configured model against the same synthetic inputs. Draft annotations still
require human review, and absent token usage remains null. Read the runner's README
for the precise scope of its two experimental interventions.

For a local workbench configured through Settings, pass `--data-dir .local/research-dev`
to `scripts/evaluate_research.py --mode live --pilot`. The selected directory is applied
before the app-settings overlay is loaded. Vault references resolve only in the copied
model client configuration; missing references stop the run before any request. The
saved settings and result summaries retain no resolved key.

`scripts/research_model_smoke.py` separately requires a real model-selected, successful
research tool call. It accepts at most two reasoner requests and does not pass a plain
chat response. It records no semantic accuracy or cost estimate. The selected model's
SDK may perform its configured transport retries within those requests.

## Approved live batches

Live evaluations require `--review-receipt PATH` and `--scoring-rules PATH`. The receipt
must approve every scenario, the exact scoring hash, the synthetic scope and an additional
spending limit. The original draft manifest remains unchanged. The freeze binds the receipt,
scoring, model settings, evaluation code and exact matrix; output review remains separate.

The batch uses native tools, no hidden transport retries and a shared SQLite currency ledger.
Each request first reserves the documented context ceiling and configured output allowance
at verified peak cache-miss rates. Returned usage settles a conservative upper estimate,
not the provider's bill. Setup, repairs and in-flight requests share the same allowance.
Unknown usage or a reservation above the stopping threshold stops new paid requests.
An interrupted request stays reserved across process restarts and requires reconciliation.

`--workers 1` is the default. Up to four independent executions can run concurrently with
one shared ledger. All variants retain the same model settings. Original failures and
unexecuted cases remain visible; stopping never turns them into successful results.

For a separate repair attempt, `--variant` can select one intervention or the full
version. `--budget-ledger PATH` reuses an existing approved ledger, including all prior
settled spending; it does not grant another allowance. Repairs use a new output directory
and freeze, retain a budget-before snapshot, and never replace the original matrix results.

## Interpretation after review

The original `no_version_invalidation` intervention only restored status flags. Current-version
filtering still excluded those decisions from model input, so that historical four-run group
is invalid as evidence for the version mechanism. Its usage and failures remain in the record.
The repaired intervention supplies previous-version decisions, evidence and snapshots alongside
current requirements, while retaining execution fences and source safety checks. Each result
records the decision and evidence IDs actually selected for comparison context.

`freeze_sha256` identifies the exact audit record, including time and machine paths. The added
`content_sha256` fingerprints logical input paths, file hashes, matrix and configuration for
comparison across machines. Target usage includes all following replay attempts, even after
a target failure. `stale_reference_misuse_count` measures obsolete references mechanically;
`stale_fact_semantic_misuse` remains null until the actual claims are reviewed.

The fault matrix now runs the original 30 cases plus nine abrupt publication-process exits.
Report references, database integrity, model-call records and emitted audit logs are checked
where those artifacts exist. These tests establish recovery behavior, not model accuracy.

## 中文

评测运行器默认只冻结场景、SHA-256 和运行矩阵，不请求模型。显式选择 fixture 后，才会
用固定资料和 `MockLLM` 真实执行存储、编排及报告流程。12 个合成场景对应 32 次首轮运行，
另有四次预先指定的复跑。每次尝试保留独立数据库、报告、调用和失败，不覆盖历史结果。

离线通过只能证明脚本流程运行，不能写成语义正确率、有效提醒或模型效果改善。live 模式
沿用现有模型配置，输入仍为同一组合成资料。标注待人工审查，缺失 token 不估算补全。

live 批次另须提供审定回执与评分规则，冻结批准的范围、标注、预算和配置。共享账本在请求前
预留保守费用，覆盖准备、修复和在途请求；用量缺失或即将超出停止阈值时停止新增付费请求。
输出语义仍需另行复核，不能用标注获批替代结果审查。

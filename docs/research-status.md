# Research implementation checkpoint, 2026-10-16

## Requirement and recovery follow-up

Requirement revisions now re-extract unchanged snapshots for the new specification,
superseding obsolete machine facts while preserving user corrections. Inconsistent
verdicts attached to the same hard condition are rejected for repair. Unrelated
observations must not bind to that condition. Short quotation fragments retain their
qualifying source paragraph when it fits the evidence contract; this is not a semantic
entailment guarantee.

The follow-up passed 411 related tests, repository Ruff and formatting checks, and mypy
for 161 source files. Four controlled-clock recovery cases passed in 16.03 real seconds:
cross-midnight, seven days offline, spring daylight-saving transition and fall transition.
Each used separate prepare/resume processes and persisted databases, tested interval and
daily schedules, merged missed checks, and produced no new model calls, reports or notices.
A separate real-clock stopped-worker probe passed after 131.535 seconds. Both probes cover
runtime digest `0aaf2cc4bba567d90b19123b97acc63ee807ccdba47a2937fa91072ce1391078`.
These checks verify application recovery without waiting overnight. They do not establish
OS sleep/resume, long-lived network behavior or a physical overnight pass of this runtime.
Run the reusable [accelerated probe](../scripts/research_accelerated_probe.py) as documented
in [the research guide](research.md).

The new full-variant live rerun completed all twelve synthetic scenarios. Assistant review
found all twelve core scenario behaviors met, including the repaired sandbox and Linux
condition cases. The batch used 181 requests including setup, with 119 target/replay calls.
This is a separate cohort, with no ablation rerun. Three reports still contain incidental
errors: an incomplete description of a source's entire contents, a cross-candidate
readability claim in an evidence fact, and a false unreadable-source note in a locator.
The latter two were identified by the comparison stage but remain visible in the underlying
evidence. All stored quotations match their snapshots; that does not validate these extra
claims. Comprehensive semantic acceptance remains open. See the
[follow-up record](../evals/research/results/2026-10-16-semantic-repair.json).

A separate fixed-README probe succeeded with six model requests in isolated storage.
Both Windows runtime requirements correctly remained unknown and all quotations matched
their snapshots. Its semantic review remains partial: one candidate row still makes a
cross-candidate comparison without that candidate's evidence, an absence claim exceeds
the cited text, and optional relay use is not clearly qualified. It did not replace the
workbench's existing editorial report.

The sections below retain earlier checkpoints and their original populations.

The review repairs are implemented on `research-assistant`. The main repair is `c67f3c9`,
dependency-scoped muting and safe diagnostic refinements are `c78fe0c`, and the shared
hard-condition aggregate verdict is `d45ccad`. The earlier conflict-reminder fix was
integrated as `ef5601e`. The owner subsequently delegated the remaining review to the assistant
and waived the three-person trial requirement. Assistant review is complete; it is not
independent human validation, and the retained outputs do not meet every quality gate.

## Review repairs

- H1: the recommendation binds to one structured candidate that satisfies every hard
  requirement. The renderer repeats the candidate gate. Recommendation and hard-condition
  tables share one verdict rule: a preference alone is unknown, a supplementary preference
  does not erase meets, and fails or unknown prevents selection.
- H2: each definitive hard-condition reference needs direct evidence in that condition's
  dimension. This does not establish semantic entailment or validate every compound fact.
- H3: unchanged source text retains its immutable snapshot across unrelated commits.
  Sources separately record observed versions and ETags. Repository counters and push time
  are excluded from metadata content.
- H4: the version ablation now supplies old decisions, evidence and snapshots alongside
  current requirements. All four repaired ablations actually included historical decision
  IDs. The old status-only ablation is invalid as evidence for the mechanism.
- H5: hard-condition and conflict evidence is prioritized. Named critical omissions remain
  visible; mandatory metadata that cannot fit causes an explicit stop.

The same change fixed truncated tool execution, stale decision filtering, scoped mute
lifetime, HTML header/hidden-content handling, SHA-only GitHub ref reads, background refresh,
optional correction quotes, timezone bounds and safe bilingual error diagnostics. Database
upgrades save a backup. More detail is in the [contracts](research-contracts.md).

## Automated verification

| Check | Observed result |
| --- | --- |
| Current research, evaluation and documentation tests | 405 passed |
| Web | npm ci, lint, TypeScript, 133 tests and production build passed; generated assets committed |
| Fault matrix | Original 30 cases plus nine actual publication-process exits, 39 passed; also covered by the current research suite |
| Static analysis | Repository Ruff and format checks passed; mypy passed for 161 source files |
| Complete repository test attempt | 1028 passed, two failed, 59 skipped, one deselected |
| Targeted environment recheck | Both failures passed after process-only loopback NO_PROXY and Git for Windows usr/bin configuration; the earlier whole-suite result is retained |
| Real GitHub source tracking | Two README sources read live, then checked after 15 real seconds; zero additional analysis calls, reports, events or notifications; initial reasoning used MockLLM |
| Current stopped-worker recovery | 131.752 real seconds, different processes, zero new calls, reports or notifications |
| Earlier overnight recovery | Passed at 2026-10-16 10:05 Asia/Shanghai, 39,281 real seconds, crossed the local date; it covers the earlier runtime |

The current short probe uses runtime digest `0d9559cd495f58985962d53e78ca16ff5f2eebc14bc5aa0037f8003db9c814d4`. It is not an
overnight test. The overnight pass does not establish an overnight pass of the current
runtime. The GitHub probe did not require an unrelated commit to occur during its interval;
changed-commit/equal-content behavior is covered by the controlled regression test.
Windows dependency snapshots mark pywin32 as win32-only. A new Linux installation of this
repair is not claimed. See the [test environment notes](research.md).

## Retained live evaluations

The owner approved S1: two fictional candidates, eight synthetic snapshots, all twelve
scenario labels and the scoring rules. No independent annotation review took place.
Real repository comparisons are separate. Approval of inputs is not review of outputs.

| Cohort | Workflow result | Interpretation |
| --- | --- | --- |
| Original 36 executions | 34 succeeded, one failed, one needed attention | The full conflict case also missed its expected reminder despite succeeding as a workflow |
| Review-repair 20 executions at c67f3c9 | 19 succeeded, one failed | Eleven of twelve full workflows succeeded; both four-run ablations succeeded |
| Separate conflict follow-up at c78fe0c | One succeeded, one source-change reminder | Does not replace the failed extract-format attempt in the 20-run cohort |

The repair cohort made 319 provider requests including setup, with 2,446,971 tokens.
Target stages and subsequent replay attempts accounted for 176 requests and 1,449,491
tokens. The separate conflict follow-up made 22 requests including setup. Before/after
quality and equal-quality savings are not established.

The unchanged C4 platform page already refers to a Linux-only README before the update.
This limits clean before/after interpretation and unassisted contradiction claims. Inputs
and expected labels were not changed to obtain a pass. Original failures remain available.

## Real report and source correction

The workbench's two-repository report was re-extracted with the real model using the same
saved public README versions, with six requests. Both Windows requirements remain unknown
because the stated requirement rejects documentation as a substitute for runtime tests.

A separate assistant-authored source correction expanded three incomplete quotations,
qualified the experimental local-Claude desktop route and made the optional own-key,
no-server path explicit. Its Markdown and metadata identify it as `agent_editorial_correction`,
with zero new model requests and owner review pending at that checkpoint. The owner later
delegated the review to the assistant. The raw model report remains in
history and is the one used for model-quality review.

The current renderer re-rendered 22 retained reports without modifying model results.
One Linux ablation report's mixed meets/preference hard-condition display changed from
unknown to meets. That display correction is separate from model scoring.

## Usage and open acceptance

Since approval, all phases shared one ledger: 884 settled provider requests, 6,426,639 input
tokens and 1,004,066 output tokens. No usage is unsettled. The conservative peak cache-miss
cost upper estimate is 20.885806 CNY, not a verified provider bill. Rates of 2 CNY per million
input and 8 CNY per million output tokens were rechecked on 2026-10-16 against
[DeepSeek pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/).
Earlier pilots, workbench requests before approval and coding-assistant usage are excluded.

The assistant reviewed all 31 current and 38 historical compound claim groups against the
fixed sources. Current outputs have 26 supported groups and 5 partially supported groups;
historical outputs have 29 supported and 9 partially supported groups. Missing bindings,
truncated quotations and claims broader than their cited text count as partial support even
when the full document contains additional relevant facts. These are assistant judgments,
not independent human labels or comparable before/after accuracy measurements.

Review of the twelve full first-round scenarios found nine meeting their core requirements,
two partial cases and one extraction failure. B1 retains a dimension mismatch after a new
hard condition; B2 incorrectly attaches an unrelated Windows uncertainty to the Linux
condition. Core scenario success does not imply every incidental claim is supported.
The original all-claims-supported and ten-complete-scenarios gates are not established.

The code review found that editing requirements reactivated disabled sources for retained
candidates. This is fixed and covered by regression tests. Earlier evaluation artifacts retain
the extra source-failure notices; they were not rewritten or paid-rerun for this review.
The three-person trials are waived, not passed. Independent owner review is not claimed.
The current runtime still lacks a new overnight observation. No additional paid model
requests were made for this review. The editorial version does not replace model outputs.

The [sanitized repair record](../evals/research/results/2026-10-16-review-repair.json),
[original matrix](../evals/research/results/2026-10-16-live-matrix.json) and
[earlier isolated repair](../evals/research/results/2026-10-16-conflict-repair.json) retain
separate populations. Raw logs, frozen inputs, databases and review forms remain local.
Video, presentation slides and screen recordings remain outside delivery scope.

The delegated review and source-selection fix passed 407 related checks, Ruff, formatting,
mypy and the documentation build. See the [review summary](../evals/research/results/2026-10-16-delegated-review.json).

# Research implementation checkpoint, 2026-10-15

The local research workspace completed a real DeepSeek tool call, two live pilots and
a two-repository comparison with process recovery. Full evaluation, overnight acceptance
and human review remain open. This checkpoint does not establish completion of all plan gates.

The implementation reuses nanoMuse's model adapters, Sentinel, authentication and web
shell. New modules own registered-source reads, research state, leased steps, evidence,
conflicts, report publication, tracking and scenario execution. See
[attribution](../UPSTREAM.md) and [the user guide](research.md).

## Verified behavior

| Check | Observed result |
| --- | --- |
| Separate local clone and new Python 3.13.9 environment | 90 locked dependencies installed; research suite: 226 passed |
| Web checks from the separate clone | ESLint and TypeScript passed; 130 tests passed; production build passed |
| Fault matrix | Ten offline assertion groups, three repetitions each: 30 passed |
| Actual abrupt process exits | All four named crash points returned exit code 86 and recovered; old report remained readable during an interrupted replacement |
| Source and reasoning safety | Project isolation, quote binding, unavailable/private sources, redirects, injection and restricted tool checks passed in the research suite |
| Host integration | Authentication, startup, shutdown, WebSocket and static-cache checks: five passed |
| Real local server | Authenticated project creation, offline research, report export and persistence across restart checked |
| Static analysis | Ruff passed; mypy passed for 20 source files |

The fault matrix uses FakeClock and database reopen for state transitions. Additional
subprocess tests exercise actual process death. Neither proves an overnight elapsed-time
test or a remote-provider recovery test. The dependency snapshot and reproduction were
checked on Windows; a second operating system has not been claimed.

## Frozen scenario run

The first checkpoint used code commit `52a1021` with a clean research tree and freeze SHA-256
`83672183d7126b44e8a778b54ea15d80ac0fe13c8b86fa713af96e0fddf35ef9`.

- Twelve synthetic scenarios produced 32 initial executions and four preregistered
  repeats. All 36 workflows reached succeeded.
- Target stages made 72 scripted MockLLM requests. Provider tokens, monetary cost,
  semantic quality and improvement metrics remain null.
- No obsolete or unbound evidence reference was observed in the full-version runs.
- The fixture does not automatically identify the two annotated contradiction cases.
  It also leaves the three annotated important updates uncertain, with no source-change
  notification. These expected behaviors have not been satisfied by the fixture.
- Scripted structured-result tests separately verify that a supplied valid conflict is
  persisted, both sides remain visible, definitive verdicts are rejected, meaningful
  updates notify once, and mute rules suppress the scoped notification.

Workflow success is not a semantic pass. Annotations are agent-authored drafts awaiting
human review. The [sanitized result summary](../evals/research/results/2026-10-15-offline.json)
retains expected and observed behavior. Detailed local traces, databases, frozen input
copies and JUnit files are under `.local/verification/current/`.

## Open acceptance

1. Review the draft annotations and choose the full evaluation budget using the measured
   pilot usage below. Model configuration, the tool probe and both live pilots completed.
2. Complete actual overnight catch-up and next-day scheduling. Short wall-clock recovery
   passed and the overnight timer is running, awaiting real elapsed time.
3. Review cited claims and record reminder quality and maintenance costs. A live public
   repository comparison completed; no semantic accuracy or improvement percentage is available.
4. Complete human trials and an owner review of generated code and design tradeoffs.

## Same-day follow-up

Runtime commit `d675f28` reran all 36 fixture workflows, with 72 target MockLLM requests.
Its freeze SHA-256 is
`1fa1b89dd176c0391e7a2ceeb08e975022596d74448cc509f53f4ee1fabec34a`.
The semantic expectations still require a real model and reviewed annotations.

- 248 research tests, 97 documentation checks and 30 repeated fault assertions passed.
  Ruff passed; mypy checked 22 runtime and acceptance-script files. Web code did not change.
- Explicit `--source-dns cloudflare` obtained real public addresses on this machine while
  retaining private-address rejection, redirect validation and IP pinning. Windows DNS
  settings were not changed.
- Three retained network attempts cover every required target at least once. The first
  was 5/6, with oversized releases rejected. After bounded release pagination, the next
  was 5/6, with a fixed file rate-limited by GitHub. A conditional raw read then passed
  that file check 1/1. This is cumulative source coverage, not one 6/6 run. Failures remain.
- Release JSON declares `history_complete=false` for up to five records and keeps bodies
  intact. Full-commit files avoid API quota and never receive the API token at the raw host.
- Model commands load the selected workbench overlay and resolve vault references only in
  the client copy. Before user configuration, preflight returned `needs_configuration`, with
  zero model requests. A plain chat response cannot pass the research-tool probe.
- A short probe passed after 131 real seconds in distinct processes. Each schedule merged
  into one check; unchanged sources made zero new calls, reports or notices. SQLite passed
  integrity checks. Its inputs remain saved excerpts and scripted MockLLM responses.
- The isolated overnight worker has exited. A background timer will start a new worker after
  09:00 Asia/Shanghai on 2026-10-16. This is pending, not passed. The computer and timer must
  stay running; research runtime changes reject the probe. The workbench restarted with both
  projects and reports intact and returned health 200.
- Local annotation, trial and owner-review worksheets are prepared but unfilled. No outside
  invitations were sent and no self-test is reported as a human trial.

Raw follow-up artifacts are under `.local/verification/2026-10-15-continuation/`, with
separate directories for network, model preflight, wall-clock and overnight checks.
The [sanitized follow-up](../evals/research/results/2026-10-15-followup.json) retains failures
and pending items; raw data and keys stay local. The [showcase guide](research-showcase.md)
adds product scope, diagrams and a project walkthrough. Deliverables are the workspace,
Markdown reports, documentation, screenshots and acceptance records. Video, presentation
slides and screen recordings were removed from the delivery scope at the owner's request;
the generated video and its dedicated generation materials were removed.

The initial broad upstream Python baseline was 713 passed, 59 skipped, one deselected
and three failed on this Windows machine. The failures concerned a runtime-check
temporary directory, a channels CLI request returning 502, and a shell test requiring
`sleep`. They have not been converted into passes. The upstream web line-ending issue
was corrected through LF attributes for shared Ideas data. The complete upstream Python
suite is therefore not reported as green.

## Live model and recovery acceptance

Runtime commit `9be1805` repaired a failure found during real repository comparison.
The model initially requested more than eight tools in one response; extraction stopped
without publishing a report. Oversized batches now execute no tools and receive feedback
within the existing two-repair allowance. The failed attempt remains saved.

- The configured DeepSeek model selected and executed `read_source` in one request,
  reporting 507 tokens. The key stayed in the local vault and is absent from the records.
- Both original pilots completed, using 48 requests and 475,890 tokens including setup.
  On the repaired runtime both pilots completed again, using 41 requests and 413,848 tokens
  including setup. Target stages accounted for 29 requests and 325,907 tokens in the repaired
  run. These are separate attempts, not a controlled improvement experiment or a price quote.
- The dependency-update pilot registered one source-change notice. Both pilots had six-section
  reports and no observed obsolete or unbound evidence references. Inputs remain synthetic;
  annotations and semantic support await human review. The full matrix has not run live.
- The workbench fetched current public READMEs for Open Dots and nanoMuse, preserving commits
  `64e48f6a784ee83471c45daf4dcfc5393dc6f30e` and
  `c33c9e83804d226b689be28d4ebc8a70b798074e`. A forced process exit on the repaired runtime
  recovered extraction in a new process and published one report with 14 evidence records.
  Exact quote, candidate and version binding checks passed; semantic correctness remains open.
- Across all retained workbench attempts, 16 requests include two interrupted outcomes marked
  unknown. Their unreported tokens stay unknown. A subsequent unchanged-source check succeeded
  with zero new model requests, reports or notifications; SQLite integrity was `ok`.
- The repaired runtime passed 251 research tests, 97 documentation checks, Ruff and mypy for
  23 files. The web implementation did not change. The earlier broad upstream failures remain.
- The earlier overnight preparation was superseded before resumption because the runtime
  changed. A fresh isolated preparation started at 23:10 Asia/Shanghai on 2026-10-15 and waits
  until 09:00 on 2026-10-16. Its runtime SHA-256 is
  `9fe9f9ee8d905c63c1bbb3a236bd0473d37afebd048b610398bfb06f6c7d39ad`.
  This remains pending; the computer and timer must stay running.

The [sanitized live summary](../evals/research/results/2026-10-15-live.json) retains both pilot
attempts and the original repository failure. Raw records and the exported report are local
under `.local/verification/2026-10-15-live-workbench/`. Full evaluation still requires reviewed
annotations and a total budget; human trials and owner review remain open.

The user planning file is retained locally, with delivery requirements updated at the owner's
request. Runtime data, keys, raw logs and internal planning are excluded from the commits.

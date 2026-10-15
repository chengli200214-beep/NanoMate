# Research implementation checkpoint, 2026-10-15

The local research workspace runs and produces labelled offline reports. Real-model,
overnight and human-review acceptance remain open. This checkpoint
does not establish completion of the two-day plan's gates.

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

1. Configure a local DeepSeek key, test a real research tool call, then run the two
   live evaluation pilots before deciding the full evaluation budget.
2. Complete actual overnight catch-up and next-day scheduling. Short wall-clock recovery
   passed and the overnight timer is running, awaiting real elapsed time.
3. Review scenario annotations and cited claims, run the live comparison, and record
   actual reminder quality and maintenance costs. No improvement percentage is available.
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
  the client copy. Local preflight remains `needs_configuration`, with zero model requests.
  A plain chat response cannot pass the new model-selected research-tool probe.
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
adds product scope, diagrams and a script. A 180-second narrated offline screenshot walkthrough
passed duration, full audio/video decode and key-frame checks. Continuous operation recording
remains pending.

The initial broad upstream Python baseline was 713 passed, 59 skipped, one deselected
and three failed on this Windows machine. The failures concerned a runtime-check
temporary directory, a channels CLI request returning 502, and a shell test requiring
`sleep`. They have not been converted into passes. The upstream web line-ending issue
was corrected through LF attributes for shared Ideas data. The complete upstream Python
suite is therefore not reported as green.

The original user planning file is retained locally without replacing it. Runtime data,
keys, raw logs and internal planning are excluded from the commits.

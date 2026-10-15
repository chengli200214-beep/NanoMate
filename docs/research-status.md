# Research implementation checkpoint, 2026-10-15

The local research workspace runs and produces labelled offline reports. Real-model,
public-network, overnight and human-review acceptance remain open. This checkpoint
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

The current run used code commit `52a1021` with a clean research tree and freeze SHA-256
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
2. Correct public-source routing. This machine returned private addresses for public
   GitHub hosts, so source protection rejected the live smoke test. The guard remains
   enabled.
3. Perform actual stopped-service catch-up and next-day scheduling. FakeClock cases
   passed; a real overnight interval has not elapsed as part of this checkpoint.
4. Review scenario annotations and cited claims, run the live comparison, and record
   actual reminder quality and maintenance costs. No improvement percentage is available.
5. Complete human trials and an owner review of generated code and design tradeoffs.

The initial broad upstream Python baseline was 713 passed, 59 skipped, one deselected
and three failed on this Windows machine. The failures concerned a runtime-check
temporary directory, a channels CLI request returning 502, and a shell test requiring
`sleep`. They have not been converted into passes. The upstream web line-ending issue
was corrected through LF attributes for shared Ideas data. The complete upstream Python
suite is therefore not reported as green.

The original user planning file is retained locally without replacing it. Runtime data,
keys, raw logs and internal planning are excluded from the commits.

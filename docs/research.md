# Continuous technical research

The research workspace compares two or three repositories, keeps evidence and reports
with their source versions, and checks registered sources for changes. It is a
single-user extension of nanoMuse's Python runtime and web console. Upstream model
adapters, authentication and Sentinel are retained. See [upstream attribution](../UPSTREAM.md).

## Run locally

Python 3.13 and Node.js 22 or newer are required for this development path. The
Python dependency snapshot was produced on Windows with Python 3.13.9.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-research.txt
.venv/Scripts/python.exe -m pip install --no-deps -e .
```

Build the web console from the repository root:

```powershell
npm --prefix web ci --registry=https://registry.npmjs.org/
npm --prefix web run build
.venv/Scripts/python.exe scripts/research_dev.py --seed-fixture
```

Open `.local/research-dev/open-research.url` on Windows. The shortcut contains the
local pairing token. The server binds to `127.0.0.1:8787`, retains Bearer authentication,
and stores the token, settings, audit, SQLite database and reports under
`.local/research-dev/`. That directory is ignored by Git. Stop the server with Ctrl+C;
run the same command and data directory to resume it. `--port` and `--data-dir` allow
another isolated local instance. Do not run two servers against one data directory.

This dedicated launcher skips chat onboarding and does not require a Cloud account.
It disables connected mail, calendar, contacts, GUI, browser, skills, MCP and Hub for
this local process. Live research still requires a model configuration. The normal
`nanomuse serve` entry point retains its existing setup behavior.

## Configure DeepSeek

Open Settings, then Connections and the chat model. Choose the DeepSeek provider,
enter your own key and test the connection. The current documented OpenAI-compatible
base URL is `https://api.deepseek.com`, and `deepseek-flash` is a supported model ID.
These values were checked against [DeepSeek's official API documentation](https://api-docs.deepseek.com/)
on 2026-10-15. Another domestic provider can use the existing catalogue or a custom
OpenAI-compatible endpoint. Select a model that supports tool calls and structured
JSON responses. A connection test alone does not validate research quality.

Keys entered through Connections use nanoMuse's vault. Do not put them in project
requirements, source documents, fixtures, commits or screenshots. Create a **live**
project for real-model research. An existing fixture project cannot switch mode.

## Use the workspace

1. Create a project with two or three GitHub repository URLs, a goal and explicit
   hard requirements or preferences. Choose live research or labelled offline replay.
2. Register extra README, repository file, releases, metadata, public documentation or
   pasted text. Every source belongs to a candidate. Imported text is treated as data.
3. Start research. Overview shows the six-section report; Evidence retains quotes,
   exact source versions, verification levels, correction history and conflicts.
4. Use Runs to pause after the current step, resume, cancel, or retry failed steps.
   Already committed steps survive restarts. A hard requirement without adequate
   current evidence stays unknown.
5. Revise conditions or correct evidence. The old report becomes stale and a new run
   is queued. Old workers cannot publish over the new inputs. Reverify schedules a
   fresh source check and evidence extraction.
6. Enable tracking in Changes with a timezone and interval or local daily time.
   Checks happen while the service runs. Missed checks merge into one catch-up run
   at restart. Unchanged sources do not trigger model requests or a new report.
7. Inspect version differences, export Markdown, accept updates, give reminder
   feedback or mute a source's impact level. Only recommendation-level source changes
   create an impact notification; a new research report has a separate ready notice.

## Persistence and limits

`research/research.db` uses SQLite WAL. Atomic claims run one research step at a time;
leases last 60 seconds and renew every 10 seconds. Attempt, specification and input
versions fence late results. Source, specification and report revisions are immutable.
Publication updates the report pointer and its logical notification in one transaction.
A temporary report file can remain after abrupt exit without replacing a formal report.

Transient step failures have at most three attempts. An unavailable individual source
is shown as a gap; all sources unavailable, missing credentials or an exhausted model
budget require attention. The default budget is 40 model requests per run, editable
from 1 to 200. Tool rounds, repairs and failures count. Requests reserve a persistent
call record before the provider is contacted; interrupted requests retain unknown usage.
Provider SDK transport retries are not separate reasoner requests. Tokens not returned
by the provider and costs without a verified price remain unknown.

The research model sees only four research tools. Registered public reads also cross
Sentinel, and public HTTP fetches reject private destinations, unsafe redirects and DNS
rebinding. Proxy configurations that return private or fake IPs for public GitHub names
will fail closed. Correct the resolver/proxy routing before live-source acceptance.
Manual imports and offline fixtures can still exercise the workflow.

## Verification and remaining acceptance

```powershell
.venv/Scripts/python.exe -m pytest -q tests/research tests/test_research_reasoning.py tests/test_research_call_budget.py tests/test_research_conflicts.py tests/test_research_fixture_excerpt.py tests/test_research_evaluation.py
.venv/Scripts/python.exe -m ruff check nanomuse/research
.venv/Scripts/python.exe -m mypy nanomuse/research --follow-imports=silent
npm --prefix web run check
.venv/Scripts/python.exe scripts/evaluate_research.py --mode fixture
.venv/Scripts/python.exe scripts/research_fault_matrix.py
```

The fault tests include four real abrupt subprocess exits, lease recovery, stale input
rejection, atomic publication and project isolation. The scenario runner freezes code
and data for 32 initial executions and four preregistered repeats. It preserves semantic
expectations and observed behavior separately. Offline results do not establish real
model accuracy, useful reminders, or a reduction in research cost.
The fault-matrix command selects ten assertion groups with three repetitions each.
Its state-recovery cases use FakeClock and database reopen; the separate subprocess
tests additionally verify real `os._exit(86)` recovery. It refuses to overwrite a result.

Live DeepSeek research, real-source network access, overnight scheduling, next-day
checks, reviewed scenario labels and human trials are separate acceptance items.
See [contracts](research-contracts.md), [reasoning](research-reasoning.md),
[source adapters](research-sources.md) and [evaluation](research-evaluation.md).

## Implementation ownership

AI coding agents implemented the source, reasoning, web and evaluation lanes in
separate worktrees. Integration defined the shared contracts, durable state and
orchestration, reviewed the modules, and added consistency and process-recovery checks.
This attribution describes the implementation process. It does not assert that the
project owner has independently reviewed the generated code or completed user trials.

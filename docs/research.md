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

If system DNS returns a proxy's synthetic addresses, explicitly select public DNS
for registered research sources:

```powershell
.venv/Scripts/python.exe scripts/research_dev.py --seed-fixture --source-dns cloudflare
```

This setting affects the research source reader only. It sends source hostnames to
Cloudflare, retains public-address checks and does not alter Windows network settings.
See [source transport](research-sources.md).

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
   Reports use requirement text for comparison labels rather than internal condition IDs.
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
   Replaced wording is retained as superseded history after successful reverification.
6. Enable tracking in Changes with a timezone and interval or local daily time.
   Checks happen while the service runs. Missed checks merge into one catch-up run
   at restart. Unchanged sources do not trigger model requests or a new report.
7. Inspect version differences, export Markdown, accept updates, give reminder
   feedback or mute a source's impact level for the current condition version and affected
   condition IDs. Only recommendation-level source changes
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
The fault-matrix command retains ten assertion groups with three repetitions each and
adds nine publication-process exits, for 39 cases. Lease timing uses FakeClock and
database reopen; publication cases verify real `os._exit(86)` recovery. It refuses to
overwrite a result.

For the complete upstream suite on Windows, expose Git for Windows' `usr/bin` tools
in the test process and bypass proxies for loopback requests. The CLI and shell failures
observed without these settings passed targeted rechecks with them. These commands affect
only the current PowerShell process and retain existing proxy exclusions:

```powershell
$researchGitRoot = Split-Path (Split-Path (Get-Command git).Source -Parent) -Parent
$env:PATH = (Join-Path $researchGitRoot 'usr/bin') + ';' + $env:PATH
$env:NO_PROXY = '127.0.0.1,localhost,::1,' + $env:NO_PROXY
.venv/Scripts/python.exe -m pytest -q -m 'not live'
```

Live DeepSeek research, real-source network access, overnight scheduling, next-day
checks and reviewed scenario labels are separate acceptance items. The owner waived human trials.
See the [product and demonstration guide](research-showcase.md) for scope, diagrams and
the project walkthrough.

Additional acceptance commands retain independent output directories and refuse to
overwrite results. The model commands use the workbench's settings and encrypted vault:

```powershell
.venv/Scripts/python.exe scripts/research_network_smoke.py --source-dns cloudflare --output .local/verification/network-check
.venv/Scripts/python.exe scripts/research_model_smoke.py --data-dir .local/research-dev --output .local/verification/model-check
.venv/Scripts/python.exe scripts/evaluate_research.py --mode live --pilot --data-dir .local/research-dev --review-receipt PATH --scoring-rules PATH
.venv/Scripts/python.exe scripts/research_wall_clock_probe.py watch --output .local/verification/wall-clock
.venv/Scripts/python.exe scripts/research_wall_clock_probe.py watch --overnight --daily-time 09:00 --timezone Asia/Shanghai --output .local/verification/overnight
```

The network command reads both repositories, metadata, releases, a pinned file and
public documentation through Sentinel, then checks unchanged reads. `--kind file`
can verify a failed kind in a new output directory without repeating every request.
The model probe requires an actual model-selected research tool call. A normal chat
reply cannot pass it. Missing credentials record `needs_configuration` with zero
requests. Neither command measures model quality.

The wall-clock probe creates two isolated fixture projects and stops their worker
process. A timer starts a different process after 130 real seconds, checking missed-run
merging and zero new calls, reports or notices for unchanged sources. `--overnight`
waits until the next local date at the chosen daily time and requires six real hours.
The computer and timer must remain running. Runtime changes during the interval reject
the probe. Its database is separate from the workbench's projects; the timer itself
does not run a research worker. Only a completed overnight summary establishes that
acceptance item. A human-review claim requires an actual human reviewer. The owner waived
three-person trials and delegated the remaining review to the assistant; see the checkpoint.
The [implementation checkpoint](research-status.md) records measured results and open gates.
See [contracts](research-contracts.md), [reasoning](research-reasoning.md),
[source adapters](research-sources.md) and [evaluation](research-evaluation.md).

## Implementation ownership

For a fast recovery check, run `python scripts/research_accelerated_probe.py --output <new-directory>`.
It uses controlled application clocks in separate prepare and resume processes for a date
change, a week offline and both New York daylight-saving transitions. Each case checks interval
and daily scheduling, merged catch-up, unchanged reports and zero duplicate model calls or
notices. It never changes the operating-system clock. Combine this with the short real-clock
probe and crash matrix for application-level recovery acceptance. It does not verify physical
overnight operation, OS sleep or long-lived network connections.

AI coding agents implemented the source, reasoning, web and evaluation lanes in
separate worktrees. Integration defined the shared contracts, durable state and
orchestration, reviewed the modules, and added consistency and process-recovery checks.
This attribution describes the implementation process. It does not assert that the
project owner has independently reviewed the generated code or completed user trials.

Editing requirements preserves disabled sources for candidates that remain in the project.
Removing a candidate disables its sources; adding that candidate back reactivates them.

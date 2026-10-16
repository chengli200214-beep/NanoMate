# Personal assistant development entry

The personal assistant opens at chat and retains the research workspace. This first
integration restores the existing conversation, goals, reminders, memory and file tools.
The task ledger coordinates research and user-confirmed checkpoints. Existing goals and
reminders remain available with their original scheduler. The underlying tools remain
subject to Sentinel. Cross-application recovery is still a separate implementation stage.

## Separate data and process

Use a separate checkout while a research recovery observation is pending. Do not reinstall
an editable package over that observation's environment or edit its runtime and database.
From the assistant checkout, using its configured Python dependencies:

```powershell
python scripts/assistant_dev.py --offline
```

The default address is `http://127.0.0.1:8788/?tab=chat`. Open the private shortcut
`.local/assistant-dev/open-assistant.url` to pair the browser. The shortcut contains a local
token and must not be shared. Authentication stays enabled. The research-only launcher
and its default port 8787 are unchanged.

The entry rejects a nonempty directory that does not belong to this assistant profile.
An OS lock prevents two assistant launchers using the same directory and releases when
the process exits. Do not point other launchers at this directory. No existing research
database, account or key is imported. `--data-dir` and `--port` select a separate instance.

`--offline` uses explicitly labelled MockLLM chat responses, not a general offline model.
It does not interpret requests or prove real-model task success. Remove it and configure
the model in Connections for live chat. Research fixture/live projects retain their own
mode; offline chat does not turn live research projects into fixtures. Do not create live
research projects when testing without provider calls.

## Capability inventory

Connections includes a capability inventory. The authenticated
`GET /api/assistant/capabilities` endpoint reports model configuration, registered tools,
disabled capabilities and missing Playwright dependencies. It makes no provider requests,
takes no screenshots and does not operate the desktop. It returns no keys, tokens or
workspace paths. `--check --offline` prints the same inventory without starting a server.

Registered tools are not verified executions. A configured model is not a successful
connection test. Browser binaries, desktop permissions, focus and a working vision model
require separate checks. In particular, a text model alone does not prove GUI capability.

Browser and computer control are off on a fresh profile until configured. Existing local
choices are retained on restart. External mail, calendar, contacts, Hub, MCP and skills are
disabled by this development entry; extra filesystem roots are cleared and the Sentinel
mode is `ask`. Embedding requests are off. The workspace is scoped to this data directory.

## Verification and limits

MockLLM integration exercises authenticated chat, file output and a persisted reminder
with the research endpoint present. A separate process checks the directory lock.
Existing research regression tests remain applicable to that module, not evidence that
the entire personal-assistant plan is complete. Reminder editing, GUI recovery and the
new personal-task evaluation matrix remain subsequent implementation work.

## Task ledger

Open **Task ledger** from the sidebar or tab bar. Give a task a title, success criteria,
an optional existing research project, and one checkpoint per line. Research steps run
through the existing research orchestrator; checkpoints wait for the user to enter
completion evidence. A checkpoint is not an automatically executed file or desktop task.
The main chat can create and inspect the same ledger through `assistant_tasks`, subject
to Sentinel. The model cannot confirm a checkpoint on the user's behalf through that tool.

The detail view shows progress, plan history and pause, resume and cancel controls. A new
plan replaces the remaining work using the current form. Completed steps and their evidence
remain in history; including them in a replacement plan would request new work. Changing a
plan while a step is running or bound to research is rejected. Pause takes effect at the
next coordinator pass, and the domain worker fences results from cancelled or paused work.
Cancel waits for outstanding dispatch intents to reconcile before reporting cancellation.
Cancelling a task does not undo work already completed.

The existing goals and reminders section reads the original stores. It does not create
duplicate tasks or schedule owners. A reminder being marked fired means the reminder was
dispatched, not that an autonomous task requested by it succeeded. Legacy goal and reminder
execution has not been migrated to ledger leases in this stage.

## Scheduling and recovery

Tasks may have a future start time. To create a recurring plan, specify the first local
start time and an interval in minutes. The browser converts the local instant to UTC;
repeats use elapsed seconds, so a daylight-saving change can change the displayed local
hour. Use the existing reminder module for daily wall-clock reminders. The runtime must
be running; after downtime, missed occurrences merge into one. An unfinished occurrence
blocks new occurrences. Stopping a schedule stops future occurrences, not its current task.

Only checkpoint and research adapters are accepted. There is no automatic replay of shell,
mail, browser submissions or GUI operations. Research tracking and assistant schedules
cannot both own the same project's periodic trigger. Disabling one permits enabling the
other. An assistant plan pins research requirement and input versions; changed inputs
require reviewing a new plan instead of silently dispatching under new conditions.

The SQLite ledger lives at `assistant/tasks.sqlite3` inside the selected data directory.
Schema version 1 is additive and rejects newer, unknown versions. Creation request IDs
deduplicate retries. `BEGIN IMMEDIATE` serializes claims and schedule creation across
connections. Each attempt has a fresh token and a finite lease. Expired attempts and
previous plan revisions cannot commit. Plan history, results and transition events are
durable, and no application or provider secret is copied into this database by the adapter.

The durable step acts as a dispatch outbox. Its `assistant:<step-id>` key binds exactly one
research run through the research store's idempotent enqueue. After a crash between the two
database commits, reconciliation finds the same run. Result ingestion changes a bound step
once and records the research run, domain status and report IDs with content hashes. These
are result references, not copies of the research database. External operations are not
promised exactly-once delivery. Research keeps its existing tool subset, Sentinel and model
limits; this stage does not introduce a new currency budget or a separate autonomous model.

## HTTP contract: assistant-tasks-v1

All routes require the host bearer authentication. Times are finite UTC Unix seconds.
Missing records return 404, incompatible state or revision returns 409, and malformed input
returns 400 or 422. Existing research routes remain compatible.

| Method and path | Input or result |
| --- | --- |
| `GET /api/assistant/tasks` | Recent task summaries, newest first, up to 500 |
| `POST /api/assistant/tasks` | `request_id`, `plan`, optional `due_at` |
| `GET /api/assistant/tasks/{id}` | Task, immutable plans, all steps and events |
| `POST /api/assistant/tasks/{id}/plan-revisions` | `request_id`, `expected_revision`, `plan`, `reason` |
| `POST /api/assistant/tasks/{id}/actions` | `request_id`, `expected_revision`, `action`: pause, resume, cancel |
| `POST /api/assistant/tasks/{id}/steps/{step}/confirm` | `request_id`, `expected_revision`, user `evidence` |
| `GET /api/assistant/tasks/{id}/artifacts` | Versioned research report references and hashes |
| `GET /api/assistant/module-bindings` | Read-through goals and reminders, original owner |
| `GET /api/assistant/schedules` | Recurring plans, next trigger, enabled state |
| `POST /api/assistant/schedules` | Creation fields plus `interval_seconds`, at least 60 |
| `DELETE /api/assistant/schedules/{id}` | Disable future occurrences, retain history |

A plan contains `title`, `success_criteria`, and 1 to 30 ordered `steps`. Each step has
`adapter` and `title`; research also has `project_id` and `kind` (`research` or `check`).
The server records its `spec_id` and `input_version`. Revisions, controls and confirmations require a stable `request_id` and expected plan
revision. Retrying identical inputs reuses the committed mutation and returns current task
state. Reusing that ID with different inputs returns 409. Receipts commit in the same
transaction as state changes. API clients should distinguish user-supplied
checkpoint evidence from independently verified research results.

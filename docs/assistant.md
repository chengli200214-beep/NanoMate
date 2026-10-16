# Personal assistant development entry

The personal assistant opens at chat and retains the research workspace. Its task ledger
coordinates research, user-confirmed checkpoints, reminder changes, local document artifacts
and confirmed personal preferences. Existing goals and reminders keep their original
scheduler. Personal actions run through the host Sentinel in one serial asynchronous worker.
Browser and desktop execution recovery remains a separate implementation stage.

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
Focused tests cover reminder revisions and replay, source changes and artifact verification,
preference consent and memory projection, and cancellation during approval waits. These
checks use local data and MockLLM, with no paid provider calls. Existing research regression
tests remain applicable to that module. GUI recovery and real-model personal-task acceptance
still require separate verification; these checks do not establish that the entire
personal-assistant plan is complete.

## Task ledger

Open **Task ledger** from the sidebar or tab bar. **Personal task** creates a single reminder,
document or preference step. **Compose a plan** creates checkpoints with an optional existing
research project. Research steps run through the existing research orchestrator; checkpoints
wait for the user to enter completion evidence. A checkpoint is not an automatically executed
file or desktop task. The main chat can create and inspect the same ledger through
`assistant_tasks`, subject to Sentinel. The model cannot confirm a checkpoint or authorize a
personal preference on the user's behalf through that tool.

The detail view shows progress, plan history and pause, resume and cancel controls. A new
plan replaces the remaining work using the current form. Completed steps and their evidence
remain in history; including them in a replacement plan would request new work. Changing a
plan while a step is running or bound to an executor is rejected. Personal actions wait for
Sentinel approval when required. After an approval wait, the worker checks the current task
state and preference consent again before writing. Pause and cancel interrupt a waiting
worker; cancellation reconciles existing domain receipts before reporting whether an action
already committed. Cancelling a task does not undo work already completed.

The existing goals and reminders section reads the original stores. It does not create
duplicate tasks or schedule owners. A reminder being marked fired records a dispatch claim,
not successful completion of its requested work. A crash between that claim and chat enqueue
can still lose delivery; the ledger does not promise crash-proof reminder delivery. Legacy
goal and reminder execution has not been migrated to ledger leases.

## Reminder changes

Choose **Reminder change**, then create, update or cancel. An update or cancellation selects
an existing active reminder and records its current `revision`. Updating text or timing keeps
the same reminder ID; a successful change advances its revision. Stale revisions are rejected
before changing the record. **Keep current time** edits text without moving the next trigger.
An explicit future local time becomes an ISO UTC instant in the browser. Repeat rules such
as `daily 08:00` or `weekly mon 09:00` use the runtime's local timezone.

The reminder store commits each change and its request receipt together. Retrying the same
task step reuses that receipt, including after a restart between the domain and ledger
commits. The existing due-reminder scheduler remains the only delivery owner, and checks
the reminder revision and scheduled time before claiming an occurrence. The task result
verifies the reminder configuration; its completion does not claim that a future reminder
was delivered or that the requested work succeeded.

## Document artifacts

Choose **Document artifact** and give explicit workspace-relative source paths, one per line.
Sources must be UTF-8 `.md` or `.txt` files: at most 10 files, 256 KiB per file and 1 MiB in
total. Absolute paths, traversal, symbolic links and junctions are rejected. The server
freezes source SHA-256 hashes when validating the plan; changed sources require reviewing a
new plan before execution.

The result is a deterministic Markdown extractive digest, containing the first 4000
characters from each source and optional user notes. It labels omitted text and includes
source hashes. It does not call a model, synthesize a semantic summary or treat source text
as instructions. The output filename must be a `.md` basename. Files are published beneath
`assistant-artifacts/<artifact-id>/` in the workspace, without replacing an existing file.

A durable publication intent stores the approved bytes before atomic publication. Recovery
can verify a file published before a crash and complete its receipt. **Task outputs** shows
the file hash and verification state; **Read verified output** reads the file only when its
actual bytes match the receipt. A completed receipt does not hide a missing or modified
file, and replay does not silently recreate a completed artifact that was removed.

## Personal preferences

Choose **Personal preference** to save, replace or delete a preference. A stable key, value,
optional purpose and optional expiry are recorded with the source task, plan revision, step
and confirmation time. Long-term memory must be enabled. **Confirm this personal preference**
explicitly authorizes the submitted preference when creating the task. A candidate created
through the model tool waits for the user's **Confirm preference change** action in the task
detail. Source documents and model output cannot grant this consent. Consent belongs to one
immutable step and plan revision; a replacement plan needs fresh confirmation.

Confirmed active preferences are projected into the existing memory store for retrieval.
They are context for relevant tasks; the user's current request takes priority. Replacements
and deletions require the current preference revision. Deletion or expiry removes the memory
projection, and replaying an earlier receipt cannot restore an old value. Removing the owned
preference memory also prevents recovery from restoring it.

Deletion is not full history erasure. The preference store clears the active value and
purpose, and receipts contain no value, but immutable task plans still retain the originally
submitted preference. The task ledger and its plan history remain inspectable.

## Scheduling and recovery

Tasks may have a future start time. To create a recurring plan, specify the first local
start time and an interval in minutes. The browser converts the local instant to UTC;
repeats use elapsed seconds, so a daylight-saving change can change the displayed local
hour. Use the existing reminder module for daily wall-clock reminders. The runtime must
be running; after downtime, missed occurrences merge into one. An unfinished occurrence
blocks new occurrences. Stopping a schedule stops future occurrences, not its current task.

Recurring assistant plans accept only checkpoint and research adapters. Reminder, document
and preference steps are one-off task commands; repeating reminders use their own `repeat`
rule. There is no automatic replay of shell, mail, browser submissions or GUI operations.
Research tracking and assistant schedules cannot both own the same project's periodic
trigger. Disabling one permits enabling the
other. An assistant plan pins research requirement and input versions; changed inputs
require reviewing a new plan instead of silently dispatching under new conditions.

The SQLite ledger lives at `assistant/tasks.sqlite3` inside the selected data directory.
Schema version 1 is additive and rejects newer, unknown versions. Creation request IDs
deduplicate retries. `BEGIN IMMEDIATE` serializes claims and schedule creation across
connections. Each attempt has a fresh token and a finite lease. Expired attempts and
previous plan revisions cannot commit. Plan history, results and transition events are
durable. Submitted task text, source paths, notes and preference values are part of plan
history; the adapter does not copy application or provider configuration secrets.

The durable step acts as a dispatch outbox. Its `assistant:<step-id>` key binds exactly one
research run through the research store's idempotent enqueue. After a crash between the two
database commits, reconciliation finds the same run. Result ingestion changes a bound step
once and records the research run, domain status and report IDs with content hashes. These
are result references, not copies of the research database. Personal steps use the same
stable key for their domain receipts. Document artifacts and preferences have separate stores
under the selected data directory's `assistant/`; cross-store recovery reconciles receipts
rather than claiming a single transaction across databases. External operations are not
promised exactly-once delivery. Research keeps its existing tool subset, Sentinel and model
limits. Personal-domain adapters do not start a separate autonomous model loop.

## HTTP contract: assistant-tasks-v1

All routes require the host bearer authentication. Ledger scheduling and preference expiry
use finite UTC Unix seconds; reminder `at` uses an ISO timestamp with an offset. Missing task
or module IDs return 404, incompatible state or revision returns 409, and malformed input
returns 400 or 422. Artifact verification failures return 400. Existing research routes
remain compatible.

| Method and path | Input or result |
| --- | --- |
| `GET /api/assistant/tasks` | Recent task summaries, newest first, up to 500 |
| `POST /api/assistant/tasks` | `request_id`, `plan`, optional `due_at`, optional `confirm_preferences` (default false) |
| `GET /api/assistant/tasks/{id}` | Task, immutable plans, all steps and events |
| `POST /api/assistant/tasks/{id}/plan-revisions` | `request_id`, `expected_revision`, `plan`, `reason` |
| `POST /api/assistant/tasks/{id}/actions` | `request_id`, `expected_revision`, `action`: pause, resume, cancel |
| `POST /api/assistant/tasks/{id}/steps/{step}/confirm` | `request_id`, `expected_revision`, user `evidence` |
| `POST /api/assistant/tasks/{id}/steps/{step}/authorize-preference` | User confirmation: `request_id`, `expected_revision` |
| `GET /api/assistant/tasks/{id}/artifacts` | Versioned research report references and document artifacts with hashes and verification state |
| `GET /api/assistant/artifacts/{id}` | Verified document metadata and current file content |
| `GET /api/assistant/preferences` | Preference records, revisions, provenance and active, deleted or expired status |
| `GET /api/assistant/module-bindings` | Read-through goals and reminders, original owner |
| `GET /api/assistant/schedules` | Recurring plans, next trigger, enabled state |
| `POST /api/assistant/schedules` | Creation fields plus `interval_seconds`, at least 60 |
| `DELETE /api/assistant/schedules/{id}` | Disable future occurrences, retain history |

A plan contains `title`, `success_criteria`, and 1 to 30 ordered `steps`. Each step has
`adapter` and `title`; research also has `project_id` and `kind` (`research` or `check`).
The server records its `spec_id` and `input_version`. A personal step supplies exactly the
matching nested payload:

| Adapter | Nested payload |
| --- | --- |
| `reminder` | `reminder`: action `create`, `update` or `cancel`; text and either ISO `at` or `repeat` when needed; edits require `reminder_id` and `expected_revision` |
| `document` | `document`: `source_paths`, optional `output_name` (default `digest.md`) and `notes`; the server prepares `source_hashes` |
| `preference` | `preference`: action `set` or `delete`, `key`, `expected_revision`; sets require `value` and may include `purpose` and UTC Unix `expires_at` |

New preference keys use `expected_revision: 0`. Deletions carry no value, purpose or expiry.
`confirm_preferences: true` is an authenticated user's explicit consent for the preference
steps in the submitted creation plan; the model tool does not expose that flag or the
authorization endpoint. Revision requests do not inherit earlier consent.

Revisions, controls and confirmations require a stable `request_id` and expected plan
revision. Retrying identical inputs reuses the committed mutation and returns current task
state. Reusing that ID with different inputs returns 409. Receipts commit in the same
transaction as state changes. API clients should distinguish user-supplied
checkpoint evidence, reminder configuration receipts, verified artifact bytes, confirmed
preference projections and independently verified research results.

# Personal assistant development entry

The personal assistant opens at chat and retains the research workspace. Its task ledger
coordinates research, user-confirmed checkpoints, reminder changes, local document artifacts,
confirmed personal preferences, workspace files, browser actions and bounded Windows native
actions. Existing goals and reminders keep their original scheduler. Personal actions run
through the host Sentinel in one serial asynchronous worker.

## Separate data and process

Use a separate checkout while a research recovery observation is pending. Do not reinstall
an editable package over that observation's environment or edit its runtime and database.
From the assistant checkout, using its configured Python dependencies:

```powershell
python scripts/assistant_dev.py --offline
```

For a separate Windows Python 3.13 environment, runtime, development, browser and hands
dependencies are hash-locked in `requirements-assistant-windows.txt`:

```powershell
uv venv .venv-assistant --python 3.13
uv pip sync requirements-assistant-windows.txt --python .venv-assistant/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .venv-assistant/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
.venv-assistant/Scripts/python.exe -m playwright install chromium
.venv-assistant/Scripts/python.exe scripts/assistant_dev.py --offline
```

The lock is Windows-specific. Keep this environment separate from any pending recovery
observation and do not enable system site packages. Chromium is a separate installation.

The default address is `http://127.0.0.1:8788/?tab=chat`. Open the private shortcut
`.local/assistant-dev/open-assistant.url` to pair the browser. The shortcut contains a local
token and must not be shared. Authentication stays enabled. The research-only launcher
and its default port 8787 are unchanged.

Local channel CLI commands contact the loopback service directly, without environment
proxies. Their local authorization token stays on that connection.

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
preference consent and memory projection, cancellation during approval waits, file publication
receipts and browser/native observation checks. These
checks use local data and MockLLM, with no paid provider calls. Existing research regression
tests remain applicable to that module. Passing driver tests does not establish real Windows
editing and saving, or real-model personal-task acceptance; these require separate checks.
These checks do not establish that the entire
personal-assistant plan is complete.

## Task ledger

Open **Task ledger** from the sidebar or tab bar. **Personal task** creates a single reminder,
document or preference step. **Compose a plan** creates checkpoints with an optional existing
research project. Research steps run through the existing research orchestrator; checkpoints
wait for the user to enter completion evidence. A checkpoint is not an automatically executed
file or desktop task. The main chat can create and inspect the same ledger through
`assistant_tasks`, subject to Sentinel. **File, browser and Windows tasks** creates execution
previews. The model cannot confirm a checkpoint, authorize a personal preference or approve
an execution preview on the user's behalf through that tool.

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

## Execution previews and human control

Create an execution preview, inspect **Action preview and result** in the task detail, then
choose **Approve this exact action**. Approval belongs to that immutable step and plan
revision. Each new execution step needs its own approval; Sentinel can request an additional
permission for its actual risk and target. A plan or a model response is not approval.
The worker checks task state again after approval, before input or publication.

**Pause** stops further work and cancels an approval wait. **Take over manually** also reserves
the browser and desktop session for the user, blocking automatic input from other tasks and
chat until the user resumes or cancels that task through its task controls. Manual ownership
survives a process restart. Only the authenticated user control endpoint can return it;
the model's `assistant_tasks` tool cannot release it by resuming or cancelling the task.
Cancellation does not undo an action already received by an application. A task uses the
shared browser and desktop session serially; it does not run another autonomous model loop.

Once a paused execution worker has stopped, the runtime settles its bound step by inspecting
existing receipts and effects without sending application input or repeating file operations.
The task stays paused and any manual session ownership remains with the user. A verified
receipt can retain the completed effect; an uncertain intent remains `outcome_unknown`, while
a step stopped before execution needs a new preview. The user can observe the current state
and revise the remaining plan before returning control through the task controls. Returning
control is separate from approving a new action. For browser steps, a fresh observation
obtained during manual control can remain valid after the user resumes; the action still
checks its expiry and compares the actual page state before input. For native steps, a
manual observation permits inspection but does not restore the opened-document binding.
After returning control, open the workspace file again with a new `open_app` step and use
its fresh observation for subsequent input.

Pause, takeover and process restart invalidate saved observation tokens. Observe the
current state again before preparing a new interaction. Old coordinates, element numbers and
focus information are not replay permissions. If a previous browser/native intent exists
without a completed receipt, the step becomes `outcome_unknown`. Inspect the application,
observe its present state and replace the remaining plan with a reviewed revision. Resuming
does not automatically repeat the unknown input, and checkpoint evidence cannot certify it.
Cancellation also preserves an `outcome_unknown` step and leaves the task requiring attention,
rather than relabelling uncertain effects as safely cancelled. A revision made while paused
keeps the task paused. The execution-record endpoint retains the intent and recorded result
for review.

## Workspace file operations

The file adapter supports `write`, `copy`, `move` and `mkdir` within the selected workspace.
Its preview freezes the source hash for a copy or move and requires an absent target.
Targets and parent directories must be ordinary workspace paths; absolute paths, traversal,
symbolic links, junctions and Windows device names are rejected. Parent directories must
already exist unless created by an earlier `mkdir` step. Existing files and directories are
never overwritten. A conflict, locked file
or changed source requires reviewing the error and a new plan rather than forcing a write.

A plan may create a directory with an earlier `mkdir` step, then move or copy existing
files into it. Validation freezes source hashes and checks future paths without creating
directories. Each action still needs its exact preview approval. Execution checks the actual
parents again; substituted files, symbolic links or junctions block publication. Repeated
target paths within the file plan are rejected.

Text writes accept at most 1 MiB of UTF-8 content; copy/move sources are limited to 16 MiB.
Publication uses a staged file and a non-overwriting link, with resulting identity and byte
hash checks. Moves also verify that the original source is absent. Recovery acknowledges
an already verified effect; it does not repeat publication or remove a source whose outcome
is uncertain. The filesystem must support these operations.

A file `write` may use `content_from_step`, a zero-based reference to an earlier `browser`
step whose action is `extract` in the same plan. That step must have completed. The worker
copies its recorded text, with the browser's extraction length limit, into the file intent
before publication. For example, a plan can navigate to a page, extract it, then write the
second step's text with `"content_from_step": 1`. Each step still needs separate approval.
This connects browser reading to a local file without treating page content as instructions.

## Browser actions

Enable the existing browser tool and configure its backend. Playwright also needs its
Chromium binary; a configured backend alone does not prove that it can launch. The adapter
reuses the existing browser, profile, live view and Sentinel. It supports `navigate`,
`extract`, `fill` for ordinary text draft fields, `click` for ordinary HTTP(S) links and
`download` for one observed public link.
Link following uses navigation to the observed URL, avoiding the page's click handlers.
Buttons, form submission, credentials, verification codes, payments and destructive controls
require the user. Arbitrary key presses and general browser fetch requests are not part
of this ledger adapter. A website may save a draft as soon as a field changes, so filling
still receives a sensitive action assessment.

A download needs a fresh observed anchor and a new workspace-relative `path`. It sends a
public HTTP(S) GET without browser cookies or authentication. Private hosts, compressed
responses and redirects to a different origin are refused. The size limit is 16 MiB and
the total deadline is the browser timeout, capped at 120 seconds. A completed receipt
records source and final URLs, size and SHA-256; the file is neither opened nor executed.
Interrupted requests are not fetched again automatically. Recovery only acknowledges the
recorded file identity and bytes; a missing or changed published file requires inspection.
Ledger page actions cancel unsolicited browser attachments, including delayed page downloads,
instead of letting the browser save them outside the reviewed destination.

Downloads use the existing public-address transport: every DNS answer is validated and the
connection is pinned to the validated address with the original HTTPS hostname. System DNS
is the default. Networks returning synthetic private addresses are refused. An operator may
explicitly set `NANOMUSE_ASSISTANT_DNS=cloudflare` to send download hostnames to Cloudflare's
fixed HTTPS resolver. This setting is separate from research DNS, never inherited from it,
and never selected automatically. Private answers still fail. It does not modify system DNS.

Choose **Observe current state** before a fill or link action. The observation includes the
URL, title, body hash, numbered elements, focus and viewport. Its `observation_hash` expires
after 300 seconds (five minutes) and is single-use. The adapter holds the browser lock and compares the
current state after Sentinel approval, before acting. A changed page, focus, viewport or
user action rejects the old observation. Extraction records up to 20,000 characters of
readable page text; pages exceeding the complete observation limit of 200,000 characters
are rejected. Observation during manual control can inspect the page without permitting
input. A result verifies the observed page or draft field, not a remote transaction.

## Windows native actions

The native adapter supports Notepad and File Explorer on Windows with computer use enabled,
the `hands` dependencies installed and an unlocked interactive desktop. It records a fresh
screenshot hash, foreground window, keyboard focus, application, display size and scale.
Inputs and save verification need a single-use `expected_observation`, valid for 300 seconds
(five minutes). A fixed `open_app` launch can start without one. It returns a new observation
when it verifies two consecutive stable frames of the target application; otherwise it
records `observation_unavailable` and requires a fresh observation. Choose
**Use the selected task's latest observation** to prepare the next action with a valid token.
The adapter can restore only the exact observed window after a web approval; it checks the
window, focus, display and content again. A closed or minimized window and changed content
require another observation. App permission still uses the existing
per-application gate. Native dialogs require manual control.

Supported actions are `observe`, `open_app`, bounded `type`, allowed `key` chords, labelled
`click` coordinates and `verify_saved_file`. Notepad opens an existing workspace `.txt` or
`.md` file; File Explorer requires an explicit workspace-relative directory `path` when
opening. An empty File Explorer path is rejected. Typing is limited to the opened
Notepad document. Key chords are restricted by the adapter, and neither arbitrary programs
nor shell commands are accepted. A native launch or input result records dispatch only.
It does not prove that the application opened the intended document, that text was saved,
or that the user's overall task completed.

Windows text input sends literal UTF-16 events through `SendInput` with
`KEYEVENTF_UNICODE`, including ASCII text and spaces. It leaves the selected IME and all
clipboard formats untouched and runs the existing corner safety check before dispatch.
Held modifiers stop input; a partial insertion is reported as uncertain and is not retried.

Save verification is a separate action. It requires a GUI save dispatched for the opened
workspace file and a matching `expected_sha256` of the actual file bytes. Writing that file
through the file adapter is not proof of a GUI save. Pause, takeover or restart clears the
in-memory open/save context. An observation taken during manual control cannot restore that
context: after the user returns control, reopen the workspace path through `open_app` before
a new edit/save sequence. A matching window title alone cannot establish the file path,
including when Notepad has multiple tabs. Automated
driver tests and file hash verification do not replace real-machine editing and save
acceptance. Text input checks pause, cancellation and target focus between blocks of at most
eight characters. An already dispatched block or atomic click may finish; cancellation does
not undo it. An input-context check travels with the worker thread, so cancelling the async
task also stops its next input block. Recognized Notepad editing controls use their content area fingerprint, and
clicks stay within that verified area. Other controls retain the full client-area check.
Immediately before a click, `WindowFromPoint` must resolve to the exact observed Notepad
editing control, or the observed application window or a real child with the same root window
and process; an overlay intercepting that point rejects the click and requires observation again.

For recognized Notepad Edit/RichEdit controls whose text can be read safely within 65,535
UTF-16 code units, the observation also binds the text hash and logical selection. Focus
restoration may repaint the caret, and remote desktop captures may include the mouse
pointer. These redraws are accepted only when text, selection, window, focus and display
state are unchanged and every changed pixel lies inside the exact bounded caret or pointer
rectangles reported by Windows in the two frames. Unknown or oversized overlay regions
receive no exception, and other occlusions or content changes reject the observation.
If bounded text reading is unavailable or the document is larger, the strict fingerprint
check remains.

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

Recurring assistant plans accept only checkpoint and research adapters. Reminder, document,
preference, file, browser and native steps are one-off task commands; repeating reminders use their own `repeat`
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

## Research from the main chat

The main agent's `assistant_tasks` tool supports `research_list`, `research_get`,
`research_create` and `research_revise`. Creation requires an explicit fixture or live
mode and the existing research specification. Ordinary personal tasks do not need repository
candidates. Live research retains its source permissions, Sentinel, tool subset and limits.

Creation links one project, run and task with a stable request key. The run stays paused
until the coordinator binds the task. Retrying identical inputs or recovering between the
two database commits reuses those records. Changing conditions requires the current
`expected_spec_id`; it keeps the project ID, source settings and report history, and creates
a follow-up task for the new conditions. The original task remains inspectable.

Report and important-change notices return to the main chat with a project link. Stable IDs
are persisted before the module's outbox is acknowledged, so repeated delivery does not
create a second stored bubble. This local delivery starts no model turn and sends no mail.
Report text remains untrusted source material. Existing feedback and mute rules apply.
Existing projects are backed up before additive link metadata is introduced; their historical
notices are not replayed into chat.

Evidence checks reject contradictory concrete file paths and explicit snapshot-text line
ranges. Normalized HTML lines are not claimed as original source-file lines. Reports expose
`semantic_review` metadata and a review notice: claim entailment, evidence for both sides of
a comparison, and assertions about absent capabilities still require semantic review.
These markers do not certify claims as true. Legacy reports remain readable.

## Preference scope and civil time

Confirmed preferences with a nonempty `purpose` enter prompt context only when the current
text shares meaningful terms with their purpose or key. Empty purpose remains global.
This is a deterministic lexical filter, not a semantic guarantee. Ordinary memories retain
their original retrieval behavior. Deleted, expired or malformed preference projections
cannot return through a stale asynchronous index result.

Reminder cadence uses the operating system's local civil time. A nonexistent local time
during a clock change is skipped; a repeated local time uses its first occurrence only.
Future dates use that date's local offset. Explicit aware-zone scheduling helpers preserve
the supplied zone. There is no new per-reminder IANA time-zone selector. Elapsed-time ledger
schedules keep their existing interval semantics.

## HTTP contract: assistant-tasks-v1

All routes require the host bearer authentication. Ledger scheduling and preference expiry
use finite UTC Unix seconds; reminder `at` uses an ISO timestamp with an offset. Missing task
or module IDs return 404, incompatible state or revision returns 409, and malformed input
returns 400 or 422. Artifact verification failures return 400. Existing research routes
remain compatible.

This is an additive extension of `assistant-tasks-v1`, with ledger schema version 1 retained.
Old plans remain valid: absent `file`, `browser` and `native` payloads default to null.
Existing routes and fields keep their meanings. Clients should tolerate new adapter names
and additional result/event fields, and display an unknown action as inspectable data rather
than assume it is a completed checkpoint. Execution approval is separate from preference
consent and checkpoint evidence.

| Method and path | Input or result |
| --- | --- |
| `GET /api/assistant/tasks` | Recent task summaries, newest first, up to 500 |
| `POST /api/assistant/tasks` | `request_id`, `plan`, optional `due_at`, optional `confirm_preferences` (default false) |
| `GET /api/assistant/tasks/{id}` | Task, immutable plans, all steps and events |
| `POST /api/assistant/tasks/{id}/plan-revisions` | `request_id`, `expected_revision`, `plan`, `reason` |
| `POST /api/assistant/tasks/{id}/actions` | User controls: `request_id`, `expected_revision`, `action`: pause, resume, cancel; resume/cancel also returns manual session control |
| `POST /api/assistant/tasks/{id}/steps/{step}/confirm` | `request_id`, `expected_revision`, user `evidence` |
| `POST /api/assistant/tasks/{id}/steps/{step}/authorize-preference` | User confirmation: `request_id`, `expected_revision` |
| `POST /api/assistant/tasks/{id}/steps/{step}/authorize-action` | User approval of the next exact execution preview: `request_id`, `expected_revision` |
| `GET /api/assistant/tasks/{id}/actions` | Durable execution intents, status and recorded results |
| `POST /api/assistant/tasks/{id}/take-over` | Pause and reserve manual session control: `request_id`, `expected_revision` |
| `POST /api/assistant/execution/observe` | `surface`: `browser` or `native`; current observation, without input |
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
| `file` | `file`: `action` (`write`, `copy`, `move`, `mkdir`), `path`; copies/moves require `source_path`; writes accept `content` or `content_from_step`; the server freezes `source_sha256` and `target_absent` |
| `browser` | `browser`: `action` (`navigate`, `extract`, `fill`, `click`, `download`); navigation needs `url`; interactions need `index`, `expected_url`, `observation_hash`; filling also needs `text`; downloads require `path` and the server freezes `target_absent` |
| `native` | `native`: `action`, `app` (`notepad`, `explorer`), and action-specific fields; inputs and save checks require `expected_observation`, while `observe` and `open_app` do not; opening accepts `path` (required for `explorer`), typing `text`, key actions `keys`, clicks `x`, `y`, `label`, and save checks `saved_file`, `expected_sha256` |

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
Browser/native action records distinguish observed state, input dispatch and verified save
bytes. A `started` intent without a completed result remains uncertain; clients must not
retry it as an idempotent external command. New execution approvals cannot be given while
the task is paused or to a superseded revision.

## Model tool boundary

The isolated assistant profile enables `agent.task_ledger_only`. Its model receives a
filtered tool view: workspace files are read-only, reminder access is listing only, and
shell, Python, raw browser/desktop input and direct memory mutation tools are unavailable.
File, browser and native changes use `assistant_tasks` plans and the user's execution
preview approvals. Reminder changes use ledger steps; confirmed preferences use their
consent flow. Unknown outcomes require observation and a revised plan, not another raw tool.
The underlying drivers remain available to the deterministic executor. Other runtime
profiles default to their existing tools; the research executor keeps its separate subset.
Forwarded research source text is excluded from the ideas and feed's recent assistant context.

## Acceptance preparation

The public `evals/assistant/manifest.json` and `scoring.json` describe 24 cases and the
specified repeat layout. They are acceptance inputs, not completed results. Preparing a
review bundle through `python -m evals.assistant` makes no provider call or approval.
Code, inputs, model and dependency configuration must be frozen before a new paid batch.
The evaluation guard shares durable reservations across main chat and research, stops on
unknown usage and refuses old research approval receipts. Execution and manual-review
limits are documented in `evals/assistant/README.md`. This guard does not establish a
production-wide daily spending allowance.

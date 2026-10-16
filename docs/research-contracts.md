# Research contracts v1

The local development plan owns the product scope. `nanomuse/research/contracts.py` owns
the shared types. Sources and reasoners implement its protocols. State uses SQLite and
JSON dictionaries at the service boundary. All project records are scoped by project ID.

The orchestrator owns collect, extract, compare and publish stages. The comparison is a
structured value, not an executable plan. Public reads are source-ID based. No shell,
mail, arbitrary file or browser tool belongs to the research agent.

## REST wire format

All routes use the existing server's Bearer authentication. Prefix: `/api/research`.
Lists return JSON arrays. Objects and action results return JSON objects. Errors use
`detail`. Identifiers are strings.

- GET and POST `/projects`: POST accepts ProjectSpec and returns a project.
- GET `/projects/{id}`: project with `spec`, `sources`, `runs`, `current_report`.
- POST `/projects/{id}/revisions`: a complete ProjectSpec, returns a revision.
- POST `/projects/{id}/sources`: SourceInput, returns a source.
- POST `/projects/{id}/runs`: `{kind: research|check, request_key?: string}`.
- GET `/runs/{id}`: run with `steps`.
- POST `/runs/{id}/control`: `{action: pause|resume|cancel|retry}`.
- GET `/projects/{id}/evidence`: array of evidence, optional candidate/dimension/status.
- GET `/projects/{id}/conflicts`: conflict array, with both evidence IDs and bound versions.
- POST `/evidence/{id}/correction`: `{fact, quote?, reason}`.
- POST `/evidence/{id}/reverify`: schedules a scoped research run.
- GET `/projects/{id}/reports`: report array with markdown.
- GET `/reports/{id}/diff?previous=...`: `{before, after, diff}`.
- GET `/reports/{id}/export`: Markdown response.
- PUT `/projects/{id}/tracking`: `{enabled, interval_minutes, timezone, daily_time, max_calls}`.
- GET `/projects/{id}/changes`: `{events, notifications, mute_rules}`.
  An event may include `model_judgment` and `notification_policy`. If an uncertain
  source update contributes current evidence to an unresolved hard-requirement conflict,
  the attention policy sets the applied `impact_level` to `recommendation` and retains
  the original model judgment. `notification_policy` records its id, constraint refs and
  evidence refs. Comparison verdicts stay unknown; recommendation-level muting and event
  deduplication still apply. These fields are optional, so older clients can ignore them.
- POST `/changes/{id}/action`: `{action: accept|useless|mute|unmute}`.
- POST `/notifications/{id}/feedback`: `{feedback: useful|useless|read}`.
- GET `/projects/{id}/diagnostics`: `{runs, steps, calls, events}`.

Project: id, title, spec_id, created_at, tracking, mode. Evidence: id, project_id,
candidate, dimension, fact, quote, snapshot_id, locator, verification, status, source_url,
version, fetched_at. Report: id, project_id, spec_id, markdown, created_at, stale.
Step: id, run_id, stage, status, attempt, error, output. Run: id, project_id, kind,
spec_id, status, created_at, error, steps. Notification: id, content, feedback, created_at.

Fixture mode is explicitly labelled. It uses public-source snapshots and scripted model
responses, and is never reported as a live model verification.

SQLite schema version 2 adds run input versions. Opening an existing v1 research
database adds the field with default 1. New input changes increment the project version
and fence previous runs. Step commits check owner, attempt, lease, input hash, input
version and current specification. Snapshots, specifications and reports are immutable.
Model requests reserve a stable call ID before network I/O; completed usage updates
the same record. A reclaimed running request becomes unknown and still consumes budget.

## Review repairs

`Comparison.recommended_candidate` is an optional candidate ID or null. It defaults to
null for older records. With hard requirements, only a candidate whose every hard verdict
is meets can be selected. The validator and renderer derive recommendation text from this
field; unbound or ineligible selections abstain. A definitive hard verdict needs direct
evidence whose dimension equals each referenced hard-condition ID. These are structural
checks; quote entailment and compound factual claims still require semantic review.
Recommendation and hard-condition tables use the same aggregate verdict. A preference
alone stays unknown; a supplementary preference does not erase a direct meets verdict.
Any unknown or failing verdict still prevents selection for that requirement.

Report revisions may add `origin`, `derived_from_report_id` and
`human_semantic_review_completed`. An `agent_editorial_correction` is explicitly labelled
in its Markdown and retains the raw model report. It is excluded from raw model scoring.

Unchanged cleaned source content retains its original immutable snapshot, URL and version.
A ref moving to another commit updates the source's `observed_version`, `observed_url`,
ETag and check time. It does not invalidate evidence or call the model. Metadata excludes
stars, forks, issue counters and push time. HTML article headers remain readable; explicit
hidden content and update decoration are removed. Ref resolution requests only GitHub's
SHA representation, documented in the [GitHub commit API](https://docs.github.com/en/rest/commits/commits#get-a-commit).

WebSocket research frames add `kind: research`, `project_id`, `run_id`, `stage` and a durable
per-project `sequence`; the older `type: research` remains for compatibility. Frames are
invalidation hints, not state patches. Clients fetch the current snapshot after a frame or
reconnection. The workspace also polls every two seconds during runs and every five seconds
while idle. Project detail adds optional `tracking_blocked` for paused or attention runs.

Events add optional `affected_constraint_refs`, including condition dependencies derived
from validated affected-decision IDs. Mute rules match source, impact level, condition version, change type and sorted affected
condition IDs. Previous broad rules and rules for previous conditions no longer suppress
new reminders. The changes API returns expired rules as disabled. Existing action paths
remain valid. Blank optional correction quotes are sent as null by the web client.

Timezone names are bounded to 100 characters. Only missing-record exceptions become 404.
Error diagnostics use fixed bilingual messages with a next action, without raw exceptions or
local paths. Transient errors retry three times with exponential delays unless Retry-After
is supplied; certificate verification failures do not retry. Before upgrading an existing
research database, the store saves a consistent `research.pre-v<target>.*.db` backup.

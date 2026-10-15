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

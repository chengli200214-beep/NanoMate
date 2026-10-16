# Personal assistant development entry

The personal assistant opens at chat and retains the research workspace. This first
integration restores the existing conversation, goals, reminders, memory and file tools.
The unified durable task coordinator and cross-application recovery are not implemented
by this checkpoint. The underlying tools remain subject to Sentinel.

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
the new personal-assistant plan is complete. Reminder editing, unified task state, GUI
recovery and the new evaluation matrix remain subsequent implementation work.

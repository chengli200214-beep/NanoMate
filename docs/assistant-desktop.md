# NanoMate personal assistant desktop

The standalone Windows app opens the personal assistant and its existing research module.
It shares the runtime's chat, task ledger, reminders, file outputs, memory, approvals and
manual takeover controls. The existing DeepSeek Harness desktop remains a separate entry.

The project repository is named NanoMate. The current 1.0.5 installer retains the
`nanoMuse Assistant` application name and data directory.

## Download for Windows

[Download the Windows x64 installer](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)
from the [Windows release](https://github.com/chengli200214-beep/NanoMate/releases/tag/desktop-assistant-v1.0.5).
The release includes checksums and installation instructions. The installer is unsigned and
ships no saved key, personal profile, task database or research data. New profiles start in
offline mode with zero paid allowance. Configure your own connection and limits before use.

## Using the app

Run `nanoMuse-Assistant.exe` from the unpacked directory, or install the Windows setup build.
The Python runtime and the browser binaries are included. A system Python installation and
a separately started web server are not required. The first launch uses labelled offline
scripted responses. It is not a general offline language model.

The sidebar opens conversations, the task ledger, plans and reminders, workspace outputs,
research and personal memory. The home cards prepare editable drafts without sending them.
`Ctrl+K` searches conversation titles. `Ctrl+Shift+O` creates a conversation. Light and dark
appearance are available beside the workspace settings. Research keeps its projects,
reports and approval controls.

Closing the window keeps the process and reminders running in the system tray. Double-click
the tray icon or open the application again to show it. Choose **Exit** from the tray menu
to stop its owned service. Reminders do not run while the desktop process is stopped or the
computer is asleep. The app does not promise Windows sleep or lock-screen recovery.

Use the application menu, available with `Alt`, for zoom, model mode and local folders.
Under **Connections**, configure a model through the existing local credential store.
Saving a key does not grant a paid allowance. **Runtime → Use configured model** restarts
the owned runtime after confirmation. It does not increase any usage limit. The new profile
starts with a zero paid allowance. **Open local limit configuration** opens `assistant.toml`
in the desktop's user-data directory. Its `assistant_usage` section uses the same reviewed
pricing-policy and request limits documented in [the assistant guide](assistant.md#local-runtime-usage-limits).
Restart after changing that file. Never put model keys in it.

## Local data and existing profiles

A normal launch keeps its profile in the Electron user-data directory under
`nanoMuse Assistant/assistant`. Desktop preferences and logs are beside that profile.
No existing research database or credential store is imported automatically.

For an existing personal-assistant profile, pass `--profile` with its directory. A successful
connection retains that selection in local desktop preferences for later launches. The app
checks its profile marker and authenticates against its private local pairing link. If that
service is already running, the window attaches to it and never stops it on exit. If the
profile is locked or foreign, startup fails rather than changing it. Do not use an original
research-only data directory. Keep the profile and user-data directories on a local disk.

The shell starts a hidden child process on loopback, sends local authentication through a
fragment and request headers, and never puts it in process arguments. Exiting sends shutdown
through the owned process's stdin. A closed parent pipe also requests shutdown. It does not
kill processes by port or by executable name. The runtime's existing recovery rules retain
uncertain effects rather than replaying input.

The renderer has sandboxing and context isolation enabled, with Node integration disabled.
Its desktop bridge only controls the window and focuses chat. It cannot read credentials,
run commands or approve tasks. Embedded HTML receives no bridge. External HTTP links open
in the system browser. Output downloads ask for a save location. The shell denies device
permissions; it does not provide microphone dictation. The existing file, browser and
Windows adapters continue through the task ledger and Sentinel.

## Building from source on Windows

Use Node 22 and a separate Python 3.13 build environment from the repository root:

```powershell
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

The script checks and builds the web app, verifies the desktop package lock uses the public
registry, stages Chromium, freezes the assistant runtime and packages the Windows app.
Build products remain under `desktop/assistant/dist/`. `-Target dir` produces an unpacked
application. The build is local and does not publish a release or alter version numbers.
The local Windows installer has no publisher certificate.

For source development, run `npm ci` and `npm start` in `desktop/assistant/` after preparing
the assistant Python environment and building the web app.

## Product screenshots

The [project README](../README.md) introduces this fork's Windows assistant, its features,
DeepSeek setup and source build. [Screenshot provenance](images/assistant/README.md) describes
the isolated offline profile used for its home, task ledger and research images. All task
and research content in those images is synthetic. The screenshots do not contain a saved
key or a production record and do not claim live-model quality results.

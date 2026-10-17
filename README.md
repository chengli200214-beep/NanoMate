# nanoMuse Assistant

A Windows personal assistant with a task ledger and an evidence-based research module.

[English](README.md) | [简体中文](docs/readme/README_zh.md) | [繁體中文](docs/readme/README_zh-TW.md) | [Español](docs/readme/README_es.md) | [Français](docs/readme/README_fr.md) | [Bahasa Indonesia](docs/readme/README_id.md) | [日本語](docs/readme/README_ja.md) | [한국어](docs/readme/README_ko.md) | [Русский](docs/readme/README_ru.md) | [Tiếng Việt](docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**nanoMuse Assistant** brings conversations, plans, reminders, workspace outputs and research into one desktop app. Describe a goal, review the steps, and follow the results in a persistent task ledger. This fork builds on [nanoMuse](https://github.com/nano-muse/nanoMuse), adds a standalone Windows workspace, and retains the original research workflow as a separate module.

## Product views

Captured from the current desktop UI preview using an isolated offline profile. Task names, repositories and report text are synthetic examples, not model-quality results. Open an image to see it at full size.

<a href="docs/images/assistant/desktop-home.png"><img src="docs/images/assistant/desktop-home.png" alt="Home and conversation entry" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="docs/images/assistant/desktop-tasks.png"><img src="docs/images/assistant/desktop-tasks.png" alt="Task ledger and human checkpoints"></a><br><b>Task ledger and human checkpoints</b></td>
    <td width="50%" valign="top"><a href="docs/images/assistant/desktop-research.png"><img src="docs/images/assistant/desktop-research.png" alt="Research conditions and a versioned report"></a><br><b>Research conditions and a versioned report</b></td>
  </tr>
</table>

## Features

| Area | What it does |
| --- | --- |
| Conversations | Use DeepSeek text models, create conversations and search their titles. Light and dark appearance are available. |
| Task ledger | Keep goals, steps, plan revisions, execution records and results together across restarts. |
| Plans and reminders | Create or modify reminders and recurring tasks, with their changes recorded in the ledger. |
| Workspace outputs | Read local Markdown material and produce source-linked outputs with file-content checks. |
| Personal preferences | Save explicitly confirmed preferences, with expiry and removal controls. |
| Controlled actions | Preview workspace file operations, observe browser state and request bounded Windows actions. Pause or take over when needed. |
| Research | Compare candidates using source snapshots, evidence and report versions. Revisit decisions when conditions or sources change. |
| Approvals and usage | Require approval for actionable steps, keep uncertain effects for review, and share persistent model-spending limits. |

## Quick start

1. Build the Windows app below, then open `nanoMuse-Assistant.exe` or the generated setup installer. Packaged builds include Python and Chromium.
2. Open **Connections** from the workspace settings or model selector. Choose DeepSeek, use `https://api.deepseek.com` and `deepseek-flash`, and save your own key in the local encrypted credential store.
3. Choose daily and per-task allowances in the local `%APPDATA%\nanoMuse Assistant\assistant.toml` under `[assistant_usage]`. New profiles start at zero. Configure the reviewed pricing policy and an allowance that covers request reservations; see the [usage guide](docs/assistant.md#local-runtime-usage-limits). Keep keys out of this file.
4. Switch **Runtime → Use configured model**. Alternatively, exit from the tray and start `nanoMuse-Assistant.exe --live`. Saving a key alone does not switch the offline runtime or grant spending permission.
5. Describe your goal in chat. Review the task plan and individual action previews, then follow progress in **Task ledger**. Use pause or manual takeover to intervene.

> A new profile uses labelled offline scripted responses until you select live mode. These responses are not an offline language model.

## Example requests

- Help me plan three learning goals for this week and prepare a review checklist.
- Read the Markdown notes in my workspace and prepare a summary with source references.
- Preview a plan to organize these workspace files, then wait for my confirmation.
- Compare two repository candidates against my requirements and retain the evidence and report versions.

## Build on Windows

Requirements: Windows x64, Git, Node.js 22, Python 3.13 and uv. Run from the repository root. The build script prepares JavaScript dependencies, Chromium and the Python runtime.

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

The unpacked app is written to `desktop/assistant/dist/win-unpacked/`; the setup installer is in `desktop/assistant/dist/`. Use `-Target dir` for an unpacked build. Locally built installers are unsigned. Full instructions: [desktop guide](docs/assistant-desktop.md).

## Data and execution

- The Electron shell opens a local Python service. Conversations, tasks, reminder records and research revisions are stored in local files and SQLite databases.
- Model requests send the necessary conversation and tool context to your selected provider. Local storage does not mean cloud-model requests stay on this computer.
- The tray keeps the app running when its window is closed. Exiting stops its owned service. Select an existing personal-assistant profile with `--profile="D:\AssistantData\profile"`; original research-only directories are not imported automatically.

## Current scope

- This branch focuses on the standalone Windows x64 assistant. The upstream mobile, relay and Harness trees remain in the repository.
- The isolated profile supports reviewed DeepSeek text adapters and deterministic Windows actions. Microphone dictation, visual-model control and embedding-based memory are not enabled in this profile.
- Reminders require the process to remain running and the computer to be awake. Sleep and lock-screen recovery of the new assistant architecture are not claimed as verified.

## Documentation

- [Desktop usage, packaging and existing data](docs/assistant-desktop.md)
- [Task plans, approvals and usage limits](docs/assistant.md)
- [Runtime architecture](docs/architecture.md)
- [Upstream and third-party notices](THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## License and attribution

Built on nanoMuse, licensed under **GPL-3.0-or-later**. Copyright and component notices remain in [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The original DeepSeek Harness desktop remains a separate entry. See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution rules.

<details>
<summary>Upstream community contributors</summary>

<!-- contributors:start -->
<p>
<a href="https://github.com/wsvn53"><img src="https://avatars.githubusercontent.com/u/912204?v=4&s=48" width="48" height="48" alt="wsvn53"></a>
<a href="https://github.com/cutey-none"><img src="https://avatars.githubusercontent.com/u/47075002?v=4&s=48" width="48" height="48" alt="cutey-none"></a>
<a href="https://github.com/Mola-maker"><img src="https://avatars.githubusercontent.com/u/229602071?v=4&s=48" width="48" height="48" alt="Mola-maker"></a>
<a href="https://github.com/PovedaAqui"><img src="https://avatars.githubusercontent.com/u/9494679?v=4&s=48" width="48" height="48" alt="PovedaAqui"></a>
<a href="https://github.com/jiangkaiqi2005"><img src="https://avatars.githubusercontent.com/u/184961141?v=4&s=48" width="48" height="48" alt="jiangkaiqi2005"></a>
<a href="https://github.com/Ichiro786"><img src="https://avatars.githubusercontent.com/u/158778170?v=4&s=48" width="48" height="48" alt="Ichiro786"></a>
<a href="https://github.com/hobostay"><img src="https://avatars.githubusercontent.com/u/110803307?v=4&s=48" width="48" height="48" alt="hobostay"></a>
<a href="https://github.com/aliabbas6622"><img src="https://avatars.githubusercontent.com/u/193962983?v=4&s=48" width="48" height="48" alt="aliabbas6622"></a>
<a href="https://github.com/cypggs"><img src="https://avatars.githubusercontent.com/u/3694954?v=4&s=48" width="48" height="48" alt="cypggs"></a>
</p>
<!-- contributors:end -->

</details>

# nanoMuse Assistant

Windows 個人助理，讓日常任務與有依據的研究共用一個工作空間。

[English](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="../../docs/zh/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/zh/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**nanoMuse Assistant** 將對話、計畫、提醒、資料產物與研究整合為獨立桌面應用程式。說明目標、審閱步驟，再透過持久保存的任務帳本追蹤結果。本專案以 [nanoMuse](https://github.com/nano-muse/nanoMuse) 為基礎，新增 Windows 工作空間，保留原有研究模組。

## 產品畫面

畫面擷取自目前桌面介面的獨立離線預覽。任務、候選儲存庫與報告均為合成範例，不代表模型品質評測。點選圖片可查看原圖。

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="首頁與對話入口" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="任務帳本與人工檢查點"></a><br><b>任務帳本與人工檢查點</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="研究條件與版本化報告"></a><br><b>研究條件與版本化報告</b></td>
  </tr>
</table>

## 主要功能

| 功能 | 說明 |
| --- | --- |
| 對話 | 使用 DeepSeek 文字模型，支援多對話、標題搜尋、淺色與深色外觀。 |
| 任務帳本 | 保存目標、步驟、計畫版本、執行紀錄與結果，重新啟動後仍可查看。 |
| 計畫與提醒 | 建立或修改提醒與週期任務，將變更記錄至帳本。 |
| 資料產物 | 讀取本機 Markdown，建立帶來源的產物並檢查檔案內容。 |
| 個人偏好 | 經明確確認後儲存偏好，支援到期與刪除。 |
| 受控操作 | 預覽工作空間檔案操作、觀察瀏覽器及執行有界的 Windows 操作，支援暫停與人工接管。 |
| 研究模組 | 保留來源快照、證據與報告版本，在條件或來源變更後重新核對。 |
| 審批與用量 | 逐項核准執行，保留結果不明的操作供審閱，統一限制模型費用。 |

## 開始使用

1. 依下方步驟建置並開啟 `nanoMuse-Assistant.exe`，或使用產生的安裝程式。打包版本包含 Python 與 Chromium。
2. 從工作空間設定或模型選擇器開啟「連線」，選擇 DeepSeek、`https://api.deepseek.com` 與 `deepseek-flash`，將自己的金鑰儲存在本機加密憑證庫。
3. 在本機 `%APPDATA%\nanoMuse Assistant\assistant.toml` 的 `[assistant_usage]` 設定每日及單任務額度。新設定預設為零；啟用已審定價格政策並涵蓋請求預留，詳見[用量說明](../../docs/zh/assistant.md#local-runtime-usage-limits)。金鑰不放在此檔案。
4. 選擇「執行模式 → 使用已設定的模型」，或從系統匣退出後以 `nanoMuse-Assistant.exe --live` 重啟。儲存金鑰不會自動切換模式或授予費用額度。
5. 在對話中說明目標，審閱計畫與操作預覽，透過任務帳本追蹤，必要時暫停或人工接管。

> 新設定預設採用明確標示的離線腳本回應，並非通用離線語言模型。

## 請求範例

- 安排本週三項學習目標及複盤清單。
- 讀取工作空間內的 Markdown，整理帶來源的摘要。
- 先預覽檔案整理方案，等我確認再執行。
- 依條件比較兩個儲存庫，保留證據與報告版本。

## 從原始碼建置

需要 Windows x64、Git、Node.js 22、Python 3.13 與 uv。在儲存庫根目錄執行，腳本會準備 JavaScript 依賴、Chromium 與 Python 執行環境。

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

解壓縮版位於 `desktop/assistant/dist/win-unpacked/`，安裝程式位於 `desktop/assistant/dist/`。`-Target dir` 僅產生解壓縮版。本機安裝程式尚未簽署，詳見[桌面說明](../../docs/zh/assistant-desktop.md)。

## 資料與執行方式

- Electron 連線至本機 Python 服務，對話、任務、提醒與研究版本存於本機檔案及 SQLite。
- 必要對話與工具上下文會傳送給所選模型供應商；本機儲存不代表雲端請求留在本機。
- 關閉視窗後保留於系統匣，退出才停止所屬服務。已有個人助理資料可用 `--profile="D:\AssistantData\profile"` 指定，不會自動匯入研究專用目錄。

## 目前範圍

- 本分支聚焦獨立 Windows x64 助理，保留上游手機、relay 與 Harness 程式碼。
- 隔離設定啟用已審定的 DeepSeek 文字介面與確定性 Windows 操作，未啟用聽寫、視覺模型控制與嵌入式記憶檢索。
- 提醒需要程式持續執行且電腦保持喚醒；新架構的睡眠與鎖定畫面恢復尚未宣告驗證通過。

## 文件與目錄

- [桌面使用、建置與已有資料](../../docs/zh/assistant-desktop.md)
- [任務、審批及用量限制](../../docs/zh/assistant.md)
- [執行環境架構](../../docs/zh/architecture.md)
- [上游與第三方聲明](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## 授權與上游致謝

以 nanoMuse 為基礎，採用 **GPL-3.0-or-later**。原版權與元件聲明保留於 [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE) 與 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。DeepSeek Harness 桌面入口仍獨立，貢獻規範見 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

<details>
<summary>上游社群貢獻者</summary>

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

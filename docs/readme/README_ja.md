# NanoMate

タスク台帳と根拠を残す調査モジュールを備えた Windows 個人アシスタント。

[プロジェクトのホーム](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁體中文](../../docs/readme/README_zh-TW.md) | [Español](../../docs/readme/README_es.md) | [Français](../../docs/readme/README_fr.md) | [Bahasa Indonesia](../../docs/readme/README_id.md) | [日本語](../../docs/readme/README_ja.md) | [한국어](../../docs/readme/README_ko.md) | [Русский](../../docs/readme/README_ru.md) | [Tiếng Việt](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-text-c47759" alt="DeepSeek text">
  <img src="https://img.shields.io/badge/UI-React%20%2B%20TypeScript-6e7f91" alt="React and TypeScript">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe"><img src="https://img.shields.io/badge/Download-Windows_x64-c47759" alt="Download Windows x64"></a>
  <a href="../../docs/assistant-desktop.md"><img src="https://img.shields.io/badge/Docs-desktop-746b62" alt="Desktop documentation"></a>
  <a href="../../docs/assistant.md"><img src="https://img.shields.io/badge/Docs-task%20ledger-746b62" alt="Task ledger documentation"></a>
</p>

**NanoMate** は、会話、計画、リマインダー、資料、調査を一つにまとめます。目標を伝え、手順を確認し、永続化されたタスク台帳で結果を追えます。[nanoMuse](https://github.com/nano-muse/nanoMuse) を基に Windows 用の独立画面を追加し、既存の調査機能を保持しています。

## 製品画面

現行画面のプレビューを独立したオフライン環境で撮影しています。タスク、リポジトリ、レポートは架空の例であり、モデルの評価結果ではありません。画像を開くと原寸表示できます。

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="ホームと会話" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="タスク台帳と人による確認"></a><br><b>タスク台帳と人による確認</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="調査条件と版管理されたレポート"></a><br><b>調査条件と版管理されたレポート</b></td>
  </tr>
</table>

## 主な機能

| 機能 | 内容 |
| --- | --- |
| 会話 | DeepSeek のテキストモデル、複数の会話、タイトル検索、ライト・ダーク表示。 |
| タスク台帳 | 目標、手順、計画の版、実行記録と結果を再起動後も保持。 |
| 計画とリマインダー | リマインダーや定期タスクの作成・変更を台帳に記録。 |
| 資料と成果物 | ローカル Markdown を読み、出典付きの成果物を作成して内容を確認。 |
| 個人設定 | 明示的な確認後に好みを保存し、有効期限と削除を管理。 |
| 制御された操作 | ファイル操作のプレビュー、ブラウザーの観察、範囲を限定した Windows 操作。一時停止と手動引き継ぎに対応。 |
| 調査 | 出典のスナップショット、証拠、レポートの版を保存し、条件変更後に結論を再確認。 |
| 承認と使用量 | 操作ごとの承認、不確かな結果の確認、永続化された共通の費用上限。 |

## 使い始める

1. [Windows x64 インストーラー、署名なし](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)をダウンロードして実行するか、下記の手順でビルドします。Python と Chromium を同梱します。
2. 設定またはモデル選択から **Connections** を開き、DeepSeek、`https://api.deepseek.com`、`deepseek-flash` を選び、自分のキーを暗号化されたローカル保管庫に保存します。
3. ローカルの `%APPDATA%\nanoMuse Assistant\assistant.toml` の `[assistant_usage]` に日次とタスク単位の上限を設定します。初期値はゼロです。審定済みの価格ポリシーと予約額を満たす上限は[利用ガイド](../../docs/assistant.md#local-runtime-usage-limits)を参照してください。ここにキーは書きません。
4. **Runtime → Use configured model** を選ぶか、トレイから終了して `nanoMuse-Assistant.exe --live` で再起動します。キーの保存だけではモードや費用許可は変わりません。
5. 会話で目標を伝え、計画と各操作を確認して **Task ledger** で進捗を追います。必要に応じて一時停止や手動引き継ぎを行います。

> 新しいプロファイルは明示されたオフラインのスクリプト応答を使います。汎用のオフライン言語モデルではありません。

## 依頼の例

- 今週の学習目標を三つと振り返りリストを作って。
- Markdown を読み、出典付きで要約して。
- ファイル整理の計画を見せて、確認まで実行を待って。
- 二つのリポジトリを比較し、根拠とレポートの版を残して。

## Windows でのビルド

Windows x64、Git、Node.js 22、Python 3.13、uv が必要です。ルートで実行します。スクリプトが JavaScript の依存関係、Chromium、Python を準備します。

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/NanoMate.git
cd NanoMate
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

アプリは `desktop/assistant/dist/win-unpacked/`、インストーラーは `desktop/assistant/dist/` に出力します。`-Target dir` は展開済み版のみを作ります。ローカルのインストーラーは未署名です。[デスクトップガイド](../../docs/assistant-desktop.md)を参照してください。

## データと実行

- Electron がローカル Python サービスを開きます。会話、タスク、リマインダー、調査の版はローカルファイルと SQLite に保存します。
- 必要な会話とツールの文脈はモデル提供者へ送信します。ローカル保存はクラウドモデルへの送信がないという意味ではありません。
- ウィンドウを閉じてもトレイで動作し、終了すると専用サービスが停止します。既存プロファイルは `--profile="D:\AssistantData\profile"` で指定でき、調査専用フォルダーは自動取り込みしません。

## 現在の範囲

- このブランチは Windows x64 が対象です。上流のモバイル、relay、Harness のコードは保持しています。
- 分離プロファイルは審定済み DeepSeek テキスト接続と決定的な Windows 操作に対応します。音声入力、視覚モデル操作、埋め込みメモリは有効にしていません。
- リマインダーには動作中のプロセスと端末の起動状態が必要です。新構成のスリープ・ロック画面からの復帰は検証済みとはしていません。

## ドキュメント

- [デスクトップの利用、ビルド、既存データ](../../docs/assistant-desktop.md)
- [計画、承認、使用上限](../../docs/assistant.md)
- [アーキテクチャ](../../docs/architecture.md)
- [上流と第三者の通知](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron shell
web/src/             React UI
nanomuse/assistant/  Task ledger and action adapters
nanomuse/research/   Evidence and research reports
scripts/desktop-app/ Runtime packaging
```

## ライセンスと謝辞

nanoMuse を基に **GPL-3.0-or-later** で提供します。通知は [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE)、[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) に保持しています。DeepSeek Harness の入口は別です。規則は [CONTRIBUTING.md](../../CONTRIBUTING.md) を参照してください。

<details>
<summary>上流コミュニティの貢献者</summary>

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

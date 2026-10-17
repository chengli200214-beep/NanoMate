# nanoMuse Assistant 1.0.5 · Hearth

The standalone Windows personal assistant is available as an installer. It includes the
Python runtime and Chromium, so using the packaged app does not require installing Python
or starting a separate web server. Research remains part of the same workspace.

[Download Windows x64](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)
· [Checksums](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/SHA256SUMS.txt)
· [Installation guide](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/INSTALL-Windows.md)

## Highlights

- Conversations, task plans and revisions, reminders, workspace outputs and confirmed
  preferences share one Windows interface, with light and dark appearance.
- Workspace file operations, browser observation and bounded Windows actions retain
  approvals, pause controls and manual takeover.
- The research module retains source snapshots, evidence, comparison reports and their
  versions.
- The installer bundles the runtime and browser. Closing the window keeps the assistant
  in the system tray; exiting stops its owned local service.

## Upgrade Notes

- This release is for **Windows 10/11 x64**. Its installer is **unsigned**.
- New profiles start with labelled offline scripted responses, an empty credential
  store and zero paid allowance. Configure your own DeepSeek connection and local
  allowances, then select the configured model or restart with `--live`. See the
  [desktop guide](https://github.com/chengli200214-beep/continuous-research-assistant/blob/research-assistant/docs/assistant-desktop.md).
- The package contains no saved model key, account session, personal configuration, task
  database or private research record. User data stays outside the application directory.
  The local installer creates an application shortcut without a developer-specific profile.
- Existing personal-assistant users should keep their data directory and local preferences.
  A new installation does not automatically import an original research-only directory.
- Reminders require the process to remain running and the computer to be awake. Recovery
  of the new assistant architecture after Windows sleep or lock is not claimed as verified.
- This is the existing application build from `7f35ecbad0d5ecfa17621e6f9814f0e473ea30b5`.
  Later changes only updated documentation and product images. This publication reused
  that installer; no rebuild or new test run was performed.

Installer SHA-256:

```text
718deac47ce6540c46686f2699138abc785d1b6b447ac2684bd11cc2ef7f4e50
```

## Community

Built on nanoMuse under GPL-3.0-or-later. Corresponding source is available from this
release's tag. Existing copyright and third-party notices remain in the repository and
application package. Report usage problems in this repository's
[issues](https://github.com/chengli200214-beep/continuous-research-assistant/issues).
This standalone Windows package is a separate entry from the upstream Harness desktop.

<details>
<summary>简体中文</summary>

## nanoMuse Assistant 1.0.5 · Hearth

独立 Windows 个人助手现已提供安装包。程序内置 Python 和 Chromium，下载安装后无需另装 Python，
也不必手动启动网页服务。现有调研功能保留在同一个工作空间中。

[下载 Windows x64 安装包](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)
· [校验文件](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/SHA256SUMS.txt)
· [安装说明](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/INSTALL-Windows.md)

### 亮点

- 对话、任务计划及版本、提醒、资料产物和经确认的个人偏好共用桌面界面，支持浅色和深色外观。
- 文件操作、浏览器观察和限定范围的 Windows 操作保留审批、暂停及人工接管。
- 调研模块保留来源快照、证据、比较报告及其版本。
- 安装包内置运行环境与浏览器。关闭窗口后保留在托盘，退出时停止其所属本地服务。

### 升级说明

- 支持 **Windows 10/11 x64**，安装包目前**未签名**。
- 全新配置默认使用明确标记的离线脚本响应，凭据库为空，付费额度为零。使用者需要自行配置 DeepSeek
  连接和本地额度，再切换到已配置的模型，或退出后用 `--live` 重新启动。详见
  [桌面端使用说明](https://github.com/chengli200214-beep/continuous-research-assistant/blob/research-assistant/docs/zh/assistant-desktop.md)。
- 安装包不包含已保存的模型密钥、账号会话、个人配置、任务库或私人调研记录。用户数据位于应用目录外。
  安装过程创建的快捷方式不携带开发者的数据目录参数。
- 已有个人助手用户应保留自己的数据目录和本地偏好。新安装不会自动导入原调研专用目录。
- 提醒需要程序持续运行且电脑保持唤醒，新架构的 Windows 休眠或锁屏恢复尚未宣布验证通过。
- 发布复用 `7f35ecbad0d5ecfa17621e6f9814f0e473ea30b5` 对应的现有安装包，后续改动仅涉及文档和产品图。
  本次没有重新构建，也没有新增测试。

安装包 SHA-256：

```text
718deac47ce6540c46686f2699138abc785d1b6b447ac2684bd11cc2ef7f4e50
```

### 社区

基于 nanoMuse，遵循 GPL-3.0-or-later，完整对应源码可从本 Release 的 tag 获取。
仓库和应用包保留原有版权与第三方声明，使用问题可提交到本仓库的
[issues](https://github.com/chengli200214-beep/continuous-research-assistant/issues)。
这个独立 Windows 程序与上游 Harness 桌面端保持为不同入口。

</details>

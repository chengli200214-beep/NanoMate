# nanoMuse 个人助手

Windows 个人助手，把日常任务和有依据的调研放进同一个工作空间。

[项目首页](../../README.md) | [简体中文](../../docs/readme/README_zh.md) | [繁体中文](../../docs/readme/README_zh-TW.md) | [西班牙语](../../docs/readme/README_es.md) | [法语](../../docs/readme/README_fr.md) | [印度尼西亚语](../../docs/readme/README_id.md) | [日语](../../docs/readme/README_ja.md) | [韩语](../../docs/readme/README_ko.md) | [俄语](../../docs/readme/README_ru.md) | [越南语](../../docs/readme/README_vi.md)

<p>
  <img src="https://img.shields.io/badge/Windows-x64-5d7161" alt="Windows x64">
  <img src="https://img.shields.io/badge/DeepSeek-%E6%96%87%E5%AD%97%E6%A8%A1%E5%9E%8B-c47759" alt="DeepSeek 文字模型">
  <img src="https://img.shields.io/badge/%E7%95%8C%E9%9D%A2-React%20%2B%20TypeScript-6e7f91" alt="React 与 TypeScript 界面">
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-GPL--3.0--or--later-746b62" alt="GPL-3.0-or-later"></a>
  <br>
  <a href="https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe"><img src="https://img.shields.io/badge/%E4%B8%8B%E8%BD%BD-Windows_x64-c47759" alt="下载 Windows x64 安装包"></a>
  <a href="../../docs/zh/assistant-desktop.md"><img src="https://img.shields.io/badge/%E6%96%87%E6%A1%A3-%E6%A1%8C%E9%9D%A2%E7%AB%AF-746b62" alt="桌面端使用说明"></a>
  <a href="../../docs/zh/assistant.md"><img src="https://img.shields.io/badge/%E6%96%87%E6%A1%A3-%E4%BB%BB%E5%8A%A1%E8%B4%A6%E6%9C%AC-746b62" alt="任务账本说明"></a>
</p>

**nanoMuse 个人助手** 将对话、计划、提醒、资料产物和调研整合为独立桌面端。说出目标，审阅执行步骤，再通过持久化任务账本跟进结果。本项目基于 [nanoMuse](https://github.com/nano-muse/nanoMuse) 二次开发，新增独立的 Windows 工作空间，原有调研流程保留为单独模块。

## 产品界面

图片取自当前桌面界面的独立离线预览。任务名称、候选仓库和报告均为合成示例，不代表真实模型的效果评测。点击图片可查看原图。

<a href="../../docs/images/assistant/desktop-home.png"><img src="../../docs/images/assistant/desktop-home.png" alt="首页与对话入口" width="1200"></a>

<table>
  <tr>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-tasks.png"><img src="../../docs/images/assistant/desktop-tasks.png" alt="任务账本与人工检查点"></a><br><b>任务账本与人工检查点</b></td>
    <td width="50%" valign="top"><a href="../../docs/images/assistant/desktop-research.png"><img src="../../docs/images/assistant/desktop-research.png" alt="调研条件与版本化报告"></a><br><b>调研条件与版本化报告</b></td>
  </tr>
</table>

## 主要功能

| 功能 | 说明 |
| --- | --- |
| 对话入口 | 接入 DeepSeek 文字模型，支持多对话、按标题搜索，以及浅色和深色外观。 |
| 统一任务账本 | 集中保存目标、步骤、计划版本、执行记录和结果，重启后仍可查看。 |
| 计划与提醒 | 创建或修改提醒、安排周期任务，相关变更同步记录到账本。 |
| 资料与产物 | 读取工作空间中的 Markdown 材料，生成关联来源的产物，并核验文件内容。 |
| 个人偏好 | 经明确确认后保存个人偏好，支持到期和删除。 |
| 受控电脑操作 | 预览工作空间文件操作、观察浏览器状态，并执行有界的 Windows 操作，支持暂停和人工接管。 |
| 独立调研模块 | 比较候选，保留来源快照、证据和报告版本；条件或来源变化后重新核对结论。 |
| 审批与用量 | 执行步骤保留审批，结果不明时交由审阅；主聊天和调研共享持久化模型费用限制。 |

## 开始使用

1. 下载并运行 [Windows x64 安装包，未签名](https://github.com/chengli200214-beep/continuous-research-assistant/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)，也可按下方说明从源码构建。程序内置 Python 与 Chromium。
2. 从工作空间设置或输入框下方的模型选择器打开「连接」。选择 DeepSeek，填写 `https://api.deepseek.com` 与 `deepseek-flash`，在本地加密凭据库中保存自己的密钥。
3. 在本地 `%APPDATA%\nanoMuse Assistant\assistant.toml` 的 `[assistant_usage]` 中设置每日及单任务额度。新配置初始为零；启用已审定的价格政策，并选择能覆盖请求预留的额度，详见[用量说明](../../docs/zh/assistant.md#local-runtime-usage-limits)。不要把密钥填入此文件。
4. 通过「运行模式 → 使用已配置的模型」切换。也可从托盘退出后，以 `nanoMuse-Assistant.exe --live` 重新启动。仅保存密钥不会切换离线模式，也不会自动授予费用额度。
5. 在对话中描述目标，审阅任务计划及逐项操作预览；通过「任务账本」查看进度，需要时暂停或人工接管。

> 新配置默认使用有明确标记的离线脚本响应，切换为真实模型后才会调用 API。离线脚本响应不具备通用语言模型能力。

## 你可以这样提问

- 帮我安排本周三个学习目标，并整理一份复盘清单。
- 读取工作空间里的 Markdown 笔记，整理带来源的摘要。
- 先预览这批工作空间文件的整理方案，等我确认后再执行。
- 按我的条件比较两个仓库，保留证据和报告版本。

## 从源码构建

需要 Windows x64、Git、Node.js 22、Python 3.13 和 uv。在仓库根目录执行。构建脚本会准备 JavaScript 依赖、Chromium 和 Python 运行时。

```powershell
git clone --branch research-assistant https://github.com/chengli200214-beep/continuous-research-assistant.git
cd continuous-research-assistant
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

解压目录位于 `desktop/assistant/dist/win-unpacked/`，安装包位于 `desktop/assistant/dist/`。使用 `-Target dir` 可只生成解压版。本地构建的安装包尚未签名，完整步骤见[桌面端说明](../../docs/zh/assistant-desktop.md)。

## 数据与执行方式

- Electron 外壳连接本机 Python 服务，对话、任务、提醒记录和调研版本保存在本地文件及 SQLite 数据库中。
- 调用模型时，必要的对话和工具上下文会发送给所选提供商。本地保存数据不等于云端模型请求始终留在本机。
- 关闭窗口后程序留在托盘运行，从托盘退出才会停止其启动的服务。已有个人助手数据可通过 `--profile="D:\AssistantData\profile"` 指定；不会自动导入原调研专用目录。

## 当前范围

- 本分支主要交付独立 Windows x64 个人助手。仓库继续保留上游手机端、中继服务与 Harness 代码。
- 隔离配置目前启用已审定的 DeepSeek 文字接口和确定性 Windows 操作；未启用麦克风听写、视觉模型控制和嵌入式记忆检索。
- 提醒需要程序持续运行且电脑处于唤醒状态。新个人助手架构的睡眠与锁屏恢复尚未宣布验证通过。

## 文档与目录

- [桌面端使用、构建与已有数据](../../docs/zh/assistant-desktop.md)
- [任务计划、审批与费用限制](../../docs/zh/assistant.md)
- [运行时架构](../../docs/zh/architecture.md)
- [上游及第三方声明](../../THIRD_PARTY_NOTICES.md)

```text
desktop/assistant/   Electron 桌面外壳
web/src/             React 界面
nanomuse/assistant/  任务账本与操作适配器
nanomuse/research/   调研证据与报告
scripts/desktop-app/ 运行环境打包
```

## 许可证与上游致谢

基于 nanoMuse，遵循 **GPL-3.0-or-later**。原有版权及组件声明保留在 [LICENSE](../../LICENSE)、[NOTICE](../../NOTICE) 和 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)。原 DeepSeek Harness 桌面端仍为独立入口，贡献约定见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

<details>
<summary>上游社区贡献者</summary>

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

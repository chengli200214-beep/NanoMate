# NanoMate 1.0.5 Windows 安装说明

NanoMate 是基于 nanoMuse 开发的个人助手项目。当前安装包沿用 `nanoMuse Assistant` 的程序名称和数据目录。

## 下载与安装

1. 从[版本发布页面](https://github.com/chengli200214-beep/NanoMate/releases/tag/desktop-assistant-v1.0.5)下载
   [Windows x64 安装包](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/nanoMuse-Assistant-1.0.5-win-x64.exe)
   和[校验文件](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-assistant-v1.0.5/SHA256SUMS.txt)。
2. 在 PowerShell 中计算安装包的 SHA-256，与校验文件中的对应记录比对：

   ```powershell
   Get-FileHash .\nanoMuse-Assistant-1.0.5-win-x64.exe -Algorithm SHA256
   ```

3. 运行安装包，选择安装目录，再从快捷方式打开 **nanoMuse Assistant**。安装包目前未签名，
   已内置 Python 和 Chromium，无需另装 Python 或手动启动网页服务。
4. 全新配置默认使用有明确标记的离线脚本响应，付费额度为零。从工作空间设置或模型选择器打开「连接」，
   填写自己的 DeepSeek 密钥、`https://api.deepseek.com` 和 `deepseek-flash`。
5. 在 `%APPDATA%\nanoMuse Assistant\assistant.toml` 设置已审定的价格政策、每日及单任务费用额度，
   修改后重启。不要把密钥填入这个文件，请按[用量文档](https://github.com/chengli200214-beep/NanoMate/blob/research-assistant/docs/zh/assistant.md#local-runtime-usage-limits)
   设置能覆盖请求预留的额度。
6. 选择「运行模式 → 使用已配置的模型」，或从托盘退出后，以已安装的
   `nanoMuse-Assistant.exe --live` 重新启动。随后可描述目标、审阅计划和操作预览。

## 已有数据与后台运行

普通配置位于 `%APPDATA%\nanoMuse Assistant\assistant`，桌面偏好和额度文件在其旁边。
此前通过 `--profile` 选择的个人助手目录仍会沿用。安装包不含已保存的模型密钥、账号会话、个人配置、
本地任务或私人调研数据库，也不会自动导入原调研专用目录。已有用户应保留自己的数据目录和本地偏好。

关闭窗口后程序留在托盘，退出才会停止其所属的本地服务。提醒需要应用保持运行且电脑处于唤醒状态。
新个人助手架构的 Windows 休眠或锁屏恢复尚未宣布验证通过。

## 文档与源码

- [中文桌面端说明](https://github.com/chengli200214-beep/NanoMate/blob/research-assistant/docs/zh/assistant-desktop.md)
- [任务计划、审批与用量限制](https://github.com/chengli200214-beep/NanoMate/blob/research-assistant/docs/zh/assistant.md)
- [对应源码标签](https://github.com/chengli200214-beep/NanoMate/tree/desktop-assistant-v1.0.5)
- [问题反馈](https://github.com/chengli200214-beep/NanoMate/issues)

项目基于 nanoMuse，遵循 GPL-3.0-or-later，仓库和应用包保留原有版权及第三方声明。
安装包复用源码提交 `7f35ecbad0d5ecfa17621e6f9814f0e473ea30b5` 对应的现有构建，后续修改仅涉及文档和产品图，
此次仓库更名没有重新打包或运行测试。

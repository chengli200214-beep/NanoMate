# NanoMate 1.0.5 · Windows 桌面版

桌面窗口、首页、托盘、快捷方式和 Windows 安装包现已统一使用 NanoMate 名称。
这是 1.0.5 的更名构建，继续使用原来的数据目录，已有 DeepSeek 配置、费用额度、任务和调研记录可继续沿用。

[下载 Windows x64 安装包](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-nanomate-v1.0.5/NanoMate-1.0.5-win-x64.exe)
· [校验文件](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-nanomate-v1.0.5/SHA256SUMS.txt)
· [中文安装说明](https://github.com/chengli200214-beep/NanoMate/releases/download/desktop-nanomate-v1.0.5/INSTALL-Windows.md)

## 本次变化

- 桌面标题、侧栏、启动页、托盘和退出提示统一显示为 NanoMate，程序文件为 `NanoMate.exe`。
- Windows 安装包和安装快捷方式使用 NanoMate 名称，保留原安装标识和用户数据保留规则。
- 默认沿用 `%APPDATA%\nanoMuse Assistant`，保留其中的桌面偏好、费用配置及已选择的个人助手目录。
- 首页和文档保留中文介绍，下载地址和产品图同步更新。

## 升级与首次使用

- 支持 **Windows 10/11 x64**，安装包目前**未签名**，内置 Python 与 Chromium。
- 升级前先从系统托盘退出旧版程序，再运行新安装包。请保留原数据目录，无需复制或重新填写已有密钥。
- 通过 `--profile` 或 `--user-data` 指定过目录的用户，应继续使用原目录；显式参数仍优先于保存的偏好。
- 全新安装没有开发者的密钥、账号会话、任务库或私人调研记录，默认使用离线脚本响应且付费额度为零。
  使用者需要自行配置 DeepSeek 连接、费用额度并切换到已配置的模型，详见
  [中文桌面端说明](https://github.com/chengli200214-beep/NanoMate/blob/research-assistant/docs/zh/assistant-desktop.md)。
- 任务账本、提醒、资料产物、受控电脑操作、审批、暂停、人工接管和调研模块沿用现有功能。
- 提醒需要程序持续运行且电脑保持唤醒，新个人助手架构的 Windows 休眠或锁屏恢复尚未宣布验证通过。

## 验证范围

完成了前端生产构建、7 项针对性桌面检查及打包程序的离线启动核验，窗口与程序元信息均显示为 NanoMate。
已有配置和任务、调研记录已核对保留。安装包内容经过检查，不包含开发者的密钥或本地数据。
本次没有进行大规模回归，也没有新增真实模型调用。

安装包 SHA-256：

```text
b71723fff4371d5d7df17012723e6c115fbf26ae7500db2febd83ec161f14a89
```

## 源码与许可证

对应源码见本次发布的 `desktop-nanomate-v1.0.5` 标签。项目基于 nanoMuse，遵循 GPL-3.0-or-later，
仓库和应用包保留原有版权与第三方声明。使用问题可提交到本仓库的
[问题反馈](https://github.com/chengli200214-beep/NanoMate/issues)。

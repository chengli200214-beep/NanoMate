# 个人助手桌面端

独立 Windows 桌面端以个人助手为入口，共用现有对话、任务账本、提醒、文件产物、记忆、审批与人工接管功能。调研保留为独立模块，原有 DeepSeek Harness 桌面入口也继续保留。

## 使用

运行解压目录中的 `nanoMuse-Assistant.exe`，或使用 Windows 安装包。应用已包含 Python 运行时和浏览器文件，无需另装 Python 或手动启动网页服务。首次启动使用明确标注的离线脚本响应，这不是通用离线大模型。

左侧可进入对话、任务账本、计划与提醒、资料与产物、调研模块及个人记忆。首页卡片只会把建议填入输入框，编辑后再发送。`Ctrl+K` 搜索对话标题，`Ctrl+Shift+O` 新建对话。侧栏底部可切换浅色和深色外观。调研继续使用现有项目、报告及审批。

关闭窗口后，进程和提醒继续在系统托盘运行。双击托盘图标或再次打开应用即可显示窗口。右键托盘图标选择「退出」，会停止桌面端自己启动的服务。退出应用或电脑休眠时，提醒不会执行。此版本未承诺 Windows 休眠或锁屏恢复。

按 `Alt` 可使用应用菜单中的缩放、运行模式和本地文件入口。在「连接」中配置模型，密钥仍由现有本地凭据库保存。保存密钥不会增加调用额度。「运行模式 → 使用已配置的模型」会先确认，再重新启动桌面端所属的服务，不会调整用量上限。

新建配置的付费额度默认为零。「打开本地额度配置」可打开桌面端用户数据目录中的 `assistant.toml`，其中 `assistant_usage` 沿用[个人助手文档](assistant.md)中的价格策略、请求和用量上限。修改后需重启。不要在这个文件里填写模型密钥。

## 数据与已有配置

普通启动会把个人助手配置保存在 Electron 用户数据目录的 `nanoMuse Assistant/assistant` 下，桌面偏好和日志在其旁边。不会自动导入已有调研数据库或凭据。

如需使用已有个人助手配置，可通过 `--profile` 指定目录。成功连接后，该选择会保存在本地桌面偏好中，供后续启动使用。应用会核验配置标记，再使用其本地配对链接验证服务。若服务已经运行，桌面端只连接，退出时不会停止它。目录被占用或属于其他配置时，启动会失败，不会覆盖数据。不要指定原调研专用的数据目录，配置和用户数据目录应位于本地磁盘。

桌面端的后台子进程仅监听回环地址。配对凭据通过地址片段和请求头传递，不进入进程参数。退出时通过所属子进程的标准输入请求关闭，父进程管道断开时也会请求关闭。不会按端口或进程名称批量结束服务。运行时仍保留原有恢复规则，结果不确定的输入不会自动重放。

渲染层启用沙箱和上下文隔离，关闭 Node 集成。桌面桥接只处理窗口和对话聚焦，不提供读取密钥、执行命令或批准任务的能力。嵌入的 HTML 不会得到桥接。外部 HTTP 链接交给系统浏览器，下载产物会询问保存位置。外壳拒绝设备权限，因此不提供麦克风听写。文件、浏览器和 Windows 原生应用操作继续经过任务账本和 Sentinel。

## Windows 源码构建

使用 Node 22，并从仓库根目录准备独立的 Python 3.13 环境：

```powershell
uv venv .local/assistant-desktop-build --python 3.13
uv pip sync requirements-assistant-windows.txt --python .local/assistant-desktop-build/Scripts/python.exe --require-hashes --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe --no-deps -e . --default-index https://pypi.org/simple --no-config
uv pip install --python .local/assistant-desktop-build/Scripts/python.exe pyinstaller==6.22.3 --default-index https://pypi.org/simple --no-config
powershell -File scripts/build-assistant-desktop.ps1 -Target installer
```

脚本会检查并构建网页、核对桌面依赖使用公开注册表、准备 Chromium、冻结助手运行时并生成 Windows 应用。产物位于 `desktop/assistant/dist/`，`-Target dir` 只生成解压目录。构建仅在本地进行，不会发布版本或修改版本号。当前本地安装包没有发布者签名。

源码开发时，在准备好助手 Python 环境并构建网页后，进入 `desktop/assistant/` 执行 `npm ci` 和 `npm start`。已配对浏览器中可用 `?ui=desktop` 预览布局，该参数不会授予桌面桥接能力。

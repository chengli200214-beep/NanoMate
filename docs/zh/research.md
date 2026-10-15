# 持续技术选型助手

调研工作台比较两个或三个仓库，保存可追溯的证据、条件和报告版本，并持续检查已登记来源的
变化。当前是基于 nanoMuse Python 运行时与网页控制台的单用户扩展，沿用模型适配、认证和
Sentinel。上游归属与固定 commit 见 [UPSTREAM.md](../../UPSTREAM.md)。

## 本地启动

开发路径使用 Python 3.13、Node.js 22 或更高版本。Python 依赖快照在 Windows、Python
3.13.9 下生成。从项目根目录运行：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-research.txt
.venv/Scripts/python.exe -m pip install --no-deps -e .
npm --prefix web ci --registry=https://registry.npmjs.org/
npm --prefix web run build
.venv/Scripts/python.exe scripts/research_dev.py --seed-fixture
```

已有 `.venv` 且已安装依赖时，运行最后一行即可。Windows 双击
`.local/research-dev/open-research.url` 打开带本地配对信息的工作台。

服务仅监听 `127.0.0.1:8787`，保留 Bearer 认证。令牌、模型设置、审计、数据库和报告保存在
`.local/research-dev/`，该目录已加入 Git 忽略规则。用 Ctrl+C 停止，使用同一命令和数据目录
重启后继续工作。可用 `--port`、`--data-dir` 建立另一个独立实例。同一数据目录只启动一个服务。

系统 DNS 返回代理虚拟地址时，可显式选择公共加密 DNS：

```powershell
.venv/Scripts/python.exe scripts/research_dev.py --seed-fixture --source-dns cloudflare
```

该选项只作用于已登记研究来源的读取，会把来源域名发送给 Cloudflare，保留公网地址检查，
不修改 Windows 网络设置。详见[来源读取](research-sources.md)。

这个专用启动器直接进入调研工作台，跳过聊天初始化，不要求登录 nanoMuse Cloud，并在当前
进程禁用邮件、日历、联系人、GUI、浏览器、skills、MCP 和 Hub。真实项目仍须配置模型。
上游 `nanomuse serve` 的初始化流程保持原有行为。

## 配置 DeepSeek

打开「设置」，进入「连接」的聊天模型配置，选择 DeepSeek，填写自己的密钥并测试连接。
官方当前说明的 OpenAI 兼容地址为 `https://api.deepseek.com`，可用模型 ID 包括
`deepseek-flash`。已于 2026-10-15 核对 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/)。
其他国内服务可使用上游已有的提供方列表，或填写自定义 OpenAI 兼容地址；所选模型需要支持
工具调用和结构化 JSON。连接成功只说明接口可用，研究效果需要另外验收。

通过连接页录入的密钥进入 nanoMuse vault。不要把密钥放入项目需求、资料文本、测试快照、
提交或截图。配置完成后新建「实时模式」项目。已有的固定资料项目不能改成实时项目。

## 使用流程

1. 新建项目，填写两个或三个 GitHub 仓库地址、选型目标、必须满足的条件和偏好；选择实时
   调研或明确标记的固定资料回放。
   报告的对照维度显示条件正文，便于直接阅读。
2. 添加 README、仓库文件、发布记录、元数据、公开文档或粘贴文本。每个来源归属一个候选。
   资料中的指令只作为来源内容处理。
3. 开始调研。「概览」展示六部分报告；「证据」展示原文、固定版本、核验级别、纠正记录和
   冲突双方。未获充分证据的硬条件保持「无法确认」。
4. 在「运行」查看步骤，暂停后当前步骤会收尾，之后停止领取；可继续、取消或重试失败步骤。
   重启复用已提交的步骤产物。
5. 编辑条件、纠正事实时，旧报告标为过期，并排队生成新版本。旧 worker 的结果不能覆盖新
   输入。重新核验会检查来源，并重做相应证据提取。
   重新核验生成不同表述时，旧事实保留为已替代记录。
6. 在「变化」启用跟踪，设置时区、间隔或当地每天检查时刻。服务运行时执行检查；停机期间
   错过的检查在重启后合并成一次补查。来源没有变化时不调用模型、不新增报告。
7. 查看报告差异、导出 Markdown，接受更新、反馈提醒是否有用，或静默某个来源的影响级别。
   只有影响推荐的来源变化产生变化提醒，首次或重新调研完成另有报告就绪通知。

## 状态、恢复与预算

数据位于 `research/research.db`，使用 SQLite WAL。原子领取保证同一时间运行一个研究步骤；
租约 60 秒，每 10 秒续约。提交同时校验 attempt、条件和输入版本。来源快照、条件版本和
报告版本不可覆盖。正式报告指针和逻辑通知在同一事务提交。异常退出可能留下临时文件，但
它不会成为当前正式报告。

临时错误最多尝试三次。单个来源不可访问时显示资料缺口；所有来源不可访问、缺少密钥或
模型预算耗尽时进入待处理。默认每次运行最多 40 次模型请求，可在 1 至 200 之间调整，
工具轮次、格式修复和失败请求计入次数。请求前先持久化调用预留；中断后的用量保持未知。
提供方 SDK 的传输重试不是独立的推理请求。提供方未返回的 token、未核实单价的费用不补造。

研究模型只拿到四个研究工具。已登记的公开读取也经过 Sentinel；HTTP 抓取拒绝私网地址、
不安全重定向和 DNS 重绑定。如果代理或 DNS 把公开 GitHub 域名解析到私网或 fake IP，抓取
会拒绝访问，需要先调整解析或代理路由，再做真实来源验收。手动资料和固定快照仍可验证流程。

## 验证命令与待验收项

```powershell
.venv/Scripts/python.exe -m pytest -q tests/research tests/test_research_reasoning.py tests/test_research_call_budget.py tests/test_research_conflicts.py tests/test_research_fixture_excerpt.py tests/test_research_evaluation.py
.venv/Scripts/python.exe -m ruff check nanomuse/research
.venv/Scripts/python.exe -m mypy nanomuse/research --follow-imports=silent
npm --prefix web run check
.venv/Scripts/python.exe scripts/evaluate_research.py --mode fixture
.venv/Scripts/python.exe scripts/research_fault_matrix.py
```

故障测试包含四个真实子进程异常退出点、租约恢复、过期输入拒绝、原子发布和项目隔离。
场景运行器在执行前冻结资料与代码，首轮 32 次运行，另有四次预先指定复跑，分别保留预期
与实际行为。离线通过只能证明流程，不能写成真实模型准确率、有效提醒率或研究成本改善。
故障矩阵命令选择十组断言，每组重复三次；状态恢复部分使用 FakeClock 和数据库重开，
另外的子进程测试验证真实 `os._exit(86)` 退出后的恢复。矩阵结果文件不能覆盖。

DeepSeek 真实研究、真实来源网络访问、夜间停机与次日定时检查、人工审定标注、真人试用
均需另行完成验收。接口见 [契约](research-contracts.md)，实现见
[研究推理](research-reasoning.md)、[来源读取](research-sources.md)，实验口径见
[评测说明](research-evaluation.md)。
已完成的检查和待办验收见 [开发与验证记录](research-status.md)。
产品范围、架构与数据模型图、三分钟演示脚本见[作品展示说明](research-showcase.md)。

以下命令分别留存验收结果，输出目录不能覆盖。模型命令使用工作台原有配置和加密保险库：

```powershell
.venv/Scripts/python.exe scripts/research_network_smoke.py --source-dns cloudflare --output .local/verification/network-check
.venv/Scripts/python.exe scripts/research_model_smoke.py --data-dir .local/research-dev --output .local/verification/model-check
.venv/Scripts/python.exe scripts/evaluate_research.py --mode live --pilot --data-dir .local/research-dev
.venv/Scripts/python.exe scripts/research_wall_clock_probe.py watch --output .local/verification/wall-clock
.venv/Scripts/python.exe scripts/research_wall_clock_probe.py watch --overnight --daily-time 09:00 --timezone Asia/Shanghai --output .local/verification/overnight
```

联网命令通过 Sentinel 读取两个仓库、元信息、发布记录、固定文件和公开文档，再验证未变化
时的读取。可用 `--kind file` 在新目录复查失败类型，避免重复全部请求。模型命令要求模型
实际选择并成功调用研究工具，普通聊天回复不能通过。缺少凭据时记录 `needs_configuration`
和零次请求。两者都不代表研究结论质量已经达标。

真实时间检查建立两个独立的固定资料项目，先停止工作进程，等待 130 个真实秒数后用另一个
进程恢复，核对漏跑合并和无变化时零新增调用、报告及提醒。加 `--overnight` 时等待次日所选
时区的指定时刻，并要求至少经过六小时。电脑和计时程序需要持续运行，期间修改运行时代码
会拒绝验收。验收数据库与工作台项目分开，等待程序本身不运行研究 worker。只有跨夜摘要
实际完成后，才能标记跨夜验收通过。标注审定和真人试用仍需要真实的审查者与使用者。

## 实现参与方式

AI 编码代理在独立 worktree 中完成来源、推理、网页和评测泳道；集成部分定义共享契约、实现
持久状态与编排、审查模块，并补充一致性与真实进程恢复检查。这是本次实现过程的归属说明，
不代表项目所有者已经亲自审查全部生成代码，或已经完成用户试用。

# 研究接口契约 v1

共享类型位于 `nanomuse/research/contracts.py`。来源读取和研究推理分别实现
`SourceFetcher` 与 `Reasoner` 接口。持久状态使用独立 SQLite 表，服务边界使用 JSON。
每条记录归属一个项目。

编排阶段依次为收集来源、提取证据、比较、发布。比较结果是结构化数据，不能作为可执行
命令。研究 Agent 不获得 shell、邮件、任意文件或浏览器工具。

所有路由沿用宿主的 Bearer 认证，前缀 `/api/research`。列表返回 JSON 数组，错误字段为
`detail`，标识符为字符串。

| 方法与路径 | 输入或输出 |
| --- | --- |
| GET、POST `/projects` | 项目列表；创建输入 ProjectSpec |
| GET `/projects/{id}` | 项目、条件、来源、运行和当前报告 |
| POST `/projects/{id}/revisions` | 完整 ProjectSpec，返回新条件版本 |
| POST `/projects/{id}/sources` | SourceInput，返回登记来源 |
| POST `/projects/{id}/runs` | kind 为 research 或 check，可带 request_key |
| GET `/runs/{id}` | 运行和步骤 |
| POST `/runs/{id}/control` | action 为 pause、resume、cancel 或 retry |
| GET `/projects/{id}/evidence` | 证据数组，可按 candidate、dimension、status 筛选 |
| GET `/projects/{id}/conflicts` | 冲突数组，含双方证据 ID 与绑定版本 |
| POST `/evidence/{id}/correction` | fact、reason，可选 quote |
| POST `/evidence/{id}/reverify` | 排队重新核验 |
| GET `/projects/{id}/reports` | 报告版本，含 Markdown |
| GET `/reports/{id}/diff?previous=...` | before、after、diff |
| GET `/reports/{id}/export` | 已认证的 Markdown 下载 |
| PUT `/projects/{id}/tracking` | enabled、interval_minutes、timezone、daily_time、max_calls |
| GET `/projects/{id}/changes` | events、notifications、mute_rules |
| POST `/changes/{id}/action` | accept、useless、mute 或 unmute |
| POST `/notifications/{id}/feedback` | useful、useless 或 read |
| GET `/projects/{id}/diagnostics` | runs、steps、calls、events |

ProjectSpec 包含 title、goal、两个或三个 candidates、constraints 与 live/fixture 模式。
证据包含候选、维度、事实、原文、快照、核验级别、状态、来源地址、版本与抓取时间。冲突
包含同一候选的至少两个不同有效证据 ID、原因和从实际绑定资料生成的版本范围。冲突不能
支持「满足」或「不满足」的确定结论。

SQLite schema v2 为运行增加 input_version，已有 v1 数据库打开时补入默认值 1。用户改变
输入会递增项目版本并阻止旧任务提交。步骤提交校验 owner、attempt、租约、输入指纹、输入
版本和当前条件版本。来源、条件与报告版本不可覆盖。

模型请求在网络调用前预留稳定调用 ID，完成后更新同一记录；失联请求标为 unknown 并继续
占用预算。fixture 模式始终显式标注，固定快照与脚本响应不能当作真实模型验证。

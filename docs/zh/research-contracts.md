# 研究接口契约 v1

条件版本改变后，即使正文不变，也会按新条件重新提取当前快照。来源的可选字段
`extracted_spec_id` 记录最近完成提取的条件版本；没有此字段的旧记录仍可读取，在下一次
研究时刷新。新提取未保留的旧机器事实转为 superseded；用户纠正及其替代的事实不会被
自动重新启用。

同一候选、同一硬条件的非偏好判断互相矛盾时，发布前拒绝该结果，要求模型修正。不相关
观察应使用空 `constraint_refs`，来源冲突仍须保持 unknown。未以句末标点结束的短引文，
在不超过 2,000 字符时保留来源的完整所在段落；这能保留限定语，但不证明事实由引文推出。
候选行只陈述自身事实，跨候选汇总须指出双方证据。

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

变化事件可附带 `model_judgment` 与 `notification_policy`。模型将变化判为 uncertain，且
变化后的当前证据参与尚未解决的硬条件冲突时，提醒策略将生效的 `impact_level` 设为
recommendation，并保留模型原判断。`notification_policy` 保存策略 id、条件与证据引用。
候选判断仍为 unknown，recommendation 级静默和事件去重继续生效。新增字段均为可选，旧
客户端可忽略。

ProjectSpec 包含 title、goal、两个或三个 candidates、constraints 与 live/fixture 模式。
证据包含候选、维度、事实、原文、快照、核验级别、状态、来源地址、版本与抓取时间。冲突
包含同一候选的至少两个不同有效证据 ID、原因和从实际绑定资料生成的版本范围。冲突不能
支持「满足」或「不满足」的确定结论。

SQLite schema v2 为运行增加 input_version，已有 v1 数据库打开时补入默认值 1。用户改变
输入会递增项目版本并阻止旧任务提交。步骤提交校验 owner、attempt、租约、输入指纹、输入
版本和当前条件版本。来源、条件与报告版本不可覆盖。

模型请求在网络调用前预留稳定调用 ID，完成后更新同一记录；失联请求标为 unknown 并继续
占用预算。fixture 模式始终显式标注，固定快照与脚本响应不能当作真实模型验证。

## 审查修复 {#review-repairs}

`Comparison.recommended_candidate` 是可选的候选 ID 或 null，旧记录默认为 null。有硬条件
时，只有每项硬条件均为 meets 的候选可以被选中。校验器和渲染器从该字段生成推荐文本，
未绑定或不合格的推荐改为暂不选择。每项确定的硬条件判断都需要维度与该条件 ID 一致的
直接证据。这些检查约束数据结构，引文是否支持结论、复合事实是否成立仍需语义复核。
推荐与硬条件表格共用汇总规则。只有偏好时保持无法确认，补充的偏好说明不会抹掉直接
的 meets 判断；任何 unknown 或 fails 判断仍阻止该条件对应的推荐。

报告版本可增加 `origin`、`derived_from_report_id` 与 `human_semantic_review_completed`。
`agent_editorial_correction` 在 Markdown 中明确标注并保留原模型版本，不计入原始模型判分。

清洗后的来源正文不变时，保留原始不可变快照、地址与版本。分支移向新提交只更新来源的
`observed_version`、`observed_url`、ETag 和检查时间，不使证据过期，也不请求模型。元信息
排除星标、fork、issue 计数及推送时间。HTML 正文中的页眉保留，显式隐藏文字与更新时间
装饰被移除。解析提交时只请求 GitHub 的 SHA 表示，依据
[GitHub 提交 API](https://docs.github.com/en/rest/commits/commits#get-a-commit)。

WebSocket 研究事件增加 `kind: research`、`project_id`、`run_id`、`stage` 和持久化的项目内
`sequence`，保留旧的 `type: research` 以兼容现有客户端。事件只提示状态变化，不携带需要
直接应用的状态增量。客户端收到事件或重新连接后重新拉取完整状态；运行中每两秒轮询，
空闲时每五秒轮询。项目详情增加可选 `tracking_blocked`，提示暂停或待处理运行阻挡跟踪。

事件增加可选的 `affected_constraint_refs`，包含由已校验的受影响决策 ID 推导出的条件依赖。
静默规则匹配来源、影响级别、条件版本、变化类型与排序后的受影响条件 ID。旧的宽范围
规则及旧条件规则不再压住新提醒；变化接口将过期规则返回为禁用。原动作路径继续可用，
网页将可选纠正引文的空值发送为 null。

时区名称限制为 100 个字符，只有记录不存在的专用异常会变为 404。错误诊断使用带下一步操作
的固定中英文文本，不保存异常原文和本机路径。临时错误最多重试三次，未提供 Retry-After
时采用指数退避；证书校验失败不重试。升级已有研究数据库前，保存一致的
`research.pre-v<target>.*.db` 备份。

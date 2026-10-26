# 状态与用量的数据来源

整理日期：2026-10-10。通信层次见 [架构文档](/Users/bytedance/personal/codex-activity-monitor/dev-history/01_initial_experiment/architecture.md)。

## 1. 先区分需要观察的东西

| 信息 | 含义 | 最合适的来源 |
| --- | --- | --- |
| 生成进度 | 模型输出是否有新增量到达 | 上游接收层日志；正文也可用 app-server delta |
| 工具生命周期 | 工具准备调用、等待审批、实际执行、完成 | app-server 完整事件；hooks 提供部分阶段 |
| 持久化历史 | 已产生的消息、结果、文件修改 | sessions / archived_sessions |
| token 用量 | 某次响应或累计请求用了多少 token | 服务端 usage、会话用量记录 |
| 账户限额 | 某个账户窗口用了多少、何时重置 | `account/rateLimits/read` 等账户接口 |

token 用量、实时生成速度、当前上下文大小、账户限额是四个不同指标，不能相互替代。

## 2. Desktop ↔ app-server 完整事件

**官方契约 + 部分实测**：常见事件包括：

| 事件 / 项目 | 可用于展示的信息 |
| --- | --- |
| `turn/started`、`turn/completed` | 回合开始、完成、中断或失败 |
| `item/started`、`item/completed` | 某项活动的生命周期与类型 |
| `item/agentMessage/delta` | 正文新增文本 |
| `commandExecution` | 命令、工作目录、状态、退出码、耗时 |
| `item/commandExecution/outputDelta` | 命令 stdout / stderr 增量 |
| `mcpToolCall` | MCP server、工具名、参数与结果 |
| `fileChange` | 补丁目标、差异、应用状态 |
| `turn/diff/updated` | 当前回合的汇总 diff 快照 |
| `thread/tokenUsage/updated` | 用量更新，不等于每个生成 token 的通知 |
| 审批 / 用户输入请求 | 需要用户操作的等待状态 |

它们描述后端工作，并不保证暴露所有上游生成阶段。特别是外层 `exec` 的长输入生成过程，本次 app-server 观察客户端没有收到工具参数 delta，而上游接收层持续有数据。

默认 stdio 中可以通过透明转发器复制完整 stdout 消息；已存在的监听地址则可以通过观察客户端读取。两者都必须对准 Desktop 实际使用的后端。

`item/fileChange/patchUpdated` 曾在隔离配置实验中验证，属于特定版本、模式和开关下的能力。不能将它视为所有文件编辑都会产生的事件。`turn/diff/updated` 的快照也不能直接累加字节数。

安装包核查发现，新增 / 删除行数计数和数字动画仍存在，显示还受界面模式、是否已有 diff 等条件影响。缺少补丁预览事件会使生成阶段没有行数增长，但目前没有证据确定 OpenAI 在某次更新中主动取消了该功能。diff 行数也不是 token 数。[动画与显示条件核查](/Users/bytedance/personal/codex-activity-monitor/analysis/file-edit-progress/FINDINGS.txt)

参考：[官方 app-server 文档](https://learn.chatgpt.com/docs/app-server)、[上游与 app-server 对照实验](/Users/bytedance/personal/codex-activity-monitor/analysis/upstream-stream/verification.json)。

## 3. app-server ↔ 模型服务的接收层日志

**实测**：隔离后端启用接收日志后，观察到 `response.custom_tool_call_input.delta`，字段包括 `delta`、`item_id`、`output_index`、`sequence_number`，没有 token 数量字段。

这条记录适合回答“工具参数是否仍在生成”。正文事件、reasoning 摘要事件也应分别聚合，避免混成一个没有明确含义的速度。

### 已验证的长工具输入

- 2,367 条输入 delta，合并后 10,068 个字符 / UTF-8 字节。
- 首尾 delta 相隔 12.328634 秒，区间接收均速约 816.64 字符/s。
- 单条 delta 为 1–40 字符，不能视为固定 token 数。
- 合并结果与上游完成项、持久化工具输入逐字一致。
- 内层 Python 命令完成并通过自检。

这里统计的是接收速度，不是服务端内部计算速度。传输、缓冲和日志处理都会影响到达时间。整个 trace 文件约 48 MB，其中包含重复诊断、预热和其他消息，文件增长速度不能当作生成速度。

### Reasoning 的边界

`summary=auto` 可以请求受支持的推理摘要，它不请求完整隐藏思考链。

**实测**：xhigh 实验收到两段摘要 delta，共 60 字符；完成 usage 报告 443 个 reasoning tokens。没有连续 reasoning token 计数。`encrypted_content` 只在开始和结束看到两个大小快照，不能用于实时思考速度。

reasoning 开始后曾有约 3.88 秒没有上游新事件。无事件只能说明观察端没有收到数据，不能证明模型停止计算。

隐藏 reasoning 内容与提供进度计数是两回事。当前接口缺少所需计数；未来若服务端提供计数或心跳，仍可在不暴露内容的情况下监控进度。

参考：[官方 delta 字段](https://developers.openai.com/api/reference/resources/responses/streaming-events#response.custom_tool_call_input.delta)、[实验记录](/Users/bytedance/personal/codex-activity-monitor/analysis/upstream-stream/FINDINGS.txt)、[分析脚本](/Users/bytedance/personal/codex-activity-monitor/analysis/upstream-stream/analyze_trace.py)。

## 4. sessions 与其他本地文件

### sessions / archived_sessions

当前默认数据目录下，聊天记录位于 `/Users/bytedance/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`；归档记录可位于 `archived_sessions`。实际根目录应按 `CODEX_HOME` 和环境识别。

**本机只读检查**：当前聊天记录中实际存在：

| 记录 | 内容及用途 |
| --- | --- |
| `session_meta` | 聊天与运行环境元数据 |
| `turn_context` | 当时模型等上下文信息 |
| `response_item` | 消息、完整工具输入、工具输出、reasoning 项目等 |
| `event_msg: token_count` | 累计用量、最近一次请求用量、限额快照 |
| `token_usage_record` | 带 response / thread / turn 身份的用量记录 |
| `event_msg: item_completed` | 已完成的命令、MCP、文件修改等项目 |

常见用量字段包括 `input_tokens`、`cached_input_tokens`、`output_tokens`、`reasoning_output_tokens`。不同版本和记录类型的字段可能不同。reasoning token 是输出用量的一部分，不应在总输出之上再加一次。

采集时应对新增完整 JSONL 行做增量读取，处理未写完的末行、文件移动、归档和历史重放。`total_token_usage` 是累计快照，不能把每一行累计值相加。`token_usage_record` 与 `token_count` 可能记录同一次响应，必须去重；恢复、压缩和子代理历史也会影响计数。

### 为什么文件增长不能替代实时事件？

当前本地公开源码的持久化策略过滤 `item_started`、正文 delta、命令输出 delta、补丁预览等瞬时事件；已完成项目会携带开始和结束时间。

所以，尾读 sessions 能较快知道“刚完成了什么”，但不能仅凭历史中的 `started_at_ms` 判断“现在什么刚开始”。外层 `exec` 的调用与输出还可能包含 yield / wait，简单配对也不能保证恢复所有内部执行状态。

参考：[持久化策略](/Users/bytedance/playground/codex/codex-rs/rollout/src/policy.rs:134)、[异步记录写入](/Users/bytedance/playground/codex/codex-rs/rollout/src/recorder.rs:1048)。公开源码快照与安装二进制的区别见索引；上述字段同时有本机会话检查证据。

### 其他文件

- `history.jsonl`：主要用于输入历史等，不能作为完整 agent 事件流。
- `config.toml`、模型目录 / 缓存：解释模型和有效工具入口，不代表正在发生的活动。
- SQLite 状态 / 诊断数据库：可辅助定位 thread、状态和 trace，路径与 schema 依版本变化，不作为当前方案的首选实时契约。
- `auth.json` / 系统凭据存储：认证用途，不是进度数据。常规采集器不需要保存其中的令牌。

## 5. Hooks

**官方契约**：Codex 可在配置层旁发现 `hooks.json`，或读取 `config.toml` 的 hooks 配置。支持用户级、项目级和插件 hooks。

| Hook | 对监控的价值 | 解释边界 |
| --- | --- | --- |
| `UserPromptSubmit` | 标记用户提交后的回合起点 | 不代表模型已经开始输出 |
| `PreToolUse` | 工具名、参数、调用 ID；即将调用 | 之后可能还要等待审批或调度 |
| `PermissionRequest` | 进入权限处理阶段 | 不是完整审批结果事件流 |
| `PostToolUse` | 支持的工具产出结果 | 不保证所有异常、取消都有成对回调 |
| `Stop`、`Interrupt` | 结束或中断的补充信号 | 按版本和主 / 子代理语义解释 |
| `SessionStart` | session 身份、transcript 路径 | transcript 格式本身不是稳定 hook 契约 |

输入可带 `session_id`、`turn_id`、`tool_use_id`、`tool_name`、`tool_input`，PostToolUse 还可带 `tool_response`。具体字段以事件 schema 为准。子代理 hook 的 session 字段可能指向父 session，不能只凭这个字段合并所有工作。

Shell / unified exec 通常匹配 `Bash`；`apply_patch`、MCP 和多数本地函数工具有 hook 路径。hosted WebSearch 等不走同一路径，特殊工具也可能绕过默认 hook。`write_stdin` 不会为已经批准的命令重新触发 PreToolUse，命令真正完成后的 PostToolUse 可能在后续轮询时到达。

**本机只读验证**：隔离 app-server 的 `experimentalFeature/list` 返回 `hooks` 为 stable、enabled、defaultEnabled。尚未安装监控 hook，也尚未验证当前 Code Mode host 下全部内部工具的回调覆盖。

用于监控的 handler 只应报告事件并正常退出，不返回模型上下文、改写参数或决定允许 / 拒绝。command hooks 默认同步，可选择异步；异步回调可能延迟、乱序，且结束时未完成任务可能被取消。非托管 hook 有 Codex 自身的信任流程。

因此 hooks 是正式支持的扩展入口，但不是零开销、无遗漏的旁路探针，也没有逐 token / 工具参数 delta 回调。

参考：[官方 Hooks 文档与覆盖说明](https://learn.chatgpt.com/docs/hooks#tool-coverage)、[后台 hooks](https://learn.chatgpt.com/docs/hooks#run-hooks-in-the-background)、[工具分发中的 hook 路径](/Users/bytedance/playground/codex/codex-rs/core/src/tools/registry.rs:697)。

## 6. 账户限额与开源监控工具

### 限额可以独立查询

`account/rateLimits/read` 查询登录账户的限额；`account/rateLimits/updated` 通知变化。它们回答账户窗口状态，不依赖某个活跃 thread 被当前进程加载。

所以，为查询同一账户额度另开一个 app-server 是可行方向；这个新实例不能因此自动观察 Desktop 的当前工具执行。额度还可能包含其他机器或云端工作的使用情况。

会话中的 `rate_limits` 可以作最后已知快照，但必须展示采集时间。窗口名称和期限应使用服务端返回值，不能硬编码“primary 必定是 5 小时、secondary 必定是每周”。

参考：[官方账户接口](https://learn.chatgpt.com/docs/app-server)。

### 开源项目的实际来源

| 项目 | token / 历史来源 | 限额来源或其他处理 |
| --- | --- | --- |
| ccusage | 扫描 Codex sessions / archived_sessions，解析 token_count 等 | 按模型与价格数据估算 API 等价费用 |
| CodexBar | 本地会话中的 token_count / token_usage_record 等，做历史汇总与去重 | 支持 OAuth usage 接口、CLI RPC，以及显式选择的网页来源等 |
| codexusage / cdxusage | 优先 sessions 的真实 token_count | 旧记录缺少用量时，可用 history 输入文本估算；不能视为精确用量 |

CodexBar 的 OAuth 路径可请求 `GET https://chatgpt.com/backend-api/wham/usage`；CLI 路径会使用 `codex app-server`，初始化后读取账户与限额。其网页来源和认证支持也会随项目版本变化。

这些工具“实时刷新用量”，不意味着它们拿到了每个 token 的生成时间。它们多数读取请求完成后的用量快照或定期查询账户窗口。按 API 价格计算的金额也不是 ChatGPT 订阅实际账单或额度余额。

参考：[ccusage 数据来源](https://ccusage.com/guide/codex/)、[CodexBar 数据来源](https://github.com/steipete/CodexBar/blob/main/docs/codex.md)、[codexusage 数据来源](https://github.com/linpatr/codexusage#data-source)。

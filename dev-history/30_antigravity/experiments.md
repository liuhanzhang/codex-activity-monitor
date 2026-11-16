# Antigravity Desktop 实验与结果

2026-11-14 至 11-15，北京时间。Apple Silicon macOS，独立版 Antigravity Desktop 2.32.1。模型样本使用 Gemini 4 Argon (High)，提示词为合成内容。以下时间来自同机观测，相邻间隔按有不同正文的更新计算。

## 状态流、落盘和 Agent 活动

`StreamAgentStateUpdates` 是 Language Server 的按会话状态订阅。实测传输是 HTTP/2 上的 gRPC-Web JSON。消息带会话 ID、轨迹 ID 和步骤索引。正文是同一步骤反复发送的 `plannerResponse.response` 累计快照。统计正文时按步骤和内容去重，排除重复、纯状态和完成后重发的帧。

| 11 月 14 日样本 | 观察方式 | 正文更新 | 相邻间隔中位数 | 最长间隔 | 间隔 ≤ 1 秒 |
| --- | --- | ---: | ---: | ---: | ---: |
| A 英文长回复 | 观察桌面已有订阅 | 124 | 579 ms | 1,537 ms | 118/123 |
| C 英文并行回复 | 观察桌面已有订阅，切走时结束 | 65 | 421.5 ms | 1,438 ms | 63/64 |
| D 英文并行回复 | 观察桌面已有订阅 | 78 | 593 ms | 1,366 ms | 73/77 |
| E 英文长回复 | 独立订阅 | 83 | 645 ms | 1,172 ms | 81/82 |
| F 中文长回复 | 测试 Hook 发现会话，独立订阅 | 45 | 203.5 ms | 322 ms | 44/44 |

- **正文与落盘时点。** A 在首次 `PreInvocation` 后 15.509 秒出现第一段正文。以同一时点为基准，JSONL 在 91.496 秒才观测到首条模型回复，状态为 `DONE`，此前只见用户消息。该条记录的 3,674 个输出 token，在 SQLite 终态数据中拆为 3,271 个回复 token 和 403 个思考 token。可从 JSONL 读取已经落盘的内容，正文逐秒速度需要状态流。
- **会话与后台。** C、D 的正文更新重叠约 17.4 秒，能按会话区分。切走 C 时桌面取消了自己的订阅，C 的 Agent 仍在执行。E 的独立订阅在桌面取消原订阅后继续收到 39 次正文更新和最终空闲状态。F 使用测试工作区的 `SessionStart` 获得会话 ID，独立订阅在切走页面后也收到了正文、终态用量和空闲状态。11 月 15 日另行复测了只读查询会话的路径。
- **实际用量。** E、F 的采样器只在模型步骤变为 `DONE` 时观察到 `metadata.modelUsage`。F 的 1,125 个回复 token 和 4,027 个思考 token，合计 5,152，与 SQLite 和 JSONL 同轮记录一致。A 至 D 的采样器没有统计中间帧的这个字段。长回复里相邻帧可以相隔一秒以上。从首个正文更新对齐固定一秒窗口，C、D、E、F 分别有 28、48、52、9 个完整窗口，本次窗口内均有新正文。窗口结果随对齐点变化。
- **默认桌面配置的补测。** 11 月 15 日在原 Desktop 提交了一个英文合成样本，观察到 84 个状态帧和 19 次正文变化，正文相邻间隔中位数 225 ms，P90 317 ms，最长 368 ms。终态有 492 个回复 token 和 735 个思考 token。默认日志记录了上游 SSE 请求，逐分片时点来自后续对照实验。

### 命令阶段和详情

B 在独立测试工作区运行一条只读命令，其中等待三秒后读取一段测试文本。下表以该轮首次 `PreInvocation` 为零点。JSONL 每 100 ms 轮询一次，表中时间是首次读到记录的时间。

| 相对时间 | 观察到的信号 |
| ---: | --- |
| 10.347 s | 状态流中步骤 2 为 `CORTEX_STEP_TYPE_GENERIC`，状态 `PENDING` |
| 10.365 s | 同一 `GENERIC` 步骤进入 `RUNNING` |
| 10.425 s | `PreToolUse`，同一会话、步骤 2、`run_command` |
| 16.264 s | `PostToolUse`，同一会话和步骤 |
| 16.271 s | 状态流中同一 `GENERIC` 步骤进入 `DONE` |
| 16.288 s | 读到步骤 2 的已完成 JSONL 记录 |
| 16.323 s | 首次 `PostInvocation` |
| 16.378 s | 下一次 `PreInvocation` |
| 26.200 s | `Stop`，`fullyIdle=true` |

状态流可提供该步骤的等待、运行和结束边界。测试 Hook 以同一会话和步骤索引确认它是 `run_command`。采样器只保存了 `generic` 的外层字段，尚未验证内层是否提供工具名或参数。B 的 `PostInvocation` 在工具结束后才出现，因此两条 Invocation Hook 之间在该样本中包含了工具耗时。 [Antigravity Hooks 与 transcript 路径](https://antigravity.google/docs/hooks/)

完成后，对同一会话调用 `GetCascadeTrajectorySteps`，用 `stepOffset: 2` 从该步骤开始读。首个返回步骤为 `CORTEX_STEP_TYPE_RUN_COMMAND`，其 `runCommand` 含 `commandLine`、`combinedOutput` 和 `exitCode`。transcript 中也可读用户消息、模型正文、用量和 `tool_calls`。工具运行时查询能否取得具体类型和参数，以及能否连续观察输出进度，尚未验证。

## 上游 SSE 定位与对照实验

默认运行的 Language Server 日志出现 `v1internal:streamGenerateContent?alt=sse` 请求记录。六个受控会话的首次请求记录与首个 `PreInvocation` 相隔约 0.9–1.5 秒。该版本 Go 二进制的 `codeassistclient.ProcessStreamChunks` 在成功解析分片、向后分发之前，按 `stream_helpers.go` 的 verbosity 2 级别打印 `Cortex API Chunk`。因此 `--vmodule=stream_helpers=2` 可提供上游 SSE 的本机处理时点。日志点有成功解析条件，SSE 事件覆盖范围以实测为准。默认日志仅足以确认请求路径。本地 `DevService/Dev` 探测返回了前端页面，未验证运行中切换详细日志的方式。

用同一安装包的 Language Server 启动独立后端，配临时数据目录和日志参数。最初几次能建立会话订阅，Google 登录恢复失败，因而没有真实模型分片。一次按授权重启原桌面后也未恢复登录，回到普通启动时现象仍在。该版本的 `Ineligible` 资格校验分支可将待验证凭据留在旧进程中并跳过持久化。这个机制与旧进程有凭据、新进程恢复失败的现象相符。这次登录失效的完整原因未核实。

11 月 15 日 20:00–20:04，单独给实验后端完成 Google 授权并刷新模型目录后，成功采到同一请求的两路数据。测试后端随后退出，原 Desktop 登录复核有效。这个授权流程只用于让实验实例发起模型请求。只读本机订阅使用 Language Server 的临时本地 CSRF 值。

### 同一回复的两路更新频率

按模型响应 ID 拼接上游正文，与状态流最后的正文快照比较，比较时去除首尾空白。两轮最终正文吻合。上游时点取日志时间，下游取同机收到状态帧的时点。标题、思考摘要及其他非正文事件未纳入下表。

| 样本 | 正文更新，上游 / 状态流 | 中位间隔 | P90 间隔 | 最长间隔 | 间隔 > 1 秒 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 英文 | 84 / 84 | 459 / 460 ms | 603 / 604 ms | 1,024 / 1,024 ms | 1 / 1 |
| 中文 | 108 / 108 | 152 / 152 ms | 204 / 204 ms | 239 / 239 ms | 0 / 0 |

中文 108 个分片的累计字节数与状态流累计快照逐帧吻合，状态流接收比上游日志晚 0–5 ms，中位数 2 ms。英文最初的解析器漏识别本机日志前缀，随后用同轮原始日志和已完成正文补算了上游统计。英文只保留状态流间隔统计，逐帧延迟结论仅来自中文。两轮中使用 SSE 日志与使用状态流的正文更新频率基本相同。

### Token 计数和分词误差

两轮每个可解析的正文分片带单调增加的 `usageMetadata.candidatesTokenCount`，每片新增量中位数为英文 25 和中文 24。对每秒边界前最后可见的计数取差，可得到该窗口的上游到达 token 数。英文最后可解析计数 2,175，与终态相同。中文最后可解析计数 2,725，终态为 2,729，末尾还有一条不完整日志，无法核对其最终计数。客户端时间的粒度由分片、传输和接收时点决定。 [Gemini 用量字段](https://aistudio.google.com/api-reference/generate-content#Resource:GenerateContentResponse)

对相同的最终正文离线复算，并以状态流终态 `responseOutputTokens` 为基准。每秒的误差另以 SSE 计数增量为基准，从第一段正文起只比较完整窗口。

| 样本 | 终态正文 token | `o200k_base` | Gemma 4 | 每秒平均绝对误差，`o200k_base` / Gemma 4 |
| --- | ---: | ---: | ---: | ---: |
| 英文，38 个一秒窗口 | 2,175 | 2,178（+0.14%） | 2,187（+0.55%） | 1.68 / 0.55 token/s |
| 中文，17 个一秒窗口 | 2,729 | 3,418（+25.25%） | 2,955（+8.28%） | 40.47 / 13.35 token/s |

11 月 14 日另一中文样本 F 用 `o200k_base` 得到 1,338 个正文 token，较终态高 18.9%。9 个完整窗口估算为 113–160 token/s，中位数 143 token/s。同批四个长英文样本的正文终态误差为 −4.9% 至 +5.1%。

Gemma 4 的复算关闭了自动添加特殊 token，Gemma 3 在表中两份正文上的结果一致。上游 `modelVersion` 为 `gemini-4-argon`，Google SDK 将 Gemini 3.5 Flash 等指定公开模型映射到 Gemma 4，未列出 Argon 的映射。Gemma 4 在这轮中文样本中减小了误差，剩余差异与实际分词规则的关系未确认。 [Google SDK 的分词器映射](https://github.com/googleapis/python-genai/blob/main/google/genai/_local_tokenizer_loader.py#L47-L55)

### 思考摘要和思考用量

状态流提供用户可见的思考摘要，F 样本摘要的更新间隔中位数约 4.7 秒。SSE 中也观察到标记为 `thought` 的摘要分片。11 月 15 日的中文样本中，SSE 在正文开始前报告 14 次不同的 `thoughtsTokenCount`，相邻变化间隔中位数 4.142 秒，最后的 4,002 与终态一致。该信号可以观察思考用量的分批上报，稳定的逐秒思考速度和内部思考内容未得到验证。

## 只读发现与订阅复测

11 月 15 日在正在运行的原 Desktop 2.32.1 复测。进程未开启 `--vmodule=stream_helpers=2`。验证本机进程身份、回环端口和包内证书后，用该进程的本地 CSRF 值连接。

1. `GetAllCascadeTrajectories` 返回 14 个会话摘要，均为空闲。其中先前的 6 个测试会话均可找到，对应 6 份 transcript 均可读。
2. 从目录选一个测试会话，独立调用 `StreamAgentStateUpdates`，收到 HTTP 200 和该会话的初始状态，包含两个步骤。此次复测没有安装 Hook、附加调试器或重启 Desktop。
3. 用 `GetCascadeTrajectorySteps` 查询既有命令测试，确认详情字段可读，并与本地 transcript 对照。

该复测闭合了已有会话的只读发现、订阅及详情查询。动态发现尚在生成的新会话、跨重启恢复订阅、所有工具类型及子 Agent 关系需要继续验收。

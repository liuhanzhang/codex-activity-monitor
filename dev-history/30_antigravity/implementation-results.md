# Antigravity 接入实现与验收

2026-11-15，源码基线 `d319ffb`。实机使用 Apple Silicon macOS 和独立版 Antigravity Desktop 2.32.1。Monitor 的 Antigravity 开关在「设置 → 通用 → Agent 接入」，可以在 Codex 接入关闭时单独使用。

## 实现

- 采集器验证本机回环监听者是同一用户的 Antigravity Language Server，并使用安装包内已验证的 TLS 证书和进程的本地 CSRF 值。先读 `GetAllCascadeTrajectories`，再订阅 `JetboxSubscribeToSummaries` 发现新会话和状态变化。目录订阅失败时可用只读查询补接。每个会话用独立的 `StreamAgentStateUpdates` 订阅。 [本机连接](../../src/codex_activity_monitor/antigravity_transport.py) · [目录与订阅](../../src/codex_activity_monitor/antigravity_source.py)
- 正文快照转换为累计接收量，复用 Monitor 的一秒计数窗口。重复正文去重，中途接入、重连和正文改写重新建立基线。已匹配的 Gemini 模型在本地用 Gemma 4 估算，其余模型回退为 `o200k_base` 估算。终态用量按会话、回合和步骤更新同一条记录。每轮平均速度使用有边界的模型请求和实际输出用量，时间取模型步骤的 `createdAt` 到 `finishedGeneratingAt`。实测两条模型步骤的 `startedAt` 接近完成时点，作为起点会把耗时计得接近零。 [状态投影](../../src/codex_activity_monitor/antigravity_projection.py) · [分词资产](../../src/codex_activity_monitor/antigravity_tokenizer.py)
- 步骤状态驱动实时活动。流中的通用步骤可用 `metadata.toolCall.name` 显示已知工具名。活动详情通过只读轨迹查询和 transcript 补齐，完成时带具体类型的步骤更新同一条活动。子 Agent 的会话归属使用 `trajectoryMetadata.parentConversationId`。跨回合用父轨迹的 `invokeSubagent.results[].conversationId` 绑定到实际发起的回合。历史读 JSONL，Monitor 自有索引保留已验证的子 Agent 调用关系。 [步骤解释](../../src/codex_activity_monitor/antigravity_records.py) · [历史读取](../../src/codex_activity_monitor/antigravity_history.py)

## 实测

| 场景 | 结果 |
| --- | --- |
| 正常运行的 Desktop 上新建会话 | `JetboxSubscribeToSummaries` 在创建请求发出后约 74 ms 推送新 ID。订阅在约 114 ms 收到首个用户步骤，早于模型正文。 |
| 两个会话并行生成 | 隔离 Monitor 采集器同时观察真实的英文和中文测试回合。两轮在完成前都有正的估算速度，计数器使用 `gemma4`。英文终态输出 3,082，中文 1,339，输出和思考用量均与原轨迹一致。中文命令详情可查。可计算的平均速度样本数分别为 1 和 2。 |
| 工具等待、运行、完成 | 同一命令索引的通用步骤依次报告等待和运行。运行中轨迹查询的 `metadata.toolCall` 有 `id` 和 `name`。完成帧才提供具体命令类型、命令行、输出和退出码。另一次测试的非零退出码 127 在 Monitor 中显示为工具失败，后续模型回复正常完成。 |
| 生成中取消 | 在合成会话出现正文速度后取消，Monitor 显示该轮已中止并清除实时速度。 |
| 其他工具 | 运行中观察到 `ask_question`。对已完成记录验证了读文件的路径和行范围、文件操作结果、问答内容以及子 Agent 调用参数和结果。详情只展示接口返回的字段。 |
| 子 Agent 回看 | 从现有 Desktop 的只读目录找到四个子 Agent，并与父轨迹的四个调用结果对照。四条子轨迹都出现在对应父回合的时间线，能读取各自的历史步骤。 |
| 独立开关和恢复 | 用隔离 Monitor 数据目录打开 Antigravity，Codex 配置未启用。两次启动采集器都恢复了 19 个可见会话。关闭 Antigravity 后采集器退出，隔离目录可以清理。另用原版 Language Server 和临时数据目录复测，替换实例后重新识别了新的 PID 和 TLS 端口。 |
| 生成中补接 | 正文正在输出时重启隔离 Monitor。重新接到同一会话后以已有正文为基线，仅计量后续新增内容。结束后报告的 5,277 个输出 token 与轨迹接口一致，未因重启重复累计。 |
| 两个来源共同采集 | 同一个隔离采集器接收真实 Antigravity 状态和合成 Codex bridge 事件。关闭 Antigravity 后，Codex 连接仍然有效。 |

原 Desktop 的实测使用独立订阅和合成会话。PID 和端口切换发生在临时 Language Server 中。真实工具的审批交互和其他 Antigravity 版本仍需实际覆盖。工具等待以可观察的步骤状态显示。

Python 单元测试 606 项和 Swift 测试 168 项通过。独立 DMG 验证覆盖签名、隔离安装、旧 Codex 路径和 Gemma 4 离线计数。另从打包后的后端在隔离 Monitor 数据目录开启 Antigravity，连接原 Desktop 并读取会话目录，随后验证关闭和清理。

最终 DMG 的第一次验收在 Codex 历史回退用例中收到缺少 `state` 的查询响应。复验同一 DMG 通过。类似的隔离历史查询间歇失败已记在项目待办中。 [待办索引中的 RH07](../TODO.md)

## 工具参数生成和执行时间复测

- 22:55 开始的 Gemini 4 Hydrogen 回合包含 60 次工具调用。原实现用 JSONL 的创建时点覆盖了执行开始，橙色段最多提前 15.61 秒。修正后使用轨迹的 `startedAt` 和 `completedAt`，JSONL 补齐缺失的生命周期字段。已完成的会话按所选回合从本机轨迹补回精确时点。
- 60 次调用均带模型侧的 `createdAt` 和 `finishedGeneratingAt`，跨度中位数约 223 毫秒。用这组时间戳显示模型行的参数生成段，并关联工具执行段。回放原状态流和从 JSONL 冷加载两条路径均与 60 组原始时间戳吻合。三个非零退出码均标为失败，被取消的命令保留中止状态。
- 用同一模型生成一条含 4,096 字符合成参数的只读命令。59 个状态帧中，参数在 `GENERIC/PENDING` 首次完整到达，后续 `RUNNING` 和 `DONE` 保持同一参数。模型步骤记录的工具参数生成区间约 253 毫秒。当前监控使用这些时点绘制生成阶段，参数实时 tok/s 留空。

## 已完成回合的模型时间轴复测

2026-11-16 13:19，北京时间。同一会话的第二轮在实时缓存清理后只剩 JSONL 的模型步骤创建时间。上一版从本机轨迹补齐了工具步骤，9 个思考段却缺少结束时间，最终回复也缺少时间范围。时间轴只到 82 秒，平均速度没有可用样本。

- 现按所选回合从本机轨迹恢复模型和工具步骤。使用 `createdAt` 与 `thinkingDuration` 确定思考区间。8 次带工具调用的模型请求中，算出的思考结束与首个工具生成开始相差均小于 0.03 毫秒。最终回复使用思考结束与 `finishedGeneratingAt`。
- 冷加载已核对 9 个思考段、12.53 秒的最终回复、21 个参数生成段和 21 个执行段。完整区间为 114.14 秒。9 次模型请求共输出 12,128 token，累计请求耗时 110.81 秒，平均 109.45 tok/s。已结束回合中 JSONL 遗留的 `RUNNING` 状态按当前可观测证据处理。

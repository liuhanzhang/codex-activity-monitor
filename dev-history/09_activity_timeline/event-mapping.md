# 活动时间线：事件映射

日期：2026-10-17。状态：已按指定版本来源实现；实际通知与保守降级见[来源验证](source-validation.md)。来源与限制见[证据](evidence.md)。

## 1. 最小展示映射原则

保留原始 `scope / thread_id / turn_id / item_id / call_id`、事件类型和来源。展示层只负责分类、生命周期合并、明确身份关联、镜像去重与渲染；不生成“分析项目／实现功能”等推测性工作阶段。

一个 item 的 start、delta、completed 更新同一阶段；delta 不是独立时间块。一个工具调用若有独立的模型输入生成与执行生命周期，可以有两个阶段块，但没有明确关联时不能强行合并。

`scope` 优先使用已确认的 Codex home／来源域。backend 连接 ID 是观察实例，跨重启可能变化，不能单独作为持久身份。home 不明确时保留实例级 scope 和质量标记，不能把不同源的相同字符串 ID 直接合并。

## 2. 生命周期与轮次

| 输入 | 时间线含义 | 处理规则 |
| --- | --- | --- |
| `turn/started` | 新轮开始、默认轮选择候选 | 使用原 turn ID，不创建本地替代轮次 |
| `turn/completed` | 本轮生命周期结束 | 更新轮状态及时间点；不结束仍有独立运行证据的工具或子智能体 |
| `turn/steer` 请求及成功／失败结果 | 向当前 turn 提交中途输入 | 保留请求 ID、目标 turn 和接受状态；成功仍是原 turn，失败不是已接收输入 |
| `userMessage` item | 已记录的用户输入 | 首条在轮标题可访问；后续同轮输入以时间点表达，不增加输入行 |
| `thread/status/changed`、连接与观察缺口 | 聊天状态、等待或数据可靠性 | 不根据状态字符串反推某个具体工具已完成 |
| `thread/archived / deleted / reverted` 等 | 历史来源变化 | 失效相应索引／游标，按真实来源重新读取；不修改 Codex 历史 |

`turn/steer` 请求与结果的必要元数据已补齐。指定版本的实际 canonical userMessage 缺少 clientId，因此首版仅用 canonical 用户消息生成输入标记；请求记录不再额外画点。没有明确请求与消息关联时，不按正文相同或时间最近配对。错误请求不产生输入标记。

## 3. 模型轨

| 原始来源 | 阶段类别 | 开始／更新／结束 |
| --- | --- | --- |
| upstream `response.output_item.added/done`，item 为 `reasoning` | `reasoning` | item added→done；可见摘要 delta 更新可用内容，不按段落增加独立耗时块 |
| app-server `item/started`、`item/completed`，item 为 `reasoning` | `reasoning` | canonical 生命周期；与明确相同的 upstream 记录合并，保留时间来源 |
| app-server／历史 canonical `contextCompaction` | `contextCompaction`，模型轨 | 同一 item 的开始→完成构成过程；原 canonical 身份不变；缺边界保持未知，旧完成通知不补造开始 |
| upstream message／app-server `agentMessage`、`plan` | `text` | 相应 added／started→done／completed；正文与计划用同一模型子类别 |
| upstream `function_call`、`custom_tool_call` | `tool_input` | `response.output_item.added`→`done` 是输入生成窗口；arguments／input delta 更新同一块 |
| 明确 patch 工具的输入生成 | `file_edit_input` | 与该输入生成相同的生命周期，仅更改展示子类别，不另算一段耗时 |

有关流式增量：

- `response.output_text.delta` 对应正文内容。
- `response.function_call_arguments.delta`、`response.custom_tool_call_input.delta` 对应工具参数／代码生成。
- `response.reasoning_summary_text.delta` 及 app-server 可见 summary 通知用于可见摘要；不能据此声称得到完整内部思维链。
- source item、part／summary index 用于更新内容与去重，不默认把每个 part 分成一个时间块。
- 只有 final 或部分 delta 时，生成起点未知；不能用下一次工具开始时间或上一项完成时间补造生成起点。

### 文件编辑生成识别

复用 `tool_labels.py` 现有的明确 `apply_patch / functions.apply_patch` 身份，以及有界静态 Code Mode literal patch 识别。识别只用于分类与安全摘要，不运行代码、不求值变量、不把 patch 正文写入补充元数据。

当同一个 Code Mode cell 混合多种操作且没有分段生成边界时，保留一个 `tool_input`；可在详情说明包含可识别的编辑内容。不能把某次文件写入的工具执行时间反投影为模型的“编辑生成”时间。

## 4. 工具轨

| item／事件 | 显示 | 生命周期与注意事项 |
| --- | --- | --- |
| `commandExecution` | 命令执行／交互 | 使用确认的执行生命周期；仅有待执行／准备证据时保持等待标记，不冒充运行 |
| `mcpToolCall`、`dynamicToolCall`、`webSearch` | 工具调用 | 使用其 canonical 执行状态与时间；支持相互重叠 |
| `fileChange` | 应用文件改动 | 独立于 patch 参数生成；canonical 开始／完成建立实际应用边界 |
| `imageGeneration`、`imageView` | 对应工具活动 | 按可用生命周期显示，详情按实际内容能力提供 |
| `collabAgentToolCall` | 派发／交互／等待／关闭调用 | 这是父级调用本身，不是 child 完整工作区间 |
| programmatic exec 的外层调用 | 代码执行 | 外层程序可包含内部调用与等待；嵌套步骤有明确身份时可在详情查看，不把它们重新当成模型生成 |
| `functionCallOutput` 或 raw call output | 返回内容／关联资料 | 不是另一段模型生成，也不自动新建工具执行块；用于补全明确匹配的调用详情 |

`exec_command.yield_time_ms` 控制等待多久返回，返回 session／process 身份可以支持后续交互。不能仅凭工具名、等待参数或 `processId` 给工具贴“前台／后台”标签；固定工具轨接受真实重叠。

对持久进程，应区分一次调用／读取输出的区间与进程仍存活的状态。只有实际可观察的进程生命周期才能形成持续执行记录，不能把几次 `write_stdin` 调用之间的空档都宣称为已知 CPU 工作时间。真实隔离跨轮进程已验证原 turn 所有权和最终完成；见[来源验证](source-validation.md)。

## 5. 子智能体与跨轮

| 来源 | 用途 | 不能推出的结论 |
| --- | --- | --- |
| `collabAgentToolCall.senderThreadId / receiverThreadIds / agentsStates` | 明确派发或交互对象、辅助状态 | 调用完成不等于接收方工作完成 |
| `subAgentActivity.agentThreadId / kind` | child 身份或生命周期状态补充 | 单个状态事件不能恢复 child 内部全部轨迹 |
| child 自己的 `threadId / turnId / itemId` | child 的真实活动和轮次 | child turn ID 不应替换成父 turn ID |
| 已验证的 thread source／parent 关系 | 展示树结构 | 树的父子关系本身不足以将 child 所有轮次归入父级某一轮 |

关联保留两层：`parent_thread` 表达智能体关系；`dispatch / interaction` 记录父轮与 child 工作的明确关系。父轮视图引用 child 活动，不复制或改写 child 的 owner。

同一 child 被复用、多次 steer 或参与多个父轮时，按可证实的交互范围引用；范围缺失时显示状态和“关联不完整”。跨 scope、候选父级多于一个或关系成环时不强制挂靠。

跨轮的进程也保留最初来源 turn，并以明确 process／call 身份补充后续交互关系。当前轮可以有“前轮仍在运行”的导航引用，但不增加一个来源被改写的新执行块。

## 6. 状态标记

| 来源 | 展示方式 |
| --- | --- |
| canonical `userMessage` | 首条从轮次入口访问，后续用户消息画菱形；steering 请求不重复画点 |
| 审批／提问请求、`serverRequest/resolved` | 继续用于生命周期判断，不独立展示点；resolved 也可能是清理，不能当作已回答 |
| hook、重试、auth recovery、buffering、warning | 不显示时间点；其中 buffering 不是通用网络等待 |
| `contextCompaction` | 模型轨上的过程条，复用 canonical 身份与有效计时 |
| 旧 `thread/compacted` | 不额外画点，也不据此推算开始 |
| bridge gap／disconnect、collector restart | 保留质量提示，不画独立点，不把没有收到消息解释成工作停止 |
| 未识别但属于范围内的新 marker item | 保留来源／质量信息，不生成可点击但无正文的通用菱形 |

过滤发生在展示合流中，旧侧文件与新实时记录适用同一规则；隐藏项也不进入键盘、AX 和活动候选。现有内部工具继续展示，不增加外层 Code Mode 执行区间或专门问答响应适配。

## 7. 实时与历史合流规则

1. canonical 历史的 `(scope, thread_id, turn_id, item_id)` 优先恢复同一个 canonical item；raw upstream item 保留独立 source namespace。
2. `call_id`、明确 raw→canonical 映射或经过验证的协议关联可以连接不同来源阶段；不同命名空间的 `id` 不默认相等。
3. 不按正文／参数相同、文件路径相同、前后相邻或时间接近自动合并。无法关联时保留独立来源，标明未关联。
4. 对同一 item 的同一语义边界，历史提供有效 producer 时间时可补全实时接收时间；同时保留 provenance。不能用执行时间覆盖模型输入生成时间。
5. `delivery=async` 等明确镜像不重复计入独立工作；原记录仍可在详情解释。没有原始记录时说明来源是镜像，不能凭空创建原始工作轨迹。
6. 重复完成幂等，晚到 delta 不重开已终止阶段。仅补回开始时间可改善质量，不能抹掉明确的失败／中断状态。
7. 历史缺少生成起点时仍可展示 raw 参数内容，在详情说明时间不可恢复；不拿单条 JSONL 外层 timestamp 画出一段伪精确生成时长。

raw call ID 与 canonical item ID 的实际对应并不普遍成立，已见本机样本。首版依据[来源验证](source-validation.md)固定关联规则；未确认映射保留为独立阶段。

# 面向用户的进度展示：事件消费与展示决策

状态：2026-10-12，作为本轮实施的产品基准。实施前差异见 [逐项复核](event-implementation-review.md)，实施范围、验证与运行切换限制见 [实现结果](implementation-results.md)。本文的原有现状字段保留方案生成时的基线。

问题背景、下一步查证方法、实现顺序与验收条件见 [背景与查证 / 实现计划](background-and-implementation-plan.md)。本文保留逐项事件取舍，作为该计划的展示基线。

建议把 monitor 做成一眼能读懂的进度总览：显示当前在做什么、是否仍在工作，以及实时生成速度。协议事件在内部保证状态准确，界面把它们归并为少量用户能理解的动作。

本次逐项判断覆盖 87 种服务端通知、11 种服务端请求、19 种活动类型、10 种已接入模型流事件，以及 84 种 core 事件的观测路径，共 211 条分类记录。这些集合处于不同层次，不能相加理解成 211 种独立客户端事件。完整建议见本文附表及 [机器可读决策表](event-display-decisions.json)，当前实现事实见 [事件盘点](app-server-event-inventory.md)。

源码基线为 `/Users/bytedance/playground/codex` 的 `806d9732c974bc8a51b8317c1bd8985544fe627c`。CLI 协议及当前 monitor 的消费路径已核对；**当前 Desktop 对每一种事件的实际呈现尚未逐项验证**。以下展示规则是产品建议，不能当作已经实测的 Desktop 完整映射。旧版 Desktop 归档也不能证明当前版本的展示行为。

## 决策准则

- 展示 Desktop 中用户能理解的工作动作：生成回答、执行命令、修改文件、搜索、计划和子任务等。暂时无法确认 Desktop 呈现的长尾事件，先采用中性状态或内部跟踪。
- 审批、回答问题、登录和验证等操作由 Desktop 负责。monitor 不重复问题、理由、选项、链接、按钮或提醒，也不替用户响应。
- 自动审核与上下文压缩作为实际工作过程展示，分别使用“自动审核中”和“压缩上下文”；自动审核不是要求用户操作的审批。
- 相比 Desktop，默认新增的指标为实时生成速度。累计 token、输出字数、协议字段和内部机制不成为默认信息。
- 必须区分参数准备、实际执行、调用返回、后台运行和任务结束。界面可以简化，内部事实不能合并。
- 有真实事件才更新工作进展。时间流逝、传输心跳、配置变化和任意后台消息不能制造“稳步推进”的假象。

## 与日常进度最相关的结果（建议）

| 能力 | 是否消费 | 用户看到什么 | 关键边界 |
| --- | --- | --- | --- |
| 回合与任务边界 | 必须 | 准备开始、任务完成、已停止、任务失败 | 普通回合结束可显示完成；长期目标仍有效时区分“本轮完成”。后台进程有独立状态。 |
| 回答与可见思考摘要 | 必须 | 生成回答 / 思考中；有文本增量时显示生成速度 | 不复制正文与摘要，不将可见摘要速度称作完整推理速度。 |
| 工具参数生成 | 必须，沿用已接入模型流 | 准备命令 · `rg --fi…` / 准备操作 · 工具名 | 名称或命令前缀可识别后即时补充。准备不代表执行；不显示外层 `exec`、参数 JSON。 |
| 命令实际执行 | 必须 | 运行命令 · `pytest -q` | 审批前的 `item/started` 不足以证明启动；等待真实进程、输出等证据。 |
| 文件修改 | 必须 | 修改文件 · `engine.py`（另 2 个） | 只保留有界文件摘要，不复制 patch、diff 或审核入口。 |
| MCP / 动态工具 | 必须 | 调用工具 · 可读工具名 | 优先映射为搜索、读取、查询等已知动作；未知工具显示简短原名，避免猜测。 |
| 网页、图片 | 必须 | 搜索网页、查看图片、生成图片 | 展示活动生命周期，内容和结果由 Desktop 展示。 |
| 计划 | 必须 | 整理计划；简要详情可显示计划 2/4 · 当前步骤 | 步骤数来自结构化状态，不能当作整个任务完成百分比。 |
| 子任务 | 必须，先修正类型与身份 | 分配子任务、等待子任务；简要详情显示数量与已知状态 | 派发调用完成不等于子任务完成；父子镜像不重复计速。 |
| 上下文整理与明确程序等待 | 必须 | 压缩上下文 / 等待中 | `sleep` 是程序主动等待，不是用户待办；只收到完成事件时不反造进行中阶段。 |
| 人工 / 客户端审批、用户输入 | 必须，内部跟踪 | 全部前台工作确实被阻塞时：已暂停 | 不重复 Desktop 的操作提示；并行工作仍在进行时继续显示实际工作。 |
| 自动审核 | 必须 | 自动审核中；结束后清理并显示下一动作 | 不当作用户待办，不展示长审核理由或风险分数；审核通过不等于工具启动，并行时保留可见活动。 |
| 错误与恢复 | 错误、认证恢复必须；其他等待按条件 | 正在重试、连接服务、等待输出、任务失败 | 按实际恢复 / 终止事实决定；安全缓冲需 `showBufferingUi=true`，不暴露机制或行动建议。 |
| MCP 启动、hook、环境变化 | 条件消费 | 确实阻塞当前工作且持续可见时：准备工具 / 处理中 / 连接环境 | 后台启动和异步 hook 不抢主状态；不显示脚本、服务列表或内部阶段。 |
| token 用量、限额、配置与独立 UI 操作 | 进度展示忽略；模型配置保留最小信息 | 无独立进度项 | 服务端 token 用量不能替代流式速度；文件搜索、账号设置等不扩展为第二套 Desktop。 |

## 默认界面与信息层次

默认列表保留聊天 / 项目、当前活动、活动耗时和一列“生成速度”。有明确并行活动时，在当前活动后显示简短的“另 2 项进行中”；展开后只列用户可读动作、简短结果和必要的计划 / 子任务上下文。

下面是文案示意，数值为模拟数据：

| 聊天 | 当前活动 | 耗时 | 生成速度 |
| --- | --- | ---: | ---: |
| 修复登录 | 运行命令 · `pytest -q` | 24s | — |
| 方案讨论 | 生成回答 | 12s | 48 token/s |
| 文档整理 | 准备命令 · `rg --fi…` | 2s | 73 token/s |
| 代码重构 | 压缩上下文 | 4s | — |
| 测试任务 | 已暂停 | — | — |

“已暂停”只是当前工作状态，不加徽标、弹窗、提醒或“请前往 Desktop”文案。短暂的请求等待不必闪出一个新阶段。自动审核显示“自动审核中”，结束后切换实际动作，简要详情可保留已知结果；不展示长审核理由和内部评分。上下文压缩明确显示“压缩上下文”。

默认界面移除 `exec → exec_command`、`tool_ready` 等内部词，token 累计量、摘要字 / 段数、输出字数、事件数、请求与进程 ID、tokenizer 来源及协议质量字段也不进入主列表或普通详情。已有诊断数据可留在诊断导出中，不为本次改进新增复杂的用户面板。

命令摘要保留可识别的命令及必要短参数，使用有界截断；长命令仅展示前缀和省略号。普通工具优先使用已确认的可读名称；无法确认语义时使用简短工具名。参数生成阶段的部分命令带省略号，并由“准备命令”明确其尚未执行。参数生成中若只知道外层 Code Mode，就先显示“准备操作”，等内部工具名或命令出现再补齐。

采集失去连接时应显示“连接中断”或“状态暂不可用”，冻结并标明旧状态已失效，停止速度；不能把观测中断写成任务失败。正常连接状态不占据一个技术指标列。

## 生成速度的含义

一列“生成速度”统计当前聊天去重后的**可观测模型生成文本**：回答、计划、工具参数，以及可见思考摘要。它是收到文本后重新分词得到的接收速度，沿用当前约 2 秒窗口；网络缓冲会影响读数，不能保证等于服务器瞬时计算速度。

- 不计命令 stdout、MCP progress 消息、用户输入、hook 注入、工具结果或服务端 token 使用量。
- 正文与模型 trace 的镜像只计一次；父聊天收到的子任务镜像也不能再次累计。只有明确属于该聊天的生成流才计入它的速度。
- 结束时收到的完整内容用于校正已有流和补齐摘要，不倒填此前未观测到的实时速度；计划最终文本可能与增量拼接结果不同。
- 有可见摘要时，“思考中”的速度只代表这部分文本；没有可观测增量时显示 `—`，不估算隐藏推理速度。
- `0 token/s` 仅用于已确认仍开启、仍在可靠观测且窗口内暂时无新文本的生成流；流结束、暂停、执行命令或采集中断时显示 `—`。
- 模型 / encoding 未知时使用 `≈` 标记估算。正确消费 `threadSettings.model` 与实际模型变更，分词来源放在帮助说明或诊断数据中。
- 多个活动并行时，当前动作不一定是文本生成；只要该聊天另有已确认的生成流，仍可显示聊天总生成速度，简要详情标明来源活动。

这项读数帮助用户感知生成是否持续，不应展示成吞吐排名、费用估算或整个任务的完成度。

## 状态选择与连续性

主状态按当前有证据的活动决定，不采用“只要有审批就覆盖所有工作”的规则：

1. 有新文本、已确认命令执行、文件编辑或其他工具工作的，展示实际动作；多个活动显示最近有真实进展的动作，加并行数量，避免高频来回跳动。
2. 所有前台工作确实被用户 / 客户端请求阻塞时，才显示“已暂停”。请求必须按独立 ID、目标和作用范围维护；缺少关联时不暂停所有聊天。
3. 没有其他工作，但已知仍在自动审核、恢复连接、压缩上下文或程序等待时，显示对应简短状态。
4. 只知道当前回合仍有效、无法证明具体动作时，显示“处理中”。参数已完成而执行尚不可观测也用此状态，必要时保留短命令摘要，不能显示“运行命令”。
5. 结束、失败和中断立即显示。回合结束时存在明确仍运行的后台进程，在简要详情单独保留其状态；不通过新模型文本或其他工具结束推断后台进程结束。

刚完成的动作可以短暂保留“命令完成 · `pytest -q`”等结果，直到下一项实际工作出现；不能因结束一个 item 就闪回没有意义的“未知”。成功命令默认不显示退出码；单项失败可在详情显示“命令失败”，但不直接把整个任务判失败，后续可能继续修复。

拟采用的呈现节奏：真实工作变化和首个文本增量立即更新；约 1 秒内的临时准备 / 恢复状态合并，持续约 3 秒且明确影响当前工作时显示中性等待。这两个阈值是初始产品选择，需通过真实回放调整，不是已经测定的最优值。最终结果、失败和采集中断即时更新，不受等待合并延迟影响。

## 消费范围与实现顺序

附表的“必须”表示第一轮完整进度能力需要的最小语义消费；“条件”表示仅已有数据能明确关联当前工作或确有兼容需求时接入；“忽略”表示本轮不为进度状态增加消费。消费不意味着保存完整正文，也不意味着新增一条展示。

87 种通知中建议必需消费 30 种、条件消费 18 种、忽略 39 种；11 种请求中必需内部消费 6 种、条件消费 3 种、忽略 2 种。19 种 item 全部识别最小类型与边界，但用户输入、hook 注入、工具结果和退出模式等不展示成独立活动。84 种 core 事件经已有协议投影或模型流处理，不另加一套订阅造成重复。

| 顺序 | 修改重点 | 可 review / 验证的结果 |
| --- | --- | --- |
| P0：状态正确 | 修复协作类型、嵌套模型配置、resolved 清理；补齐请求 / 自动审核身份；消费 turn、thread status、错误和流结束；区分参数与执行 | 不残留等待，不把审批通过写成运行，不把单次流失败写成任务结束，并行工作不会被另一个等待覆盖。 |
| P1：日常展示 | 统一用户动作文案；命令准备预览；文件、计划、工具和子任务最小摘要；单列生成速度与去重；合并短暂状态 | 默认列表接近上面的示意；无技术字段、无重复操作提示、无虚假速度。 |
| P2：条件长尾 | 同步阻塞 hook、明确 MCP 启动依赖、环境 / 认证恢复、可见缓冲、旧协议兼容与长期目标 | 先确认当前版本真实事件与 Desktop 呈现，再接入会影响状态准确性的场景；不以支持全部协议方法作为目标。 |

桥接重放和采集器重连需要保存最小状态快照与已结束标记，防止开始事件重放后重新出现旧暂停或旧工具。恢复时只能重建仍有效的活动，无法确认的旧活动降为“状态暂不可用”。

外层 `exec` 与内部调用的可靠父子关联仍需运行时身份依据；不能按命令相似度、工具名或时间接近补猜。缺乏外层 cell 结束证据时，保留内部未知状态，在用户界面展示已确认的具体工作；不承诺所有外层工具都发出统一 `functionCallOutput`。

## 验收场景

| 场景 | 预期行为 |
| --- | --- |
| 参数从 `rg --fi` 逐步补齐，随后工具开始 | 准备阶段显示可识别名称 / 命令前缀和生成速度；有执行证据后改为运行命令。 |
| 参数已完成，但执行开始证据缺失 | 显示处理中，不继续虚报生成，不把准备项写成执行中。 |
| 审批前 item 已开始，用户拒绝 | 无运行命令假象；不展示审批提示；按实际请求和结束事件清理暂停及活动。 |
| 自动审核通过，但命令尚未启动 | 自动审核中结束，继续处理中；详情可保留简短审核结果，不误报命令已执行。 |
| 收到 resolved，没有看到客户端回复 | 按请求 ID 清理对应阻塞，决定保留未知；不假定批准，不保留过期暂停。 |
| 一个工具等待用户输入，另一个命令仍在运行 | 主行继续显示真实运行的命令；无重复用户提醒。 |
| 模型流失败后自动重试 | 关闭旧流速度；由 willRetry 与当前回合事实显示正在重试；后续恢复后重新计速。 |
| 一条命令失败，模型继续修复 | 命令结果可见，聊天继续显示新工作；整个任务未被提前判失败。 |
| 同一文本同时出现在 v2 与模型 trace，或转发到父聊天 | 去重计速；不加倍，不把子任务结果当父聊天的新生成。 |
| 长命令无输出；仅收到心跳 | 保持已确认的运行命令与耗时，速度为 —；不造输出量或任务百分比。 |
| 计划由四步改为三步，派发子任务调用返回 | 计划以新结构为准；调用返回不宣称子任务全部结束。 |
| 只有压缩完成通知、一次 subAgentActivity 完成项 | 补充结束事实或已知上下文；不反造“正在整理 / 子任务运行中”。 |
| 后台 MCP 启动、异步 hook、全局账号变化 | 不覆盖无关聊天的真实工作状态。 |
| 回合结束，但已知进程继续后台运行 | 显示回合结果与明确的后台活动；不会因为新文本、归档或外层返回就判进程结束。 |
| 采集器断线重连，重放已解决请求 | 断线期间速度为 — 且状态失效；重连不恢复旧暂停、不重复计数。 |
| 用户没有可见思考摘要，模型实际仍在推理 | 可以显示思考中；速度为 —，不宣称测到了隐藏推理。 |

上述是后续实现的验收目标，本轮没有把模拟分类或 schema 支持写成真实端到端验收。

## 本次供 review 的主要选择

1. 用户操作相关事件只维护状态；确实停住时保留中性“已暂停”，不展示操作内容与提醒。
2. 自动审核与上下文压缩过程可见，分别显示“自动审核中”和“压缩上下文”；外层 Code Mode 隐入内部；默认保留具体命令或可读工具动作。
3. 默认只新增一列可观测文本生成速度，包含参数与可见摘要，具体计量限制通过帮助说明表达。
4. 计划与子任务使用简要上下文，长尾后台事件按明确作用范围接入，不增加协议阶段清单。

这些选择不依赖额外产品信息，可以先作为实现默认值。需要补证据的是当前 Desktop 的长尾呈现、静默命令的实际启动信号，以及外层 cell 身份与结束来源；实现时按上面的条件接入和保守兜底处理。

## 附表：87 种服务端通知

“当前支持”来自盘点基线；“建议”是本次展示决策。源码链接定位方法定义，具体作用范围仍以 payload 和真实事件为准。下一步按 [A0 逐项复核计划](background-and-implementation-plan.md) 对 87 种通知分别核对现有过滤、消费、展示与恢复处理，补齐保留 / 修复 / 补充 / 忽略的实现结论；当前分类不等于该轮逐链路复核已经完成。

| 事件 / 类型 | 当前支持 | 建议消费 | 建议展示 | 用户看到什么 / 生效条件 | 判断依据 |
| --- | --- | --- | --- | --- | --- |
| `error` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1950) | 丢弃 | 必须 | 状态 | 正在重试 / 任务失败 | 按 willRetry 区分恢复与终止；显示简短状态，不复制异常堆栈、登录要求或操作入口；最终状态仍以权威回合事件为准。 |
| `thread/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1951) | 已展示 | 必须 | 上下文 | 聊天名称 | 对应 Desktop 中的聊天，保存最小身份；名称变化不是任务取得进展。 |
| `thread/status/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1952) | 仅透传 | 必须 | 状态 | 处理中 / 已暂停 / 状态暂不可用 | 消费 activeFlags、idle、systemError 等校正任务状态；用户等待只显示中性暂停，有其他实际工作时继续显示实际工作。不能用 idle 单独推断任务成功。 |
| `thread/readState/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1954) | 仅透传 | 忽略 | 不展示 | — | 已读与未读属于 Desktop 信息组织，不解释当前任务执行进度。 |
| `thread/archived` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1955) | 仅透传 | 必须 | 不单独展示 | 维护聊天列表与活动有效性 | 归档、删除、关闭、回退需要清理或失效旧身份；恢复不能自动恢复为执行中；不能据此判定后台进程已结束。 |
| `thread/deleted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1956) | 仅透传 | 必须 | 不单独展示 | 维护聊天列表与活动有效性 | 归档、删除、关闭、回退需要清理或失效旧身份；恢复不能自动恢复为执行中；不能据此判定后台进程已结束。 |
| `thread/unarchived` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1957) | 仅透传 | 必须 | 不单独展示 | 维护聊天列表与活动有效性 | 归档、删除、关闭、回退需要清理或失效旧身份；恢复不能自动恢复为执行中；不能据此判定后台进程已结束。 |
| `thread/closed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1958) | 仅透传 | 必须 | 不单独展示 | 维护聊天列表与活动有效性 | 归档、删除、关闭、回退需要清理或失效旧身份；恢复不能自动恢复为执行中；不能据此判定后台进程已结束。 |
| `thread/reverted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1959) | 仅透传 | 必须 | 不单独展示 | 维护聊天列表与活动有效性 | 归档、删除、关闭、回退需要清理或失效旧身份；恢复不能自动恢复为执行中；不能据此判定后台进程已结束。 |
| `skills/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1960) | 丢弃 | 忽略 | 不展示 | — | 技能、附件、下一步预测、输入队列变化不证明当前任务在执行；不复制 Desktop 管理功能，也不因队列通知主动查询队列。 |
| `thread/name/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1961) | 已展示 | 必须 | 上下文 | 聊天名称 | 对应 Desktop 中的聊天，保存最小身份；名称变化不是任务取得进展。 |
| `thread/attachment/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1962) | 仅透传 | 忽略 | 不展示 | — | 技能、附件、下一步预测、输入队列变化不证明当前任务在执行；不复制 Desktop 管理功能，也不因队列通知主动查询队列。 |
| `thread/goal/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1963) | 仅透传 | 条件 | 不单独展示 | 已暂停 / 本轮完成 / 任务完成 | 只在当前被观察聊天使用长期目标时消费 goal.status；用于区分回合与目标结束，不显示预算、用量和目标全文；active 不能单独证明正在工作。 |
| `thread/prediction/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1965) | 仅透传 | 忽略 | 不展示 | — | 技能、附件、下一步预测、输入队列变化不证明当前任务在执行；不复制 Desktop 管理功能，也不因队列通知主动查询队列。 |
| `thread/goal/cleared` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1966) | 仅透传 | 条件 | 不单独展示 | 已暂停 / 本轮完成 / 任务完成 | 只在当前被观察聊天使用长期目标时消费 goal.status；用于区分回合与目标结束，不显示预算、用量和目标全文；active 不能单独证明正在工作。 |
| `thread/queue/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1968) | 仅透传 | 忽略 | 不展示 | — | 技能、附件、下一步预测、输入队列变化不证明当前任务在执行；不复制 Desktop 管理功能，也不因队列通知主动查询队列。 |
| `project/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1970) | 丢弃 | 条件 | 上下文 | 项目名称 | 仅用已观测且能明确关联的名称校正聊天上下文；不另加项目变化状态，不为此发起查询。 |
| `thread/project/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1972) | 仅透传 | 条件 | 上下文 | 项目名称 | 仅用已观测且能明确关联的名称校正聊天上下文；不另加项目变化状态，不为此发起查询。 |
| `thread/environment/connected` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1974) | 仅透传 | 条件 | 状态 | 连接环境 / 连接暂不可用 | 仅明确影响当前任务时消费并展示；环境连接与 monitor 观测连接分别维护，不影响无关后台环境。 |
| `thread/environment/disconnected` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1976) | 仅透传 | 条件 | 状态 | 连接环境 / 连接暂不可用 | 仅明确影响当前任务时消费并展示；环境连接与 monitor 观测连接分别维护，不影响无关后台环境。 |
| `thread/settings/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1978) | 部分消费 | 必须 | 上下文 | 模型信息（简要详情） | 消费正确的嵌套配置和实际模型，保证 tokenizer 选择及身份准确；不把模型切换当作新阶段或弹出通知。 |
| `thread/tokenUsage/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1979) | 丢弃 | 忽略 | 不展示 | — | 当前增量价值是流式生成速度；累计用量、限额与原始 usage 不作为速度或进度指标，不建设额外用量面板。 |
| `turn/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1980) | 已展示 | 必须 | 状态 | 准备开始 / 本轮完成 / 任务完成 / 已停止 / 任务失败 | 维护回合边界及最终结果；后台任务仍按自身生命周期继续；不发送第二份完成或失败通知。 |
| `hook/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1981) | 丢弃 | 条件 | 状态 | 处理中 / 收尾中 | 仅消费明确阻塞当前回合的同步 hook；短暂执行合并，异步后台 hook 不抢主状态；不展示 hook 名、脚本、日志或内部阶段。 |
| `turn/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1982) | 已展示 | 必须 | 状态 | 准备开始 / 本轮完成 / 任务完成 / 已停止 / 任务失败 | 维护回合边界及最终结果；后台任务仍按自身生命周期继续；不发送第二份完成或失败通知。 |
| `hook/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1983) | 丢弃 | 条件 | 状态 | 处理中 / 收尾中 | 仅消费明确阻塞当前回合的同步 hook；短暂执行合并，异步后台 hook 不抢主状态；不展示 hook 名、脚本、日志或内部阶段。 |
| `turn/diff/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1984) | 丢弃 | 忽略 | 不展示 | — | 不复制 Desktop 文件 diff 与审阅功能；修改文件进度由 fileChange 的最小元数据提供。 |
| `turn/plan/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1985) | 丢弃 | 必须 | 上下文 | 计划 2/4 · 当前步骤 | 消费结构化步骤状态，主行保留紧凑上下文或简要详情；步骤计数不是整个任务百分比，重新规划后重新计算，不复制完整计划正文。 |
| `item/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1986) | 已展示 | 必须 | 状态 | 按 19 种活动映射 | 统一消费最小生命周期、身份、状态与必要摘要；展示由具体活动类型决定，用户输入和注入内容不计生成速度。 |
| `item/autoApprovalReview/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1987) | 仅透传 | 必须 | 状态 | 自动审核中 | 按 reviewId 记录开始、目标及作用范围，展示实际审核活动；并行时保留可见活动，不把自动审核当用户待办，不显示长理由、风险分数或审批入口。 |
| `item/autoApprovalReview/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1988) | 仅透传 | 必须 | 上下文 | 自动审核结束；简要详情可保留结果 | 按审核身份清理阶段，必要时保留已知简短结果；无执行证据时切回处理中，不能把审核通过当工具启动，也不能结束仍在运行的其他活动。 |
| `autoApprovalReview/strictReviewRequired` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1990) | 丢弃 | 必须 | 不单独展示 | 自动审核中（回合级补充） | 当前 schema 提供 threadId、turnId 与 startedAtMs，补充回合级审核状态；与明确审核活动去重，按实际审核 / 回合边界清理，不凭此制造用户审批请求或工具启动。 |
| `item/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1991) | 已展示 | 必须 | 状态 | 按 19 种活动映射 | 统一消费最小生命周期、身份、状态与必要摘要；展示由具体活动类型决定，用户输入和注入内容不计生成速度。 |
| `rawResponseItem/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1993) | 丢弃 | 条件 | 不展示 | — | 仅在标准 item / 已接入上游流无法关联时评估最小身份补充；不主动启用实验原始事件，不作为本轮必需展示数据源。 |
| `rawResponse/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1995) | 丢弃 | 忽略 | 不展示 | — | 当前增量价值是流式生成速度；累计用量、限额与原始 usage 不作为速度或进度指标，不建设额外用量面板。 |
| `item/agentMessage/delta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1996) | 已展示 | 必须 | 状态 | 生成回答 / 整理计划 · 生成速度 | 消费模型可见文本增量，去重后计生成速度；正文、计划全文继续由 Desktop 展示。 |
| `item/plan/delta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1998) | 仅透传 | 必须 | 状态 | 生成回答 / 整理计划 · 生成速度 | 消费模型可见文本增量，去重后计生成速度；正文、计划全文继续由 Desktop 展示。 |
| `command/exec/outputDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2000) | 丢弃 | 忽略 | 不展示 | — | 这是客户端独立命令 / 进程 API；当前 scope 为会话中模型工具，不把独立终端或客户端辅助进程并入任务进度。 |
| `process/outputDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2003) | 丢弃 | 忽略 | 不展示 | — | 这是客户端独立命令 / 进程 API；当前 scope 为会话中模型工具，不把独立终端或客户端辅助进程并入任务进度。 |
| `process/exited` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2006) | 丢弃 | 忽略 | 不展示 | — | 这是客户端独立命令 / 进程 API；当前 scope 为会话中模型工具，不把独立终端或客户端辅助进程并入任务进度。 |
| `item/commandExecution/outputDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2007) | 已展示 | 必须 | 不单独展示 | 运行命令 · 命令摘要 | 用输出和交互推进已关联命令的执行状态、更新真实进展；不显示输出字数、stdin 内容、终端原始输出；交互不证明命令已完成。 |
| `item/commandExecution/terminalInteraction` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2008) | 仅透传 | 必须 | 不单独展示 | 运行命令 · 命令摘要 | 用输出和交互推进已关联命令的执行状态、更新真实进展；不显示输出字数、stdin 内容、终端原始输出；交互不证明命令已完成。 |
| `item/fileChange/outputDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2010) | 仅透传 | 条件 | 不单独展示 | 修改文件 | 仅旧后端已有明确关联的补丁活动时补充活跃证据；去重，不独立展示旧输出流。 |
| `item/fileChange/patchUpdated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2011) | 仅透传 | 必须 | 不单独展示 | 修改文件 · 文件名 | 只保留文件名 / 个数等有界摘要，更新已关联编辑活动；不保存或展示完整补丁，不将单个 delta 当完成。 |
| `serverRequest/resolved` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2012) | 部分消费 | 必须 | 不展示 | 清除暂停原因 | 按请求 ID 清理内部阻塞；resolved 不等于批准，也不能关闭仍在运行的实际工具；与客户端回复和 activeFlags 共同校正。 |
| `item/mcpToolCall/progress` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2013) | 仅透传 | 必须 | 不单独展示 | 继续展示当前工具动作 | 更新工具最近进展；默认不直接显示任意 message。只有经过用户可读性验证且不要求用户操作的短消息才可进入简要详情。 |
| `mcpServer/oauthLogin/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2014) | 丢弃 | 条件 | 不展示 | — | 仅与已知当前认证阻塞关联时清理内部状态；不复制 Desktop 登录提示、结果 toast 或操作按钮。 |
| `mcpServer/startupStatus/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2015) | 丢弃 | 条件 | 不单独展示 | 明确阻塞当前任务时：准备工具 | 只消费与当前任务有明确依赖关系的就绪状态；不能因为后台某个 MCP 服务在启动就宣称所有聊天都在准备工具。 |
| `mcpServer/event/stream/notification` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2017) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `account/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2018) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `account/gatewayOAuth/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2019) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `account/rateLimits/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2020) | 丢弃 | 忽略 | 不展示 | — | 当前增量价值是流式生成速度；累计用量、限额与原始 usage 不作为速度或进度指标，不建设额外用量面板。 |
| `app/list/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2021) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `remoteControl/status/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2022) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `externalAgentConfig/import/progress` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2023) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `externalAgentConfig/import/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2024) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `fs/changed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2025) | 丢弃 | 忽略 | 不展示 | — | 属于账户、目录、独立服务、远程控制或客户端配置操作；当前本机会话监测不从这些事件推断任务进展。 |
| `item/reasoning/summaryTextDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2026) | 已展示 | 必须 | 状态 | 思考中；有可见文本增量时显示生成速度 | 保存可见摘要流的必要边界和计数；不展示摘要字数 / 段数。速度只能代表收到的可见文本，不代表隐藏推理速度。 |
| `item/reasoning/summaryPartAdded` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2027) | 仅透传 | 必须 | 状态 | 思考中；有可见文本增量时显示生成速度 | 保存可见摘要流的必要边界和计数；不展示摘要字数 / 段数。速度只能代表收到的可见文本，不代表隐藏推理速度。 |
| `item/reasoning/textDelta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2028) | 仅透传 | 忽略 | 不展示 | — | 不新增接入原始 reasoning 内容，也不将其与可见摘要拼成隐藏推理速度。 |
| `thread/compacted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2030) | 仅透传 | 条件 | 不单独展示 | 压缩上下文（旧后端补充） | 旧版完成通知只能补充完成事实；canonical contextCompaction 可用时去重；不能收到完成通知后反造压缩中阶段。 |
| `model/rerouted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2031) | 已展示 | 必须 | 上下文 | 模型信息（简要详情） | 消费正确的嵌套配置和实际模型，保证 tokenizer 选择及身份准确；不把模型切换当作新阶段或弹出通知。 |
| `model/verification` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2032) | 仅透传 | 条件 | 不单独展示 | 确实阻塞且无其他工作时：已暂停 | 消费当前任务的待验证关联；无实际阻塞证据不暂停，不复制验证说明、链接或行动提示。 |
| `modelProvider/authRecoveryStarted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2033) | 丢弃 | 必须 | 状态 | 连接服务 | 明确认证恢复影响当前回合时展示中性连接状态；不显示供应商、认证机制、刷新 token 或重新登录要求。 |
| `modelProvider/authRecoveryCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2034) | 丢弃 | 必须 | 状态 | 连接服务 | 明确认证恢复影响当前回合时展示中性连接状态；不显示供应商、认证机制、刷新 token 或重新登录要求。 |
| `turn/moderationMetadata` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2036) | 仅透传 | 忽略 | 不展示 | — | 内部审核元数据，没有必要成为用户进度条目。 |
| `model/safetyBuffering/updated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2037) | 仅透传 | 条件 | 状态 | 等待输出 | 只在 showBufferingUi=true 且对应当前等待中的生成活动时展示；不显示安全审核分类、模型切换建议或处理按钮，不覆盖正在执行的其他工作。 |
| `warning` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2038) | 丢弃 | 条件 | 不展示 | — | 只有明确关联正在恢复 / 阻塞 / 失败的当前任务时消费；普通警告不改变任务状态，不复制 Desktop 警告正文，不凭警告猜测失败。 |
| `guardianWarning` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2039) | 丢弃 | 条件 | 不展示 | — | 只有明确关联正在恢复 / 阻塞 / 失败的当前任务时消费；普通警告不改变任务状态，不复制 Desktop 警告正文，不凭警告猜测失败。 |
| `deprecationNotice` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2040) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `configWarning` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2041) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `fuzzyFileSearch/sessionUpdated` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2042) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `fuzzyFileSearch/sessionCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2043) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `thread/realtime/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2045) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/itemAdded` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2047) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/item/started` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2049) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/item/transcript/delta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2051) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/item/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2053) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/transcript/delta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2055) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/transcript/done` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2057) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/outputAudio/delta` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2059) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/sdp` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2061) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/error` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2063) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `thread/realtime/closed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2065) | 仅透传 | 忽略 | 不展示 | — | 本轮范围是 Codex 任务执行与文本生成，不扩展实时音视频会话；不解码音频、转录或连接协商内容来伪造任务进度。 |
| `windows/worldWritableWarning` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2068) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `windowsSandbox/setupCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2069) | 丢弃 | 忽略 | 不展示 | — | 属于配置、独立 UI 搜索或当前 macOS scope 外的操作；由 Desktop 呈现，不作为会话进度证据。 |
| `account/login/completed` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:2074) | 丢弃 | 条件 | 不展示 | — | 仅与已知当前认证阻塞关联时清理内部状态；不复制 Desktop 登录提示、结果 toast 或操作按钮。 |

## 附表：11 种服务端请求

这是 app-server 发给客户端的请求；monitor 只旁观最小语义，不代 Desktop 执行、批准或回答。

| 事件 / 类型 | 当前支持 | 建议消费 | 建议展示 | 用户看到什么 / 生效条件 | 判断依据 |
| --- | --- | --- | --- | --- | --- |
| `item/commandExecution/requestApproval` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1797) | 已展示 | 必须 | 不单独展示 | 只有全部前台工作确实被阻塞时：已暂停 | 仅消费请求 / 目标身份和客户端回复，维护准确阻塞状态；不显示原因、选项、登录链接、问题或审核按钮，不提醒、不代答；其他工具仍运行时显示实际工作。 |
| `item/fileChange/requestApproval` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1804) | 已展示 | 必须 | 不单独展示 | 只有全部前台工作确实被阻塞时：已暂停 | 仅消费请求 / 目标身份和客户端回复，维护准确阻塞状态；不显示原因、选项、登录链接、问题或审核按钮，不提醒、不代答；其他工具仍运行时显示实际工作。 |
| `item/tool/requestUserInput` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1810) | 已展示 | 必须 | 不单独展示 | 只有全部前台工作确实被阻塞时：已暂停 | 仅消费请求 / 目标身份和客户端回复，维护准确阻塞状态；不显示原因、选项、登录链接、问题或审核按钮，不提醒、不代答；其他工具仍运行时显示实际工作。 |
| `mcpServer/elicitation/request` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1816) | 丢弃 | 必须 | 不单独展示 | 只有全部前台工作确实被阻塞时：已暂停 | 仅消费请求 / 目标身份和客户端回复，维护准确阻塞状态；不显示原因、选项、登录链接、问题或审核按钮，不提醒、不代答；其他工具仍运行时显示实际工作。 |
| `item/permissions/requestApproval` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1822) | 部分消费 | 必须 | 不单独展示 | 只有全部前台工作确实被阻塞时：已暂停 | 仅消费请求 / 目标身份和客户端回复，维护准确阻塞状态；不显示原因、选项、登录链接、问题或审核按钮，不提醒、不代答；其他工具仍运行时显示实际工作。 |
| `item/tool/call` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1828) | 仅透传 | 必须 | 不单独展示 | 按 dynamicToolCall 生命周期展示动作 | 关联客户端执行与结果，避免委托后状态断开；不展示 JSON-RPC 请求或再次调用工具，不代客户端执行。 |
| `account/chatgptAuthTokens/refresh` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1833) | 丢弃 | 条件 | 不展示 | — | 只有能明确关联当前阻塞时维护认证恢复；不显示刷新 token，不干预客户端回复，不把后端级事件误归到所有聊天。 |
| `attestation/generate` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1839) | 丢弃 | 忽略 | 不展示 | — | 内部认证证明请求，不影响用户可理解的工作活动。 |
| `currentTime/read` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1846) | 丢弃 | 忽略 | 不展示 | — | 内部时钟读取，无需建立进度活动。 |
| `applyPatchApproval` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1854) | 丢弃 | 条件 | 不单独展示 | 仅遇到旧客户端时内部维护暂停 | 作为实际出现的旧协议请求兼容，仍不展示审批内容或操作入口；不主动启用旧接口。 |
| `execCommandApproval` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/common.rs:1860) | 丢弃 | 条件 | 不单独展示 | 仅遇到旧客户端时内部维护暂停 | 作为实际出现的旧协议请求兼容，仍不展示审批内容或操作入口；不主动启用旧接口。 |

## 附表：19 种活动类型

| 事件 / 类型 | 当前支持 | 建议消费 | 建议展示 | 用户看到什么 / 生效条件 | 判断依据 |
| --- | --- | --- | --- | --- | --- |
| `userMessage` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:239) | 未识别 | 必须 | 不展示 | — | 只记录必要身份与边界，用户输入不计模型速度。 |
| `hookPrompt` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:246) | 未识别 | 必须 | 不展示 | — | 识别注入项，避免误判为模型正文或独立执行阶段，不保存全文。 |
| `agentMessage` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:252) | 已识别 | 必须 | 状态 | 生成回答 | 只用生命周期及文本增量统计速度，不复制回答内容。 |
| `functionCallOutput` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:266) | 未识别 | 必须 | 不展示 | — | 只用明确身份补充相应活动，禁止推断所有 exec 都有统一返回项；不保存输出正文。 |
| `plan` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:276) | 未识别 | 必须 | 状态 | 整理计划 | 模型正在生成计划时计可见速度；结构化计划步骤另作简要上下文，不复制计划正文。 |
| `reasoning` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:282) | 已识别 | 必须 | 状态 | 思考中 | 可见摘要有增量才显示相应生成速度；没有可见增量时为 —，不伪造隐藏推理速度。 |
| `commandExecution` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:291) | 已识别 | 必须 | 状态 | 运行命令 · pytest -q | 审批前 item/started 不等于进程开始；有启动 / 输出证据才用运行；不显示 exec/exec_command 箭头、进程 ID 或输出字数。 |
| `fileChange` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:330) | 已识别 | 必须 | 状态 | 修改文件 · engine.py（另 2 个） | 保留有界文件名 / 数量及状态，不展示补丁和审批动作。 |
| `mcpToolCall` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:337) | 已识别 | 必须 | 状态 | 调用工具 · 可读工具名 | 优先使用已知的用户可读名称；无映射时用简短工具名，不展示 MCP 前缀或服务端名称、参数 JSON。 |
| `dynamicToolCall` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:360) | 已识别 | 必须 | 状态 | 调用工具 · 可读工具名 | 与客户端执行请求关联，不复制请求协议或工具结果全文。 |
| `collabAgentToolCall` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:374) | 未识别 | 必须 | 状态 | 分配子任务 / 等待子任务 | 保留 receiverThreadIds 与已知代理状态；显示任务动作或数量，转发调用结束不等于子任务全部完成。 |
| `subAgentActivity` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:397) | 未识别 | 必须 | 上下文 | 子任务进展（简要详情） | 仅明确的活动更新简要子任务信息；一次完成事件不反造持续运行，不默认展示路径、模型及推理档位。 |
| `webSearch` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:407) | 已识别 | 必须 | 状态 | 搜索网页 | 已知时可提供短查询摘要；结果与引用由 Desktop 展示。 |
| `imageView` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:410) | 已识别 | 必须 | 状态 | 查看图片 | 不复制图片内容、绝对路径或资源 ID。 |
| `sleep` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:414) | 未识别 | 必须 | 状态 | 等待中 | 这是明确的程序等待，不是要求用户操作；不显示 clock.sleep 或制造完成百分比。 |
| `imageGeneration` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:415) | 已识别 | 必须 | 状态 | 生成图片 | 仅真实生命周期；不把下载或展示图片另造进度阶段。 |
| `enteredReviewMode` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:418) | 未识别 | 必须 | 上下文 | 审阅代码 | 这是代码审阅工作上下文，与权限审批不同；具体思考或工具动作仍决定主状态。 |
| `exitedReviewMode` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:424) | 未识别 | 必须 | 不单独展示 | 结束审阅上下文 | 清理模式标记，不据此单独结束整个任务，不复述审阅结果。 |
| `contextCompaction` [源码](/Users/bytedance/playground/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs:430) | 未识别 | 必须 | 状态 | 压缩上下文 | 按实际开始 / 结束展示压缩过程；完成后切换下一动作，不凭完成通知反造进行中，不显示压缩 token 数或内部机制。 |

## 附表：10 种已接入模型流事件

普通 v2 工具生命周期无法提供本项目已有的逐参数增量能力，因此继续沿用已接入模型流；不把生成调用与执行调用合为一个状态。

| 事件 / 类型 | 建议消费 | 建议展示 | 用户看到什么 / 生效条件 | 判断依据 |
| --- | --- | --- | --- | --- |
| `response.completed` | 必须 | 不展示 | — | 校正流结束与模型元数据，不显示 usage，不代替任务或命令结束。 |
| `response.custom_tool_call_input.delta` | 必须 | 状态 | 准备命令 / 准备操作 · 生成速度 | 参数生成时展示实际可识别的短命令前缀，不暴露 exec、JavaScript、参数 JSON。 |
| `response.failed` | 必须 | 不单独展示 | 等待权威重试 / 失败状态 | 停止已终止流的速度，避免保留过期生成状态；单次 response 失败可能重试，不能直接结束整个任务。 |
| `response.function_call_arguments.delta` | 必须 | 状态 | 准备命令 / 准备操作 · 生成速度 | 与自定义工具参数一致；不把生成的调用当作已执行。 |
| `response.incomplete` | 必须 | 不单独展示 | 等待后续状态 | 停止结束流的速度并标注内部流质量；不直接推断整个任务失败或已完成。 |
| `response.output_item.added` | 必须 | 不单独展示 | 由模型输出类型决定 | 提供生成活动身份与起点；外层 Code Mode 名称保留内部，默认显示用户可读动作。 |
| `response.output_item.done` | 必须 | 不单独展示 | 参数 / 可见内容生成结束 | 最终内容用于校正计数、补齐名称与命令预览，不倒填实时速度；不等于真实工具执行结束。 |
| `response.output_text.delta` | 必须 | 状态 | 生成回答 · 生成速度 | 只计算可见模型文本，协议镜像去重。 |
| `response.reasoning_summary_part.added` | 必须 | 不展示 | — | 维护分段和去重，不显示字 / 段计数。 |
| `response.reasoning_summary_text.delta` | 必须 | 状态 | 思考中；可见文本生成速度 | 只代表可见摘要，不能标成完整思考速度。 |

## 附表：84 种 core 事件的处理路径

core 事件与 v2 通知不是一一对应；以下是建议使用的投影 / 来源，不表示 monitor 要直接订阅全部 core 事件。表中明确标出的无直接投影情况，以实际可观测边界为准。

| Core EventMsg | 建议使用的投影 / 来源 | 判断依据 |
| --- | --- | --- |
| `Error` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1466) | error | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `Warning` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1470) | warning | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AuthRecoveryStarted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1473) | modelProvider/authRecoveryStarted | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AuthRecoveryCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1476) | modelProvider/authRecoveryCompleted | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `GuardianWarning` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1479) | guardianWarning | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RealtimeConversationStarted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1482) | 实时音视频接口，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RealtimeConversationRealtime` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1485) | 实时音视频接口，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RealtimeConversationClosed` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1488) | 实时音视频接口，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RealtimeConversationSdp` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1491) | 实时音视频接口，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ModelReroute` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1494) | model/rerouted | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ModelVerification` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1497) | model/verification | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TurnModerationMetadata` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1500) | turn/moderationMetadata | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `SafetyBuffering` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1503) | model/safetyBuffering/updated | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ContextCompacted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1506) | contextCompaction item；thread/compacted 仅兼容 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ThreadRolledBack` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1510) | thread/reverted 或实际回合身份变化 | 内部旧持久化标记在当前 bespoke 分支没有直接通知；消费实际 v2 回退 / 边界，不猜测其 wire 映射。 |
| `TurnStarted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1515) | turn/started | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ThreadSettingsApplied` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1519) | thread/settings/updated | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TurnComplete` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1524) | turn/completed | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TokenCount` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1528) | thread/tokenUsage/updated / account/rateLimits/updated，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AgentMessage` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1531) | canonical agentMessage / reasoning item | 旧完整消息不额外展示或计数；采用当前 item 生命周期与可见增量。 |
| `UserMessage` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1534) | canonical userMessage item，仅内部 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AgentReasoning` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1537) | canonical agentMessage / reasoning item | 旧完整消息不额外展示或计数；采用当前 item 生命周期与可见增量。 |
| `AgentReasoningRawContent` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1540) | 原始 reasoning，不新增消费 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AgentReasoningSectionBreak` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1543) | item/reasoning/summaryPartAdded | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `SessionConfigured` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1546) | 已观察客户端响应中的聊天 / 模型元数据 | 配置应答不作为用户进度活动；不假定与 thread/started 一一对应。 |
| `EnvironmentConnected` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1549) | thread/environment/connected | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `EnvironmentDisconnected` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1552) | thread/environment/disconnected | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ThreadGoalUpdated` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1555) | thread/goal/updated / cleared | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ThreadQueueChanged` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1558) | 队列变化，本轮忽略 | 当前 bespoke 分支无直接投影；不把用户消息队列变化算当前工作进展。 |
| `McpStartupUpdate` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1561) | mcpServer/startupStatus/updated | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `McpStartupComplete` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1564) | 启动就绪的已有状态，条件消费 | 当前 bespoke 分支无单独 v2 通知；不新增完整启动统计展示。 |
| `McpToolCallBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1566) | canonical mcpToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `McpToolCallEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1568) | canonical mcpToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `WebSearchBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1570) | canonical webSearch item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `WebSearchEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1572) | canonical webSearch item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ImageGenerationBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1574) | canonical imageGeneration item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ImageGenerationEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1576) | canonical imageGeneration item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ExecCommandBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1579) | canonical commandExecution item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ExecCommandOutputDelta` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1582) | item/commandExecution/outputDelta | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TerminalInteraction` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1585) | item/commandExecution/terminalInteraction | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ExecCommandEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1587) | canonical commandExecution item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ViewImageToolCall` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1590) | canonical imageView item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ExecApprovalRequest` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1592) | item/commandExecution/requestApproval，仅内部阻塞 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RequestPermissions` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1594) | item/permissions/requestApproval，仅内部阻塞 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RequestUserInput` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1596) | item/tool/requestUserInput，仅内部阻塞 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `DynamicToolCallRequest` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1598) | canonical dynamicToolCall item 与 item/tool/call | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `DynamicToolCallResponse` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1600) | canonical dynamicToolCall item 与 item/tool/call | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ElicitationRequest` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1602) | mcpServer/elicitation/request，仅内部阻塞 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ElicitationAbandoned` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1605) | serverRequest/resolved / activeFlags 的实际清理 | 不假定此内部事件存在一一对应通知；消费当前协议明确清理状态。 |
| `ApplyPatchApprovalRequest` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1607) | item/fileChange/requestApproval，仅内部阻塞 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `GuardianAssessment` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1610) | item/autoApprovalReview/*，展示自动审核过程 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `DeprecationNotice` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1614) | deprecationNotice，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `StreamError` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1618) | error | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `PatchApplyBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1622) | canonical fileChange item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `PatchApplyUpdated` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1625) | item/fileChange/patchUpdated | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `PatchApplyEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1628) | canonical fileChange item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TurnDiff` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1630) | turn/diff/updated，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RealtimeConversationListVoicesResponse` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1633) | 实时音视频接口，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `PlanUpdate` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1635) | turn/plan/updated | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `TurnAborted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1637) | turn/completed | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ShutdownComplete` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1640) | 实际 thread / bridge 关闭状态 | 此内部事件没有对应用户进度通知；不将关闭自动认作成功。 |
| `EnteredReviewMode` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1643) | canonical enteredReviewMode / exitedReviewMode item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ExitedReviewMode` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1646) | canonical enteredReviewMode / exitedReviewMode item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RawResponseItem` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1648) | rawResponseItem/completed，条件内部关联 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `RawResponseCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1649) | rawResponse/completed，本轮忽略 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ItemStarted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1651) | item/started，按 19 种类型分流 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ItemCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1652) | item/completed，按 19 种类型分流 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `HookStarted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1653) | hook/started，条件消费 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `HookCompleted` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1654) | hook/completed，条件消费 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `AgentMessageContentDelta` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1656) | item/agentMessage/delta | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `PlanDelta` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1657) | item/plan/delta | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ReasoningContentDelta` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1658) | item/reasoning/summaryTextDelta | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `ReasoningRawContentDelta` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1659) | 原始 reasoning，不新增消费 | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabAgentSpawnBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1662) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabAgentSpawnEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1664) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabAgentInteractionBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1666) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabAgentInteractionEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1668) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabWaitingBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1670) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabWaitingEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1672) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabCloseBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1674) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabCloseEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1676) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabResumeBegin` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1678) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `CollabResumeEnd` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1680) | canonical collabAgentToolCall item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |
| `SubAgentActivity` [源码](/Users/bytedance/playground/codex/codex-rs/protocol/src/protocol.rs:1683) | canonical subAgentActivity item | 经对应 v2 通知或 canonical item 消费；不再增加独立 core 订阅。 |

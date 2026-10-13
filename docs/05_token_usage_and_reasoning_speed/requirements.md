# 本回合 Token 用量与最近推理均速：需求

日期：2026-10-13。状态：已实现并完成隔离运行验收；日常旧进程的加载边界见 [实现结果](implementation-results.md)。实施步骤见 [实现计划](implementation-plan.md)。

本轮为每个聊天增加 Output Tokens、Reasoning Tokens 和最近一次有效的 reasoning 平均速度。已有的流式生成速度继续保留。需求依据本次对话中用户最后确认的规则；此前文档中“不展示服务端 usage”的范围约定由本轮扩展，历史实验结论保留原时点。

## 1. 已确认的需求

| 编号 | 需求 | 规则 |
| --- | --- | --- |
| R1 | 显示本回合累计 Output Tokens | 使用服务端报告的总输出用量，包含 reasoning，与官方统计口径一致。 |
| R2 | 显示本回合累计 Reasoning Tokens | 显示上述总输出中用于 reasoning 的部分；不与 Output 再次相加。 |
| R3 | 累计数量按回合清零 | 每个 thread 的新 `turn/started` 生效时，将该回合的两个累计计数归零。 |
| R4 | 显示最近推理均速 | reasoning 结束且对应 usage 可可靠关联后，计算并显示该段的事后估算均速。 |
| R5 | 均速保留到新结果替换 | 下一段 reasoning 开始、进行中、结束后等待 usage，以及工具执行期间，均保留最近一次有效结果；新有效结果算出后直接替换。 |
| R6 | 不显示“统计中” | 没有新统计结果时保持已有数值；首次无均速结果时显示 `—`。 |
| R7 | 保留现有生成速度 | 接收文本重新分词得到的流式生成速度，与新增服务端用量和推理均速分别处理。 |

界面只有两个累计 token 计数，没有第三个“本轮累计 token”指标。本文中的“本回合累计用量”是对 Output 和 Reasoning 两个字段的统称；“回合”严格对应内部 turn，不推断用户任务的连续性。

## 2. 指标口径

### 2.1 Output Tokens 与 Reasoning Tokens

| 指标 | 官方 Responses usage 字段 | app-server 字段 | 含义 |
| --- | --- | --- | --- |
| Output Tokens | `output_tokens` | `outputTokens` | 服务端报告的全部生成 token，包含 reasoning、工具调用参数和不可见结构等。 |
| Reasoning Tokens | `output_tokens_details.reasoning_tokens` | `reasoningOutputTokens` | 服务端报告的推理 token，是 Output 的子集。 |

本回合累计值是归属于同一 thread、同一 turn 的已报告响应用量之和，或由有可靠回合基线的累计快照计算得到的等价结果。例如同一 turn 中两次响应分别报告 Output / Reasoning 为 `1,267 / 114`、`157 / 42`，本回合显示 `Output 1,424 · Reasoning 156`。

- 不累计 input、cached input、账户限额或当前上下文 token。
- 不把本地 tokenizer 的结果、可见思考摘要 token、delta 条数或加密内容长度填入服务端用量。
- `Output - Reasoning` 不等于回答正文 token。本轮不增加这个字段。
- 字段缺失不等于零；官方明确报告的零值才是零。
- 每次 usage 到达后更新累计计数，未完成响应的未知用量不作外推。因此数字可以在生成期间保持不变，随后分批增长。

官方定义见 [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning#managing-the-context-window) 和 [Output token counts](https://developers.openai.com/api/docs/guides/token-counting#understand-output-token-counts)。

### 2.2 回合与清零边界

- “本回合”对应真实 `turn_id`，以新 `turn/started` 为边界，不以一次模型 response、用户提交请求或 goal 状态变化为边界。
- 一个 turn 内可有多次模型响应，以及 reasoning、回答、工具参数生成和工具执行的交替。
- 同一回合内的追加输入、工具结束、reasoning 开始 / 结束和 response 完成不清零累计计数。只有确认进入新的 turn 才清零。
- 重复或恢复重放的同一 `turn/started` 不再次清零。旧回合晚到的 usage 只归属旧回合，不污染当前回合。
- turn 完成、中断或失败后保留该回合最后的已报告计数，直到下一回合开始。暂停、阻塞、用户插话或恢复本身不触发清零；实际出现新的 turn 才清零。
- 每个 thread 独立统计。子代理使用自己的 thread；不把子代理用量或父聊天中的活动镜像再次加到父聊天。

本次撤掉“跨 turn 合并为用户轮次”的要求，不增加整个 Goal 的累计量、Goal 专用清零规则或用户请求 / 工作任务归属判断。Goal 执行中的 turn 仍按统一规则显示：自动续跑启动新 turn 时计数归零，同一 turn 内继续执行则继续累计。后端明确延续同一 turn 的恢复不清零；恢复为新 turn 时清零。

不根据 goal 是否 active、用户有没有发言、rootTurnId 或输入语义决定是否合并。Goal 的完整执行不等于一个回合，界面明确标为“本回合 Token”，避免误解为整个目标的用量。

### 2.3 最近推理均速

```text
最近推理均速 ≈ 对应 reasoning tokens / 该 reasoning 段的观测持续时间
观测持续时间 = reasoning 结束事件的接收时间 - reasoning 开始事件的接收时间
```

这是一项事后估算，分子来自服务端 usage，分母来自本地观测到的阶段边界；不是隐藏 reasoning 的实时 token/s，也不是服务端纯计算吞吐。默认带 `≈`，使用内部完整时间精度计算，不使用界面已取整的耗时。

只在以下条件全部成立时生成新结果：

1. usage 所属 response、thread 和 turn 能可靠关联。
2. 该 response 的 reasoning 用量能唯一对应到一个完整观测的 reasoning 段。
3. 开始、结束边界真实存在，使用同一时钟，持续时间大于零，期间没有影响该段的观测缺口或身份冲突。
4. usage 字段合法，reasoning 项正常完成；不是恢复重放的开始时间或推测的结束时间。

如果一次 response 包含多段 reasoning 而 usage 只给合计，本轮不按时间比例拆分、不把合计全部分给最后一段，也不改用另一种“响应均速”冒充此指标。该次不产生新结果，继续保留最近一次有效结果；尚无有效结果时为 `—`。

不把正文 / 工具参数生成、工具执行、审批等待、用户输入等待、网络重试或等待 usage 的时间加入分母。reasoning 摘要的首尾 delta 不能替代 reasoning 生命周期边界；仅靠 `response.completed` 补出的结束时间也不足以计算该段均速。

### 2.4 均速保留与替换

| 事件 | 累计 Output / Reasoning | 最近推理均速 |
| --- | --- | --- |
| 首次完整观测到新 turn 开始 | `0 / 0` | 尚无历史结果时为 `—`。 |
| reasoning 开始或进行中 | 保持已报告累计值 | 保持最近一次有效结果。 |
| reasoning 已结束，usage 尚未到达 | 保持已报告累计值 | 保持最近一次有效结果，不显示“统计中”。 |
| usage 到达，关联与阶段边界有效 | 更新所属回合计数 | 新结果原子替换旧结果。 |
| usage 到达，无法分配到单段 reasoning | 可可靠归属时仍更新计数 | 不产生新均速，保留旧结果。 |
| 工具执行、等待、turn 完成 | 保持最后已报告值 | 保持最近一次有效结果。 |
| 该 thread 的下一 turn 开始 | 清零两个累计计数 | 保留该 thread 最近一次有效结果，直到新有效结果替换。 |

新 turn 只清零累计计数，均速跨 turn 保留。这是对“直到下一段 reasoning 的平均速度结果出来后再替换”的具体落地规则。

替换顺序按 reasoning 段的实际先后关系决定，不按 usage 到达顺序决定。较早段的迟到结果不能覆盖已展示的较新段结果。较早段的有效结果到达时，如果仍是该 thread 最近可用的结果，可以补齐展示；其用量仍只计入原 turn。

## 3. 展示约定

新增信息在每个聊天的主行中可见，不要求用户先展开详情。默认采用紧凑的五列布局：聊天 / 项目、当前活动、耗时、本回合 Token、速度。

“本回合 Token”单元格显示两行：

```text
Output 12,000
Reasoning 9,000
```

“速度”单元格保留现有生成速度，并增加最近推理均速：

```text
生成 ≈24.6 token/s
推理均速 ≈85.0 token/s
```

- 最近推理均速的完整指标名为“最近推理均速”，紧凑单元格可用“推理均速”；详情保留完整名称与所属模型 / 回合，便于区分跨回合保留的结果。
- token 计数显示精确整数与千位分隔，不用 `≈`，不四舍五入成 `12k`。均速默认保留一位小数和 `≈`。
- 正常完整观测的新 turn 从 `0 / 0` 开始，首次均速为 `—`。半途接入、缺失必要 usage 或无法恢复完整回合用量时，无法确认的字段用 `—`，不伪造 `0` 或完整累计值。
- 不增加“统计中”、通知、按钮或另一套用量面板；数据来源、完整性和关联失败原因保留在结构化诊断字段中。
- 窄终端可以换行，优先压缩聊天 / 活动文字，避免把两个 token 计数或均速悄悄隐藏；保持现有 1Hz 定时刷新和按键即时响应。
- 中文文案进入已有语言资源；Output / Reasoning 的字段口径不随语言变化。旧快照缺少新字段时显示 `—`。

## 4. 数据来源与当前实现

以下为 2026-10-13 工作区检查，代码基线提交 `11f7c8835228`；描述当前状态，不代表功能已经接入。

| 位置 | 当前行为 | 本轮需要补充 |
| --- | --- | --- |
| [events.py](../../src/codex_progress_monitor/events.py) | 未放行 `thread/tokenUsage/updated`；上游完成 response 只保留 ID、模型、状态、item IDs 等身份信息，usage 被移除。 | 放行所需数值字段与可靠身份，继续过滤其他响应内容。 |
| [engine.py](../../src/codex_progress_monitor/engine.py) | 已关联 backend / thread / turn / item，处理 reasoning 生命周期；未维护服务端回合用量或最近推理均速。 | 增加回合用量与 reasoning 结果状态，处理延迟、重复、冲突和作用域。 |
| [metrics.py](../../src/codex_progress_monitor/metrics.py) | 对接收文本累计重新分词，生成流式速度。 | 与服务端 usage 分离；现有 TextStream 计数不作为本轮累计用量。 |
| [bridge.py](../../src/codex_progress_monitor/bridge.py)、[collector.py](../../src/codex_progress_monitor/collector.py) | 有界缓存并恢复部分活动 / 元数据，重启后清除不可信实时速率。 | 恢复可验证的已报告计数与已完成均速；不从重放的活动补造计时。 |
| [display.py](../../src/codex_progress_monitor/display.py)、[中文资源](../../src/codex_progress_monitor/messages_zh_cn.py) | 四列布局，速度只有现有生成速度。 | 增加计数和最近推理均速，兼容旧快照与窄终端。 |

官方 [app-server 文档](https://learn.chatgpt.com/docs/app-server#turn-events)列出 `thread/tokenUsage/updated`。已检查的本地协议包含 threadId、turnId、累计 `total` 和最近响应 `last`，但该通知本身不提供 responseId 或 reasoning itemId，不能仅凭“最近一次 reasoning”强行配对。

既有实测已证明 usage 的到达可以晚于 reasoning 结束：2026-10-11 的一个样本中，reasoning 在相对时间 11.278s 结束，17.665s 才收到 Output 1,267 / Reasoning 114 的 usage。它用于支持延迟回填设计，不证明所有 reasoning 段均可唯一关联。该本机记录位于 `.local/product-preflight/live-20261011/protocol.jsonl`，未提交原始记录；公开精简证据另见 [上游实验报告](../../analysis/upstream-stream/FINDINGS.txt)与[验证数据](../../analysis/upstream-stream/verification.json)。

## 5. 恢复与异常边界

- 两个采集来源记录同一 response 时只计一次。`total` 是累计快照，不能逐条求和；`last` 可能被重发，不能无条件逐条累加。
- 多 backend 连接同一 home / thread 时不混加；切换后端必须核对逻辑身份、回合基线和去重状态。
- 服务端用量缺失、累计快照回退、上下文压缩 / 恢复 / 回退重放不能自动视为一个新 turn；无法解释的差值进入诊断，不能通过裁成零隐藏错误。
- 半途接入或观测缺口后，只有可靠恢复回合边界及计数基线 / 响应记录，才重新展示完整累计值。不能把线程历史总用量当作本轮用量。
- 已完成且来源完整的均速属于历史结果，不因下一段正在推理或短暂断连而清空。collector 重启后若可验证恢复该结果，可继续保留；无法验证时为 `—`。
- 新段跨越采集缺口、缺失开始 / 结束或只有重放生命周期时，不产生新均速。现有实时生成速度仍按原来的断连 / 过期规则失效。
- 回合完成后晚到且身份有效的 usage 可以回填该回合。回合已经切换时，不能写入当前回合计数。
- 缓存、待关联事件和去重记录均有容量 / 生命周期限制，正常展示和恢复不需要保存隐藏 reasoning、完整模型 response、用户输入或工具结果。

## 6. 验收条件

| 编号 | 验收内容 |
| --- | --- |
| A1 | Output 包含 Reasoning；示例两次 usage 后显示 1,424 / 156，无重复相加、无第三个累计字段。 |
| A2 | 新 turn 清零累计计数，无论是否由 Goal 续跑触发；同 turn 的 response、reasoning、工具、追加输入、暂停 / 恢复和重放不清零；不合并多个 turn。 |
| A3 | 同一响应通过两条来源或重复通知到达只计一次；旧回合晚到 usage 不污染新回合。 |
| A4 | 有唯一关联、完整边界与服务端 reasoning 用量时计算正确均速；等待 usage 与工具时间不进入分母。 |
| A5 | 下一段开始、等待 usage 和下一 turn 开始均保留最近均速；新有效结果直接替换，旧结果迟到不倒退。 |
| A6 | 多段 reasoning 合计、缺失字段、非正耗时、身份冲突、重放或观测缺口不生成伪均速；均速首次无结果为 `—`。 |
| A7 | 首次完整回合为 0 / 0；半途接入与恢复失败不显示伪零或线程历史累计；已完成静态结果可以可靠恢复。 |
| A8 | 多聊天、子代理、不同 home 与重复 backend 不串账；后台活动不影响计数回合边界。 |
| A9 | 主行以“本回合 Token”展示两个计数与两种速度，无“统计中”；旧快照、窄终端、1Hz、中文资源与现有流式速度正常。 |
| A10 | 完成已有真实记录回放，并记录最小运行验证、进程加载情况与未验证边界；敏感原始内容不进入新增快照 / 恢复缓存。 |

源码检查、已有实测、回放验证和新版本运行生效分别记录。实施完成后更新本目录结果文档，不以需求文档已写完替代实现或运行验收。

# 活动文案清单与修改工作表

日期：2026-10-26。清单采集基线：`6b7825a`。状态：下列文案已分两轮实施、通过定向检查并完成日常 App 重载，详见[实施结果](implementation-results.md)。下方完整清单的“当前”列保留修改前快照，后续查阅以本节和实施结果为准。

## 已确定的核心文案

| 活动 | 列表 | 图例 | 详情类型 | 讨论状态 |
| --- | --- | --- | --- | --- |
| 思考 | Reasoning／思考中 | Reasoning／思考 | Reasoning／思考 | 已实施 |
| 回复 | Responding／回复中 | Response／回复 | Response／回复 | 已实施 |
| 工具参数生成 | Preparing · 工具名；没有对象名时只显示 Preparing | Tools／工具 | Tool params／工具参数 | 已实施；列表保留原有表达 |
| 等审批／等用户输入 | Pending／等待中 | — | 本轮未提出变更 | 已实施，不展开等待原因 |
| 文件编辑 | Editing · 文件名／编辑中 · 文件名 | Edits／改动 | File edits／文件改动 | 已实施；列表和图例保留原文案 |
| 压缩 | Compacting／压缩中 | Compaction／压缩 | Compaction／压缩 | 已实施 |

Preparing 继续用一个动作词，具体对象由工具名补充，详情用 Tool params 说明内容。审批／用户输入等待使用独立的 activity.pending，其他目标暂停或受限分支保留 Paused。内容页签 Input / Result / Visible reasoning summary 保留原名。

以下完整清单保留作查阅，不需要逐行处理。

覆盖范围：聊天列表、时间线（图例、轨道、悬浮提示）和点击时间块打开的 Activity Viewer，包含活动名、状态词、协作动作、内容标签。附录补齐这些界面的按钮、空状态、来源说明、观测提示、输入浮层和邻近统计文案。设置、接入／卸载流程、CLI 帮助与整套终端界面不在本次清单范围内；共用词库的补充项单独标出。

依据源码分支与中英文资源核对，不代表每种状态均已在运行中的 App 重现。聊天／项目名、模型名、工具名、命令、路径、用户输入、模型正文和工具结果按来源展示，不作为固定 UI 文案逐条收录。

## 如何修改

1. 在“修改后英文／中文”栏直接填写。留空表示待定；保持原样请填“保留”。需要删除、拆分或合并，可写“删除”“拆分为……”或“合并到 编号”。
2. 保留当前文案与定位键作为基线。编号只用于本文审阅，不是程序中的消息键。
3. 保留需要的变量，例如 `{target}`、`{count}`、`{activity}`、`%d`、`%@`。`{target}` 非空时，现有渲染器自动添加 ` · `：`Preparing{target}` 显示成 `Preparing · python`；为空时只显示 `Preparing`。
4. 本文是可编辑工作表，填写后再按最终选择实施。顶部各项已按用户要求完成，其他留空项不自动更改 App 或后端资源。

当前优先查看最前面的核心文案汇总。第 1–11 节保留表达规则与核心活动的完整底表；第 12–16 节是长句、周边控件和共享资源；第 17 节记录其他语义问题，后续按需要讨论。

## 1. 表达规则与核心术语（以本轮汇总为准）

此前候选方向是列表使用动作、图例与详情使用活动名称、内容标签使用名词、生命周期状态独立表达。现已确认 Reasoning 与 Responding / Response；工具参数项允许图例用 Tools、详情用更具体的短称，不再要求图例和详情逐字同名。此前答复中的 Generating a response、Executing a tool 等仅为示例，不作为已确认文案。源码基线仍以下面“当前”列为准。

| 决策项 | 候选方向 | 用户决定 / 补充 |
| --- | --- | --- |
| 思考核心词 | 统一用 Reasoning；中文使用“思考中／思考” | 已确认 |
| 聊天列表 | 动作表达；英文以现在分词形式为主 |  |
| 图例与详情类型 | 活动名称；允许图例使用更短的概括词 | 已实施：思考、回复同名；工具参数图例用 Tools，详情用 Tool params |
| 内容标签 | 内容名称，如 Input、Result、Reasoning summary |  |
| 大小写 | 使用句首大写 sentence case | 本次采用 Tool params、File edits；第二个单词小写 |
| 任务与执行者 | 分别确定 subtask / subagent 和“子任务／子代理／子智能体”用法 |  |
| 未知与观测不完整 | 分别确定生命周期未知、观测缺口、内容缺失的称呼 |  |

### 当前跨界面对照（只读索引，修改填后面的逐项表）

| 活动 | 聊天列表 | 图例 | 详情类型 | 内容标签 |
| --- | --- | --- | --- | --- |
| 思考 | Thinking | Thinking | Reasoning | Visible reasoning summary；实时补充时可能为 Live content |
| 回复生成 | Generating response | Reply | Response output | Response；实时补充时可能为 Live content |
| 工具参数生成 | Preparing · … | Params | Tool input | Input / Result |
| 编辑内容生成 | Editing · 文件名 | Edits | Edit input | Input / Result |
| 工具执行 | Running · …，或搜索／图片／协作等动作 | 无固定图例项 | Tool execution，或 Coordination 动作 | Input / Result |
| 应用文件改动 | Editing · 文件名；目标未知时可能为 File edit | 无固定图例项 | Tool execution | Input / Result |
| 上下文压缩 | Compacting context | Compacting | Compacting context | 通常无标签，显示无内容说明 |
| 计划生成 | Updating plan | 归入 Reply | 归入 Response output | Response；实时补充时可能为 Live content |
| 用户输入记录 | 不直接作为当前工作动作 | 无固定图例项 | User input | User input / Image attachments |

列表 Paused 可能表示等待用户输入，与时间线中一条用户消息的 User input 不是同一语义。Preparing 同时用于参数生成和工具服务启动等待；Processing 也覆盖多种过渡状态。

## 2. 聊天列表：模型活动、阶段与等待状态

来源：Python activity_message / presentation 生成消息，Swift Chat.activityLabel 渲染。以下保留实际模板。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| A01 | Thinking | 思考中 | 思考阶段；`activity.reasoning` | Reasoning | 保留 |
| A02 | Generating response | 生成回答 | 生成回复正文；`activity.text` | Responding | 回复中 |
| A03 | Updating plan | 整理计划 | 生成或更新计划；`activity.plan` |  |  |
| A04 | Compacting context | 压缩上下文 | 压缩上下文；`activity.compaction` | Compacting | 压缩中 |
| A05 | Preparing{target} | 准备中{target} | 生成工具参数，或命名工具的依赖服务尚在启动；目前共用此键；`activity.preparing` | 保留 | 保留 |
| A06 | Running{target} | 运行中{target} | 已确认执行的通用工具／命令动作；`activity.running` |  |  |
| A07 | Processing{target} | 处理中{target} | 工具就绪待确认执行、hook、短暂服务／等待转换，或已有活动但无具体主活动；`activity.processing` |  |  |
| A08 | Getting ready | 准备开始 | 回合进行中但尚无活动记录；`activity.waiting` |  |  |
| A09 | Waiting | 等待中 | 明确的 sleep／等待活动；最初 3 秒可能暂用 Processing；`activity.sleep` |  |  |
| A10 | Waiting for output | 等待输出 | 输出安全缓冲等待；最初短暂阶段可能用 Processing；`activity.buffering` |  |  |
| A11 | Connecting to service | 连接服务 | 服务认证／连接恢复；最初短暂阶段可能用 Processing；`activity.auth` |  |  |
| A12 | Retrying | 正在重试 | 错误后重试；最初短暂阶段可能用 Processing；`activity.retry` |  |  |
| A13 | Automatic review | 自动审核中 | 自动审批审核；`activity.review` |  |  |
| A14 | Paused | 已暂停 | 修改前共用 activity.paused；审批／用户输入现使用独立 activity.pending，其他分支保留 | 审批／输入：Pending；其余保留 | 审批／输入：等待中；其余保留 |
| A15 | Status unavailable | 状态暂不可用 | 有未决工作、系统错误或观测不足等，无法确认状态；`activity.unknown` |  |  |
| A16 | Just completed · {activity} | 刚完成 · {activity} | 成功完成活动后约 1 秒内的提示；activity 为嵌套活动名称；`activity.recent` |  |  |

## 3. 聊天列表：具体工具与文件动作

文件编辑活动可能处于生成内容或应用改动阶段，列表共用 Editing。缺少文件目标时可退回 File edit，不应据此判定写入成功。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| F01 | Searching the web | 搜索网页 | 网页搜索工具执行；`activity.web` |  |  |
| F02 | Viewing an image | 查看图片 | 查看图片工具执行；`activity.image_view` |  |  |
| F03 | Generating an image | 生成图片 | 图片生成工具执行；`activity.image_generation` |  |  |
| F04 | Editing{target} | 编辑中{target} | 可识别文件目标的编辑活动进行中；`activity.file_editing` | 保留 | 保留 |
| F05 | Editing{target} (+{count} more) | 编辑中{target}（另 {count} 个） | 同上，另有 count 个文件；`activity.file_editing_more` |  |  |
| F06 | File edit{target} | 编辑文件{target} | 已结束编辑的名称，或缺少文件目标时的中性显示；不一定表示已成功写入；`activity.file_edited` |  |  |
| F07 | File edit{target} (+{count} more) | 编辑文件{target}（另 {count} 个） | 已结束的多文件编辑名称，另有 count 个文件；`activity.file_edited_more` |  |  |
| F08 | File change{target} | 修改文件{target} | 文件改动来源名；也用于时间线执行块的次级来源标签；`activity.file_change` |  |  |
| F09 | File change{target} (+{count} more) | 修改文件{target}（另 {count} 个） | 带额外文件数的共享资源；当前 activity_message 不直接选用此数量变体；`activity.file_change_more` |  |  |

## 4. 聊天列表：协作动作

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| C01 | Waiting for subagents | 等待子任务 | 等待子代理；`activity.agent_wait` |  |  |
| C02 | Assigning a subtask | 分配子任务 | 派发子任务；`activity.agent_spawn` |  |  |
| C03 | Updating a subtask | 更新子任务 | 向子代理输入、发送消息、追加任务目前共用的列表动作；`activity.agent_update` |  |  |
| C04 | Resuming a subagent | 恢复子任务 | 恢复子代理；`activity.agent_resume` |  |  |
| C05 | Closing a subagent | 结束子任务 | 关闭子代理；`activity.agent_close` |  |  |
| C06 | Interrupting a subagent | 暂停子任务 | 中断子代理；当前中文写成“暂停”；`activity.agent_interrupt` |  |  |
| C07 | Listing subagents | 查看子任务 | 列出子代理；`activity.agent_list` |  |  |
| C08 | Working on subtasks | 处理子任务 | 其他协作工作兜底；`activity.agent_work` |  |  |

## 5. 聊天列表：GUI 直接显示的状态与标记

列表先判断数据可用性，再判断当前聊天及子树是否有工作，最后显示结束状态。此处的 Completed / Failed 与后端 Task completed / Task failed 是不同资源入口。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| L01 | Status unavailable | 状态暂不可用 | 数据过期、断开或聊天源不可用；优先覆盖当前动作；App 键 `状态暂不可用` |  |  |
| L02 | Subagents working | 子代理工作中 | 聊天自身无活动，其子树仍在工作；App 键 `子代理工作中` |  |  |
| L03 | Completed | 已完成 | 无活动时 status=completed；App 键 `已完成` |  |  |
| L04 | Failed | 失败 | 无活动时 status=failed；App 键 `失败` |  |  |
| L05 | Stopped | 已停止 | 无活动时 status=interrupted；App 键 `已停止` |  |  |
| L06 | Unknown status | 状态未知 | 无活动且 status 不在三个已知终态中；App 键 `状态未知` |  |  |
| L07 | Subtask | 子任务 | 已确认归属的子代理列表标记；App 键 `子任务` |  |  |
| L08 | Unassigned | 归属待确认 | 子代理归属不确定时的标记；App 键 `归属待确认` |  |  |
| L09 | Untitled chat | 未命名聊天 | 缺少聊天标题时的占位；Model 键 `未命名聊天` |  |  |

## 6. 时间线图例

前五项是固定颜色图例，Coordination 在存在协作活动时额外显示。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| G01 | Thinking | 思考 | 固定图例；kind=reasoning；App 键 `思考` | Reasoning | 保留 |
| G02 | Reply | 回复 | 固定图例；kind=text；App 键 `回复` | Response | 保留 |
| G03 | Params | 参数 | 固定图例；kind=tool_input；App 键 `参数` | Tools | 工具 |
| G04 | Edits | 改动 | 固定图例；kind=file_edit_input；App 键 `改动` | 保留 | 保留 |
| G05 | Compacting | 压缩 | 固定图例；kind=contextCompaction；App 键 `压缩` | Compaction | 保留 |
| G06 | Coordination | 协作 | 存在协作活动时显示的额外说明；App 键 `协作` |  |  |
| G07 | （无固定图例项） | （无固定图例项） | tool_execution 有独立颜色；此行可决定是否补充图例 |  |  |

## 7. 时间块详情：活动类型

也用于时间块悬浮／辅助功能描述。工具来源名在类型旁单独显示。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| K01 | Reasoning | 思考 | kind=reasoning；Model 键 `思考` | 保留 | 保留 |
| K02 | Response output | 正文输出 | kind=text；Model 键 `正文输出` | Response | 回复 |
| K03 | Tool input | 工具参数生成 | kind=tool_input；Model 键 `工具参数生成` | Tool params | 工具参数 |
| K04 | Edit input | 编辑内容生成 | kind=file_edit_input；Model 键 `编辑内容生成` | File edits | 文件改动 |
| K05 | User input | 用户输入 | kind=input；Model 键 `用户输入` |  |  |
| K06 | Tool execution | 工具执行 | kind=tool_execution；Model 键 `工具执行` |  |  |
| K07 | Compacting context | 整理上下文 | kind=contextCompaction；Model 键 `整理上下文` | Compaction | 压缩 |
| K08 | Activity | 活动 | kind=其他 kind；英文兜底为 Activity，中文可能直接使用来源 label；Model 键 `活动` |  |  |

同一键“思考”在 Model 英文资源中为 Reasoning，在 App 英文资源中为 Thinking。计划在后端映射为 text，因此当前没有独立 Plan 类型标题。

## 8. 时间块详情：协作类型与目标

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| J01 | Coordination · Spawn | 协作 · 派发 | coordination_action=spawn；Model 键 `协作 · 派发` |  |  |
| J02 | Coordination · Send message | 协作 · 发送消息 | send；Model 键 `协作 · 发送消息` |  |  |
| J03 | Coordination · Follow-up | 协作 · 追加任务 | followup；Model 键 `协作 · 追加任务` |  |  |
| J04 | Coordination · Wait | 协作 · 等待 | wait，指定目标；Model 键 `协作 · 等待` |  |  |
| J05 | Coordination · Wait for subagent messages | 协作 · 等待子智能体消息 | wait，全局消息等待；Model 键 `协作 · 等待子智能体消息` |  |  |
| J06 | Coordination · Close | 协作 · 关闭 | close；Model 键 `协作 · 关闭` |  |  |
| J07 | Coordination · Resume | 协作 · 恢复 | resume；Model 键 `协作 · 恢复` |  |  |
| J08 | Coordination call | 协作调用 | 未知协作动作兜底；Model 键 `协作调用` |  |  |
| J09 | Tool input · {action} | 工具参数生成 · {action} | 参数生成阶段组合显示；action 为上述协作类型；Model 前缀键 `工具参数生成 · ` |  |  |
| J10 | Waiting for subagent messages | 等待子智能体消息 | 全局等待的目标描述；Model 键 `等待子智能体消息` |  |  |
| J11 | %d agents | %d 个智能体 | 多个目标的计数；该分支用于数量大于 1；Model 键 `%d 个智能体` |  |  |
| J12 | Unknown target | 对象未知 | 缺少目标身份；Model 键 `对象未知` |  |  |
| J13 | Agent ·  | 智能体 ·  | 未解析到显示名的目标前缀，随后附加 ID 后缀；Model 键 `智能体 · ` |  |  |
| J14 | Agent | 智能体 | 目标详情中的通用执行者称呼；Model 键 `智能体` |  |  |

## 9. 时间块详情：生命周期状态

与活动类型、内容读取状态分别表达。默认分支当前使用 Partial observation，“状态未知”和“观测不完整”在这里有复用。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| S01 | Active | 进行中 | status=active；Model 键 `进行中` |  |  |
| S02 | Completed | 已完成 | status=completed；Model 键 `已完成` |  |  |
| S03 | Failed | 失败 | status=failed；Model 键 `失败` |  |  |
| S04 | Stopped | 已停止 | status=interrupted；Model 键 `已停止` |  |  |
| S05 | Blocked, not executed | 已阻止，未执行 | status=declined；Model 键 `已阻止，未执行` |  |  |
| S06 | Waiting to execute | 等待执行 | status=waiting；Model 键 `等待执行` |  |  |
| S07 | Partial observation | 部分观测 | status=其他状态；Model 键 `部分观测` |  |  |

## 10. 时间线：子代理在所选父回合中的状态

仅在 status_scope=selected_parent_turn 时按以下状态展示，不能据此判定子代理全部历史的最终状态。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| R01 | Active in this turn | 本轮活动中 | active；Model 键 `本轮活动中` |  |  |
| R02 | Waiting in this turn | 本轮等待中 | waiting；Model 键 `本轮等待中` |  |  |
| R03 | Ended in this turn | 本轮已结束 | completed；Model 键 `本轮已结束` |  |  |
| R04 | Stopped in this turn | 本轮已停止 | interrupted；Model 键 `本轮已停止` |  |  |
| R05 | Failed in this turn | 本轮失败 | failed；Model 键 `本轮失败` |  |  |
| R06 | Unknown status | 状态未知 | 未知状态或 scope 不匹配；Model 键 `状态未知` |  |  |

## 11. 详情内容标签与缺失提示

标签由历史记录或实时补充决定。实时文本可先显示 Live content，再由历史补齐成 Response 或 Visible reasoning summary。空思考记录有专门的空白展示分支，不保证每种无内容状态都显示提示。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| D01 | Input | 参数 | 工具参数页签；`detail.input` |  |  |
| D02 | Result | 结果 | 工具结果页签；`detail.output` |  |  |
| D03 | Response | 正文 | 回复或计划正文页签；`detail.body` |  |  |
| D04 | Visible reasoning summary | 可见思考摘要 | 可见思考摘要页签；`detail.reasoning` |  |  |
| D05 | User input | 用户输入 | 用户输入正文页签；`detail.user` |  |  |
| D06 | Live content | 实时内容 | 实时文本补充页签；`detail.live` |  |  |
| D07 | Image attachments | 图片附件 | 图片附件说明页签；`detail.attachments` |  |  |
| D08 | Activity content | 活动内容 | 其他来源的通用内容页签；`detail.content` |  |  |
| D09 | The source record provides no separate input. | 源记录未提供独立参数。 | 来源没有独立参数；`detail.missing_input` |  |  |
| D10 | The source has no result for this call yet. | 来源中尚无这次调用的结果。 | 来源尚无调用结果；`detail.missing_output` |  |  |
| D11 | This input contains images. See the preview below. | 此条输入包含图片，预览见下方。 | 图片输入的正文说明；`detail.image_notice` |  |  |

## 12. 详情内容读取状态与来源说明

输入浮层也复用部分提示，但它对 missing / no_content 等状态的合并方式与 Activity Viewer 有差异。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| N01 | This source cannot load more content. Update Monitor and reload. | 当前来源不支持继续加载，请更新 Monitor 后重新读取。 | 旧详情协议不能继续分页；App 键 `当前来源不支持继续加载，请更新 Monitor 后重新读取。` |  |  |
| N02 | The source content changed. Your loaded content is preserved; reload to continue. | 源内容已变化。保留已加载内容，请重新读取。 | content_changed；App 键 `源内容已变化。保留已加载内容，请重新读取。` |  |  |
| N03 | The source was truncated. All content retained by Codex is shown below. | 来源已截断，以下是 Codex 保留的全部可用内容。 | source_truncated；App 键 `来源已截断，以下是 Codex 保留的全部可用内容。` |  |  |
| N04 | Content is partially observed. History will fill it in after completion. | 当前内容为部分观测，完成后将从历史补齐。 | partial；这里只记录当前文案，不保证必然补齐；App 键 `当前内容为部分观测，完成后将从历史补齐。` |  |  |
| N05 | Full content has not been written to history yet. | 完整内容尚未写入历史。 | pending；App 键 `完整内容尚未写入历史。` |  |  |
| N06 | Source history was removed or this content is unavailable. | 源历史已移除或当前内容不可用。 | missing；输入浮层也用于 no_content；App 键 `源历史已移除或当前内容不可用。` |  |  |
| N07 | No readable source history was found. | 当前找不到可读取的源历史。 | history_unavailable；App 键 `当前找不到可读取的源历史。` |  |  |
| N08 | The source history has no content for this activity yet. Try reading it again later. | 源历史中尚无这项活动的可用内容，可稍后重新读取。 | content_unavailable；App 键 `源历史中尚无这项活动的可用内容，可稍后重新读取。` |  |  |
| N09 | The source provides no viewable content. Activity status and timing are shown above. | 来源未提供可查看内容，活动状态与时间见上方。 | no_content；App 键 `来源未提供可查看内容，活动状态与时间见上方。` |  |  |
| N10 | Codex history did not retain this unfinished content. | 这段未完成的内容未被 Codex 历史保留。 | not_retained；App 键 `这段未完成的内容未被 Codex 历史保留。` |  |  |
| N11 | Cannot read source history right now. Try again later. | 暂时无法读取源历史，可稍后重试。 | unavailable；输入浮层也用于其他未细分状态；App 键 `暂时无法读取源历史，可稍后重试。` |  |  |
| N12 | This source does not support reading this content type. | 当前来源不支持读取此类内容。 | unsupported；App 键 `当前来源不支持读取此类内容。` |  |  |
| N13 | Reading activity content… | 正在读取活动内容… | 尚无 detail 的默认提示；App 键 `正在读取活动内容…` |  |  |
| N14 | The source record is an empty string. | 来源记录为空字符串。 | 所选内容存在但为空字符串；App 键 `来源记录为空字符串。` |  |  |
| N15 | Activity records retained by Codex | Codex 保留的活动记录 | 来源状态栏通用说明；App 键 `Codex 保留的活动记录` |  |  |
| N16 | Locally observed activity metadata | 本地观测的活动元数据 | observed_metadata；App 键 `本地观测的活动元数据` |  |  |
| N17 | Synthetic preview records | 合成预览记录 | synthetic；App 键 `合成预览记录` |  |  |

当前 source=observed_stream 的部分文本也可能在正常状态栏分支显示“Codex 保留的活动记录”；来源说明是否细分可在对应行提出。

## 13. 附录：周边控件、称呼、说明与异常提示

补齐所列界面源文件中其余固定本地化文案，包括按钮、辅助功能、时间／数量模板和统计说明。同一资源包的同一键只列一次；App 与 Model 分别持有的同名资源保留各自定位。较长提示可以最后再改。

`.one` 是英文数量为 1 时选用的单数资源；中文不走这个分支。预览或兼容路径的资源在使用位置中另作说明。

### 13.1 App 资源

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| U01 | %d active chats | %d 个活跃聊天 | App/DashboardView.swift；App 键 `%d 个活跃聊天` |  |  |
| U02 | %d active chat | %d 个活跃聊天 | App/DashboardView.swift；App 键 `%d 个活跃聊天.one` |  |  |
| U03 | %d ms | %d 毫秒 | App/ActivityViewer.swift；App 键 `%d 毫秒` |  |  |
| U04 | %d tools | %d 项工具 | App/ActivityViewer.swift；App 键 `%d 项工具` |  |  |
| U05 | %d tool | %d 项工具 | App/ActivityViewer.swift；App 键 `%d 项工具.one` |  |  |
| U06 | Activity lifecycle recorded by Codex. | Codex 记录的活动生命周期。 | App/ActivityViewer.swift；App 键 `Codex 记录的活动生命周期。` |  |  |
| U07 | Read-only preview | 只读预览 | App/DashboardView.swift；只读预览模式；App 键 `ReadOnly` |  |  |
| U08 | Main agent | 主智能体 | App/ActivityViewer.swift / App/TimelinePanelView.swift / App/TimelineSurface.swift；App 键 `主智能体` |  |  |
| U09 | Close notice | 关闭提示 | App/ChatDetailsView.swift / App/DashboardView.swift；App 键 `关闭提示` |  |  |
| U10 | Close input popover | 关闭输入浮层 | App/InputPopover.swift；App 键 `关闭输入浮层` |  |  |
| U11 | %d turns earlier · %@ | 前 %d 轮 · %@ | App/TimelinePanelView.swift；App 键 `前 %d 轮 · %@` |  |  |
| U12 | %d turn earlier · %@ | 前 %d 轮 · %@ | App/TimelinePanelView.swift；App 键 `前 %d 轮 · %@.one` |  |  |
| U13 | Earlier turns still active | 前轮仍有活动 | App/TimelinePanelView.swift；App 键 `前轮仍有活动` |  |  |
| U14 | Earlier turn activity | 前轮活动 | App/TimelinePanelView.swift；App 键 `前轮活动` |  |  |
| U15 | History lacks turn identifiers; some activity cannot be recovered. | 历史缺少轮次字段，部分轨迹不可恢复。 | App/TimelinePanelView.swift；App 键 `历史缺少轮次字段，部分轨迹不可恢复。` |  |  |
| U16 | Historical turn | 历史轮次 | App/ActivityViewer.swift；App 键 `历史轮次` |  |  |
| U17 | Visible time range | 可见时间范围 | App/TimelinePanelView.swift；App 键 `可见时间范围` |  |  |
| U18 | Including reasoning %@ | 含推理 %@ | App/ChatDetailsView.swift；App 键 `含推理 %@` |  |  |
| U19 | Back to latest | 回到最新 | App/TimelinePanelView.swift；App 键 `回到最新` |  |  |
| U20 | Copy | 复制 | App/ActivityViewer.swift；App 键 `复制` |  |  |
| U21 | Copy current content | 复制当前内容 | App/ActivityViewer.swift；App 键 `复制当前内容` |  |  |
| U22 | Copy loaded content | 复制当前已加载内容 | App/ActivityViewer.swift / App/InputPopover.swift；App 键 `复制当前已加载内容` |  |  |
| U23 | Subagent | 子智能体 | App/TimelinePanelView.swift / App/TimelineSurface.swift；App 键 `子智能体` |  |  |
| U24 | Subagent activity | 子智能体活动 | App/ActivityViewer.swift；App 键 `子智能体活动` |  |  |
| U25 | Subagent activity is not fully linked yet | 子智能体轨迹尚未完整关联 | App/TimelinePanelView.swift；App 键 `子智能体轨迹尚未完整关联` |  |  |
| U26 | Full content has not been written to history yet | 完整内容尚未写入历史 | App/TimelineClient.swift；无句号变体来自 FixtureTimelineClient，不是正式查询的 pending 文案；App 键 `完整内容尚未写入历史` |  |  |
| U27 | Overview | 工作概览 | App/DashboardView.swift；App 键 `工作概览` |  |  |
| U28 | Tool | 工具 | App/TimelinePanelView.swift；App 键 `工具` |  |  |
| U29 | Expanded | 已展开 | App/TimelinePanelView.swift；App 键 `已展开` |  |  |
| U30 | Collapsed | 已折叠 | App/TimelinePanelView.swift；App 键 `已折叠` |  |  |
| U31 | Observed generation speed | 当前观测到的生成速度 | App/DashboardView.swift；App 键 `已接入聊天的生成总速度` |  |  |
| U32 | Observed subtotal %@ | 已观测小计 %@ | App/DashboardView.swift；App 键 `已观测小计 %@` |  |  |
| U33 | Unassigned subtasks (%d) | 归属待确认的子任务（%d） | App/DashboardView.swift；App 键 `归属待确认的子任务（%d）` |  |  |
| U34 | Unassigned subtask (%d) | 归属待确认的子任务（%d） | App/DashboardView.swift；App 键 `归属待确认的子任务（%d）.one` |  |  |
| U35 | Speed cannot currently be measured reliably. | 当前无法可靠计算速度。 | App/ChatRowView.swift；App 键 `当前无法可靠计算速度。` |  |  |
| U36 | Current model | 当前模型 | App/ChatDetailsView.swift；App 键 `当前模型` |  |  |
| U37 | No active chats | 当前没有活跃聊天 | App/DashboardView.swift；App 键 `当前没有活跃聊天` |  |  |
| U38 | Current turn | 当前轮 | App/ActivityViewer.swift / App/TimelinePanelView.swift；App 键 `当前轮` |  |  |
| U39 | Owning agent | 所属智能体 | App/ActivityViewer.swift；App 键 `所属智能体` |  |  |
| U40 | The selected content is too large to load at once. | 所选内容过多，暂时无法一次载入。 | App/TimelineClient.swift；App 键 `所选内容过多，暂时无法一次载入。` |  |  |
| U41 | No readable initial input for the selected turn | 所选轮次没有可读取的初始输入 | App/TimelinePanelView.swift；App 键 `所选轮次没有可读取的初始输入` |  |  |
| U42 | Statistics for the selected turn are unavailable | 所选轮次统计暂不可用 | App/ChatDetailsView.swift；App 键 `所选轮次统计暂不可用` |  |  |
| U43 | Estimated from received events; not pure server computation time. | 接收事件窗口估算，并非服务端纯计算耗时。 | App/ActivityViewer.swift；App 键 `接收事件窗口估算，并非服务端纯计算耗时。` |  |  |
| U44 | Hide completed | 收起已完成 | App/DashboardView.swift；App 键 `收起已完成` |  |  |
| U45 | Zoom in timeline | 放大时间线 | App/TimelinePanelView.swift；App 键 `放大时间线` |  |  |
| U46 | Timeline unavailable | 时间线暂不可用 | App/TimelineClient.swift；App 键 `时间线暂不可用` |  |  |
| U47 | Show recently completed (%d) | 显示最近完成（%d） | App/DashboardView.swift；App 键 `显示最近完成（%d）` |  |  |
| U48 | Show recently completed (%d) | 显示最近完成（%d） | App/DashboardView.swift；App 键 `显示最近完成（%d）.one` |  |  |
| U49 | Cannot load more content right now | 暂时无法读取更多内容 | App/InputPopover.swift / App/TimelineStore.swift；App 键 `暂时无法读取更多内容` |  |  |
| U50 | Cannot read activity content right now | 暂时无法读取活动内容 | App/InputPopover.swift / App/TimelineStore.swift；App 键 `暂时无法读取活动内容` |  |  |
| U51 | Cannot read the timeline right now | 暂时无法读取轨迹 | App/TimelineClient.swift / App/TimelineStore.swift；App 键 `暂时无法读取轨迹` |  |  |
| U52 | Latest turn | 最近一轮 | App/TimelinePanelView.swift；App 键 `最近一轮` |  |  |
| U53 | Latest turn | 最近一轮活动 | App/TimelinePanelView.swift；App 键 `最近一轮活动` |  |  |
| U54 | Untitled chat | 未命名聊天 | App/ActivityViewer.swift；App 键 `未命名聊天` |  |  |
| U55 | This turn | 本轮活动 | App/TimelinePanelView.swift；App 键 `本轮活动` |  |  |
| U56 | Turn input | 本轮用户输入 | App/InputPopover.swift；App 键 `本轮用户输入` |  |  |
| U57 | View available activities | 查看可用活动 | App/TimelinePanelView.swift；App 键 `查看可用活动` |  |  |
| U58 | View execution | 查看执行 | App/ActivityViewer.swift；App 键 `查看执行` |  |  |
| U59 | View turn input | 查看本轮用户输入 | App/TimelinePanelView.swift；App 键 `查看本轮用户输入` |  |  |
| U60 | Model | 模型 | App/TimelinePanelView.swift；App 键 `模型` |  |  |
| U61 | Model above, tools below | 模型在上，工具在下 | App/TimelinePanelView.swift；App 键 `模型在上，工具在下` |  |  |
| U62 | Processing | 正在处理 | App/DashboardView.swift；App 键 `正在处理` |  |  |
| U63 | %d seconds per screen | 每屏 %d 秒 | App/TimelinePanelView.swift；App 键 `每屏 %d 秒` |  |  |
| U64 | Activity image | 活动图片 | App/ActivityViewer.swift；App 键 `活动图片` |  |  |
| U65 | Activity timeline. Use left and right arrows to select an activity, then Return to open it. | 活动时间线，左右方向键选择活动，回车打开 | App/TimelineSurface.swift；App 键 `活动时间线，左右方向键选择活动，回车打开` |  |  |
| U66 | Activity Viewer | 活动查看器 | App/ActivityViewer.swift；App 键 `活动查看器` |  |  |
| U67 | Activity timeline | 活动轨迹 | App/TimelinePanelView.swift；App 键 `活动轨迹` |  |  |
| U68 | Generation is estimated over a live window from received, distinct content streams. | 生成是实时窗口估算，统计已接收的独立生成内容。 | App/ChatRowView.swift；App 键 `生成是实时窗口估算，统计已接收的独立生成内容。` |  |  |
| U69 | Short tools appear as thick marks; overlaps have stronger colors. Click to view tool details. | 短工具以粗线标记；重叠处增强颜色，点击直接查看此处的工具详情。 | App/TimelinePanelView.swift；App 键 `短工具以粗线标记；重叠处增强颜色，点击直接查看此处的工具详情。` |  |  |
| U70 | Short activities appear as thick marks. Mark width does not indicate duration; click for details. | 短活动以粗线标记，符号宽度不代表耗时；点击查看活动详情。 | App/TimelinePanelView.swift；App 键 `短活动以粗线标记，符号宽度不代表耗时；点击查看活动详情。` |  |  |
| U71 | The system clock changed; activities in this turn cannot be aligned reliably. | 系统时间发生变化，这轮活动无法可靠对齐。 | App/TimelinePanelView.swift；App 键 `系统时间发生变化，这轮活动无法可靠对齐。` |  |  |
| U72 | About these statistics | 统计口径说明 | App/ChatDetailsView.swift；App 键 `统计口径说明` |  |  |
| U73 | Load more content | 继续加载完整内容 | App/ActivityViewer.swift / App/InputPopover.swift；App 键 `继续加载完整内容` |  |  |
| U74 | Zoom out timeline | 缩小时间线 | App/TimelinePanelView.swift；App 键 `缩小时间线` |  |  |
| U75 | Live chat status is unavailable. Readable activity records are shown below. | 聊天实时状态暂不可用；以下仅为可读取的活动记录。 | App/ChatDetailsView.swift；App 键 `聊天实时状态暂不可用；以下仅为可读取的活动记录。` |  |  |
| U76 | Actual menu bar size | 菜单栏原尺寸 | App/DashboardView.swift；--preview-status 验证模式；App 键 `菜单栏原尺寸` |  |  |
| U77 | Reading selected turn… | 读取所选轮次… | App/TimelinePanelView.swift；App 键 `读取所选轮次…` |  |  |
| U78 | Follow content | 跟随内容 | App/ActivityViewer.swift；App 键 `跟随内容` |  |  |
| U79 | Timeline identity mismatch | 轨迹身份不匹配 | App/TimelineStore.swift；资源存在，但此处只传 error.message，通常回退为通用读取失败提示；App 键 `轨迹身份不匹配` |  |  |
| U80 | Turn | 轮次 | App/TimelinePanelView.swift；App 键 `轮次` |  |  |
| U81 | Output | 输出 | App/ChatDetailsView.swift；App 键 `输出` |  |  |
| U82 | Output includes reasoning tokens. Statistics only include data confirmed to belong to the selected turn. | 输出包含推理用量；统计只显示可确认属于所选轮次的数据。 | App/ChatDetailsView.swift；App 键 `输出包含推理用量；统计只显示可确认属于所选轮次的数据。` |  |  |
| U83 | No timeline available for this turn | 这轮暂无可用轨迹 | App/TimelinePanelView.swift；App 键 `这轮暂无可用轨迹` |  |  |
| U84 | Some history records are unavailable | 部分历史记录暂不可用 | App/TimelinePanelView.swift；App 键 `部分历史记录暂不可用` |  |  |
| U85 | Some time boundaries were not fully observed. Unknown durations are not inferred from mark width. | 部分时间边界未被完整观测，未知耗时不会按短线宽度计算。 | App/ActivityViewer.swift；App 键 `部分时间边界未被完整观测，未知耗时不会按短线宽度计算。` |  |  |
| U86 | Some timing or content was not fully observed. Click an activity to view available records. | 部分耗时或内容未被完整观测，点击活动查看可用记录。 | App/TimelinePanelView.swift；App 键 `部分耗时或内容未被完整观测，点击活动查看可用记录。` |  |  |
| U87 | Partial observation | 部分观测 | App/ActivityViewer.swift / App/TimelinePanelView.swift / App/TimelineSurface.swift；App 键 `部分观测` |  |  |
| U88 | Read again | 重新读取 | App/ActivityViewer.swift / App/InputPopover.swift；App 键 `重新读取` |  |  |
| U89 | Retry | 重试 | App/TimelinePanelView.swift；App 键 `重试` |  |  |
| U90 | Isolated preview | 隔离验证 | App/DashboardView.swift；App 键 `隔离验证` |  |  |

### 13.2 Model 资源

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| V01 | %.0f ms | %.0f 毫秒 | Model/Timeline.swift；Model 键 `%.0f 毫秒` |  |  |
| V02 | %.1f s | %.1f 秒 | Model/Timeline.swift；Model 键 `%.1f 秒` |  |  |
| V03 | Subagent ·  | 子智能体 ·  | Model/Timeline.swift；旧名称前缀，当前 displayName 会去除此中文前缀；资源不等于正常路径仍显示它；Model 键 `子智能体 · ` |  |  |
| V04 | Tool | 工具 | Model/Timeline.swift；Model 键 `工具` |  |  |
| V05 | The source provides no viewable content. Activity status and timing are shown above. | 来源未提供可查看内容，活动状态与时间见上方。 | Model/ActivityContent.swift；Model 键 `来源未提供可查看内容，活动状态与时间见上方。` |  |  |
| V06 | Model | 模型 | Model/Timeline.swift；Model 键 `模型` |  |  |
| V07 | Unknown duration | 耗时未知 | Model/Timeline.swift；Model 键 `耗时未知` |  |  |

## 14. 附录：聊天行速度观测提示

以下为 Chat.speedObservationNotice 可能选用的全部提示，属于悬浮／辅助功能说明。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| O01 | The observation connection for this chat is disconnected. | 此聊天的采集连接已中断。 | disconnected；App 键 `NoticeSourceDisconnected` |  |  |
| O02 | Current generation lacks continuous observation. Its speed is unknown. | 当前生成内容缺少连续观测，相关速度暂不可确认。 | observation_gap；App 键 `NoticeObservationGap` |  |  |
| O03 | Duplicate or conflicting connections make the affected speed unknown. | 接入身份重复或冲突，相关速度暂不可确认。 | identity_conflict / multiple_active_backends；App 键 `NoticeIdentityConflict` |  |  |
| O04 | Speed measurement reached its resource limit. The affected speed is unknown. | 速度计算达到资源上限，相关速度暂不可确认。 | 计数／内容／计时／内存等资源上限；App 键 `NoticeSpeedResourceLimit` |  |  |
| O05 | Token counting failed. Speed is temporarily unavailable. | 速度计数失败，暂时无法估算速度。 | tokenizer_error；App 键 `NoticeSpeedCountFailed` |  |  |
| O06 | Some current generation rates are unavailable. | 部分当前生成速度暂不可用。 | 其他当前速度不可用情况；App 键 `NoticeStreamUnavailable` |  |  |

## 15. 附录：共享活动词库中的其余模板

用于通用消息、已结束活动命名、兼容或其他入口，不代表列表会直接逐条显示。例如断开时 GUI 列表优先显示 Status unavailable，普通终态直接使用 Completed / Failed / Stopped。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| B01 | {name} | {name} | 原始对象展示名，不翻译 name；也用于时间线工具来源标签；`activity.named` |  |  |
| B02 | Command | 命令 | 通用命令来源名；`activity.command` |  |  |
| B03 | Tool | 工具 | 通用工具来源名／中性回退；`activity.tool` |  |  |
| B04 | Disconnected | 连接中断 | 后端连接中断消息；GUI 列表通常被不可用状态覆盖；`activity.disconnected` |  |  |
| B05 | Task completed | 任务完成 | 后端任务完成消息；GUI 普通终态直接使用 Completed；`activity.completed` |  |  |
| B06 | Turn completed | 本轮完成 | 有未完成目标时的后端回合完成消息；`activity.turn_completed` |  |  |
| B07 | Stopped | 已停止 | 后端中断状态消息；`activity.stopped` |  |  |
| B08 | Task failed | 任务失败 | 后端失败消息；工作态有错误时也可能显示；`activity.failed` |  |  |
| B09 | Idle | 空闲 | 后端空闲消息；普通 GUI 无活动分支不直接选择 Idle；`activity.idle` |  |  |

## 16. 附录：共享状态词库

这组 status.* 与第 9 节 TimelinePhase.statusLabel 不是同一入口，主要用于终端／共享展示等路径，不能据此推断时间块文案。全产品术语确定后可一起检查。

| 编号 | 当前英文 | 当前中文 | 含义 / 定位键 | 修改后英文 | 修改后中文 |
| --- | --- | --- | --- | --- | --- |
| Q01 | Active | 进行中 | 共享状态资源；`status.active` |  |  |
| Q02 | Completed | 完成 | 共享状态资源；`status.completed` |  |  |
| Q03 | Failed | 失败 | 共享状态资源；`status.failed` |  |  |
| Q04 | Not executed | 未执行 | 共享状态资源；`status.declined` |  |  |
| Q05 | Approved | 审核通过 | 共享状态资源；`status.approved` |  |  |
| Q06 | Denied | 审核未通过 | 共享状态资源；`status.denied` |  |  |
| Q07 | Review timed out | 审核超时 | 共享状态资源；`status.timedOut` |  |  |
| Q08 | Stopped | 已停止 | 共享状态资源；`status.stopped` |  |  |
| Q09 | Status unavailable | 状态暂不可用 | 共享状态资源；`status.unknown` |  |  |
| Q10 | Ended | 已结束 | 共享状态资源；`status.ended` |  |  |
| Q11 | Command failed | 命令失败 | 共享状态资源；`status.command_failed` |  |  |

## 17. 需要单独决定的语义问题

| 编号 | 当前情况 | 用户决定 / 修改要求 |
| --- | --- | --- |
| P01 | Thinking / Reasoning：同一中文键在 App、Model 有两种英文。 | 已确认统一为 Reasoning |
| P02 | Reply / Response / Response output 与“回复／回答／正文”的表达。 | 已确认列表 Responding／回复中，图例和详情类型 Response／回复；内容页签未另行变更 |
| P03 | Preparing 同时表达参数生成和工具服务启动等待，是否拆开。 |  |
| P04 | Getting ready / Processing 是否明确尚未开始、阶段转换、工具就绪待执行等含义。 |  |
| P05 | Editing 同时表达编辑内容生成和应用改动，是否按阶段分别命名。 |  |
| P06 | 计划归入 text / Reply：只改通用类型名，还是新增 Plan 类型；后者涉及分类实现。 |  |
| P07 | tool_execution 有独立颜色但没有固定图例，是否增加。 |  |
| P08 | Partial observation 同时作为未知生命周期兜底与观测质量提示，是否拆开。 |  |
| P09 | 修改前 Paused 覆盖审批、用户输入和目标受限；Waiting 又有 sleep、子代理、执行等待等情境。 | 已实施审批／用户输入统一为 Pending，其他暂停／受限分支保留原样 |
| P10 | 子任务／子代理／子智能体与 subtask / subagent / agent：分别确定任务与执行者称呼。 |  |
| P11 | Visible reasoning summary 是否保留“可见摘要”限定；改名后仍需如实表达实际保留的内容。 |  |
| P12 | Live content 会随历史补齐换成内容类型标题：是否固定页签名称并独立标注来源。 |  |
| P13 | %d tools／%d 项工具按选中的 entries 数量显示，是否改为通用活动计数。 |  |
| P14 | 部分内容“完成后将从历史补齐”是否改成有条件的说明；来源状态栏是否区分实时流。 |  |

## 18. 源码入口与后续实施定位

| 清单范围 | 源码入口 |
| --- | --- |
| A / F / C / B / Q 英文 | [src/codex_progress_monitor/messages_en.py](../../src/codex_activity_monitor/messages_en.py) |
| A / F / C / B / Q 中文 | [src/codex_progress_monitor/messages_zh_cn.py](../../src/codex_activity_monitor/messages_zh_cn.py) |
| 列表活动含义与优先级 | [src/codex_progress_monitor/progress_state.py](../../src/codex_activity_monitor/progress_state.py) |
| 阶段键与 target 拼接 | [src/codex_progress_monitor/i18n.py](../../src/codex_activity_monitor/i18n.py) |
| L 与速度提示 | [macos/Sources/MonitorApp/ChatRowView.swift](../../macos/Sources/MonitorApp/ChatRowView.swift) |
| G 固定图例 | [macos/Sources/MonitorApp/TimelineLegend.swift](../../macos/Sources/MonitorApp/TimelineLegend.swift) |
| K / J / S / R 类型、状态、协作 | [macos/Sources/MonitorModel/Timeline.swift](../../macos/Sources/MonitorModel/Timeline.swift) |
| 时间线分类 | [src/codex_progress_monitor/timeline.py](../../src/codex_activity_monitor/timeline.py) |
| 时间线控件与图例旁说明 | [macos/Sources/MonitorApp/TimelinePanelView.swift](../../macos/Sources/MonitorApp/TimelinePanelView.swift) |
| 时间块悬浮／辅助功能提示 | [macos/Sources/MonitorApp/TimelineSurface.swift](../../macos/Sources/MonitorApp/TimelineSurface.swift) |
| D 内容标签 | [src/codex_progress_monitor/messages_detail.py](../../src/codex_activity_monitor/messages_detail.py) |
| 历史内容类型与标签选择 | [src/codex_progress_monitor/history_content.py](../../src/codex_activity_monitor/history_content.py) |
| 实时内容标签选择 | [src/codex_progress_monitor/timeline_query.py](../../src/codex_activity_monitor/timeline_query.py) |
| N 与详情控件 | [macos/Sources/MonitorApp/ActivityViewer.swift](../../macos/Sources/MonitorApp/ActivityViewer.swift) |
| 输入浮层 | [macos/Sources/MonitorApp/InputPopover.swift](../../macos/Sources/MonitorApp/InputPopover.swift) |
| 统计与旁注 | [macos/Sources/MonitorApp/ChatDetailsView.swift](../../macos/Sources/MonitorApp/ChatDetailsView.swift) |
| 列表展开、计数、空状态 | [macos/Sources/MonitorApp/DashboardView.swift](../../macos/Sources/MonitorApp/DashboardView.swift) |
| 查询异常 | [macos/Sources/MonitorApp/TimelineClient.swift](../../macos/Sources/MonitorApp/TimelineClient.swift) |
| 读取异常回退 | [macos/Sources/MonitorApp/TimelineStore.swift](../../macos/Sources/MonitorApp/TimelineStore.swift) |
| O 速度观测提示选择 | [macos/Sources/MonitorModel/Snapshot.swift](../../macos/Sources/MonitorModel/Snapshot.swift) |
| App 英文 | [macos/Sources/MonitorApp/Resources/en.lproj/Localizable.strings](../../macos/Sources/MonitorApp/Resources/en.lproj/Localizable.strings) |
| App 中文 | [macos/Sources/MonitorApp/Resources/zh-Hans.lproj/Localizable.strings](../../macos/Sources/MonitorApp/Resources/zh-Hans.lproj/Localizable.strings) |
| Model 英文 | [macos/Sources/MonitorModel/Resources/en.lproj/Localizable.strings](../../macos/Sources/MonitorModel/Resources/en.lproj/Localizable.strings) |
| Model 中文 | [macos/Sources/MonitorModel/Resources/zh-Hans.lproj/Localizable.strings](../../macos/Sources/MonitorModel/Resources/zh-Hans.lproj/Localizable.strings) |
| 共享词库导出 | [bin/export-localizations](../../bin/export-localizations) |

### 编辑后如何落实

先依据“修改后”列与 P01–P14 确定词汇、表达形式、分类与保留的语义区别，再修改拥有该文案的资源及分支。共享 Python 资源变化后，按现有 bin/export-localizations 流程更新 Swift CoreMessages 与本地化示例，不手工维护导出副本。App 与 Model 分别持有的同义资源需要同时核对。分类拆分、图例增减与来源展示变化另行记录，不能只用文字替换代替语义决定。

本文是本次编辑工作表，不自动从源码刷新；后续保留用户填写内容。已实施的文案与验证边界见[实施结果](implementation-results.md)。

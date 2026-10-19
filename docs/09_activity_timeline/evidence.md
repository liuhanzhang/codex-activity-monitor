# 活动时间线：来源证据与工程验证门槛

日期：2026-10-17。本文保留设计文档阶段的检查快照；后续真实运行与实施验收分别见[来源验证](source-validation.md)、[实施结果](implementation-results.md)。下文的“本次”指初始文档整理，不代表后续工作仍未执行。

本章区分已观察事实、源码定义和待验证实现；不含真实聊天正文、参数、命令、补丁或工具结果。

## 1. 检查基线

| 来源 | 本次基线 | 证据范围 |
| --- | --- | --- |
| Monitor 仓库 | 文档整理前 `20cc995`，工作树干净 | 当前实现与已存在测试源码；本轮未运行产品测试 |
| 本地 Codex 源码 | `/Users/bytedance/playground/codex`，`806d9732c974bc8a51b8317c1bd8985544fe627c`，检查时工作树干净 | 指定源码快照的协议、持久化策略及 writer 行为 |
| 两份本机历史样本 | `cli_version=0.163.0-alpha.10.2`，`history_mode=paginated` | 这两份文件的实际字段与结构；不代表所有历史格式 |

源码快照与实际运行二进制没有在本轮做 hash／构建对应验证。源码支持的字段和 API 不等于本机当前链路已成功读取它们。

## 2. 本机 JSONL 样本

本次只读检查了以下 Codex 自有文件，统计不把全文复制到仓库：

```text
/Users/bytedance/.codex/sessions/2026/10/17/
  rollout-2026-10-17T16-24-16-01a148f6-3133-78e3-9074-9dc9a4c31121.jsonl

/Users/bytedance/.codex/sessions/2026/10/16/
  rollout-2026-10-16T18-41-30-01a1444d-786e-75a0-8c91-c7e441c1dbf9.jsonl
```

下表是本次整理中的一次采样快照。第一份文件仍在增长，数量不是固定验收值。

| 指标 | 2026-10-17 样本 | 2026-10-16 样本 |
| --- | --- | --- |
| canonical `event_msg.item_completed` 数量 | 236 | 889 |
| 其中带有效非零开始／结束及 turn 字段 | 236 | 889 |
| Reasoning | 100 | 347 |
| AgentMessage | 34 | 97 |
| CommandExecution | 60 | 221 |
| McpToolCall | 17 | 102 |
| FileChange | 0 | 34 |
| raw function／custom call 数量 | 46 | 309 |
| 上述 raw call 自身带开始／结束时间字段 | 0／0 | 0／0 |
| raw item ID 与 canonical item ID 相等的数量 | 0 | 0 |
| raw call ID 与 canonical item ID 相等的数量 | 3 | 88 |

这说明新格式能提供大量已完成活动的生命周期区间及保留内容，但 raw 模型参数最终内容本身不带完整生成区间。本次同时核对了 ID：raw item ID 与 canonical item ID 并不普遍一致，只有部分 call ID 存在相等候选。字段相等仍需结合工具与协议语义确认，不能把两个命名空间中的同名字段默认视为同一身份。两份样本的 canonical 起止字段检查未发现非正数或结束早于开始的区间。

### 外层 timestamp 与活动边界

| 文件／记录位置 | 已观察结构 | 含义 |
| --- | --- | --- |
| 10-17 样本第 27 行，Reasoning | `started_at_ms=1792225467778`、`completed_at_ms=1792225469778`；外层 `2026-10-17T08:24:29.778Z` | Codex 记录的区间为 2 秒；外层是单个记录时间点 |
| 10-16 样本第 172 行，FileChange | `started_at_ms=1792150473959`、`completed_at_ms=1792150474058`；外层 `2026-10-16T11:34:34.059Z` | 活动区间 99ms，外层写入点比结束晚约 1ms |
| raw function／custom call | 有最终输入／参数、call 身份及外层 timestamp，缺少该生成阶段的 start／end | 可以读内容，不能仅凭此恢复前面的参数生成耗时 |

行号仅用于这次样本定位，不是产品持久引用格式。外层写入时间恰好等于某次完成时间，也不能推广为活动起点。用 tool call 记录到 tool result 的间隔计算模型参数生成时间，会混入后续执行／等待，语义错误。

## 3. 源码支持的结论

下表 Codex 路径相对本地源码根，Monitor 路径链接到本仓库。

| 结论 | 复查位置 | 限制 |
| --- | --- | --- |
| JSONL 外层 timestamp 在写记录时生成 | `codex-rs/rollout/src/recorder.rs`，`JsonlWriter::write_rollout_item` | 不是 item 开始事件的通用时间 |
| paginated 持久化 canonical completed item，legacy 策略不同 | `codex-rs/rollout/src/policy.rs`，`persisted_event_msg / persisted_item_completed_event` | 不能按新样本承诺旧格式完整时间恢复 |
| 许多 start／delta／error／approval 等临时事件不持久化 | 同上 transient 分支 | JSONL 不能替代全部实时观察 |
| command 和 MCP 持久化结果存在 64 KiB 量级上限 | 同上 `PERSISTED_COMMAND_OUTPUT_MAX_BYTES / PERSISTED_MCP_RESULT_MAX_BYTES` | 查看器不能找回来源已丢弃部分 |
| 历史 item 可含可空开始／结束时间；旧记录可能缺失 | `codex-rs/protocol/src/protocol.rs`，`ItemCompletedEvent`；`codex-rs/app-server-protocol/src/protocol/v2/thread.rs`，`ThreadItemEntry` | 缺失或默认 0 不能当有效 epoch 边界 |
| app-server 通知定义有 `startedAtMs / completedAtMs` | `codex-rs/app-server-protocol/src/protocol/v2/item.rs`，`ItemStartedNotification / ItemCompletedNotification` | 需要核对本机真实投递、版本与字段是否被完整保留 |
| 分页历史 API 在该源码中存在 | `codex-rs/app-server-protocol/src/protocol/v2/thread.rs`，`ThreadTurnsListParams / ThreadItemsListParams` | 本轮没有执行真实 API 请求，也未证明安全连接方式 |
| steering 目标是活动轮，并返回接受的原 turn ID | `codex-rs/app-server/src/request_processors/turn_processor.rs`，`turn_steer_inner` | 输入消息的实时／历史去重仍需验证 |
| 工具可并行；yield 是等待控制 | `codex-rs/core/src/tools/parallel.rs`、`codex-rs/core/src/tools/handlers/shell_spec.rs`、`codex-rs/core/src/unified_exec/process_manager.rs` | 不构成统一前台／后台分类 |
| 当前参数活动会改为 tool_ready 并重置开始时间 | [engine.py](../../src/codex_progress_monitor/engine.py)，`_upstream` | 应在转换前建立独立生成阶段，不能从最终快照反推 |
| 当前线程终态活动只留最近 20 条 | [engine.py](../../src/codex_progress_monitor/engine.py)，`tick` | 不能直接用于整轮历史时间线 |
| 原始事件过滤保留双时钟，尚未保留 turn/steer 请求 | [events.py](../../src/codex_progress_monitor/events.py)，`Event.from_row / filter_event` | 新功能需最小扩展，不改变旧速度时钟 |
| 文件编辑身份／静态识别已有保守边界 | [tool_labels.py](../../src/codex_progress_monitor/tool_labels.py)与[test_file_editing.py](../../tests/test_file_editing.py) | 不能推广到任意动态脚本的写文件意图 |
| 原生应用是 accessory，popover 与现有 preview 窗口已有承载 | [Main.swift](../../macos/Sources/MonitorApp/Main.swift)、[PopoverController.swift](../../macos/Sources/MonitorApp/PopoverController.swift) | 不代表新活动查看器的焦点／关闭行为已验证 |

协议语义也可复查 OpenAI 的[活动轮 steering](https://learn.chatgpt.com/docs/app-server#steer-an-active-turn)、[item 生命周期](https://learn.chatgpt.com/docs/app-server#items)与[函数参数流式生成](https://developers.openai.com/api/docs/guides/function-calling#streaming)。网页会变化，工程兼容性以明确版本的源码、通知和实测共同确定。

## 4. 必须完成的工程验证

| 编号 | 问题 | 当前状态与推荐处理 |
| --- | --- | --- |
| E01 | 真实通知是否含 producer 时间、字段如何保留？ | 源码有定义，当前过滤也允许 camelCase 时间字段；需实时样本验证，不改既有速度时钟 |
| E02 | 当前拓扑怎样安全读取历史？ | JSONL 已验证存在；API 的隔离只读可达性未验证。首版以 JSONL 作为确定落地基础 |
| E03 | raw→canonical、参数→执行能怎样明确关联？ | 仅部分 ID 可直接匹配；需要按工具／来源给出实际映射。缺证据保留未关联，不按时间／正文猜配 |
| E04 | 持久进程、child 复用与跨轮如何保留真实范围？ | 当前代码／测试已有部分边界；新投影需真实样本与夹具验证，不把父轮结束当进程／child 完成 |
| E05 | 活跃详情可取得多少完整内容？ | 现有过滤并未保留通用详情，历史可能尚未落盘；验证已知流缓冲／按需订阅，缺前缀标部分，完成后再读源历史 |
| E06 | legacy、归档、回滚、临时聊天和未知格式如何降级？ | 当前直接检查的是两份 paginated；以能力判断、来源校验及合成故障夹具补齐，不承诺未验证范围 |
| E07 | steering／其他中途输入怎样不重复标记？ | steering 协议语义明确，Monitor 请求过滤缺口明确；实际消息生命周期与请求关联仍需采样确认 |
| E08 | 大文件、长轮次、侧存储保留与只读查询性能？ | 设计采用懒加载、索引、分块与视口绘制；需要定量记录解析／查询延迟、内存和 bridge 是否受影响 |
| E09 | accessory App 的普通窗口焦点及生命周期？ | 有既有 AppKit 承载基础；需实际验证 popover 收起、窗口前置／复用、关闭后采集继续等路径 |

E01–E07 是 P0 的数据门槛；E08 随存储和查询阶段验证，E09 随原生窗口阶段验证。能通过保守降级保持核心产品模型时，由工程处理并回写文档，不需要重新询问已确认的轨道与窗口形式。

## 5. 本次整理完成的检查

本次重新检查了仓库状态、相关实现和 Codex 源码版本，并对上述两份 JSONL 仅作只读结构／时间字段统计。没有对真实聊天发送消息或 steering，没有调用历史 API、修改采集、重启 Codex、更新日常 GUI、运行产品测试、提交或推送。

文档链接、编号、代码引用与差异检查在整理完成时进行；它们只验证文档交付，不算本功能的实现或验收。后续运行证据应追加到独立结果文档，不改写本次样本所能支持的范围。

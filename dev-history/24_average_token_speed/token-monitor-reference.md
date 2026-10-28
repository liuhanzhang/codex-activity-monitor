# Token Monitor 平均速度和当前速度实现

核对日期：2026-10-28。项目为 [Javis603/token-monitor](https://github.com/Javis603/token-monitor)。参照代码固定为 App 提交 `fd494149c70bd94bdd489f8850af5878976165cf`，其 vendor 清单固定 Tokscale fork 提交 `f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540`，避免把 fork 最新分支误认为 App 实际使用的解析器。[vendor 清单](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/scripts/vendor/tokscale.json)

## 平均速度使用成对的累计量

`sessionRows.js` 的 `sessionTokenRate()` 读取会话自己的 `timedOutputTokens` 和 `timedDurationMs`，按下面的比例展示速度；期间汇总的 `tokenRatePerSecond()` 使用相同口径。[会话函数](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/electron/renderer/sessionRows.js#L328-L343)、[期间函数](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/electron/renderer/tokenRatePresentation.js#L50-L55)

```text
平均输出速度 = timedOutputTokens × 1000 / timedDurationMs
```

分母是各有效请求样本的耗时之和，不是整个会话首尾的墙钟跨度，也不是请求速度的算术平均。并发会话的样本时长分别相加，不能将它当成设备整体每秒产出的吞吐量。

Tokscale 在正耗时、正总 token 的样本中保留精确的输出和推理子计数；没有有效耗时的消息保留用量，但不贡献 performance 样本。App 的 `entryThroughput()` 优先使用这些精确子计数，并对 Codex 这样的独立 reasoning 桶做合并；显式零不能被全部输出替代。旧解析器没有精确子计数字段时，仍有兼容的整行回退，这不应和当前精确路径混为一谈。[样本汇总](https://github.com/Javis603/tokscale/blob/f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540/crates/tokscale-core/src/lib.rs#L363-L390)、[App 汇总](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/shared/usage.js#L1171-L1184)

Codex 原始 `output_tokens` 包含 reasoning。Tokscale 先将它拆成可相加的 output 和 reasoning 两桶，Token Monitor 按客户端语义加回。迁移算法时应沿用原始 inclusive output，不能又额外加一次 reasoning。[Codex 桶转换](https://github.com/Javis603/tokscale/blob/f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540/crates/tokscale-core/src/sessions/codex.rs#L275-L300)

## Codex 请求耗时的推导

计时实现位于 [CodexRequestTiming](https://github.com/Javis603/tokscale/blob/f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540/crates/tokscale-core/src/sessions/codex_timing.rs#L13-L139)。它跟踪请求边界、已固定起点、最后模型记录、工具结果、最近 token_count 以及 usage record。

1. **起点边界。** turn_context、轮次开始、真实用户输入可建立或重置边界；工具结果和非 assistant 消息可以推进边界。首次观察到 reasoning、assistant message 或工具调用后，固定本次请求起点。随后异步工具结果不再将已固定的起点向后移动。对部分旧版本在 token_count 后 100 毫秒内补写的输出记录，有避免误认作下一次请求的处理。
2. **优先终点。** `token_usage_record` 的外层 timestamp 可以作为终点，但要与对应 token_count 的 `last_token_usage` 各 token 桶匹配，且不早于最后模型记录、不晚于 token_count。计数或时间不匹配就没有有效计时样本。
3. **旧格式回退。** 没有 usage record 时，通常用 token_count；如果工具结果与 token_count 在 100 毫秒内一起落盘，则尝试用最后模型输出记录估算终点。这个 100 毫秒是回退的同批落盘启发式，不是最短生成耗时或速度过滤阈值。
4. **正耗时门槛。** 只有已知、为正的时间窗才进入 performance。并行工具和模型输出可重叠，不将工具持续时间求和后从总时长机械扣除。

解析器主要仍以 token_count 作为用量确认来源，usage record 提供更接近请求结束的时间证据。`performance_timing` 与会话活动跨度分开保存，因此修正速度不会将工具等待从会话活动记录里删除。[解析器结算点](https://github.com/Javis603/tokscale/blob/f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540/crates/tokscale-core/src/sessions/codex.rs#L1746-L1768)

这些时间来自客户端日志，可能包含首输出等待、排队、网络等。实现没有将 turn 级 TTFT 从每次请求中扣除，也没有每个请求的服务端纯生成起止字段。[请求计时说明](https://github.com/Javis603/tokscale/blob/f52a392c0e1eed3a6c7ba013e6b6e0e2251b9540/docs/request-performance.md)

## 当前速度使用相邻快照的增量

`createLiveTokenRateTracker()` 的第一份有效快照只建立基线。后续快照计算 matched counters 的变化：

```text
本批输出增量 = 本次 timedOutputTokens − 上次 timedOutputTokens
本批耗时增量 = 本次 timedDurationMs − 上次 timedDurationMs
本批速度 = 本批输出增量 × 1000 / 本批耗时增量
```

因此它的“当前速度”是新结算批次的请求速度，不是最近一秒流式 token 的接收速率。一次扫描可能结算多个请求，分母仍是这些请求的计时时长，而非两次扫描之间隔了多少秒。[增量计算](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/electron/renderer/tokenRatePresentation.js#L139-L165)

有效模型归属存在时，它保存每个模型最近一次有效批次，默认活跃期为 8 秒；合并时先累加活跃样本的 token 与耗时，再相除。它不是将最近 8 秒内所有原始 token 放进一个滑动窗口。归属无法一致分解时回退到设备级增量；相同快照保持已有样本，计数回退则清空样本、重建基线。[模型样本合并](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/electron/renderer/tokenRatePresentation.js#L85-L225)

组级显示默认在活跃样本过期后保留最后一个读数并标记 idle，最长保留 180 秒。这是展示保留，不是新的采样，也不代表期间仍有同样速度。[组级保留](https://github.com/Javis603/token-monitor/blob/fd494149c70bd94bdd489f8850af5878976165cf/src/electron/renderer/tokenRatePresentation.js#L242-L350)

另一个 `tokenBurnPerMinute()` 使用 `timedTokens × 60000 / timedDurationMs`。它涉及总 token 消耗，不应将 TPM 文案理解成输出 token/s 简单乘 60。

## 与本项目的对应关系

| 指标 | token 来源 | 时间范围 | 更新方式 |
| --- | --- | --- | --- |
| Monitor 现有实时速度 | 本地分词的已接收生成内容，不含隐藏推理 | 固定一秒接收窗口 | 流式观测及界面发布 |
| 本章计划的平均速度 | JSONL 官方输出，包含推理 | 所选轮次内各有效请求窗口之和 | 新 usage 时更新，之后保留 |
| Token Monitor 平均速度 | 有有效 performance 的输出 | 会话或期间累计请求耗时 | 扫描汇总后更新 |
| Token Monitor 当前速度 | 相邻快照 matched counters 的增量 | 最新有效批次的请求耗时 | 新快照增量及活跃样本保留 |

本项目借鉴请求计时边界与 token/耗时成对统计，不把 Token Monitor 的 8 秒活跃期、180 秒展示保留或其批次当前速度直接替换现有实时速度。

本项目还要求 usage 到来时就更新。实验在原状态机副本上提前预览，之后再核对 token_count；285 次吻合只覆盖本批记录。产品需要明确处理预览与确认发生冲突的撤销、遗漏记录和恢复路径，不能把实验的 detached preview 直接当成已经实现的生产账本。

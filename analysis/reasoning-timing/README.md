# 推理事件计时核对：接收窗口不保证覆盖实际推理

日期：2026-10-15，Asia/Shanghai。结论来自官方公开 schema 和 5 次隔离 Codex 模型回合；没有修改产品计时逻辑或重启日常采集器。

## 当前处理决定

暂时继续观察，保持产品计时与展示逻辑现状，不增加截断、过滤或替代估算。保留本报告、精简证据和手动复测脚本，供以后对照 OpenAI / Codex 的事件时序变化，或重新评估更可靠的计时方法。当前不追加模型实验或自动监测。

这些材料记录的是已确认的计量限制，不代表已确定某个 OpenAI 缺陷或其修复计划。未来复测应使用新的运行目录和当时的版本 / 字段定义，保留本次结果作为对照。完整本地运行数据继续位于忽略目录，不纳入 Git。

## 结论

`reasoning_tokens / (reasoning done 接收时间 - added 接收时间)` 可以精确计算这两个事件之间的接收速率，但不能保证分母覆盖模型生成这些推理 token 的过程。本次绕过产品 bridge / collector 后仍复现约 **114,078 token/s**。Codex 自身的接收日志也只有毫秒间隔，因此仅改用日志时间、提高 Python 时钟精度或调节看板刷新频率都不能解决该样本。

公开 Responses API 提供整个响应的 `created_at`、可选的 `completed_at` 和 `usage.output_tokens_details.reasoning_tokens`。当前公开 reasoning item schema 和 `output_item.added/done` 事件没有提供单独的推理开始时间、结束时间或耗时。整个响应的时间差不能直接充当纯推理耗时。

这次复现确认了现有分母的语义限制；没有确认原先日常界面那一帧 `182808.3` 的完整原始事件，也没有把延迟定位到 OpenAI 服务、网络中间层或 Codex WebSocket 接收任务中的具体一层。

## 实验范围与方法

- 使用安装版 CLI `0.162.0-alpha.17.2`，由探针独立启动 stdio app-server，退出码均为 0。
- 复用已独立登录的实验 `CODEX_HOME`，每个进程使用单独 SQLite 目录；实验配置前后 SHA-256 一致。没有复制登录凭据，没有向日常后端注入请求。
- 每个案例创建新的 ephemeral thread，effort 均为 `medium`。比较 `gpt-6.1-sol` 的 `summary=auto/none`；另有一个 `gpt-6-luna` 对照。
- 组合题为长度 30、恰好十个 1 且不含 111 的二进制串计数；短题为 `19*23*29*31`。都只要求最终整数；实际没有工具调用。实验不评估答案正确率。
- 探针在 `os.read()` 立即记录单调时钟和批次 ID，同时保留 CLI tracing 时间戳、事件类型 / 顺序 / 生成身份、时间字段和数值 usage。
- 原始模型正文、推理摘要、加密 reasoning、请求参数正文、账号内容和凭据在写证据前被丢弃。捕获在忽略目录 `.local/reasoning-timing/`；[公开精简证据](verification.json)只包含计数、身份、时间和哈希。
- 初次运行后为探针增加了性能字段收集，因此首例没有完整 `timing_metrics`。每次运行的脚本哈希单独记录，最终分析脚本哈希另行记录，不冒充所有运行都使用最终脚本。

## 观察结果

所有数值均为单个样本。`added → done` 为 Python 原始读取间隔，日志间隔为 CLI tracing 时间差；两者都不是服务端实际推理时钟。

| 案例 | 推理 token | added → done | CLI 日志间隔 | 按现有公式的速率 | 整个响应接收窗口 |
| --- | ---: | ---: | ---: | ---: | ---: |
| sol / auto / 组合题 | 237 | 6.333578 s | 6.333559 s | 37.42 token/s | 7.183064 s |
| sol / auto / 短题 | 26 | 1.694573 s | 1.694480 s | 15.34 token/s | 3.010644 s |
| sol / none / 短题 | 25 | 0.149392 s | 0.149417 s | 167.34 token/s | 1.651293 s |
| sol / none / 组合题 | 316 | **0.002770 s** | **0.002777 s** | **114,077.69 token/s** | **8.786628 s** |
| luna / auto / 组合题 | 0 | 无 reasoning item | 无 | 无 | 0.568513 s |

异常案例的相对接收时间线：

```text
0.000000 s  response.created
8.532034 s  reasoning output_item.added
8.534804 s  reasoning output_item.done
8.786628 s  response.completed，Output=325，Reasoning=316
```

同一 reasoning item 的 CLI 日志为 `09:36:07.336130Z` / `09:36:07.338907Z`，两个事件在不同 Python 读取批次中到达，独立 app-server 的 `item/started → item/completed` 也只有 `0.002832 s`。因此换成 protocol 边界同样无法恢复缺失的窗口。

该响应的公开响应级字段为 `created_at=1792056958`、`completed_at=1792056967`，本次是整数秒；相减是 9 秒。它们说明整个响应的生命周期，没有给出 316 个推理 token 所用的具体时段。字段类型允许数值，不据此保证所有版本或通道都只有整数秒精度。

异常样本在 `summary=none` 下发生；这些案例不足以证明该参数是唯一触发条件，也不足以保证 `summary=auto` 的样本就是准确的模型推理速度。看起来合理的速率也可能漏掉前面的生成或等待时间。

## OpenAI 官方字段核对

2026-10-15 查询、打开并核对的官方资料：

1. [Response 返回对象](https://developers.openai.com/api/reference/python/resources/responses/methods/create)：`created_at` 为响应创建时刻，`completed_at` 为完成时刻、只在 completed 状态存在；是整个 response 的生命周期。
2. [ResponseReasoningItem 完整 schema](https://developers.openai.com/api/reference/python/__sdk_schema?declaration=%28resource%29+responses+%3E+%28model%29+response_reasoning_item+%3E+%28schema%29&selected=%28resource%29+responses)：包含 id、summary、type、content、encrypted_content、status，没有单段推理的时间字段。
3. [Responses 流事件](https://developers.openai.com/api/reference/resources/responses/streaming-events#response.output_item.added)：added / done 表示输出 item 被加入 / 标记完成；事件包含 item、output_index、sequence_number、type，没有事件产生时间。官方没有在此承诺 added 等于服务端开始执行推理。
4. [Reasoning 用量说明](https://developers.openai.com/api/docs/guides/reasoning#controlling-costs)：`usage.output_tokens_details.reasoning_tokens` 是数量；摘要是另一种可见输出，不提供真实推理耗时。

本次实时实验使用 Codex 隔离后端的现有登录通道，没有另做一组直接调用公共 REST endpoint 的实时请求。公开 API 字段结论来自官方 schema；Codex 运行结论来自本机事件。两者不互相替代。

## Codex 额外遥测

运行中收到未在上述公开 Responses 事件清单中找到的 `responsesapi.websocket_timing`。异常案例携带：

```json
{
  "total_turn_time_s": 8.81399831,
  "pre_inference_ms": 235.5298,
  "num_engine_calls": 1,
  "num_sampled_tokens_total": 325,
  "client_tool_pause_total_ms": 0.0
}
```

本地 Codex 源码也认识这个事件，但其 telemetry 提取的 `engine_service_total_ms` 等字段没有在本次真实捕获出现。不能把源码中支持的字段当作当前服务器已返回的值。当前实际返回的数据没有拆分 reasoning 与可见回答的耗时；`total_turn_time_s` 的精确服务端计量边界也没有查到公开契约。

源码参考：`codex-rs/codex-api/src/endpoint/responses_websocket.rs` 的 timing 事件处理，`codex-rs/otel/src/events/session_telemetry.rs` 的性能字段提取。仓库源码只是实现解释，未把其版本默认视为与安装二进制一致；真实样本与 CLI 二进制哈希是独立证据。

## 可以可靠提供的指标

| 想测量什么 | 可用方法 | 能否称为准确的纯推理耗时 |
| --- | --- | --- |
| 单次客户端请求到完成的等待时间 | 单调时钟测请求发出到对应 completed 到达 | 否，包含网络、等待、输入处理和输出生成 |
| 整个服务端响应生命周期 | `completed_at - created_at`；注意实际时间精度 | 否，是响应级范围 |
| reasoning item 的客户端可见窗口 | added / done 接收间隔 | 否，本实验已证明可严重缩短 |
| 每段模型实际执行推理的时间 | 需要服务端按段暴露生成开始 / 结束或 duration，并明确计量边界 | 当前公开字段不可直接获得 |

若产品改用请求整体耗时，应命名为“响应耗时”；相应 `output_tokens / 整体耗时` 应命名为“响应吞吐”，其中 Output 包括 Reasoning，且分母的口径要明确。一次 Codex turn 可能包含多次模型响应和工具执行，不能直接把 turn 总时长混入单次响应速率。

若仍保留现有 item 窗口数值，只能明确称为事件接收窗口上的估算。按速度或最短耗时过滤可以避免极端值进入界面，但不能证明留下的样本具有真实推理耗时。仅更新时钟来源或把极端值截到某个上限，不构成准确性修复。

## 复现和检查

探针：[reasoning_timing_probe.py](../../experiments/product-preflight/reasoning_timing_probe.py)。示例：

```sh
.local/venv/bin/python experiments/product-preflight/reasoning_timing_probe.py \
  --run-dir .local/reasoning-timing/new-run \
  --case sol-auto:gpt-6.1-sol:auto \
  --case sol-none:gpt-6.1-sol:none
```

运行目录必须不存在；会产生真实模型用量。该脚本不会操作日常 runtime。

已检查：5 回合正常完成、3 个自有进程正常退出、配置哈希一致、无工具调用、捕获哈希与原运行结果一致、证据未保留正文 / 加密内容、源代码可编译、两时钟在异常段相符。只核对本次探针与结果，没有重跑产品历史测试。实验不覆盖所有模型、reasoning 模式、服务通道或自然使用场景。

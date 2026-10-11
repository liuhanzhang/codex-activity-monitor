# 第一版实现前验证结果

验证日期：2026-10-11，Asia/Shanghai。

## 结论与实现选择

采用 **Desktop 启动原版 CLI，透明转发完整 stdio 协议，同时旁路观察上游接收事件** 的路线。实现原型已验证并发身份映射、文本计数与基本故障隔离，可以开始产品采集与终端看板的实现。

按用户要求复用初始实验结论，仅对 Desktop 做一次独立 profile 的启动 / 初始化回归，没有重新提取安装包或重跑版本架构核查。真实模型回合由实验自有 stdio 后端驱动；日常 Desktop 中由用户提交任务到看板的完整试用尚未执行。

| 验证项 | 结果 | 证据范围 |
| --- | --- | --- |
| E1 接入 | 独立 Desktop 通过包装器完成正常 initialize / initialized，随后关闭，原有 Desktop 持续运行 | 一次启动回归；另用同一原版 CLI 的自有 stdio 后端验证真实回合 |
| E2 并发关联 | 长输出实验有 445 次跨聊天增量切换，6 个内容流实时关联成功，无未关联增量 | 两条真实并行聊天；短工具回归另有 43 次切换 |
| E2 内容一致性 | 两次运行共 12 个流均与上游完成项和各自持久化项目逐字一致 | 8 个正文流、4 个工具参数流；报告只保存计数、ID 和哈希 |
| E3 Tokenizer | 9 个样本、每个 5 种分块方式的最终重算计数完全一致 | o200k_base 本地分词；当前 gpt-6-* 模型没有官方编码映射，必须标注估算 |
| E4 工具阶段 | 两个短命令实际并行执行，结束码分别为 0 和 7；可见开始、完成及输出事件 | 不把失败工具等同于整个 turn 失败 |
| E4 审批 | 可见 `item/commandExecution/requestApproval`；拒绝后工具为 declined | 只对实验创建的请求返回 decline |
| E4 取消与退出 | 一条真实 turn 变为 interrupted；真实 stdio 回合结束后包装器与 CLI 正常退出 | 独立实验环境 |
| E4 传输故障 | EOF、退出码、SIGTERM、非 app-server 调用、旁路写失败、拥堵与容量上限共 7 个场景通过 | 合成 CLI 的故障注入；不等同于完整 Desktop 崩溃测试 |
| E4 重放故障 | 2,800 条重复 delta 被去重，未知 item 与缺少 item 开始的增量被隔离 | 真实长输出记录的离线重放 |

## 并发关联与阶段

上游 item 元数据的 `turn_id` 与完整协议中的 `threadId / turnId` 建立映射，随后使用 `item_id` 归属正文和工具参数。12 个内容流均在收到增量时已有映射，不依赖完成文件事后补齐，也不使用测试标记或时间接近来猜测聊天。

长输出实验的工具输入分别为 3,803 / 5,171 字符、1,056 / 1,405 条 delta。两条聊天内容交错时仍保持完整且归属正确。捕获了 4 个明确 reasoning 开始 / 结束项目，可用于阶段持续时间；本次并发任务没有 reasoning summary 增量，summary 计数利用旧实验的 2 段、60 字符记录进行重放验证。

外层 exec 输入项与内层 commandExecution 的 ID 不同。当前证据支持按 thread / turn 维护独立活动；没有直接父子字段时，不强行声称某个内部命令属于某个外层调用。

在审批实验中，commandExecution 的 item/started 在审批请求前出现，状态也可能是 inProgress。产品必须结合明确审批事件展示“等待审批”，不能仅凭 item/started 就声称命令已实际执行。

首次长输出实验受父进程的嵌套沙箱影响，shell 被 `sandbox_apply: Operation not permitted` 拒绝。该运行仅用于生成增量和身份验证，未宣称工具成功。随后在允许子 Codex 启动自身只读沙箱的环境中补做较短工具回归，确认实际执行与退出状态。

### 采集完整性与延迟

| 运行 | 旁路捕获 | 未关联 / 延迟映射 / 丢包 | 接收日志时间至观察时间 P95 |
| --- | --- | --- | --- |
| 长输出并发 | 3,301 行，2,030,710 字节 | 0 / 0 / 0 | 约 0.474 ms |
| 短工具并发 | 468 行，1,044,061 字节 | 0 / 0 / 0 | 约 0.407 ms |

此延迟来自同机日志接收时间与观察时间之差，不代表网络端到端延迟、模型计算速度或启用采集的整体开销。没有进行模型性能的随机对照，因此不宣称零开销。

## Token 计数与开销

tiktoken 版本固定为 0.14.0；其模型映射未包含 gpt-6-luna、gpt-6.1-sol、gpt-6-astra。第一版明确选用 o200k_base 作为估算编码，同时保留模型、编码及匹配状态。

旧实验的 10,068 字符工具输入，在此编码下为 2,367 token。若逐字符独立分词相加，得到 10,068 token，多算 7,701；三种随机分块分别多算 480、474、455。原始分块在这个样本上恰好没有多算，不代表 delta 永远与 token 边界相同。

短样本复现了 6 次前缀追加后累计计数下降的情况。因此保留真实重算计数和尾部修正；窗口差值为负时暂时显示速度不可用，不能将负差截为零并累加到总量。

以下是独立内容缓冲的串行快照计数，使用每项 3 次计数的最大耗时。它不包括实时到达队列、完整聚合器或 UI 渲染。

| 每条流大小 | 1 条流 | 4 条流 | 8 条流 |
| --- | --- | --- | --- |
| 10 KB | 0.67 ms | 1.36 ms | 2.59 ms |
| 100 KB | 3.16 ms | 12.03 ms | 23.54 ms |
| 1 MB | 29.69 ms | 118.24 ms | 241.30 ms |

建议初版以 250 ms 显示刷新、2 秒接收窗口开始。计数采用后台快照任务，仅对变化内容重新计数；长内容或过多并行流自适应降频，不能让接近刷新周期的计数工作阻塞事件采集。基准进程峰值内存约 109–145 MB，包含 tokenizer 词表和文本缓冲，不是增量内存或产品整体内存。

## 故障与剩余边界

旁路队列被刻意缩小并放慢消费者后丢弃了 126 个观察包，主协议的 128 个 burst 输出及最终响应仍完整。旁路文件无法打开与捕获容量耗尽时，协议也完整，故障计数可见。产品须据这些信号标记“观测不完整”，不能把丢包后的速度作为可靠数据。

转发器是协议路径上的关键进程；这轮测试证明旁路解析 / 写入故障可以隔离，未证明转发器自身崩溃不影响任务。

后续产品集成需要完成：

- 日常 Desktop 的首次配置、正常启动入口和恢复说明，以及用户实际提交的多聊天看板试用。
- 完整活动状态机、接收窗口速度展示、历史积压处理与采集恢复。
- 后台长命令 / write_stdin、完整 MCP / hosted 工具覆盖及更多重试场景。
- 长期运行容量、真实事件队列负载及升级后必要的小范围回归。

这些边界不要求重做已通过的生成可观测性实验；应随产品最小完整路径逐项验收。

## 文件与复查

- [live.json](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/live.json)：两个实时运行的字段映射、计数、哈希、持久化一致性与证据路径。
- [desktop.json](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/desktop.json)：独立 Desktop 启动回归与恢复。
- [tokenizer.json](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/tokenizer.json)：模型映射、分块一致性、修正与计算开销。
- [approval.json](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/approval.json)：审批请求及 declined 生命周期。
- [faults.json](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/faults.json)：传输故障与重放验证。
- [实验运行说明](/Users/bytedance/personal/codex_progress_monitor/experiments/product-preflight/README.md)：可复用脚本与复查方法。

原始内容、配置备份、Desktop profile 和依赖都在被忽略的 `.local/product-preflight/`；真实任务会话记录保留于原隔离 CODEX_HOME。所有新建实验进程已结束，原有空闲隔离 WebSocket 后端保留。

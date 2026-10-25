# 验证与测量附件

本目录只保存脱敏统计和合成输入的结果。方案、当前预算和验收边界分别见上级的[技术设计](../technical-design.md)、[资源预算](../resource-budgets.md)和[实施记录](../implementation-results.md)。JSON 不是新的设计或完成声明；同名指标须先核对工作集、实现阶段和仪器化范围再比较。

| 阶段／用途 | 附件 | 证据边界 |
| --- | --- | --- |
| P0 基线 | [p0-baseline.json](p0-baseline.json) | 旧驻留链的自然样本、投影成本与环境记录 |
| 切换前旧文件链 | [pre-cutover-file-channel.json](pre-cutover-file-channel.json) | 原 P0 探针 3,607 次成功发布，另采当前旧链 20 s 文件大小／更新下界；当前点样本已含 P0 语义改动，不冒充 untouched 基线 |
| P3 受控原生验收 | [p3-validation.json](p3-validation.json) | 存档阻塞、慢读端与正常退出；不替代新版本自然回合 |
| P3 日常 GUI／collector 更新 | [p3-daily-cutover.json](p3-daily-cutover.json) | 实际构建／源码、同实例递增序号及拥有连接；Desktop 重开与完整自然样本仍待验收 |
| P3 Desktop 重开后核对 | [p3-desktop-restart.json](p3-desktop-restart.json) | 新驻留 bridge 身份／源码与原后端父子关系、实时许可及旧提示消失；自然延迟／长期样本仍待验收 |
| P5 自然窗口复核 | [p5-natural-window-review.json](p5-natural-window-review.json) | 30 分钟／1,793 条新鲜探测，但有超长诊断行丢弃和 bridge RSS 峰值；不构成完整自然回合、结束延迟或长期验收通过 |
| P5 首轮前后值 | [before](p5-before.json)、[after](p5-after.json) | 全索引复制改为所选 turn 视图；不是最终目录实现 |
| P5 旧缓存边界 | [百万行](p5-million-lines.json)、[超过缓存上限](p5-over-cache.json) | 保存促成目录修订的原反例 |
| P5 当前目录 | [百万行](p5-directory-million-lines.json)、[原超限工作集](p5-directory-over-cache.json) | 相同规模下冷／热／追加、权重与临时分配 |
| P5 50 源轮转 | [before](p5-working-set-before.json)、[after](p5-working-set-after.json) | 目录与 turn 互相挤出的反例及修正；相同 300,050 行工作集 |
| P5 源分布 | [10×10 万行](p5-ten-sources-100k.json)、[50 源含百万行](p5-fifty-sources-million.json) | 顺序 provider，填充行明确为合成 token-count；不当作并行活动 |
| P5 巨大 turn | [before](p5-giant-turn-before.json)、[after](p5-giant-turn.json) | 超大单项准入不挤掉其他缓存；整 turn 的临时构造成本仍高 |
| P5 校正读取 | [完整摘要](p5-facts-full.json)、[明确 item 范围](p5-facts-scoped.json)、[50 源窄事实](p5-working-set-facts.json) | 最近 20 项与请求范围保留；不代表完整时间线选择峰值 |
| P5 单项详情 | [完整 turn 对照](p5-detail-full.json)、[当前单项](p5-detail-selected.json) | 同一 48,000 phase 源／同一首页面；冷范围验证仍为线性成本 |
| P5 目录不准入 | [p5-detail-over-directory.json](p5-detail-over-directory.json) | 700,000 phase 超过目录缓存预算，保留热单项；冷／追加可能超过 GUI 期限，直接 provider 结果不当作 GUI 通过 |
| P5 时间线与回复 | [原路径对照](p5-timeline-full.json)、[当前元数据](p5-timeline-metadata.json) | 原帧超过既有 GUI 预算，当前返回完整错误；全部元数据构造仍有临时峰值 |
| P5 校正队列 | [p5-history-queue.json](p5-history-queue.json) | 默认请求／结果字节预算、丢弃／重试与单次解码 |
| P5 分词反例 | [native 边界](p5-tokenizer-boundary.json)、[8 MiB 原型](p5-tokenizer-process-before.json) | panic 与单任务内存，保留收紧准入的依据 |
| P5 当前计数进程 | [p5-tokenizer-process.json](p5-tokenizer-process.json) | 1 MiB 准入工作集、deadline／恢复、冷／热及子进程 RSS |
| P5 默认身份准入 | [p5-identity-default-limits.json](p5-identity-default-limits.json) | 不改默认值，分别填满 65,536 项／8 MiB 源 ID；直接账本，不代表 Engine 事件吞吐或自然运行 |
| P5 事件速率 | [p5-event-rates.json](p5-event-rates.json) | 9 组有节奏合成输入，各 3 s；未经过 socket 或原生 draw |
| P5 组合原生负载 | [p5-combined-native.json](p5-combined-native.json) | 50 流／30 万 delta、双查询、存档／慢 reader、300 s 负载加 120 s 空闲；保留 fixture 失败和测量器额外 I/O，不是自然 soak |
| P6 投影 | [p6-validation.json](p6-validation.json) | 语义样本、getter／构建／主 actor 与原生交互分别计量 |
| P7 派生存储 | [p7-resources.json](p7-resources.json) | 元数据阻塞队列与加速 Engine／身份计量，不是自然 soak |
| P7 边界审计 | [p7-boundary-audit.json](p7-boundary-audit.json) | eager 相对导入、保留的延迟边及所有权；不是任意动态依赖证明 |
| 切换前范围审计 | [pre-cutover-audit.md](pre-cutover-audit.md) | 对照原计划 41 项；保留日常切换／新版自然记录的未完成结论 |

原始私有 trace、runtime、截图和真实 JSONL 留在忽略目录，不随这些附件进入公共仓库。本文保留各次测量，不用最新数值覆盖原失败证据。

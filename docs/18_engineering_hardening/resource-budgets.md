# 资源预算与测量范围

状态：P5 的分段索引、后台校正队列及独立计数进程已有测量与预算；并行组合压力与自然持续运行仍需补验收，EH04 尚未完成。工作区未提交。合成输入不包含本机会话，所有数值都带有下面的工作集边界。

## 可复现测量

```sh
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_resources.py --label current --output /tmp/cpm-resources.json
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_history_working_set.py --output /tmp/cpm-history-working-set.json
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_history_queue.py --output /tmp/cpm-history-queue.json
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_tokenizer_process.py --output /tmp/cpm-tokenizer-process.json
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_reconciliation.py --output /tmp/cpm-fact-projection.json
PYTHONPATH=src .local/venv/bin/python tests/benchmarks/measure_event_rates.py --output /tmp/cpm-event-rates.json
CPM_PROJECTION_BENCHMARK=1 swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos --filter 'syntheticSnapshotProjectionBenchmark|syntheticProjectionConstructionAndMemoryBenchmark'
```

Python 3.14.7、macOS arm64，本轮数据为 [P5 修改前](evidence/p5-before.json)、[修改后](evidence/p5-after.json)、[P7 队列预算](evidence/p7-resources.json)及 [P6 投影](evidence/p6-validation.json)。含随机选择的基准使用种子 20261023，其余使用固定输入；计时和 RSS 包含基准自身及运行库开销，不是最低支持环境或发行版本指标。cold 指 provider 首次建索引；生成写入后的系统页缓存可能仍热，没有清空系统缓存。

下表保存首轮测量；后续目录、计数进程和队列修订见下文，不将早期的“修改后”当作最终状态。

| 工作集 | 修改前 | 修改后 | 含义与取舍 |
| --- | --- | --- | --- |
| 单个 rollout，1,000 turn／4,000 phase／约 7.7 MB；读取其中一个 4-phase turn | 热视图 p50 38.16 ms；峰值新增分配约 5.14 MB | 热视图约 0.7–0.8 ms；峰值新增分配约 0.14 MB | 只脱离所选 turn；仍有遍历元数据的成本，不是常数查询 |
| 同一工作集冷索引／追加后读取 | 约 98.6／21.9 ms | 约 129／56 ms（首轮） | 增加了变更后的堆权重计量；在读 worker 内完成，不在 Engine tick 内。完整原始数据见 JSON，时间会波动 |
| 256 个 64 KiB 流，注入计数函数被阻塞并切换两次模型 | 768 个待执行／执行中任务，约 48 MiB 输入 | 2 个，128 KiB 输入 | 模型切换不免除旧 Future 的预算；这些是阻塞场景字节计量，不是 RSS |
| 混合中英／emoji 文本分词 | 64 KiB 约 1.6 ms；1 MiB 约 24 ms | 相近 | 任务准入改变，BPE 算法未替换；不能推断极端重复文本的最坏耗时 |
| 1,000 个加速合成 turn／5,000 事件 | 未取得同口径前值 | 单线程五事件加一次 tick 的 p95 约 0.16 ms，最多 21 stream／20 item | 同步小文本计数，约 0.15 s 加速运行；不是自然速度、并行流或长时间 soak |
| 同例身份保护 | 旧状态依赖展示保留 | 2,002 身份，2,000 终止；压缩账本约 262 KiB | 删除正文／展示活动后仍保留拒绝旧事件的证据 |

Python 基准进程高水位约 143 MB（修改前约 153 MB），包括 tokenizer 初始化和全部顺序场景；不能将差值归因为应用长期常驻内存改善。

## 较大索引与缓存边界：新增证据

以下两个附件是目录拆分前的测量，保留为对照，不代表当前热查询成本。

[百万行档位](evidence/p5-million-lines.json)：1,000,002 行、159,159,063 B、10,000 turn／40,000 phase。首次索引约 3.963 s，3 次热视图的中位数约 8.27 ms；追加后约 371 ms。索引权重 64,107,255 B，仍在 64 MiB 内；基准进程 RSS 高水位约 176.7 MB。此例补足单 rollout 的百万行数量档位，尚未覆盖 50 个聊天或百万个活动。

[刚超过缓存上限的反例](evidence/p5-over-cache.json)：72,002 行、92,877,063 B、12,000 turn／48,000 phase，整索引不能常驻。首次索引约 1.916 s；带 tracemalloc 的单次“热”视图变成约 7.525 s 的全量重建，追加后未启用 tracemalloc 的查询仍约 1.667 s。3 次淘汰后缓存为零，热视图临时分配峰值约 115.7 MB，进程高水位约 267.5 MB。单样本不能当延迟分位数；可以确认当时的缓存粒度造成了反复重建。

### 当前修订与复测

当前 source 目录与所选 turn 索引分开，详情、完整时间线和历史校正共用 provider。目录只记录 turn 范围、固定大小摘要链、关系与活跃项；所选 turn 才保存完整 phase 元数据和只读引用，内容仍从 JSONL 读取。范围摘要及 session／文件 stamp 校验覆盖旧区域改写后追加的反例，完整解析器继续用于语义对照。

同一 [12,000 turn／48,000 phase 工作集复测](evidence/p5-directory-over-cache.json)：首次目录约 1.554 s，3 次热详情中位数约 0.199 ms，追加后约 135 ms；缓存权重 19,848,278 B，未淘汰，热查询新增分配峰值约 153 KB。共享预算仍为 64 MiB。这消除了该反例的每次全量重建；不是单个巨大 turn 或任意大目录都能常驻的保证。

新增 [50 源修改前](evidence/p5-working-set-before.json)／[修改后](evidence/p5-working-set-after.json)：合计 300,050 行、约 42 MB，每源 1,000 turn／4,000 phase，每次校正 20 个 turn。原有目录／turn 淘汰互相挤出，第二轮仍解析 306,050 行，单源中位数约 93.7 ms。当前优先淘汰可重建目录，turn 条目上限按 50×20 的工作集调整到 1,024；文件 header／stamp 未变时，不要求目录常驻。第二轮解析 **0 行**，单源中位数约 0.858 ms，50 源合计约 43.8 ms，缓存权重约 65.6 MB，低于 64 MiB。冷轮约 5.52 s；这个改进没有隐藏冷扫描成本。基准进程高水位约 105.8 MB，是顺序 provider 压力，不是 50 个并行事件流或 collector RSS。

当前 [百万行目录复测](evidence/p5-directory-million-lines.json)：同为 1,000,002 行／159,159,063 B，首次目录约 4.089 s，3 次热详情中位数约 0.193 ms，追加后约 112 ms，缓存权重约 16.5 MB。百万行首次扫描仍需约 4 s；优化的是重复查询的扫描量和元数据驻留。

进一步补齐两个分布档位：[10 源各 10 万行](evidence/p5-ten-sources-100k.json)合计 1,000,000 行／约 90 MB，冷轮合计约 3.93 s，热轮约 7.58 ms，扫描 0 行，缓存约 17.9 MB。[50 源含一个百万行源](evidence/p5-fifty-sources-million.json)合计 1,294,049 行／约 128 MB，冷轮约 8.52 s，热轮约 40.2 ms，扫描 0 行，缓存约 65.6 MB。每源仍为 1,000 turn／4,000 phase，其余行是合成 token-count 记录；行数不冒充活动数量。这些分别验证源分布和扫描成本，仍是顺序 provider 基准。

### 单个巨大 turn：原反例与按用途读取

[准入修正前](evidence/p5-giant-turn-before.json)／[修正后](evidence/p5-giant-turn.json)将同样 48,000 phase 集中在一个 turn：48,004 行／约 90.9 MB。当前目录约 5.22 MB 可驻留，超大 turn 索引绕过缓存，不再先挤掉其他正常选择；对应新反例在修正前失败、修正后通过。追加后的未仪器化读取从约 2.42 s 降到约 1.44 s。

当时仍需一次构造整个所选 turn。带 tracemalloc 的单次视图约 6.22 s，临时分配峰值约 134 MB，基准进程高水位约 347 MB；单次样本不是时延分位数。这促成下述事实、单项详情和时间线元数据的用途拆分；64 MiB 常驻缓存不能称为全部 RSS 上限。

当前事实校正已与完整阅读选择分开，改动不缩短正文或历史可查看范围。[完整 turn 摘要](evidence/p5-facts-full.json)／[明确 item 范围摘要](evidence/p5-facts-scoped.json)使用同一个 48,000 phase、约 90.9 MB 源。请求两个较早 item，按原有规则额外保留最近 20 个终止活动：完整路径返回 48,000 项且超出 8 MiB 交接预算；窄路径返回 22 项，编码 12,997 B。

在目录已建立的前提下，首次摘要的 tracemalloc 峰值从约 134 MB 降到约 0.194 MB；窄路径首读含仪器约 2.34 s，热摘要中位数约 0.300 ms，追加后的未仪器化摘要约 603 ms。窄路径基准进程高水位约 40.6 MB，完整摘要约 328.5 MB。首次目录另花约 0.96 s，约 5.0 MB 的目录／事实缓存驻留；完整来源范围仍要验证，不能宣称追加或冷查询已变成常数时间。

结果携带 `phase_selection`，只有明确请求的 item 能作缺失／退役判定。范围外事实不因没出现在窄结果中而被撤销；分批来源替换也不保留旧源时间。真实 collector 的 20,000 phase 合成 turn 在没有时间线查询的情况下，当前工具得到校正且没有交接超限。完整时间线的巨大选择仍有上述临时构造成本，本项优化不能替代它。

补充 [50 源生产窄事实工作集](evidence/p5-working-set-facts.json)：同为 300,050 行、每源校正 20 turn，每个 item 范围明确列出。热轮扫描 0 行，单源中位数约 1.53 ms，50 源合计约 77.2 ms；缓存权重约 66.3 MB，仍在 64 MiB 内。它包含额外的身份选择／覆盖判断，不能只引用先前不带该契约的约 0.86 ms 作为当前生产成本。

### 单项详情与完整时间线

[旧单项读取](evidence/p5-detail-full.json)／[当前单项读取](evidence/p5-detail-selected.json)对同一 48,000 phase／约 90.9 MB turn 读取同一项、输出相同 1,374 B 首页面。旧路径构造整 turn；当前 `HistoryDetails` 只保留目标及明确 call 依赖。首次临时分配峰值约 134 MB → 0.165 MB，热读取中位数约 1,552 ms → 0.226 ms，追加后约 1,468 ms → 663 ms，进程高水位约 334 MB → 40.5 MB。当前目录首次约 1.10 s、仪器化首次详情约 2.29 s；冷／追加仍需完整范围摘要验证，明确依赖有时需要两遍。

[旧完整时间线](evidence/p5-timeline-full.json)／[当前时间线](evidence/p5-timeline-metadata.json)保留相同的 48,000 项展示元数据，当前不缓存正文／输出引用。首次查询与编码的 tracemalloc 峰值约 204 MB → 148 MB，基准 RSS 高水位约 534 MB → 264 MB；未仪器化热查询加编码约 1,867 ms → 766 ms。旧路径的 30.6 MB 回复超过现有 GUI stdout 限制；当前按同一 8 MiB 预算返回 40 B 的 `query_too_large` 完整帧，避免截断后再次离线构造。缓存权重约 61.6 MB，低于 64 MiB。工具只生成回复，不把另一客户端的整帧回读解码计入服务端峰值。

完整时间线仍是与 phase 数量成正比的元数据构造，没有总 RSS 硬上限；本轮不靠静默删 phase 或截短原始详情满足预算。现有 GUI 无法接收的超大帧获得明确错误，已有阅读保留。扩大整帧可读能力需单独的时间线分页设计，不能将单项详情的窄读取收益套用到它。上述首次峰值含仪器化，热时间不含；均为合成基准而非自然时延分位数。

[超过目录预算的单项样本](evidence/p5-detail-over-directory.json)包含 700,000 phase、700,004 行、约 135 MB。目录冷扫描约 12.08 s 后被淘汰，缓存权重为零；单项热读约 0.168 ms，最终缓存约 7,142 B。追加后的未仪器化重读约 16.82 s，说明默认 12 s GUI 命令期限可能先结束；直接 provider 基准没有该期限，不能称为 GUI 读取通过。仪器化首次详情约 82.44 s，临时峰值约 148 MB、进程高水位约 328 MB。可控的是常驻准入与失败呈现，巨大目录的冷成本仍线性，扩大这种支持规模需单独的派生目录／按需定位方案。

### 校正队列与分词测量

[默认校正队列压力](evidence/p5-history-queue.json)：200 个合成请求触发请求字节准入，首批接收 69 个；待处理峰值 1,035,818 B，待取结果峰值 33,437,030 B，均在默认预算内。14 个旧结果被挤出后等待后续轮转重试；每次 drain 最多约 7.90 MB，纯 JSON 解码约 8.2–8.6 ms，不含事实应用。单个超过 8 MiB 的结果降级为无 revision 的来源不可用摘要；不能因预算失败撤销历史事实。

[原生分词边界](evidence/p5-tokenizer-boundary.json)发现 1 MiB 连续空格触发 PyO3 `PanicException`，它不属于普通 `Exception`；现已在第三方边界归一化并在所属子进程内隔离。其他工作和官方 usage 继续。失败正文缓冲立即释放，速度未知。

[8 MiB 输入试验](evidence/p5-tokenizer-process-before.json)中重复 ASCII 的计数约 2.14 s，子进程高水位约 537 MB，说明只有任务数上限不足以控制单任务成本。因此生产准入采用单次 **1 MiB**、两个任务合计 **2 MiB** 输入；更长前缀只停止近似计数，正文保留／回收仍遵守独立 Engine 预算，JSONL 可查看范围不变。

[当前计数进程复测](evidence/p5-tokenizer-process.json)：首个 64 KiB 混合文本含启动约 237 ms；热 1 MiB 混合文本约 28.7 ms，1 MiB 重复 ASCII 约 229 ms。将 deadline 显式降为 100 ms 的真实 native 运算约 109 ms 中断，后续计数约 222 ms 重建并成功；空闲关闭约 2 ms。被回收子进程的 RSS 高水位约 165 MB，是这些输入的实测，不是任意内容的硬 RSS 上限。默认单次请求期限 3 s；原始 stdio 和 Engine 不等待计数结果。阻塞请求 pipe、native panic／崩溃、错误响应及关闭活动／排队请求已有测试。

## 已执行的上限

| 组件 | 上限／单位 | 回收、降级与验证 |
| --- | --- | --- |
| 历史索引常驻缓存 | 每个共享 provider 64 MiB 可达 Python 元数据权重；源条目 12（collector 为 64），turn／窄事实／单项详情／时间线选择合计 1,024 | 字节超限优先淘汰目录，再按 LRU 回收选择；目录淘汰不要求丢掉未变的选择。单个超大索引可读但不常驻；变更 revision 后计权重 |
| 分词任务 | 全局 2 个 Future／2 MiB UTF-8，单次 1 MiB；一个所属子进程，单次执行期限 3 s | 每流合并最新正文，轮转准入；失效模型工作仍计量。超限 `count_state=budget`，失败／超时为计数不可用；关闭中断所属 worker 并取消排队工作，官方 usage 独立 |
| 历史校正交接 | 最多 128 请求／结果；请求合计 1 MiB、单个 64 KiB；结果合计 32 MiB，单个及每次 drain 8 MiB | 请求只含 turn／item 身份及紧急程度；worker 编码不可变结果，慢读端挤出旧结果后重试。单项超限不携带 revision／完整成员声明；有限 close 不发布迟到结果 |
| Engine 文本 | 单流 8 MiB；总量超过 32 MiB 后降到不超过 16 MiB UTF-8 | 释放较大缓冲，标记 `memory_limit`；修复原先按字符数停止回收的错误。多字节文本反例已通过；Future 输入另行计量 |
| 身份准入／终止保护 | 生命周期内合计 65,536 个 scoped observation identity，累计源 ID UTF-8 最多 8 MiB | 压缩键只用于准入，不提供历史完成状态。终止证据不按 TTL 遗忘；达到任一上限，关闭观察准入、记录 gap／`identity_budget`，等待新 collector 基线。终止 item／turn、明确停止的 backend 迟到重放、长断线后真实重连及两种预算耗尽已有反例 |
| 未关联事件 | 原有 4,096 条／2 MiB／10 s | 到期不猜归属，无法关联保持未知；不是历史存储 |
| Bridge 副本 | 原有 128 chunk／8 MiB | 观察端丢弃并记录 gap；原始 stdio 不等待、不变更字节。P3 验证许可失效与分帧代次 |
| 状态发送 | 4 peer；单帧 8 MiB；每 peer 发送中帧加一个可替换最新帧；3 s 写入期限 | 过大状态明确 unavailable；慢读端关闭。P3 有 30 s 存档阻塞和 60 s 慢读端原生验收；内核缓冲与对象开销不包含在应用帧字节内 |
| 元数据待写 | 4,096 record／2 MiB UTF-8；另有一份有同样上限的写入中批次 | 最新同 ID 合并。超限清空不完整派生缓存并有界重建，不影响 Codex JSONL；数量、字节、单记录过大、写失败恢复及关闭后的拥有权均有反例 |
| 恢复存档 | 一份写入中加一份最新待写；常规约 10 s | 生命周期优先、有限退出；P2／P3 已验证背压和失败重试，不构成 RSS 总上限 |
| 详情分页／查询回复 | token 最多 16 KiB，单 section 页最多 262,144 code point；socket／CLI／Swift 共用含换行 8 MiB 响应 | token 不授权路径，变化不拼页；超限返回完整错误，坏帧不触发重复离线解析。分页、实际服务／CLI 的 UTF-8 输出和两个阅读入口保留已有内容均有反例 |

身份数量、累计 ID 字节和 Future 输入都有明确上限，但这不代表所有 Python 对象、单次 JSONL 冷加载或 tokenizer 的 CPU 都被限制在一个固定 RSS 数字内。预算耗尽产生真实观察缺口，不能靠删 tombstone 后继续接收旧 ID 来伪造连续性。此项降级不缩短权威 JSONL 的可查看历史范围。

[默认身份账本补测](evidence/p5-identity-default-limits.json)没有调小预算：65,536 个短 ID 填满数量上限，集合／32 B 摘要可达堆约 6.36 MB；2,048 个各 4,096 B 的 ID 则恰好填满 8 MiB 字节上限，账本可达堆约 0.265 MB。两档下一身份均拒绝，首／中／末三个终止探针仍存在。填充时间约 1.71 s／86 ms，带 tracemalloc，不是事件吞吐；基准进程高水位约 41.5 MB，也不是产品 RSS 预算。Engine 的 gap／未知及旧事件重放由资源用例另验。

元数据阻塞基准：20,000 个窄记录加 2,000 个宽记录触发 7 次缓存失效，待写峰值 4,096 条／2,095,786 B，写入中为 1 条／43 B；解除屏障后约 13.4 ms 排空。结果在 `p7-resources.json`。被丢弃的是不完整派生缓存，不是权威会话记录；该样本也不代表 SQLite 的所有锁／磁盘故障。历史缓存诊断以不可变摘要发布，实时 tick 不等待 JSONL reader 锁。

## 有节奏的并行流输入

[事件速率结果](evidence/p5-event-rates.json)包含 1／10／50 个同时活跃流与 10／100／1,000 delta/s 的 9 个组合，每组 3 s。使用真实异步计数进程，预热成本单独记录；约每 100 ms 做一次 Engine tick／snapshot、脱离副本和完整状态编码。它只测 Engine 与编码，未经过 socket 或原生 GUI；自然长期运行和组合 I/O 压力仍是独立验收。

各组 ingest p95 约 0.20–0.44 ms；50 流的 tick 加 snapshot p95 约 5.9–10.3 ms，一次性结束全部 50 流并生成空闲状态约 9.6–15.9 ms。所有组最多两个计数任务，没有执行超时；准入不足时仍使用明确未知状态，并未要求每个流的速度始终可知。基准父进程 RSS 高水位约 33.1 MB，已回收计数子进程约 113.3 MB；不能直接与旧的单进程 RSS 相减，也不代表自然运行峰值。调度迟到、状态复制／编码、frame 字节及关闭时间保留在 JSON，未只取最好的一组。

## GUI 投影

每个已解码 `Snapshot` 持有一个不可变 `SnapshotProjection`；副本和 heartbeat 共用它，没有按所有历史序号累积的全局缓存。投影不持有 store／NSView／窗口；丢弃状态后 ARC 释放的反例已通过。

10／100／300 聊天的同口径 getter 中位数约 0.0033／0.0035／0.0034 ms；P0 为 2.153／156.443／1336.178 ms。缓存之外的解码和完整投影另测约 0.229／2.084／5.121 ms；3,000 聊天约 52.012 ms，进程高水位约 63.3 MB。计时在 Swift debug，构建位于读 worker；不能当成原生 draw 或 release 耗时。

图关系与汇总约 `O(V+E)`，recent 排序另计；5,000 节点隐藏祖先链的 cycle／ancestor 步数分别为 5,000／4,999，展开使用迭代遍历。最近列表到期只更新选择，语言与健康改变不重建图。60 组固定合成样本从 `c3996dc` 的分组实现生成，逐项比对 roots／members／unassigned／children／active／展开顺序和深度一致。

另在主 actor 实测 Dashboard 接受已解码状态、更新展示和行标志：10／100／300／3,000 聊天中位数约 0.100／0.522／1.686／16.663 ms，解码和图构建放在 detached worker。该范围不含 SwiftUI／AppKit 绘制；高聊天数下的原生 draw 仍待压力验收。后台实例只在明确 `bridge/stopped` 后退役，长断线的展示回收不作为终止证据，避免阻止有效重连。

## 组合原生负载与资源回落

[50 流组合验收](evidence/p5-combined-native.json)经过实际 bridge／collector／Unix socket／Preview AppKit，并叠加 50 源×1,000 历史 phase、双查询、59 次源追加、至少 30 s 存档阻塞和 60 s 慢 reader。300 s 内发送 30 万 delta，随后保持 120 s 空闲；210 次状态健康探测、2,650 次查询均通过，原始转发字节和退出码不变。最后全部结束到空闲 draw 约 198.62 ms，是一个批次样本。

| 当前 RSS | GUI | collector | collector 所属子进程合计 |
| --- | --- | --- | --- |
| 本轮峰值 | 107.8 MB | 138.3 MB | 127.3 MB |
| 负载最后 60 s 中位数 | 100.1 MB | 136.8 MB | 77.7 MB |
| 空闲最后 60 s 中位数 | 90.5 MB | 60.4 MB | 23.5 MB |

缓存权重约 63.5 MB，计数最多两个任务／204,000 B 输入，无 deadline 超时；校正和元数据队列均在默认预算内，空闲时无待处理。累计 56,384 次计数准入延后，当前覆盖不完整时继续显示未知。查询的 p50/p95/p99、调度迟到、RSS 序列和资源检查点保留在 JSON。

首轮 fixture 的重复 ID 失败与测量器额外屏障文件 I/O 明确记在[实施记录](implementation-results.md#p5组合原生负载与空闲回落)。这是带仪器化的受控持续样本；回落可能包含系统驻留／回收，不能证明没有泄漏，也不能当作自然长期运行或任意聊天数下的 draw 保证。

## 仍需补足

- 巨大 turn 的事实、单项详情、时间线与回复边界分别已有测量；完整时间线及巨大目录冷读取仍有线性临时成本，尚无任意源规模的 RSS 硬上限。组合压力已有 300 s 负载加 120 s 空闲样本，仍不能替代自然长期运行。
- 分词准入／deadline、校正字节预算和子进程 RSS 已一起进入原生压力样本；巨大目录不准入的冷成本也已测量，可能超过现有命令期限。单次在途索引／编码的临时分配不等于常驻队列字节上限。
- 当前 50 流已有原生 draw，主 actor／投影成本另外计量。统一切换后的自然运行和长期 RSS 趋势仍待验收；并行 Remote 属发行矩阵。P0 自然样本与 P3 原生屏障继续保留其各自范围。
- 若测量表明必须改变历史可读范围或产品交互，先提供实际数据及选项供维护者决定；不以缓存上限静默丢失权威历史。

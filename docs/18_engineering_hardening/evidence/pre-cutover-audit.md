# 切换前范围核对（非完成声明）

日期：2026-10-23。对照第18章最初落盘方案的 **41 个实施项**，逐项保留原范围；编号按原阶段内顺序。核对依据副本保存在私有切换准备中，下面的证据不是用当前勾选状态反推原任务已经完成。当前实现／测试结果的时间边界仍见[实施记录](../implementation-results.md)。

当前结论：实施、受控故障与规模证据已有记录，**日常 GUI／collector／驻留 bridge 已统一更新并核对实际运行，新版自然／长期记录未完成**，不满足整项目标完成条件。切换前核对后新增的实际证据见[日常更新记录](p3-daily-cutover.json)与[Desktop 重开核对](p3-desktop-restart.json)。最低支持平台、发行矩阵及开源发行准备另行验收，不由本表替代。

## P0：固定基线、历史交接与菜单栏验收

[基线](p0-baseline.json)、[历史校正反例](../../../tests/test_reconciliation.py)、[真实菜单栏调查](../../17_review_and_refactor/status-regression.md)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P0.1：核对 `c3996dc` 的构建／运行身份，记录 Python／Swift、系统、后端版本和本机环境；不因源码已修改就假定驻留进程已更新。 | p0-baseline 记录系统／Python／Swift和 Monitor 指纹；目标磁盘 Desktop／CLI 版本另在切换准备中记录，不反推旧进程已加载目标文件。 | 基线证据 |
| P0.2：从开始到结束观察完整新回合，记录正文／turn 结束、Engine、状态发布、GUI 应用和 AppKit draw；覆盖短回复及回复结束后临时任务。 | 3 个完整自然回合；明确临时后台任务另有真实跨重载样本。新版完整自然链路仍待切换后验收。 | 基线有限 |
| P0.3：复核已修复的 pending end、临时会话范围及文件通知，明确约 2 秒现象是否还存在、耗时落在哪一层。失败时先形成可复现场景和有界修正。 | 完整样本未复现约 2 s；不可用区间保留。新版受控完成到 draw 约 199 ms，不推广为自然时延保证。 | 基线证据 |
| P0.4：按决策文档收敛当前补充与 JSONL 历史交接：区分未落盘、临时不可读与已验证源替换；清理不必要的并列历史 fallback。 | reconciliation 覆盖迟到、末行、不可读、替换／截短和旧影子完成；4 个基线失败反例修复后通过。 | 实现／反例 |
| P0.5：记录改造前的事件延迟、快照大小／写盘次数、进程 RSS 和 GUI 分组成本，供后续比较。 | P0 成本／3607 次成功发布、RSS 点样本及合成投影字节已记录；另补旧文件链 20 s 大小／更新下界，明确其已经包含 P0 语义修改。 | 有范围记录 |

## P1：统一子进程执行器

[执行器](../../../macos/Sources/MonitorApp/ProcessExecutor.swift)、[真实进程测试](../../../macos/Tests/MonitorAppTests/ProcessExecutorTests.swift)、[调用方测试](../../../macos/Tests/MonitorAppTests/BackendExecutionTests.swift)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P1.1：定义 `ProcessExecuting`、请求／结果／失败类型和取消所有权。 | ProcessRequest／Result／ProcessExecutionError 与 ProcessExecuting；取消和进程组由同一协调者拥有。 | 实现／受控 |
| P1.2：并发排空 stdout／stderr，落实输出字节限制及结构化失败。 | drainsBothPipesAndKeepsOnlyBoundedStderrTail、outputOverflowTerminatesAndReapsOwnedChild 使用真实 pipe。 | 实现／受控 |
| P1.3：完成 deadline、SIGTERM／SIGKILL 宽限、有限 pipe drain 和可证明的进程组归属。 | 忽略 TERM、继承 pipe、截止及取消竞争均经过实际所属 child；WNOWAIT／组归属在执行器收敛。 | 实现／受控 |
| P1.4：让 `BackendClient` 与 `SystemTimelineClient` 接入同一执行器；保留服务注入与选择代际校验。 | BackendClient 与 SystemTimelineClient 都调用统一 executor；真实 Swift→Python CLI 与选择代际有证据。 | 实现／受控 |
| P1.5：对有副作用的管理命令定义取消／超时后的状态重查与恢复；独立 collector 与短命执行进程的所有权分别验证。 | 失败／取消后的管理状态重查、已有副作用和 detached service 分别验证。 | 实现／受控 |
| P1.6：删除被替代的 `QueryProcess` 超时／取消实现，验证没有旧 timer 或后台进程残留。 | 生产 QueryProcess 已删除；启动前取消、退出竞争和后续请求不受旧操作影响有真实进程反例。 | 实现／受控 |

## P2：状态订阅和后台恢复存档

[服务端测试](../../../tests/test_state_transport.py)、[Swift订阅测试](../../../macos/Tests/MonitorAppTests/StateSubscriptionTests.swift)、[P3原生记录](p3-validation.json)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P2.1：用临时 runtime 验证 Python／Swift Unix stream 的分帧、取消、UTF-8 边界、权限及版本协商；固定首版状态协议与起始参数。 | Python／Swift 真实 Unix socket、合包／逐字节 UTF-8、权限、版本与取消检查通过。 | 实现／受控 |
| P2.2：建立不可变状态交接，加入实例、序号、tick 进展和有界最新状态发送。 | DetachedSnapshot 脱离 Engine；实例／序号和 tick/nonce 的时钟边界分别验证。 | 实现／受控 |
| P2.3：实现 `state.sock` 的完整状态、心跳、连接上限、背压和慢读端关闭；I/O 不持有领域锁。 | 4 peer、8 MiB 帧、慢端关闭；原生 60 s 暂停 reader，正常端持续健康。 | 实现／受控 |
| P2.4：将恢复检查点序列化／原子写入移到有界 worker，加入重试、诊断和有限退出保存。 | 30 s writer 屏障、失败／恢复、最新值合并和有限 close；写 worker 结束前不交生命周期锁。 | 实现／受控 |
| P2.5：增加 Swift `StateSubscribing`，在隔离界面路径中应用状态；验证独立于 management 查询的任务生命周期。 | StateSubscribing 已接生产代码；management 阻塞时独立应用状态，原生路径已有验收。 | 实现／受控 |

## P3：租约、桥接热路径与实时读端切换

[租约](../../../tests/test_observation_lease.py)、[许可](../../../tests/test_observer_permit.py)、[管理](../../../tests/test_managed_control.py)、[原始转发](../../../tests/test_runtime.py)、[组合原生](p5-combined-native.json)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P3.1：GUI 通过拥有连接续期观察租约；普通订阅／终端 watch 不取得管理权限。正常释放、过期、多个 GUI 竞争与旧 session 均有规则。 | v2 grant 与拥有连接续期；release、崩溃到期、竞争 GUI、重试／确认丢失和旧 session 有反例。 | 实现／受控 |
| P3.2：collector 的已连接热循环不再读 observation 文件；管理、启用配置和恢复记录有独立 I/O 边界。 | load_lease 只在启动读取；原生验收阻塞后续配置读取，观测热循环继续。 | 实现／受控 |
| P3.3：给 bridge worker 增加能力握手及内存许可；`Observer.offer()` 只读内存／有界入队。原始字节、退出码和 EOF 转发回归保持一致。 | Observer.offer 的内存许可／有界队列及分帧代次；原始字节／退出码和 pipe 信号路径有真实 fixture。 | 实现／受控 |
| P3.4：终端 `watch`、`--once`、`--json`、`status` 和启动存活检查按各自职责迁移；显式离线／fixture 路径保持独立。 | CLI 实时读端用 state.sock；显式文件与离线查询边界独立，坏服务回复不会触发第二次离线读取。 | 实现／受控 |
| P3.5：切换生产 GUI，移除生产路径的目录通知和常规快照读取；按连接／tick 进展呈现新鲜度与恢复状态。 | 生产文件读路径已替换；日常更新后实际 GUI build、collector 源码、同实例序号与拥有连接核对通过。 | 已日常切换 |
| P3.6：验证旧 GUI／collector／bridge 组合有明确降级；完成驻留 bridge 更新需要完整重开 Codex Desktop 的运行验收。 | 旧能力拒绝与升级提示有受控证据；维护者重开后，新 bridge 的真实进程、源码、许可和重开提示消失均已核对。 | 已运行核对 |

## P4：详情分页内容快照

[分页16例](../../../tests/test_content_paging.py)、[单项依赖](../../../tests/test_history_detail.py)、[两个阅读入口](../../../macos/Tests/MonitorAppTests/TimelineStoreTests.swift)、[输入阅读](../../../macos/Tests/MonitorAppTests/InputPopoverTests.swift)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P4.1：固定 v2 detail／section token、内容摘要规范、长度上限和错误状态；定义 v1 第一页的有限兼容。 | detail v2 section-prefix token、摘要、身份、版本和长度边界；v1 仅第一页。 | 实现／受控 |
| P4.2：使源身份校验、section 生成、摘要和第一页切分基于一致的内容视图。 | 一个 HistoryRead 句柄检查 header／记录摘要／来源前后身份；读取中同 inode 变化反例通过。 | 实现／受控 |
| P4.3：后续页验证原 token、身份与 code point offset；允许原快照范围内的追加前缀，不接受被改写内容。 | 追加只读原前缀长度，改写／截短不拼页；输入、结果与上下文归属均校验。 | 实现／受控 |
| P4.4：两个查看入口共用版本变化、来源消失、重新加载及旧请求失效逻辑。 | ActivityPageCursor 与 ActivityDetail 共用验证；两个入口错误／变更保留已读内容、选区／滚动和代际。 | 实现／受控 |
| P4.5：用独立 provider／独立 CLI 调用证明 token 稳定，验证大内容没有每页扫描全 JSONL。 | 独立 provider／实际 CLI、Unicode 和热页引用检查；巨大单项窄读取已测，冷扫描边界保留。 | 实现／受控 |

## P5：规模测量与资源预算

[资源预算](../resource-budgets.md)、[上限反例](../../../tests/test_resource_budgets.py)、[校正队列](../../../tests/test_history_refresh.py)、[当前测量索引](README.md)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P5.1：建立带种子和配置的合成 workload／基准工具，区分冷加载、热查询、追加和重连。 | 固定输入／种子、冷／热／追加、10／50 源及 1／10／50 流；P3 的重连和实例故障另验。 | 实现／测量 |
| P5.2：同时测 Python／Swift RSS、索引和身份条目、排队文本字节、序列化／计数／分组成本、端到端延迟。 | 原生组合同时采样 Python／Swift／子进程 RSS、队列和端到端；细分 CPU 成本单独计量，不声称同一计时范围。 | 测量有边界 |
| P5.3：发布实际测量和 `resource-budgets.md`，给出每项数值、依据、准入／回收和降级行为。 | resource-budgets 与当前 evidence 给出单位、准入、回收、未知／失败及未覆盖规模。 | 已发布 |
| P5.4：按测量落实所选 turn 索引视图、按权重淘汰／按需加载、计数任务总预算、终止身份保护等必要改进。 | 目录／turn／事实／单项／时间线用途分开，共享权重；计数与终止身份总预算已执行。 | 实现／测量 |
| P5.5：触发每个预算上限，证明计数保守降级、慢消费者受控，且旧事件不重新打开终止活动。 | 缓存、队列、计数、身份、元数据、状态帧／peer、文本和输出都有超限反例；默认身份 65536 项／8 MiB 又单独触顶。 | 实现／受控 |
| P5.6：累积自然运行记录，核对合成工具未覆盖的上游行为。 | P0 旧链 3 个完整回合、7 min 受控组合样本均不替代新版本自然／长期运行。 | 待自然记录 |

## P6：GUI 单快照投影

[图语义](../../../macos/Tests/MonitorModelTests/ProjectionTests.swift)、[性能](p6-validation.json)、[原生组合](p5-combined-native.json)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P6.1：固定现有分组语义的输入／输出样本，记录多 getter 下的实际重复次数。 | 从 c3996dc 生成的 60 组固定图样本逐项比对；P0 重复 getter 成本保留。 | 受控等价 |
| P6.2：建立只读 `SnapshotProjection`，按 scoped identity 一次计算 parent/children、roots/members/unassigned 和汇总。 | 单 SnapshotProjection，scoped parent/children、组归属与汇总共享；循环和深隐藏链保守处理。 | 实现／受控 |
| P6.3：让 store 和行复用投影，处理实例／代际、语言及 recent/freshness 时钟失效。 | store／行复用，recent 独立时钟，语言／健康刷新保留投影和阅读；迟到状态沿用实例／代际保护。 | 实现／受控 |
| P6.4：比较正常与压力规模下的计算次数、内存和主 actor 时间，验证选择和原生交互。 | 10–3000 聊天构建／getter／主 actor 分开测量；原生窗口／阅读／宽度和 50 流实际 draw 有证据。 | 测量／原生 |

## P7：剩余类型与职责收尾

[已实现边界](../implemented-architecture.md)、[导入核对](p7-boundary-audit.json)、[持久化](../../../tests/test_timeline_storage.py)

| 原实施项 | 当前证据范围 | 核对结果 |
| --- | --- | --- |
| P7.1：检查公开读接口、持久化记录和跨线程摘要，明确身份、单位、枚举及错误。 | 冻结请求／选择／摘要、持久 allowlist、单位和结构化失败明确；领域 tuple key／适配层字典按用途保留。 | 边界已核对 |
| P7.2：收敛剩余跨层字典和重复适配；删除被新路径替代的实现／字段，保留仍承担旧版本迁移职责的入口。 | 旧 QueryProcess、生产文件读路径、影子完成删除；fixture／离线／完整诊断入口明确限于边界。 | 边界已核对 |
| P7.3：在已有行为回归保护下做有理由的 Engine 协作拆分；纯搬移／格式调整独立审阅。 | 状态、历史扫描／读取、事实应用、后台交接、计数／存储分别拥有规则；保留 Engine 相互依赖的事件状态机。 | 有理由的拆分 |
| P7.4：在本章新增已实现架构／实施结果并更新公共阅读入口，保留第 17 章的基线架构；按实际验收更新本章状态。 | 已实现架构／实施记录／预算／公开 README 与第17章基线分别维护，旧 next-steps 只保留迁移入口。 | 文档已接入 |

## 命令、样本与保留限制

- 最新完整 Python discovery 为448项通过；最新 Swift 默认报告 Model 62／App 113，其中模型2个基准、App 1个基准及6个原生场景跳过，跳过不是通过。原生3个窗口检查和组合验收单独执行，已有证据沿用其当时范围。日志副本保留在私有准备目录。
- localization导出、Preview严格签名和Swift源码指纹、Git diff格式及文档／JSON检查分别有记录；这些不替代产品语义或自然运行。候选包副本已在日常runtime运行并核对；回退包仍仅准备与签名检查，未执行回退。
- 默认身份预算触顶仅测准入账本；Engine gap／UI／旧事件重放由资源反例验证。目录和回复预算不是总RSS上限；700000 phase冷读可能超过GUI期限，超大整帧明确失败，未静默缩短JSONL或截断原始详情。
- 表中“基线”“受控”“原生”“自然”各自限定结论范围；当前的 [日常切换清单](../rollout-checklist.md)和[验证计划](../validation-plan.md)继续承担未完成的运行门槛。

# 实施结果与验证边界

日期：2026-10-29，Asia/Shanghai。Monitor 基线 `61f751d25ae57e8cfe798bba73402235b96d1fca`，上游参照 `9def09afa71e1443a1028866ac22dc82aedea00e`。

状态：RH01、RH02、32 项新增回归及本轮隔离验收完成。2026-10-30 按用户要求更新并重启了日常 Monitor，详情见文末交付记录；其后已发布 [alpha.5](alpha5-release.md)。以下保留原实现阶段的身份和门槛证据，发行的最终构建与公网验收另行记录；未重启用户 Codex。

独立构建身份：`5f8572edb9bae1359a2c021a6fb096238f07ec735d9ff2385962ef86617beea4`。当前工作树的运行时模块摘要与该构建清单、源码及独立包集成记录逐项一致。

## 实际实现

| 位置 | 已实现职责 |
| --- | --- |
| [history_source.py](../../src/codex_activity_monitor/history_source.py) | 只读选择状态库中的当前 rollout；接受可选协议提示；兼容旧布局的受限发现；核对文件名、头部身份及受管路径；构造有截止边界的继承链 |
| [history_stream.py](../../src/codex_activity_monitor/history_stream.py)、[history_read.py](../../src/codex_activity_monitor/history_read.py) | 将已验证范围映射为可 seek 的读取视图，不生成合并后的 JSONL；以组合指纹、范围映射和记录摘要约束引用，正文读取核对实际来源 owner |
| [history.py](../../src/codex_activity_monitor/history.py)、[history_index.py](../../src/codex_activity_monitor/history_index.py) | 完整、目录和窄索引共用来源；切换时失效派生缓存；保留单文件增量路径；继承范围内的请求计时账本随来源重建 |
| [events.py](../../src/codex_activity_monitor/events.py)、[bridge.py](../../src/codex_activity_monitor/bridge.py)、[engine.py](../../src/codex_activity_monitor/engine.py) | 有界保留 `Thread.path`、历史模式及显式 SQLite home；只在内存中维护提示和请求代次，文件／数据库 I/O 留在历史读取线程 |
| [history_refresh.py](../../src/codex_activity_monitor/history_refresh.py)、[reconciliation.py](../../src/codex_activity_monitor/reconciliation.py) | 交接请求代次和来源代次；过滤已过时的读取结果；完整覆盖之外不作删除判断；实际已执行工具事实与当前模型历史成员关系分开处理 |
| [timeline.py](../../src/codex_activity_monitor/timeline.py)、[timeline_query.py](../../src/codex_activity_monitor/timeline_query.py) | 当前时间线、详情、翻页和所选轮均速使用同一有效历史；丢弃分支不再返回正文或继续旧分页；有效正向记录可恢复成员关系，缺祖先时不因此隐藏可证明的当前内容 |
| [agent_identity.py](../../src/codex_activity_monitor/agent_identity.py) | 复用当前来源解析器，避免独立按旧文件名寻找头部 |

普通用户聊天 fork 的继承记录先验证其实际源头部与记录 owner，再投影到请求聊天的阅读视图，turn/item 身份保留。`history_base.thread_id` 按 rollout ID 解析；引用的祖先不跟随该祖先聊天后来选中的其他分支。ordinal 与 byte 截止必须一致，引用链可以多次回退或跳回更早祖先。

被排除的已完成模型生成不再充当当前时间线／内容；当前仍在生成但尚未落盘的活动不因缺少历史记录而被关闭。实际工具执行事实仍可保留为观测元数据，不能借此读回被排除分支的正文。来源重建不会把历史记录重放进实时 delta 或官方用量入口。

没有引入新的 GUI 查询版本或计数算法。原生客户端仍通过现有 `TimelineBundle`、详情 v2 和分页 token 解码；支持源中的内容一致性继续验证，当前历史不再包含的旧内容不能仅因旧文件字节未变而继续翻页。

## 配置与预算

默认适配 `state_5.sqlite` 的只读、按 thread 查询；支持顶层 `sqlite_home` 和新桥接记录的显式 `CODEX_SQLITE_HOME`，读取能看到已提交 WAL 更新。配置中存在无法确认是否生效的 profile 数据库覆盖时，不猜默认库；有新鲜协议路径时只提供相应的部分覆盖，否则明确不可用。未实现一套完整的 Codex CLI／profile／项目配置求值器。

| 约束 | 当前值与行为 |
| --- | --- |
| 继承链长度 | 最多 64 个 rollout，循环或超限拒绝 |
| 来源解析时间检查 | 1 秒检查期限，在解析步骤之间检查；不是对操作系统 I/O 的硬中断承诺 |
| 文件发现 | 检查预算为 20,000 个文件名或 0.25 秒；不按修改时间选择分支 |
| 文件系统候选缓存 | 10 秒上限，并核对相关目录修改时间及候选存在性；状态库当前指针在使用缓存前仍读取 |
| 头部与截止记录 | 头部最多 1 MiB；截止记录向后查找最多 8 MiB，边界验证结果缓存有界 |
| 历史派生缓存 | 沿用 64 MiB 权重和 1,024 份窄选择预算，将来源描述计入索引权重 |
| 解析提示及辅助表 | 路径提示／发现最多 512 项，边界／代次等辅助表有独立数量上限；最后来源表只保存指纹，不额外保留整条历史链 |

数据库只读打开并及时关闭，不创建或迁移 Codex 表，不修改 journal 配置。受管目录之外的路径、越界引用、身份冲突、来源变化和无法证明的范围返回不可用或部分覆盖。

## 回归和集成证据

最终验证汇总见 [validation.json](evidence/validation.json)。该文件只保留构建身份、源码摘要、合成检查结果和发行产物摘要，不包含真实会话、原始聊天或本机绝对路径。构建产物和完整运行日志保留在忽略的 `.local/` 中。

| 检查 | 当前证据 |
| --- | --- |
| 完整 Python 回归 | 578 项通过；其中新增 rollout 回归 32 项 |
| 原生客户端和活动查看器 | 源码、独立后端各运行一次 `isolatedRolloutHistoryBackendClient` 并通过；真实 `SystemTimelineClient` → CLI／collector → `ActivityViewerStore` 验证本轮正文、保留前缀、均速、丢弃轮次及 fork 内容 |
| 独立 App／DMG | 签名、复制推出、源码／网络拒绝、合成切换及临时原生 GUI 验收通过；打包后同一历史检查全部通过 |
| 本地化与格式 | 使用现有中英文状态，无新增文案键；导出一致性、编译检查和 `git diff --check` 通过 |

32 项新增回归见 [test_rollout_history.py](../../tests/test_rollout_history.py)。现有文件读取量探针改为比较规范路径，避免 macOS `/var` 别名导致探针未测到真实读取；相邻历史、分页、均速、校正、资源预算和桥接用例继续纳入完整回归。

| 原计划门槛 | 验证入口 |
| --- | --- |
| A01–A03：旧布局、当前指针、预热与重建 | 新增当前选择、warm detail、all warm projections、optional hint、WAL 用例及已有 history 用例 |
| A04–A05：权威路径不可用、配置与数据库失败 | missing authoritative、completed live fallback、custom SQLite directory、busy/schema、unresolved profile 用例 |
| A06–A08：有效前缀、连续回退、fork 与截止 | retained prefix、multiple reverts、source owner、invalid cutoffs、实际集成 fixture |
| A09：非法／缺失／超限链及路径边界 | cross-scope symlink、missing ancestor、cycle/length、独立时间预算用例 |
| A10–A11：晚到与旧分页 | source epoch、request epoch、渲染中切换指针、旧提示、discarded paging 用例 |
| A12：所选轮均速 | 有效前缀／当前后缀独立期望、warm ledger、排除后缀 usage、部分覆盖用例 |
| A13：实时保护与无关聊天 | 双 Engine 同序列对照；真实分词与相同计数成本下，两个聊天的 token 窗口投影逐项相等；源码和独立包集成期间 collector 保持健康 |
| A14：暂缓形态 | 压缩祖先、subagent 起始 ordinal、attached 特殊边界仅做保守降级与不新增错误归属的检查 |
| A15：缓存与预算 | 稳定继承详情不重扫目录；长度／时间上限、缓存重建、已有权重与工作预算用例 |

可复验入口：

```sh
PYTHONPATH=src .local/venv/bin/python -m unittest discover -s tests -p 'test_rollout_history.py'
PYTHONPATH=src .local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src .local/venv/bin/python tests/acceptance/verify_rollout_history.py \
  --output .local/rollout-integration.json --native-client
```

隔离 collector 验收使用一次性 home、状态库、假原版 CLI 和合成 stdio 事件，经过真实桥接、collector、查询 socket 和 CLI；不会调用模型服务或接入日常配置。`--backend` 可改用独立后端。可选原生检查显式启动 `isolatedRolloutHistoryBackendClient`，并要求执行完成标记，默认 Swift 测试不会访问此类 runtime。该检查验证原生客户端与查看器 store 的真实数据链路，不等于逐屏视觉或长期使用验收。

DMG 验收沿用 [packaging/verify_app.py](../../packaging/verify_app.py)，新增 [verify_rollout_history.py](../../packaging/verify_rollout_history.py) 场景：预热旧详情和均速，切换当前路径，读取保留前缀及新轮次，阻断已丢弃分页，继续追加一轮，验证普通用户 fork 的来源归属，并核对读取前后源文件及状态表未被修改。

## 暂缓与交付状态

RH03 压缩解码、RH04 子智能体／attached 完整继承语义、RH05 旧 rollback 重放、RH06 类型映射继续暂缓。RH04 本轮仅增加边界保护，不能据此声称已完整支持所有子智能体历史。未知配置、缺祖先或暂不支持形态不会被解释为完整有效历史。

原实现阶段的隔离验收产物使用当时仓库的版本标识，没有将其作为新版本发布；后续发行递增到 alpha.5／构建 5，见[发布结果](alpha5-release.md)。源码工作树、独立包和日常运行实例的状态分开记录；上述隔离检查未替换日常 App 或重启 Codex bridge。旧 bridge 缺少新增可选提示时仍可使用默认状态库和旧布局回退；需要新增配置提示的环境须在任务结束后重启 Codex 才能换用新 bridge。

## 2026-10-30：更新并重启日常 Monitor

用户随后明确要求重启正在运行的 Monitor，已将 `/Applications/Codex Activity Monitor.app` 更新为上述已验收构建。先准备安装副本并通过签名和后端自检，再通过原生应用终止请求正常退出 GUI；确认旧 GUI 与 collector 均退出后，原子替换 App 并重新打开。旧 App 和必要管理记录的回退副本保留在忽略的 `.local/daily-update/` 中。

为保留 Codex 正在持有的桥接，本地安装将新后端放在 `MonitorBackend-5f8572edb9ba.app`，保留原有两个后端目录及其内容摘要；更新外层配置后重新签名并通过深度严格验证。这是日常本地安装布局调整，原始已验收 App／DMG 未改动。

更新后只有一个日常 GUI，新 collector 从新后端路径启动；其实际报告的 41 项实现模块摘要与当前源码一致。原生 GUI 诊断确认构建身份正确、传输新鲜且已连接；collector、bridge、parsing、tokenizer、history、metadata、timeline_query、checkpoint 和 logging 均为 healthy。接入记录 ID、原环境备份、legacy agent 备份和用户偏好逐项保持一致，既有时间线数据库仍可用。

Codex 及其旧 bridge 未结束，旧 bridge 已重新连接到新 collector；接入状态明确报告需要重启 Codex 才能完成桥接版本切换。由于本轮活动跨越 collector 重启，实际记录了 observation gap，GUI 可以显示总速度暂不可用；未把中断轮当作完整速度证据，也未将这次健康检查表述为下一自然轮次或长期使用验收。完整私有交付记录保留在 `.local/daily-update/`，不提交真实运行记录或会话数据。

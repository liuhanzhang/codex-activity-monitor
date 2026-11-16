# 全局代码审查报告（后端与客户端）

审查日期：2026-11-16，代码基线：`704df5a`。覆盖 `src/codex_activity_monitor/`（55 个 Python 模块）、`macos/Sources/`（41 个 Swift 源文件）、构建与打包脚本（`bin/`、`packaging/`）及相关回归测试。

---

## 一、整体架构优势与应保持的设计

在评估重构范围前，以下经过前序迭代验证的核心设计应保持不变：

| 维度 | 现有设计 | 评价 |
| --- | --- | --- |
| 进程与通道隔离 | `events.sock`（采集入站）、`state.sock`（GUI 状态流与租约）、`tl.sock`（只读时间线与详情）三通道分离 | 避免慢查询、慢磁盘或 GUI 卡顿反压实时采集与原版 CLI 转发 |
| 数据诚实性（Provenance） | 严格区分 `unknown` 与 `0`、区分 `receipt`（接收时钟）与 `producer` / `history`（源端时钟），显式记录 `observation_gap` | 防止断线重连或中途挂载时伪造连续时间线或虚假速率 |
| 资源与内存预算 | `IdentityBudget`、`HistoryProvider`（64 MiB 权重）、`HistoryRefresh`（字节上限）、`TokenCounter`（独立子进程 + 3s 超时） | 有效防止长会话或异常输入撑爆常驻进程 RSS |
| 客户端投影与分页契约 | 解码期一次性构建不可变 [`SnapshotProjection`](../../macos/Sources/MonitorModel/SnapshotProjection.swift)；详情分页按 Python Unicode 标量偏移与 `page_token` 校验 | UI 渲染帧率与状态一致性有保障 |

---

## 二、问题与架构债务汇总（`MR01`–`MR08`）

优先级说明：**P1** 为影响现有功能正确性、状态真实性或线程可用性的缺陷/隐患；**P2** 为影响多 Agent 扩展性、资源统计完整性或代码可维护性的结构性问题。

| 编号 | 优先级 | 类别 | 问题概述 | 关键位置 |
| --- | --- | --- | --- | --- |
| `MR01` | P1 | 功能缺陷 | 关闭 Codex、仅开启 Antigravity 时，GUI「运行状态」误判为 `disabled` 并隐藏全部后端组件健康状态；后端同时误报 `bridge` 等待连接 | [`IntegrationPresentation.swift`](../../macos/Sources/MonitorApp/IntegrationPresentation.swift#L59-L71) · [`diagnostic_health.py`](../../src/codex_activity_monitor/diagnostic_health.py#L38-L48) |
| `MR02` | P1 | 并发隐患 | `TimelineQuery._bundle` 在持有 `history.lock` 期间同步调用 `read_antigravity_steps` RPC，慢响应会阻塞后台 `HistoryRefresh` 与其他查询线程 | [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L64-L68) · [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L230-L245) |
| `MR03` | P2 | 资源预算与隔离 | `AntigravityHistory` 未纳入 `HistoryProvider` 的 64 MiB 预算、`diagnostics()` 统计和 `_view()` 深拷贝隔离；`AntigravityProjection` 绕过了 `IdentityBudget` | [`history.py`](../../src/codex_activity_monitor/history.py#L48) · [`antigravity_history.py`](../../src/codex_activity_monitor/antigravity_history.py#L224-L239) · [`antigravity_projection.py`](../../src/codex_activity_monitor/antigravity_projection.py#L39-L44) |
| `MR04` | P2 | 封装穿透 | `AntigravityProjection` 直接调用 `Engine` 与 `TimelineBook` 的私有方法（`_thread`、`_activity`、`_put`、`_save`、`_relation`）及内部表 | [`antigravity_projection.py`](../../src/codex_activity_monitor/antigravity_projection.py#L42) · [`antigravity_projection.py`](../../src/codex_activity_monitor/antigravity_projection.py#L180-L295) |
| `MR05` | P2 | 多源耦合 | 核心层（`timeline.py`、`history.py`、`timeline_query.py`、`engine.py`）散落大量 `source == 'antigravity'` 分支，缺少统一的 Adapter 与策略特征抽象 | [`timeline.py`](../../src/codex_activity_monitor/timeline.py#L154-L226) · [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L230-L335) · [`engine.py`](../../src/codex_activity_monitor/engine.py#L929) |
| `MR06` | P2 | 协议与控制面 | 开关文件位置不统一（`data_root/integration.enabled` vs `run/antigravity.enabled`）；`Snapshot` 与 `ManagementState` 顶层按 Agent 名称硬编码字段 | [`app_control.py`](../../src/codex_activity_monitor/app_control.py#L281-L319) · [`Snapshot.swift`](../../macos/Sources/MonitorModel/Snapshot.swift#L128) · [`Management.swift`](../../macos/Sources/MonitorModel/Management.swift#L39) |
| `MR07` | P2 | 客户端与分词细节 | `SnapshotProjection` 与 `ChatRowView` 存在“非 Antigravity 即 Codex”的二元硬编码；`ChatRowView` 用 `source` 而非 `generation_estimated` 判断 `≈`；Gemma 4 模型匹配白名单过窄 | [`SnapshotProjection.swift`](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L151) · [`ChatRowView.swift`](../../macos/Sources/MonitorApp/ChatRowView.swift#L17) · [`antigravity_tokenizer.py`](../../src/codex_activity_monitor/antigravity_tokenizer.py#L17) |
| `MR08` | P2 | 目录层次结构 | Python 55 个模块、Swift `MonitorApp` 33 个文件全部单层平铺，核心引擎与具体 Agent 实现混杂；构建与源码指纹脚本硬编码了单层路径假设 | [`runtime.py`](../../src/codex_activity_monitor/runtime.py#L28-L37) · [`build_app.py`](../../packaging/build_app.py#L94-L105) · [`export-localizations`](../../bin/export-localizations#L64) |

---

## 三、重点问题详细分析

### 1. `MR01`：单开 Antigravity 时「运行状态」页面的状态误判

- **触发条件**：用户在「设置 → 通用 → Agent 接入」中关闭 Codex（或从未开启 Codex，使 `management.configured == false`、`integration_status.confirmedEnabled == false`），同时开启 Antigravity（`management.antigravity_enabled == true`）。
- **代码定位**：
  - 客户端 [`IntegrationPresentation.swift`](../../macos/Sources/MonitorApp/IntegrationPresentation.swift#L59-L71)：
    ```swift
    let disabled = management?.waitingForCleanup == true || management?.integration_status?.confirmedEnabled == false ||
        (management != nil && management?.configured == false && management?.state != "error")
    var values: [ComponentHealth] = [.init(component: "connection", state: disabled ? "disabled" : busy || management == nil ? "unknown" : fresh ? "healthy" : "unavailable",
                                          code: disabled ? "stop_completed" : fresh ? "state_connected" : "state_disconnected")]
    if !disabled {
        if fresh {
            values += snapshot?.diagnostics?.supportedComponents ?? []
    ```
    这里的 `disabled` 只检查了 Codex 的配置状态（`configured` 和 `integration_status.confirmedEnabled`），完全忽略了 `management.observationEnabled`（即 `configured || antigravity_enabled == true`）。
  - 后端 [`diagnostic_health.py`](../../src/codex_activity_monitor/diagnostic_health.py#L38-L48)：
    `HealthMonitor.snapshot` 始终检查 Codex `backends` 并输出 `add('bridge', 'unknown', 'waiting_for_connection')`，即使 `observe_codex` 已被禁用。
- **实际影响**：单开 Antigravity 时，「设置 → 运行状态」页面的连接状态显示为“已暂停采集”，且 `collector`、`antigravity`、`tokenizer`、`history` 等所有后端组件的健康卡片全部消失。

### 2. `MR02`：`TimelineQuery._bundle` 持锁执行同步 RPC

- **触发条件**：用户在面板或活动查看器中查看已完成的 Antigravity 会话回合（特别是包含子 Agent 的回合），触发 `timeline` 查询。
- **代码定位**：
  - [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L469-L476) 中，`detail` 请求特意在进入 `with self.history.lock:` 前调用了 `self._antigravity_detail(request)`。
  - 但 `bundle` 请求在 [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L65) 全程持有 `with self.history.lock:`，并在 [`L230-L245`](../../src/codex_activity_monitor/timeline_query.py#L230-L245) 中对每个 `(owner, owner_turn)` 调用 `index.trajectory_turn(owner_turn, self.read_antigravity_steps)`。
  - `read_antigravity_steps` 底层通过 [`LocalRPC.call('GetCascadeTrajectorySteps')`](../../src/codex_activity_monitor/antigravity_transport.py#L161) 发起同步 HTTPS 请求（单次 timeout 为 4 秒）。
- **实际影响**：当 Language Server 负载较高或连接切换时，单次时间线查询可能持有 `self.history.lock` 数秒，阻塞 Collector 的 `HistoryRefresh` 后台线程（超过 5 秒将触发 `history_slow` 告警）并阻塞其他并发查询。

### 3. `MR03` & `MR04`：预算绕过与私有状态穿透

- **历史缓存脱离预算与深拷贝隔离**：
  - [`HistoryProvider`](../../src/codex_activity_monitor/history.py) 为 Codex 维护了基于 `retained_bytes` 的 64 MiB 权重缓存（`self.indices` / `self.weights`），每次返回前通过 `_view()` 拷贝隔离可变字典，并通过 `diagnostics()` 向快照输出缓存占用与淘汰次数。
  - 而 `HistoryProvider` 中的 `self.antigravity = AntigravityHistory()` 维护了独立的 8 会话 `OrderedDict`（[`antigravity_history.py`](../../src/codex_activity_monitor/antigravity_history.py#L237)），既不计入 `HistoryProvider._stats`，也在 `index()`、`detail_view()`、`timeline_view()` 中直接将可变的 `TranscriptIndex` 暴露给调用方（例如 [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L141) `phases.setdefault(p['id'], p)` 直接引用了 `TranscriptIndex.phases` 中的字典）。
- **`Engine` 与 `TimelineBook` 私有成员穿透**：
  - [`AntigravityProjection`](../../src/codex_activity_monitor/antigravity_projection.py) 直接调用 `engine._thread`、`engine._activity`、`book._put`、`book._save`、`book._relation`，并直接操作 `engine.backends`、`engine.streams`、`book.live`。
  - 由于绕过了 `Engine.ingest`，Antigravity 会话与步骤不会经过 `Engine.identity_budget.reserve()`，也无法与 Codex 共享统一的生命周期辅助逻辑。

### 4. `MR05`–`MR07`：跨层散落的 Agent 特判（阻碍新增第 3 个 Agent）

目前如果新增第 3 个 Agent（例如 `claude_code`），需要修改以下所有核心文件：
1. **采集主循环（[`collector.py`](../../src/codex_activity_monitor/collector.py)）**：硬编码了 `codex_enabled` 与 `antigravity_enabled` 的启停、快照 `snapshot['antigravity']` 注入、历史恢复过滤与 `history.drain()` 分发。
2. **时间线与合并规则（[`timeline.py`](../../src/codex_activity_monitor/timeline.py)）**：
   - `merge_phase` ([L154-L196](../../src/codex_activity_monitor/timeline.py#L154-L196))：用 `result.get('source') == 'antigravity'` 翻转 `history` / `producer` / `receipt` 的优先级，并处理非零 `exit_code`。
   - `merge_history_phase` ([L213-L226](../../src/codex_activity_monitor/timeline.py#L213-L226))：针对 `GENERIC` 步骤与 `source == 'antigravity'` 特判保留更具体的实时工具名与退出码。
   - `settled_model_items` ([L116](../../src/codex_activity_monitor/timeline.py#L116)) 与 `prune` ([L587-L599](../../src/codex_activity_monitor/timeline.py#L587-L599))：针对 `source == 'antigravity'` 特判 `-input` 阶段与最多 4096 条跨回合 `relations` 保留。
3. **Engine 淘汰策略（[`engine.py`](../../src/codex_activity_monitor/engine.py#L929)）**：`retained_directory = self.backends.get(thread.backend, {}).get('source') == 'antigravity'` 豁免 600 秒空闲清理。
4. **分词选择（[`metrics.py`](../../src/codex_activity_monitor/metrics.py#L67-L75) & [`antigravity_tokenizer.py`](../../src/codex_activity_monitor/antigravity_tokenizer.py#L17)）**：
   - `TextStream.set_model` 按 `self.source == 'antigravity'` 分支；
   - `antigravity_tokenizer.py` 的 `MODELS = ('gemini-4-argon', 'gemini-3.5-flash', 'gemini-3.1-flash-lite', 'gemini-3.1-pro')` 仅匹配 4 个前缀，实测出现的 `Gemini 4 Hydrogen`（`gemini-4-hydrogen`）等同系列模型会回退到 `o200k_base`。
5. **Swift 客户端模型与视图**：
   - [`SnapshotProjection.swift`](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L151) 与 [L170-L177](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L170-L177)：硬编码 `$0.chat.source == "antigravity"` 决定是否将 `idle` 会话纳入 `recentRoots` 及豁免 600 秒过期。
   - [`ChatRowView.swift`](../../macos/Sources/MonitorApp/ChatRowView.swift#L17)：硬编码 `chat.source == "antigravity" ? "≈" : ""`（未复用 `generation_estimated` 语义，[`GroupSpeed`](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L23-L45) 也未聚合 `generation_estimated`）。
   - [`ChatRowView.swift`](../../macos/Sources/MonitorApp/ChatRowView.swift#L57-L59)：硬编码 `chat.source == "antigravity" ? "Antigravity" : "Codex"`。
   - [`SettingsWindow.swift`](../../macos/Sources/MonitorApp/SettingsWindow.swift#L198)：卸载确认弹窗文案仅提示退出 Codex。

### 5. `MR08`：代码目录单层平铺与物理结构分析

目前 `src/codex_activity_monitor/` 下共有 **55 个 `.py` 文件**，`macos/Sources/MonitorApp/` 下共有 **33 个 `.swift` 文件**，全部平铺在同一级目录下。

#### 为什么当前平铺结构会带来维护阻力？
在项目早期（仅支持 Codex 单一来源、约 20–30 个文件时），平铺目录有助于快速跳转和简单导入。但随着工程硬化（第 18–27 章）和多 Agent 接入（第 30 章），单目录下已经混杂了 **6 个不同职责与抽象层级**：

1. **核心状态机与计量层（Core Domain）**：`state.py`、`engine.py`、`timeline.py`、`usage.py`、`metrics.py`、`request_rate.py`、`overview.py`、`menu_presentation.py`、`agent_relations.py`、`agent_presentation.py`、`agent_identity.py`、`tool_labels.py`
2. **通用历史与查询门面（History & Query Facade）**：`history.py`、`history_contract.py`、`history_refresh.py`、`timeline_storage.py`、`timeline_query.py`、`content_version.py`、`query_request.py`、`query_response.py`
3. **Codex 专属接入与历史实现（Codex Agent）**：`bridge.py`、`observer_permit.py`、`events.py`、`progress_state.py`、`integration.py`、`integration_status.py`、`history_source.py`、`history_stream.py`、`history_index.py`、`history_read.py`、`history_content.py`、`reconciliation.py`
4. **Antigravity 专属接入与历史实现（Antigravity Agent）**：`antigravity_transport.py`、`antigravity_source.py`、`antigravity_records.py`、`antigravity_projection.py`、`antigravity_history.py`、`antigravity_tokenizer.py`
5. **运行时、IPC 传输与控制面（Runtime, IPC & Control）**：`collector.py`、`publication.py`、`state_transport.py`、`state_client.py`、`observation_lease.py`、`app_session.py`、`tokenizer_process.py`、`app_control.py`、`cli.py`、`display.py`、`runtime.py`、`execution.py`、`owned_files.py`、`test_profile.py`、`diagnostic_log.py`、`diagnostic_health.py`、`resource_budget.py`、`identity_budget.py`、`json_encoding.py`
6. **国际化文案（i18n）**：`i18n.py`、`messages_zh_cn.py`、`messages_en.py`、`messages_control.py`、`messages_detail.py`、`messages_diagnostics.py`

此外，Codex 专属模块大多使用通用命名（如 `events.py`、`progress_state.py`、`history_index.py`、`reconciliation.py`），而 Antigravity 模块使用前缀命名（`antigravity_*.py`），在同一个文件夹下平铺时，新贡献者很难一眼分辨哪些文件是“多 Agent 共享的核心设施”，哪些是“仅服务于 Codex 的协议细节”。

#### 调整物理目录时必须注意的 3 处硬编码约束
代码库中有 3 处逻辑直接假设了源码文件在单层目录下平铺，若引入子目录/子包必须同步更新：
1. [`runtime.py: source_identity`](../../src/codex_activity_monitor/runtime.py#L28-L37)：非打包模式下使用 `directory / (name + '.py')` 计算模块 SHA-256 指纹。
2. [`packaging/build_app.py`](../../packaging/build_app.py#L94-L105)：使用非递归的 `ROOT.glob('src/codex_activity_monitor/*.py')` 计算 `build_id` 和 `manifest['modules']`。
3. [`bin/export-localizations`](../../bin/export-localizations#L64)：使用非递归的 `root.glob('*.swift')` 扫描本地化 Key。

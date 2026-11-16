# 修复与重构方案

方案日期：2026-11-16，基于 [代码审查报告](review.md) 中的 `MR01`–`MR08`。目标是在不破坏现有进程隔离、数据诚实性与资源预算的前提下，修复已确认缺陷，收敛“核心引擎 ↔ Agent 适配器”边界，并理顺代码目录层次。

---

## 一、“逻辑收口”与“物理目录分层”的关系

此前讨论中提到的“平铺收口重构”包含两个互补维度：

1. **逻辑边界收口（Logical Convergence）**：消除散落在核心模块中的 `if source == 'antigravity'` 平铺条件分支，以及适配器对 `Engine` / `TimelineBook` 私有方法的直接穿透调用。将不同 Agent 的差异收敛为**标准化的公共写入接口 + 声明式数据源特征（Source Traits）+ 统一的历史/实时 Adapter 协议**。
2. **物理目录分层（Package Hierarchy）**：解决 `src/codex_activity_monitor/` 下 55 个 `.py` 文件和 `macos/Sources/MonitorApp/` 下 33 个 `.swift` 文件单层堆叠的问题，让文件系统结构直接反映“通用核心层 vs 具体 Agent 接入层”的架构边界。

---

## 二、分阶段设计与修复方案

### 阶段 1：高优缺陷与并发/预算修复（`MR01`–`MR03`、`MR07`）

1. **修复运行状态页与健康检查的双源协同（`MR01`）**
   - 客户端 [`IntegrationPresentation.swift`](../../macos/Sources/MonitorApp/IntegrationPresentation.swift#L59)：将 `disabled` 判定改为基于 `management?.waitingForCleanup == true || (management != nil && !management!.observationEnabled && management?.state != "error")`，确保仅开启 Antigravity 时 `connection` 与后端组件健康状态正常展示。
   - 后端 [`diagnostic_health.py`](../../src/codex_activity_monitor/diagnostic_health.py#L38-L48)：`HealthMonitor.snapshot` 接收 `observe_codex` 状态；当 Codex 未启用且无连接中的 bridge 时，不输出误导性的 `bridge: waiting_for_connection`（或仅在开启的 Agent 范围内报告对应组件）。
2. **将 `TimelineQuery._bundle` 的轨迹 RPC 移出 `history.lock`（`MR02`）**
   - 在 [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py#L230-L245) 中，先在锁内读取索引并确定需补齐精确时点的 `(owner, owner_turn, first_step, last_step)`（命中 `trajectory_snapshot` 缓存的直接复用）；
   - 若存在未缓存的已完成回合，先释放 `self.history.lock` 执行只读 RPC 读取，再重新持锁（或对不可变快照合并）生成最终 bundle，避免慢 RPC 阻塞 `HistoryRefresh`。
3. **将 `AntigravityHistory` 纳入 `HistoryProvider` 统一预算与视图隔离（`MR03`）**
   - 让 `TranscriptIndex` 接入 `HistoryProvider._budget()`（64 MiB 权重统计与淘汰）和 `HistoryProvider.diagnostics()`；
   - `HistoryProvider` 对外返回 `TranscriptIndex` 数据时统一经过 `_view()` 脱离可变内部字典。
4. **完善分词匹配与客户端细节（`MR07`）**
   - [`antigravity_tokenizer.py`](../../src/codex_activity_monitor/antigravity_tokenizer.py#L17)：将模型匹配从 4 个固定前缀扩展为覆盖 `gemini-` / `gemma-` 系列（如 `gemini-4-hydrogen` 等），并统一在分词注册表中按 `(source, model)` 路由。
   - [`SnapshotProjection.swift`](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L23-L45) 与 [`ChatRowView.swift`](../../macos/Sources/MonitorApp/ChatRowView.swift#L17)：在 `GroupSpeed` 中聚合 `isEstimated`（`chat.generation_estimated == true`），`ChatRowView` 根据 `groupSpeed.isEstimated` 展示 `≈`，不再硬编码 `chat.source == "antigravity"`。
   - 更新卸载弹窗文案以涵盖已接入的桌面端应用。

---

### 阶段 2：逻辑收口——核心引擎与 Agent 适配器解耦（`MR04`–`MR06`）

#### 1. 声明式数据源特征（`SourceTraits`）替代 `if source == 'antigravity'`
定义轻量、不可变的 `SourceTraits`（或阶段级元数据标记），集中描述各 Agent 的客观协议差异：

| 特征字段 | Codex 默认值 | Antigravity 值 | 替代的硬编码位置 |
| --- | --- | --- | --- |
| `provenance_ranks` | `{'history': 3, 'producer': 2, 'receipt': 1}` | 模型：`{'producer': 3, 'receipt': 2, 'history': 1}`；工具：`{'producer': 3, 'history': 2, 'receipt': 1}` | [`timeline.py: merge_phase`](../../src/codex_activity_monitor/timeline.py#L162-L167) |
| `retains_idle_directory` | `False`（空闲满 600s 淘汰） | `True`（跟随源端会话目录保留） | [`engine.py: tick`](../../src/codex_activity_monitor/engine.py#L929) · [`SnapshotProjection.swift`](../../macos/Sources/MonitorModel/SnapshotProjection.swift#L151) |
| `persistent_relations` | `False`（随 turn 淘汰，JSONL 含父子关系） | `True`（子 JSONL 无父 turn，侧车保留最近 4096 条） | [`timeline.py: prune`](../../src/codex_activity_monitor/timeline.py#L587-L599) |
| `synthesizes_tool_input_phase` | `False`（独立 `function_call` item） | `True`（由模型步骤生成 `-input` 阶段） | [`timeline.py: settled_model_items`](../../src/codex_activity_monitor/timeline.py#L116-L117) |

- 对于客户端 `SnapshotProjection.swift`，由后端在 thread 快照中输出显式布尔语义（或统一由后端控制目录可见性），使 Swift 侧不再根据 `chat.source == "antigravity"` 判断过期与 `recentRoots` 准入。

#### 2. 收敛 `Engine` 与 `TimelineBook` 的公共写入接口（消除私有方法穿透）
在 `Engine` 与 `TimelineBook` 上提供面向 Adapter 的正式公共方法：
- `Engine`：`register_backend(...)`、`upsert_thread(...)`、`upsert_activity(...)`、`end_activity(...)`、`update_snapshot_stream(...)`、`mark_gap(...)`、`remove_thread(...)`。所有入口内部统一执行 `self.identity_budget.reserve(...)`。
- `TimelineBook`：`record_turn(...)`、`record_phase(...)`、`record_relation(...)`、`set_live_content(...)`、`record_turn_request(...)`。
- `AntigravityProjection` 改为完全通过上述公共接口更新状态，不再访问 `engine._thread`、`engine._activity`、`book._put`、`book._save` 等私有成员。

#### 3. 抽象统一的 `HistoryAdapter` 与锁外远程补齐接口
在 `HistoryProvider` 与 `TimelineQuery` 之间建立统一的 Adapter 契约：
- `matches_scope(scope) -> bool`：判断 `scope` 归属的 Agent 类型。
- `load_index(scope, thread, directory_only=False)`：加载或刷新索引，统一接入 `HistoryProvider` 的权重预算与 `_view()` 隔离。
- `prepare_turn_supplement(scope, thread, turn, index)` / `prepare_detail_supplement(request, phase, index)`：可选的**锁外**补齐钩子（供 Antigravity 执行 `GetCascadeTrajectorySteps` RPC，Codex 默认为 no-op），彻底消除 `TimelineQuery` 中的 `_antigravity_detail` 和 `if antigravity:` 分支。

#### 4. 控制面与客户端 Agent 元数据表驱动化
- 在 `ChatRowView.swift` 等视图中，提取统一的 `AgentSource` 展示映射（支持 `"codex" -> "Codex"`、`"antigravity" -> "Antigravity"` 及未来未知 source 的回退展示），消除三元表达式硬编码。
- 控制面与快照协议见下方“待决策问题 2”。

---

### 阶段 3：物理目录分层方案（`MR08`）

针对 `src/codex_activity_monitor/` 下 55 个文件平铺的问题，提供两种演进力度供选择（详见下方“待决策问题 1”）：

#### 方案 A：Agent 子包分层 + 核心层保持平铺（推荐，改动最稳健）
保持通用核心模块在顶层（避免全仓大规模移动文件），将特定于 Agent 的实现迁入 `agents/` 子包：

```text
src/codex_activity_monitor/
├── agents/
│   ├── __init__.py          # Agent 适配器协议、SourceTraits、注册表
│   ├── codex/               # Codex 专属实现（bridge, events, progress_state, integration*, history_*, reconciliation）
│   └── antigravity/         # Antigravity 专属实现（transport, source, records, projection, history, tokenizer）
├── engine.py                # 通用核心状态机
├── state.py                 # 领域模型 (Thread, Activity)
├── timeline.py              # 时间线相位模型与合并
├── timeline_query.py        # 统一时间线与详情查询服务
├── timeline_storage.py      # SQLite 侧车持久化
├── history.py               # 统一历史缓存门面 (HistoryProvider)
├── history_refresh.py       # 后台历史刷新调度
├── metrics.py / usage.py / request_rate.py / overview.py / menu_presentation.py
├── collector.py / app_control.py / cli.py / runtime.py / ...
└── i18n.py / messages_*.py
```

#### 方案 B：全量按职责分层为 6 个子包（最彻底，但文件移动面大）
将全部 55 个文件按职责拆入 `core/`、`history/`、`ipc/`、`control/`、`i18n/`、`agents/{codex,antigravity}/`，顶层仅保留 `cli.py` 与兼容性重导出。

#### 构建与源码指纹同步修改
无论采用方案 A 还是方案 B，均同步更新：
1. [`runtime.py: source_identity`](../../src/codex_activity_monitor/runtime.py#L28-L37)：支持相对路径模块名（如 `agents/antigravity/source`）或自动递归扫描包内 `.py` 文件。
2. [`packaging/build_app.py`](../../packaging/build_app.py#L94-L105)：将 `glob('src/codex_activity_monitor/*.py')` 升级为 `rglob('*.py')`（排除 `__pycache__`），使用相对于包根目录的模块标识写入 `build-manifest.json`。
3. [`bin/export-localizations`](../../bin/export-localizations#L64)：将 `root.glob('*.swift')` 升级为 `root.rglob('*.swift')`。

---

## 三、第一阶段实施与验收记录（2026-11-16）

第一阶段已完成 `MR01`、`MR02`、`MR03`、`MR07` 的代码修复与回归测试补充：

| 编号 | 修改内容 | 回归测试与验证 |
| --- | --- | --- |
| `MR01` | [`IntegrationPresentation.swift`](../../macos/Sources/MonitorApp/IntegrationPresentation.swift) 在 `antigravity_enabled == true && purge != true` 时不再将诊断状态置为 `disabled`；[`diagnostic_health.py`](../../src/codex_activity_monitor/diagnostic_health.py) 与 [`collector.py`](../../src/codex_activity_monitor/collector.py) 在 `observe_codex=False` 且无连接 bridge 时跳过 `bridge: waiting_for_connection` | `DiagnosticsTests.antigravityOnlyObservationShowsHealthyConnectionAndBackendComponents` · `DiagnosticLogTests.test_antigravity_only_observation_omits_idle_codex_bridge_component` |
| `MR02` | [`timeline_query.py`](../../src/codex_activity_monitor/timeline_query.py) 与 [`antigravity_history.py`](../../src/codex_activity_monitor/antigravity_history.py) 先在锁内识别未缓存的轨迹步骤范围，释放 `history.lock` 后调用 `read_antigravity_steps`，再持锁合并并缓存 `trajectory_snapshot` | `AntigravityProjectionTests.test_bundle_trajectory_rpc_does_not_hold_history_lock` |
| `MR03` | [`history.py`](../../src/codex_activity_monitor/history.py) 将 `TranscriptIndex` 纳入 `self.indices`、`self._budget()`（64 MiB 权重）与 `_view()` 深拷贝隔离，移除独立的无预算 `AntigravityHistory` 字典；[`engine.py`](../../src/codex_activity_monitor/engine.py) 提取 `admit_identities` 并在 [`antigravity_projection.py`](../../src/codex_activity_monitor/antigravity_projection.py) 统一执行 `IdentityBudget` 准入与 `retire` | `AntigravityProjectionTests.test_antigravity_history_is_detached_and_accounted_in_cache_budget` · `AntigravityProjectionTests.test_identity_budget_exhaustion_stops_antigravity_admission_and_marks_gap` |
| `MR07` | [`antigravity_tokenizer.py`](../../src/codex_activity_monitor/antigravity_tokenizer.py) 扩展匹配 `('gemini-', 'gemma-')` 模型族；[`Snapshot.swift`](../../macos/Sources/MonitorModel/Snapshot.swift)、[`SnapshotProjection.swift`](../../macos/Sources/MonitorModel/SnapshotProjection.swift)、[`ChatRowView.swift`](../../macos/Sources/MonitorApp/ChatRowView.swift) 通过 `GroupSpeed.isApproximate`、`retainsIdleHistory`、`sourceDisplayName` 解耦视图层硬编码；更新卸载弹窗双语文案 | `AntigravityProjectionTests.test_gemini_and_gemma_model_families_select_gemma4_when_cached` · `PanelUXTests.groupedSpeedMarksApproximateRatesFromSubagentsAndResolvesSourceDisplayName` |

验证结果：
- `.local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'`：**615 项测试全部通过**。
- `.local/venv/bin/python bin/export-localizations --check`：**通过**。
- `swift test --package-path macos`：**170 项测试（3 个测试套件）全部通过**。
- `bin/build-gui --preview`：**预览应用包构建与签名校验通过**。

---

## 四、后续阶段决策备忘

1. **物理目录结构（阶段 3）**：采用方案 A1，建立 `agents/{codex,antigravity}/` 子包收敛特定 Agent 实现，通用核心模块保留顶层；Swift `MonitorApp/` 按功能建立子文件夹分组。
2. **协议与特征（阶段 2）**：保持现有 `Snapshot` / `ManagementState` wire JSON 兼容，在内部通过 `SourceTraits` 与公共写入 API 收口逻辑边界。

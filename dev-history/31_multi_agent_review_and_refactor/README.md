# 全局代码审查与多 Agent 架构重构

记录日期：2026-11-16，审查基线：`704df5a`（`feat(monitor): support Antigravity Desktop`）。本章对后端（Python）与客户端（macOS Swift）进行整体代码审查，重点评估 Antigravity 接入后的模块划分、跨模块耦合点、物理目录层次结构及面向后续更多 Agent（如 Claude Code、Cursor 等）的可扩展性，并给出分阶段修复与重构方案。

| 文档 | 内容 |
| --- | --- |
| [代码审查报告](review.md) | 架构优势、多 Agent 耦合瓶颈、已确认缺陷与风险清单（`MR01`–`MR08`）及单层平铺目录分析 |
| [修复与重构方案](refactoring-plan.md) | 缺陷修复、逻辑边界收口（Adapter 与 Traits）、物理目录分层方案、实施阶段与第一阶段验收记录 |

## 当前进度

- **第一阶段（`MR01`、`MR02`、`MR03`、`MR07` 缺陷与并发/预算修复）已完成**：
  - 修复仅开启 Antigravity 时 GUI「运行状态」误判 `disabled` 与后端 `bridge` 状态误报（`MR01`）；
  - 将 `TimelineQuery.bundle` 的轨迹 RPC 移出 `history.lock`，避免慢 RPC 阻塞后台历史刷新与并发查询（`MR02`）；
  - 将 Antigravity `TranscriptIndex` 纳入 `HistoryProvider` 的 64 MiB 权重预算、`diagnostics()` 统计与 `_view()` 深拷贝隔离，并在 `AntigravityProjection` 接入 `Engine.identity_budget`（`MR03`）；
  - 扩展 Gemma 4 对 Gemini/Gemma 模型族的前缀匹配，在 `SnapshotProjection.GroupSpeed` 中聚合估算速率语义驱动 `≈` 显示，并更新卸载提示文案（`MR07`）；
  - 全量 615 项 Python 单元测试、170 项 Swift 测试与本地化导出检查通过。
- **第二阶段（`MR04`–`MR06` 逻辑收口——核心引擎与 Agent 适配器解耦）已完成**：
  - 在 `state.py` 定义不可变的 `SourceTraits` 与 `source_traits(source)`，统一收敛 `timeline.py`、`engine.py`、`diagnostic_health.py`、`timeline_query.py` 中的源特征判断（`MR04`）；
  - 在 `Engine` 与 `TimelineBook` 上提供正式公共写入 API（`register_backend`、`thread`、`remove_thread`、`activity`、`record_turn`、`record_turn_request`、`record_phase`、`record_relation`、`set_live_content`），消除 `AntigravityProjection` 对私有方法的直接穿透调用（`MR05`）；
  - 将回合平均速率计算（`turn_average_speed`）与远程步骤详情补齐（`fetch_remote_detail`）下沉至历史索引/适配器层，并在 Swift `Snapshot.swift` 中通过 `AgentSourceTraits` 与 `Backend.requiresBridgeRestart` 收敛客户端源特征判断（`MR06`）；
  - 全量 617 项 Python 单元测试、170 项 Swift 测试与本地化导出检查通过。
- **第三阶段（`MR08` 物理目录分层）待执行**。

## 核心结论

1. **现有工程基线稳固**：Bridge 字节转发与非阻塞观察队列、Collector 单线程状态机、独立只读查询服务、Swift `SnapshotProjection` 不可变图计算，以及严格的资源预算和数据诚实性（Provenance / Gap）设计成熟，应完整保留。
2. **Antigravity 内部模块职责清晰，但跨模块边界仍处于“Codex 主干 + `if antigravity` 特判”的过渡态**：新增的 6 个 `antigravity_*.py` 文件内部拆分合理，但在状态写入、时间线合并、历史缓存、控制面和客户端投影中存在 4 类耦合瓶颈，且引入了单开 Antigravity 时运行状态页显示异常、时间线查询持锁发起同步 RPC 等具体问题。
3. **逻辑收口与物理目录分层需配合推进**：`src/codex_activity_monitor/` 下 55 个 Python 文件与 `macos/Sources/MonitorApp/` 下 33 个 Swift 文件全部单层平铺，不同抽象层次混杂。在收敛逻辑接口的同时，将特定 Agent 适配代码与核心引擎在目录结构上分层，能显著降低后续新增第 3、第 4 个 Agent 的认知与修改成本。

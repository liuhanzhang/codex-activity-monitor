# 重构设计方案：L1 全量物理子包化 + L2 Codex 实时投影剥离

方案日期：2026-11-16，基于 [代码审查结论](review.md)。本方案分两步实施：**阶段 1（L1）** 完成 Python 核心层 44 个模块的物理子包化与现代绝对导入规范统一；**阶段 2（L2）** 将 `Engine` 与 `TimelineBook` 中内嵌的 Codex 实时协议/SSE 投影逻辑剥离至 `agents/codex/projection.py`。

---

## 一、 阶段 1（L1）：核心层全量物理子包分层方案

### 1. 目标目录结构

重构后，`src/codex_activity_monitor/` 顶层从 **46 个平铺文件** 精简为 **2 个顶层文件 + 7 个职责子包**：

```text
src/codex_activity_monitor/
├── __init__.py                  # 包版本 + MetaPathFinder 全量向后兼容模块别名映射
├── cli.py                       # 唯一 CLI 入口（codex-activity-monitor 命令与 python -m 入口）
│
├── agents/                      # [已有 + L2 增强] 具体 Agent 适配与协议实现层
│   ├── __init__.py
│   ├── codex/                   # Codex 专属实现（12 个既有模块 + L2 新增 projection.py）
│   └── antigravity/             # Antigravity 专属实现（6 个模块：transport, source, records, projection, history, tokenizer）
│
├── core/                        # [新增] 核心状态机、领域模型、Token 计量与视图聚合层（13 个模块）
│   ├── __init__.py
│   ├── engine.py                # 多 Agent 共享核心状态机与快照生成
│   ├── state.py                 # 领域模型（Thread, Activity, SourceTraits）
│   ├── identity_budget.py       # 会话/回合/活动 ID 内存准入预算
│   ├── metrics.py               # 流式文本接收计量与速率窗口计算
│   ├── tokenizer_process.py     # 独立子进程分词器守护与超时控制
│   ├── usage.py                 # 官方 Token 账本与分摊恢复
│   ├── request_rate.py          # 请求级配对速率估算
│   ├── tool_labels.py           # 工具名称、Shell 命令与补丁预览提取
│   ├── overview.py              # 顶部全局概览聚合与父子会话注解
│   ├── menu_presentation.py     # 状态栏菜单模式与原因裁决
│   ├── agent_presentation.py    # 子 Agent 树状展示与协同状态投影
│   └── agent_relations.py       # 跨会话父子关系与协同原语常量
│
├── timeline/                    # [新增] 时间线相位账本、SQLite 侧车与只读查询服务（6 个模块）
│   ├── __init__.py
│   ├── timeline.py              # 相位数据模型、Provenance 合并规则与 TimelineBook
│   ├── timeline_query.py        # 只读时间线 bundle/detail 查询服务与 QueryServer (tl.sock)
│   ├── timeline_storage.py      # 异步 SQLite 元数据侧车持久化
│   ├── content_version.py       # 详情内容稳定版本指纹与 Unicode 标量分页
│   ├── query_request.py         # 查询请求 DTO 校验与反序列化
│   └── query_response.py        # 查询响应帧编码与长度边界控制
│
├── history/                     # [新增] 统一历史缓存门面、资源权重与后台刷新（4 个模块）
│   ├── __init__.py
│   ├── history.py               # HistoryProvider（64 MiB 权重缓存、_view 深拷贝隔离）
│   ├── history_refresh.py       # 后台单线程历史刷新调度队列
│   ├── agent_identity.py        # 会话头部昵称/角色异步缓存读取
│   └── resource_budget.py       # 派生元数据对象内存占用递归估算
│
├── runtime/                     # [新增] 采集主循环、IPC 状态传输、租约、运行时文件与诊断（13 个模块）
│   ├── __init__.py
│   ├── collector.py             # 独立采集主循环（events.sock / state.sock / tl.sock 协调）
│   ├── runtime.py               # 运行时目录、源码 SHA-256 指纹、collector.lock 与快照节奏
│   ├── execution.py             # 源码模式与 PyInstaller 独立包资源路径解析
│   ├── owned_files.py           # 受管目录内合法文件白名单判定
│   ├── json_encoding.py         # 有界 UTF-8 JSON 编码辅助
│   ├── publication.py           # DetachedSnapshot 快照深拷贝与异步 RecoveryWriter
│   ├── state_transport.py       # state.sock 帧流式发布服务 (StateServer)
│   ├── state_client.py          # 客户端只读状态探测与会话授权
│   ├── app_session.py           # GUI 进程启动授权与 PID/启动时间身份校验
│   ├── observation_lease.py     # 内存级观察租约状态机
│   ├── observer_permit.py       # Bridge 端一次性观察许可握手
│   ├── diagnostic_log.py        # 有界异步滚动诊断日志
│   └── diagnostic_health.py     # 组件健康状态聚合 (HealthMonitor)
│
├── control/                     # [新增] 桌面端受管控制面、终端展示与验收测试配置（3 个模块）
│   ├── __init__.py
│   ├── app_control.py           # AppController（Launchctl 集成、启停、恢复与卸载清理）
│   ├── display.py               # 终端 Rich 实时面板渲染
│   └── test_profile.py          # 跨进程 GUI 验收隔离 Profile
│
└── i18n/                        # [新增] 国际化引擎与双语文案表（6 个模块）
    ├── __init__.py
    ├── i18n.py                  # 语言校验、消息构建与模板渲染
    ├── messages_zh_cn.py        # 简体中文基础文案
    ├── messages_en.py           # 英文基础文案
    ├── messages_control.py      # 控制面错误码双语文案
    ├── messages_detail.py       # 详情查看器分节标题双语文案
    └── messages_diagnostics.py  # 诊断组件与健康状态码双语文案
```

### 2. Import 规范统一要求

1. **子包 `__init__.py` 轻量化**：`core/`、`timeline/`、`history/`、`runtime/`、`control/`、`i18n/` 的 `__init__.py` 仅包含模块文档字符串，不预加载重型子模块，彻底杜绝子包初始化引发的循环导入。
2. **跨子包导入统一采用标准绝对路径**：
   - 所有跨子包引用统一写为 `from codex_activity_monitor.<subpackage>.<module> import ...`（例如 `from codex_activity_monitor.core.state import Thread`），全面清理跨层级的 `from ..xxx` 相对导入。
   - 同一子包内部模块互引可使用 `from .<module> import ...` 或完整绝对路径。
3. **向后兼容与源码指纹同步**：
   - 在 [`src/codex_activity_monitor/__init__.py`](../../src/codex_activity_monitor/__init__.py) 的 `LEGACY_MODULE_ALIASES` 中注册全部 44 个迁入子包的模块（连同阶段 3 已注册的 18 个 Agent 模块，共 62 个别名映射），确保外部或测试代码通过 `codex_activity_monitor.<legacy_name>` 导入或 `unittest.mock.patch` 时指向同一模块实例。
   - 在 [`runtime/runtime.py`](../../src/codex_activity_monitor/runtime.py) 的 `MODULE_PATHS` 中同步登记这 62 个模块短名到 `<subpackage>/<module>` 的相对路径映射，并在 `directory` 解析时定位到包根目录（`Path(__file__).resolve().parents[1]`）。

---

## 二、 阶段 2（L2）：`Engine` 与 `TimelineBook` 中的 Codex 实时投影剥离方案

### 1. 设计目标

建立 [`src/codex_activity_monitor/agents/codex/projection.py`](../../src/codex_activity_monitor/agents/codex/)，将原本内嵌在 `core/engine.py` 和 `timeline/timeline.py` 中的 Codex 专属协议与上游 SSE 实时投影逻辑抽离出来，使 `agents/codex/` 与 `agents/antigravity/` 形成**完全对称的架构**：

| 模块职责 | `agents/codex/` | `agents/antigravity/` |
| --- | --- | --- |
| **实时传输与采集** | `bridge.py`、`events.py` | `transport.py`、`source.py` |
| **实时状态与时间线投影** | **`projection.py`（L2 新增）**、`progress_state.py` | `projection.py`、`records.py` |
| **历史索引与对账** | `history_*.py`、`reconciliation.py` | `history.py` |
| **集成控制与专用分词** | `integration.py`、`integration_status.py` | `tokenizer.py` |

### 2. 具体剥离边界

#### ① 从 `core/engine.py` 剥离至 `agents/codex/projection.py` 的逻辑（约 620 行）
- **迁移内容**：
  - `ingest_event(engine, row)`：原 `Engine.ingest` 的完整实现（含 `Event.from_row`、身份准入、`lifecycle` / `protocol_in` / `protocol_out` / `upstream` 分发、延迟队列 `_resolve`、Usage 计量与 Timeline 观察触发）；
  - `handle_lifecycle(engine, event)`：原 `Engine._lifecycle`（处理 `bridge/started`、`bridge/heartbeat`、`bridge/gap`、`bridge/reconnected`、`bridge/disconnected`、`bridge/stopped`）；
  - `handle_protocol_event(engine, event)` 与 `handle_protocol_item(engine, thread, turn_id, item, method, at)`：原 `Engine._protocol` 与 `Engine._protocol_item`（处理 JSON-RPC `thread/*`、`turn/*`、`item/*`、`collabAgentToolCall` 等）；
  - `queue_unassociated(engine, event, item_id)` 与 `resolve_pending(engine, backend)`：原 `Engine._queue` 与 `Engine._resolve`；
  - `handle_upstream(engine, event)` 与 `handle_delta(engine, event, ...)`：原 `Engine._upstream` 与 `Engine._delta`（处理 OpenAI Responses SSE `response.output_item.*`、`response.*.delta` 等）。
- **`Engine` 保留的通用职责（约 480 行）**：
  - 核心状态容器初始化（`threads`、`backends`、`streams`、`usage`、`identity_budget` 等）；
  - 公共状态写入与生命周期辅助 API（`register_backend`、`thread`、`remove_thread`、`activity`、`admit_identities`、`_finish_stream`、`_tool_metadata`、`_tool_label`、`_assign_parent`、`_map_turn`、`_metadata`）；
  - 跨源共享的 `history_requests()`、`history_hint()`、`restore_unresolved()`、`apply_history()`；
  - 周期性推进与淘汰 `tick(now)`、全局快照生成 `snapshot(now, wall_time)` 与 `close()`；
  - 保留薄委托入口 `ingest(self, row)`（调用 `codex_projection.ingest_event(self, row)`），确保所有外部调用方与单元测试无需修改。

#### ② 从 `timeline/timeline.py` 剥离至 `agents/codex/projection.py` 的逻辑（约 150 行）
- **迁移内容**：
  - `observe_timeline_event(book, event, engine)`：原 `TimelineBook.observe` 的完整实现（处理 Codex 事件时钟锚点、跳变检测、`turn/started`、`turn/completed`、`item/started`、`item/completed`、`SERVER_REQUESTS` 标记相位等）；
  - `classify_tool_input(book, scope, thread, turn, item, engine, backend)` 与 `capture_command_output(book, scope, thread, turn, item, params)`：原 `TimelineBook._classify_input` 与 `TimelineBook._capture_output`。
- **`TimelineBook` 保留的通用职责（约 470 行）**：
  - 相位合并纯函数（`merge_phase`、`merge_history_phase`、`settled_model_items`、`display_phase` 等）；
  - 相位账本存储与公共写入 API（`record_turn`、`record_turn_request`、`record_phase`、`record_relation`、`set_live_content`、`apply_history`、`gap`、`snapshot`、`pin`、`live_detail`、`prune`）；
  - 保留薄委托入口 `observe(self, event, engine)`（调用 `codex_projection.observe_timeline_event(self, event, engine)`）。

#### ③ 构建指纹同步
- 在 [`runtime/runtime.py`](../../src/codex_activity_monitor/runtime.py) 的 `MODULE_PATHS` 及 [`runtime/collector.py`](../../src/codex_activity_monitor/collector.py) 的 `implementation = source_identity(...)` 列表中加入 `'codex_projection': 'agents/codex/projection'`，确保 Collector 源码指纹完整覆盖剥离后的新模块。

---

## 三、 实施与验收记录（2026-11-17）

### 1. 分步实施结果汇总

| 阶段 | 实施内容 | 量化结果与验证 |
| --- | --- | --- |
| **阶段 1（L1：物理子包迁移与导入规范化）** | 创建 [`core/`](../../src/codex_activity_monitor/core/__init__.py)（12 个模块）、[`timeline/`](../../src/codex_activity_monitor/timeline/__init__.py)（6 个模块）、[`history/`](../../src/codex_activity_monitor/history/__init__.py)（4 个模块）、[`runtime/`](../../src/codex_activity_monitor/runtime/__init__.py)（13 个模块）、[`control/`](../../src/codex_activity_monitor/control/__init__.py)（3 个模块）、[`i18n/`](../../src/codex_activity_monitor/i18n/__init__.py)（6 个模块）六个子包；将顶层 44 个核心模块迁入对应子包，顶层仅保留 [`__init__.py`](../../src/codex_activity_monitor/__init__.py) 与 [`cli.py`](../../src/codex_activity_monitor/cli.py)；将包内所有跨子包引用统一改为 `from codex_activity_monitor.<subpackage>.<module> import ...` 标准绝对导入；通过 `_AliasFinder` + `_PackageFacade` 与 [`runtime/runtime.py`](../../src/codex_activity_monitor/runtime/runtime.py) 的 `MODULE_PATHS` 保持向后兼容与源码指纹一致 | `src/codex_activity_monitor/` 顶层平铺 `.py` 文件数从 **46 个降至 2 个**（减少 **95.7%**）；包内跨目录多点相对导入（`from ..`）清零 |
| **阶段 2（L2：Codex 实时投影剥离）** | 新建 [`src/codex_activity_monitor/agents/codex/projection.py`](../../src/codex_activity_monitor/agents/codex/projection.py)（753 行），将 `Engine` 中的 Codex JSON-RPC / Responses SSE 解析方法（`ingest_event`、`handle_lifecycle`、`handle_protocol_event`、`handle_protocol_item`、`queue_unassociated`、`resolve_pending`、`handle_upstream`、`handle_delta`）与 `TimelineBook` 中的 Codex 相位观察方法（`observe_timeline_event`、`classify_tool_input`、`capture_command_output`）迁入该模块；`Engine.ingest` 与 `TimelineBook.observe` 保留薄委托入口；在 [`runtime/collector.py`](../../src/codex_activity_monitor/runtime/collector.py) 与 [`runtime/runtime.py`](../../src/codex_activity_monitor/runtime/runtime.py) 中注册 `codex_projection` 源码指纹 | [`core/engine.py`](../../src/codex_activity_monitor/core/engine.py) 从 **1,121 行降至 549 行**（精简 **51.0%**）；[`timeline/timeline.py`](../../src/codex_activity_monitor/timeline/timeline.py) 从 **643 行降至 496 行**（精简 **22.9%**）；`agents/codex/` 与 `agents/antigravity/` 结构完全对称 |
| **阶段 3（专项回归测试与全链路打包验收）** | 在 [`tests/test_antigravity.py`](../../tests/test_antigravity.py) 新增 `test_subpackage_hierarchy_import_conventions_and_codex_projection_decoupling`，校验 7 大子包目录结构、顶层仅保留 `{'__init__.py', 'cli.py'}`、无跨级 `from ..` 导入、`LEGACY_MODULE_ALIASES` 模块对象同一性、`codex_projection` 解耦及全量 63 个模块的 `source_identity` 指纹解析 | 全量 619 项 Python 测试、170 项 Swift 测试、本地化检查、`build-standalone` 与 `build-gui` 全部通过 |

### 2. 验证结果清单

1. `.local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'`：**619 项单元测试全部通过**（95.8s）。
2. `.local/venv/bin/python bin/export-localizations --check`：**通过**。
3. `swift test --package-path macos`：**170 项测试（3 个测试套件）全部通过**。
4. `./bin/build-standalone` & `./bin/build-gui`：**PyInstaller 独立后端打包、`--self-check`、Mach-O 架构校验与 GUI 签名全部通过**。

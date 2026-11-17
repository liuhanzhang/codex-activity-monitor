# 代码审查结论：核心层平铺治理与架构对称性评估

审查日期：2026-11-16，代码基线：`b13e921`。本章聚焦第 31 章阶段 3（保守物理分层）完成后的代码库状态，从**可读性、可维护性、可迭代性**及**现代 Python 工程规范**四个维度评估是否需要进一步整理或重构。

---

## 一、 现状诊断：为什么阶段 3 之后仍然存在结构痛点？

统计当前全仓源码规模与目录分布：
- **Swift 客户端（`macos/Sources/`）结构已合理**：
  - `MonitorApp/` 下 37 个 `.swift` 文件已按职责拆入 `App/` (13)、`Dashboard/` (7)、`Timeline/` (7)、`Settings/` (6)、`Diagnostics/` (4) 五个子目录；
  - `MonitorModel/` 仅 8 个 `.swift` 文件，职责边界清晰，无需再拆。
- **Python 后端（`src/codex_activity_monitor/`）顶层依然平铺 46 个文件**：
  - 阶段 3 将 12 个 Codex 模块和 6 个 Antigravity 模块迁入 `agents/{codex,antigravity}/`，但包根目录下仍保留了 **46 个 `.py` 文件（共约 10,800 行）**。

深入分析这 46 个顶层文件及核心类的内部实现，可以识别出三大核心痛点：

### 1. 六个不同抽象层级的模块混排在同一目录（可读性痛点）

当前顶层目录同时堆放着从底层字节编码、文案字典到顶层 CLI 入口的 6 类文件，新读者或维护者无法通过目录树建立系统的心智模型：

| 职责域 | 当前顶层平铺文件 | 文件数 | 总行数 | 结构问题 |
| --- | --- | --- | --- | --- |
| **1. 国际化资源（i18n）** | `i18n.py`、`messages_zh_cn.py`、`messages_en.py`、`messages_control.py`、`messages_detail.py`、`messages_diagnostics.py` | 6 | 448 | 纯静态文案字典占去顶层 13% 的文件数，干扰核心代码导航 |
| **2. 微型工具与协议碎片** | `owned_files.py` (15L)、`json_encoding.py` (19L)、`resource_budget.py` (29L)、`execution.py` (36L)、`query_response.py` (41L)、`app_session.py` (48L)、`query_request.py` (54L)、`observer_permit.py` (59L)、`test_profile.py` (66L) 等 | 9 | 367 | 历史章节逐次迭代留下的极小模块（平均 40 行）散落在顶层，与上千行核心文件并列 |
| **3. IPC 传输、运行时与诊断** | `collector.py` (425L)、`runtime.py` (191L)、`publication.py` (140L)、`state_transport.py` (322L)、`state_client.py` (170L)、`observation_lease.py` (114L)、`diagnostic_log.py` (321L)、`diagnostic_health.py` (87L) | 8 | 1,770 | 进程生命周期锁、Unix Socket 传输与健康检查未与业务领域模型隔离 |
| **4. 时间线与只读查询子系统** | `timeline.py` (643L)、`timeline_query.py` (560L)、`timeline_storage.py` (218L)、`content_version.py` (132L) | 4 | 1,553 | 与 `query_request.py` / `query_response.py` 同属 `tl.sock` 查询闭环，但在顶层被字母序拆散 |
| **5. 历史缓存与后台刷新** | `history.py` (490L)、`history_refresh.py` (215L)、`agent_identity.py` (139L) | 3 | 844 | 与 `resource_budget.py` 共同构成历史读取门面，未形成独立子包 |
| **6. 核心状态机、计量与控制面** | `engine.py` (1121L)、`state.py` (141L)、`identity_budget.py` (77L)、`usage.py` (495L)、`metrics.py` (399L)、`tokenizer_process.py` (171L)、`request_rate.py` (282L)、`tool_labels.py` (500L)、`overview.py` (127L)、`menu_presentation.py` (78L)、`agent_presentation.py` (140L)、`agent_relations.py` (77L)、`app_control.py` (770L)、`cli.py` (298L)、`display.py` (241L) | 15 | 4,917 | 领域模型、Token 计量、GUI 聚合视图与桌面端 Launchctl 控制面混杂 |

### 2. 核心大类内部“Codex 内嵌耦合 vs Antigravity 外部解耦”不对称（可维护性与可迭代性痛点）

在第 31 章阶段 2 中，我们为 `Engine` 和 `TimelineBook` 提炼了公共写入 API，使 [`agents/antigravity/projection.py`](../../src/codex_activity_monitor/agents/antigravity/projection.py)（408 行）完全通过公共方法将源端事件投影到核心状态机。

然而，**Codex 的实时投影逻辑仍然直接寄生在 `Engine` 与 `TimelineBook` 内部**：
- **[`engine.py`](../../src/codex_activity_monitor/engine.py)（1,121 行）**：其中 `L295–L923`（共 **628 行，占整个文件的 56%**）的 `ingest`、`_lifecycle`、`_protocol`、`_protocol_item`、`_queue`、`_resolve`、`_upstream`、`_delta`、`_finish_stream` 全部是针对 Codex CLI Bridge（`bridge/started`、`bridge/gap`、JSON-RPC `turn/started` / `item/started` / `collabAgentToolCall`、OpenAI Responses SSE `response.output_item.added` 等）的专属协议解析与状态投影。
- **[`timeline.py`](../../src/codex_activity_monitor/timeline.py)（643 行）**：其中 `L395–L544`（共 **150 行**）的 `TimelineBook.observe`、`_classify_input`、`_capture_output` 同样完全服务于 Codex JSON-RPC / SSE 事件的相位投影。

这种不对称导致：
1. `engine.py` 体积破千行，阅读“通用多 Agent 状态机与快照聚合”时不得不跨过 600 多行 Codex 协议细节；
2. `agents/codex/` 子包中只有 `progress_state.py`（处理特殊通知）和 `reconciliation.py`（处理历史对账），却缺少最核心的实时投影模块 `projection.py`，与 `agents/antigravity/projection.py` 不成体系。

---

## 二、 现代 Python / `uv` 项目的包结构与 Import 规范澄清

针对审查过程中关于“是否应使用 `from src.xxx import xxx` 还是 `from .xxx import xxx`”的疑问，明确以下工程规范：

### 1. 为什么不能写 `from src.xxx import xxx`？
本项目采用现代 Python（PyPA / `uv`）标准的 **`src-layout`**（[`pyproject.toml`](../../pyproject.toml#L24-L26) 中声明 `where = ["src"]`，包名为 `codex_activity_monitor`）：
- `src/` 是**源码容器目录**，不是 Python 包（无 `__init__.py`）。
- 当通过 `uv` / `pip` 安装到环境或通过 PyInstaller 打包为独立 macOS 应用（`MonitorBackend.app`）后，运行环境中只有顶层包 `codex_activity_monitor`，不存在 `src`。
- 若代码中出现 `from src.xxx import ...`，打包后的独立应用或安装在系统中的 CLI 将直接抛出 `ModuleNotFoundError: No module named 'src'`。

### 2. 标准绝对导入 vs 相对导入的最佳实践
在标准 `src/codex_activity_monitor/` 结构下，结合 PEP 8 与现代 Python 社区规范：
1. **跨子包导入（不同目录之间）**：统一推荐使用**以包名开头的绝对导入**：
   ```python
   from codex_activity_monitor.core.state import Activity, Thread, source_traits
   from codex_activity_monitor.i18n.i18n import message, render_message, tr
   ```
   相比跨目录相对导入（`from ..core.state import ...` 或 `from ...i18n import ...`），标准绝对导入无需数点号、可读性最强，且移动文件位置时无需修改导入语句。
2. **同一子包内导入（同目录兄弟模块之间）**：允许使用单点相对导入（`from .records import ...`）或标准绝对导入，表达同子系统内部的高内聚关系。
3. **子包 `__init__.py` 与循环导入防控**：
   - 循环导入是否发生，不取决于使用相对导入还是绝对导入，而取决于**模块的顶层依赖拓扑**以及**子包 `__init__.py` 是否提前加载了重型模块**。
   - 当执行 `from codex_activity_monitor.core.state import Thread` 时，Python 必然先执行 `core/__init__.py`。若 `core/__init__.py` 中导入了 `core.engine`，就会把原本只依赖 `state` 的轻量调用方卷入 `engine` 的完整依赖链中，引发循环导入。
   - **规范约束**：所有新增功能子包（`core/`、`timeline/`、`history/`、`runtime/`、`control/`、`i18n/`）的 `__init__.py` **仅保留模块文档字符串（不执行重型子模块重导出）**，调用方精确导入到具体模块。

---

## 三、 重构梯度评估与风险分析

我们将可选的重构力度划分为 **L1、L2、L3** 三个梯度：

| 维度 | L1：全量物理子包分层 + 导入规范化 | L2：剥离 `Engine`/`TimelineBook` 中的 Codex 投影 | L3：全量动态插件化 Runtime 与协议重写 |
| --- | --- | --- | --- |
| **核心动作** | 将顶层 46 个 `.py` 文件按职责迁入 `core/`、`timeline/`、`history/`、`runtime/`、`control/`、`i18n/` 六个子包；顶层仅留 `__init__.py` 与 `cli.py` | 新建 `agents/codex/projection.py`，将 `engine.py` (628L) 和 `timeline.py` (150L) 中的 Codex 实时协议解析抽离出去 | 将 `collector.py`、`history.py`、`app_control.py` 及 `Snapshot` JSON 契约全部改写为插件注册表 |
| **收益** | 顶层文件数从 **46 降至 2**；目录树一眼看清 7 大子包职责边界 | `engine.py` 降至 ~480 行，`timeline.py` 降至 ~470 行；`agents/codex/` 与 `agents/antigravity/` 彻底对称 | 未来接入第 3 个 Agent 时减少主循环分支 |
| **风险等级** | **低**（纯文件迁移 + 导入路径更新） | **中低**（方法抽离与委托，保持原有状态字段不变） | **高**（破坏前后端 JSON 契约，且目前仅 2 个异构样本易过度设计） |
| **决策结论** | **立即执行** | **立即执行（紧随 L1）** | **暂不执行（留待第 3 个 Agent 立项时按 Rule of Three 推进）** |

### 关键风险与防控措施

1. **`unittest.mock.patch` 与历史导入路径兼容风险**
   - **现象**：`tests/` 中存在约 40 处 `patch('codex_activity_monitor.<module>.<attr>')`（如 `patch('codex_activity_monitor.metrics.count_text')`、`patch('codex_activity_monitor.app_control.process_alive')`）及内联子进程脚本。
   - **防控**：复用并扩展 [`src/codex_activity_monitor/__init__.py`](../../src/codex_activity_monitor/__init__.py) 中的 `LEGACY_MODULE_ALIASES` 与 `_AliasFinder`。由于 `_AliasFinder` 返回真实子包模块的同一个 `module` 对象实例，任何对 `codex_activity_monitor.<legacy_name>` 的导入或 `mock.patch` 均直接作用于真实模块，实现 100% 零成本向后兼容。
2. **源码 SHA-256 指纹与独立应用打包（PyInstaller）风险**
   - **现象**：[`runtime.py: source_identity`](../../src/codex_activity_monitor/runtime.py#L49-L60) 与 [`packaging/build_app.py`](../../packaging/build_app.py#L95-L110) 依赖模块路径映射计算 `source_sha256` 和 `build-manifest.json`。
   - **防控**：同步更新 `MODULE_PATHS` 映射表（将所有迁入子包的模块短名映射到 `<subpackage>/<module>`），并通过 `test_bundled_execution.py` 与 `./bin/build-standalone` 验证。
3. **L2 拆分 `Engine` 与 `TimelineBook` 时的内部状态协作风险**
   - **现象**：`agents/codex/progress_state.py`、`agents/codex/reconciliation.py`、`usage.py` 会直接访问 `engine.items`、`engine.turns`、`engine.ended_turns`、`engine.approvals`、`engine._thread`、`engine._activity`、`engine._finish_stream` 等属性与方法。
   - **防控**：在 L2 中采用**“职责抽离 + 保持宿主状态容器与入口方法签名不变”**的设计：`Engine` 仍持有各状态字典并保留 `engine.ingest(row)` 等入口方法（内部直接委托给 `agents.codex.projection`），既将 600+ 行 Codex 协议解析代码迁出 `engine.py`，又保证所有外部调用方与测试契约零破坏。

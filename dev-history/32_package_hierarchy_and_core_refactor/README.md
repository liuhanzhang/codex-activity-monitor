# 核心层子包化与 Codex 投影解耦（L1 + L2 重构）

记录日期：2026-11-16，审查基线：`b13e921`（`refactor(monitor): organize agent subpackages and GUI subdirectories (phase 3)`）。本章在第 31 章完成多 Agent 缺陷修复、逻辑特征收口与 `agents/{codex,antigravity}/` 初步分层的基础上，站在**可读性、可维护性、可迭代性**角度对 Python 后端进行二次深度代码审查，制定并实施 **L1（核心层全量物理子包化与导入规范统一）+ L2（`Engine` 与 `TimelineBook` 中的 Codex 实时投影逻辑剥离）** 重构。

| 文档 | 内容 |
| --- | --- |
| [代码审查结论](review.md) | 阶段 3 后的可读性/可维护性痛点诊断（顶层 46 文件平铺、核心大类内 Codex 与通用状态机不对称）、现代 Python / `uv` 包导入规范澄清，以及 L1/L2/L3 三级重构梯度与风险评估 |
| [重构设计方案](refactoring-plan.md) | L1 全量物理子包划分（`core/`、`timeline/`、`history/`、`runtime/`、`control/`、`i18n/`）、L2 `agents/codex/projection.py` 剥离设计、标准导入规范、向后兼容与打包指纹保障及分步验收标准 |

## 当前进度

- **阶段 1（L1：核心层全量物理子包化与导入规范统一）已完成**：
  - 将 `src/codex_activity_monitor/` 顶层 44 个核心模块按职责迁入 `core/`（12 个模块）、`timeline/`（6 个模块）、`history/`（4 个模块）、`runtime/`（13 个模块）、`control/`（3 个模块）、`i18n/`（6 个模块），顶层仅保留 `__init__.py` 与 `cli.py`（顶层 `.py` 文件从 46 个减少至 2 个）；
  - 全仓跨子包导入统一为 `from codex_activity_monitor.<subpackage>.<module> import ...` 标准绝对导入，彻底清零跨目录 `from ..` 相对导入；
  - 扩展 `LEGACY_MODULE_ALIASES`（59 个别名映射）与 `_PackageFacade`（支持 `runtime`、`history`、`timeline`、`i18n` 四大同名子包零开销惰性转发），并在 `runtime.MODULE_PATHS` 同步登记全部 63 个模块路径。
- **阶段 2（L2：`Engine` 与 `TimelineBook` 中的 Codex 实时投影逻辑剥离）已完成**：
  - 新建 [`src/codex_activity_monitor/agents/codex/projection.py`](../../src/codex_activity_monitor/agents/codex/projection.py)（753 行），将 `Engine` 中的 Codex JSON-RPC / Responses SSE 事件解析与 `TimelineBook` 中的 Codex 相位观察逻辑完整抽离；
  - [`core/engine.py`](../../src/codex_activity_monitor/core/engine.py) 从 **1,121 行降至 549 行**（精简 **51.0%**），[`timeline/timeline.py`](../../src/codex_activity_monitor/timeline/timeline.py) 从 **643 行降至 496 行**（精简 **22.9%**），实现 `agents/codex/` 与 `agents/antigravity/` 架构完全对称；
  - 全量 619 项 Python 单元测试、170 项 Swift 测试、双语本地化检查及 `./bin/build-standalone`、`./bin/build-gui` 独立打包与签名校验全部通过。

## 核心结论

1. **顶层平铺文件过多是当前最大的可读性阻力**：阶段 3 虽将 18 个 Agent 专属文件迁入 `agents/`，但 `src/codex_activity_monitor/` 顶层仍平铺着 46 个 `.py` 文件，混杂了国际化字典、微型工具碎片、IPC 传输、时间线查询、历史缓存和核心状态机 6 个不同职责域。
2. **核心大类存在“Antigravity 外部解耦 vs Codex 内嵌耦合”的不对称**：Antigravity 通过独立的 [`agents/antigravity/projection.py`](../../src/codex_activity_monitor/agents/antigravity/projection.py)（408 行）调用公共 API 写入状态；而 Codex 的 JSON-RPC / Responses SSE 实时投影逻辑（[`engine.py`](../../src/codex_activity_monitor/engine.py) 约 620 行 + [`timeline.py`](../../src/codex_activity_monitor/timeline.py) 约 150 行）仍直接内嵌在核心类中，导致 `engine.py` 膨胀至 1,121 行。
3. **采用“L1 + L2”组合重构投入产出比最高，L3 延后至第 3 个 Agent 立项**：
   - **L1（全量物理子包化 + 导入规范统一）**：将顶层 46 个文件收口为 `2 个入口文件 + 7 个职责子包`，跨子包统一采用 `from codex_activity_monitor.<subpackage>.<module> import ...` 标准绝对导入，子包 `__init__.py` 保持轻量无副作用；
   - **L2（Codex 实时投影剥离）**：将 `Engine` 与 `TimelineBook` 中的 Codex 协议/上游流解析抽入 `agents/codex/projection.py`，使 `engine.py` 瘦身至 ~480 行、`timeline.py` 瘦身至 ~470 行，实现 `agents/codex/` 与 `agents/antigravity/` 架构完全对称；
   - **L3（全量插件化 Runtime 注册表）**：遵循 Rule of Three（三次法则），暂不在仅有两个异构数据源时过度抽象控制面与快照协议。

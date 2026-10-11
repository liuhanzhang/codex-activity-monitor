# Codex Progress Monitor

本项目研究并实现 Codex Desktop 的进度反馈：当前阶段、工具活动、正文与工具参数的接收速度、最近进展、token 用量与账户限额。

目前已完成部分隔离实验和架构研究，尚未完成监控产品。第一版先覆盖本机，通过简单实时看板验证采集效果，正式 UI 后续再做。产品范围与实施顺序见 [第一版产品需求与实施计划](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/requirements-and-plan.md)；实验依据见 [初始实验文档](/Users/bytedance/personal/codex_progress_monitor/docs/01_initial_experiment/README.md)。

## 目录

| 目录 | 用途 | 是否应纳入版本控制 |
| --- | --- | --- |
| `docs/01_initial_experiment/` | 架构、数据来源、需求和验证计划 | 是 |
| `docs/02_product_mvp/` | 已确认的第一版产品需求与实施计划 | 是 |
| `analysis/` | 研究结论、分析脚本、版本清单及精简证据 | 是，生成源码快照除外 |
| `experiments/desktop-isolation/` | 隔离环境的启动、实验、配置和停止脚本 | 是，运行数据除外 |
| `experiments/product-preflight/` | 第一版接入、并发、tokenizer 与故障验证脚本 | 是 |
| `.local/research/` | 从安装包提取的源码及生成的协议 schema | 否 |
| `experiments/desktop-isolation/runs/` | 本机隔离 App 配置、登录状态、数据库、原始日志与聊天记录 | 否 |

未来产品代码与测试可分别放在 `src/` 和 `tests/`；当前不创建空目录或预设技术栈。

第一版核心验证结果见 [实现前验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/README.md)。已选择原版 CLI 的透明 stdio 转发路线，完整产品和日常 Desktop 看板试用仍待实现。

## 数据与实验约定

- 可复用脚本、文档和精简验证结果与本机运行状态分开管理。
- 生成源码已集中到 `.local/research/`；原位置保留兼容软链接，使历史定位和文档链接继续有效。它们都被 `.gitignore` 排除。
- 正在使用的隔离运行目录保留原位置，避免影响 App、SQLite 和实验脚本。当前入口见 [实验说明](/Users/bytedance/personal/codex_progress_monitor/experiments/desktop-isolation/README.md)。
- 原始日志不是可随意删除的缓存；已结束的日志可无损压缩。精简结论的复查仍可能依赖这些本地材料。
- 不提交实验登录令牌、浏览器配置、数据库、原始对话或下载的运行时依赖。

Git 忽略规则已排除本机运行状态和生成材料；其他开发者仍需按目标安装版本重新准备相应本地材料。

本次整理记录见 [工作区整理说明](/Users/bytedance/personal/codex_progress_monitor/docs/01_initial_experiment/workspace-organization.md)。

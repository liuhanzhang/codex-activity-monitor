# Codex Progress Monitor

本项目实现 Codex Desktop 本机聊天的实时进度反馈：当前阶段、工具活动、持续时间、Reasoning 摘要增长、正文与工具参数的接收 token/s。

第一版采集器、状态聚合和终端看板已经可以运行。当前机器已配置登录启动与原 CLI 包装器，已观察到主 home 后端接入；隔离 Desktop 的真实 UI 核心采集检查已通过。主 App 普通入口的独立启动验收与长期运行验收尚待完成。正式 UI 与远程主机后续增加。

```sh
/Users/bytedance/personal/codex_progress_monitor/bin/codex-progress watch
```

安装、使用和恢复步骤见 [使用说明](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/usage.md)。产品范围见 [需求与计划](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/requirements-and-plan.md)，实测边界见 [产品验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-mvp/README.md)。

接入 Monitor 后的前后端通信、监听位置和指标计算见 [产品架构与工作方式](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/architecture.md)。

主 App / 隔离 App 的启动步骤、环境变量保存位置，以及各进程如何启动、停止和通信，见 [启动步骤与进程管理](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/startup-and-processes.md)。

## 目录

| 目录 | 用途 | 是否应纳入版本控制 |
| --- | --- | --- |
| `docs/01_initial_experiment/` | 架构、数据来源、需求和验证计划 | 是 |
| `docs/02_product_mvp/` | 已确认的第一版产品需求与实施计划 | 是 |
| `analysis/` | 研究结论、分析脚本、版本清单及精简证据 | 是，生成源码快照除外 |
| `experiments/desktop-isolation/` | 隔离环境的启动、实验、配置和停止脚本 | 是，运行数据除外 |
| `experiments/product-preflight/` | 第一版接入、并发、tokenizer 与故障验证脚本 | 是 |
| `.local/research/` | 从安装包提取的源码及生成的协议 schema | 否 |
| `src/codex_progress_monitor/` | 产品桥接、聚合、分词、看板与恢复模块 | 是 |
| `tests/` | 核心语义、运行可靠性与有限验收脚本 | 是 |
| `.local/runtime/` | 本机启动配置、socket 与指标快照 | 否 |
| `.local/venv/` | 产品依赖环境 | 否 |
| `experiments/desktop-isolation/runs/` | 本机隔离 App 配置、登录状态、数据库、原始日志与聊天记录 | 否 |

入口采用原版 CLI 的透明 stdio 转发，正文与完整参数不默认落盘。实现前路线验证见 [实现前验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/README.md)。

## 数据与实验约定

- 可复用脚本、文档和精简验证结果与本机运行状态分开管理。
- 生成源码已集中到 `.local/research/`；原位置保留兼容软链接，使历史定位和文档链接继续有效。它们都被 `.gitignore` 排除。
- 正在使用的隔离运行目录保留原位置，避免影响 App、SQLite 和实验脚本。当前入口见 [实验说明](/Users/bytedance/personal/codex_progress_monitor/experiments/desktop-isolation/README.md)。
- 原始日志不是可随意删除的缓存；已结束的日志可无损压缩。精简结论的复查仍可能依赖这些本地材料。
- 不提交实验登录令牌、浏览器配置、数据库、原始对话或下载的运行时依赖。

Git 忽略规则已排除本机运行状态和生成材料；其他开发者仍需按目标安装版本重新准备相应本地材料。

本次整理记录见 [工作区整理说明](/Users/bytedance/personal/codex_progress_monitor/docs/01_initial_experiment/workspace-organization.md)。

# Codex Progress Monitor

本项目实现 Codex Desktop 本机聊天的实时进度反馈：当前阶段、工具活动、持续时间、正文与工具参数的接收 token/s、本回合 Output / Reasoning 用量。

第一版采集器、状态聚合和终端看板已经可以运行。当前机器已配置登录启动与原 CLI 包装器，已观察到主 home 后端接入；隔离 Desktop 的真实 UI 核心采集检查已通过。主 App 普通入口的独立启动验收与长期运行验收尚待完成。原生 GUI 已在本机实现与接入，远程主机支持后续增加。

```sh
/Users/bytedance/personal/codex_progress_monitor/bin/codex-progress watch
```

安装、使用和恢复步骤见 [使用说明](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/usage.md)。产品范围见 [需求与计划](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/requirements-and-plan.md)，实测边界见 [产品验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-mvp/README.md)。

接入 Monitor 后的前后端通信、监听位置和指标计算见 [产品架构与工作方式](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/architecture.md)。

时间线三档缩放与分段跟随已实现并完成定向验证：默认 60 秒参考比例，可切换 30／120 秒；视窗每次保留 25% 上下文向前切换，原生滚动条保持现状。日常 App 已构建并重载，采集已恢复。见[第 15 章](docs/15_timeline_viewport_paging/README.md)与[实施结果](docs/15_timeline_viewport_paging/implementation-results.md)。

后续改进见 [待办清单](docs/TODO.md)，包括将 GUI 实时同步从磁盘快照轮询迁移到本地 IPC。

文案统一、命令名简化、1Hz 看板与多语言准备见 [易用性需求](docs/04_usability_and_localization/requirements.md)、[实现计划](docs/04_usability_and_localization/implementation-plan.md) 和 [实施结果](docs/04_usability_and_localization/implementation-results.md)。

本回合官方 Output / Reasoning 用量已实现并完成隔离运行验证。推理均速已移除：事件接收区间不能保证覆盖服务端实际推理过程，且多段推理缺少通用的逐段用量。保留可见文本的实时接收速度；现行范围与旧数据兼容规则见[移除说明](docs/05_token_usage_and_reasoning_speed/removal.md)。原[需求](docs/05_token_usage_and_reasoning_speed/requirements.md)、[计划](docs/05_token_usage_and_reasoning_speed/implementation-plan.md)和[验收记录](docs/05_token_usage_and_reasoning_speed/implementation-results.md)保留为历史资料。

主 App / 隔离 App 的启动步骤、环境变量保存位置，以及各进程如何启动、停止和通信，见 [启动步骤与进程管理](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/startup-and-processes.md)。

原生 macOS 菜单栏 GUI 已可在本机运行：`bin/build-gui` 构建，`bin/run-gui` 打开。支持总速度、聊天 / 活动详情、配置与恢复、退出停止采集及登录启动；缺 App / Python 时的 shell 回退已通过隔离进程验证。新版桥接暂停 / 恢复观察、日常双聊天生成、正式恢复后的延后清理和主动再配置已验证。见 [GUI 使用](docs/06_gui_app_options_and_decisions/usage.md)、[实施结果与验收边界](docs/06_gui_app_options_and_decisions/implementation-results.md) 和 [方案决策](docs/06_gui_app_options_and_decisions/README.md)；公开发行包后续交付。

紧凑面板、以三位数为基准并按需扩宽的菜单栏、整数 `T/s`、空闲沙滩图标、单会话三点依次明暗变化 / 敲击锤子及弹出交互已实现，并更新本机 App。沙滩图标先用静态版；小狗素材和开发预览保留，普通界面不提供切换选项。需求、设计原稿、原生预览和验证边界见 [GUI UI / UX 重设计](docs/08_gui_ui_ux_redesign/README.md)及[实施结果](docs/08_gui_ui_ux_redesign/implementation-results.md)。

展开面板的活动时间线已实现并完成本机针对性验证：每轮模型／工具双轨、四类模型颜色、子智能体汇总、独立活动查看器，复用 Codex 历史并补充阶段元数据。见 [活动时间线文档](docs/09_activity_timeline/README.md)及[实施结果与限制](docs/09_activity_timeline/implementation-results.md)。

展开面板的信息结构与交互重设计已实现：运行聊天默认展开、最近完成默认收起，使用原生展开控件；聊天内先看时间线，再看两列轮次统计。见[第 10 章](docs/10_expanded_panel_ux/README.md)和[实施结果](docs/10_expanded_panel_ux/implementation-results.md)。本轮只改展开面板，独立活动窗口保持现状。

第三轮展开面板已实现并载入日常 GUI：统一聊天列表、最多 5 行、单选查看区、主子智能体固定紧凑双轨、标题轮次入口和原生加减缩放。71 项定向测试及关键原生交互、有限来源核对已完成；日常重载触发的既有采集器租约重启及验证边界见[实施结果](docs/13_expanded_panel_round3/implementation-results.md)。设计与[最终交互线框](docs/13_expanded_panel_round3/assets/wireframe.html)见[第 13 章](docs/13_expanded_panel_round3/README.md)。

“JSONL 已记录完成、Monitor 仍显示运行中”的修复已实现并载入日常 collector：以 Codex JSONL 为基础，补充实时活动，在后台主动校正残留状态。232 项相关测试、隔离 collector、原始来源和有限日常检查已完成；实际证据与观测重载边界见[第 14 章实施结果](docs/14_jsonl_activity_reconciliation/implementation-results.md)。

## 目录

**日常开发主要看 `src/`、`tests/`、`bin/`、`pyproject.toml` 和 `docs/02_product_mvp/`。** 其余目录是实验资料或本机运行环境。

```text
codex_progress_monitor/
├── src/codex_progress_monitor/      产品代码：包装器、采集器、指标计算、看板
├── macos/                          原生 SwiftUI / AppKit 菜单栏 App 与 Swift 测试
├── tests/                          产品测试与有限验收脚本
├── bin/codex-progress              命令入口，使用 .local/venv
├── pyproject.toml                  Python 包配置和产品依赖
├── docs/
│   ├── 01_initial_experiment/       最初可行性研究，解释方案从何而来
│   ├── 02_product_mvp/             当前产品的需求、架构与使用说明
│   ├── 03_event_lifecycle_and_progress_display/  生命周期与展示改进
│   ├── 04_usability_and_localization/           易用性与多语言准备
│   ├── 05_token_usage_and_reasoning_speed/      本回合用量、推理均速历史资料及移除说明
│   ├── 06_gui_app_options_and_decisions/       GUI 目标、技术与采集方案比较、安装 / 卸载决策
│   ├── 07_remote_transport_repair/            Remote 路由修复与本机验证边界
│   ├── 08_gui_ui_ux_redesign/                 紧凑 GUI 需求、设计稿、实施与原生验证
│   ├── 09_activity_timeline/                  展开面板时间线需求、设计、事件映射、技术方案与实施计划
│   ├── 10_expanded_panel_ux/                 展开面板信息结构、原生控件及实施验证结果
│   ├── 11_agent_timeline_ux/                 子智能体时间轴设计与实施
│   ├── 12_subagent_monitoring_fixes/         子智能体归属、状态与历史加载修复
│   ├── 13_expanded_panel_round3/             第三轮展开面板设计、实施计划、最终线框与实施验证
│   └── 14_jsonl_activity_reconciliation/     JSONL 基础与实时活动合并、残留状态修复方案及验收计划
├── experiments/                    可复用实验脚本
│   ├── desktop-isolation/          隔离 Desktop / WebSocket 实验
│   │   └── runs/                   本机隔离 App 数据和原始记录，不提交
│   └── product-preflight/          实现前的采集、计数、故障验证原型
├── analysis/                       精简实验结果、验证报告与分析脚本
│   ├── upstream-stream/            上游流式增量研究
│   ├── file-edit-progress/         文件编辑与工具参数可观测性研究
│   ├── product-preflight/          实现前验证结果
│   ├── product-mvp/                产品验收结果
│   └── workspace-cleanup/          清理清单
└── .local/                         本机环境和原始数据，不提交
    ├── venv/                       产品与实验共用的 Python 环境
    ├── runtime/                    正在使用的包装器入口、socket、配置与快照
    ├── research/                   提取的 Codex 源码和协议快照
    ├── product-preflight/          实现前验证的原始数据
    └── product-implementation/     产品开发期间的原始数据与隔离界面环境
```

`experiments/` 保存“如何做实验”，`analysis/` 保存“实验结论”，`.local/` 与 `runs/` 保存“这台机器上的环境和原始数据”。原型代码不属于产品启动链。

### 哪些可以清理

| 路径 / 材料 | 能否删除 | 原因 |
| --- | --- | --- |
| `src/`、`tests/`、`bin/`、`docs/`、`analysis/` 中的提交文件 | 保留 | 产品实现、文档与验证依据 |
| `__pycache__/`、`.DS_Store` | 可以 | 自动生成的缓存 |
| `src/*.egg-info/` | 通常保留，无需手动编辑 | 可编辑安装产生的包元数据；重新安装可以生成，已被 Git 忽略 |
| `.local/venv/` | 停用后可重建；运行中保留 | 当前包装器与采集器使用它；产品和实验共用，不再单独维护实验 venv |
| `.local/runtime/` | 运行中保留 | 含包装器入口、卸载所需的原配置备份、socket 和指标快照，不全是缓存 |
| `.local/research/` | 本次保留 | 文档与分析引用的特定版本源码；删除会失去本地复查依据 |
| `.local/product-preflight/` | 原始证据保留，缓存可以重建 | `live-*`、`tools-*` 等目录含协议捕获、配置副本和实验报告 |
| `.local/product-implementation/` | 原始证据和隔离 profile 保留 | `live-*`、`bg-*`、`fb-*` 是正文、后台命令与反馈实验；`desktop-1/profile/` 是隔离 App 界面数据 |
| `experiments/desktop-isolation/runs/` | 本次保留 | 含实验登录状态、聊天、数据库及原始日志；删除不会由重新安装产品恢复 |

2026-10-12 清除了未被使用的旧实验 venv、实验 tokenizer 下载缓存和 Python 字节码缓存。实验命令现统一使用 `.local/venv`。精简报告与原始证据均保留；目录没有迁移，已有代码、文档引用和隔离启动路径继续有效。

产品入口转发原版 CLI 的 stdin / stdout，并筛选 stderr。正文与完整参数不默认落盘。实现前路线验证见 [实现前验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/README.md)。

## 数据与实验约定

- 可复用脚本、文档和精简验证结果与本机运行状态分开管理。
- 生成源码已集中到 `.local/research/`；原位置保留兼容软链接，使历史定位和文档链接继续有效。它们都被 `.gitignore` 排除。
- 隔离运行目录保留原位置，避免破坏登录环境、SQLite 和实验脚本的引用。入口见 [实验说明](/Users/bytedance/personal/codex_progress_monitor/experiments/desktop-isolation/README.md)。
- 原始日志不是可随意删除的缓存；已结束的日志可无损压缩。精简结论的复查仍可能依赖这些本地材料。
- 不提交实验登录令牌、浏览器配置、数据库、原始对话或下载的运行时依赖。

Git 忽略规则已排除本机运行状态和生成材料；其他开发者仍需按目标安装版本重新准备相应本地材料。

历史整理见 [2026-10-10 说明](/Users/bytedance/personal/codex_progress_monitor/docs/01_initial_experiment/workspace-organization.md)，本次逐项清理见 [2026-10-12 清单](/Users/bytedance/personal/codex_progress_monitor/analysis/workspace-cleanup/2026-10-12.json)。

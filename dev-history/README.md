# 开发历史与迭代记录

本目录按迭代保存需求、设计、实施计划和验收记录，开发完成后归档，不作为随版本持续更新的产品手册。阅读时以每份记录标注的日期、代码基线和状态为准；改名归档不表示其中尚未完成的验收已经通过。

产品构建与使用从仓库 [README](../README.md) 开始，参与开发见 [CONTRIBUTING](../CONTRIBUTING.md)。

| 阅读目的 | 入口 | 状态与边界 |
| --- | --- | --- |
| 给朋友提供独立开发版 | [20：自包含 macOS App](20_standalone_packaging/README.md) | arm64、最低 macOS 26；隔离打包验证与实际朋友机器验收分开记录 |
| 了解已完成 CR 和修正 | [17：系统 CR 与基线修复](17_review_and_refactor/README.md) | 代码基线 `c3996dc`；保存已发生的审查和验证 |
| 查看六项工程及实施进展 | [18：六项工程改进](18_engineering_hardening/README.md) | 当前设计、计划与实际验证；状态以本章为准 |
| 查看历史缺口与当前速度展示调整 | [19：观测缺口与当前速度](19_observation_gap_and_live_speed/README.md) | 代码、受控验证和日常 GUI／collector 更新已完成；驻留 bridge 分类更新及完整自然回合验收待完成 |
| 查看跨阶段待办 | [TODO](TODO.md) | 仅作索引，详细方案见对应阶段文档 |
| 追踪菜单栏结束延迟 | [回归调查](17_review_and_refactor/status-regression.md) | 已确认局部修正与待完成的完整验收分开记录，可继续追加证据 |
| 理解当前代码 | [已实现架构与阅读路径](18_engineering_hardening/implemented-architecture.md) | 当前工作区的所有者、协议及读写路径；日常切换边界另记 |
| 理解基线代码 | [基线架构与阅读路径](17_review_and_refactor/architecture.md) | 描述 `c3996dc` 的文件传输等路径；当前代码另见第 18 章的已实现架构 |
| 了解发行范围与待决事项 | [发行准备](18_engineering_hardening/requirements-and-decisions.md#发行准备与维护者决策) | 许可证、素材、兼容承诺、独立发行与验收另行完成 |

01–16 章保留各功能的历史需求、设计和实现记录，不批量重写为当前状态。其中近期相关背景是 [JSONL 校正](14_jsonl_activity_reconciliation/README.md)、[时间线视口分页](15_timeline_viewport_paging/README.md)和[英文支持](16_english_localization/README.md)。

新设计写入当前计划；实际实现和验证发生后另记结果与提交。历史的“计划”“最终测试”只表示当时阶段，不自动成为当前产品能力或最新验收结论。原始本机运行数据不进入公共文档。

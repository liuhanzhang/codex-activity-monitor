# Codex Desktop 进度监控：架构、数据来源与实施方案

整理日期：2026-10-10。

这组文档整理本项目的讨论、源码核查和隔离实验。目标是让使用者知道 Codex 当前处于什么阶段、是否收到新的进展、生成和执行的速度如何，从而减少等待时对“界面卡死”的疑虑。

## 阅读顺序

1. [架构与通信原理](/Users/bytedance/personal/codex-activity-monitor/dev-history/01_initial_experiment/architecture.md)：Desktop、CLI / app-server、daemon、Code Mode 与 OpenAI 服务之间的关系。
2. [状态与用量的数据来源](/Users/bytedance/personal/codex-activity-monitor/dev-history/01_initial_experiment/data-sources.md)：通信事件、日志、sessions、hooks、限额接口及开源工具的取数方式。
3. [需求、方案与验证计划](/Users/bytedance/personal/codex-activity-monitor/dev-history/01_initial_experiment/requirements-and-plan.md)：监控指标、状态表达、组合采集方案、接入决策和验收标准。

工作区的目录归属和清理记录见 [工作区整理说明](/Users/bytedance/personal/codex-activity-monitor/dev-history/01_initial_experiment/workspace-organization.md)。

## 当前判断

- 上游正文和工具参数的增量可观测；字符/s、UTF-8 字节/s 可计算。实时 token/s 需要准确 tokenizer 或服务端计数，目前只能考虑估算。
- 工具生命周期可从 app-server 事件获取；hooks 是值得优先验证的另一条入口。会话文件主要补充已完成项目和用量。
- 当前接口没有连续 reasoning token 计数；静默不能证明模型卡住。
- 优先保留 Desktop 自己启动的原版 CLI 和原有 harness。是否需要 stdio 转发器，取决于 hooks 和现有日志能否覆盖目标状态。
- 现有工作是可行性验证和方案研究，还没有完成常驻采集器、状态面板和性能验收。

## 证据如何阅读

文中使用四种标记：

- **实测**：在当前安装 CLI / 隔离 Desktop 上发生并留有记录。
- **源码核查**：安装包提取代码或指定公开源码快照支持该结论。
- **官方契约**：官方文档描述的接口与行为。
- **计划 / 待验证**：尚未完成的接入、覆盖或性能测试。

实验基线为 Desktop **26.1007.21159 / build 20052**，自带 CLI **0.162.0-alpha.17.2**。公开源码不等于该二进制的完整源码：较早实验使用 `325cf16194` 快照；本次文档整理时本地公开仓库为 `806d9732c974bc8a51b8317c1bd8985544fe627c`。旧实验说明中的源码行号可能已变化，运行结果以对应实验记录为准。

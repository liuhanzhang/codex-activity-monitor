# Antigravity Desktop 接入

记录日期 2026-11-15。实验环境为 Apple Silicon macOS、Antigravity Desktop 2.32.1，主要测试模型为 Gemini 4 Argon (High)。Monitor 接入已在 `d319ffb` 基线上实现，见 [实现与验收](implementation-results.md)。

| 想查什么 | 文档 |
| --- | --- |
| 状态流、JSONL、上游 SSE、分词误差和只读接入的依据 | [实验与结果](experiments.md) |
| 采集、数据归一、历史校对、产品复用和验收顺序 | [实现方案](implementation-plan.md) |
| 源码入口、实机验证和接入限制 | [实现与验收](implementation-results.md) |

已验证的路径是在同一 macOS 用户下，向正在运行的 Antigravity Language Server 查询会话，找到会话后独立订阅 `StreamAgentStateUpdates`。正文快照可用于估算每秒接收速度，步骤变化可用于实时活动时间线，终态用量和本地历史可用于会话统计及活动详情。命令的实际输出和退出码可按需从轨迹接口读取。 [只读接入复测](experiments.md#只读发现与订阅复测)

采集可以在现有桌面进程上进行，无需改应用配置、加启动参数、安装 Hook 或重启。相比当前 Codex 通过启动环境变量接入 bridge，这条路径减少了启动集成。Antigravity 接入使用本机内部 RPC，当前实机覆盖范围为 2.32.1。 [实现与验收](implementation-results.md) · [Codex 启动集成](../../src/codex_activity_monitor/integration.py)

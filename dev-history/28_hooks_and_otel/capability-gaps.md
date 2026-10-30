# Hooks + OTel 相对现有采集方案的功能缺口

日期：2026-10-30。适用版本：`codex-cli 0.166.0-alpha.5.1`，使用默认 WebSocket 模型连接。

比较范围：用 Hooks + OTel 替换现有 wrapper 采集的 app-server 协议和 stderr TRACE，保留现有 JSONL/state 只读功能；不修改 Codex 源码，不强制切换 SSE。下表只依据本次已验证的导出字段，不依赖尚未验证的内部 trace 重建方法。

## 无法保持的功能

| 功能点 | 缺少的数据 | 替换后的具体影响 |
| --- | --- | --- |
| **并发聊天、子代理分别显示实时速度** | WS metrics 没有聊天、回合或 item ID | 同一 app-server 内、指标标签相同的并发流被合并，无法把计数分别分配给聊天或子代理。 |
| **保持现有实时 token 测速口径** | 没有逐段正文和工具参数文本，只有窗口内 delta 事件数 | 无法继续对累计文本前缀分词。可以显示估算 token/s，但数值口径与现版不同。 |
| **在生成过程中持续更新内容详情** | 没有正文、推理摘要和工具参数的文本 delta | 无法随生成逐段展示这些内容；只能等 Hook 或 JSONL 提供相应内容。PreToolUse 提供的是已生成完的工具输入。 |
| **按模型 item 生命周期实时更新阶段时间线** | Hooks 没有逐 reasoning / agentMessage item 的开始、结束回调；本次 OTel 导出未提供等价的 item 事件 | 无法在模型 item 尚未结束时，按其 ID 建立并实时更新对应阶段。JSONL 的完成记录可用于事后补齐起止时间。 |
| **接收工具运行期间的独立进度通知** | 没有与 commandExecution/outputDelta、fileChange/patchUpdated、mcpToolCall/progress 等协议事件等价的回调 | 无法按这些通知更新执行中的输出量、编辑进度和 MCP 进度。工具调用或轮询返回时取得的结果片段仍可使用。 |

## 可以实现，不属于功能缺口

- **全局近实时估算速度**：WS metrics 在生成过程中导出事件计数和窗口时间，可以计算 delta/s。实测 247 个正文 delta 分 74 批到达；不需要等待流结束。
- **官方 token 用量、历史详情、每轮平均速度**：完成后的用量和现有 JSONL/state 读取路径仍可使用。
- **已验证的会话/回合边界、工具前后和用户中断**：可通过 SessionStart、SessionEnd、UserPromptSubmit、Stop、PreToolUse、PostToolUse、Interrupt 接收；中断不能只依赖 Stop。

## 尚未验证，不列为无法实现

所有审批和工具路径、子代理关系与完整生命周期、异常终止覆盖，以及利用完整 trace 父子关系重建实时归属。这些项目需要单独验收。

依据：[调研与实验记录](research-and-experiments.md)、[脱敏实验数据](evidence/results.json)。现有实现对照：[事件入口](../../src/codex_activity_monitor/events.py)、[实时状态处理](../../src/codex_activity_monitor/engine.py)、[时间线与实时详情](../../src/codex_activity_monitor/timeline.py)。

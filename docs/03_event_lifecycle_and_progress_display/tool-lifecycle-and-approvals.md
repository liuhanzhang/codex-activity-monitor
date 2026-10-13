# 工具生命周期与审批状态改进

状态：2026-10-12 已核对事件与制定修复计划；本文中的生命周期改进尚未实现。工具名称与命令预览的已有改动见 [工具活动名称与命令显示](tool-activity-display.md)。

面向用户的呈现以新的 [事件消费与展示决策](event-display-decisions.md) 为待 review 方案：人工 / 客户端审批及用户输入仍在内部消费，界面不重复 Desktop 的操作提示；自动审核作为实际工作过程展示“自动审核中”。本文保留生命周期证据，并据此更新下方修复方案。

完整事件清单及当前消费 / 展示范围见 [App-server 事件盘点](app-server-event-inventory.md)。该盘点进一步定位了协作活动类型、配置字段层级和请求清理的实现缺口。

## 已确认的事实

### 模型参数完成与工具执行结束是不同事件

`response.output_item.done` 中的 `custom_tool_call` / `function_call` 表示模型已生成完整调用内容。运行时的 `item/started`、`item/completed` 才描述相应工具活动的生命周期。不能用参数完成事件代替执行结束事件。

本地 `.local/product-implementation/fb-3/protocol.jsonl` 中，同一命令 ID `exec-4501f753-955a-4818-9ff9-fdb50f434364` 收到：

- `item/started`：`commandExecution`，`source=unifiedExecStartup`，`processId=14399`，`status=inProgress`。
- `item/completed`：`status=completed`，`exitCode=0`，`durationMs=19981`；采集时间间隔约 19.98 秒。

因此，不能将“执行未确认”解释为 CLI 没有工具开始、结束事件。

### 命令活动开始可能早于进程启动

[OpenAI 官方审批协议](https://learn.chatgpt.com/docs/app-server#approvals) 描述的命令审批顺序为：

1. `item/started`：建立待审批的命令活动。
2. `item/commandExecution/requestApproval`：向客户端请求决定。
3. 客户端返回审批结果。
4. `serverRequest/resolved`：请求已回答或清理。
5. `item/completed`：命令活动最终结束。

本地 `.local/product-preflight/approval-20261011/capture.jsonl` 第 142–146 行验证了该顺序：开始项的 `source=agent`、`processId=null`，客户端返回 `decline`，最终 `status=declined`。这里的开始项不能证明命令已经执行。

`serverRequest/resolved` 本身不表示批准：拒绝、取消或请求清理也会产生该事件。批准也不表示进程已启动。

### 客户端审批与自动审核各有事件

从本机实际安装的 CLI `0.162.0-alpha.17.2` 导出实验协议 schema，确认以下事件存在：

| 路径 | 事件或消息 | 可确认的含义 |
| --- | --- | --- |
| 客户端审批 | `item/commandExecution/requestApproval` | 等待客户端提供决定；普通交互场景通常由用户确认 |
| 客户端回复 | 同一 JSON-RPC 请求 ID 的结果 | 可读取批准、拒绝或取消等决定 |
| 客户端请求结束 | `serverRequest/resolved` | 请求已回答或清理，不代表批准 |
| 自动审核 | `item/autoApprovalReview/started` | 自动审核开始 |
| 自动审核 | `item/autoApprovalReview/completed` | 自动审核结束，结果在 `review.status` |

自动审核结果包括 `approved`、`denied`、`timedOut`、`aborted`；开始时为 `inProgress`。审核通过不能替代实际工具启动证据。

自动审核通知携带 `reviewId`、可空的 `targetItemId`、`threadId`、`turnId`；结束通知还包括 `decisionSource`。安装版 schema 中决策来源为 `agent`。本地 Codex 源码的 Guardian 路径使用独立审核会话和模型请求，不是要求正在完成用户任务的模型自行批准自己的操作。它也不能代表所有审批都由模型完成：普通客户端审批与自动审核是不同路径，实际使用取决于配置。

schema 导出命令：

```sh
/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex app-server generate-json-schema --experimental --out /private/tmp/codex-monitor-protocol-20261012
```

协议形状由安装版验证；Guardian 内部实现参考本地 `/Users/bytedance/playground/codex`，未证明该源码提交与安装二进制完全一致。上述三份已有捕获均未包含自动审核事件，因此尚未验证 Desktop 自动审核通知在当前采集链中实际到达。schema 支持不等于当前运行配置会启用此功能。

## 当前 monitor 的问题

- `engine.py` 将上游参数完成后的外层 `exec` 留在 `tool_ready`；内部工具以另一个运行时 ID 创建活动。当前没有可靠的父子关联，外层活动可能保留“工具准备 / 执行未确认”，内部活动则已确认执行。
- 命令活动已有进程与输出证据判断，但普通工具的开始、审批和执行阶段需要进一步明确，不能将所有 `item/started` 都视为实际操作已开始。
- `events.py` 允许 `item/` 通知通过，但没有保留自动审核的 `reviewId`、`targetItemId`、`review.status` 等字段，`engine.py` 也没有对应状态处理。仅允许事件名称通过不等于完整支持。
- 客户端审批目前主要依靠回复推进状态。只收到 `serverRequest/resolved` 时，需同时结束对应等待状态，保留“决定未知”，不能推断已批准。
- 审核不能只按目标工具 ID 保存：同一命令可能有多个子命令或 stdin 审核，网络审核的 `targetItemId` 还可能为空。应按独立请求或审核 ID 管理。

## 修复方案

1. **分开保存调用参数、审核与执行活动。** 参数流继续提供工具名及“拟执行”命令摘要，不承担执行状态判断。客户端审批按后端和请求 ID 管理；自动审核按后端和 `reviewId` 管理，并记录明确的目标 ID。
2. **补齐自动审核的最小字段。** 保留审核身份、目标身份、状态、决策来源、时间和动作类别；需要命令展示时沿用有界命令摘要，不保存完整审核理由或任意参数正文。桥接缓存与重连恢复也需处理审核开始、结束事件。
3. **自动审核展示过程，客户端审批只维护阻塞。** 自动审核期间显示“自动审核中”，并行时在可见活动中保留；结束后清理审核阶段，详情可保留简短结果。客户端待决只有在所有前台工作确实被阻塞时显示“已暂停”，不显示问题、理由、按钮或提醒。通过后仍无执行证据时使用“处理中”，不把批准写成运行。请求清理而决定不可见时，内部保留未知并结束对应阻塞。实际执行显示具体用户动作，最终按工具完成事件记录结果。
4. **让真实工具活动决定主状态。** 内部工具已有执行证据时，主状态显示可读工具动作与实际命令。并行工作仍运行时，不让其他工具的审批等待覆盖它。外层参数 / Code Mode 活动只保留内部上下文，默认不展示 `exec → exec_command`；准备阶段继续显示可识别的拟执行命令。这可以先实现，不依赖完整父子关联。
5. **父子关联只使用明确身份。** 不按工具名称、命令相似性或时间接近猜测。当前命令协议字段未提供直接外层 `exec` 父 ID；本地源码内部保留 cell 与调用来源，但尚未确认当前采集接口能稳定取得它们。无法关联时保持独立活动，外层未知状态保留在内部，用户界面以已确认的实际工作为准。
6. **分别处理调用返回与后台运行。** Code Mode 可以 yield 后继续运行，命令也可以后台执行。新模型输出或一个内部工具结束不能证明整个 cell 已结束。后续若取得外层运行时结果或 cell 生命周期，需要分别记录调用返回、cell 结束和进程结束。

## 需要验证的边界

- 自动审核在当前 Desktop 配置下是否启用，以及通知是否穿过包装器、桥接缓存和采集器完整到达。
- 审批前已发送命令开始项后，实际启动通知可能被去重；静默命令若没有新的进程事件或输出，能否从其他运行时证据及时确认执行。不能承诺只消费现有 `item/started` 就覆盖该情况。
- 外层 `exec` 与内部调用的稳定身份映射是否有当前版本可用的观测接口；没有时先交付独立、准确的内部工具状态。
- 多个审核针对同一命令、无目标 ID 的网络审核、并行调用、yield / wait、跨回合后台进程，以及采集器重连时的状态恢复。

## 实现验收计划

- 回放已有命令启动 / 完成记录和审批拒绝记录，验证参数完成、审批、实际执行与结束不会互相替代。
- 使用安装版协议形状验证自动审核开始、通过、拒绝、超时、取消，以及请求清理但决定缺失的状态转换。
- 验证普通界面不重复人工 / 客户端审批及用户输入提示；自动审核过程可见且正确结束；存在实际并行工作时不被另一个请求的等待状态覆盖。
- 覆盖同一目标多个审核、网络审核无目标、乱序 / 重复事件、静默命令、并行调用与后台进程。
- 在隔离后端验证真实可触发的审批路径；自动审核若无法触发，明确保留未验收范围，不将 schema 或模拟事件测试写成真实运行验收。
- 完成这些检查后再重载运行中的 monitor；本轮仅记录方案，不重复宣称此前工具名称与命令预览已覆盖新的生命周期行为。

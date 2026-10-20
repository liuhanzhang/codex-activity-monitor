# 实施结果

日期：2026-10-20。源码基线：`728af1e47d84`。F01–F04 已实现并加载到日常 Monitor。本章记录实施验证时的结果；代码、测试与文档随后按用户要求一并提交。脱敏机器可读结果见[验证摘要](assets/validation.json)。

## 1. 实际改动

| 问题 | 实现与结果 |
| --- | --- |
| F01：父子关系与最近完成 | 新增 `agent_relations.py`、`agent_identity.py`；只接受校验后的同 scope header 父身份或明确创建调用。普通回信、等待、完成事件不建立父子边；冲突边不能被旧 spawn 回退重新启用。Python 菜单计数与 Swift 列表先按结构归组，再对最近完成取十个主组。已知但归属缺失的 child 进入“归属待确认的子任务”，保留历史入口，不计入主聊天数。 |
| F02：结束状态与记录质量 | `scoped_status` 允许有明确终止记录的轮次在仅缺少 item end 时显示结束；新增组级 `activity_quality`，主组与子组都提示记录不完整。缺失阶段继续为 unknown，保留原时间和 not_retained。可靠活动、追加轮未结束、断连及身份／生命周期冲突继续保守降级。 |
| F03：真实协作动作 | 正式 collaboration namespace／完整工具名加匹配 scope、thread、turn、call ID，为原有 raw 参数阶段补充派发、发送消息、追加任务及对象。参数生成保留模型轨、kind 和颜色，不合成额外工具阶段。bundle 与 detail 使用同一投影；普通同名工具、代码字符串不识别为协作。 |
| F04：全局等待 | 正式 `collaboration.wait_agent` 的完整参数结构只有可选超时字段时，标记全局等待并显示“等待子智能体消息”。只有 canonical 空接收对象列表时仍显示对象未知。全局等待不绑定全部 child，详情标题不重复显示等待对象。 |

改动覆盖 Python engine／overview／collector／timeline／query／storage，以及 Swift Snapshot／DashboardStore／DashboardView／Timeline／TimelinePanelView／ActivityViewer 和中文资源。SQLite 只扩展有限 metadata allowlist，未改表结构、清库或重写 Codex JSONL。

身份读取在有界单后台 worker 中进行：队列最多 128、结果与最小上下文最多 512；只读首行最多 64 KiB，缓存至少间隔两秒重查。快照路径不等待 header IO，未命中先降级。仍可显示的 child 所需过期父上下文只保留元数据，释放 activities；查询每个不同线程每次最多加载一次完整历史。

重载兼容检查补充了已观测 user 聊天标题／cwd 的最小上下文保留，避免恢复活动缓存到期后回退为短 ID／空路径；关闭快照也复用身份补充。两个已丢失元数据的 user 聊天仅从实施前快照恢复名称与 cwd，并重新校验相同 scope／ID 的 user header；没有恢复旧活动、状态或指标。子任务昵称不从失效 header 的旧上下文沿用。

当前按轮查询、默认轮次、统计归属与两个窗口布局模式保持原约束。用户排除的“当前轮默认不显示前轮 child”没有作为问题修改。

## 2. 自动检查

通过数按不同用例计算，重复运行不累加：

| 范围 | 通过数 | 覆盖 |
| --- | ---: | --- |
| Python timeline／query／storage | 39 | 身份与时间、按轮归属、raw 详情、重复关系加载边界 |
| Python history | 21 | 历史追加、替换、身份、内容缺失与分页 |
| Python overview／menu | 21 | 显式父边、跨 scope、循环、独立流汇总及主聊天数 |
| Python engine | 30 | 现有状态、活动归属与恢复兼容 |
| Python runtime／app_session | 21 | collector 生命周期、暂停重连、协议透传及恢复 |
| Python 新增真实结构回归 | 9 | 回信、冲突回退、缺失阶段、正式工具名、后台身份 IO、父上下文释放、标题作用域及协作详情 |
| Swift MonitorModel | 39 | 回信归组、十主组限额、待确认入口、全局等待模型轨及既有模型回归 |
| Swift MonitorApp | 39 | 归属后补时保留 TimelineStore／展开状态、现有详情与时间线交互回归 |

合计 **141 个 Python、78 个 Swift 用例通过**。另一个需要 WindowServer 的自动窗口用例跳过；下面的原生定向操作单独记录，不能算成该自动用例通过。

执行命令使用以下形式，Python 按表内相关文件分别执行，Swift 先跑两个 target 后对最终改动做定向回归：

```sh
PYTHONPATH=src .local/venv/bin/python -m unittest discover -s tests -p 'test_timeline*.py' -v
PYTHONPATH=src .local/venv/bin/python -m unittest discover -s tests -p test_agent_fixes.py -v
swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos --filter MonitorModelTests
swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos --filter MonitorAppTests
bin/build-gui --preview
bin/build-gui
```

最终 collector 关闭快照修改后重跑 app_session 六项；正式全名兼容和冲突回退修改后重跑新增九项与 overview 九项，均通过。Swift 最终改动已构建，并通过受影响的模型／App 定向用例。另完成 Python AST、中文 strings、Markdown 本地链接与 `git diff --check` 检查。

## 3. 同一来源的修复前后对照

初次实验固定来源样本修复前后均为 **190 个阶段、另有 1 个用户输入标记**。逐项核对 phase ID、thread／turn、item、namespace、item_type、track、kind、start／end 均一致。主组从 unknown 改为 completed，并带 missing_item_end 提示；对应阶段仍 unknown、结束时间缺失，未补造内容。三个 child 仍为 completed；单独查询回信 child 时只有一个组，不再把父级当作其 child。

该样本的展示注解阶段数为 spawn/specified 3、send/specified 8、followup/specified 1、wait/global 16。这里统计的是阶段，raw 与 canonical 可以各有一个，**不等于实际调用次数**；16 个等待阶段来自 8 次等待。

使用基线源码与新源码对同一份私有来源／元数据分别查询四类详情：function_call、collabAgentToolCall、失败 commandExecution、custom_tool_call。四者的 state、source、sections、attachments 一致；前三类 available，最后一类仍 not_retained。此结果只证明这四个样本未改变原始内容，不声称恢复了源历史未保留的数据。

同机该固定样本首次查询 0.4067 秒，后三次 0.0490／0.0662／0.0499 秒。该数字是特定本地样本，不是延迟承诺。后台慢 header 读取的隔离检查确认 enrich 仍可从缓存返回；失败刷新会移除旧 header 父身份。未做长期 CPU 测量或系统级目录扫描／header 读取次数基准。

## 4. 原生定向结果

以下为原生 macOS 应用显示合成 fixture 的截图，不能作为自然运行证据。样例覆盖结束主组＋部分记录、三个结束 child、最近完成主组计数及全局等待详情。归属待确认入口另由可访问性树与模型／App 用例核对，位于截图滚动区域下方。预览不会读取日常数据；顶部 48 T/s 是合成值，不作为速度或用量验证证据。

| 画面 | 结果 |
| --- | --- |
| [浅色面板](assets/panel-light.png) | 最近完成为一个主组，主组显示结束与部分记录，child 在组内 |
| [深色面板](assets/panel-dark.png) | 同一已结束主组和组内 child 的深色展示 |
| [等待详情](assets/wait-detail.png) | 全局等待标题无对象未知／重复等待；参数与结果页签显示 fixture 原文 |

实际操作了鼠标展开、键盘 Tab／Space 折叠子组，打开独立 Activity Viewer，并切换参数／结果页签。分页／复制与选择状态复用既有自动回归；未重新执行整套窗口尺寸／生命周期或完整 VoiceOver 验收。截图覆盖的可见布局与最终版本一致；期间最后的 orphan 未知状态保留与关系校验没有改变这些已完成样例画面。

## 5. 真实运行与日常更新

更新日常版本后创建三个真实子智能体，让它们分别执行多步只读工具任务、向父级回信并完成；其中一个完成后通过 followup_task 追加了一轮。三个 child 在各自明确 turn 上结束，轮次数分别为 2／1／1，均有已验证 session_header 父身份，无 parent 冲突；分别查询 child 视角都只得到自身组。

真实 bundle 中可见 spawn、send、followup 与 global wait 注解。原生日常面板显示三个 child 都在当前父聊天内，均为“本轮已结束”，没有对应独立 `monitor.chat.<child>` 行。另一聊天同时存在，因此没有用全局“正在进行”／“最近完成”数字推测本次归属。真实画面和完整查询只存入忽略目录。

**自然运行边界：当前父聊天在本结果写入时仍未结束，不能确认它随后自然进入“最近完成”的最终画面。** 该路径已经通过 Swift 最近完成归组／数量测试及原生合成样例。真实验证支持“child 完成后仍留在活动父聊天内”和“回信／复用不生成错误组”，不能扩大为父聊天最终结束转换已自然验收。

collector 在父轮中途重载，来源保留 observation_gap；整轮缺失耗时／用量继续显示未知，不按结束 child 或新快照补齐。快照 root turn_id 曾为空时，验证使用查询返回的 selected_turn_id，没有据此猜测轮次。

日常 GUI 通过现有构建与正常退出／启动流程更新，没有重启 Codex、建立额外 writer 或更换接入。最终检查：

- GUI build SHA-256：`debb7232cf90cd35a0d15f20bbab782505259b3d2ee5565b1c3987dc049ad56c`，与当前 Swift sources 匹配。
- collector source SHA-256：`f00438ad1941c5e0504a4138dedd0de7fbf1d464c7331f40d6ccc4d33da861fc`，加载的 20 个 Python 模块均匹配。
- 运行中、timeline 可用、projection_errors 为 0；检查时快照年龄约 0.07 秒。它是一次观测，不是新鲜度 SLA。
- integration 的 `id / previous / legacy_backup / legacy_agent_backup / installed` 字段哈希与实施前一致；恢复备份保存在 integration 内，未复制其内容入 Git。
- 测试预览进程与 fixture writer 已结束；日常 Monitor 保持运行。此次交付只做本地提交，不推送。

## 6. 证据保留与后续边界

Git 仅保存本章文档、合成数据的原生截图和脱敏 [validation.json](assets/validation.json)。完整 JSONL、SQLite、快照、真实原生画面、scope／thread／turn 对照和原始详情留在 Git 忽略的 `.local/subagent-monitoring-fixes/`，包括 `source-comparison.json`、`detail-comparison.json`、`live-final-summary.json`、`live-native-final.*`、`daily-final-summary.json`；未把私人正文、工具输出或恢复备份写入版本库。

本轮未验证：父聊天本次自然结束后的最终入口、长时间资源占用、全量辅助功能、干净机器、签名公证与分发环境。没有更改用户已排除的跨轮展示行为，也没有新增待用户决策的产品选项。

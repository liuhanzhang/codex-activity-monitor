# 实施结果与验收边界

日期：2026-10-22。状态：本章修复已实现，完成定向回归、隔离 collector、原始来源对照及有限日常检查；日常 collector 已加载最终代码。实现前基线：`5579bd527a0f`。本文记录提交前的验证结果，提交信息以 Git 历史为准。

本文为首轮实施记录。其后发现的实时模型补充未退出历史展示问题，以及 R13 的实现、验证和加载结果，另见[实时补充退出规则](live-supplement-retirement.md)；首轮 232 项通过不表示当时已覆盖该问题。

## 1. 最终行为与代码

仍采用本章确认的简单规则：以 JSONL 中的记录为基础，补充实时观测。现有 `Engine` 保存实时补充和身份匹配的历史事实；快照、总速度及时间线不再只有查询时才知道命令已经结束。

| 位置 | 已实现内容 |
| --- | --- |
| [`history.py`](../../src/codex_progress_monitor/history.py) | canonical 完成摘要增加明确终止标记和可用退出码；提供无正文的校正摘要。文件替换、截断或重写会产生新的索引 generation；查询使用分离的索引视图，避免后台更新其正在使用的对象。 |
| [`reconciliation.py`](../../src/codex_progress_monitor/reconciliation.py) | 有界后台读取，候选按数据源和线程去重；未决项约每秒读取，完成项先快速重试、之后退避。队列轮转避免容量用尽时始终跳过后面的线程。文件 I/O 不占用实时事件锁。 |
| [`collector.py`](../../src/codex_progress_monitor/collector.py) | 与时间线查询共享历史 provider，在发布快照前应用就绪摘要；面板关闭同样有效。重启恢复未决身份并继续从 JSONL 校正，不恢复旧速度，也不保留另一份永久权威快照。 |
| [`engine.py`](../../src/codex_progress_monitor/engine.py) | 按 scope、thread、turn、item 类型和 ID 合并；JSONL 完成覆盖残留运行状态。区分正在工作与未知是否仍在工作，保留真实后台进程、较新的回合及其他不可靠活动。源时间独立保存，不写入单调时钟字段。 |
| [`metrics.py`](../../src/codex_progress_monitor/metrics.py) | 历史确认终止后停止该流的实时贡献、拒绝后续追加并释放已完成计数的临时文本；没有实时结束时间时不补造流式耗时或速度。 |
| [`overview.py`](../../src/codex_progress_monitor/overview.py)、[`menu_presentation.py`](../../src/codex_progress_monitor/menu_presentation.py) | 总速度与菜单状态采用相同的已结束流判定，历史确认结束的流不再计为当前生成。 |
| [`timeline.py`](../../src/codex_progress_monitor/timeline.py)、[`timeline_query.py`](../../src/codex_progress_monitor/timeline_query.py) | 对已经观测的阶段应用同一历史完成事实；历史时间优先于接收时间，保留缺口和终止冲突。完整历史和原始内容仍按需读取。 |
| [`progress_state.py`](../../src/codex_progress_monitor/progress_state.py)、[`timeline_storage.py`](../../src/codex_progress_monitor/timeline_storage.py) | 未决活动显示状态未知；sidecar 只扩展必要元数据 allowlist，没有迁移表结构或复制正文。 |

Swift 源码、GUI 布局、官方用量账本和 Codex writer 路由没有改动。快照中新来源字段为补充字段，原生界面继续使用原有状态和耗时字段；本次用正在运行的 GUI 检查了解码及查看器兼容，没有重建 App。

历史读失败保存在单独的 `history_quality` 中，不把“暂时没有历史文件”当作实时流断连。身份／生命周期冲突和未知活动仍按其实际影响保留；并非清空整个会话的质量集合。

## 2. 测试结果

最终一次同组定向回归的 232 项相关测试全部通过，不叠加此前重跑次数：

| 测试范围 | 用例数 |
| --- | ---: |
| 新增 JSONL 合并、恢复、后台读取与队列测试 | 22 |
| Engine | 30 |
| History | 21 |
| Timeline、TimelineQuery、TimelineStorage | 39 |
| Overview、MenuPresentation | 21 |
| Usage | 30 |
| Progress semantics、Agent fixes | 33 |
| Integration、File editing | 18 |
| Runtime，含新增 3 个正式 collector 场景 | 18 |
| 合计 | 232 |

修复期间补充了队列公平性、字段缺失、同 inode 截断、未缓存的异常 header，以及历史结束流在后续 gap／模型切换／迟到完成后保留计数的边界用例；最后重新执行同一组相关模块，232 项通过，用时约 34.33 秒。没有扩展到无关全仓测试或完整 GUI 布局验收。

整组定向回归命令：

```sh
PYTHONPATH=tests .local/venv/bin/python -B -m unittest \
  test_reconciliation test_engine test_history test_timeline test_timeline_query \
  test_timeline_storage test_overview test_menu_presentation test_usage \
  test_progress_semantics test_agent_fixes test_integration test_file_editing test_runtime
```

最终代码经过语法及 whitespace 检查，文档链接亦单独检查。

## 3. 要求与验证对应

以下覆盖[修复规则](README.md)的 R01–R12 及[验收计划](validation-plan.md)的 V01–V17。测试与受控进程证明对应条件，不表示所有条件都在自然使用中发生过。

| 要求 | 主要证据 |
| --- | --- |
| R01、R03；V01、V02 | `test_missing_completion_is_corrected_without_query_or_usage_replay`；正式 collector 的 `test_history_corrects_without_timeline_request`；修复原始来源的生产引擎对照 |
| R02；V05、V06 | `test_identity_type_and_scope_are_not_guessed`、`test_only_canonical_completion_closes_process`；参数、外层返回不被当成内部命令完成 |
| R03、R04；V03、V04 | `test_realtime_completion_precedes_history_and_late_start_does_not_reopen`，重复应用摘要及现有生命周期重放测试 |
| R04、R11；V15 | `test_missing_history_fields_preserve_valid_realtime_fields`、`test_conflicting_terminal_sources_are_reported`；时间线合并保留相应终止冲突 |
| R05、R06；V07 | `test_background_command_survives_turn_end_until_own_completion`；回合终止不会结束可靠后台命令 |
| R06；V08 | `test_old_completion_leaves_new_turn_and_generation_intact`；慢读 collector 场景中先接收新回合，再补入旧命令完成 |
| R07、R11；V09 | gap／断连后已知活跃数为零，但未决工作继续使总速度未知；新回合、可靠新进度和历史完成分别处理，不因静默自动判成功 |
| R08、R09；V10、V11 | `test_unrelated_unknown_work_still_blocks_total`、新回合生成及历史结束流计数保留用例、原始来源对照；流结束不回放为 delta，官方 Usage 的 30 项回归及用量未改变断言通过 |
| R10；V13 | 后台去重、慢读、队列公平性用例；`test_blocked_history_read_does_not_block_collector_events` 用独立正式 collector 实际阻塞历史读取，同时确认新回合和快照继续处理 |
| R11；V12 | 半行后追加、文件缺失、替换、同 inode 截断、分离视图；现有 History 的 header 身份和替换／读取失败检查 |
| R12；V14 | `test_historical_clock_does_not_refresh_old_retention`；十小时前的源时间不刷新最近完成期限，来源时间和单调时钟分开处理 |
| R01；V16 | `test_snapshot_timeline_and_original_detail_agree`；实际日常只读查询及原生面板、活动查看器参数／结果页检查 |
| R10、R12；V17 | `test_restored_unknown_identity_uses_history_without_new_turn`；正式 collector 的 `test_history_corrects_restored_unknown_without_starting_new_turn` |

## 4. 原始问题的实际来源对照

使用重载前的日常快照及之前已确认的原始 JSONL，在隔离的正式 `Engine` 中恢复同一未决身份并应用生产历史摘要，没有往日常 producer socket 注入事件：

| 项目 | 结果 |
| --- | --- |
| 原始命令与来源身份 | 与前次证据指向同一 canonical item |
| 合并前 | 仍被算作未决工作 |
| 合并后 | `status=completed`、`active=false`、`exit_code=0`，不再计入当前工作 |
| 来源时间 | 开始与结束均来自原始记录，区间为 0.353 秒；未把读取时刻当成结束时刻 |
| 时间线 | 同一命令显示 completed，结束时间与快照来源时间一致 |
| 质量 | 原有观测缺口保留，没有重建当时的速度 |

0.353 秒是来源起止时间差；源命令报告的执行耗时约 0.350 秒，两者不作强行相等处理。

该对照证明正式合并逻辑能纠正真实问题。日常重载后的旧线程已不在当前列表；本章没有用“重启后看不见它”作为纠正逻辑生效的唯一证据。

## 5. 日常加载与原生检查

日常 collector 从本轮开始时的 PID `84710` 更新为最终 PID `28884`，实现过程中有多次版本重载。最终快照内所有模块指纹与工作区一致，`timeline_projection_error=0`。PID 只是此次观察的进程身份，不是后续运行的固定要求。

日常只读 `tl.sock` 查询原始命令，返回 completed 及正确源起止时间。原生面板可以选择当前聊天并显示完成／进行中的活动；独立活动查看器可打开已完成活动的原始参数、原始结果及耗时。实际检查的原生详情是当时可访问的另一条已完成工具活动，原始旧命令超过最近窗口，不能把这次原生观察说成直接点击验收了旧命令。

本轮重载保留正在进行的回合的真实观测缺口。最终日常总速度仍为 `—`，对应当前两个回合的不完整覆盖，不是旧 `rg` 仍在运行；没有为消除提示清除这些质量标记。下一次完整新回合的自然恢复没有在本次任务中单独制造或等待，受控新回合与生成流场景已定向验证。

## 6. 本机证据与限制

Git 忽略目录 `.local/diagnostics/jsonl-reconciliation-20261022/` 保留：

- `daily-before.json`：重载前来源快照。
- `real-source.json`：真实来源在生产引擎中的隔离对照。
- `daily-transition.json`：第一次重载的采样；旧命令未在新进程采样中出现，不能单独证明其完成状态被处理。
- `daily-original-query.json`：重载后原始命令的只读查询结果。
- `daily-final.json`：最终加载身份、模块指纹、质量与汇总。
- `native-panel.ax.txt`、`native-viewer.ax.txt`、`native-viewer.png`：有限原生检查。

这些材料包含私人会话元数据或界面内容，不纳入 Git。可行性原型仍保留为历史实验，其直接更新实验状态的适配器不是产品入口，不能替代本章正式测试。

本轮没有改造采集队列的高流量可靠性，没有证明最初具体哪条帧如何丢失。任意规模首次索引、更多 Codex 版本、Remote 历史、长时间运行和发行验证未覆盖；这些是既有范围外限制，不将定向通过扩大为全面兼容承诺。

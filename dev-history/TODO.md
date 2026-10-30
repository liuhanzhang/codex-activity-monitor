# 待办索引

更新：2026-10-30。核对基线：`main` / `d0aa506`。本页维护跨阶段的未完成工作；技术方案和验收证据分别见各章。历史记录中的“未发布”“尚未合入 main”只代表当时状态，不直接作为当前待办。

## TL01：时间线数据保留与历史展示不一致

**P1，已确认，待修复。** 2026-10-30 的真实历史回看中，首轮约 2:03–8:50 出现空白；对应的长工具参数生成活动仍可找到，但历史查询返回 `start_ms=null`、`end_ms=null`、`start_unknown`。现存数据不足以还原第一条 delta 的时刻，也不能证明整个空白区间都在连续生成。

原因与影响：

- 展示层允许通过 Codex JSONL 回看旧回合；清理层却把部分无法从 JSONL 重建的旁路时间信息当作可淘汰缓存。
- 当前普通保留条件是“属于该聊天最近 6 轮，并且（回合 active 或距 `updated_ms` 不足 10 分钟）”；活跃阶段、10 秒详情保护和父子关系另有保留例外。未保留的阶段、回合及相应关系会从内存和 SQLite 一起删除。见[清理实现](../src/codex_activity_monitor/timeline.py)。
- Codex 的原始 `function_call` / `custom_tool_call` 保存完整参数，没有参数生成起止时间；审批等待、连接恢复和 Monitor 观察缺口等也不能依赖 JSONL 完整恢复。删除这些旁路记录后，旧回合仍可打开，但部分区间无法重建。这是保留与展示契约不一致，不能解释成“该时段没有活动”。

待办与验收条件：

- 分离内存缓存淘汰与历史元数据保留；对 JSONL 无法重建的活动，持久保存稳定身份、类别、已有的起止时间及缺口信息，不另存完整正文。
- 单独定义磁盘预算和保留策略，并让可回看的范围、覆盖状态与实际保留数据一致；缺失时间明确标记未知，不推测起点或补造连续活动。
- 覆盖超过 10 分钟、超过 6 轮、collector 重启、详情保护过期及跨轮活动的回归；已保留的工具参数生成、等待和缺口区间在回看时仍可用。
- 保留的旁路数据继续遵守当前 rollout、回退和分叉边界，不复活已丢弃分支。已经删除且无其他证据的数据不作精确恢复承诺。

本项只登记问题和修复目标，尚未修改产品代码；不要与已修复的[旧 backend 保留缺陷](25_settings_speed_integration/collector-retention-fix.md)混为一项。

## 其他未完成工作

| 工作 | 当前状态与剩余范围 | 依据 |
| --- | --- | --- |
| EH01 / EH03 / EH04 / EH06 的自然与长期验收 | 实现和受控验证已完成。继续核对完整自然回合、详情分页、日常规模、资源回落及超长诊断行／bridge RSS 异常；菜单栏结束延迟的原始因果调查仍未完全闭合 | [六项工程状态](18_engineering_hardening/README.md)、[菜单栏调查](17_review_and_refactor/status-regression.md)、[后续速度展示](19_observation_gap_and_live_speed/implementation-results.md) |
| 历史兼容 RH03–RH06 | 暂缓：压缩历史、特殊子代理继承边界、旧格式回退标记，以及部分活动类型映射；按第 27 章的重新评估条件推进 | [第 27 章](27_rollout_history_compatibility/README.md) |
| 发行与跨环境验收 | macOS 14 实机、首次下载 Gatekeeper 流程、Developer ID 签名／公证、自然长期使用仍未完成；继续验证真实接入、Codex 升级／崩溃恢复、VoiceOver 和多显示器。Remote 不在当前支持范围，扩大范围后再安排相应验收 | [alpha.5 验收边界](27_rollout_history_compatibility/alpha5-release.md)、[打包指南](../packaging/README.md)、[用户指南](../docs/user-guide.md) |
| 素材及公开资料复核 | MIT 许可证已确定；第三方／外部素材分发范围，以及公开文档与 Git 历史的隐私、来源材料复核仍需单独闭合 | [发行准备](18_engineering_hardening/requirements-and-decisions.md#发行准备与维护者决策) |
| Hooks + OTel 备选接入 | 调研和隔离实验已完成，未替换产品架构。先决定功能取舍；若继续方案，再验收真实 Desktop 的插件安装、信任、既有聊天刷新和故障行为 | [功能缺口](28_hooks_and_otel/capability-gaps.md)、[调研记录](28_hooks_and_otel/research-and-experiments.md) |

以上未验证项不能因为源码测试、隔离实验或版本发布通过就标为完成。每轮平均速度、设置和诊断的长期使用边界分别见[第 24 章](24_average_token_speed/README.md)与[第 23 章](23_settings_and_diagnostics/README.md)。

## 已完成，不再列为实现待办

- EH01–EH06 的代码实现和受控验证已有记录；EH02、EH05 不再重复安排同一轮实现。未闭合的自然／长期验收列在上表，阶段细节仍由[第 18 章](18_engineering_hardening/README.md)维护。
- 项目已采用 [MIT 许可证](../LICENSE)，已完成改名、原生设置／诊断、每轮平均速度及其集成。旧 TODO 的 `c3996dc` 基线不再代表当前实现状态。
- 独立 arm64 DMG、macOS 14 最低部署目标、CI、Sparkle 更新检查及公开更新源均已落地，RH01/RH02 已随 alpha.5 发布，见[发布记录](27_rollout_history_compatibility/alpha5-release.md)。更新下载／安装按当前产品选择手动完成，不将自动安装列为缺陷。

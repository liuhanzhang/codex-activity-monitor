# Codex 历史路径切换与继承读取

日期：2026-10-29，Asia/Shanghai。

状态：RH01、RH02 已实现并随 [alpha.5 发布](alpha5-release.md)；发行 CI 的 580 项 Python 测试、Swift 默认套件及最终 DMG／原生客户端验收通过，更新清单已上线。RH03–RH06 继续暂缓。原始调查、范围和计划保留于本章；实际实现、早期日常安装及 bridge 边界见[实施结果](implementation-results.md)。

代码基线：

- Monitor：`61f751d25ae57e8cfe798bba73402235b96d1fca`。
- 本地参照的 `openai/codex`：`9def09afa71e1443a1028866ac22dc82aedea00e`。以下上游设计结论对应这个版本，不自动适用于其他版本。

## 阅读入口

| 目的 | 文档 |
| --- | --- |
| 问题、证据、优先级和本轮范围 | 本文 |
| 路径解析、继承范围、缓存及失败处理 | [技术方案](technical-design.md) |
| 实施顺序、回归场景及完成标准 | [实施计划](implementation-plan.md) |
| 已实现行为、验证证据及剩余边界 | [实施结果](implementation-results.md) |
| alpha.5 发行、最终包及公网更新验收 | [发布结果](alpha5-release.md) |

相关背景：[历史 JSONL 校正](../14_jsonl_activity_reconciliation/README.md)、[历史读取与缓存架构](../18_engineering_hardening/implemented-architecture.md)、[每轮平均 Token 速度](../24_average_token_speed/README.md)。

## 用户问题与调查结论

日常使用中，Monitor 能显示新的活动时间块，但点击后提示“源历史中尚无这项活动的可用内容”。只读核对发现，实时事件中的活动已经写进 Codex 当前的 rollout，Monitor 却仍然读取同一聊天的旧文件。用相同活动身份核对，新文件中的工具参数、执行结果和正文可以读取；本次症状不能据此解释为内容丢失。

Codex 的稳定聊天身份 `thread_id` 与具体历史文件的 `rollout_id` 可以不同。普通文件名是 `rollout-{timestamp}-{thread_id}.jsonl`；执行 `thread/revert` 后，聊天身份不变，新文件名可以是 `rollout-{timestamp}-{thread_id}_{rollout_id}.jsonl`。旧文件保留，当前路径通过状态数据库切换，新文件可用 `history_base` 引用之前保留的历史范围。

调查基线的查找规则只匹配 `*{thread_id}.jsonl`，且已有缓存路径仍存在时通常不重新定位。因此，既会漏掉带后缀的新文件，也会继续使用已经不再代表当前历史的旧索引。仅放宽文件名匹配或重启 Monitor 都不足以可靠修复。

此前回归覆盖了单文件内的增量追加、改写、截断、原地替换和内容一致性校验，但缺少“稳定聊天身份对应的当前文件发生切换”以及“有效历史由多个有边界的文件组成”的场景。这是兼容性验收的遗漏。

## 优先级与本轮范围

核心使用场景是显示实时 token 速度、查看本轮活动及前几轮活动。按触发条件和对这些功能的影响分级；尚无使用频率统计，不将下面的优先级解释为用户发生比例。

| 编号 | 问题 | 优先级 | 本轮安排 | 用户影响与判断依据 |
| --- | --- | --- | --- | --- |
| RH01 | 当前 rollout 定位和缓存切换不正确；协议提供的 `Thread.path` 被过滤 | P1，高优 | 必修 | 已在日常使用发生；本轮及后续轮次可能持续查不到内容，旧缓存还会掩盖文件切换 |
| RH02 | 没有按 `history_base` 读取有效继承范围 | P1，高优 | 与 RH01 一起修 | 找到新文件后，前几轮仍可能缺失；直接合并所有候选文件会混入被回退的分支 |
| RH03 | 不支持 `.jsonl.zst` 压缩历史 | P2，普通 | 暂缓 | 上游压缩针对至少 7 天未修改的冷文件；活跃聊天的当前文件通常不涉及，但较旧的继承源可能涉及 |
| RH04 | 忽略子智能体继承上下文的专门边界 | P2，普通 | 暂缓完整支持 | 特定历史中，父上下文被投影成子智能体自己的活动；不能推断所有多智能体任务都会触发 |
| RH05 | 未重放旧格式 `thread_rolled_back` 的有效历史语义 | P2，普通 | 暂缓 | 主要影响旧格式中包含回退标记的历史；与当前 `thread/revert` 切换文件的路径不同 |
| RH06 | 部分持久化活动类型映射不完整 | P3，低优 | 暂缓 | 已复现 `clock.sleep`、HookPrompt、FunctionCallOutput、审查模式等退化为“其他”；尚未证明其破坏主要活动详情或实时测速 |

本轮交付 RH01、RH02，目标是：回退或普通用户聊天分叉后继续使用，Monitor 能读取当前文件，并正确查看保留的本轮与前几轮内容；来源切换后，旧索引、详情与均速不能冒充当前结果。

实时 token/s 主要由实时事件驱动；目前没有证据证明这次缺口导致实时测速数值错误。历史也参与活动结束校正，本轮仍需保护这条边界。所选轮次的平均速度来自 JSONL，请求计时账本必须跟随有效历史范围切换，不能串轮或沿用已丢弃分支的样本。

RH02 本轮覆盖普通用户聊天及其回退、分叉的未压缩历史链；子智能体或 attached thread 的专门展示边界仍归 RH04。出现暂不支持的压缩祖先或特殊继承形态时，只返回可证明的内容与部分覆盖状态，不把缺失的祖先或未解析的边界当作完整历史。

本轮不新增深度历史浏览功能，不重做实时计数算法，不主动发送 Codex 管理请求，不修改 Codex 的历史或数据库。打包、日常替换和完整重启验收在实现通过后单独记录。

## 证据与上游源码

只读真实核对用于确认当前症状；仓库不保存实际会话身份、正文、路径、登录状态或原始 JSONL。另以临时合成数据完成 8 组调查检查：

| 检查 | 已观察到的当前行为 | 对应问题 |
| --- | --- | --- |
| 状态数据库指向带后缀的新文件 | 仍选择旧文件 | RH01 |
| 缓存建立后切换路径，旧文件保留 | 仍复用旧索引 | RH01 |
| 子聊天用 `history_base` 引用父聊天前缀 | 只读到子文件自身后缀 | RH02 |
| 合法 JSONL 压缩后移除明文文件 | 历史被当作不存在 | RH03 |
| 旧格式追加一次回退标记 | 被回退的轮次仍在源历史投影中 | RH05 |
| 子智能体声明自身历史起始 ordinal | 继承前缀仍被投影成自身活动 | RH04 |
| 输入缺失映射的 canonical 活动类型 | 退化为“其他” | RH06 |
| 过滤带路径、历史模式、父身份的协议元数据 | 相关字段被丢弃 | RH01；RH04 的后续输入边界 |

以上是修复前的缺口复现；RH01、RH02 及必要降级保护随后已增加正式回归，见[实施结果](implementation-results.md)。本轮验收范围仍以[实施计划](implementation-plan.md)为准，暂缓项不因记录在调查表中而自动成为已修复能力。

上游链接固定在本次参照提交：

- [文件名与两种身份](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/rollout/src/rollout_file_name.rs)、[回退并切换当前路径](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/thread-store/src/local/revert_thread.rs)、[当前路径解析优先级](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/thread-store/src/local/thread_rollout_resolver.rs)。
- [继承链与范围验证](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/thread-store/src/local/rollout_lineage.rs)、[历史位置字段语义](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/protocol/src/protocol.rs)、[Thread 元数据](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/app-server-protocol/src/protocol/v2/thread_data.rs)。
- [压缩读写与冷文件条件](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/rollout/src/compression.rs)、[子智能体上下文边界](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/thread-store/src/local/thread_history_materialization.rs)、[旧回退规则](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/core/src/thread_rollout_truncation.rs)、[扩展活动类型](https://github.com/openai/codex/blob/9def09afa71e1443a1028866ac22dc82aedea00e/codex-rs/ext/items/src/lib.rs)。

## 暂缓项的重新评估条件

- RH03：近期活动需要的继承源被压缩，或用户实际反馈冷历史不可读时，提升优先级；本轮先保证明确降级，不自动解压回 Codex 目录。
- RH04：真实多智能体任务出现活动重复、归属错误或影响当前状态时，提升优先级；合成复现不替代真实影响范围的判断。
- RH05：支持范围明确纳入含旧回退标记的历史，或收到相关用户问题时，补齐有效历史重放。
- RH06：某个缺失类型成为常用活动，或发现其影响主面板状态、计数或主要详情时，单独提升；单纯分类文案继续低优处理。

实现后在本目录另记实际改动、提交、测试和部署结果，不把本计划中的目标改写成已经完成。

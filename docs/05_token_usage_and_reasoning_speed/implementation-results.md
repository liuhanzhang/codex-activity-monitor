# 本回合 Token 用量与最近推理均速：实现结果

> 2026-10-22 更新：推理均速功能已移除，官方 Output / Reasoning 用量继续保留。当前范围见[移除说明](removal.md)；下文保留原需求和验收的历史记录。

日期：2026-10-13。源码实现、已有真实捕获回放、隔离 stdio 后端与采集器恢复验证已完成。首次验收时日常运行进程尚未加载本版；后续日常加载检查与采集器重载结果见第 6 节，不把隔离运行结论当作日常界面已生效。

2026-10-15 后续[计时核对实验](../../analysis/reasoning-timing/README.md)确认：完整 reasoning item 的 added / done 边界也可能只有毫秒间隔，而整个响应持续数秒。因此下文的“有效均速”只描述当时实现的接受规则；原验收没有证明该窗口覆盖模型实际推理过程，不能将其视为准确推理耗时或速度。官方 token 计数的验收与这个时间分母问题独立。

## 1. 已实现的行为

看板主表现在为五列：聊天 / 项目、当前活动、耗时、本回合 Token、速度。用量列显示精确整数 `Output` 和 `Reasoning`；Output 包含 Reasoning。速度列同时显示原有的生成速度和 `推理均速 ≈… token/s`。详情显示最近推理均速的原模型与 turn。

计数以 engine 实际接受的 `turn/started` 为边界，同一 turn 的重复事件、追加输入、Goal 状态与工具活动不清零。新 turn 只清零两个计数，最近有效均速跨段、跨 turn 保留；旧 reasoning 段的迟到 usage 不能覆盖新段结果。整个 Goal 不跨 turn 合计。

首次完整新回合为 0 / 0。字段缺失、半途加入或覆盖无法确认时为 `—`，不使用线程历史总量或本地 tokenizer 补齐。真实报告的零值保留为零。没有“统计中”。现有 TextStream 分词算法及 1Hz 看板刷新未改动。

## 2. 数据与关联

[usage.py](../../src/codex_progress_monitor/usage.py) 独立处理服务端用量、响应去重、累计基线和 reasoning 生命周期，不读取模型正文。protocol 的 `last` 不逐条相加，也不用于推理均速。响应账本与可靠累计差分是同一用量的两种视图，不能相加。

单段均速要求响应身份可靠、完整输出成员集合内恰有一个 reasoning item、真实完整边界和正常结束。优先用一对完整的上游边界；若计算时只有完整协议边界，则使用协议。两种来源不能各取一个边界拼接。计算用原始观测时钟，等待 usage、工具执行与 delta 首尾时间不作为分母；跨缺口、重放开始、重试或审批等待中的段不发布新均速。

当前安装版 `codex-cli 0.162.0-alpha.17.2` 的真实捕获中，部分 `response.completed.output` 为空，但 `usage.attribution.items` 提供 item 用量。最小过滤仅在 attribution 完整、数值合法且 output 总和与官方 usage 一致时，提取正 output 的生成 item IDs；历史输入 / 零 output IDs 与 attribution 正文不跨 IPC。仍需每个生成 item 的独立身份映射。此格式是安装版观察，不是公共 API 保证；缺失或无法验证时不按最近聊天猜测。

累计差分的初始基线仅来自明确新建 thread 的零基线，随后可随可靠回合边界延续 / 恢复。下一 turn 继承基线还要求前一 turn 已结束、账本与 total 精确一致、观测模型 item 都已覆盖。未知历史基线不能用 total 减当前账本倒推，也不能通过 last 数值恰好相同来配对；两个同值响应的迟到前缀会导致这种倒推算错。本版对已有聊天采用响应账本，基线不明且发生缺口时显示 `—`。旧 turn 的迟到 usage、累计回退、两来源前缀冲突、压缩 / 回退通知与身份冲突会停用不可信差分；不把负值裁成零。

## 3. 快照与恢复

实际字段为每个 thread 的 `usage` 对象：

```json
{
  "usage": {
    "turn_id": "current-turn",
    "output_tokens": 1424,
    "reasoning_tokens": 156,
    "source": "responses",
    "diagnostics": [],
    "latest_reasoning": {
      "tokens_per_second": 26.730141554169062,
      "reasoning_tokens": 42,
      "duration_seconds": 1.57125991700741,
      "turn_id": "original-turn",
      "response_id": "response-id",
      "item_id": "reasoning-item-id",
      "model": "gpt-6-luna",
      "source": "upstream",
      "order": 1791703614.321642
    }
  }
}
```

无法确认的计数为 null，首次无均速时 `latest_reasoning` 为 null。计数来源为 `responses`、`cumulative_difference` 或 `unavailable`；原因进入 `diagnostics`。这是计划中快照草案的实际结构，保持 schema_version 1 的增量兼容。

顶层 `usage_recovery` 为版本 2 的数值 / 身份账本，按 home / thread 恢复，并保留 turn 与 response 去重信息。草稿版 1 的基线规则不再接受。恢复验证整数、容量和均速分子 / 分母一致性，采用字段白名单；不恢复正在进行的 reasoning 边界。重放明确带 `replayed: true`，保持原观测时间。已完成静态均速可以恢复，活动段不能借重放重建连续计时。

状态上限为 64 个逻辑 thread、每 thread 4 个 turn、每 turn 256 个 response、全局 512 个 item、128 个待关联响应。历史 / 待关联记录保留约 600 秒；活动 thread 不因工具静默而过期。响应账本饱和或待关联溢出时降级完整性；不淘汰去重记录后继续冒充完整累计。bridge 缓存另有 512 行 / 4 MiB 双上限，缓存的是过滤后元数据。恢复账本不含隐藏 reasoning、加密内容、输入、回答正文或工具结果。

## 4. 验收证据

首次验收的 `unittest discover -s tests -q`：**135 项通过，19.634 秒**。新增 usage 语义用例 28 项；新缓存字节限制 / 明确重放测试 1 项。最终零用量 warmup 处理调整后，28 项 usage 用例再次通过。旧事件盘点测试仅增加 `thread/tokenUsage/updated` 的本轮例外，历史 87 项决策文档不重写。后续过期处理修复后的检查见第 6 节。

| 编号 | 结果与证据 |
| --- | --- |
| A1 | 捕获的两次响应 1,267 / 114 + 157 / 42 得到 1,424 / 156；另一个并发聊天得到 1,820 / 237。与 protocol total 一致。 |
| A2 | 新 turn 从零开始；请求、Goal 暂停 / 恢复状态、同 turn 重放不清零。新增测试保护已结束旧 turn 的迟到开始事件，统计指针不后退。 |
| A3 | response 与 cumulative 两来源不相加；相同数值的不同 response 分别计入，重复 response 去重；旧 turn 的 usage 只回填原 turn。 |
| A4 | 捕获首段 reasoning 为 114 / 2.328202833 ≈48.965 token/s，usage 晚到 6.284 秒仍不改变分母；另一段 usage 晚到 24.574 秒。 |
| A5 | 合成用例验证下一段 / 下一 turn 保留、原子替换与迟到旧结果不倒退。隔离真实两回合验证清零时保留原均速。 |
| A6 | 多段合计、缺失字段 / 边界、非正耗时、失败、缺口、重放、跨来源拼接与身份冲突不产生新均速。真正报告零 reasoning 且有完整单段时允许 0.0。 |
| A7 | 新回合 0 / 0、半途加入不冒充线程总量、可靠基线恢复缺口后计数、旧 / 损坏恢复格式降级。隔离采集器重启保留最终计数与均速且不双计。 |
| A8 | home / thread 隔离、同 home 的多个 backend 去重、子线程独立计数与父聊天镜像忽略均通过。后台命令语义回归通过。 |
| A9 | 主行两种计数 / 两种速度；千位分隔；60 / 80 / 120 / 240 列渲染可见数值；历史均速在 stale 快照保留；现有 PTY 1Hz / 即时按键用例通过。 |
| A10 | 已有真实记录回放、当前安装 CLI 的最小实际运行、静态恢复、bridge / collector 加载哈希和实际 watch 命令完成；精简证据无原始对话内容。 |

回放命令：

```sh
.local/venv/bin/python tests/verify_usage_capture.py \
  .local/product-preflight/live-20261011/capture.jsonl \
  --output docs/05_token_usage_and_reasoning_speed/capture-verification.json
```

[回放证据](capture-verification.json)记录原捕获 SHA-256、官方数值、阶段耗时、usage 延迟和关联来源。原始捕获未提交。

最小运行命令使用已有独立登录的测试 home，不复制凭据、不修改配置、不向日常后端注入请求：

```sh
.local/venv/bin/python tests/usage_smoke.py \
  --home experiments/desktop-isolation/runs/20261010-150337-2b232b/codex-home \
  --run-dir .local/usage-smoke-20261013-done
```

首次验收的最终运行第一 turn 两次响应累计 **269 / 120**，最近单段均速 **≈23.1 token/s**；下一 turn 最终 **39 / 0**，明确观察到从零开始并保留原均速。collector 重启后计数、均速及响应去重保留。当时 bridge 与 collector 的加载哈希和工作区源码完全匹配；见 [运行证据](runtime-verification.json)。该证据保留后续过期处理修复前的源码身份，不视为后续修复的真实模型验收。采集器停掉后连接出现一次 send_error，是本次恢复试验主动断开的观测连接；不称为日常零丢包验证。

最终运行首回合捕获到实时正生成速度，短正文的第二回合未捕获到正速度快照；现有分词 / 并发流 / PTY 回归通过。测试中要求的核验命令受到 sandbox 限制，未把其执行当作新增指标验收；真实工具生命周期仍以已有捕获与回归用例验证。

## 5. 加载与剩余边界

实现已在独立 stdio runtime 生效，测试进程与采集器已关闭。实际 `watch --once` 也以最终代码读取该运行快照，静态用量与历史均速正确。未进行新的 Desktop 原生窗口端到端操作，也未为 Goal 增加专用真实模型试验；Goal 使用统一 turn 边界，通过合成序列验证。

首次验收时，日常 `.local/runtime` 只做加载身份读取，未重启当前 Codex 或采集器；当时旧 bridge / collector 未匹配最终源码。加载新版需要正常重开 Codex 以加载 bridge，按 [进程管理](../02_product_mvp/startup-and-processes.md#4-停止恢复与卸载) 重载采集器，并重开 `watch`。只重开看板不能补上旧进程未处理的 usage。半途接入的回合不能凭恢复事件伪造完整计数。

压缩 / 回退后的累计覆盖、多段 reasoning 与长期并发的各种安装版本没有新的自然使用实测；对应行为采用明确降级，并有离线语义测试。有限运行通过不代表所有服务端格式或所有长期场景都已验证。

README、需求、计划和使用说明已更新。首次验收时保留用户原有暂存区，未暂存、提交或推送实现。

## 6. 日常运行加载检查与重载

用户反馈“本回合 Token”一直为空后，检查发现三个日常 bridge 的加载指纹均已匹配当前源码，但 collector PID 50091 仍运行旧代码：加载模块没有 `usage`，聊天快照没有 `usage` 字段。`start` 对已运行的采集器直接返回，因此启动命令本身不会升级旧进程。

首次重载只通过 `bin/codex-progress stop` 请求采集器退出，由现有 KeepAlive 自动拉起新版，未重启 Codex 后端。当时新版 collector PID 39446 的加载指纹与工作区源码匹配，三个原 bridge PID 35026 / 35054 / 38007 保持不变并恢复连接，快照包含版本 2 的恢复账本。

首次重载时该聊天的本回合计数为 null，原因明确为 `replayed_turn_start`、`bridge/reconnected`、`bridge/gap`：当轮开始时旧采集器未记录用量，重载属于半途接入，不能把后半轮累计冒充整轮累计。重载后实际观察到一段完整 reasoning 的 182 tokens / 8.590255209 秒，最近均速约 21.2 token/s，`watch --once --thread` 已显示该结果；未将该回合空值记作完整日常回合计数验收。

后续提交前检查发现，真实新 turn 已完整观测开始且响应账本包含官方用量，但 `unassociated_usage_expired` 错误地使其计数降级。未关联响应的 600 秒保留期限是在后续事件到达时检查；旧处理把过期发现时刻当作新的后端缺口，会误伤原响应之后才开始的回合和 reasoning。修复为按被丢弃响应的原始观测时间限制影响范围，并保留回合开始时间到恢复账本；容量淘汰也使用被淘汰响应的后端和原始时间。真正的断连 / 丢包仍按现有缺口规则处理。新增两项回归覆盖新回合不受旧 usage 过期影响，以及跨原事件持续运行的回合仍降级、后来开始的完整 reasoning 仍可发布均速。

修复后 usage 用例 **30 项通过**，完整测试 **137 项通过，21.299 秒**。日常采集器再次重载为 PID 75069，加载指纹与修复后的源码一致，恢复账本仍为版本 2；现有 bridge 恢复连接，未重启 Codex 后端。本次新增修复只改变采集器使用的账本逻辑，不改变 bridge 的数值与身份过滤函数。当前回合已经被旧逻辑降级，重载继续保留空值，不冒充已恢复完整性；新回合行为由新增跨回合用例验证，尚未取得修复后的完整日常回合实测。

# 本回合 Token 用量与最近推理均速：实现计划

> 2026-10-22 更新：推理均速功能已移除，官方 Output / Reasoning 用量继续保留。当前范围见[移除说明](removal.md)；下文保留原需求和验收的历史记录。

日期：2026-10-13。状态：P0–P6 已实施；验收与进程加载情况见 [实现结果](implementation-results.md)。产品规则与验收编号见 [需求文档](requirements.md)。以下保留实施依据，快照草案的最终字段见结果文档。

## 1. 实现策略

按“数据来源与关联验证 → 最小 usage 过滤 → 回合计数 → 推理计时与替换 → 快照恢复 → 展示 → 有限验收”推进。服务端用量、reasoning 阶段计时和现有 TextStream 分词保持独立，最后在同一聊天快照中组合展示。

优先复用现有透明观察链路，不改模型请求、工具模式、认证路径或 Codex 配置，不增加 sessions 全量扫描或主动向日常 app-server 注入请求。上游完成事件的 usage 用于响应级用量与均速关联；app-server 累计快照用于有可靠基线时的计数校正 / 恢复。两条来源不叠加。

“本轮数量清零”和“最近均速替换”使用两套状态：新 turn 更换累计计数的作用域，不清除最近一次有效均速。reasoning 开始不触发均速清空。

本次按真实 turn 统一计数，不实现 Goal 跨 turn 合计或“用户轮次”映射。无需新增 turnTrigger、clientUserMessageId、parentTurnId、rootTurnId 等启动来源字段来判断是否清零，也不因 Goal 暂停 / 恢复 / 阻塞增加特殊分支。用户请求提交不是清零信号；只有 engine 接受新的 turn 生命周期边界才清零。

## 2. 代码影响范围

| 文件 / 模块 | 计划调整 |
| --- | --- |
| [events.py](../../src/codex_activity_monitor/events.py) | 放行 `thread/tokenUsage/updated` 的最小数值字段；上游终止 response 保留 usage、response ID、身份与 item IDs，校验形状。 |
| 新增 `usage.py` | 管理响应去重、回合累计 / 基线、reasoning 完整边界和最近有效均速结果；不依赖 tokenizer。 |
| [engine.py](../../src/codex_activity_monitor/engine.py) | 接入新 turn、reasoning 边界、response 完成与 usage 通知；可靠关联后调用 usage 状态，生成新增快照字段。 |
| [bridge.py](../../src/codex_activity_monitor/bridge.py) | 缓存恢复所需的最新 usage 基线 / 有界响应元数据，明确重放身份；保留原事件观测时间，不扩展正文缓存。 |
| [collector.py](../../src/codex_activity_monitor/collector.py) | 按逻辑 thread / turn 恢复已确认计数和最近有效均速，避免历史快照与新事件双计或串回合。 |
| [display.py](../../src/codex_activity_monitor/display.py)、[中文资源](../../src/codex_activity_monitor/messages_zh_cn.py) | 五列布局、本轮两种计数、生成速度与最近推理均速；旧字段兼容、窄终端与历史静态结果展示。 |
| [runtime.py](../../src/codex_activity_monitor/runtime.py)及进程身份 | 新模块纳入实际依赖方的加载身份检查；复用已有原子快照机制。 |
| [tests](../../tests) | 增加语义测试，更新此前“usage 被过滤”的边界断言，覆盖恢复与显示。 |

新增模块名是实施约定。现有 `metrics.py` 的 TextStream 和 TokenCounter 算法继续用于接收文本速度，不复用为服务端累计器。

## 3. 数据与状态设计

### 3.1 身份与计数来源

运行期使用 backend 作用域关联事件，逻辑恢复使用已有 home / thread 身份。turn 用真实 turnId；response 去重键优先为 backend 作用域中的 responseId，跨 backend 恢复时核对 home、thread、turn 与 responseId，不能只按计数值去重。

响应关联优先使用已明确提供的 thread / turn 身份与 response 的 item IDs，再复用既有 turn / item 映射。同一 backend 并发多个聊天时禁止使用“当前 thread”或“最近 reasoning”兜底。响应输出列表缺失、截断或身份冲突时，只接受有其他独立完整身份依据的关联。

计数采用下列优先级，并在同一回合中明确来源与覆盖范围：

1. 完整观测到回合起点、所有所属 response 可去重时，累加响应级服务端 usage。
2. 已有可靠的回合前累计基线与当前累计快照，且能证明差值仅覆盖该 turn 时，使用 `current_total - turn_baseline` 作为本轮计数，并与响应账本对照。
3. 两种来源一致时互相校正；不能把两者相加。覆盖范围不同或冲突时记录原因，在能确定完整用量前不发布一个假完整值。

`thread/tokenUsage/updated.last` 没有 responseId，不能无条件累加或用于 reasoning 均速。首次看到一个累计 total 不意味着该回合基线为零；只有确认全新 thread 或拥有完整历史依据时才使用零基线。新 turn 开始时若旧 turn 尚有晚到 usage，简单的 total 差分会混入旧用量，须先对齐原响应 / 基线，否则禁用该校正路径。恢复 / 回退 / 压缩后的累计语义先验证，不对未知负差值使用 `max(0, delta)` 掩盖问题。

### 3.2 状态建议

- 每个 thread 维护当前 turn 的用量状态、最近一次有效均速和有界的旧 turn / 待关联响应记录。
- 每个 turn 保存回合起点是否完整、累计基线、已确认 Output / Reasoning、完整性、响应去重状态和最新累计快照。
- 每个 reasoning item 保存 turn、response 关联、真实首次开始 / 结束观测时间、正常完成状态、重放标记与缺口质量；协议和上游的同一 item 合并一次。
- 每个有效均速保存 reasoning item、response、turn、模型、服务端 reasoning tokens、观测时长、速率和段落先后顺序。结果生成时间不作为段落先后顺序。
- 有界缓存溢出、身份冲突和缺失字段可诊断；不能在淘汰记录后把重复事件重新计入，或继续声称可覆盖完整回合。

### 3.3 快照草案

新增字段与已有 `generation_rate` 分开。建议每个 thread 增加以下对象；字段名可在实施时统一，但来源、作用域与空值语义不可省略。

```json
{
  "usage": {
    "turn_id": "turn-current",
    "output_tokens": 12000,
    "reasoning_tokens": 9000,
    "source": "response_usage",
    "quality": "complete"
  },
  "last_reasoning_rate": {
    "turn_id": "turn-current-or-earlier",
    "response_id": "response-id",
    "item_id": "reasoning-item-id",
    "model": "model-id",
    "reasoning_tokens": 1700,
    "duration_seconds": 20.0,
    "tokens_per_second": 85.0,
    "estimated": true
  }
}
```

没有有效均速时 `last_reasoning_rate` 为 null。用量不可确认时对应计数为 null，并保留结构化质量原因；正常完整观测的新 turn 为 0。历史静态结果和正在生成的瞬时速率不共享一个“全部速度无效”的开关。

快照只含数值、身份、模型与质量元数据，不存隐藏 reasoning、加密内容、用户输入、完整 response 或工具结果。旧快照缺少对象时由显示层回退为 `—`；结构扩展与 schema_version 按实际兼容方式统一处理，恢复代码不得假定新字段始终存在。

## 4. 分步计划

### P0：验证字段与关联链路

1. 检查现有原始捕获中的 `response.completed`：usage、responseId、item IDs、日志身份和 reasoning 生命周期是否足以在并发聊天中唯一配对。
2. 对照 `.local/product-preflight/live-20261011/capture.jsonl` 与同目录 `protocol.jsonl`，确认 Output / Reasoning 值及延迟顺序；输出只保留数值、身份、时间和来源，不导出原始对话。
3. 对照当前安装版本与可用协议源码，验证 `thread/tokenUsage/updated.total/last` 的累计范围、恢复重发、压缩和回退边界。本地源码只是参考，不替代安装版本实测。
4. 优先利用已有 response 完成记录。只有关联仍不足且现有链路已经被动提供响应身份事件时，才评估最小额外字段；不为凑配对主动调用内部接口。
5. 如旧捕获不能验证某项关键行为，使用独立临时 runtime 做一个最小真实样本；多段 reasoning 的回退用合成事件验证，不必强迫模型产出指定隐藏推理结构。

完成条件：记录一组确实可计算单段均速的关联证据，以及不能计算的条件。关联不足时两个累计计数仍可先实现，但不能将均速标为已经完成；继续定位缺失身份并明确残余边界。

### P1：最小过滤与有界恢复缓存

1. 将 usage 事件加入显式 intake；只保留 threadId、turnId、total / last 中所需的 Output / Reasoning 数值和必要身份。
2. 上游终止 response 只新增所需 usage 数值；保留现有 response ID、item IDs、状态与身份，不把整个 response 对象放行。
3. 校验非负整数，拒绝布尔、浮点、字符串、非法形状；字段缺失保持缺失，不默认填零。验证 `reasoning <= output`，冲突时保留诊断并隔离异常值。
4. 定义重放标记和原观测时间的恢复规则；缓存最新累计基线与必要响应元数据，限制容量。重连不重新计数、不把恢复时刻当作 reasoning 开始。
5. 更新现有过滤测试中“usage 被丢弃”的断言，只扩展本轮必要字段；其他 out-of-scope 事件继续按原边界处理。

完成条件：所需数值与身份到达 collector，过滤后的包 / 缓存不含新增敏感响应内容；原 stdio 转发及旁路降级行为保持原有语义。

### P2：回合累计与去重

1. 新增 usage 状态模块，接入 engine 的新 turn 边界；同 turn 重放幂等，新 turn 清零当前计数。统一处理普通工作和 Goal 续跑，不识别用户任务归属、不按相同 rootTurnId 合并计数。
2. 维护响应级去重账本与累计快照校正路径，按第 3 节的覆盖范围选用计数来源。
3. response 与 protocol usage 到达顺序不同、重复 `last`、旧 total 重发和末尾 usage 延迟时均不重复累加。
4. 旧 turn 用量只更新其原状态；current turn 指针不因迟到 usage 后退。turn 完成后不提前淘汰待到达 usage。
5. 处理半途接入、缺口、负差值、恢复 / 回退和多 backend；只有可靠重建覆盖范围后才重新发布完整累计值。

完成条件：A1–A3、A7–A8 的计数部分通过；不能使用线程 total 直接填当前回合，或用本地文本 token 弥补缺失 usage。

### P3：推理均速与替换顺序

1. 合并协议 / 上游 reasoning item 的真实边界，使用稳定 item 身份去重，区分正常结束和 engine 推测 / 兜底结束。
2. response 完成后，通过身份及输出 item 集合关联 reasoning 段。只有完整且唯一的一段才进入本轮均速计算。
3. 使用服务端 reasoning token 数与原始单调时钟耗时；不沿用 TextStream 的首尾 delta 均速。
4. reasoning 开始、结束、工具活动和新 turn 不清空最近结果。usage 到达后计算并直接替换；采用 reasoning 的先后顺序，禁止迟到旧结果覆盖较新结果。
5. 多段合计、缺失边界、重放、异常终止、无 usage、身份冲突或缺口不发布新均速。可靠已报告的用量仍可独立进入计数。

完成条件：A4–A6 通过；典型序列为“旧均速保留 → 新段结束仍保留 → 对应 usage 到达 → 一次替换”。

### P4：快照与恢复

1. engine 导出新增字段；已完成均速包含完整归属与来源，计数保留 turn 作用域与完整性。
2. collector 恢复时按 home / thread / turn 匹配，结合桥接重放的用量基线 / 去重状态。禁止恢复旧累计值后再次把同一 response 加入。
3. 最近均速可以跨 turn 保留，但必须保留原 turn / response / 模型；计数只能恢复到对应 turn。
4. 保留现有生成速度的重启 / 断连降级；已验证的历史均速独立保留。没有完整边界的新段不能跨观测缺口继续计时。
5. 新模块纳入 collector 等实际依赖进程的 source_identity。恢复缓存格式不足时用 `—`，不通过猜测让数字连续。

完成条件：针对恢复、旧快照、断连和重复 backend 的用例通过；重启不会伪造当前回合累计或一段连续推理。

### P5：展示与文案

1. 主表增加“本回合 Token”列，分别显示 Output 与 Reasoning；速度列分别标识现有生成速度和推理均速，避免被理解为整个 Goal 的合计。
2. 计数为整数千位分隔，均速一位小数与 `≈`；缺失字段为 `—`，不新增“统计中”。
3. 详情显示均速结果的原模型 / 回合归属，不显示协议原始 dump 或推理内容。
4. 文案进入语言资源，新增字段兼容旧快照；保持当前 1Hz 定时刷新与用户按键即时更新。
5. 固定快照检查多聊天、中文、长名称和窄终端，数值可换行但不得被布局裁掉。

完成条件：A9 通过；主行无需展开即可看到两个计数和两种速度，当前生成速度的含义与可信度规则保持一致。

### P6：有限验收与交付

1. 运行第 5 节中与实际改动有关的测试和一份相关真实记录回放；已通过的检查不无故重复。
2. 对最小真实路径确认 usage 采集、回合计数、单段关联和均速保留 / 替换，并保存精简结果。
3. 在独立临时 runtime 验证恢复与看板渲染。日常 runtime 切换时分别核对 bridge、collector、watch 加载身份及行为，不以文件已修改认定功能生效。
4. 在本目录新增 implementation-results.md，按 A1–A10 写实际结果、数据源版本、未触发场景与限制；更新使用说明和当前文档入口。

完成条件：需求内可验证行为完成，并明确未验证边界。仅写计划、通过合成测试或安装成功不能替代运行验收。

## 5. 针对改动的验证

| 验证位置 | 必需场景 |
| --- | --- |
| 新增 `tests/test_usage.py` | 单响应 / 多响应累计、Output 包含 Reasoning、缺失与真实零值、双来源去重、相同 token 值的不同 response、重复 / 乱序、基线未知、累计回退。 |
| usage / engine 相关用例 | 新 turn 清零、同 turn 不清零；模拟 Goal 续跑 / 恢复产生新 turn 时也清零，同 turn 的暂停 / 插话 / 恢复不清零；旧 turn 晚到、并发 thread、子代理镜像、同 home 多 backend、完成后 usage 回填。 |
| 均速语义用例 | 正常单段、usage 延迟、下一段 / 下一 turn 保留旧值、有效结果替换、迟到旧结果不倒退、多段合计、缺失边界、异常结束、跨缺口与重放不造均速。 |
| `tests/test_runtime.py` 的相关用例 | 新过滤字段、敏感内容不出现在缓存 / 快照、缓存容量、原 stdio 行为、collector 恢复、响应重复重放、旧快照兼容。 |
| `tests/test_display.py` / `tests/test_usability.py` 的相关用例 | 两个计数与两种速度、千位分隔、无“统计中”、首次空值、历史均速保留、窄终端、中文资源、已有 1Hz 行为。 |
| 一份已有真实捕获 | usage 数值与两来源一致，reasoning 边界和延迟顺序保持一致；回放结果不包含原始文本。 |
| 最小新版本运行路径 | 真实事件被新 bridge 放行、collector 计算与 watch 展示可观察；缺乏可靠单段关联时不发布假均速。 |

Goal 不设置单独的实际运行验收，也不为它新增启动来源采集。通过合成不同原因的新 turn / 同 turn 序列验证统一边界；真实记录回放与最小运行检查继续验证 usage 采集和实际 turn 行为。

沿用现有 `.local/venv` 与 `unittest`。先运行新增状态测试及受影响 engine / runtime / display 用例；只有新失败或兼容疑点才扩大范围，不默认重做全部历史捕获、87 种事件盘点或产品性能实验。

## 6. 待验证项与回退

这些是实现中的技术验证任务，不要求用户重新确认已确定的产品规则。

| 待验证项 | 检查方式 | 回退 |
| --- | --- | --- |
| 当前日志能否为 response usage 提供足够身份 | 对照实际输出 item IDs、日志身份和并发聊天。 | 不按最近活动猜关联；只显示可可靠归属的计数，均速维持最近有效值或 `—`。 |
| protocol total 在恢复 / 压缩 / 回退后的语义 | 源码参考加实际记录验证，比较 Output / Reasoning 子字段。 | 停用无法证明覆盖范围的差分路径，保留诊断。 |
| 一次响应是否包含多段 reasoning | 检查输出 item 集合，并用合成事件验证合计情况。 | 不分摊合计用量，不发布该次单段均速。 |
| collector 能否完整恢复当前 turn 的用量 | 检查原回合基线、响应去重账本与 bridge 缓存。 | 缺失部分不伪造；不可确认的计数字段为 `—`，历史有效均速独立恢复。 |
| 窄终端能否同时容纳新增信息 | 固定宽度渲染与临时 watch。 | 换行 / 压缩文字，保持指标可见与现有操作。 |

## 7. 本次交付范围

本次交付需求、计划、实现、精简验收证据及使用说明。源码与隔离运行已完成；日常旧 backend 的加载需要在正常重开 Desktop 后确认，状态单独报告。Git 提交与推送不属于本次实施任务。

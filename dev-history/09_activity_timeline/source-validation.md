# 来源验证与已固定的数据决策

日期：2026-10-17。状态：工程实现中的来源验证记录；尚不代表完整产品或原生验收通过。

## 隔离真实运行

复用了既有隔离 Codex home，启动独立 stdio app-server；不连接 Desktop writer、不修改日常 home、接入变量或 Remote 路由。运行二进制为 `0.163.0-alpha.10.2`。可复用探针见 [source_probe.py](../../experiments/timeline/source_probe.py)。

本机私有结果：`.local/timeline-source-probe/result.json`。只把结构统计写入报告；额外的协议全文捕获已删除，Codex 自己的隔离历史保持原归属。

- 成功 steering 返回原 turn ID；错误 expectedTurnId 被拒绝；只有 1 次 `turn/started`。
- 该轮产生 2 条 canonical userMessage，均属于原 turn。它们有独立 item ID，clientId 为空，因此首版以 canonical 用户消息作为唯一输入标记来源，不额外绘制请求标记。
- 实际 `item/started` params 包含 `startedAtMs`，`item/completed` 包含 `completedAtMs`，与源码的 camelCase 定义一致。
- `thread/read`、`thread/turns/list`、`thread/items/list` 在这个自有 stdio 后端可读。此结果没有证明可以向现有 Desktop 后端安全注入请求，因此产品默认读取 JSONL。
- 该隔离运行的工具执行被嵌套沙箱拒绝，没有得到 canonical 命令执行区间；不将本次结果写成“真实进程跨轮已验证”。

## 实时参数时间与历史内容衔接

用 [validate_sources.py](../../experiments/timeline/validate_sources.py) 读取该隔离运行 TRACE，并通过隔离历史中的明确 turn→thread 身份建立映射。TRACE 的工具 item 只含 turn_id，不能按“当前聊天”猜归属。

结果记录在 `.local/timeline-source-probe/reconciliation.json`：112 条相关 source event，4 个模型阶段，5 条侧存储元数据记录；其中 1 个参数生成窗口约 796.16ms。关闭 writer 后，从元数据侧文件读取该区间，并成功从 Codex 自己的 JSONL 取回对应参数内容，投影错误为 0。

这是实际接收日志与实际持久化历史的离线衔接验证，不是 GUI 运行验证。只保存阶段元数据，参数全文没有写入 SQLite。后续在线路径的独立验证如下。

## 关联与降级规则

- 已检查的新格式中，正文／思考的 raw 与 canonical 共用原 item ID；两类阶段使用共同的模型身份空间。参数生成与工具执行始终分开。
- 工具关联只使用明确 call ID 或 canonical 身份。Code Mode 外层与内部命令没有明确关系时不凑配；可以分别查看原记录。
- 明确 patch 工具属于编辑内容生成；静态 Code Mode 只有可读 literal patch 且工具种类均为 patch 时才细分。变量参数、混合程序降为一般参数生成。
- child 工作优先依据其自身 parent_turn_id。仅明确 spawn 且能定位新 thread 首轮时可引用该首轮；重复使用或关系不完整时展示身份和状态，不把 child 全部历史归给当前父轮。
- 已识别的历史 Extension `web.search / image_gen.generation` 映射为相应工具类别；未知 Extension 保留为中性活动和原始内容。

## 对验证门槛的影响

E01／E02／E03／E05／E07 已形成可实施路径。E04 的真实 process 路径、E08／E09 的针对性结果如下；child 自然运行覆盖仍有限。E06 的 legacy／损坏／替换等降级已有针对性夹具；不承诺未测试的其他二进制版本。

## 隔离命令成功与在线链路

初次嵌套沙箱拒绝后，在正常 macOS 执行环境重跑自有隔离后端。`.local/timeline-source-probe-native/result.json` 确认 canonical 命令实际开始、完成。原始失败记录保留，不以重跑结果改写其证据范围。

随后经过产品新 bridge → collector → `tl.sock` query，结果为 `.local/timeline-online-probe/result.json`：

- 1 次真实 turn start，2 条 canonical 输入，成功 steering 与错误目标拒绝均符合预期。
- 命令 exit 0，canonical `source=unifiedExecStartup`、process 身份存在，生命周期约 3,946ms。
- 3 个参数阶段，其中 2 个有完整生成区间，投影错误为 0，隔离配置 hash 不变。
- 原报告的 `parameter_details_from_codex=false` 是一个真实边界：第 3 段在 steering 前开始、未完成且未被 Codex 历史保存。不是把全部参数都算作恢复成功。

停止该 collector 后重新只读查询，`.local/timeline-online-probe/reconciled_query.json` 确认：两段已完成参数均为 `available / codex_history`；未完成段为 `not_retained`，没有伪造内容；只读查询没有重新启动 collector。查看器若之前已经取得临时内容，只在窗口内存中保留至关闭。

## 真实跨轮进程

`.local/timeline-cross-turn-probe/result.json` 使用自有隔离聊天与 12 秒 print／sleep 进程：

1. 第一轮正常结束，实际 command item 仍活跃。
2. 第二个真实 turn 完成后，新轮查询提供旧轮入口，没有把旧命令复制／改属新轮。
3. canonical 命令稍后完成，约 11,922ms 区间仍保留最初 owner；投影错误为 0，隔离配置 hash 不变。

此验证没有创建真实子智能体。child 复用、派发结束不等于 child 完成、原始 parent_turn_id 与旧轮导航另由固定源码定义、合成事件和原生合成预览覆盖，不宣称已验证全部自然 child 场景。

## 性能、原生与日常边界

- 一次只读 paginated 大文件采样：282 阶段、15 轮，冷索引 205.73ms，无变化刷新 0.25ms，traced peak 10,964,463 bytes。样本之外不作同等延迟保证。
- 合成原生 Preview 已实际操作双轨、child 展开、重叠候选、键盘选择、diff 与关闭。真实 WindowServer 测试补充证明单窗口复用、用户调整尺寸保持和正常关闭重开。
- 日常 App 已正常退出旧 GUI 并打开本地构建；展开实际聊天可见双轨，已落盘旧轮正文可读取。当前实时活动的源 JSONL 可能滞后，界面保留“当前内容不可用／重试”的诚实状态。关闭查看器后同一 collector 继续运行。
- 数据契约旧快照仍可读；collector 重载前开始的 turn 保持部分观测，不能通过这次历史读取恢复已经缺失的生成窗口或实时速度。

完整检查数量、最终参数、截图与未执行范围见[实施结果](implementation-results.md)及[验收记录](validation-results.json)。

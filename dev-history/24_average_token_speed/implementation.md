# 平均速度实施结果

日期：2026-10-28，Asia/Shanghai。工作树基线为 `6c39325618c4c39ed0edd226e00f151caeea4e2c`，本次实现、测试与文档一并归档为本地提交，未推送。功能、定向测试、真实前缀回放、隔离原生预览及正在增长的真实 JSONL 接入核对完成。随后按用户指令更新了日常实例，见[部署记录](deployment.md)；未声称发行验收完成。

## 实现行为

详情底部左侧保留官方输出与推理用量，右侧新增“平均速度 / Average speed”，显示一位小数及 `T/s`，最右侧保留说明按钮。首条有效 usage 就可以显示，后续 usage 更新累计值；两条 usage 之间不累计墙钟耗时。轮次完成后保留，选择历史轮次时读取该轮自己的账本。

平均速度仍采用方法二：有效响应的官方 inclusive output 总和，除以相同响应的请求窗口总和。推理不重复相加，长窗口不按速度或时长裁掉，也不做平滑。这个窗口可能包含首 token 等待、排队和网络等时间，不能据此宣称服务端纯解码速度。

没有有效样本显示 `—`，零输出且有正耗时显示 `0.0 T/s`。部分响应缺少计时时，显示合法子集的估算并在说明中提示部分覆盖。历史可读性与实时 bridge 健康状态独立判断。现有一秒实时速度、菜单栏和聊天列表计量保持原有实现。

## 数据链路与最终细化

| 位置 | 实际职责 |
| --- | --- |
| [request_rate.py](../../src/codex_activity_monitor/request_rate.py) | 请求边界、响应去重、usage 预览、后续确认及每轮投影；只保存身份、数字和质量信息 |
| [history_index.py](../../src/codex_activity_monitor/history_index.py) | 在 HistoryDirectory 原增量扫描中推进账本；源文件重建时一起重置；usage/count、用户输入、压缩/回退及模型/工具记录参与所选轮次的验证区间 |
| [history.py](../../src/codex_activity_monitor/history.py) | 新账本纳入 retained_bytes 和原有缓存准入、淘汰规则 |
| [timeline_query.py](../../src/codex_activity_monitor/timeline_query.py) | 通过可选 `average_speed` version 1 返回所选根线程、所选轮次，不合并子线程 |
| [Timeline.swift](../../macos/Sources/MonitorModel/Timeline.swift) | 可选 DTO、版本/身份/数值校验；旧 bundle 缺少字段时仍可解码 |
| [PanelStatistics.swift](../../macos/Sources/MonitorApp/PanelStatistics.swift)、[ChatDetailsView.swift](../../macos/Sources/MonitorApp/ChatDetailsView.swift) | 切换期间隐藏旧轮均值，详情统计行、说明、中英文与无障碍标识 |

usage 的终点使用其外层 timestamp。预览只读取当前边界，不提前推进主计时状态；后续接受的 token_count 核对各 token 桶和时序。确认冲突会同步移除该样本的 token 和耗时。相同响应相同用量幂等；同一身份出现不同用量时，该响应保持冲突，不由后续重复记录恢复。

正常 EOF、半行末尾和完成记录不会自行撤销合法预览。缺失确认时，同一活动请求内第二条 usage 不能借用第一条的固定起点；其速度覆盖保守降级。确认优先匹配各 token 桶，有明确轮次时还校验归属。相同桶有多笔候选时，先匹配较早的记录；不匹配的旧预览不能阻塞后续合法确认。每次接受 count 都结束该请求的计时代次，旧轮晚到 count 不移动新轮边界。旧轮的 usage、用户记录及带 metadata.turn_id 的工具结果不会破坏当前轮的计时。

首版只为明确的 token_usage_record 路径构造样本；旧格式只有 token_count 时计为未计时，不实现 100 毫秒终点回退。边界重置和首次模型记录的规则保留实验参照中的必要行为。compaction/revert 记录会断开无法证明的请求连续性，后续明确边界可以恢复。

每轮最多保留 4096 个响应身份，一个目录最多排队 4096 条未确认记录；超过限制的受影响轮次显示未知并报告 `response_limit`。所有账本状态参与默认 64 MiB 历史缓存的权重；这不是进程 RSS 上限。文件替换、截断、同大小改写、验证区间改写及缓存重建有定向测试。请求边界参照 Tokscale 的版本与 MIT 声明保留在模块中。

## 验证结果

### Python 与源码回放

工作树测试显式使用 `PYTHONPATH="$PWD/src"`，避免本机继承的主目录路径让测试误读其他 checkout。

| 检查 | 结果 |
| --- | --- |
| `test_request_rate.py` | 25 项通过：预览、成对累计、零、重复与冲突、非法数值/时序、长窗口、异步工具、缺确认与恢复、旧轮晚到、父子隔离、分段/半行、源改写/替换/截断、缓存重建及预算 |
| `test_history*.py` | 47 项通过 |
| `test_timeline_query.py` | 16 项通过 |
| `test_resource_budgets.py` | 14 项通过 |
| [产品回放脚本](evidence/verify_product_replay.py) | 3 个固定前缀、19 轮、285 个 usage 点的输出累计量、请求累计耗时及速度全部匹配；后续确认全部结算，正常样本没有降级 |
| [原实验派生结果核对](evidence/verify_results.py) | 285 点、19 轮、44 个 item 计时缺口、稳定性分组、266 个间隔内部一致 |
| [增长中的真实源查询](evidence/live-query-check.json) | 本轮真实 JSONL 的 12 次查询快照与独立计时回放逐项匹配，未计时响应为 0；包含两次观察开始时的已有累计值 |

以上 102 项 Python 用例都是合成测试；真实源前缀只读回放，不把原始内容、实际身份或路径写入仓库。产品回放不发起模型请求。

### Swift 与原生预览

Swift 编译通过。定向覆盖平均值校验、零和部分覆盖、线程/轮次切换、旧响应兼容、晚到查询和预览线程隔离；本地化检查通过。新增三项平均值测试与既有相邻用例合计 11 项不同 Swift 测试通过。

隔离 Preview 使用当前工作树构建的二进制、独立 bundle identity、合成 JSONL 经产品 TimelineQuery 生成的 bundle，以及独立 snapshot；详情继续使用原有每秒查询。正常构建在沙箱中的 iconutil 失败，沙箱外完成相同 Preview 构建与签名。日常 App、collector、Desktop 配置均未切换。

原生界面实际观察到：

| 场景 | 显示结果 |
| --- | --- |
| 首条 usage 前 | `平均速度 —` |
| 第一条 usage，尚未收到 count | `100.0 T/s` |
| 第二条 usage | `133.3 T/s`，与累计 400 token / 3 秒一致 |
| 轮次完成，确认已结算 | 继续显示 `133.3 T/s` |
| 切换到上一轮 | 显示该轮 `25.0 T/s`，不借用当前轮数值 |
| 部分覆盖 | 说明弹窗增加 “Average speed covers only responses with valid timing.” |
| 实时状态过期 | 左侧原统计遵循原不可用规则，历史平均值继续可读 |
| 中英文 420 像素窗口 | 左侧输出、右侧平均速度、说明按钮均完整可读 |

检查结束后退出本次 Preview。预览证明受控数据下的渲染与查询刷新；它没有连接日常 collector，也不等于真实模型到日常 GUI 的自然使用验收。

### 完成性核对中补上的真实边界

1. 较早的用户输入记录被等长改写，文件再追加内容时，只校验 usage/count 会保留旧请求起点。现已将全部参与计时的记录纳入验证区间。回归用例覆盖用户输入、压缩、回退以及通用模型调用和工具结果；源变化后按正确窗口重算。
2. 正在增长的真实 JSONL 中，一次重复 turn_context 前存在没有后续确认的 usage。单纯 FIFO 消费待确认记录，使此后所有 count 错配，当前轮无法显示平均速度。现已优先按 token 桶匹配，并在接受 count 后推进请求代次；保留合法旧预览，同时让新请求恢复计时。修复后该真实轮次没有未计时响应，原有 285 点仍全部匹配。

在当前这轮自然工作中，以只读方式启动独立 QueryServer，并使用产品 `timeline-query` CLI 查询真实 Codex JSONL；CLI 结果的计数、耗时和样本数与独立参照回放一致。通过只转发数字及身份的 Preview fixture adapter，原生界面实际显示从 `28.0 T/s` 更新为 `27.8 T/s`，分别对应第 21、23 个样本的累计值。没有人为新增模型请求，也没有替换日常 collector。

这次补充验证覆盖真实新增 usage 到隔离查询/CLI，再到原生解码和展示；Preview 使用 fixture adapter，不能将它写成日常 collector 或 SystemTimelineClient 全链路上线验收。中途 Mac 锁屏，用户解锁后完成界面核对；独立查询进程已结束，Preview 已退出。

## 剩余边界与交付

- 日常实例重建、重启及 collector 到 GUI 的均速更新已完成；下一完整新轮次的实时观测恢复及长期表现仍需另行观察。
- 旧 JSONL 缺少明确 usage record 时返回未知或部分覆盖；没有以旧格式猜测补齐。
- 长期驻留、发行打包和其他机器验收未在本次范围内重复执行。
- 本次实现、文案修复、实验与部署记录一并本地提交；提交号以 Git 历史为准，未推送。

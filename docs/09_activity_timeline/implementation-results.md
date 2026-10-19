# 活动时间线：实现与本机验证结果

日期：2026-10-17。基于 Monitor `20cc995` 后的本轮未提交工作树。需求 R01–R38 的实现和针对性验证已完成；以下记录区分合成资料、隔离真实运行与日常 App 检查，不代表发行或长期自然使用验收。

## 已交付行为

- 活跃主聊天初始收起，非活跃聊天沿用最近完成入口。展开后按真实轮次浏览，默认当前／最近一轮，可选择最近 5 轮。
- 主智能体固定模型、工具双轨。模型轨以同一紫色系区分思考、正文、参数、明确的编辑内容生成；执行仍在工具轨。同轨重叠使用固定槽位与候选菜单。
- 子智能体默认汇总，用户可展开为自己的双轨。父子共用时间轴，保留各自 thread／turn 所有权。关系不完整时显示 `?`，不猜测 child 全部历史属于当前父轮。
- 共同线性时间比例、横向滚动、缩放和回到最新；短块独立扩大命中区。用户选择、阅读位置和展开状态不因轮询刷新重置。
- 点击活动收起菜单面板，打开／复用一个普通原生活动查看器。支持同轮切换、选择复制、文本／代码／JSON／diff、渐进读取和明确的来源状态。
- 已完成内容从 Codex 自有 JSONL 读取，补充 SQLite 只存阶段、身份、时间、关系和质量元数据。停止观察后，历史查询仍是只读操作。

以下均使用手写合成内容。截图中的活动、输出和检查数量是演示数据，不是本轮测试结果。

| 状态 | 原生截图 |
| --- | --- |
| 初始收起 | [收起面板](assets/native-panel-collapsed.jpg) |
| 主智能体双轨与 child 汇总 | [展开时间线](assets/native-panel-timeline.jpg) |
| child 展开自己的双轨 | [子智能体轨道](assets/native-child-tracks.jpg) |
| 同轨重叠选择原始活动 | [重叠候选](assets/native-overlap-selection.jpg) |
| 独立窗口与完整可用 diff | [活动查看器](assets/native-activity-viewer.jpg) |

## 最终工程落点

| 模块 | 职责 |
| --- | --- |
| `timeline.py` | 接收已确认身份事实，保存独立阶段、等待／输入／观察缺口、明确关系与短期 pin |
| `timeline_storage.py` | 单 writer 异步 SQLite，字段白名单、私有权限、版本与损坏隔离、受控清理 |
| `history.py` | 校验 session 身份，流式 JSONL offset 索引、增量刷新、内容与附件、截断识别 |
| `timeline_query.py` | 元数据与源历史合流、明确 ID 关联、轮次／child 范围、只读查询 socket 与离线回退 |
| `events.py / engine.py / collector.py` | 补充 steering 元数据、时间线观察 hook 与独立查询承载；保留既有指标语义 |
| `MonitorModel/Timeline.swift` | 版本化 DTO、共同线性布局、固定轨道、短块与重叠命中 |
| `TimelineClient / TimelineStore` | 后台 JSON helper、轮询取消、迟到响应保护、稳定导航与选中内容状态 |
| `TimelineSurface / TimelinePanelView` | AppKit 视口绘制与横向滚动、SwiftUI 控件、稳定可访问元素与重叠候选 |
| `ActivityViewer.swift` | 单普通窗口、位置尺寸保持、正文与差异阅读、关闭释放详情加载 |

实际 transport 是 `codex-progress timeline-query <JSON>`。`action=timeline` 返回轮次、所选轮活动与关系；`action=detail` 返回选中活动内容。collector 活跃时使用独立的 `tl.sock`，不通过 producer socket 或向 Codex writer 发送请求；collector 不在时读取侧文件与 JSONL，不启动采集。

## 固定参数与数据边界

| 参数 | 当前值 |
| --- | --- |
| 时间比例 | 默认 4 pt/s；缩放限制 0.25–64 pt/s，共同线性比例 |
| 视觉／命中 | 最小视觉宽 1 pt；最小命中宽 12 pt，不改变时间坐标 |
| 窗口 | 默认 800×600 pt，最小 640×420 pt；位置尺寸记忆并校正到可用屏幕 |
| GUI 查询 | 展开或查看器可见时约 1 秒；收起取消专用任务 |
| 详情分块 | 每个内容区每次至多 262,144 字符，提供继续读取游标 |
| 实时工具临时内容 | 仅选中活动有界内存尾部，至多 262,144 字符；不另存全文 |
| pin／裁剪 | pin 10 秒，由查看器续期；约 5 秒检查清理；保留当前＋最近 5 轮及活跃／选中所需关联 |
| 最近完成 | 沿用约 600 秒、最多 10 个主聊天的入口；活跃或 pin 关系不按此提前移除 |
| 历史索引 | 内存缓存最多 12 个 thread，保存 offset／身份元数据，不保存完整消息库 |
| 本地查询 | UNIX socket 0600，最多 3 个并发 worker，10 秒连接超时；GUI helper 12 秒超时 |
| 侧文件 | 产品数据根 `timeline/metadata.sqlite3`；隔离／CLI 模式在自己的 runtime 下；目录 0700、文件 0600 |

模型轨最终浅色／深色色值依次为：思考 `#8968B3 / #9A80C0`、正文 `#7957AC / #B097D1`、参数 `#6A429E / #C3A2E1`、编辑 `#4F2B77 / #DCBAF1`。工具为 `#B17130 / #D49B57`。已有自动检查确认色块在对应基础背景上至少 3:1；未知起点／不确定状态另以低透明度和文字表示，不对这些降级样式作相同对比度保证。

## 已执行验证

| 层次 | 结果与证据 |
| --- | --- |
| Python 针对性检查 | 185 项通过，含新投影、历史、存储与查询，及受影响生命周期、文件编辑、用量／菜单栏、管理边界；本机日志 `.local/timeline-python-tests.log` |
| Swift 常规检查 | 21 项模型＋17 项 App 检查通过；另 1 项需要 WindowServer 的窗口检查在常规运行中跳过；日志 `.local/timeline-swift-tests.log` |
| 原生窗口检查 | 显式启用窗口测试后 1 项通过，检查真实窗口复用、用户尺寸保持、关闭重开、普通层级与可调整大小；日志 `.local/timeline-window-tests.log` |
| 隔离真实 app-server | steering 保持原 turn；错误 expectedTurnId 被拒绝；实际 camelCase 开始／完成时间；JSONL 内容与参数窗口关联，见 [来源验证](source-validation.md) |
| 在线 bridge／collector／query | 实际命令 exit 0；2 段已完成参数生成区间能从 Codex 历史恢复全文；1 段被 steering 中断的输入未落盘，明确返回 `not_retained`；投影错误 0 |
| 真实跨轮进程 | 第一轮结束后实际进程仍活跃；第二轮有旧轮入口；完成后约 11,922ms 区间仍属原轮；投影错误 0，隔离配置 hash 未变 |
| 原生 Preview 操作 | 默认收起、双轨、child 汇总／展开、真实鼠标重叠菜单、键盘切换、选中项稳定、纵向滚动传递、diff、窗口复用与关闭；截图均为合成数据 |
| 日常 App | 本地构建已更新并正常重开；实际聊天可展开；选择已落盘历史轮的正文可读取；`Cmd+W` 后 collector 继续运行、`tl.sock` 和版本化 summary 可用、投影错误 0 |

详细需求／场景对应及证据层次见 [validation-results.json](validation-results.json)。VoiceOver 完整朗读、最小化／多显示器切换的人工全流程、长期自然 child 复用与所有错误分支的原生操作尚未逐项执行；已用可访问树、纯布局、状态测试及相应保守降级覆盖基本契约，不把它们写成全面自然运行证明。

只读性能采样：指定 paginated 文件约 30MB，282 个阶段、15 轮，冷索引 205.73ms，无变化刷新 0.25ms，Python traced peak 10,964,463 bytes。这是一次本机采样，不是全场景性能保证。历史解析在查询 worker／helper，侧存储在异步 writer，原生绘制只处理当前视口；没有重做 bridge 全负载基准。

## 需要保留的限制

1. **历史不包含一切。** canonical item 的有效起止可恢复生命周期；raw 参数最终文本及 JSONL 外层 timestamp 不能恢复参数生成起点。未观察到的生成区间保持未知。
2. **中断输入可能不落盘。** steering 中断的未完成参数在真实隔离运行中未被 Codex 保存。已在窗口加载的临时内容可保留至关闭；没有取得的内容不补造。
3. **源文件可能滞后。** 日常聊天中也观察到实时活动已经完成、当前源 JSONL 尚无对应记录。查看器说明当前内容不可用并可重试；不能把每个完成事件都等同全文已经可读。
4. **跨轮不是重归属。** 真实 process 路径已验证；child 的自身 parent_turn_id／spawn 首轮范围依据固定版本源码、合成事件和原生合成预览验证。本次未为验收另开真实子智能体，不声称所有自然 child 复用方式已覆盖。
5. **兼容按能力降级。** 本机真实记录是 `0.163.0-alpha.10.2` 的 paginated。legacy、归档、缺字段、半行、替换、损坏等有合成测试，不构成所有历史版本和 Remote 存储兼容保证。
6. **时钟不可信时不画伪精确轴。** 检测接收 wall／monotonic 跳变或不合法边界后标明质量；无法共同定位时仍允许选择可用活动内容。
7. **重载有边界。** 日常 GUI 与 collector 已载入新代码；本轮开始于此前，速度／用量和部分时间可能暂为 `—`。已运行的 Desktop bridge 要在该聊天工作结束后重开 Codex 才载入新增 transient output 过滤；没有在本次中途重启 Desktop 或更改 Remote 路由。

日常 App 正常 bootstrap 会沿用既有 configure／resume 路径更新接入收据，不能以整个 `integration.json` hash 不同判断接入配置被破坏。本次只记录该正常重载；原配置恢复与 Remote 全链路不重新宣称验收。

本轮未提交、未推送、未制作公开发行包。签名／公证、全新机器与长期自然使用保持独立验证范围。

# 技术方案

日期：2026-10-20。基线：`d2b0986`。本章字段名是实施建议；对外兼容原则是必需约束。

状态：已实施，实际字段、测试和限制见[实施结果](implementation-results.md)。header 中的父身份不作为新的父轮挂靠依据，组与轮次归属继续由既有明确关系提供。

## 1. 身份读取与验证

在 `RolloutIndex.refresh()` 已经读取 `session_meta` 的位置提取有限身份元数据，先验证 `payload.id == requested thread`。当前样本证实以下结构存在：

- `payload.agent_nickname`。
- `payload.source.subagent.thread_spawn.agent_nickname`。
- `payload.source.subagent.thread_spawn.agent_path` 及明确的 `parent_thread_id`。

只接受预期类型的有界字符串。agent role 可能为空；不能把空值补成推测职责。顶层／嵌套字段冲突时保留质量信息，宁可使用可区分 ID，也不静默猜测。

索引在文件替换、截断或身份验证失败时同步失效身份元数据，不能把前一个文件的昵称沿用到新对象。元数据变化应反映在查询 revision 中。现有 engine 中真实结构化线程名可参与名称优先级；排除 `thread.id[:8]` 等已知回退值。

第一阶段以已验证 JSONL 字段和现有 engine name 为来源。没有真实事件样本支持的实时 nickname 字段不直接加入事件 allowlist；历史尚未写出时可使用回退名，后续刷新自然补全。

不新增历史目录扫描，不增加 app-server 请求，不额外启动 writer。身份元数据只保存在现有索引缓存及查询 DTO，不新增完整会话或消息存储。

## 2. 请求内组装

`TimelineQuery.bundle()` 继续使用 `d2b0986` 的 `loaded[thread]` 请求内缓存。读取身份、形成对象名称、构造协作对象引用都复用本次已加载的 index／relations，不再次调用完整历史加载。

名称解析是按完整 `(scope_id, thread_id)` 进行的纯展示步骤。现有父轮与 child turn 的关联判定、phase identity、merge 和 raw／canonical 区分不随命名改变。

状态聚合只使用当前 bundle 已明确引用的 child turns／phases。active／waiting、终止、未知与覆盖不足按设计中的证据要求形成展示状态，不能简单取整个 child 历史的最后一轮状态。历史视图不借用另一个父轮的当前活动。

## 3. 查询 DTO 与兼容

| 对象 | 建议增量 | 兼容方式 |
| --- | --- | --- |
| `agents[]` | `display_name`、`name_source`、可选 `nickname`／`agent_path`、`status_scope` 与展示质量 | 保留现有 `id/thread_id/name/parent_thread_id/status/quality`；新增字段可选 |
| phase 展示信息 | 可选 `coordination_action`、`related_agent_ids` | 由明确调用与关系投影，不修改原 phase ID、track、kind 或时间 |
| Swift 模型 | 可选解码新增字段，统一计算 display name／协作文案 | 旧 DTO 缺字段时继续使用现有 name、item type、label 和 unknown 降级 |

维持 bundle `version=1` 的添加字段兼容方式。旧 GUI 忽略新增字段仍能读取，新 GUI 能读取旧 sidecar／collector／fixture；保留 `name` 作为完整、可理解的兼容名称。

只有 canonical `collabAgentToolCall` 或已有明确协议类型才能投影协作语义。relation 必须在相同 scope、父 thread、父 turn 内，以明确调用 ID 与 phase item ID 关联。目标无法证实时只显示“协作调用／对象未知”。不能按名称、代码片段、相邻时间或某个 exec 中出现 collaboration 字符串关联。

协作动作通过已确认 operation 枚举映射，未知 operation 保留通用文案。原始 `subAgentActivity` 关系／状态补充不直接变成新的执行阶段。原有参数／结果读取与分页不参与身份命名，也不被改写成生成摘要。

本轮不需要 SQLite schema 迁移。新增展示字段在查询时计算；已有 sidecar 中的 `item_type` 和关系仍可供投影使用。若来源本身缺少明确字段，降级而非为补齐 UI 修改原历史。

## 4. 面板与查看器隔离

已确认 `ActivityViewer.swift` 也使用 `TimelinePanelView(compact: false)`，而 `TimelineStore.lanes/layout` 当前被两种界面共享。不能直接在全局 lanes 中加主组标题。

建议增加显式展示模式：`panelGrouped` 与 `activityViewer`，由两个调用位置明确传入。模式只决定 lane／header 的呈现，不进入数据所有权与历史查询参数。

- lane builder 接收展示模式，在 panelGrouped 下生成主／子 header 及折叠 summary；activityViewer 保持既有 lane 排布。
- `TimelineStore` 继续保存 bundle、完整 ID 对应的展开集合、选中 ID、缩放及阅读状态；布局按 mode 计算，不用一个可变全局 mode 切换两个窗口。
- 组标题使用独立 header lane，其位置来自同一布局计算；标题覆盖整行，右侧绘图在 header lane 中留空。summary 与 model/tool 才映射真实活动。
- `TimelinePanelView` 的标签、标题、绘图区和可访问元素共用同一 lanes／layout，避免左右 y 坐标错位。
- `TimelineSurface` 沿用来源 x 坐标、最小宽度、工具局部重叠与点击选择逻辑；只增加标题空行适配与准确的所属智能体／协作描述。

有明确多层 parent 时按父组顺序和已有稳定顺序排布，视觉缩进封顶；显示名变化不重排。关联不完整的对象仍有独立身份和质量说明，不强制变成普通工具或移到错误父组。

## 5. 状态与交互稳定性

展开集合继续按完整 thread ID 保存；phase 选择继续使用原 phase ID。名称补全、同名消歧和新增 header lane 都不修改这些键。横向阅读位置按来源时间保存，增加标题只影响垂直坐标。

同一时刻面板与活动窗口采用各自布局计算，并复用已有数据／选中状态。验证切换、重开和尺寸变化时没有把一种模式的 lane 缓存用于另一种模式。旧 UI 是否允许共享展开状态沿用当前行为，本轮不重定义窗口状态管理。

## 6. 主要风险与对策

| 风险 | 对策 |
| --- | --- |
| 昵称格式因历史版本不同缺失 | 严格可选读取、有界值、旧字段回退、错误身份测试 |
| 多个 ID／昵称碰撞 | bundle 内消歧，完整 ID 始终作为关联键 |
| “已完成”错误表达整个 agent 结束 | 状态限定所选引用范围，缺少结束／覆盖证据时显示未知 |
| 分组改动污染活动窗口 | 显式 presentation mode，分别验证 lane 排布与窗口行为 |
| header 增行导致标签与命中错位 | 使用同一布局源，检查 y 坐标变化后对应 phase ID 不变 |
| 元数据读取再次引入卡顿 | 复用已读 session header 与 loaded cache，验证每线程每请求最多加载一次 |
| 协作对象被猜测或重复计数 | 仅采用明确 ID 关系，展示投影不复制 phase、不进入 usage 统计 |

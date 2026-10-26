# 实施计划

日期：2026-10-20。状态：P0–P4 已完成；实际范围及证据见[实施结果](implementation-results.md)。改动前代码基线：`d2b0986`。

## 1. 预计落点

| 文件／区域 | 改动目的 |
| --- | --- |
| `src/codex_progress_monitor/history.py` | 已验证 session header 的身份读取、缓存与失效 |
| `src/codex_progress_monitor/timeline_query.py` | 命名、消歧、轮次状态、协作对象的添加字段投影 |
| 可选独立身份展示 helper | 若命名／状态纯函数使 query 明显膨胀，拆为小模块；不建立新服务或存储 |
| `macos/Sources/MonitorModel/Timeline.swift` | 可选字段兼容、显示语义、按展示模式生成 lanes |
| `macos/Sources/MonitorApp/TimelineStore.swift` | 从共享全局布局改为按模式计算，保持现有交互键 |
| `macos/Sources/MonitorApp/TimelinePanelView.swift` | 全宽组标题、折叠汇总、状态、协作辅助描述 |
| `macos/Sources/MonitorApp/TimelineSurface.swift` | header 空行适配、所属智能体／协作可访问描述 |
| `macos/Sources/MonitorApp/ChatDetailsView.swift` | 明确使用 panelGrouped 模式 |
| `macos/Sources/MonitorApp/ActivityViewer.swift` | 明确使用 activityViewer 模式，复用现有文本位置显示名称／协作语义 |
| `macos/Sources/MonitorApp/Resources/zh-Hans.lproj/Localizable.strings` | 本轮新增的身份、状态和协作文字 |

预计新增／调整现有 Python、Swift 模型、App 状态、原生预览约 4–6 个测试／验证文件。具体以完成所需验证为准，不为达到文件数拆分实现。

## 2. P0：冻结基线和定向 fixture

- 保存当前干净／混合工作区情况与本轮文件清单。
- 使用三子智能体、两个 ID 前缀相同的脱敏 fixture；包含真实 header 字段结构，但不提交原始私有会话。
- 保留“528 条关系、3 个 child”的重复加载回归条件和现有查询计数测试。
- 确定 panelGrouped／activityViewer 两种模式的预期 lane 层级。

退出条件：测试数据能复现命名碰撞和原 UI 层级问题，来源身份与预期明确。

## 3. P1：身份与查询增量

- 在现有索引中提取身份字段，处理缺失、错误类型、冲突及文件替换失效。
- 实现有界名称解析、回退值识别和同名消歧；元数据更新影响 revision。
- 添加兼容 DTO 字段，形成限定所选轮次的状态和明确协作对象引用。
- 保持现有所有权与 history merge 算法，复用请求内 loaded cache。

退出条件：身份、跨 scope、历史追加／替换、旧 DTO 与重复关系测试通过；无新查询通道或持久化 schema。

## 4. P2：分组布局与交互

- Swift 模型兼容添加字段与旧 payload。
- 显式隔离两种展示模式，面板增加全宽身份标题，折叠时显示紧凑汇总。
- 同步左右布局、phase 命中和辅助功能归属；保留完整 ID 交互状态。
- 现有详情文字复用准确身份与协作语义，沿用原始内容展示。

退出条件：有／无子智能体、折叠／展开、名称补全、换轮与查看器模式的定向测试通过；活动 x 坐标和 owner 不变。

## 5. P3：原生预览与小范围回归

- 本机只读预览展示 420 pt、浅／深色、三子智能体和长名称等状态。
- 检查组标题、状态、纵向滚动、横向比例、键盘选择及辅助功能名称。
- 仅验证受共享组件影响的独立窗口行为，不重复整个历史功能验收。
- 运行查询性能对照，记录首读、后续刷新、每线程加载次数及来源限制。

退出条件：通过[验收计划](validation-plan.md)中的新增行为与兼容检查，截图和数据证据能够区分实现结果与设计建议。

## 6. P4：日常构建与交付

- 完成源码 diff、私有数据、文档链接和构建／源码指纹检查。
- 构建日常 GUI；Python 改动按既有 stop／start 生命周期重载 collector，GUI 更新重开对应 App，保留接入与恢复记录。
- 真实读取有子智能体的历史轮次，确认名称、分组、内容归属和查询延迟。
- 将实际参数、验证结果和残余问题记录到本章 `implementation-results.md`，此文件在有实际结果后再创建。

P0–P4 的实际完成情况及其证据范围以实施结果为准。当前已构建、重载和验证，尚未提交；用户请求 commit 时再按实际文件白名单提交。

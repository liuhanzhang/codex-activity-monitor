# 实现与验收结果

日期：2026-10-28。起始基线：`6c39325`。本章随功能一并提交。独立窗口、接入状态、运行诊断及本地日志已实现；当前机器上的隔离验收通过。初次验收未执行日常安装切换；随后按用户请求完成原位更新及 Monitor 重启，见第 6 节。真实 Codex 完整重启和发布仍未执行。

## 1. 实际交付

| 范围 | 实现与入口 |
| --- | --- |
| 原生设置 | [SettingsWindow.swift](../../macos/Sources/MonitorApp/SettingsWindow.swift)：保留单一 `NSWindowController`，`NavigationSplitView` 侧栏与分组表单（按后续反馈移除顶栏折叠按钮），通用／运行状态／日志。760 × 560 pt 起始、680 × 460 pt 最小内容尺寸。齿轮及 Command-, 打开，Command-W 关闭；共享原 `DashboardStore`，不新增观察租约。 |
| 通用设置 | 应用语言、登录启动、Agent 接入和维护分组。Codex 开关调用原事务管理；普通关闭不清理数据，卸载另有确认。未知／部分状态禁止普通切换，提供检查、重新配置或继续恢复。操作错误与已确认配置分开显示。 |
| 接入证据 | [integration_status.py](../../src/codex_activity_monitor/integration_status.py)：有界只读检查配置、进程身份及 bridge 注册。规范化配置及实现指纹形成稳定代次，launcher 固定启动代次和预期环境指纹，bridge 核对实际继承值。GUI 还须取得新鲜、匹配的实时连接才显示已生效。 |
| 状态投影 | [IntegrationPresentation.swift](../../macos/Sources/MonitorApp/IntegrationPresentation.swift)：区分已配置、等待启动、旧进程待重启、部分已生效、运行不可用和检查失败。新旧连接并存不掩盖重启要求；关闭后保留恢复／待清理信息。 |
| 自诊断 | [diagnostic_health.py](../../src/codex_activity_monitor/diagnostic_health.py)：collector、bridge、解析、tokenizer、历史读取、元数据、查询、检查点及日志。GUI 独立保留连接、管理、偏好写入和日志错误，并报告上次未正常结束。当前错误与累计失败分开，恢复有新的成功依据。 |
| Python 日志 | [diagnostic_log.py](../../src/codex_activity_monitor/diagnostic_log.py)：四类 JSONL、字段白名单、后台 writer、非阻塞锁、重复合并、轮转及归属检查。顶层／线程意外异常只记录类型和允许字段。collector 原始输出使用独立非阻塞管道排空和有界兼容文件。 |
| GUI 日志 | [DiagnosticRecorder.swift](../../macos/Sources/MonitorApp/DiagnosticRecorder.swift)：独立队列、内存失败信号、保留期维护和正常结束标记；失败后需成功写入才解除日志故障。所有权未确认时不创建安装。 |
| 日志页面 | [DiagnosticLogClient.swift](../../macos/Sources/MonitorApp/DiagnosticLogClient.swift)、[DiagnosticLogView.swift](../../macos/Sources/MonitorApp/DiagnosticLogView.swift)：独立 actor 读取，来源／级别／中英文搜索、分页、详情、复制、打开目录。时间按本地时区显示；坏行、未完成尾行、未知代码和读取受限均可降级。 |
| 原生背景与兼容 | 删除主面板显式 `.background(.regularMaterial)`，保留原 `NSPopover` 的系统背景；未引入玻璃 API 或材质版本分支。部署目标仍为 macOS 14，诊断与接入摘要带独立版本、可选解码，不使旧活动数据失效。 |
| 清理及说明 | 普通恢复保留 `diagnostics/`；卸载采用明确文件 allowlist，未知文件阻止扩大删除。中英文 README、用户指南、架构和本章同步。 |

实现没有把工具失败解释为 Monitor 失败，也没有以无聊天、无 token 或历史观测缺口推断 Codex 已失败。未添加远程日志服务或其他 Agent 的实际适配。

## 2. 资源及实现取舍

- 每进程队列最多 256 条／256 KiB，事件最多 8 KiB。重复签名最多 64 个，5 s 汇总，首条即时排队；队列满丢弃新记录并增加计数。首版没有严重度优先队列，不承诺日志完整。
- 每角色当前文件 2 MiB、三个归档，四角色约 32 MiB；原始 collector 当前／归档各 2 MiB。固定文件数量使正常归属文件在 64 MiB 设计预算内，无无界文件扫描。私有目录／文件分别采用 0700／0600。
- 每分钟后台维护保留期；按文件出生时间淘汰，reader 再按记录时间排除超过七天的事件。这可能提前淘汰同一过期文件中的较新事件；保留期是上限。
- 日志页初始 200 条，最多 1,000 条／2 MiB 编码记录。默认有界尾部窗口，文件身份、大小、修改时间未变则缓存。搜索最多扫描 64 MiB／1 s，支持取消及受限提示；首版没有跨轮转持续游标。
- 历史、元数据及检查点 worker 只在有未完成工作时检查 5 s 截止；空闲不报警。解析／丢弃增量采用 5 s 近期窗，累计数保留。GUI 日志恢复探测间隔 5 s。
- 页面选择用保留视图的局部状态，日志筛选用 `DiagnosticsStore`，健康投影用共享 store 的独立扩展，未再建一层 `SettingsStore`。
- 管理和配置读取有截止；状态检查不调用会删除旧注册信息的清理路径。日志失败不能反过来阻止配置恢复或字节转发。writer 遇慢盘可能无法及时落盘，退出只作有界尽力收尾。

## 3. 自动化证据

机器：Apple Silicon，macOS **27.0（26A428）**，SDK **27.0**，目标 **macOS 14.0**。本机不是 macOS 26，也不是旧系统兼容验收机。

| 检查 | 结果与边界 |
| --- | --- |
| Python 全套 | `PYTHONPATH="$PWD/src" .local/venv/bin/python -m unittest discover -s tests`：**503 项，通过**。包括当时新增 16 项诊断／管理测试及既有协议、生命周期、管理失败恢复。最后的卸载 writer 顺序修正后，定向诊断套件 **17 项通过**，并重跑隔离完整链；未重复 90 s 的全套。 |
| Swift 全套 | `swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos`：Model **68 项**、App **132 项**，套件通过；默认共 **11 项 opt-in 跳过**，实际执行 189 项。跳过项包含原生窗口、性能和离线 tokenizer 专项，不能当作本轮通过。 |
| 原生剪贴板 | 单独设置 `MONITOR_WINDOW_TESTS=1` 执行 `diagnosticCopyWritesTheSelectedJSONToAPrivatePasteboard`：**1 项，通过**。使用独立命名 pasteboard，不依赖测试沙箱中不可用的 WindowServer 通道。 |
| 最后定向修正 | 新增跨四角色搜索的编码容量测试，通过；随后完整 Swift 套件通过。最后 Preview 已包含该修正及恢复提示文案。最终审查还将管理 writer 的停止移到 restore／cleanup 调用之前，避免与 purge 删除竞争；新增此顺序约束测试并通过。 |
| 隔离完整链 | [verify_settings_diagnostics.py](../../tests/acceptance/verify_settings_diagnostics.py)：**11 个检查，通过**。真实子进程、socket、collector／bridge、临时 CLI、文件环境；不是纯字典 fixture。摘要见 [evidence.json](evidence.json)。 |
| 本地化 | `PYTHONPATH="$PWD/src" .local/venv/bin/python bin/export-localizations --check` 通过，词库导出及 GUI 双语键一致。 |
| Preview 构建 | `bin/build-gui --preview` 成功；使用本工作区源码和虚拟环境，最终源码指纹与构建记录一致，ad-hoc 签名校验通过。未重建日常 `.local/gui`。 |

Python 验证曾发现继承的 `PYTHONPATH` 指向主 checkout。早期 487 项结果作废，改为显式指向本工作区并重新执行上述 503 项。不要复用没有确认模块来源的结果。

新增覆盖见 [test_diagnostics.py](../../tests/test_diagnostics.py) 与 [DiagnosticsTests.swift](../../macos/Tests/MonitorAppTests/DiagnosticsTests.swift)。重点包括：多进程轮转、坏 UTF-8／尾行／文件替换、不可写与队列压力、32 MiB 原始输出排空、worker 期限与空闲、局部故障恢复、旧进程身份与代次、重复配置不虚构重启、用户后改值恢复、旧 schema、归属与 purge。

完整链顺序：稳定配置代次 → 真 bridge 启动证据 → 新鲜健康摘要 → 普通恢复并等待 bridge → 恢复后原始 stdio 仍转发 → collector 停止 → 依赖退出后清理 → 关闭状态保留 → 独立日志保留 → 再开启 → 完整 purge。

## 4. 原生交互实测

通过 Computer Use 操作本工作区 Preview，使用只读合成诊断和 `/tmp` 一次性管理 profile。临时 profile 使用独立 `CODEX_HOME`、模拟 CLI 和文件环境，不读真实聊天、不写真实 `launchctl`、不改变登录项。临时实例已正常退出，测试 profile 的归属数据已 purge。

1. 齿轮打开独立窗口，侧栏三页及分组表单可见；关闭设置后 owner 仍连接，collector 实例不变，重新打开复用窗口。
2. 首次安装自动开启，显示等待 Codex；启动临时真实 bridge 后显示已生效。中文切换英文立即作用于当前窗口，重开仍保留 English。
3. 关闭开关先显示处理中，再显示配置已恢复但等待 Codex 退出；临时 bridge 退出后恢复完成。日志页仍可读取。正常退出并重开后开关仍关闭；再次开启成功。
4. 注入的元数据局部失败在运行状态显示降级，相关日志入口筛选该组件，能读取失败和恢复记录。受控终止预览后，下一次出现“上次未正常结束，原因未知”，未宣称崩溃。
5. 日志搜索 `restore` 缩小到恢复事件；选中后详情含后端操作标识。点击复制，再用原生 Command-V 粘贴到搜索框，得到所选完整 JSON。新增标准 Edit 菜单支持原生选择／复制／粘贴。
6. 卸载确认 sheet 能打开并取消，没有在 UI 执行永久删除；隔离 CLI 完整链验证实际 purge。Command-W、Command-,、Command-Q 均执行预期行为。
7. 浅色及应用自身 `--preview-dark` 深色模式下，设置和主弹出面板可读、无额外背景覆盖；保留原生圆角与 popover 箭头。没有为测试更改系统外观。

期间 Mac 锁屏，原生操作暂停；用户解锁后继续完成以上步骤。曾有两个同标识 Preview 实例导致工具选中旧只读实例，已按确切可执行路径关闭本工作区旧实例，再继续验收；未操作日常 Monitor。

## 5. 验收映射与剩余范围

| 场景 | 证据 |
| --- | --- |
| A01 | 原生同窗、关闭不停止观察、owner 连接／实例检查；沿用生命周期测试。 |
| A02 | 原生双语、分组、卸载确认取消及退出；登录偏好错误有既有测试。**真实系统登录审批未触发**，隔离模式故意禁用登录项。 |
| A03–A04 | 原生首次自动开启、关闭重开、再开启；后台原值／空值／未设置、用户后改值及重配测试。 |
| A05–A06 | 既有管理失败后回读测试、新接入投影测试、旧／未知／混合 bridge、真实临时桥接与恢复等待。 |
| A07–A09 | 既有 nonce／主循环新鲜度测试、组件故障恢复和 worker 期限测试、历史计数恢复、局部失败 fixture；旧健康不能续期。 |
| A10 | 原生日志筛选、搜索、详情、复制；自动化预算、压力、锁／轮转、不可写、异常结束与恢复。 |
| A11 | 隔离恢复保留日志／purge、新文件 allowlist、未知文件拒绝清理及重试测试。 |
| A12 | **macOS 27 本机浅色／深色已验证**；下列系统或辅助功能未取得实测。 |

剩余验证明确保留：

- macOS 14／15／26 实机外观与交互；最低部署目标和编译成功不能替代它们。
- 减少透明度、不同桌面背景和多屏边缘的专项视觉验收；本轮未修改这些系统设置。原 popover 定位／关闭测试保留，没有改写该逻辑。
- 真实登录项审批、日常安装原位更新、旧驻留 bridge 升级和真实 Codex 完整回合。需要时由用户在任务结束后完全退出并重开 Codex；隔离 bridge 的结果不能代替它们。
- 本轮没有测量长时间驻留的 CPU／内存曲线，没有重打 standalone 发布包或执行另一台机器的首次安装。资源边界测试证明的是受控上限与非阻塞行为。

这些是发布和环境覆盖边界；当前没有为了通过测试而清除日常错误、改写原始聊天或重启用户任务。日常版本更新见第 6、7 节；代码提交按后续用户请求执行，未推送。

## 6. 后续：日常版本原位更新

同日用户明确要求更新并重启正在使用的 Monitor，现已完成：

- 确认旧实例位于原仓库 `.local/gui/Codex Activity Monitor.app`，使用 Application Support 下既有数据，接入已开启。先构建正式身份的 source GUI，指纹为 `cfd47c9d2e88de6ae456f55bdf83dc7ef519bb814e5a4a99c0918949b1c49020`，源码比对及签名验证通过。
- 通过旧菜单正常退出 Monitor，确认旧 GUI／collector 均结束；原位替换同一路径的 App 并打开新设置窗口。旧 App 与管理状态的私有回退副本保存在工作区忽略的 `.local/daily-update-backup/`。
- 保留配置记录身份、原配置备份哈希、中文偏好、登录启动设置；既有时间线数据库 inode 不变。没有恢复接入、清理用户数据或改动原仓库受跟踪源码。
- 日常 App 的 `backend.json` 现指向本次工作区的源码及 `.local/venv`。这是源码开发构建，运行仍依赖该工作区；后续合并并从稳定 checkout 重建后再移除工作区。
- 确认只有一个新 GUI 和 collector，新 collector 实现指纹匹配当前源码，独立健康摘要版本 1 已启用。原生日常运行状态页显示采集连接、collector、bridge、解析、计数、历史、存储、查询、检查点与诊断日志均正常。
- 已有 Codex／bridge 继续运行。旧 bridge 不具备新的启动配置证据，通用页显示已配置但等待确认，不能声称新证据已全面生效。任务结束后完整重开 Codex 才能验证新启动上下文；本轮未结束 Codex 任务，也未将短时恢复当作自然长期使用验收。

本节只证明日常应用与 collector 已更新、数据和偏好保留，以及当时的实时健康；未扩大第 5 节的旧系统、系统审批、真实 Codex 重启及长期性能结论。

## 7. 后续：移除侧栏折叠按钮

用户反馈展开／收起时顶栏按钮位置跳变。最终决定只移除顶栏按钮，保留 `NavigationSplitView` 的原生分栏、拖动调整宽度及拖动隐藏行为。使用 macOS 14 起可用的 `.toolbar(removing: .sidebarToggle)`，不增加版本分支或自定义玻璃层。

该调整同时移除了多余的自动工具栏，窗口使用标准居中标题。侧栏继续使用系统 `List`，页面仍为原生 `Form`／`Table`。

验证以定向 Swift 回归、Preview 构建与原生操作为主；未重复未变化的 Python 协议测试。实际更新和检查结果记于本章 evidence.json 的 sidebar_adjustment。

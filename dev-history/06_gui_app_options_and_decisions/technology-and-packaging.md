# GUI 技术栈与 Python 打包方案

日期：2026-10-16 更新。状态：讨论与实施基线记录。已确认保留 Python、目标 macOS 26+，本机采用 SwiftUI 视图 + AppKit 菜单栏 / 窗口；自包含打包工具仍为候选，未构建发行产物。

决策总览见 [README](README.md)，执行基线与分步验证见 [实施计划](implementation-plan.md)，接入与恢复见 [采集与生命周期](capture-and-lifecycle.md)。

最新范围见 [产品需求](requirements.md)：当前先在本机运行，GitHub 开源与 Release 为后续目标。自包含打包与免 Python 安装仍是发行目标；签名、公证、全新机器离线验收及跨架构交付不作为当前本机里程碑的前置条件。未以本机原型证明这些发行能力。

## 1. Python 如何随 App 交付

Python 可以打包成用户直接执行的程序。以 PyInstaller 为例，产物包含所需的 CPython 运行时、项目代码、第三方依赖和数据资源；用户无需安装 Python 或 pip。

因此，“提供可执行程序”和“随 App 携带 Python 运行时”可以同时成立。它通常不是把 Python 代码转换成一个完全不含解释器的原生二进制，也不需要复制开发机的整个 venv。

| 方案 | 工作方式 | 对本项目的判断 |
| --- | --- | --- |
| PyInstaller `onedir` | 可执行程序与运行时、扩展和资源放在同一个目录 | 当前优先推荐。可嵌入 `.app`，目录结构稳定，便于诊断与签名。 |
| PyInstaller `onefile` | 将依赖归入单文件，启动时解包到临时目录 | 可行但不优先。增加启动解包及临时文件管理；macOS 的 `.app` 已能呈现为一个整体。 |
| 手工嵌入 CPython | 自行组织解释器、代码、依赖、加载路径与启动入口 | 可行，需要自行维护更多打包与运行环境细节，暂不优先。 |
| Nuitka 等编译 / 独立分发方式 | 改变代码编译与依赖收集方式 | 曾作为方向提及，未调查或比较，不认定其体积、启动或性能更优。 |

构建工具和完整 pip 开发环境不需要交付；实际需要的 Python 模块、原生动态库、证书及 tokenizer 资源必须完整。不同系统和架构需要分别验证构建，不能假设一个 macOS 产物可直接运行于 Windows / Linux。

依据：[PyInstaller 工作方式](https://pyinstaller.org/en/stable/operating-mode.html)和[发行选项](https://pyinstaller.org/en/stable/usage.html)。

## 2. 体积的读数与估计

以下为 2026-10-14 对话期间对本机 Python 3.14.7 / `tiktoken` 0.14.0 环境的只读统计，单位为 MiB。它们是已安装组件大小，不是冻结后的发行包；也不是本轮重新执行的打包验收。

| 组件 | 本机读数 | 边界 |
| --- | --- | --- |
| Python 解释器动态库 | 约 5.2 MiB | 仅解释器库，不含标准库及其动态依赖。 |
| 标准库 Python 源文件 | 约 10.1 MiB | 排除 site-packages、测试、部分 GUI / 开发辅助目录；实际按需收集并可能压缩为字节码归档。 |
| 标准库原生扩展 | 约 7.3 MiB | 本机扩展目录总量；不是所有扩展都必然进入产物。 |
| `tiktoken` 及核心第三方依赖 | 约 6.4 MiB | 统计时排除字节码缓存，包含实际安装的原生扩展。 |
| `o200k_base` 与 `cl100k_base` 词表 | 约 5.1 MiB | 其中约 3.45 / 1.60 MiB；其他编码是否包含取决于产品支持范围。 |
| OpenSSL 动态库 | 约 5.5 MiB | 本机 `_ssl` / `_hashlib` 依赖的两个库；实际构建须检查传递依赖。 |
| Rich、Pygments 等终端渲染依赖 | 约 12.1 MiB | 若 GUI 发行包不包含终端看板，可评估排除；是否仍随包交付 CLI 尚待决定。 |

对话中的初步预算为：**ARM64 单架构、原生 GUI、常用词表、排除终端渲染组件时，安装后约 40–80 MiB，压缩下载包约 15–35 MiB。**

该区间是估计，不是实测产物大小，也不是已确定的包体上限。架构数量、依赖收集、资源、签名与压缩方式均会改变结果。用户已表示可以接受几十 MiB，因此当前不因体积而重写核心。

## 3. 当前源码不能完全原样冻结

| 当前实现 | 发行版需要适配的地方 |
| --- | --- |
| [cli.py](../../src/codex_activity_monitor/cli.py) 和 [integration.py](../../src/codex_activity_monitor/integration.py) 用 `sys.executable -m ...` 启动子进程 | 冻结后 `sys.executable` 指向产品程序，需要定义清楚的桥接 / 采集子命令入口；不因此增加常驻卸载 Helper。 |
| [runtime.py](../../src/codex_activity_monitor/runtime.py) 从源码目录推导 `.local/runtime` | 区分 App 内只读资源与用户目录中的设置、socket、快照和恢复记录，不能向签名包写运行数据。 |
| `source_identity()` 读取 `.py` 文件计算指纹 | 冻结后改用可复核的构建身份或随包 manifest，不能假设源码文件仍存在。 |
| 当前包装器保存开发机 Python 与 CLI 的绝对路径 | 轻量脚本与备份移到 App 外的固定目录，回退分支不依赖 App 内 Python；处理 App 缺失、移动、升级和旧进程依赖。 |
| `tiktoken` 动态加载编码资源 | 显式收集扩展、动态导入和所需词表，验证无系统 Python、无开发缓存、离线的首次运行。 |

词表加载可能在缓存缺失时联网下载；“开发机可运行”不证明新机器离线可用。见 [tiktoken 资源加载源码](https://github.com/openai/tiktoken/blob/main/tiktoken/load.py)。

## 4. 是否重写底层

### 4.1 比较

| 方案 | 收益 | 成本与风险 | 当前选择 |
| --- | --- | --- | --- |
| Python 核心 + 原生 Swift GUI | 复用已验证逻辑与测试，最快形成 GUI；核心与视图可以独立运行 | 携带 Python 运行时；维护 Python 与 Swift 的构建和通信接口 | 已选为本机实施基线，先复用开发机 Python，冻结打包后续验证。 |
| Swift 核心 + Swift GUI | macOS 工具链统一，取消 Python 运行时；方便直接调用系统 API | 迁移状态、用量、恢复等逻辑；tokenizer 要另选实现或接入原生库；其他平台仍需适配 | 可行备选，本轮不采用。 |
| Rust 核心 + Swift GUI | 取消 Python 运行时，核心便于跨平台复用；可沿用 Rust tokenizer 路线 | 迁移与回归成本；若走跨语言库需要接口设计，也可保持独立进程通信 | 后续需要重写时优先评估的方向，本轮不迁移。 |

`tiktoken` 提供 Python 接口，其分词核心使用 Rust；0.14.0 的官方构建配置将 Python 绑定列为可选 feature。因此迁移 Rust 在技术上成立，但本项目的 Python 状态机不会自动转换过去，词表和模型映射也仍需维护。[官方构建配置](https://github.com/openai/tiktoken/blob/main/Cargo.toml)

讨论时产品 Python 源码约 4,100 行。需要迁移的难点主要是既有语义：重复 / 迟到事件、回合归属、父子镜像、断连恢复和可信度降级。没有新的性能测量证明 Python 聚合是瓶颈；分词核心本已使用 Rust，不能把重写后的整体提速视为确定收益。

### 4.2 分层、进程与语言分别决定

UI 与逻辑分层不要求使用不同语言；统一语言也不要求合并进程。无论核心未来用 Python、Swift 还是 Rust，都要保持：

- GUI 负责显示和用户操作，核心负责事件、状态与指标；界面不自行重建一套计数算法。
- bridge 生命周期独立于 GUI。GUI 退出或崩溃不能直接结束承载中的 stdio 转发。
- 用户退出 GUI 时停止采集，保留配置和必要桥接；不增加删除监听或卸载恢复用的常驻 Helper。
- 采集器 / 显示故障降级为观测缺口，不制造连续性或可靠速度。
- 平台接入、启动项、目录和 IPC 与可复用的状态 / 指标逻辑区分，便于未来移植。

现有实现仍包含 Unix socket、文件锁、进程组与信号等平台相关行为；保留 Python 不等于当前实现已支持 Windows / Linux。

## 5. GUI 技术栈对比

| 方案 | macOS 体验与 Liquid Glass | 跨平台复用 | 本轮判断 |
| --- | --- | --- | --- |
| SwiftUI + AppKit | 系统视图、菜单栏与窗口控制直接；可使用系统 Liquid Glass | 可复用后端和协议，SwiftUI/AppKit 界面不能直接搬到 Windows / Linux | 已选为本机实施基线；以 NSStatusItem / NSPopover 承载 SwiftUI 起步并验证。 |
| Tauri + Web UI | 可通过原生集成使用系统玻璃容器；网页控件不会自动成为原生控件 | 主要界面可复用，但仍有 WebView、窗口和系统集成差异 | 有竞争力的备选；不因 Liquid Glass 排除它，本轮不优先引入 Python / Rust / Web 多套栈。 |
| PySide6 / Qt | 可延续 Python，具备托盘和桌面界面；系统 Liquid Glass 需额外原生集成 | GUI 与核心较多复用，仍需处理各平台托盘行为 | 可行备选，未做原生效果或发行体积的实测比较。 |

Electron 在讨论中作为通用跨平台路线提及，未开展同等深度评估；不据此给出确定的包体、内存或性能比较。

Apple 的 `glassEffect` 与 `NSGlassEffectView` 从 macOS 26 开始支持。Tauri 团队维护的 `window-vibrancy` 也有 `apply_liquid_glass` 和 WebView 集成示例；系统玻璃并非 SwiftUI 独占。采用其他框架时，系统材质、控件交互和无障碍仍需分别验证。

macOS 菜单栏之外，各平台的“图标旁常驻数字”并非统一能力。例如 Tauri 的托盘标题在 Windows 不支持，在 Linux 取决于面板实现。跨平台接入与生命周期也不会被 GUI 框架自动解决。

## 6. 实施基线与待验证细节

- 采用 `NSStatusItem` / `NSPopover` 承载 SwiftUI 起步，具体交互需结合本机系统验证；遇到平台问题时局部调整窗口承载，不改核心数据与配置语义。
- Liquid Glass 的使用范围与具体布局；最低系统版本已定为 macOS 26，不将更早系统兼容列为首版要求。
- GUI 读取扩展后的既有原子 JSON 快照，管理动作通过一次性 Python CLI 调用；不新增 HTTP 服务或 FFI。本机先复用现有 Python 环境。
- App、核心、外部启动脚本与恢复备份的目录布局，以及退出停止采集与登录启动管理方式。
- 是否随未来 GUI 发行终端 CLI、是否提供 Intel 架构版本；签名与公证在后续发行阶段确定。

当前先验证本机 GUI 与核心联通、配置 / 恢复和误删回退。进入自包含发行阶段后，再取得真实包体、冷启动和离线运行结果，决定是否需要更换打包方式或迁移语言。

## 7. 参考资料

- [Apple MenuBarExtra](https://developer.apple.com/documentation/swiftui/menubarextra)
- [Apple NSGlassEffectView](https://developer.apple.com/documentation/appkit/nsglasseffectview)
- [Apple SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)：登录项 / helper 管理候选，不自动撤销本产品写入的任意启动环境。
- [Tauri window-vibrancy](https://github.com/tauri-apps/window-vibrancy)
- [Tauri 托盘标题的平台限制](https://v2.tauri.app/reference/javascript/api/namespacetray/#settitle)
- [Qt 系统托盘](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QSystemTrayIcon.html)

外部资料对应此前方案比较时的核对结果；本机原生实现与验证另见 [实施结果](implementation-results.md)；后续发行仍应核对所选版本与 API。

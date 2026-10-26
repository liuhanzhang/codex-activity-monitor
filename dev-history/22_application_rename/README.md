# 应用与仓库全面改名

日期：2026-10-26。盘点基线：`83a3ee7`。状态：本地与远程改名、新安装、隔离验收、重启后的自然回合验证及旧数据清理均已完成，见[实施结果](implementation-results.md)。

本轮主任务是将产品统一命名为 **Codex Activity Monitor**。改名覆盖应用、仓库、内部包、命令入口、构建产物和本机安装；清空旧监控数据是配套步骤，不作为独立的数据整理项目。详细顺序与验收见[实施步骤](implementation-plan.md)。

## 1. 已确认的范围

用户已确认：

- 产品使用 Codex Activity Monitor，旧名称可以全面修改，包含仓库名称。
- 清空监控记录、实验运行数据、缓存和旧构建；保留开发文档、实验源码、测试和 Git 提交历史。
- 本次一并重置日常安装，接受暂停监控，并在提示后手动重启 Codex。

以下目标拼写是执行本轮改名时采用的统一规则。大小写按用途区分，避免混用连字符、下划线和产品显示名。

## 2. 名称对应表

| 用途 | 基线名称 | 目标名称 / 处理 |
| --- | --- | --- |
| 产品显示名、README 标题、终端标题 | Codex Progress Monitor / Codex Monitor / Codex Progress | Codex Activity Monitor |
| GitHub 仓库 | `LiuHanzhang/codex_progress_monitor` | `LiuHanzhang/codex-activity-monitor` |
| 本地主仓库目录 | `/Users/bytedance/personal/codex_progress_monitor` | `/Users/bytedance/personal/codex-activity-monitor` |
| Python 发行包名 | `codex-progress-monitor` | `codex-activity-monitor` |
| Python 模块、源码目录 | `codex_progress_monitor` / `src/codex_progress_monitor/` | `codex_activity_monitor` / `src/codex_activity_monitor/` |
| CLI、仓库脚本入口 | `codex-progress` / `bin/codex-progress` | `codex-activity-monitor` / `bin/codex-activity-monitor` |
| Swift Package 名 | `CodexProgressMonitor` | `CodexActivityMonitor` |
| 日常 App | `Codex Monitor.app` | `Codex Activity Monitor.app` |
| Preview App | `Codex Monitor Preview.app` | `Codex Activity Monitor Preview.app` |
| ZIP 发行文件 | `Codex-Monitor-<version>-arm64-dev.zip` | `Codex-Activity-Monitor-<version>-arm64-dev.zip` |
| 用户数据目录 | `~/Library/Application Support/CodexProgressMonitor` | `~/Library/Application Support/CodexActivityMonitor` |
| 运行目录覆盖变量 | `CODEX_PROGRESS_RUNTIME` | `CODEX_ACTIVITY_MONITOR_RUNTIME` |
| 原版 CLI 覆盖变量 | `CODEX_PROGRESS_REAL_CLI` | `CODEX_ACTIVITY_MONITOR_REAL_CLI` |
| 内部构建 / benchmark 前缀 | `CPM_*`、`cpm-` | `CAM_*`、`cam-`，同步更新读写两端 |
| 数据归属标记内容 | `codex-progress-monitor:1` | `codex-activity-monitor:1` |
| 合成测试 profile 类型 | `codex-monitor-disposable-test` | `codex-activity-monitor-disposable-test` |
| 可选 CLI LaunchAgent 标签 | `com.local.codex-progress-monitor` | `com.liuhanzhang.codexactivitymonitor.collector`；先清理旧注册 |

主 App 的 Bundle ID 已是 `com.liuhanzhang.codexactivitymonitor`，本轮沿用。后端 `.backend` 和 Preview `.preview` 后缀沿用。GUI 登录启动继续使用现有 `SMAppService` 机制，改 LaunchAgent 标签不表示为 GUI 增加另一套后台服务。

`MonitorApp`、`MonitorModel`、`MonitorBackend`、`monitor-backend` 等是组件或可执行文件的通用名称，没有使用旧产品全称，本轮保留。Codex 自身的 `CODEX_CLI_PATH` 等接入变量是上游接口，不改为本项目品牌名称。

## 3. 改动面与源码入口

以下路径按改名前基线列出，模块目录移动后以新路径为准。

| 改动面 | 需要同步的内容 | 主要入口 |
| --- | --- | --- |
| 应用品牌与本地化 | 窗口标题、错误提示、菜单中的产品名、无障碍标签、终端标题、中英文资源 | `macos/Sources/MonitorApp/Main.swift`、App / Model 本地化资源、`src/codex_progress_monitor/messages_*.py` |
| Python 包与 CLI | 包目录、发行元数据、入口脚本、import、`python -m`、动态 patch 目标、源码存在性校验 | `pyproject.toml`、`bin/`、`src/`、`tests/`、`experiments/`、Swift 后端调用 |
| 原生构建与打包 | Swift Package 名、App / Preview 名、plist 显示名、ZIP 名、后端隐藏导入、依赖锁与本项目许可证排除规则 | `macos/Package.swift`、`bin/build-gui`、`bin/run-gui`、`packaging/` |
| 本机运行与管理 | 默认数据目录、环境变量、归属标记、安装记录中的路径、旧服务清理、测试 profile 类型 | `runtime.py`、`app_control.py`、`integration.py`、`bridge.py`、`test_profile.py`、`BackendConfiguration.swift` |
| 测试及生成资源 | 测试 import、期望文案、路径、子进程调用、资源 bundle、合成样例 | `tests/`、`macos/Tests/`、`bin/export-localizations` |
| 当前文档与仓库入口 | 中英文 README、贡献说明、打包说明、CI 中存在的引用、Git remote、开发脚本 | `README*`、`CONTRIBUTING.md`、`packaging/README.md`、`.github/` |
| 历史开发资料 | 修复指向当前源码的导航链接；保留当时名称、命令、证据路径、基线和验收结论 | `dev-history/`、`analysis/`、实验说明 |

共享词库从 Python 源词库重新导出，不能只手改 `CoreMessages.json` 或测试示例副本。实验源码继续保留，并更新其可执行代码中的 import 和当前启动入口。

历史报告中的原始路径或名称是证据的一部分，不做全仓库机械替换，也不将旧阶段的计划、未完成验收改写为已完成。旧名称在历史档案和明确标注的一次性旧安装清理逻辑中可以保留。

## 4. 旧数据清理边界

本轮采用新安装、空监控历史的方式，不迁移旧监控数据库和快照，也不以保留旧 CLI / 包名别名作为默认兼容方案。旧安装的恢复与注销仍须完成，不能用删除目录代替。

| 数据类别 | 处理 | 影响 / 前提 |
| --- | --- | --- |
| Monitor 时间线元数据、恢复快照、日志、旧运行记录 | 清空 | 曾观测到的部分时间边界、观测信息和快照计量可能永久丢失；不能承诺从 Codex 历史完整重建 |
| 实验生成的运行目录、隔离测试 profile、截图、旧包、构建缓存 | 清理已核实的生成物 | 保留实验源码、跟踪在 Git 中的资料和产品素材；先排除仍被进程使用的路径 |
| `.local/venv`、打包 Python、editable install、Swift 构建目录 | 旧依赖退出后删除并按需重建 | 旧虚拟环境和安装脚本可能含绝对路径，目录移动后不能直接复用 |
| `integration.json` 等原环境备份、旧 launcher、服务 / 登录注册 | 恢复和注销成功后清理 | 在旧 Codex / bridge 仍引用旧入口时，暂留所需最小文件，不能先删恢复依据 |
| 开发文档、实验源码、测试、合成 fixture、图标与许可证 | 保留 | 只更新与当前改名相关的有效引用 |
| `.git`、分支与提交历史 | 保留 | 不重建仓库、不改写历史；盘点时 `main` 有 7 个未推送提交，不随改名自动推送 |
| Codex 自身的聊天、会话 JSONL、账号、设置与数据库 | 保留 | 不属于 Monitor 的可清理数据；实验目录中明确自建的隔离 profile 单独按所有权处理 |

2026-10-26 只读盘点：旧数据目录约 67 MB，主要为 `timeline/metadata.sqlite3`；`.local/` 约 2.4 GB；`experiments/` 约 849 MB，其中 Git 跟踪的实验源码约 0.23 MB。数字是改名前快照，不是全部可立即释放的空间，也不是清理结果。

盘点时旧采集器仍持有运行锁，安装记录为 `configured`；本次工具读取的四项 GUI-session 接入变量均为空，与记录中的安装值不同。实施时必须重新核对真实运行环境和恢复状态，不能只按记录中的状态字段决定删除。

## 5. 本轮交付与边界

完成标准是：当前产品及开发入口统一使用新名称，GitHub 和本地目录对应新仓库名，新安装使用新路径，旧监控数据按上述范围清理，相关测试、构建与切换检查有实际结果记录。

此次改名不改变采集协议、token 速度口径、活动状态语义或第 21 章已经确认的活动文案；不顺带实现其他工程重构、代码功能或公开发行准备。构建成功不等于签名、公证、干净机器和长期运行验收通过。

本页的基线盘点与决策保留作范围依据。实际进度以[实施结果](implementation-results.md)为准；未实际完成的切换不能提前标为完成。

# 全面改名实施结果

日期：2026-10-26。基线：`83a3ee7`。状态：本轮改名、安装切换与清理已完成。用户删除最后的旧目录占位后，已只读确认旧目录和旧数据目录均不存在；新采集器健康，重启后的真实回合已完成，时间线没有未结束阶段。

范围与目标拼写见[改动说明](README.md)，实施顺序见[实施计划](implementation-plan.md)。本章随改名变更一起提交本地 Git，不推送，也不改写此前已有的提交。

## 最终结果

| 阶段 | 已完成的结果 |
| --- | --- |
| P0 | 固定基线、主工作区和远程地址，核对旧安装，并建立明确的生成物清理清单 |
| P1 | 停止旧监控、恢复原接入环境，待旧 bridge 退出后清理旧安装；保留稳定 Bundle ID 对应的新版登录注册 |
| P2 | 产品全名、Python 发行包 / 模块、CLI、Swift Package、App / Preview / ZIP、数据目录、自有环境变量、标记、测试 profile、构建前缀和服务标签已统一 |
| P3 | Python、Swift、本地化、原生界面和最终独立包隔离验收通过 |
| P4 | 主仓库与私有 GitHub 仓库均更名，`origin` 已更新并验证；在最终路径重建环境和全部 App；Git 历史保留 |
| P5 | 用户重启 Codex 后，新 bridge 的源码指纹与当前源码一致；实际 GUI 显示真实活动、阶段及计数；同一自然回合最终为 completed |
| P6 | 旧监控数据、旧代码 / 环境 / App、生成物和临时备份已清理；最后两个说明文件及目录由用户删除，并核验不存在 |

产品统一为 **Codex Activity Monitor**。Python 发行包和 CLI 为 `codex-activity-monitor`，模块为 `codex_activity_monitor`，Swift Package 为 `CodexActivityMonitor`。数据目录为 `~/Library/Application Support/CodexActivityMonitor`；不迁移旧监控数据库或快照，不保留旧 CLI / 包名别名。主 App、后端和 Preview 沿用既定 Bundle ID。

全新安装暴露了原有的默认 runtime 不一致：源码 CLI 默认读项目 `.local/runtime`，GUI 全新安装使用数据根目录的 `run`。本轮统一到新数据目录的 `run`，Preview 保持隔离 runtime，并增加了实际配置新 GUI 后比较终端默认 runtime 的回归场景。采集协议、速度口径和活动生命周期语义没有变更。

开发文档、实验源码、测试、图标、许可证和 Git 历史保留。档案仅修复指向当前源码和仓库的导航链接；原始命令、名称、证据路径和阶段结论不改写。旧聊天仍可展示原始 cwd 中的项目名，这属于来源元数据，并非当前应用品牌或安装路径；未修改用户 Codex 会话来替换它。

## 验证

| 检查 | 结果与边界 |
| --- | --- |
| Python 回归 | 最终目录下 487 项通过，包括新增的 GUI / CLI runtime 场景；最后仅统一产品短称，再通过 6 项本地化定向测试 |
| Swift 回归 | 最后资源修改后 119 项通过；现有 opt-in benchmark / window 测试仍按原条件启用 |
| 本地化 | `bin/export-localizations --check` 通过，共享词库、UI 资源及生成示例一致 |
| GUI / Preview 构建 | 最终路径构建及签名检查通过；plist 名称 / ID 和后端路径已核对；实际 Preview 标题及日常退出菜单使用完整新名称 |
| 独立 App / ZIP | 使用固定打包 Python 和依赖重建，架构、签名、构建清单与最终 ZIP 哈希已验证 |
| 独立后端隔离验收 | 源码 / Python / 用户历史及网络拒绝负对照、离线 tokenizer、worker、collector、合成回合、时间线、二进制 stdio、外部回退和原环境恢复通过 |
| 独立包原生 GUI | 源码目录外解压，以合成 profile 启动；拒绝源码、Homebrew、用户历史、外部缓存及网络后，GUI 仍 configured 且 transport_fresh；build ID 与最终 ZIP 相同 |
| 日常真实接入 | 新 bridge 指纹匹配，GUI 显示真实模型 / 工具阶段和计数；CLI 默认读取同一健康 collector |
| 自然回合终态 | turn `01a1786e-afce-79b3-9228-f55940e2b4a4` 为 completed；188 个阶段中 179 completed、8 failed、1 declined，无未结束阶段。失败和拒绝保留实际工具调用状态，不改写为全部成功 |
| 最终清理 | 用户确认删除占位后，已只读验证旧仓库目录及旧数据根目录不存在，新采集器及新源码身份仍正常 |
| 提交前检查 | 当前文档链接、JSON、生成资源和 Git 空白检查；无需因文档收尾重复运行已通过的完整构建和回归 |

Python 故障隔离测试会主动触发 tokenizer 原生 panic；日志中的 `StackOverflow` 来自受控场景，套件成功结束。自然回合中重载监控、工具失败及审批拒绝造成的质量标记按来源保留，不据此宣称完整观测、长期稳定性、Remote、Developer ID 签名、公证或干净机器验收完成。

机器可读证据：

- [验证汇总](evidence/verification.json)
- [运行与品牌检查](evidence/runtime-and-branding.json)
- [最终 ZIP 隔离验收](evidence/isolated-acceptance.json)
- [独立包原生 GUI 隔离验收](evidence/bundled-gui-isolated.json)
- [仓库更名](evidence/repository-rename.json)
- [旧安装清理](evidence/legacy-cleanup.json)
- [重启后的实时快照](evidence/post-restart.json)
- [同一自然回合的最终状态](evidence/post-restart-natural-turn.json)

实时快照是当时的观测，最终状态由后续证据补齐；没有将观察器超时误写成完成。证据只保留状态、时间、计数和源码身份，不包含聊天正文、控制 token 或原环境备份。

## 清理与交付

首批清单删除 186 个生成物路径，按删除前分配块统计合计 **3,260,039,168 字节**。另删除旧时间线数据库文件 **69,820,416 字节**、旧快照 / 日志及迁移后淘汰的环境和构建缓存；这些不计入首批数字，也不把清单统计冒充精确的磁盘剩余空间增量。

旧源码、venv、App、runtime、数据根目录和 `/private/tmp/cam-rename-20261026` 原环境备份均已清理。活动 cwd 保护曾阻止自动删除最后的占位目录；用户随后手动删除，最终只读检查确认不存在。Codex 自己的聊天、账号、设置及数据库未删除。

最终入口：

- 主仓库：`/Users/bytedance/personal/codex-activity-monitor`
- 私有远程：`https://github.com/liuhanzhang/codex-activity-monitor`
- `origin`：`git@github.com:LiuHanzhang/codex-activity-monitor.git`
- 日常 App：`.local/gui/Codex Activity Monitor.app`
- Preview：`.local/gui-preview/Codex Activity Monitor Preview.app`
- 独立 App：`.local/standalone/Codex Activity Monitor.app`
- ZIP：`.local/standalone/Codex-Activity-Monitor-0.1.0-arm64-dev.zip`
- 默认 runtime：`~/Library/Application Support/CodexActivityMonitor/run`

远程仍是此前已推送的代码版本；本轮只更新仓库名称和本地提交，不推送原有 7 个未推送提交或本次提交。后续正常版本管理时再决定推送。

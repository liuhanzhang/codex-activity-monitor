# Sparkle 接入实现与验证结果

日期：2026-10-28。对应 [实施前方案](sparkle-plan.md)。本地接入和验证已完成；远程 Pages 尚未上线，源码未提交／推送，也未发布新版 App。随后按用户请求完成日常 App 原位更新，见末节。

## 最终行为

- Sparkle 2.10.1 固定依赖与锁文件。`SparkleUpdateService` 管理 SDK controller、KVO 和委托；`UpdateStore` 仅适配 UI、一次性迁移旧自动检查选择和生命周期。不再有 GitHub Releases API 客户端、自写 SemVer 或每日 Task 轮询。
- 默认每天自动检查。后台新版使用主面板／关于页和齿轮提示点；「查看更新」唤起 Sparkle 标准窗口，用户选择网页按钮后进入对应发行页，当前不执行更新包下载或安装。窗口会话结束时清理提示并恢复后续调度。
- 手动检查不受自动开关限制；网络错误不触发后台弹窗。系统不兼容单独提示，不误报为「已是最新」。检查时间是尝试时间，不标为成功时间。
- 版本比较使用递增构建号；关于页和 Sparkle 标准窗口保留完整预发布版本。标准 Sparkle 窗口使用系统语言，Monitor 页面继续使用应用内语言设置。
- Preview／只读／隔离 profile 默认不创建 SDK updater，不读写日常偏好。专门验收开关仅在独立 `updatetest` 身份、测试分发类型、只读模式和 `127.0.0.1` HTTP 更新源同时满足时有效。
- 更新偏好域为 `{bundleID}.updates`，旧 `updates.json` 的自动选择只迁移一次。停止服务释放 controller，由 SDK 取消定时器及当前请求，不把自动检查偏好改成 false。卸载通过既有 login-remove 辅助入口清理专属更新偏好；单元测试确认不会清理其他域。
- 信息清单不含 enclosure；客户端进一步拒绝安装类型条目和非本项目 Release 链接。默认关闭可选系统统计和自动下载／安装。

## 构建与发布准备

- 本地与独立构建都显式嵌入 Framework，保留 symlink，裁剪 arm64，按内到外顺序签名并收录许可。
- 实际检查发现 SwiftPM 为新 Framework 加入开发目录绝对 rpath。已改成先嵌入 Framework，再验证依赖齐全、删除工具链／SwiftPM 开发路径并签名；不会靠移除搜索路径掩盖缺失库。
- `website/updates/releases.json` 当前只记录已确认发布的 alpha.2／构建号 2。预发布清单包含它，稳定清单为空；没有虚构稳定版或测试版。生成脚本和 deploy workflow 会校验真实 Release 与对应已上传 ZIP。
- `.github/workflows/pages.yml` 仅部署 `website/`，Actions 固定 SHA，配置最小 Pages 权限。更新源 URL、公钥、每日检查和偏好域由共用 metadata helper 写入两个构建路径。
- 官方 `generate_keys` 使用专用 account `com.liuhanzhang.codexactivitymonitor` 生成密钥，私钥留在本机钥匙串；仓库仅包含公钥。本轮没有导出私钥。当前清单通过 HTTPS 传输，未启用签名清单；自动安装和更新包签名仍属于后续工作。

## 验证证据

1. Python `test_app*.py` 34 项通过，覆盖本次 metadata／appcast、既有 App 归属、恢复与卸载等相关场景；显式 `PYTHONPATH=src` 避免本机旧 editable 部署干扰。
2. 默认 Swift 两个测试运行报告分别为 68、144 项，套件通过（包含各自 opt-in 跳过项）。最后的完整预发布显示和偏好卸载改动后，更新相关定向运行 12 项通过；未因纯显示改动重复全部后端回归。
3. 实际本地 Sparkle 自动检查：新版、当前已最新、系统过旧、坏 XML、自动关闭五种场景全部通过。对应记录在 `.local/sparkle-acceptance-20261028-r2/result.json`。每个需要检查的场景仅请求一次 loopback appcast；关闭场景在受控启动观察窗口内没有请求。
4. 每个原生场景都复制 App 到临时中文路径、使用独立 ID／偏好、禁止读取 SwiftPM 构建目录，证明运行时使用随包 Framework。合成版本 9999 没有写入真实更新清单，也不访问真实 Codex。
5. CUA 实际检查关于页新版提示及「查看更新」→ Sparkle 标准 Software Update 窗口，确认提供 Learn More／Remind Me Later／Skip。点击 Learn More 后窗口关闭，应用内提示清除，手动检查恢复可用。浏览器最终页面内容未另行复核。
6. 预览 Mach-O 检查为 6 个 arm64 二进制，最高最低系统声明 14.0；签名深度验证通过。独立包有 12 个 Mach-O，架构／最低系统／动态库路径检查通过。
7. 独立包的现有隔离验收通过：禁止源码、Homebrew、构建工具链和 IP 网络读取／访问；完成离线词表、后台进程、合成 bridge、时间线与平均速度、恢复与缺失 App 回退，以及 native GUI＋bundled backend。实际记录在 `.local/standalone-sparkle/acceptance-result.json`。该完整后端验收对应 `b99efb…` 构建；最后仅版本显示和偏好清理接口改动后的最终包为 `d93899…`，已重建并通过构建内静态检查，复用了之前的后端验收，没有把旧记录冒充最终包的完整运行结果。这是受控合成验收，不代表真实 Codex 长期或 macOS 14 实机验证。
8. 生成清单与公开 Release 的认证只读校验、catalog 一致性、Python 编译及 whitespace 检查通过。App 本身不持有维护者 GitHub 认证。

## 验证中修正的问题与限制

- 初次原生验收已发现新版，但测试清理错误地调用登录项辅助入口；临时未注册 App 的登录服务返回错误。改为仅删除该次唯一生成的测试偏好域后，五个场景完整通过；不把第一次运行记作完整成功。
- 原生 UI 工具首次连接耗时约 9 分钟；后续交互恢复正常。没有用工具等待时长推导产品更新检测延迟。
- 原生交互脚本使用终止信号回收临时进程，与正常用户退出是不同证据；交互运行 Ctrl-C 退出后执行了清理。
- Sparkle 标准窗口最初只显示当前 `0.1.0`。通过官方版本显示协议补齐 `alpha.2`，机器构建号比较保持不变；最终原生窗口已实际显示 `you have 0.1.0-alpha.2`，点击 Remind Me Later 后提示清除、检查恢复可用。
- Apple 的签名／公证、实际 macOS 14、长期驻留、真实新版发布后的自然提醒、自动安装均未在本轮验证。

## 远程上线尚需完成

本轮只读核对：仓库公开、当前授权有 admin 权限，但 `has_pages=false`，Pages 查询为 404。部署文件、真实发行清单及 [发布步骤](../../packaging/updates.md) 均已准备。

更新源实际可用还需要：将这些文件提交并推送至 main，启用 Pages 的 GitHub Actions 构建方式，运行并检查部署，最后复核公开 HTTPS 清单和一次真实 HTTPS 检查。遵循当前未明确要求时不自行提交／推送的约定，暂未执行这些远程上线动作；这不是权限不足或已上线的证据。

## 后续：更新当前日常 App

用户随后明确要求先更新正在运行的 App，本次已执行：

- 确认旧实例为 `.local/gui/Codex Activity Monitor.app`，GUI PID 90051、collector PID 90140。保留 source 开发构建方式、正式 bundle ID、原 Python 与 Application Support 的运行／数据目录，没有切换到 standalone。
- 旧 App 与必要管理记录的私有回退副本位于忽略的 `.local/daily-update-backup/about-sparkle-20261028-214137/`。备份不进入仓库；没有清理或重建用户时间线。
- 新后端源码固定保存在 `.local/deployments/about-sparkle-20261028-214137/`，55 个运行源文件校验一致；词表缓存和虚拟环境沿用已有位置。导入探针确认实际从该快照载入，缓存 tokenizer 可用。
- 使用正式 source 构建入口重建原路径 App，指向新源码快照后重新签名，深度验证通过。版本显示 `0.1.0-alpha.2`、构建号 2；这是本地功能更新，不表示另行发布了同名 GitHub Release。
- 通过原生应用菜单正常退出旧 GUI；确认旧 GUI／collector 都已结束，未强制终止。随后启动新实例。最终只有一个日常 GUI（PID 18872），collector PID 18930，GUI build ID 为 `ac7e277bc9379fc68a0388840c4da4e430e9924d2eb93074a630c08e576dac6a`。
- 只读进程检查确认新 GUI 已加载原路径 App 内的 `Sparkle.framework`。collector snapshot 新鲜，collector、bridge、解析、tokenizer、history、metadata、timeline_query、checkpoint、logging 均为 healthy。
- 接入记录 ID、原环境备份及 legacy 备份的哈希保持一致；中文语言、登录启动、未恢复接入的选择保留；既有 `timeline/metadata.sqlite3` inode 保持不变。
- 新主面板原生 AX 检查显示已连接。由于这一轮跨越 collector 重启，当前轮保留 observation gap／速度不可确认，没有回填中断轮或把它当成完整实时证据。没有重启 Codex，也未验证下一自然轮次或长期稳定性。
- 关于页本身已在前述 Preview 与临时原生 App 检查过；本次尝试重复打开日常设置时，CUA 返回失效菜单／窗口索引和截图不可用，因此没有把这次重复 UI 检查记为通过。运行路径、实际加载的 Framework、版本元数据和健康状态另有独立证据。

日常实例已更新不代表公网更新源上线。Pages、源码提交／推送和新版 App Release 仍未执行。

# alpha.5 DMG 发布结果

日期：2026-10-30，Asia/Shanghai。用户授权将开发分支快进到 main、推送、发布新版本并删除旧开发分支。

## 发行与产物

- [v0.1.0-alpha.5](https://github.com/liuhanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.5) 已公开，构建 `5`，GitHub `prerelease=true`；实际发布时间 `2026-10-30T06:51:24Z`。
- 标签精确指向 `0cf1f7ba325e51988c8e8a302681259cbc11c0ef`。main 已保留两边历史并正常推送，没有 force push；`codex/monitor-updates-and-rollout-history` 已删除，远端原本没有该分支。
- 安装附件只有 `Codex-Activity-Monitor-0.1.0-alpha.5-macos-arm64.dmg` 和 `SHA256SUMS.txt`；不发布 App ZIP。GitHub 自动生成的源码归档与安装包分开。
- DMG 为 27,955,070 字节，SHA-256 `bc78dd3225039024524beae22012053dde4bb1ab7081e4be0acfabeed0acdae0`。
- 包 build ID `b5daea24c782ffcd63963fa676852a0da759c3fb9fb41e55e2ca26ee45f1b228`；57 个运行模块与全部打包输入摘要对应最终标签，包含正确的 alpha.5／5 元数据。
- 从公开 Release 无鉴权重新下载 DMG 与校验文件，逐字节核对与已验收本地产物一致。双语安装／升级说明已随 Release 发布，旧 Release 附件保留原样。

本次合并保留系统默认语言、DMG、升级诊断和 rollout 兼容修复，也保留 alpha.4 的更新入口、持久提示及 Sparkle Download 文案。第 27 章早期记录保留当时的提交散列；作者身份改写后，原基线 `61f751d` 对应 `fa97c65`，文件内容未变。

## 验证

| 检查 | 结果 |
| --- | --- |
| [发行源码 CI](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/41016087801) | 580 项 Python 测试、Swift 默认报告 223 项（包含默认跳过的 opt-in）、本地化、清单、Preview 构建及工作树检查通过 |
| 最终 DMG | 深度严格签名、只读映像、Finder 引导布局、Applications 快捷方式、复制后签名、12 个 arm64 Mach-O 的最低系统／依赖检查通过 |
| 隔离后端与临时 GUI | 拒绝源码、外部 Python／词表缓存、构建工具链与 IP 网络后，词表／worker、collector、bridge、历史切换、均速、恢复接入及临时原生 GUI 连接检查通过 |
| 原生历史客户端 | 最终独立后端与真实 `SystemTimelineClient`、`TimelineStore`、`ActivityViewerStore` 的保留前缀、当前后缀、丢弃内容、均速及 fork 联调通过 |
| Sparkle 合成源 | 独立临时 App 自动发现合成版本，重启后保留提示；未将合成版本写入生产清单 |
| [Pages 部署](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/41016702416) | 清单提交 `a6f3d8c` 部署成功；稳定、预发布与旧兼容三个无鉴权 HTTPS 清单逐字节匹配本地生成结果 |
| 公开 alpha.4 原始客户端 | 先校验原 ZIP 摘要，再用独立副本保持构建 4，通过真实 HTTPS 自动发现 alpha.5；重启后恢复提示 |
| 最终 alpha.5 客户端 | 独立副本保持构建 5，通过真实 HTTPS 返回已是最新，无自更新误报 |

CI 首次暴露诊断／生命周期测试隐含依赖中文系统语言，已显式固定相应测试夹具的首选语言。后续 CI 暴露旧运行时测试在等待后再次探测的竞态，已使用实际观察到的快照，并同时等待重连、终态及计数。修正只涉及测试；最终安装包的运行和打包输入没有改变。修正后的最终标签通过上述完整 CI。

## 边界

详细继承形态及暂缓项仍见[实施结果](implementation-results.md)。临时 GUI 验收验证启动和连接，不等于所有界面的视觉验收；发布未增加压缩历史、完整特殊子智能体继承或自动安装能力。

本次发布没有替换本机日常 App、重启 Codex 或把真实会话作为验收夹具。所有 SDK 副本均使用独立 bundle ID／偏好域并已清理。`requests=[]` 仅表示脚本的 loopback 服务未被访问，不表示没有生产 HTTPS 请求。macOS 14 实机、首次下载的 Gatekeeper 流程、Developer ID／公证及长期自然使用仍不在这些检查的通过范围。

完整私有日志与构建产物保留在忽略的 `.local/release-alpha5/`；仓库只保留本页可公开的身份、摘要与检查结论。

# 发布分类解耦与公网更新验收结果

日期：2026-10-28。接续 [实施步骤](channel-release-plan.md)；此前本地接入与日常 App 更新记录保留在 [Sparkle 接入结果](sparkle-results.md)。本记录中的公网证据与此前 loopback 验收分开。

## 实现与发行身份

- 功能提交：`685286528cac5ce35a0739327885b75bcd2d8b9e`，已推送 main。包含关于页面、Sparkle 2.10.1、每日自动检查、手动检查及网页下载、独立 Preview、许可和打包修正。
- `version-suffix` 只控制显示名称。`release-prerelease` 明确决定新构建默认频道；已发布清单分类取自 GitHub `prerelease`，不会从 `alpha`／`preview`／连字符推断。
- 相反组合测试通过：带 alpha 后缀但 GitHub 正式发布，只入稳定及预发布清单；纯数字版本但 GitHub Pre-release，只入预发布清单。Preview 使用独立分发身份禁用检查，与版本名称无关。
- 本次安装包：`0.1.0-alpha.3`，`CFBundleVersion=3`，`CAMReleasePrerelease=true`，`CAMUpdateChannel=prerelease`，默认 `appcast-prerelease.xml`。本次明确选择预发布，名称不强制该分类。
- 构建 ID：`c36f8ddf1e9a9bbb1b7de72b550c4241ce4619ea106528c43e03b3b99b643940`。
- ZIP：25,227,830 字节；SHA-256：`0082c38162f0dec9c652defc0df64d228e4fb29266795f62d4a0cb62dcf30824`。

## 本地与包验证

- Python 完整运行 538 项通过。Swift 默认两个运行分别报告 68、146 项通过，包含 11 项默认跳过的 opt-in；更新相关定向 12 项通过。Python 日志包含既有 tokenizer 原生 panic 隔离测试的 StackOverflow 输出（`test_real_counts_reuse_one_child_and_native_panic_is_contained`），整套结果为 OK；不把该输出当成新运行崩溃或隐去它。
- 本地化、生成清单、真实 GitHub Release 元数据校验、whitespace 检查通过。
- 对本次最终 ZIP 执行现有隔离包验收，不使用 `--gui`：拒绝源码／Homebrew／开发工具链／IP 网络访问，离线全部词表和 worker、collector、bridge、时间线、平均速度、恢复与缺失 App 回退均通过。签名深度验证通过；12 个随包 Mach-O 均为 arm64，最低系统目标不超过 14.0，未依赖开发目录。
- 记录：忽略目录 `.local/channel-release/package-acceptance.json`、`python-full.log`、`swift-full.log`、`swift-targeted.log` 和 `.local/releases/0.1.0-alpha.3/build-result.json`。不上传用户数据、构建缓存或密钥。

## 首次 Pages 上线

- Pages 已配置为 Actions workflow，只部署 `website/`。
- [首次部署](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/40705914984) 成功。
- 首次无鉴权 HTTPS 读取三个地址均为 200，并逐字节匹配本地清单。当时预发布清单只含已发布 alpha.2／构建 2，稳定清单为空。
- `appcast-alpha.xml` 与新 `appcast-prerelease.xml` 保持内容相同，用于兼容已更新的本机旧客户端。
- 上线前置 SDK 探针：隔离副本设置构建 1，从旧兼容地址自动发现真实 alpha.2；没有调用手动入口。它是新二进制的版本配置探针，不冒充原始公开 alpha.1 客户端。

## Release、最终部署与真实 HTTPS 检查

- [v0.1.0-alpha.3](https://github.com/liuhanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.3) 已公开，GitHub `prerelease=true`，实际发布时间 `2026-10-28T14:30:17Z`。tag 的实际 commit 对象与功能提交 `6852865` 一致。
- ZIP 和 SHA256SUMS 从公开 Release 重新下载后匹配上面的 SHA-256，重新解压并检查包内构建号 3、发布分类、频道和签名；SDK 最终验收以此下载副本为源。
- 清单提交：`97f612be366c3a90caf16d7a0d4da0d1a36920c3`，已推送 main。[清单部署](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/40706716135) 成功。
- 三个公开 HTTPS 地址均返回 200，逐字节匹配最终清单。预发布及旧兼容副本 SHA-256 均为 `fb07d53dcb2a4df130a50f963973591fc1342c53a7dc296fbf2b09cf3947f937`；稳定清单仍为空，SHA-256 为 `9ae65087ee0c752db65d06b704d5a2d9caf8fc127868a0781cbf5eae660fbb4b`。没有把合成高版本写入公网。
- [功能提交 CI](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/40705914833) 成功，包括 Python、Swift、Preview 构建及生成文件检查。

| 下载包的隔离测试副本 | 清单 | 触发方式与结果 |
| --- | --- | --- |
| 版本元数据改为 alpha.2／构建 2 | 旧兼容 `appcast-alpha.xml` | `automatic=true`，未调用手动入口；实际发现 alpha.3 |
| 版本元数据改为 alpha.2／构建 2 | 新 `appcast-prerelease.xml` | `automatic=false`，通过手动入口发现 alpha.3 |
| 保持 alpha.3／构建 3 | 新 `appcast-prerelease.xml` | 自动检查返回「当前已是最新版本。」 |
| 保持 alpha.3／构建 3，切至稳定源 | `appcast.xml` | 空稳定源返回「当前已是最新版本。」，没有预发布提醒 |

结果记录分别位于忽略目录 `.local/channel-release/public-old-auto/`、`public-old-manual/`、`public-current/`、`public-stable-confirmed/`，每项都有独立偏好域、UI diagnostics 与结果。这里 `requests=[]` 只表示未命中脚本内部 loopback server；公网检查通过配置的唯一 HTTPS 地址及真实 SDK 返回结果确认，未另行抓包统计网络请求次数。

稳定清单第一次探针错误地把空清单预期为「没有适用于此 Mac 的更新」，断言失败。实际 SDK 将空清单归为已最新；核对结果后以正确预期重新运行并通过，没有为迎合测试改产品逻辑。此前本地“有新版但系统不兼容”的场景仍是独立验证。

## 日常实例与验证边界

本轮不替换、不停止用户日常构建 2，不修改其偏好、系统时间或现有数据。发布后只读复核原 GUI／collector PID 仍存在、backend 配置完全相同、随包 Sparkle 仍被加载，snapshot 新鲜。公开 alpha.1／alpha.2 原始 ZIP 不含 Sparkle，无法自行发现新版，首次需手动升级。本机此前更新过的源码构建 2 已含 Sparkle，并继续使用兼容地址。

公网 SDK 验收使用从发行下载的 App 的临时副本、唯一 `updatetest` bundle ID 和独立数据／偏好域，拒绝读取 SwiftPM 构建目录；测试旧构建时只修改临时副本的版本元数据。它证明真实 SDK 对公开清单的自动／手动处理，不等同于旧公开二进制具备新功能。清理使用终止信号，不视为正常 GUI 退出测试。

长期驻留、自然等待一天的再次调度、Apple Developer ID／公证、macOS 14 实机、自动安装不在本轮通过范围。实际自动检查验证使用全新独立偏好，让 SDK 在启动时自然发起到期检查，没有改系统时间或真实 App 的检查日期。当前仅网页手动下载。

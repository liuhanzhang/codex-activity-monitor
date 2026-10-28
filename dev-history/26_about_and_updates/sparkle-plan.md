# Sparkle 接入方案与实施步骤

日期：2026-10-28。本文保留实施前方案；本地实施结果见 [sparkle-results.md](sparkle-results.md)，远程上线边界单独记录。用户已授权按此范围实施，无需再次确认代码改动。

## 目标与已确认范围

保留设置中的关于页，改用 Sparkle 自动检查和处理版本比较。默认每天检查，发现新版本后在应用内提示，点击打开本项目对应 GitHub Release 页面手动下载。当前不下载或安装更新包，不自动退出或重启 Codex，不申请通知权限。

## 设计决定

1. 固定 Sparkle `2.10.1`（本轮核对的最新正式发行）并提交依赖锁文件，保留 macOS 14 目标。App 使用 `SPUStandardUpdaterController`，由 `UpdateStore` 适配现有 SwiftUI 页面和主面板；移除自写的 GitHub API 客户端、SemVer 比较器与每日轮询。
2. 更新源为静态 HTTPS appcast，拟部署到 `https://liuhanzhang.github.io/codex-activity-monitor/updates/`。正式构建使用 `appcast.xml`；预发布构建使用 `appcast-alpha.xml`，后者也包含正式发行。公开清单只记录实际存在、有 macOS 安装包的 Release。
3. appcast 使用递增 `CFBundleVersion` 比较；显示版本保留 `CAMReleaseVersion`。只提供 `<link>`，不提供 `<enclosure>`，使用 Sparkle 官方网页下载模式。现有发布 `alpha.2` 的构建号为 `2`，下一发行必须递增；不会把本地改动伪装成已发行新版。
4. `Info.plist` 默认启用检查、间隔 86400 秒，禁用自动下载／安装和可选系统统计。更新开关和检查时间使用 Sparkle 偏好；旧 `updates.json` 的自动检查选择仅迁移一次。使用独立的、随 bundle ID 区分的更新偏好域，卸载时清理，避免 Preview 或测试污染日常偏好。
5. 菜单栏应用接入 Gentle Reminders：后台发现新版时显示主面板提示及齿轮提示点；点击提示或手动检查使用 Sparkle 标准更新界面，再前往发行页下载。手动错误可见，后台错误不打断观察。
6. Preview、只读模式和隔离 profile 不创建或启动真实 updater。原生验收使用临时测试 bundle、独立偏好域和本地 HTTP 合成 appcast；不向真实更新源发布合成版本。
7. 源码 App 和独立包均嵌入 `Sparkle.framework`，设置可迁移的 rpath，保留符号链接；在本项目 arm64 发行范围内裁剪通用二进制并重新签名。保留第三方 MIT 许可。必须验证离开 SwiftPM 产物目录仍能启动。
8. 本轮准备静态清单、生成／校验脚本和 Pages 部署 workflow。当前 Pages 查询返回 404，尚无已配置站点。远程发布必须以经过验证的清单为对象；不推送本地源码或发布新 App Release。若远程启用／部署还需要额外权限或代码推送，在本地工作全部完成后明确报告所需操作。
9. 更新签名公钥配置与私钥隔离。仅网页下载阶段先核实 Sparkle 的实际启动要求，再决定是否需要生成密钥；不导出、打印或提交私钥。正式自动安装、Developer ID 与公证是后续验收范围。

## 实施步骤

| 阶段 | 改动 | 完成条件 |
| --- | --- | --- |
| P0 | 写方案，核对 SDK、发布与 Pages 状态 | 本文完成，记录外部边界 |
| P1 | SwiftPM 依赖、Framework 嵌入与打包 | 可构建、锁定依赖、签名及链接检查通过 |
| P2 | UpdateStore、关于页、Gentle Reminders、偏好迁移和卸载 | 没有 GitHub API 请求或自写定时器；预览隔离 |
| P3 | appcast 数据、生成校验脚本、Pages workflow 和发布说明 | 无虚构发行；稳定／预发布源分离；生成结果可复现 |
| P4 | 定向测试和原生临时 bundle 验收 | 发现合成新版、当前已最新、错误、手动跳转、关闭自动检查、重复启动、偏好隔离等场景有证据 |
| P5 | 清理旧文案与测试，汇总记录 | 说明已实现、已验证、远程未上线和剩余操作，保留原有 staged 范围，不自行提交或推送 |

## 参考

- [基础接入与安全](https://sparkle-project.org/documentation/)
- [代码初始化](https://sparkle-project.org/documentation/programmatic-setup/)
- [配置项与偏好](https://sparkle-project.org/documentation/customization/)
- [菜单栏提醒](https://sparkle-project.org/documentation/gentle-reminders/)
- [网页下载模式与构建号](https://sparkle-project.org/documentation/publishing/)

## 验收边界

编译、注入服务测试、原生 Preview、可迁移的构建产物启动、真实 HTTPS 更新源可用性、自然长期驻留是不同证据。本轮不以本地合成 appcast 验收替代远程站点上线，也不以构建通过宣称 macOS 14 实机或自动安装验证完成。

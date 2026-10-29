# 常驻绿色更新箭头与关于页定位

后续 alpha.4 已发布并更新日常 App，最终状态见[alpha.4 发布记录](alpha4-release.md)。下文按实施阶段保留各阶段当时的构建／发布状态。

日期：2026-10-29。用户确认使用 SF Symbols `arrow.up.circle`：主面板设置按钮左侧和设置侧栏「关于」行最右侧显示绿色箭头，不显示顶栏更新文案；点击主面板箭头定位「关于 → 软件更新」。Sparkle 标准窗口保持原样。

## 实现

- 两处使用同一个 `UpdateIndicator` 和同一份新版状态。替换原主面板更新文案条及设置齿轮小圆点，保留关于页内的版本／查看更新操作。主面板按钮包含版本 tooltip 和无障碍标签，侧栏行包含新版无障碍值。
- `SettingsNavigation` 由既有设置窗口 controller 持有，统一选中关于页并发出滚动请求；重复点击会再次定位，不新建第二个窗口。关于页继续使用原生 Form，通过 ScrollViewReader 滚动到软件更新 Section。
- 新版记录保存在本 App 的 `.updates` 偏好域。恢复时重新校验项目 Release URL、构建号高于当前 App、更新源完全匹配；损坏记录／更换源／升级后丢弃。Preview 不启动更新服务，也不恢复日常提示。
- SDK 会话结束只结束交互，不再清空新版提示。稍后提醒、关闭窗口或打开网页都不等于已升级；明确 Skip 的 SDK 回调才清除对应版本。下一次成功检查确认没有可用新版时也会清除，网络失败则保留已知新版。
- 缓存恢复不绕开 Sparkle 调度，不修改真实检查时间或系统时间。机器比较和过滤仍由 SDK 负责，缓存只保留其已经确认的提示。
- 顺带修正手动检查的错误显示时序：SDK 先发出 idle KVO、随后回调错误时，不再提前清除手动检查标记。单元测试覆盖该顺序；这不等于确认它是此前日常自动提示缺失的原因。

## 验证

- 定向 Swift 17 项通过；默认 Swift 两个运行分别报告 68、151 项通过（包含既有默认跳过的 opt-in 项）。新增缓存恢复、升级清除、跳过再手动检查、跨源／非法缓存拒绝、错误时序和重复设置定位测试。
- 本地化资源一致性、whitespace、验收脚本编译检查通过。隔离 Preview 构建和随包签名完成。
- 本地真实 Sparkle 自动检查：新版、已最新、不兼容、坏 XML、自动关闭五种场景通过。新版检查不再只捕捉首个回调，额外观察 3 秒确认状态仍保留。
- 独立 App 自动发现合成构建 9999 后，重启同一临时 App／偏好域，恢复提示且没有额外 loopback 请求；随后 native CUA 检查两处绿色箭头和右对齐的实际外观。
- 原生点击主面板箭头后，既有设置窗口选中关于，软件更新标题／开关／查看更新按钮全部可见；截图中软件更新位于内容顶部。Sparkle 原有 Learn More／Remind Me Later／Skip 窗口保持原样。选择 Remind Me Later 后提示保留；再选择 Skip This Version 后，侧栏状态和主面板箭头均消失。最后正常退出临时 App 并清理唯一测试偏好域。
- 公网 HTTPS：新二进制的独立副本使用构建 2 的版本元数据，从旧兼容源自动发现真实 alpha.3；重启后恢复。同样在 `automatic=false` 下手动发现 alpha.3。测试不冒充原始公开 alpha.2 安装包，后者不含 Sparkle。
- 证据：忽略目录 `.local/update-indicator-tests.log`、`update-indicator-swift-full.log`、`update-indicator-native/result.json`、`update-indicator-public-auto/result.json`、`update-indicator-public-manual/result.json`、`update-indicator-local-cases/result.json`。交互记录中的请求列表最终包含随后两次人工打开 Sparkle 的请求；最初自动检查／重启观察期只有一次 loopback 请求。公网结果的 `requests=[]` 仅指内部 loopback server 未被访问，不表示没有 HTTPS 请求。

## 当前边界

本轮构建和交互验收均使用隔离副本，未替换日常源码 App、未改其设置或接入数据；未提交／推送／发布新版。GitHub alpha.3 原始 ZIP 不因源码修改而改变。本轮完善的是已知新版提示的保留与入口，之前某次日常自动提醒未出现的完整历史原因仍不能从既有日志确定。

## 后续：本地独立包与用户 UI 演示

用户随后要求先构建本地包并模拟更新，已将本轮发行配置升为 `0.1.0-alpha.4`／构建 `4`（`release-prerelease=true`），但未发布。独立包位于忽略目录 `.local/releases/0.1.0-alpha.4-local-ui/`；ZIP SHA-256 为 `c4acb7f8b28687978f422e2af8709789a490f90560ef2d49e9c30013d9f8e71f`。构建内签名深度验证及 12 个 arm64 Mach-O 的最低系统／依赖路径检查通过；此包尚未重复执行完整后端运行验收。

由独立包复制唯一 `updatetest` 身份的只读演示 App，loopback 清单模拟 `0.1.0-alpha.5（模拟）`／构建 5。真实 SDK 自动检查成功，新版提示可见；原生截图确认主面板箭头、关于行右对齐箭头，以及点击后软件更新区域定位。关于页明确标注“这是模拟更新演示”。演示留给用户操作，退出后脚本清理；源安装包、日常 alpha.2 App 和线上清单保持不变。会话与运行信息在 `.local/alpha4-ui-demo/`。

## 后续：Download 按钮

信息更新按钮由 Learn More 改为 Download，简体／繁体分别为“下载”／“下載”。通过打包时覆盖随包 Sparkle 的对应资源键实现，不修改上游 checkout／artifact，签名前完成，并对资源键变化做失败检查。原生窗口实际显示 Download；点击后浏览器实际打开模拟条目配置的 alpha.3 Release 页面（未来版本仍为模拟，没有真实 alpha.5 发行）。会话结束后绿色提示继续保留。

用户随后决定直接打开版本 Release 页面，取消 Assets 精确定位。临时增加的 Release 锚点已移除，alpha.3 Release 说明已核对恢复原文；appcast／发行记录／锚点校验及测试也恢复原状。此后发布不需要锚点维护。

本地 alpha.4／构建 4 包已重建；当前 ZIP SHA-256 为 `74445edae36f54a0d36b1b113bbb22773e8ca40d01bdb2c4b0eb702a6bb8b122`，取代上一节的早期本地包。资源覆盖测试 2 项、appcast 原有 4 项、生成清单和公开 Release 校验通过。没有发布 alpha.4 或推送源码；日常 App 未替换。原生工具初次连接新演示身份耗时很长，不将其算作产品检查更新耗时。

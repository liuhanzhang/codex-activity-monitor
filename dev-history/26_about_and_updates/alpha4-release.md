# alpha.4 发布与日常 App 更新

日期：2026-10-29。用户明确要求按此前步骤继续，包含提交／推送、发布 alpha.4、更新清单、公网验收及日常本地 App 更新。

## 已验证的发行产物

- 功能提交 `05303f95c9cfaeb150a260111cfefbc1dd300cda`，已正常推送 main。版本 `0.1.0-alpha.4`、构建 `4`，明确 `release-prerelease=true`。
- ZIP `Codex-Activity-Monitor-0.1.0-alpha.4-macos-arm64.zip`，25,247,767 字节，SHA-256 `74445edae36f54a0d36b1b113bbb22773e8ca40d01bdb2c4b0eb702a6bb8b122`。
- 包 build ID `8c6b8cc5e40910d15bc339acf7474c341441bd5bb616c5e5d6c96d4749ee33ff`；发布前重新计算源码／打包文件摘要，确认与 manifest 一致，未把早期演示包冒充最终包。
- 最终 ZIP 完成隔离包验收：源码、开发 Python、工具链及 IP 网络拒绝条件下，全部离线词表、worker、collector、bridge、合成时间线／平均速度、接入恢复和缺失 App 回退通过。签名深度验证、12 个 arm64 Mach-O 架构／最低系统／依赖检查通过。本次没有用该脚本的 `--gui`。
- Python `test_app*.py` 38 项、Sparkle 资源覆盖 2 项通过；Swift 默认运行分别报告 68、151 项通过（包含既有 opt-in 默认跳过项）。此前同一 UI 实现的原生箭头／导航／稍后提醒／跳过／Download 交互证据见 [更新指示结果](update-indicator.md)。
- 私有本地证据在 `.local/alpha4-release/`，不发布用户数据、构建缓存或签名私钥。

## 已发布与公网验收

- [alpha.4 Release](https://github.com/liuhanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.4) 已公开，GitHub `prerelease=true`，实际发布时间 `2026-10-29T11:51:23Z`；tag 对象精确指向功能提交 `05303f9`。
- [发布源码 CI](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/40867933473) 成功：Python 540 项通过，Swift 报告 219 项（含默认跳过的 opt-in）。
- 清单提交 `098519c13b9f5df3a7caa25dcdde585bb289e682` 已推送，[Pages 部署](https://github.com/liuhanzhang/codex-activity-monitor/actions/runs/40868768200) 成功。三个无鉴权 HTTPS 清单逐字节匹配本地生成结果，旧兼容源与新预发布源内容相同，稳定源仍为空。
- 从公开 Release 重新下载 alpha.4，验证 ZIP 与 SHA256SUMS 一致，解压检查版本 4、分发类型和签名。原始 alpha.3 发行包以其发布 SHA-256 校验后复用，没有用新源码的版本伪装替代原始客户端。

| 客户端 | 触发及结果 |
| --- | --- |
| 原始 alpha.3 二进制的隔离副本，保留构建 3 | 真实 HTTPS 自动发现 alpha.4 |
| 下载的 alpha.4 副本，测试元数据设置为 3 | 旧兼容源自动发现 alpha.4，重启恢复提示 |
| 下载的 alpha.4 副本，保持构建 4 | 返回已是最新，无新版误报 |
| 新实现的构建 3 测试副本，自动检查关闭 | 手动检查发现 alpha.4 |
| 实际日常 source alpha.2／构建 2 | 原生手动检查窗口明确显示 alpha.4 可用 |

独立副本均使用唯一 bundle ID／偏好域、只读运行与隔离数据目录。公网记录中的 `requests=[]` 指脚本内部 loopback server 未被访问，不能解读为无 HTTPS 请求。生产清单没有模拟 alpha.5／9999。

## 日常更新实施

日常 App 仍为 `.local/gui/Codex Activity Monitor.app` 的 source 构建。核对其固定后端快照与当前 55 个 Python 源文件逐字节一致；本次保留原 Python、固定后端快照、运行／数据目录和正式 bundle ID。由同一源码的 debug 构建准备 source 身份候选包并重新签名，替换前备份旧 App；不会把 standalone 验收当作实际日常更新证据。

实际日常构建 2 检查到 alpha.4 后，通过正常退出结束旧 GUI（PID 83646）及其 collector（PID 83686），未强制终止。替换时保留 App 根目录 inode，仅原位切换已签名的 Contents；旧包、旧 Contents 及验证信息在忽略的 `.local/alpha4-release/` 中备份。

新实例为 source alpha.4／构建 4，GUI PID 99119，collector PID 99164；不是 Preview，也没有把日常实例切为 standalone。核对唯一日常 GUI、实际加载随包 Sparkle、快照新鲜，以及语言／登录启动／接入选择、自动检查偏好、接入 ID 与原备份指纹、`.monitor-owned` 和 timeline SQLite inode 全部保留。

原生关于页显示完整 alpha.4／4，删除的三个条目不再出现；正常手动检查的 SDK 窗口及关于页均确认已是最新，未显示更新箭头符合预期。运行状态中的采集、bridge、解析、tokenizer、历史、时间线、checkpoint、日志均为正常。原有“任务结束后重开 Codex 完成切换”提示在更新前就存在，仍保留；没有重启 Codex。当前轮跨 collector 重启产生的 observation gap 也按原语义保留，不回填为连续观测。模拟演示 App 已正常退出并清理。

## 验证边界

自动检查测试通过新的独立偏好域触发真实 SDK 调度；不改系统时间、日常检查日期，也不向生产源写入合成高版本。原始公开 alpha.2 不含 Sparkle；构建 2 的 SDK 探针是独立测试副本的版本元数据配置，日常本机 source 构建 2 则确实含 Sparkle，两者分开记录。

Developer ID／公证、macOS 14 实机、长期驻留及自然等待一天的再次调度仍不在本轮通过范围。历史那次日常自动提醒缺失的完整原因不能回溯确认；此次记录明确区分已确认的状态保留修复与未知根因。

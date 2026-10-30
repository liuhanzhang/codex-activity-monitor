# 关于页与自动检查更新

日期：2026-10-28。用户要求在设置中增加关于页，并自动检查、提示新版本；下载暂由用户手动完成。

下文记录首版关于页与 GitHub API 实现，作为本轮演进记录保留。当前更新服务迁移至 Sparkle，方案见 [Sparkle 接入方案与步骤](sparkle-plan.md)，首次本地验证见 [Sparkle 实现结果](sparkle-results.md)。后续已完成发布分类解耦、Pages 和 alpha.3 上线，见[实施步骤](channel-release-plan.md)与[公网验收结果](channel-release-results.md)。2026-10-29 后续加入[常驻绿色更新箭头与关于页定位](update-indicator.md)，并完成[alpha.4 发布和日常 App 更新](alpha4-release.md)。

## 实现

- 设置增加「关于」，展示应用图标、完整版本、构建号、构建类型、作者与版权、项目主页、发行说明、问题反馈、MIT 与本地第三方许可入口。
- `pyproject.toml` 的版本、预发布标识、构建号是本地与独立构建的共同来源。保持 Apple 的 `CFBundleShortVersionString` 为三段数字，额外用 `CAMReleaseVersion` 保留 `alpha.2` 等 SemVer 标识；未来发行须递增 `build-number`。
- 默认开启自动检查，启动延迟 10 秒，运行期间每分钟判断是否到期，两次自动尝试至少相隔 24 小时；手动检查不受自动开关和间隔限制。失败不触发后台弹窗，不阻塞观察。
- `UpdateStore` 独立管理网络、状态和原子写入的 `updates.json`，退出和卸载清理时停止；未完成请求不能在清理后重建目录。卸载已识别新增的偏好文件。
- 使用无鉴权 HTTPS GitHub Releases 列表（最多最近 100 条），比较 SemVer；预发布构建允许预发布与正式版，正式构建仅接收正式版。跳过草稿、非法版本和缺少已上传 macOS arm64 ZIP 的发布。不是按发布时间或字符串字典序选择。
- 新版提示保留至下次检查或升级，显示在主面板与关于页，齿轮按钮有提示点。下载链接固定指向本项目 GitHub 的对应发行页，不执行安装，不申请系统通知权限。
- Preview、只读模式、隔离 profile 和注入管理客户端的测试不会启动真实更新请求，不读取日常更新设置。
- 更新请求不携带用户凭证、聊天、日志或使用记录。GitHub 仍能接收正常网络连接元数据。设置提供自动检查开关。

## 技术依据与边界

GitHub [`releases/latest`](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) 不返回预发布，因此当前 Alpha 阶段使用 Releases 列表。匿名 API 有限流，手动失败时提供直接查看发行页的入口。

未来自动下载、校验、替换和重启建议接入 [Sparkle](https://sparkle-project.org/documentation/)，为更新包配置 EdDSA 签名和 appcast，另行验证嵌套后端进程退出、集成路径、签名与公证。当前未实现这些安装行为。

## 验证

- 默认 Swift 测试通过；最后的数据目录归属保护改动后，更新、HTTP、版本排序、快照及卸载相关的定向运行通过（14 项，含 1 项显式跳过的联网测试）。
- Python `test_app_control.py` 25 项与版本元数据测试 1 项通过。运行时显式指定 `PYTHONPATH=src`；本机 venv 的 editable 安装指向旧部署，首次未指定时实际执行旧代码并导致新增卸载场景失败，已纠正后重跑。
- 资源导出一致性、Python 编译与 `git diff --check` 通过；Preview 已构建，读取确认完整版本 `0.1.0-alpha.2`、构建号 `2`、Preview 独立 bundle ID，且包含项目许可证和第三方许可文本。
- 通过原生 Preview 实际检查中英文关于页、导航切换、滚动、图标、版本与本地许可入口。Preview 更新开关与检查按钮禁用，没有读取或修改日常偏好。可用新版的选择与保存已用合成数据验证；未把未发生的真实新版发布当成通知验收。
- 真实无鉴权 `GitHubUpdateClient` 请求返回 HTTP 403 限流，显式联网测试失败，不能记为联网成功。通过维护者本机 `gh` 的只读认证接口另行确认当前发行有 `alpha.1`、`alpha.2`，两者都有符合筛选规则的 ZIP；应用自身不使用该认证。
- HTTP 注入测试覆盖正常 JSON、请求不携带鉴权或正文、403／429／404／500 和无效 JSON。后台静默失败与手动错误提示分别验证。
- `iconutil` 在受限环境报 `Invalid Iconset`；相同 Preview 构建在获准的原生权限环境成功，没有修改图标转换流程。
- 未发布新版本、未提交或推送、未替换日常运行 App。未执行 macOS 14 实机、长时间自然后台或自动安装验收。

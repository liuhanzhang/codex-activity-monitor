# 独立开发版的构建与验收

范围、设计与验收边界见 [第 20 章](../dev-history/20_standalone_packaging/README.md)。

## 构建

开发者需要 arm64 Mac、Swift 6 / Xcode 26+ 和用于引导的 Python 3.11+，构建机器的 macOS 版本须满足所选 Xcode 的要求。App 的最低部署目标为 macOS 14.0。运行：

```sh
bin/prepare-standalone
bin/build-standalone
```

prepare 只在本仓库 `.local/packaging/` 下安装构建工具、固定的 uv 0.13.5 与 python-build-standalone CPython 3.14.9（20261021），不修改系统 Python 或 PATH。独立构建不能直接复用本机 Homebrew Python：其原生库可能要求高于目标系统的 macOS。构建会检查每个随包 Mach-O 的架构、最低系统版本与动态库路径；不满足 arm64 / macOS 14.0 目标即失败。

Python 依赖约束保存在 `requirements-build.lock`，项目直接依赖仍由 `pyproject.toml` 声明。prepare 和第一次构建需要网络下载依赖、词表与带固定校验和的上游许可说明；运行成品不需要安装这些构建工具。

默认输出：

- `.local/standalone/Codex Activity Monitor.app`
- `.local/standalone/Codex-Activity-Monitor-0.1.0-alpha.3-macos-arm64.zip`
- `.local/standalone/build-result.json`：构建身份、ZIP SHA-256、体积与逐个二进制检查结果。

发布版本由 `pyproject.toml` 中的项目版本和 `[tool.codex-activity-monitor].version-suffix` 组合而成，当前为 `0.1.0-alpha.3`；`version-suffix` 仅影响显示名称；GitHub 发布类型由独立的 `release-prerelease` 布尔值指定，两者不推断也不强制一致。ZIP 文件名使用 `Codex-Activity-Monitor-{发布版本}-macos-arm64.zip`，tag 使用 `v{发布版本}`，构建清单与结果也记录完整发布版本。App 的数字版本保持为 `0.1.0`。Alpha 阶段与签名、公证状态分别记录。

外层 App 的包标识固定为 `com.liuhanzhang.codexactivitymonitor`，后端包标识为 `com.liuhanzhang.codexactivitymonitor.backend`，开发版与后续正式版沿用同一身份。本地 GUI 构建使用相同的主 App 标识，Preview 构建追加 `.preview` 后缀。

后端入口是 `Contents/Helpers/MonitorBackend.app/Contents/MacOS/monitor-backend`，保留 console stdio；Python 与资源通过 PyInstaller 的标准嵌套 App 布局交付。外层配置仅有相对后端路径和 build ID，不写构建机器的 home / 源码 / venv 位置。

源码构建、外层 App 和嵌套后端 App 均声明 macOS 14.0。恢复弹窗使用标准强调按钮及已有的内容布局；后端工作目录在 macOS 26+ 使用 `posix_spawn_file_actions_addchdir`，旧系统显式调用 `posix_spawn_file_actions_addchdir_np`，避免新系统继续使用已弃用接口。

SwiftPM 可能为较低部署目标加入构建机器的 Swift 兼容库搜索路径。两种 App 构建都会在签名前检查实际 Swift 运行库依赖，确认没有需要随包携带的库后才移除这些工具链路径；若检测到所需库则停止构建，不能通过删除路径掩盖缺失依赖。

## 隔离验收

```sh
.local/packaging/portable-venv/bin/python packaging/verify_app.py \
  .local/standalone/Codex-Activity-Monitor-0.1.0-alpha.3-macos-arm64.zip \
  --result .local/standalone/acceptance-result.json --gui
```

macOS 不允许嵌套调用 sandbox-exec，所以在 Codex 工具沙箱中运行时需要对这条验收命令单独放行。脚本先解压 ZIP 到 `/private/tmp/` 中包含中文和空格的路径，再给被测子进程施加禁止源码目录、Homebrew、构建 Python、当前 Xcode 工具链、用户 Codex 历史、外部 tokenizer 缓存及 IP 网络访问的 policy。用可达的本地临时 HTTP 服务作为负对照，确认被测进程确实不能联网。

所有接入操作使用显式 disposable profile（FileEnvironment / ProfileServices），不调用日常 launchctl，不读取真实会话；假 CLI 回显合成事件与二进制内容并返回 17。GUI 也仅通过 `--test-profile` 启动。测试后退出 GUI、停止测试 collector 并清理临时目录。

## 交付边界

目前是 ad-hoc 签名的 Alpha 预发布版。代码签名验证通过不代表已通过 Gatekeeper 的下载安装流程，也不代表已公证。朋友仍需安装 Codex Desktop；默认原版 CLI 路径与开发构建一致，非默认安装路径尚需单独处理。安装、首次手动允许、真实 Desktop 接入、macOS 14 实机与自然长期使用不能由上述合成验证代替。macOS 14 兼容性目前以编译目标、包声明和逐个 Mach-O 检查为证据，旧系统实际运行仍待验收。

升级前须等待任务结束，并完全退出 Codex Desktop 和 Monitor，再替换 `/Applications` 中的 App；先打开 Monitor 完成配置，再打开 Codex。GUI 退出只停止其观测会话与采集器，不结束 Desktop 持有的 bridge。只读卷上的独立 App（含 App Translocation 和磁盘映像）在写入接入配置前即被拒绝，以免将临时挂载路径写入长期 launcher。桥接代码或 App 路径变化后，接入状态会保留重启要求；旧版缺少配置修订证据的存活 bridge 也不能算作当前接入已生效。

后续构建会将项目 [MIT 许可证](../LICENSE) 放入 App 的 `Contents/Resources/LICENSE`；CPython、Python 发行版的上游汇总说明与已安装依赖的许可文本另存于 `ThirdPartyLicenses`。项目授权不替代这些第三方许可；素材来源与完整分发复核仍见发行准备清单。

本地与独立 App 的版本元数据统一从 `pyproject.toml` 生成：`project.version` 为三段数字版本，`version-suffix` 为可选显示后缀，`release-prerelease` 为独立的 GitHub 发布分类，`build-number` 为发行时需要递增的构建号。关于页使用保留预发布标识的 `CAMReleaseVersion`；`CFBundleShortVersionString` 保持 Apple 数字格式。发布流程应使这些值与 Release tag 一致。

Sparkle 的清单、Pages 初次上线、后续发布、公钥与原生验收见 [更新发布指南](updates.md)。App 嵌入固定版本 Framework，保留许可证，移除 SwiftPM 开发目录的绝对搜索路径后签名。Sparkle 使用递增的 `CFBundleVersion` 比较更新；`CAMReleaseVersion` 仅用于完整版本显示。

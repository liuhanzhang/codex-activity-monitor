# 自包含试用包：实施与验收结果

日期：2026-10-26。本机：arm64、macOS 27.0（26A428）。

后续授权更新：项目已采用标准 [MIT 许可证](../../LICENSE)。打包脚本将其带入后续 App；本章记录的 ZIP、build ID 和校验和仍对应首次打包验收快照，本次许可证更新没有重建或改写该产物。

## 已完成

- 新增 `bin/prepare-standalone` 与 `bin/build-standalone`，独立准备工具并构建 release GUI、嵌套后端 App 和保留符号链接的 ZIP。
- 保留开发模式的 `python -m` 启动；冻结模式直接启动后端 executable，在 CLI 导入前分流 multiprocessing worker。
- GUI 从当前 App 位置解析相对后端路径，从当前用户 home 解析数据目录；发行配置不含开发机源码、venv 或用户绝对路径。
- collector 使用独立的冻结程序环境启动；外置 launcher 不依赖源码存在，并保留原版 CLI 的缺失应用回退。
- 模块指纹来自 build manifest。全部 7 个 tokenizer 编码及 6 个底层资源文件随包交付，显式选择只读包内缓存。
- 带入 CPython、固定 Python 发行版与已安装依赖的许可说明。未携带用户会话、凭据、原版 Codex 或运行记录。

## 实际解决的打包问题

1. PyInstaller 默认缓存位于用户 Library；构建改为本仓库 `.local/standalone/build/pyinstaller-cache`，避免改写全局缓存。
2. 普通 onedir 直接嵌入 Helpers 时，签名工具将 Python.framework 的链接误判为无效嵌套代码。改用 PyInstaller 标准 `BUNDLE(COLLECT(...))`，保留 console stdio，外层嵌套 `MonitorBackend.app`。
3. 开发机的 Homebrew OpenSSL 动态库最低要求 macOS 27；即使 GUI 声明 macOS 26，原始试验包仍不能据此承诺 26。最终改用 python-build-standalone CPython 3.14.9、构建 20261021；逐个 Mach-O 检查成功后才生成成品 ZIP。
4. 验收脚本的两个早期断言已修正：curl 的错误文本不能直接证明网络被拒绝，现使用可达的本地监听端口作负对照；完整恢复会删除 launcher，缺失应用回退应在恢复前验证。未为迁就测试改动产品恢复语义。

## 最终产物

- App：`.local/standalone/Codex Monitor.app`
- ZIP：`.local/standalone/Codex-Monitor-0.1.0-arm64-trial.zip`
- ZIP：22,912,129 bytes，约 21.85 MiB；应用文件总大小：45,900,168 bytes，约 43.77 MiB（不重复计算符号链接）。
- build ID：`bb40e889fd39393c2ab061c954790d3781c80873f785fb5959bdc4de12d58281`
- ZIP SHA-256：`cb49fa8cc9415a48e9ba98e9ed6311875d7278128beecc362d51d6b9922745e8`
- ad-hoc 签名的 `codesign --verify --deep --strict` 校验通过，包括 ZIP 解压、移动后的副本。
- 7 个实际 Mach-O 均为 arm64；最低系统版本的最高值为 26.0.0。动态依赖和 rpath 未引用构建机器的 Homebrew / Python 路径。

逐个二进制记录见 [构建检查](evidence/build-inspection.json)，最终 ZIP 的运行记录见 [隔离验收](evidence/isolated-acceptance.json)。完整日志和带本机绝对路径的 build-result 保留在被忽略的 `.local/`。

## 已执行验证

| 验证 | 结果与边界 |
| --- | --- |
| Python 源码全套 | 便携 CPython 3.14.9 下 485 项通过；使用显式构建词表缓存，包含新增的冻结启动／launcher 回退回归。分词错误隔离测试故意触发的 Rust panic 被预期捕获 |
| Swift 源码全套 | 118 项通过，包含新增的 bundle 重定位、目录越界拒绝与开发配置兼容测试 |
| 最终 ZIP 资源隔离 | 在中文与空格路径解压，sandbox 拒绝源码、构建 Python、Homebrew、用户 Codex 历史和外部词表缓存；文件访问负对照通过 |
| 最终 ZIP 网络隔离 | 对可达的临时本地 HTTP 服务连接被拒绝；IP 网络入站／出站被 policy 禁止，Unix socket IPC 保持可用 |
| tokenizer | 7 种编码的同步计数与一个真实 spawn 分词子进程计数一致，未访问人为指定的空外部缓存 |
| collector 与 timeline | 冻结入口启动 collector，实时健康检查通过；从冻结 bridge 观察合成回合并查询到相应 timeline phase |
| bridge 透传 | 原样透传合成 JSONL 与包含全部字节值的二进制内容，stderr 与退出码 17 保留 |
| 恢复与回退 | 停止采集、App 临时移动后外置 launcher 回退、恢复原始环境并删除已无依赖的 launcher 均通过 |
| 原生 GUI | 解压副本在同样的 sandbox 与 disposable profile 下启动；`management_state=configured`、`transport_fresh=true`，GUI build ID 与后端及 ZIP 一致。无连接聊天时菜单显示 unavailable，不能据此推断真实聊天覆盖 |

实际命令、构建条件与隔离脚本说明见 [构建说明](../../packaging/README.md)。未替换日常 GUI，未对日常 launchctl 或 Codex 接入执行配置、停止或恢复。

## 尚未验收

- macOS 26 实机运行：当前只有构建目标和二进制检查证据。
- 朋友机器的真实首次安装、Gatekeeper 手动允许、默认原版 CLI 路径与 Desktop 版本兼容性。
- 真实 Codex 接入与重启、自然长期使用、升级和卸载的完整朋友侧流程。
- Developer ID、公证、自动更新与公开发行准备。

本轮交付的是独立打包验证通过的试用产物，不将合成验证写成上述验收已经完成。没有执行 Git 提交或推送。

# Remote 连接与全局强制 CLI 修复

日期：2026-10-15。

Desktop `26.1007.71326 / 20398` 的发布代码中，`CODEX_APP_SERVER_FORCE_CLI=1` 会让所有普通连接跳过 WebSocket 选择，包含原本连接 `wss://codex-cloud-backend.chatgpt.com/` 的 `durable` 主机。旧 Monitor 安装将它全局设为 `1`，实际日志出现 `hostId=durable transport=stdio`；本地和 `durable` 各派生一个本地 app-server，并打开同一个 state SQLite 文件。

正常的手机 Remote 可以接入 Desktop 自管的 stdio app-server，也可以接入 Desktop 共同连接的 daemon；多客户端不要求多个会话执行实例。额外本地后端复用 Remote 注册身份，与已观察到的注册 409、连接归属切换相符。2026-10-14 约 14:00 的原始手机请求未保留，因此不能把这次修复描述为已重现并确认了那次故障的全部因果链。

## 修复范围

1. 安装和显式启动 Desktop 都设置 `FORCE_CLI=0`，本地继续由 `CODEX_CLI_PATH` 选择包装器，保留 `durable` 云路由。
2. 重新安装时更新备份文件的 `installed` 值；保留首次安装的 `previous`，使升级后卸载仍能恢复原环境。
3. 当前安装直接更新登录脚本、安装所有权记录和 GUI 会话变量，不重启 collector，不关闭已有对话。Desktop 需完整重启后才取得新环境。

不更改 App 安装包、CLI 二进制、会话数据、Remote 注册记录或写入锁。

## 验证与边界

- 安装回归覆盖新登录脚本、显式 Desktop 启动、旧安装升级后的卸载恢复，以及卸载时保留用户自行改动的变量。
- 复用已提取的当前发布函数，代入修复后安装配置，检查 `durable` 选择云 WebSocket；它是隔离的纯函数检查，不启动 App 或模型。
- 运行已有包装器参数透传、协议字节转发与退出测试。
- 当前配置迁移与检查结果保存在忽略目录 `.local/research/remote-lock-20261015/repair-verification.json`。已运行 App 的环境、重启后的实际云连接，以及手机双端交互分别记录，不将配置检查当作手机验收。

来源：发布包 SHA-256 `2f231a64e6dd30111bcd09e03c149cd06c8679b25914842772423251c4608ded`；内置 CLI `0.162.0-alpha.17.2`。提取代码、版本对照和只读运行证据见 `.local/research/remote-lock-20261015/architecture-findings.json`；该目录包含本机研究资料，不提交 Git。

## 2026-10-16 重启后的实机检查

14:13 完整重启后，Desktop 已升级至 `26.1013.31256 / 20586`，内置 CLI 为 `0.163.0-alpha.10.2`。因此以下结果证明当前组合可用，不能单独区分配置修复与 App 升级各自的影响；前文静态代码分析对应旧版本。

- `local` 通过包装器连接一个本地 app-server；旧的两个本地后端均已退出。`durable` 改走 WebSocket，初始化成功并进入 `connected`。
- 用户报告手机已打开当前对话。同一个新 app-server 收到该对话的多个客户端恢复请求，未发现新的写入占用或 Remote 注册 409；没有独立识别手机客户端，也没有完成双端发消息、断网重连或长期稳定性验收。
- 原 collector 持续运行，接上新包装器；当前后端的观测缺口、丢包、解析错误均为 0。
- Desktop 另在重连两个离线 Remote 环境，其环境 ID 均不同于本机启用的 Remote 注册；对应设备身份尚未核对。

本轮记录保存于上述 `repair-verification.json` 的 `post_restart` 字段，包括版本、进程、日志定位与验证边界。

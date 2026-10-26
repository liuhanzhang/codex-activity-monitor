# 自包含 macOS 开发版

日期：2026-10-26。状态：自包含打包与本机隔离验收已完成；朋友机器安装与真实 Codex 接入尚未验收。

当前构建入口与 `dev` 命名见 [packaging/README](../../packaging/README.md)，首次产物与证据见 [实施结果](implementation-results.md)。历史记录中的旧包名及校验和保留原样。

## 范围

- 用户已确认目标为 Apple Silicon（arm64），macOS 26 / 27。
- 保留 SwiftUI / AppKit GUI 和 Python 后端，以 PyInstaller `onedir` 收集运行时、代码、依赖与 tokenizer 资源。
- 单独构建 `.local/standalone/` 下的 App 与 ZIP，不覆盖日常 App，不修改日常 launchctl、collector 或 Codex 接入。
- 开发版先使用 ad-hoc 签名；Developer ID、公证、自动更新、公开仓库与 Release 后续处理。
- 朋友仍需安装 Codex Desktop；接入需要重新启动 Desktop。打包不携带用户的 Codex 二进制、凭据、历史、数据库或 Monitor 配置。

## 设计

1. 抽出后端命令构造：开发模式保留 `python -m`，冻结模式直接调用包内 executable；冻结入口在 CLI 解析前调用 PyInstaller 的 `multiprocessing.freeze_support()`。
2. GUI 在运行时从 bundle 定位后端，在当前用户目录定位数据；发行配置只保存相对资源位置、版本和构建身份。
3. 采集器、桥接和管理入口共用启动选择；外置 shell launcher 仍在 App / 后端缺失或接入关闭时直接回退原版 CLI。
4. 冻结模式的源码指纹来自构建 manifest，禁止依赖 `.py` 文件存在。tokenizer 所有当前编码资源在构建时下载、校验并内置，运行时从只读包内缓存读取。
5. ZIP 保留权限和符号链接；先解压到含空格和中文的另一目录，再进行运行验收。

## 本轮验收

- 针对启动选择、资源定位、launcher 回退及 GUI 配置的回归。
- 完整 App 由独立构建脚本产出；检查架构、最低系统版本、签名、外部动态库依赖与发行配置。
- 在操作系统 sandbox 中拒绝源码目录、开发虚拟环境与网络访问，使用显式 disposable profile：验证所有编码及分词子进程、collector 启停、桥接 stdio / 退出码、状态与 timeline 查询。
- 启动解压后 App 的隔离验证模式，确认原生 GUI 使用包内后端。
- 本机系统为 macOS 27；macOS 26 仅检查构建目标，实机兼容性必须单独记录。

## 依据

- [PyInstaller 运行时信息](https://pyinstaller.org/en/stable/runtime-information.html)
- [多进程、符号链接与外部进程](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)
- [macOS 架构与签名](https://pyinstaller.org/en/stable/feature-notes.html)

本章中的“通过”仅限已记录的源码回归、二进制检查和合成隔离验证。

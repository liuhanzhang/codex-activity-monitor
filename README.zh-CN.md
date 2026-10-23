# Codex Progress Monitor

[English](README.md) · [参与开发](CONTRIBUTING.md) · [架构阅读路径](docs/17_review_and_refactor/architecture.md)

Codex Desktop 的本机 macOS 菜单栏伴侣：查看聊天当前活动、模型与工具时间线、可见文本接收速度，以及本回合官方用量。

当前提供开发者本机构建，尚无独立签名发行包。开源许可证及发行验收仍待完成，见[系统审查与发行清单](docs/17_review_and_refactor/review.md)。

## 功能与语义

- 当前阶段、工具活动、持续时间和正文／工具参数的接收 token/s。
- 所选轮次的官方 Output / Reasoning 用量；Output 已包含 Reasoning。
- 模型／工具双轨、显式关联的主子智能体、三档缩放和独立活动查看器。
- 保留源参数与结果；部分观测、来源截断、未保留或不可用均明确标记。
- 简体中文与英文，可在设置中即时切换。

速度反映本地收到的可见文本，不代表隐藏推理或服务端算力。未知与覆盖不全显示 `—`，已知零正常显示零。

## 构建与运行

需要 macOS 26+、Python 3.11+、Xcode 26+ 及其 Swift 工具链，以及本机 Codex Desktop。默认原版 CLI 位于 `/Applications/ChatGPT.app` 内；其他安装位置需要显式指定原版 CLI。

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
bin/build-gui
bin/run-gui
```

首次打开日常 App 会配置 macOS GUI 会话的 CLI 包装器并管理采集器。先结束正在执行的 Codex 任务，再退出并重新打开 Codex Desktop，使新进程使用接入配置。设置中的恢复入口会恢复原配置、保留用户后来的变更，并等待 Codex 退出后清理所属数据。GUI 管理的运行环境会在 Monitor 退出后暂停观察。

开发验证使用独立的只读预览：

```sh
bin/build-gui --preview
open '.local/gui-preview/Codex Monitor Preview.app'
```

日常 GUI 运行时，终端可读取同一采集器：

```sh
bin/codex-progress watch
bin/codex-progress watch --lang en
bin/codex-progress status
```

更多配置与恢复步骤见 [GUI 使用](docs/06_gui_app_options_and_decisions/usage.md)及[启动与进程管理](docs/02_product_mvp/startup-and-processes.md)。历史文档中的本机路径仅用于说明当时的验证环境。

## 数据和限制

包装器转发原版后端的 stdio，通过有界队列和独立 Unix socket 复制观察事件。Python 聚合状态，Swift 读取快照；只读历史查询从 Codex JSONL 取得完整详情，SQLite 仅保存阶段元数据。采集不会启动第二个会话 writer。

快照可能包含聊天标题、路径、命令预览和数值指标，并非匿名数据。实时文本在内存中用于本地分词和所选活动详情；Monitor 不上传对话。依赖安装和 tokenizer 首次下载资源可能需要网络。不要将运行数据、登录状态、原始聊天或私密截图放入 Git 或问题报告。

产品代码位于 `src/`、`macos/Sources/` 和 `bin/`。编号 `docs/` 保留需求、决策和历史验收；`experiments/` 保存实验方法，`analysis/` 保存精简结论，`.local/` 是 Git 忽略的本机环境与数据。

目前只覆盖已接入的本机聊天，解析依赖上游版本；GUI 仍通过文件轮询。长期运行、全新机器、Remote、签名与公证、素材公开分发均不能由单元测试推定完成。当前证据见[审查报告](docs/17_review_and_refactor/review.md)、[验证记录](docs/17_review_and_refactor/validation.md)与[待办](docs/TODO.md)。

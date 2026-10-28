<p align="right"><a href="README.md">English</a> · <strong>简体中文</strong></p>

<div align="center">
  <img src="docs/assets/showcase/app-icon.png" width="72" alt="Codex Activity Monitor 图标">
  <h1>Codex Activity Monitor</h1>
  <p>一款 macOS 菜单栏应用，用于实时查看 Codex Desktop 的 Token 速度、聊天状态和智能体活动时间线。</p>
  <p>
    <img src="https://img.shields.io/badge/status-alpha-B7791F?style=flat-square" alt="Alpha 早期测试版">
    <img src="https://img.shields.io/badge/macOS-14%2B-black?style=flat-square&amp;logo=apple" alt="macOS 14+">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3B7A57?style=flat-square" alt="MIT 许可证"></a>
  </p>
</div>

<a id="效果演示"></a>
<p align="center">
  <a href="docs/assets/showcase/desktop-overview.jpg"><img src="docs/assets/showcase/desktop-overview.jpg" width="720" alt="原生 Codex Activity Monitor 概览：Token 速度、三个聊天及主／子智能体的共同时间线"></a>
</p>
<p align="center"><sub>聊天概览与主／子智能体时间线。</sub></p>
<p align="center"><a href="#安装">安装</a> · <a href="#首次使用">首次使用</a> · <a href="#从源码构建">从源码构建</a> · <a href="docs/user-guide.md">使用指南</a></p>

## 主要功能

<table>
  <tr>
    <td width="435" align="center"><a href="docs/assets/showcase/speed-overview.jpg"><img src="docs/assets/showcase/speed-overview.jpg" width="420" alt="原生菜单栏 Token 速度与三个活跃聊天的概览"></a></td>
    <td width="435" align="center"><a href="docs/assets/showcase/timeline.jpg"><img src="docs/assets/showcase/timeline.jpg" width="420" alt="主智能体和两名审查者在共同时间轴上各自拥有模型及工具轨道"></a></td>
  </tr>
  <tr>
    <td width="435" valign="top"><b>实时 Token 速度</b><br>菜单栏显示总速度，概览中查看各聊天的速度、当前活动和耗时。</td>
    <td width="435" valign="top"><b>智能体活动时间线</b><br>对齐主智能体与显式关联子智能体的模型活动和工具调用。缩放、向前回看，再返回最新活动。</td>
  </tr>
</table>

### 活动详情

点击时间块，打开独立的活动查看器，读取可用的用户输入、可见模型输出、工具参数、执行结果和文件改动。部分观测、截断和不可用内容会明确标记。

<p align="center"><a href="docs/assets/showcase/activity-detail.jpg"><img src="docs/assets/showcase/activity-detail.jpg" width="870" alt="原生活动查看器：主智能体与审查者的时间线、Python 测试命令及其执行结果"></a></p>

## 安装

从 [v0.1.0-alpha.1](https://github.com/LiuHanzhang/codex-activity-monitor/releases/tag/v0.1.0-alpha.1) 下载 Apple Silicon ZIP。这是使用 ad-hoc 签名、尚未经过 Apple 公证的早期预发布版本；首次打开请按下方步骤手动允许。也可以[从源码构建](#从源码构建)。

### 运行要求

| 要求 | 当前范围 |
| --- | --- |
| Mac | Apple Silicon Mac，macOS 14+。macOS 14 实际运行验证仍待完成。 |
| Codex | 本机已安装 Codex Desktop，安装和数据位置见下方说明。 |

独立 App 自带所需的运行环境和资源。

### Codex 的安装位置

当前版本使用 `ChatGPT.app` 自带的 Codex 组件。请确保 `ChatGPT.app` 位于 Mac 的“应用程序”文件夹中，对应路径为 `/Applications/ChatGPT.app`。Monitor 会自动查找其中所需的组件。

如果你使用其他安装方式，或更改了应用的名称、安装位置，当前版本暂不支持在界面中选择其他位置，详见[使用指南](docs/user-guide.md#codex-is-installed-somewhere-else)。

### 本机数据位置

Codex 的聊天记录默认保存在当前用户主目录下的 `~/.codex/`（`~` 表示你的用户主目录）。Monitor 只读访问其中的历史记录，用于显示活动详情和校正时间线。如果你自定义了 Codex 的数据目录，请参阅[使用指南](docs/user-guide.md#local-data)。

Monitor 自己的设置、接入配置备份和时间线数据保存在 `~/Library/Application Support/CodexActivityMonitor/`。

### 安装 App

取得 ZIP 安装包后：

1. 解压 ZIP，得到 `Codex Activity Monitor.app`。
2. 将 App 移入“应用程序”（`/Applications`）文件夹。
3. 双击打开 App。如果 macOS 阻止打开，请按下方步骤手动允许。

### 首次打开时手动允许

当前 Alpha 版本**没有 Developer ID 签名，也尚未经过 Apple 公证**。首次打开下载的 App 时，macOS 可能提示无法验证开发者或无法检查 App 是否包含恶意软件。

如果尝试打开后被 macOS 阻止，请确认 App 来自本项目，再按以下步骤操作：

1. 打开 **系统设置 → 隐私与安全性**。
2. 向下滚动，在 Codex Activity Monitor 的阻止提示旁点击 **仍要打开**。
3. 按系统提示验证身份，并在确认弹窗中点击 **打开**。

详细说明见 [Apple 官方指引](https://support.apple.com/zh-cn/102445)。

## 首次使用

1. 等待正在执行的 Codex 任务结束。
2. 打开 Codex Activity Monitor。首次启动会自动配置本机接入并启动采集器。
3. 完全退出并重新打开 Codex Desktop，让新进程继承接入配置。
4. 开始一个本机 Codex 任务，点击菜单栏入口，选择聊天，查看当前轮次、时间线和用量。

观察期间请保持 Monitor 运行。齿轮按钮打开独立设置窗口，也可使用 **Command-,**。在 **通用** 中调整 **登录时启动** 或切换 **English／简体中文**。首次配置默认开启登录启动，macOS 可能要求允许该登录项。对话与工具原文保持原语言。

**运行状态** 显示已检测到的组件异常及恢复，**日志** 提供本地诊断记录，支持搜索、筛选、详情和复制。关闭设置窗口不会停止观察；这些检查不能排除所有软件错误。

## 隐私与兼容性

- 数据在本机处理，Monitor 不上传对话；这不改变 Codex Desktop 自身的联网行为。
- 本机状态可能包含聊天标题、项目路径、命令预览和数值指标；实时文本用于本地分词及所选活动详情。
- 观察范围是已接入的本机聊天。远程会话与配置后端之外的聊天不属于本快速开始的范围。
- 接入依赖特定版本的 Codex 协议和 TRACE 行为，上游更新可能影响观察。

## 配置、取消接入与卸载

**先恢复配置，再删除 App。** 只把 `.app` 拖进废纸篓会留下接入配置和本机数据。

| 操作 | 步骤 |
| --- | --- |
| 启用或修复接入 | 打开 **设置 → 通用 → Codex** 开关，按提示确认配置、连接或重启状态。配置发生外部变化时，可使用界面提供的 **重新配置接入**。 |
| 暂停观察 | 退出 Monitor。保留接入配置，重新打开 Monitor 可恢复观察。 |
| 取消接入，保留 App | 关闭 **设置 → 通用 → Codex** 开关。恢复原配置，保留偏好与诊断日志；重开 Monitor 仍保持未接入。 |
| 完整卸载 | 在 **设置 → 通用 → 维护** 中选择 **清理本机数据并退出，用于卸载** 并确认。结束任务并完全退出 Codex，等待清理完成且 Monitor 退出，再删除 `.app`。 |

恢复时会保留你在接入后另行修改的配置值，不会强制结束任务。请保持 Monitor 运行直到清理完成；中途退出后可重新打开继续。卸载不删除 Codex 自己的对话记录、源码目录或开发虚拟环境。完整步骤见[卸载指南](docs/user-guide.md#uninstall)。

## 从源码构建

以下步骤面向开发者，在仓库根目录执行。需要 Python 3.11+、Xcode 26+ 及其 Swift 工具链；构建机器的 macOS 须满足所选 Xcode 的要求。安装依赖与首次下载分词资源需要联网。

### 源码运行

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
bin/build-gui
bin/run-gui
```

产物为 `.local/gui/Codex Activity Monitor.app`。这种构建依赖当前源码目录和 `.local/venv`，请保留它们。开发测试与只读 Preview 构建见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 构建独立 App

目前仅支持 Apple Silicon Mac。首次先准备独立打包环境，再构建：

```sh
bin/prepare-standalone
bin/build-standalone
```

产物为 `.local/standalone/Codex Activity Monitor.app` 及同目录下的 ZIP 安装包。App 包含 Python、依赖和分词资源，运行时无需源码目录、另装 Python 或 Xcode。日常使用请先将 App 移入 `/Applications`，再按上方的[首次使用](#首次使用)步骤接入。

当前构建使用 ad-hoc 签名，尚未公证。打包环境、隔离验收与兼容性验证边界见[打包指南](packaging/README.md)。

## 工作原理

包装器启动原版 Codex `app-server` 并转发 stdio 协议，同时观察生命周期事件和选定的流式 TRACE 增量。本地采集器关联聊天、轮次与活动，统计收到的 Token，再将状态推送给原生 Swift 界面。

对话历史仍由 Codex 管理。Monitor 只读历史以获取持久化内容并校正活动，自己的 SQLite 仅保存阶段元数据。数据流、测量边界和接入配置变更见[架构说明](docs/architecture.md)。

## 常见问题与参与开发

**没有聊天或没有数据？** 确认 Monitor 已配置且正在运行，等任务结束，完全退出并重新打开 Codex Desktop，再开始一个本机任务，检查连接指示灯。其他情况见[故障排查](docs/user-guide.md#troubleshooting)。

报告问题时，请提供 macOS 版本、Codex 版本、Monitor 构建和复现步骤。分享日志或截图前，请移除私密对话、登录信息和配置备份。开发、测试与贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

Copyright (c) 2026 Hanzhang Liu。采用 [MIT 许可证](LICENSE)。第三方依赖和外部来源材料保留各自的许可证与版权声明。

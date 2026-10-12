# 第一版使用说明

本机终端看板已实现。正文、工具参数与摘要分别统计，采集器独立运行，关闭看板不会停止采集或 Codex 任务。当前机器已安装登录启动和 Desktop 默认包装器；原环境保存在 `.local/runtime/desktop-environment.json`，不纳入 Git。

各组件如何通信、具体监听哪些事件，以及主 App / 隔离实例如何识别，见 [产品架构与工作方式](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/architecture.md)。

每个启动步骤做什么、环境变量保存在哪里、如何暂停采集器并恢复，见 [启动步骤与进程管理](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/startup-and-processes.md)。

## 当前机器：隔离试用（主 App 还有任务时）

本机隔离 App 的真实界面提交到实时指标路径已通过：正文持续正速度、工具完整执行时间、临时标题任务隐藏，任务期间没有丢弃或解析错误。无需重复已完成的有限任务；主 App 保持运行。其他机器或后续复查可以使用隔离实例。以下命令使用已有独立登录的测试 home 与产品测试 profile，创建额外实例：

```sh
/Users/bytedance/personal/codex_progress_monitor/bin/codex-progress desktop \
  --home /Users/bytedance/personal/codex_progress_monitor/experiments/desktop-isolation/runs/20261010-150337-2b232b/codex-home \
  --profile /Users/bytedance/personal/codex_progress_monitor/.local/product-implementation/desktop-1/profile
```

隔离实例已经打开时不要重复执行。两个窗口图标相同；请在新打开、使用测试聊天记录的窗口中操作。随后在终端运行下方 `watch` 命令，在隔离窗口新建两条聊天并行测试。隔离 home 与 Electron profile 均独立于主 App；默认主 App 的现有后端不会因此切换。

可用有限测试任务：一条不调用工具的 800 字正文；另一条执行只打印 START、等待 10 秒、再打印 END 的 Python 命令，要求不读写文件、不联网。应看到两个聊天名称/ID、正文累计与速度、工具活动与时间分别更新。Reasoning 若出现则只显示时间与摘要增长；短参数阶段可能很快结束。

隔离试用可以确认真实 Desktop UI 路径。本机已观察到主 home 后端接入；普通入口的独立启动验收和长期使用仍待确认，不要求重复重启正在使用的 App。

## 当前机器：主 App 接入

1. 已接入包装器的 Codex 可直接使用。若 App 是安装前启动、尚未接入，则等需要保留的任务结束，正常退出再从原入口打开一次，让新后端使用采集入口。
2. 在终端执行：

   ```sh
   /Users/bytedance/personal/codex_progress_monitor/bin/codex-progress watch
   ```

3. 按平常方式并行提交两条聊天。正文或参数生成期间应持续出现累计量和速度；Reasoning 只显示经过时间与收到的摘要增长。

看板里 `j/k` 或上下方向键选择聊天，Enter 展开活动详情，`q` 退出。已完成结果和最近活动保留约 10 分钟；空闲历史聊天默认隐藏。右侧“最近进展”不受连接保活刷新，无新事件不等于卡死。

Codex 自动生成标题等内部临时任务默认隐藏，避免与用户聊天混在一起；采集使用明确的 `ephemeral` 字段识别。JSON 诊断仍保留这些任务，使用完整 ID 的 `--thread` 筛选可以单独查看。升级代码后，退出旧看板再运行 `watch` 才会加载展示修复；包装器升级由下一次 App 启动加载。第三轮隔离界面试用已经通过核心实时采集检查。此前的断连丢弃已修复；短参数可能在一次计数刷新前就结束，本轮约 150 ms 参数没有出现实时速度快照，最终计数完整。主 App 后端已观察接入，普通入口的独立启动验收与长期使用仍待确认。

任务完成后实时速度显示 `—`，本段计数保留，这是预期行为。`observation_gap` 表示有增量漏收，`reconciled` 表示完成文本已校准最终计数；校准不能恢复漏收期间的实时速度。

`≈` 表示模型编码未确认，采用 `o200k_base` 估算。目前测试的 gpt-6-* 模型属于这种情况。速度是接收文本的本地 token/s，窗口约 2 秒；网络缓冲与计数刷新会造成波动。长文本、多流会自动降低重新分词频率，阶段、字符与持续时间仍按 250 ms 刷新。

`工具准备 / 执行未确认` 表示已经有工具调用证据，但尚无足够证据确认命令开始执行。明确审批请求显示为等待审批；有明确的 unified execution 启动/交互来源和进程 ID 时确认执行；其他命令以输出或完成事件确认，不使用固定等待时间推断。缺少这些证据的静默命令可能保留“执行未确认”，直到完成。

后台命令可能在聊天回合完成后继续运行。看板保留可明确识别的命令活动，并标注“回合已完成”，直到收到命令自身的完成事件。快照超过 3 秒未更新时显示观测中断，停止展示旧速度；不能据此判断后台任务已经停止。

## 常用命令

```sh
cd /Users/bytedance/personal/codex_progress_monitor
bin/codex-progress status
bin/codex-progress watch --thread 聊天名称或ID
bin/codex-progress watch --once --json
bin/codex-progress replay .local/product-preflight/tools-20261011/capture.jsonl --output .local/replay.json
```

`status` 显示采集器是否在运行、已连接后端与故障统计。未通过包装器启动的后端不会出现在采集连接中；没有近期任务时，看板也可能没有聊天。若 App 重新接入后仍无后端，先确认 `status` 的 `running` 为 true，再检查包装器与实际 CLI 路径。当前适配的是原版 CLI 的 stdio app-server；远程主机和预先连接的 WebSocket 后端首版不采集。

`replay` 接受实验捕获的 JSONL 或 gzip JSONL（具有 direction、event 和接收时间），使用原接收时间计算，不把读取文件速度当作生成速度。它输出指标 JSON；原始 Rust 日志格式需要实验适配器，不能直接当作此输入。

## 恢复原启动方式

```sh
/Users/bytedance/personal/codex_progress_monitor/bin/codex-progress uninstall
```

此命令卸载本产品 LaunchAgent，恢复安装时保存的启动环境，并停止采集器。随后正常退出并重新打开 Codex，就会使用原 CLI。当前已经承载中的任务可以正常结束；不要在任务执行中删除其包装器或虚拟环境。安装与卸载的实际对照已通过。

停止采集器本身不影响 Codex 通信：

```sh
bin/codex-progress stop
bin/codex-progress start
```

已经安装登录启动时，LaunchAgent 会自动重启退出的采集器。仅暂停本次登录会话的采集可使用 `launchctl bootout`，恢复使用 `bootstrap`，具体步骤见 [停止与恢复](/Users/bytedance/personal/codex_progress_monitor/docs/02_product_mvp/startup-and-processes.md#4-停止恢复与卸载)；完全卸载接入使用 `uninstall`。恢复期间漏收的内容会标为观测缺口，速度不可用；完整完成项到达后可校准最终计数，不能补造漏收期间的速度。

重连时重放的是有限生命周期元数据，已收到的最终指标可以恢复；终止元数据不缓存正文或完整参数。身份冲突或某条并行流计数不完整时，对应合计速度也显示为不可用。

## 其他机器 / 重新安装

需要 macOS、Python 3.11+ 和已登录的原版 Codex Desktop。默认 CLI 路径是当前安装包内的 `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`。

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
bin/codex-progress setup --launch-agent
```

非默认 CLI 可通过 `setup --real-cli /实际路径 --launch-agent` 指定。安装不读取或复制凭据，也不修改主 CODEX_HOME；它为同一个原版 CLI 增加 stdio 包装器。Python 安装路径或仓库位置改变后，应重新建立虚拟环境并执行 setup。

不安装登录启动时可使用 `setup` 和 `desktop`。隔离测试可额外传入独立 home / profile：

```sh
bin/codex-progress desktop --home /已登录的隔离home --profile /独立profile
```

此命令不会让已经运行的 Desktop 实例切换后端。隔离 home 必须已有自己的认证，不要为测试复制主账号凭据。

## 数据与运行边界

- 运行路径为 `.local/runtime/`，目录权限 0700，IPC socket 和指标快照 0600。
- 默认只保存指标与必要元数据，包括聊天名、工作目录、工具名及命令首行最多 160 字。正文和完整参数只在内存中用于分词；不额外持久化原始流。
- 新增的 SSE / WebSocket TRACE、DEBUG 诊断由包装器消费；普通诊断继续转交 Desktop。已有 Codex 会话与日志仍由 Codex 自己管理。
- 旁路队列最多 128 包 / 8 MiB；未关联内容最多 4096 事件 / 2 MiB；单内容流文本上限 8 MiB，聚合文本目标上限 32 MiB。超限会显式失去相应 token 指标，不静默生成准确计数。
- tokenizer 在后台单工作线程执行，按合计重算成本调整刷新间隔，完整项结束后释放文本。
- 旁路解析、聚合器或看板故障可降级；关键 stdio 包装器进程自身故障可能影响其承载的任务。恢复原入口的命令已经提供，不能保证杀死包装器也对任务无影响。
- 接入变量与上游诊断格式属于当前版本适配点。小更新沿用既有结论并做有限回归；出现实际缺口再定位，不自动重复全套实验。

具体证据和未完成验收见 [产品实现验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-mvp/README.md)。

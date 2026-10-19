# 活动时间线的预览与来源探针

配合 [第 09 章](../../docs/09_activity_timeline/README.md)。所有输出放在被忽略的 `.local/`；Git 中只保留脚本、合成截图和脱敏结构统计。

## 只读原生预览

最新选中与结果状态见[选中与结果状态](../../docs/09_activity_timeline/selection-status.md)。夹具包含 90 秒失败长工具、5 毫秒失败短工具和无开始时间的阻止记录，用于检查淡蓝底衬、局部图标与详情文案。

```sh
PYTHONPATH=src .local/venv/bin/python experiments/timeline/preview.py --output .local/gui-preview/runtime --keep-fresh
bin/build-gui --preview
```

通过系统正常打开 `.local/gui-preview/Codex Monitor Preview.app`。该 bundle 与日常 App 分离，使用 fixture provider；不创建观察 lease、不读真实历史、不配置接入。预览时间轴固定在 90 秒，只更新快照新鲜度，便于重叠和滚动检查。结束时退出 Preview，并在启动 fixture writer 的终端按 Ctrl+C。

图中所有聊天、代码、输出与检查数量均为手写合成内容。不要把真实聊天截图放进 `docs/assets`。

最新交互见[第二轮 UI/UX 调整](../../docs/09_activity_timeline/panel-ux-round2.md)，工具布局采用[来源时间方案](../../docs/09_activity_timeline/true-time-tools.md)，夹具新增持续 90 秒的工具及 5／10 毫秒短工具，检查局部叠色、粗线直接点击和多工具详情。夹具还包含 18 个毫秒级密集工具、旧 hook、4 秒上下文整理、原始命令／文件字段，以及多个区间重叠的工具和后续独立工具。菜单默认一屏 30 秒，可用加减按钮切换到 60 秒，90 秒轮次需要横向滚动；详情沿用所选比例，调整窗口只增减可见范围。最终交互结果见[来源时间验证](../../docs/09_activity_timeline/true-time-tools.md)，固定比例见[固定比例验证](../../docs/09_activity_timeline/fixed-scale.md)，叠放交互的前版证据见[布局验收](../../docs/09_activity_timeline/overlap-layout.md)。

模型第三段还包含手写的 22 毫秒边界交叠，用于检查思考、正文、参数仍逐段展示且可直接打开详情；工具只在实际图形覆盖的位置叠色，点击直接查看命中的工具；不能把视觉交叠当作模型并行证据。

## 隔离真实运行

这些探针会在**已经准备好的独立 Codex home** 中创建自己的测试聊天和有限的 print／sleep 工具调用，可能消耗模型用量。不要把 `--home` 指向日常 `~/.codex`，不要为读历史启动或 resume Desktop 的现有 writer。

将下例路径替换为明确的独立 home；输出目录必须尚不存在：

```sh
PYTHONPATH=src .local/venv/bin/python experiments/timeline/source_probe.py --home /absolute/isolated/codex-home --output .local/timeline-probe-new --runtime .local/timeline-probe-new-runtime
PYTHONPATH=src .local/venv/bin/python experiments/timeline/validate_sources.py --home /absolute/isolated/codex-home --run .local/timeline-probe-new
PYTHONPATH=src .local/venv/bin/python experiments/timeline/cross_turn_probe.py --home /absolute/isolated/codex-home --output .local/timeline-cross-turn-new
```

- `source_probe.py`：自有 stdio app-server、正确及错误 steering、item 时间字段、只读历史 API；加 `--runtime` 后经过产品 bridge／collector／query。`validate_sources.py` 用于读取独立 stdio 的 `stderr.log` TRACE；加 runtime 时 TRACE 由 bridge 管理，按实际私有日志路径处理。
- `cross_turn_probe.py`：12 秒实际进程在第一轮完成后继续，第二轮检查旧轮导航和所有权，最终核对 canonical 完成。
- 每个运行在退出时关闭自有 app-server 和 collector，删除探针生成的额外 `protocol.jsonl` 全文副本。Codex 自己的隔离历史与必要私有 TRACE 保持原归属，不作为公共夹具。
- 嵌套 macOS 沙箱可能拒绝执行工具；此时只能记录对应数据来源结果，不能写成真实命令／跨轮已通过。WindowServer 原生窗口测试也应在图形会话中单独运行。

## 针对性检查

本轮调整只需时间线、历史和查询相关 Python 检查；其他边界未修改时复用既有证据：

```sh
PYTHONPATH=src:tests .local/venv/bin/python -m unittest test_timeline test_history test_timeline_storage test_timeline_query
```

以下保留首轮较广回归命令和 Swift／窗口检查入口，按实际影响范围选择：

```sh
PYTHONPATH=src:tests .local/venv/bin/python -m unittest test_timeline test_history test_timeline_storage test_timeline_query test_engine test_file_editing test_progress_semantics test_overview test_menu_presentation test_usage test_app_control test_app_session
swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos
MONITOR_WINDOW_TESTS=1 swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos --filter viewerReusesWindowAndPreservesResizedFrameAcrossContentChanges
```

实际执行结果及兼容边界见[首轮实现记录](../../docs/09_activity_timeline/implementation-results.md)和[后续验收](../../docs/09_activity_timeline/refinement-results.md)；不要把构建、合成截图、真实 stdio、日常 App 与发行验收合成一个“全部环境通过”的结论。

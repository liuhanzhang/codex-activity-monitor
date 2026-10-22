# 移除推理均速

日期：2026-10-22。状态：已实现、验证并载入本机 collector 与 Monitor GUI。

本说明取代本章原需求中计算、保留、恢复和展示“最近推理均速”的部分。旧需求、实验、捕获和验收结果保留其历史含义，不代表当前功能。

## 原因与范围

Codex 的 `ItemCompletedEvent` 起止时间是本机处理条目生命周期时记录的时间，并不保证覆盖服务端完整推理过程。响应级推理用量也不能通用地分配给响应中的多个推理条目。将二者相除容易被理解为模型的实际计算速度。

- 删除后台单段计时、均速计算、最近有效样本保留与恢复，不再发布 `usage.latest_reasoning`。
- 删除 macOS 面板和终端看板的推理均速数值、占位符、详情及专用文案。
- 保留官方 Output / Reasoning 用量、按真实回合归属显示的规则及未知值语义。Output 仍包含 Reasoning。
- 保留可见正文、参数及摘要文本的实时接收速度。它由本地编码器和接收窗口计算，不包含隐藏推理，也不表示官方用量的实时增长率。
- 保留时间线中的推理活动、起止时间和现有内容详情。本次不增加推理块的 token 或速度详情。

## 兼容与加载

恢复数据继续使用版本 2，只读取计数和去重所需的字段；旧 `latest_reasoning` 和响应计时字段被忽略。collector 保留最近完成聊天的静态用量时也删除旧均速字段，后续快照不再写出。Swift 解码和终端渲染同样忽略旧快照中的均速字段。

需要重载 collector 并重新打开 Monitor GUI。已有 Codex 桥接进程可以继续提供原协议事件，不要求重启 Codex；重载期间的观测缺口按已有规则显示未知，不能恢复实时速度。

## 验证

- 138 项 Python 定向回归通过，覆盖用量、Engine、runtime、终端渲染、语言、概览、JSONL 合并与桥接集成。命令及输出保存在 `.local/reasoning-speed-removal-python.log`。其中 30 项为用量测试，不重复计入总数。
- 对最终补充的静态快照兼容路径，单独复测 `test_restart_keeps_completed_metrics_when_metadata_replays` 通过：原官方计数 `100 / 40` 在重启及元数据重放后保留，旧均速字段不再出现。测试按桥接过滤后的事件格式发送用量，并等待计数及完成状态，不依赖固定发布间隔。输出保存在 `.local/reasoning-speed-removal-recovery-final.log`。
- `swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos` 通过，包含所选回合归属、零值、旧字段忽略及已有时间线与界面行为测试。
- 使用已有并发捕获回放检查官方计数，两组结果仍为 Output / Reasoning `1424 / 156`、`1820 / 237`；不再校验或输出均速。报告保存在 `.local/reasoning-speed-removal-capture.json`。
- `bin/build-gui` 成功。日常 collector 的 `usage` 与 `collector` 模块指纹和当前源码一致；当前线程用量和恢复数据均无 `latest_reasoning`，核对结果在 `.local/reasoning-speed-removal-loaded.json`。
- 原生界面核对：统计行保留“输出 / 含推理”，没有推理均速；统计口径浮层仅保留官方用量及所选回合归属说明，关闭浮层后主面板正常。

未重新运行会调用模型的在线验收脚本；保留的旧计时实验不作为当前功能的验收结果。

# 设置、诊断与平均速度的并行分支集成

日期：2026-10-28，Asia/Shanghai。用户授权先在临时集成分支合并并验证。

## 分支与合并结果

- 基线：`main` 的 `6c39325`。
- 集成分支：`codex/integrate-settings-and-speed`，使用独立 worktree。
- 设置与诊断：`codex/settings-and-diagnostics`，原提交 `08e3c6d`，合并提交 `e4b8509`。
- 每轮平均速度：`feat/per-turn-average-token-speed`，原提交 `e518817`，合并提交 `43ea0bf`。

两次合并均保留原功能提交。`main` 仍为 `6c39325`；本轮没有推送、部署日常 App 或移除原功能 worktree。

## 冲突处理

只有两个文件存在文本冲突，均可同时保留两边新增内容：

- `dev-history/README.md`：同时保留第 24、23 章索引，按章节顺序排列。
- `src/codex_activity_monitor/collector.py`：源码指纹同时覆盖 `request_rate` 和 `diagnostic_log`、`diagnostic_health`、`integration_status`。

其他共同修改的指南、架构和双语资源自动合并后已检查。无需在两个功能之间取舍。

## 验证结果

机器：Apple Silicon，macOS 27.0（26A428），SDK 27.0；最低编译目标仍为 macOS 14。

| 检查 | 结果与范围 |
| --- | --- |
| Python 全套 | 529 项通过，108.485 秒；显式 `PYTHONPATH="$PWD/src"`，另核对模块实际来自集成 worktree |
| Swift 全套 | Model 68 项、App 135 项，共发现 203 项；11 项 opt-in 跳过，实际执行 192 项通过 |
| 隔离跨进程联调 | 15 个检查通过，真实 collector、bridge、Unix socket 和 CLI，使用一次性 profile 与合成 JSONL |
| 平均速度归档证据校验 | 285 次更新、19 轮、配对求和、44 个 item 计时缺口、稳定性分组及 266 个间隔一致；这是派生证据自洽检查，不是重新回放私有源 |
| 本地化 | `bin/export-localizations --check` 通过 |
| Preview | 构建、源码指纹比对和 `codesign --verify --deep --strict` 通过 |
| 差异检查 | `git diff --check` 通过 |

重现主要自动化检查：

```sh
PYTHONPATH="$PWD/src" .local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'
CLANG_MODULE_CACHE_PATH="$PWD/.local/clang-module-cache" swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos
PYTHONPATH="$PWD/src" .local/venv/bin/python tests/acceptance/verify_settings_diagnostics.py
PYTHONPATH="$PWD/src" .local/venv/bin/python bin/export-localizations --check
PYTHONPATH="$PWD/src" bin/build-gui --preview
```

本轮扩展现有隔离验收脚本：在启用健康摘要的同一个 collector 中，通过查询 socket 和产品 CLI 读取平均速度。首条尚未确认的 usage 显示 100 T/s；追加 usage/count 后得到 400 token / 3 秒，确认队列清空。重复查询及管理状态检查不改变均值。随后原有恢复、等待 bridge、stdio 转发、停止、日志保留、重新接入和清理检查继续通过。

首次 Preview 构建的 Swift 编译成功，但系统 `iconutil` 在沙箱中报告 `Invalid Iconset`；相同构建在沙箱外完成并通过签名及源码核对。未修改图标或绕过产品构建步骤。

## 原生界面检查

通过 Computer Use 操作集成 worktree 的只读 Preview，使用独立合成 snapshot、由产品 `TimelineQuery` 生成的 timeline fixture 和合成日志：

1. 主面板显示平均速度 `133.3 T/s`。
2. 齿轮打开独立设置窗口，通用、运行状态、日志页面均可访问；健康摘要能够展示，日志能列出并打开 JSON 详情。
3. Command-W 关闭设置后主面板仍在，平均速度继续可读。
4. 向合成 JSONL 追加第三笔 usage/count 并重新生成 fixture，平均速度更新为 `120.0 T/s`。
5. Command-, 重开设置并保留页面与日志选择；Command-Q 正常退出 Preview，进程退出码为 0。

合成 snapshot 过期时，界面保留历史平均速度并提示实时数据不可用，未将 fixture 当作日常实时观测。原生检查使用 fixture adapter；collector/CLI 的真实跨进程检查另由上面的隔离验收覆盖。

## 验证边界

这次结果支持将两个功能作为整体继续验收。尚未验证日常 collector 到 GUI 的合并版本自然回合、Codex 完整重启、长期驻留、macOS 14/15/26 实机及 standalone 发布包。默认跳过的 11 项 opt-in 测试没有记为通过。

机器可读摘要见 [evidence.json](evidence.json)。原始测试及构建日志保留在集成 worktree 的忽略目录 `.local/integration-validation/`。

# 隔离 Desktop 实验

这里保留初始研究使用的脚本，不是正式监控实现。当前完整说明见 [初始实验文档](/Users/bytedance/personal/codex_progress_monitor/docs/01_initial_experiment/README.md)。

## 文件

| 文件 | 用途 |
| --- | --- |
| `launch_isolated_desktop.py` | 创建新 Electron profile、CODEX_HOME 与 localhost app-server |
| `stream_probe.py` | 在最新隔离环境中启动专用模型实验，记录两个客户端的事件 |
| `configure_tool_mode.py` | 历史工具模式对照实验；修改隔离配置并重启后端 |
| `configure_transport_logging.py` | 隔离后端的接收日志实验及模式恢复 |
| `stop_experiment.py` | 停止报告中记录的隔离进程，保留数据 |
| `STREAM-EXPERIMENT.txt` | 各次实验的原始说明和证据入口 |

配置和停止脚本会改变运行中的实验环境，stream_probe 会启动真实模型请求。不要把这些脚本当作只读进度查询接口。

## 本机材料

`runs/` 保存每次运行的数据。`latest-run.txt` 指向最近一次运行；两者均被忽略，不应提交。源码和协议 schema 已集中到 `.local/research/desktop-isolation/`，旧 `source` / `protocol-schema` 入口保留为兼容软链接。

`20261010-145714-1f2115` 和 `20261010-145742-4b6bb1` 的旧进程已停止，本次整理删除了其运行 profile / CODEX_HOME；报告与后端日志仍保留，不能直接将这些旧目录当作可恢复的登录环境。

`20261010-150337-2b232b` 的隔离 App 与后端在整理时仍运行，配置、登录状态、会话、数据库和依赖原位保留。

上游分析脚本支持读取原始 `.log` 或无损归档的 `.log.gz`。历史报告中的原始路径字符串保持当时含义；实际压缩文件的位置与校验值见 [整理清单](/Users/bytedance/personal/codex_progress_monitor/analysis/workspace-cleanup/2026-10-10.json)。

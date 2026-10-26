# 工作区整理记录

整理日期：2026-10-10。整理时工作区包含可行性实验、研究证据和隔离 App 的本机运行环境，尚未初始化 Git，也尚未实现监控产品。

本次释放约 **212 MiB**，文件内容总量从约 **1.19 GiB** 降至 **0.98 GiB**。这些数值按文件逻辑大小统计，不等同于文件系统实际分配空间；运行中的环境会继续产生数据。逐项路径、大小和压缩前校验值见 [清理清单](/Users/bytedance/personal/codex-activity-monitor/analysis/workspace-cleanup/2026-10-10.json)。

## 目录归属

```text
codex_progress_monitor/
├── README.md                         项目入口与目录约定
├── .gitignore                        排除本机状态和生成材料
├── dev-history/01_initial_experiment/       架构、数据来源、需求和整理说明
├── analysis/                        分析脚本、研究结论和精简验证结果
│   └── workspace-cleanup/            本次清理清单
├── experiments/desktop-isolation/    可复用的隔离实验脚本
│   └── runs/                        本机运行环境与原始证据，不提交
└── .local/research/                  提取源码与生成协议快照，不提交
```

分析脚本和实验脚本保留原位置，避免影响它们的路径计算、模块导入及进程识别。未来确定实现方案后，产品代码和测试可分别放在 `src/`、`tests/`。

## 移动的生成材料

以下目录已集中到 `.local/research/`，共约 **197 MiB**。原位置保留相对软链接，使现有文档中的源码行号定位和分析脚本继续可用。

| 原位置 | 新位置 |
| --- | --- |
| `analysis/file-edit-progress/26.1002.52244` | `.local/research/file-edit-progress/26.1002.52244` |
| `analysis/file-edit-progress/26.1007.21159` | `.local/research/file-edit-progress/26.1007.21159` |
| `analysis/upstream-stream/desktop-source` | `.local/research/upstream-stream/desktop-source` |
| `experiments/desktop-isolation/source` | `.local/research/desktop-isolation/source` |
| `experiments/desktop-isolation/protocol-schema` | `.local/research/desktop-isolation/protocol-schema` |

源码快照仍用于复查不同版本行为，因此本次只移动，没有删除。新位置和兼容软链接都已列入忽略规则；其他开发机器需要按目标安装版本重新准备这些材料。

## 已删除与压缩

删除了两个已停止运行的旧环境中的 `codex-home/` 和 `user-data/`：

- `runs/20261010-145714-1f2115/`
- `runs/20261010-145742-4b6bb1/`

清理前检查了对应进程和本地数据：这些旧环境没有登录文件 `auth.json`、会话 JSONL 或历史文件。其 `report.json` 与后端日志保留，用于追溯启动与停止结果。

同时删除了可重新生成的 Python `__pycache__/` 和已空的协议生成临时目录 `protocol-home/`。

在保留的运行目录 `runs/20261010-150337-2b232b/` 中，四份已关闭的历史日志转换为 `.log.gz`：

- `backend.log`
- `backend-tool-mode-20261010-170016.log`
- `backend-tool-mode-20261010-170131.log`
- `backend-transport-20261010-171554.log`

压缩后逐一解压计算 SHA-256，与原文件一致，才移除原始文件。其中最大的上游日志从约 **46.26 MiB** 压缩至 **7.38 MiB**。分析脚本已支持直接读取 gzip；历史文字记录中的 `.log` 路径对应现在的 `.log.gz` 文件。

## 保留的运行环境

整理时隔离 Desktop（PID `70084`）与其后端（PID `6498`）仍在运行。本次保留 `runs/20261010-150337-2b232b/` 中正在使用的配置、登录状态、数据库、会话、插件、浏览器运行数据及当前日志 `backend-transport-20261010-171821.log`，没有停止或重启实验进程。

该运行环境仍占约 **800 MiB**，是剩余体积的主要来源。进一步清理应在停止相应实验进程后，先保留独有证据，再判断下载缓存和临时运行文件是否可以移除。

`runs/`、`latest-run.txt`、`baseline-processes.json` 和本机生成材料已通过 `.gitignore` 排除。以后初始化 Git 时，登录状态、原始对话、数据库和下载依赖不应随项目提交。可复用脚本的用途见 [隔离实验说明](/Users/bytedance/personal/codex-activity-monitor/experiments/desktop-isolation/README.md)。

## 验证

对压缩后的上游日志执行了实际离线重放：分析结果与整理前一致，仅日志路径更新为 `.log.gz`；提取的工具参数增量文件内容校验值一致。七份 Python 脚本通过语法解析检查。

整理后检查项目入口、初始实验文档与隔离实验说明中的本地 Markdown 链接，确认目标文件和源码引用位置可访问。

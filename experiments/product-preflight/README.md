# 产品实现前验证脚本

结果见 [验证报告](/Users/bytedance/personal/codex_progress_monitor/analysis/product-preflight/README.md)。这是验证原型，不是常驻产品。

## 环境与运行

```sh
python3 -m venv .local/venv
.local/venv/bin/python -m pip install -e .
```

与产品共用 `.local/venv`；已经完成产品安装时无需重复安装。实验目前只依赖产品已包含的 tiktoken，不再单独创建旧实验 venv。原始数据仍写入 `.local/product-preflight/`。

所有实时实验使用 `experiments/desktop-isolation/latest-run.txt` 指向的已登录隔离 CODEX_HOME，不复制主账户凭据、不更改模型工具模式。运行目录必须是新的目录，脚本拒绝覆盖旧实验。

下列实时命令会创建真实实验聊天并产生模型用量。需要允许 Codex 子进程启动其自身沙箱；嵌套在受限父沙箱中可能只完成生成阶段而无法执行工具。

```sh
.local/venv/bin/python experiments/product-preflight/run_live.py \
  --run-dir .local/product-preflight/new-parallel-run

.local/venv/bin/python experiments/product-preflight/run_live.py \
  --mode tools --run-dir .local/product-preflight/new-tool-run

.local/venv/bin/python experiments/product-preflight/approval_probe.py \
  --run-dir .local/product-preflight/new-approval-run \
  --output analysis/product-preflight/approval.json
```

Desktop 回归脚本只启动和停止自己创建的独立 profile，不启动模型回合。它需要 macOS 应用启动与进程身份查询权限：

```sh
.local/venv/bin/python experiments/product-preflight/desktop_regression.py \
  --run-dir .local/product-preflight/new-desktop-run \
  --output analysis/product-preflight/desktop.json
```

离线计数与故障注入不发起模型请求：

```sh
.local/venv/bin/python experiments/product-preflight/tokenizer_probe.py \
  --output analysis/product-preflight/tokenizer.json

.local/venv/bin/python experiments/product-preflight/fault_probe.py \
  --run-dir .local/product-preflight/new-fault-run \
  --capture .local/product-preflight/live-20261011/capture.jsonl \
  --output analysis/product-preflight/faults.json
```

`export_results.py` 复查本次固定证据路径的 12 个内容流，并输出不含文本的报告。它不是自动寻找最近一次运行的通用命令。

## 模块

| 文件 | 职责 |
| --- | --- |
| `forwarder.py` | 原样转发 stdin / stdout / stderr；有界旁路队列、过滤后的 capture 与故障统计 |
| `telemetry.py` | 用明确的 turn / item 字段关联增量；跨来源单一计数、去重与未知项隔离 |
| `run_live.py` | 两条真实并行回合、短工具回归与取消 |
| `desktop_regression.py` | 独立 Desktop 的包装器启动 / 初始化回归 |
| `approval_probe.py` | 一条真实审批请求及拒绝后的状态 |
| `tokenizer_probe.py` | 旧 trace / Unicode / 代码样本的分块计数与快照开销 |
| `fault_probe.py` | 合成 CLI 的传输故障与真实 capture 重放 |
| `export_results.py` | 校验实时流与上游完成项、隔离 sessions，并输出计数和哈希 |
| `reasoning_timing_probe.py` | 绕过产品 bridge，对比 reasoning 事件的原始接收时钟、CLI 日志时钟与响应级时间字段；[结果](../../analysis/reasoning-timing/README.md) |

捕获文件仅用于有限诊断。完整上游事件包含正文和工具参数，不应该作为产品默认的长期持久化格式。包装器本身故障仍可能影响承载中的任务；旁路丢包或记录失败必须传递为观测缺口。

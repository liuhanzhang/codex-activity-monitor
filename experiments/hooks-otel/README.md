# Hooks / OTLP 隔离探针

功能对照见[功能缺口](../../dev-history/28_hooks_and_otel/capability-gaps.md)，实验过程与数据见[调研与实验记录](../../dev-history/28_hooks_and_otel/research-and-experiments.md)。`probe.py` 使用 Python 标准库，在 `/private/tmp/codex-hooks-otel-*` 创建权限受限的独立 Codex home、HTTP 接收器及实验文件，不接管日常 Monitor 或 Desktop。

从仓库根目录运行：

```sh
# 模拟模型，无真实推理请求：SSE、Hook、固定地址接收器重启
python3 experiments/hooks-otel/probe.py --delay-ms 100

# Hooks 未信任时应不执行回调
python3 experiments/hooks-otel/probe.py --delay-ms 100 --no-trust

# 首次启用 OTel、同线程 hooks 热刷新、新线程与新进程比较
python3 experiments/hooks-otel/probe.py --delay-ms 100 --reload

# OTLP HTTP 503 与回合中断
python3 experiments/hooks-otel/probe.py --delay-ms 100 --fault --interrupt

# 真实请求：英文、中文、一次固定 printf 工具调用
python3 experiments/hooks-otel/probe.py --real
python3 experiments/hooks-otel/probe.py --real --delay-ms 100

# 单个真实 SSE 请求；仅改变隔离 home 的自定义 provider
python3 experiments/hooks-otel/probe.py --real --single --sse --delay-ms 100

# 单个真实 WS 请求，同时检查三类 OTel 信号
python3 experiments/hooks-otel/probe.py --real --single --traces --metrics --delay-ms 100
```

安装路径和模型固定为本机 Desktop 组件及 `gpt-6.1-sol`。`--real` 会使用现有 `~/.codex/auth.json` 登录发起有成本的真实请求，复制到私有实验 home，结束时删除副本；不打印凭据。配置前后会比较原配置 SHA-256。真实及模拟命令工具均保留 Codex 的 read-only 沙箱；嵌套沙箱受限时可能得到 sandbox_apply 错误，应与工具成功执行区分。

默认通过 thread/start 的会话级 `bypass_hook_trust` 运行本脚本创建且内容已知的回调，仅用于隔离实验。生产接入应使用正常的 hooks 信任流程。`--no-trust` 用于对照。

输出目录中的 `summary.json`、`otel.json`、`spans.json`、`metrics.json`、`batches.json`、`hooks.jsonl`、协议和原始流记录用于交叉核对。原始文件可能含账号元数据、文本和工具输入输出，不应直接提交；公共附件只摘取统计及字段名。`turns-raw.json` 的传输 TRACE 和协议是实验对照来源，不能误当作 OTLP 导出数据。

脚本用于有限观测，不是生产 collector、长期性能基准或全部工具路径的验收套件。`--reload` 只针对隔离实例调用配置 API，不能据此推断 Monitor 已有权限操作日常 Desktop 的后端。

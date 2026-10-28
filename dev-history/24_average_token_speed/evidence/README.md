# 平均速度实验数值证据

日期：2026-10-28。这里保留真实 JSONL 实验的脱敏派生数据，不保存原始 JSONL、聊天正文、工具参数、实际会话或响应身份、本机绝对路径。

## 文件

| 文件 | 内容 |
| --- | --- |
| [measurements.json](measurements.json) | 固定代码版本、匿名源前缀字节数与 SHA256、285 次逐条结果、19 轮汇总和核对诊断 |
| [updates.csv](updates.csv) | 便于直接比较的 285 次更新；时间均为轮内相对毫秒 |
| [turns.csv](turns.csv) | 19 轮的最终值、范围、变异系数和两种方法的稳定性判定 |
| [summary.json](summary.json) | 按至少 2、3、5、10 次 usage 分组的对比结果 |
| [usage-intervals.json](usage-intervals.json) | 同轮相邻 usage 的 266 个间隔及统计口径 |
| [verify_results.py](verify_results.py) | 从派生记录重新核对累计公式、稳定性分组和 usage 间隔 |
| [verify_product_replay.py](verify_product_replay.py) | 持有原始前缀时，逐条对照产品解析器的 usage 预览与后续确认 |
| [live-query-check.json](live-query-check.json) | 增长中的真实 JSONL 经隔离查询/CLI 的 12 次累计值、独立参照核对和两次原生读数；不含实际身份、路径或正文 |

数值图在上一级 [assets](../assets/) 中，分别展示两种方法和请求时间窗法的四个相同轮次。它们只包含匿名编号、数值和单位。

## 身份与时间变换

A、B、C 只标识三个匿名文件，`turn` 或 `turn_index` 是该文件内的轮次顺序，`usage` 是同轮更新顺序。它们不是实际 session_id、turn_id 或 response_id。

每轮时间原点是方法二首条样本的推导请求起点。`usage_offset_ms`、`request_start_offset_ms` 都减去了这个原点；持续时间、同轮相邻间隔、累计 token 和速度保持不变。源记录行号用于在持有原始日志的本机进行定位，其他读者无需原始文件也能验证本章数值汇总。

`reasoning_tokens` 是 `output_tokens` 的子集，不能额外加到输出总数。`item_duration_ms` 是本次 usage 前新增的模型 item 时长总和，可以为零；它不是完整请求时长。`request_duration_ms` 是方法二的单次窗口；`count_confirmation_delay_ms` 是 usage 到对应 token_count 的时间差，不应再加到已使用 usage 终点的请求时长中。

文件前缀哈希只用于确定两次实验使用同一批源字节。哈希不能证明计时语义准确，也不提供原日志内容。

## 数值复核

在仓库根目录运行：

```sh
python3 dev-history/24_average_token_speed/evidence/verify_results.py
```

此命令使用标准库，只读证据文件，不调用模型、不连接 collector、不读取日常 Codex home。它复核的是派生数据的内部一致性，不是重新运行 Codex、Tokscale 或 Token Monitor，也不替代对源文件解析器和原生界面的验证。

重新执行原始计时重放需要本机保留的私有 JSONL 前缀及对应版本算法。归档记录了算法来源和摘要，未将这些私人文件打包进仓库。方法二的原型是固定版本 Rust 状态机的 Python 复现，并不是原生二进制测量。

产品实现后增加了独立复核入口：`verify_product_replay.py --source A=PATH_A --source B=PATH_B --source C=PATH_C`。它先校验各源的固定前缀哈希，再用当前工作树的 `request_rate.py` 对照全部 285 个更新点的成对累计量和速度，最后核对确认已结算。2026-10-28 实测全部匹配。路径由本机调用者提供，不写入证据；脚本不输出原始记录或实际身份。

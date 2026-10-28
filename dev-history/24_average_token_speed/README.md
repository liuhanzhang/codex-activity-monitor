# 每轮对话平均 Token 速度

日期：2026-10-28，Asia/Shanghai。代码基线：`6c39325618c4c39ed0edd226e00f151caeea4e2c`。

状态：产品功能已实现，定向测试、285 点产品回放、隔离预览及真实 JSONL 接入核对通过；日常实例已按用户指令更新，并确认均速随 usage 刷新。实际实现见[实施结果](implementation.md)，日常更新见[部署记录](deployment.md)。

目标是在详情底部统计区增加所选轮次的平均速度：左侧保留输出 Token 数和推理用量，右侧显示平均速度。正在进行的轮次每收到一条可配对的 usage 就更新，不等待整轮结束。

采用的计量方向是：累计有有效请求计时的官方输出 tokens，除以这些请求的累计耗时。请求开始边界从 JSONL 事件推导，终点优先使用对应 `token_usage_record` 的时间。该指标是请求级估算，可能包含首 token 等待、排队和网络时间。

## 阅读入口

| 目的 | 文档 |
| --- | --- |
| 理解两种分母及真实实验结果 | [算法对比与实验](experiments.md) |
| 查看 Token Monitor 的平均速度和当前速度算法 | [Token Monitor 源码参照](token-monitor-reference.md) |
| 查看已确认的产品需求和界面位置 | [需求与决策](requirements.md) |
| 查看数据所有者、请求账本、接口和异常处理 | [技术方案](technical-design.md) |
| 查看实施顺序及验收条件 | [实现计划](implementation-plan.md) |
| 查看已实现行为、验证与剩余边界 | [实施结果](implementation.md) |
| 查看日常 App 更新与验收 | [部署记录](deployment.md) |
| 查看统计说明精简与截断修复 | [文案修订](statistics-copy.md) |
| 检查全部逐条结果和统计口径 | [证据说明](evidence/README.md) |

## 已完成的证据

同一批 3 份 JSONL 的固定文件前缀包含 19 轮、285 条 usage。第一种方法累加模型 item 的存续时间；第二种方法复现 Token Monitor 所固定 Tokscale 版本的请求计时状态机，并在每条 usage 时做只使用当前记录的预览，之后再用 token_count 核对。

第二种方法的 285 次预览均与后续确认结果一致。16 个有多次 usage 的轮次中，全程处于该轮最终值 ±20% 内的，从第一种的 1 个增加到第二种的 7 个；至少 5 次 usage 的 7 轮中，后半段全部满足这一参考范围的，从 2 个增加到 6 个。这个范围用于事后比较稳定性，不是准确性认证或产品过滤阈值。

同轮相邻 usage 的 266 个间隔平均为 18.298 秒，中位数为 10.676 秒。它描述这批样本的 JSONL 记录间隔，不是固定推送周期，也不是请求耗时的替代值。

## 交付边界

本章包含文档、脱敏数值结果和数值图，不包含原始聊天正文、实际会话或响应身份、本机绝对路径、登录信息和原始 JSONL 副本。证据中的 A、B、C 是匿名样本编号。

早期实验是固定版本算法的 Python 复现，没有启动 Token Monitor App。产品采用独立账本接入历史增量扫描，未将实验脚本挂到服务中。真实请求的开始时间和服务端纯解码耗时仍没有直接观测值；旧格式不猜测计时，长时间运行和日常接入仍需分别验收。

相关背景：[当前架构](../../docs/architecture.md)、[历史 JSONL 校正](../14_jsonl_activity_reconciliation/README.md)、[一秒实时速度](../18_engineering_hardening/one-second-speed.md)。

# 日常 Monitor 更新记录

日期：2026-10-28，Asia/Shanghai。用户明确要求更新本机正在运行的 Monitor。本次更新完成；部署记录随后与平均速度实现一并本地提交，未推送。

## 更新范围

日常 App 位于主 checkout 的 `.local/gui/Codex Activity Monitor.app`，与开发工作树同为代码基线 `6c39325618c4c39ed0edd226e00f151caeea4e2c`。更新前主 checkout 干净；按明确清单同步了本功能的 33 个源码、测试、文档与脱敏证据文件，没有合并其他工作树的开发内容。

旧 App、被替换源码、偏好和集成记录先备份到开发工作树的 `.local/diagnostics/average-speed-deployment/`。备份含本机运行信息，仅留在被忽略的本地目录。

在日常目录运行 `bin/build-gui`，完成编译与签名校验；通过菜单正常退出旧 App，确认旧 GUI 和 collector 均已退出，再打开同一路径的新 App。没有重启 Codex Desktop，也没有中断或重建其现有 bridge。

为便于验证实际加载版本，本次同时将新模块 `request_rate` 纳入 collector 的启动源码指纹。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| GUI PID | `42504` → `61433` |
| collector PID | `42524` → `61457` |
| GUI 构建标识 | `e10a67e9a50c05f0031ca8bf6df183589918ca7a2e0d3f1d0f68cb243d509dfd`，与当前 Swift 源码及资源一致 |
| 后端源码 | collector、history、history_index、timeline_query、request_rate 的运行时指纹均匹配日常目录源码 |
| 状态通道 | healthy；snapshot 的 running 为 true，写入失败为 0 |
| 原配置与数据 | 集成身份、原环境备份、安装值、runtime、源码/App 路径保持一致；偏好内容不变，时间线数据库文件身份保留 |
| 现有 Codex bridge | 更新前的 4 个 bridge PID 均保留 |
| 定向测试 | 主目录的 25 项请求计量测试通过；工作树的 4 项运行/打包执行测试通过 |
| 日常平均值查询 | 一个实测点为 21 笔样本、13,917 输出 tokens、386,170 ms，均速约 36.04 T/s；与真实 JSONL 的独立计时回放一致 |
| 原生界面 | 详情底部出现平均速度，后续 usage 到来后由 `35.0 T/s` 更新为 `35.8 T/s`；通过正常 SystemTimelineClient → CLI → 日常查询 socket 读取，没有 fixture adapter |

以上不同数值取自不同查询/界面时刻，不能当作同一笔样本的读数差异。

本轮正在进行时重启了 collector，因此界面按既有规则提示连续观测缺口，左侧官方用量暂为 `—`。JSONL 平均值仍可读取和更新；没有为了消除提示而改写观测覆盖。这次验证不替代下一完整新轮次的实时观测恢复、长期驻留或发行验收。

日常 App 已保持运行，用户可以直接查看详情底部右侧的平均速度。

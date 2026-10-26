# 日常切换与自然验收清单

这是 P3／P5 剩余运行门槛的操作清单；当前完成状态仍以[入口](README.md)和[实施记录](implementation-results.md)为准。维护者已授权更新 GUI／collector 并完整重开 Desktop；实际 GUI／collector 与新版驻留 bridge 的版本、身份、连接核对均通过，见[切换证据](evidence/p3-daily-cutover.json)及[重开记录](evidence/p3-desktop-restart.json)。当前进入自然完整回合／长期验收；采集器重载不能更新驻留 bridge。

## 已准备的材料

私有目录 `.local/rollout-preparation/` 的 `latest-preparation.json` 指向本次准备目录；它不是产品配置，也不参与自动启动。目录均由 Git 忽略。

| 材料 | 用途与边界 |
| --- | --- |
| `manifest.json` | 目标 GUI 指纹、collector／bridge 源码摘要、协议版本、构建和回退材料摘要；已追加本次切换及剩余验收状态 |
| `candidate/Codex Monitor.app` | 当前 Preview 的同一二进制与资源，换成生产 bundle 标识／runtime 后重新签名；其副本已安装到日常路径并核对实际运行 |
| `previous/Codex Monitor.app` | 日常包的原样副本，用于核对原始产物 |
| `baseline-source.tar`、`previous-source/` | 固定 `c3996dc` 基线；还原旧 GUI 指纹只需在 `ActivityContent.swift` 末尾多保留一个换行，差异已经逐文件指纹验证 |
| `rollback/Codex Monitor.app` | 保留旧二进制，把 Python 源码指向上述独立基线，再签名；未执行回退，不声称重建驻留 collector 的内存镜像 |
| `current-worktree.tar`、`tracked-changes.patch` | 准备时的工作区／已跟踪差异副本；没有修改 Git 暂存范围，不替代正式提交 |
| `recovery/` | 私有恢复上下文和只读 SQLite backup，数据库 `quick_check` 通过；不是权威历史，不恢复过期租约 |
| `pre-cutover-inventory.json`、`target-backend.json` | 真实进程存在性／身份与目标磁盘版本分别记录；磁盘版本不能单独证明驻留进程已加载它 |
| `active-cutover.json` 指向的目录 | 本次新鲜恢复副本、原样移出的旧包、安装记录和 GUI／collector 实际运行诊断；不包含新的历史权威源 |

恢复副本带有采样时间，切换发生前再保存最新上下文。Codex JSONL 不复制为新的权威数据库，也不进行格式迁移。

## 切换顺序

1. 确认维护者方便重开 Desktop；先结束其他需要保留连续性的任务。候选包的 Swift 指纹、collector／bridge 源码摘要须仍与工作区一致；不一致就重新构建／验证相关改动。
2. 保存最新私有恢复上下文，通过 Monitor 正常退出释放观察。确认所属 collector 结束、生命周期锁释放；不终止 Codex Desktop 或其原版后端，不把旧 PID 文件当作进程身份。
3. 安装候选 GUI，严格验证签名和实际配置，然后启动新 GUI。由正常管理流程配置／恢复采集；旧协议不兼容才进行有限替换，不因连接超时重复启动 collector。
4. 核对新 collector 的状态协议 1、租约协议 2、实例／源码摘要；新 GUI 的实际诊断 build ID 应与候选一致。用新的 nonce 确认 `state.sock` 健康及序号前进，核对拥有连接。10 s 检查点不是实时验收依据。
5. 向维护者确认 GUI／collector 更新完成，再由维护者完整退出并重开 Codex Desktop。旧 bridge 尚在时保留 `bridge_restart_required`；不能拿新 GUI 的连接健康代替源 bridge 更新成功。
6. 新 bridge 以观察协议 1 重新登记，真实进程身份、源码摘要与许可吻合；管理接口不再要求重开。断流／重载产生的 gap 保留。只有新起的完整回合进入后续自然样本。

## 自然样本如何判断

10 月 23 日的私有只读状态／资源抽样已按 30 分钟期限结束，10 月 25 日复核了结果，见[自然窗口复核](evidence/p5-natural-window-review.json)。1,793 次新 nonce 探测均健康，期间一次超长诊断行丢弃／观察缺口与 bridge 约 541 MiB 的采样 RSS 峰值仍待处理；不将该窗口记为通过。脚本、状态和输出由私有准备目录的 `active-cutover.json` 定位，未自动续采或重启产品。抽样不续期观察拥有权；首个回合已在进行中，不能计为完整窗口。抽样没有源完成／最后 delta 的接收时间，不能单独推算完整链路延迟，也不能把约 1 s 的抽样间隔当作菜单栏更新耗时。GUI 的原有有界 draw 诊断也已到期；后续需要补齐有界、可对齐的源事件证据，缺失阶段保留未知。

- 从开始前已接入的真实回合观察到结束，不中途重载／重启探针来清除 gap。分别记录正文最后 delta、明确完成、状态实例／序号、GUI 应用与 AppKit draw；源证据缺失的阶段保留未知。已有受控进程证据不当作自然采集结果。
- 覆盖短／长回复、工具和真实子智能体，主回合结束后遇到明确 `ephemeral=true` 的任务时检查聚合。无临时标记不按名称／时间排除。逐步累积至少 10 个完整回合，不为数量额外调用模型。
- 同时检查原始参数／结果、分页来源变化、独立查看器、选区／滚动和语言切换。只回归与新行为或异常相关的场景，沿用仍有效的 P4／P6 原生证据。
- 记录自然窗口的实际时间、聊天／流规模、RSS 与队列趋势，以及出现的异常样本；300 s 合成负载加 120 s 空闲不能替代自然长期记录。日志只留在 `.local`，公开汇总使用合成配置或脱敏统计。
- 若某层取不到足够证据，就补对应有界诊断，不能仅凭 GUI 看起来正常宣告完整链路通过。诊断到期后检查同一进程／handle，不因观察窗口结束自动重启服务。

## 需要回退时

先正常停止新 GUI／所属 collector，保存失败证据。使用固定基线的 GUI、Python、bridge 一致版本，重新配置入口并完整重开 Desktop；不重置当前 Git 工作区，也不覆盖维护者后续环境修改。恢复文件只补显示上下文，旧 session secret 不成为新的有效租约。重新核对实例／身份和真实断流原因；回退不删除 Codex JSONL，也不把恢复文件的新鲜度当作 live 健康。

以上回退包和来源已准备并检查，实际运行回退尚未执行；只有需要回退且验证过实际链路时才追加相应通过记录。

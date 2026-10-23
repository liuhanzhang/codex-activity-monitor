# 验证记录

日期：2026-10-22 至 2026-10-23，Asia/Shanghai。基线 `6314547`。本文及 `verification.json` 记录系统审查完成时的验证快照，当时尚未提交／推送。后续修复、日常重载及验证见[状态延迟记录](status-latency.md)与[回归调查](status-regression.md)；当前本地基线提交见 Git 历史。

## 环境与结果

本机为 macOS 27.0（26A428）、arm64，Python 3.14.7，Apple Swift 6.4。产品最低目标仍是 macOS 26、Python 3.11；**本机验证不能替代这两个最低版本的干净环境验收**。新增 CI 以 macOS 26 / Python 3.11 运行，尚未推送或执行远端作业。

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 基线 Python | 307 通过 | 现有完整 unittest discovery |
| 基线 Swift | 74 通过 | 默认 suite；窗口测试未启用 |
| 最终 Python | 327 通过，50.863s | 完整 discovery，包含 20 项新增回归；已有测试也扩展了来源替换及 allowlist 反例 |
| 最终 Swift | 78 通过 | 4 项新增内容契约测试；更新了旧测试 fixture，使其满足真实身份和分页契约 |
| 本机原生窗口 | 6 通过 | 显式启用：355pt 紧凑视口、分页动画／打断／取消、窗口复用、语言切换保留阅读状态、对话框与空／错误态 |
| 预览 App | 构建及 ad-hoc 签名成功 | 只构建 preview；源码指纹与 bundle 配置一致 |
| 本地化 | `--check` 通过 | Python catalog、Swift 资源、生成示例一致 |
| 脚本 | shell 语法检查通过 | `build-gui`、`run-gui`、`codex-progress` |
| CI | YAML 语法通过 | Ruby Psych 解析；未声称 GitHub 执行成功 |
| 仓库 | whitespace 检查通过 | 新增文档相对链接及 preview 指纹另行检查 |

完整 Python 验证之后仅调整导入次序／模块文档；历史校正 40 项再次通过。最终 Swift／原生测试及构建包含旧分组模式删除、共享内容契约和图例组件拆分。

## 可重现命令

在仓库根目录，先按 README 创建 `.local/venv` 并安装项目。

```sh
.local/venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.local/venv/bin/python bin/export-localizations --check
swift test --disable-sandbox --cache-path .local/swift-cache --package-path macos
bin/build-gui --preview
sh -n bin/build-gui bin/run-gui bin/codex-progress
git diff --check
```

需要可用的本机窗口会话时，单独运行原生检查：

```sh
MONITOR_WINDOW_TESTS=1 swift test --disable-sandbox \
  --cache-path .local/swift-cache --package-path macos \
  --filter 'TimelineNativeViewportTests|localizationNative|viewerReusesWindow'
```

首次在受限进程内执行窗口测试时，显示器枚举返回空，且一个旧语言 fixture 缺少 phase 身份。随后修正 fixture 和 screen fallback，并在具有 WindowServer 访问的本机会话中重跑，6 项全部通过。没有通过跳过断言、延长 sleep 或修改真实聊天来取得通过结果。受限运行的失败不被计为原生验收通过。

## 关键行为与反例

### 身份与内容

- retired 模型项位于可见文件编辑之前时，文件、文案和活动 ID 仍对应同一记录。
- 返回其他 scope、thread、turn、phase 或 unsupported version 的详情被拒绝。
- 无身份的 unavailable 回复仅可携带空正文和空附件；不能借不可用状态展示未归属内容。
- 分页核对 section、offset 和前进游标；保留原始换行及特殊字符，不使用 Swift 字符计数重建 Python 偏移。
- 切换选择后，旧请求的成功、失败与 defer 均不能覆盖新选择状态。
- 来源在分页时消失，保留已读内容但状态更新为 missing 等明确信息。

### 历史事实与恢复

- JSONL 完成事实撤换、同 ID 源时间更新、查询先于后台 refresh、活动与轮次独立终止。
- 原始实时完成仍有效时，撤掉历史覆盖不会撤掉这份独立证据。
- 同一 persistent thread 对应多个 backend 时，source generation 对所有 backend 一致生效。
- stopped sidecar 可以保留并使用 observation basis；旧 sidecar 没有 basis 时，不补造被覆盖的起止时间。
- 新增 observation 字段仍通过元数据 allowlist，嵌套正文／结果及递归 observation 不落盘。
- unknown 恢复保留 ephemeral／可见性／明确父子来源；旧实时速度仍不恢复。
- 缺失历史缓存有界，无回复的等待映射随 phase 过期。
- history-only 活动的已知源耗时可用；缺起点仍为未知。

### 故障与存活

- 临时独立 collector 的 snapshot 写入被注入 ENOSPC，写入路径持续失败时仍能接受新 turn、响应 timeline query。
- 新鲜快照不存在但生命周期锁仍持有时，`start()` 不再启动竞争 collector。
- 解除故障后重新发布，快照带累计失败计数和最后 errno；原始模型内容不写入故障诊断。
- 未来 60 秒、非数值／非有限时间及边界窗口检查；Python 与 Swift 的有效窗口为 `-5 <= age < 3`。

## 构建和审查产物

- 预览：`.local/gui-preview/Codex Monitor Preview.app`，与日常 `.local/gui/` 分离。
- 最终 Swift source build ID：`5d648b227bda4f0d4d068208b0151633784089568f54aea992fe755ef5379ad6`。
- 机器可读摘要见 [verification.json](verification.json)，只保存检查结论和构建身份，不包含会话快照、工具正文或原始运行数据。
- 终端原始检查输出保留于本机 `/tmp/codex-monitor-review-*.log`，不纳入 Git；这些路径是临时验证材料，不能作为公共 checkout 的依赖。

## 外部依据与边界

CI runner 标签核对了 GitHub 的 [runner-images 清单](https://github.com/actions/runner-images)及 [macOS 26 发布说明](https://github.blog/changelog/2026-02-26-macos-26-is-now-generally-available-for-github-hosted-runners/)。Action commit 通过官方仓库的 `refs/tags/v6` 核对：checkout `d23441a48e516b6c34aea4fa41551a30e30af803`，setup-python `ece7cb06caefa5fff74198d8649806c4678c61a1`。这只能支持配置选择，不能证明当前仓库的 CI 已经运行。

没有重新执行真实登录／恢复、日常 collector 重载或自然 Codex 任务；当前日常进程不会因源代码已改就被视为已经加载本轮实现。没有开展长时间压力、Remote、全新机器、签名公证或完整 VoiceOver 验收。全部未完成项见 [review.md](review.md#发行前仍需完成)。

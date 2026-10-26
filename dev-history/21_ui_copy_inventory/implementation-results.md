# 活动文案调整：实施结果

日期：2026-10-26。清单与实施前基线：`6b7825a`。状态：源码修改、资源导出、定向检查、预览包构建及日常 App 重载完成；未执行完整原生窗口或自然回合验收。

## 最终文案

| 活动 | 聊天列表 | 时间线图例 | 详情类型 |
| --- | --- | --- | --- |
| 思考 | Reasoning／思考中 | Reasoning／思考 | Reasoning／思考 |
| 回复 | Responding／回复中 | Response／回复 | Response／回复 |
| 工具参数生成 | Preparing／准备中，可附加工具名 | Tools／工具 | Tool params／工具参数 |
| 等审批／等用户输入 | Pending／等待中 | 沿用原图例规则 | 沿用原活动状态规则 |
| 文件编辑 | Editing · 文件名／编辑中 · 文件名 | Edits／改动 | File edits／文件改动 |
| 压缩 | Compacting／压缩中 | Compaction／压缩 | Compaction／压缩 |

列表 Preparing 与已有 target 拼接规则保持一致。协作参数生成标题中的 Tool input 前缀同步改为 Tool params。内容页签 Input、Result、Visible reasoning summary 保留原名。

## 实现边界

- Python 中新增 activity.pending，approval / input 活动及 waitingOnApproval / waitingOnUserInput 标志使用它。实际目标暂停、阻塞或用量／预算受限继续使用 activity.paused，不改变活动生命周期和优先级。
- App 与 Model 的中英文资源一起更新；Python 共享词库经 bin/export-localizations 导出到 Swift CoreMessages 和本地化示例。
- 已知模型活动使用当前类型文案作为展示名，避免旧记录中的“正文输出”再次出现在“回复”旁。记录自身的 label 和模型正文保持原样；具名工具的来源展示保留。
- 时间线颜色、阶段分类、工具实际执行、计划归入 text 的现有规则保持一致。

## 验证

| 检查 | 结果 |
| --- | --- |
| Python：test_localization、test_progress_semantics、test_file_editing、test_usability | 53 项通过 |
| Swift：swift test --filter localization | 8 项执行通过；2 项需要 MONITOR_WINDOW_TESTS=1 的窗口测试跳过 |
| bin/export-localizations --check | 通过，中英文键、变量模板及导出资源一致 |
| 旧模型标签与具名工具展示 | 本地化回归通过；旧 label 保留，中文显示当前“回复” |
| 等待与暂停区分 | 覆盖用户输入请求、请求结束、两类等待标志及实际目标暂停 |
| git diff --check | 通过 |

Python 命令：

```sh
PYTHONPATH=src:tests .local/venv/bin/python -m unittest test_localization test_progress_semantics test_file_editing test_usability
```

Swift 命令：

```sh
CLANG_MODULE_CACHE_PATH="$PWD/.local/clang-module-cache" swift test --disable-sandbox --cache-path "$PWD/.local/swift-cache" --package-path macos --filter localization
```

## 预览包与加载边界

预览包位于 `.local/gui-preview/Codex Monitor Preview.app`。Swift 编译成功；标准 bin/build-gui --preview 在未改动的图标生成步骤报 Invalid Iconset。本次使用临时构建脚本复用现有日常包内的 AppIcon.icns，完成其余原有预览打包与 ad-hoc 签名流程；没有修改图标源码或构建脚本。

预览包已通过 codesign --verify --deep --strict、源码构建身份校验及打包后中英文资源检查。预览阶段没有重载日常 App；用户随后要求的日常切换记录如下。

## 日常 App 重载

2026-10-26，按用户明确要求，正常退出日常 Monitor，确认旧采集器释放生命周期锁后，更新 `.local/gui/Codex Monitor.app` 并重新打开。构建同样复用现有图标，未修改构建脚本；日常包签名验证通过。

实际 GUI 诊断中的 build_id 与新日常包一致，GUI 和 collector 均为新实例；collector 启动清单中的 37 个模块指纹全部匹配当前源码。两次新 nonce 状态探测健康，序号前进，GUI 已重新拥有活动观测会话。脱敏结果见[日常重载检查](evidence/daily-reload.json)。

未重启 Codex Desktop。登记的 bridge PID 只在切换后检查存活；切换前没有逐一确认，不能据此判断某个 bridge 是否在本次切换期间退出。启动清单不单独记录 messages_en 指纹，因此“37 个匹配”仅指清单覆盖的模块。短时诊断不等同于完整自然回合或原生窗口验收。

## 追加：File edits 与 Compaction

2026-10-26，按用户确认的两组表达，详情类型 Edit input 改为 File edits／文件改动；列表 Editing · 文件名和图例 Edits 保留。压缩列表改为 Compacting／压缩中，图例与详情类型统一 Compaction／压缩。采用句首大写，File edits 的第二个单词小写。

App 与 Model 两份中英文资源同步修改，共享 Python 词库经原有流程重新导出。旧压缩记录使用新的展示名，避免出现“压缩 · 整理上下文”；原始 label、canonical 身份、时间线颜色与耗时不变。

本轮重新执行上述 53 项 Python 检查，全部通过；Swift 本地化检查加压缩身份回归共 9 项执行通过，2 项窗口测试继续跳过。资源导出检查通过。日常包沿用已说明的图标复用方式重新构建，签名验证通过，并正常重载 GUI 与 collector。

新日常包的中英文资源、源码构建身份、实际 GUI 加载身份、37 个 collector 模块指纹及两次持续递增的状态探测均通过；GUI 已恢复观测归属。见[本轮日常重载检查](evidence/file-edits-compaction-reload.json)。此前预览包保留首批构建，本轮更新的是日常 App。完整原生窗口与自然回合验收仍未执行。

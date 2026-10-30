# 验证证据

日期：2026-10-29。状态与解释以[实施结果](../implementation-results.md)为准。

[validation.json](validation.json) 是从本轮测试、隔离联调、独立构建和包验收记录中抽取的可公开摘要。只保留源码散列、构建身份、数值和合成检查结论；没有真实聊天正文、实际会话身份、本机绝对路径或源数据库副本。完整本机日志不进入仓库。

其中 `code_state` 记录构建验收时尚未提交的状态；后续提交可通过模块与验证脚本摘要对应到这份证据。

运行脚本分别位于 [tests/acceptance/verify_rollout_history.py](../../../tests/acceptance/verify_rollout_history.py) 和 [packaging/verify_app.py](../../../packaging/verify_app.py)。前者可通过 `--native-client` 检查原生系统客户端和查看器 store；后者从 DMG 复制后对独立后端施加源码及网络拒绝策略。摘要不是这些脚本的输入，也不会反过来决定测试通过。

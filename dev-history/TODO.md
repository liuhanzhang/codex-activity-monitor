# 待办索引

更新：2026-10-23。代码基线 `c3996dc`。本页只索引当前待办，技术方案、参数和阶段状态统一维护在[第 18 章](18_engineering_hardening/README.md)。历史 CR 与已完成修正见[第 17 章](17_review_and_refactor/README.md)。

## 入口验收

P0 的入口样本与历史交接验证见[实施记录](18_engineering_hardening/implementation-results.md)。菜单栏调查保留在[第 17 章](17_review_and_refactor/status-regression.md)，新版本切换与长期样本按第 18 章继续验收。

## 六项工程工作

- EH01：实时通信与恢复持久化解耦，含租约、bridge 热路径和 GUI／终端读端迁移。
- EH02：统一子进程执行、输出预算、取消、超时和清理。
- EH03：详情分页的稳定内容版本和源变化处理。
- EH04：长期规模测量、资源预算与安全降级。
- EH05：边界类型、状态所有权和职责收敛。
- EH06：测量并消除 GUI 分组投影的重复成本。

状态详见[第 18 章入口](18_engineering_hardening/README.md)，方案见[技术设计](18_engineering_hardening/technical-design.md)、[实施阶段 P0–P7](18_engineering_hardening/implementation-plan.md)和[验证计划](18_engineering_hardening/validation-plan.md)。本页不维护第二份完成勾选表。

## 并行发行准备

许可证／素材范围、兼容承诺、独立发行包、CI、干净环境、Remote、辅助功能及隐私复核见[发行准备与维护者决策](18_engineering_hardening/requirements-and-decisions.md#发行准备与维护者决策)。六项工程验收不自动等于公开发行完成。

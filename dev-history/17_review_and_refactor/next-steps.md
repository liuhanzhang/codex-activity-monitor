# 剩余工作与建议顺序（迁移入口）

更新：2026-10-23。**后续计划已迁移到 [第 18 章：六项工程改进](../18_engineering_hardening/README.md)。本文件不再维护独立的技术方案或执行状态。**

第 17 章保留系统 CR、代码基线 `c3996dc` 的已完成修正和真实验证；第 18 章统一维护待实施设计、阶段计划和验收条件。

## 旧计划到新计划的对应关系

| 旧分组 | 新位置 |
| --- | --- |
| 0：固化基线与历史语义 | [范围与交接规则](../18_engineering_hardening/requirements-and-decisions.md)、实施阶段 P0 |
| 1：子进程可靠性 | EH02，实施阶段 P1 |
| 2：实时通信与持久化解耦 | EH01，实施阶段 P2／P3 |
| 3：分页来源版本 | EH03，实施阶段 P4 |
| 4：长期规模与 GUI 成本 | EH04／EH06，实施阶段 P5／P6 |
| 5：类型与职责 | EH05，随新边界建立，实施阶段 P7 收尾 |
| 并行发行准备 | [发行准备与维护者决策](../18_engineering_hardening/requirements-and-decisions.md#发行准备与维护者决策) |

六项编号固定对应 CR 报告的六项问题，编号不代表执行顺序。具体工作包、提交边界和切换／回退见[实施计划](../18_engineering_hardening/implementation-plan.md)，验收见[验证计划](../18_engineering_hardening/validation-plan.md)。

菜单栏约 2 秒延迟的完整新回合验收仍在[回归调查](status-regression.md)记录，作为 P0 的入口工作。已有局部修正不等于所有用户症状已解决。

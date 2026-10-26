# 技术设计与实验说明

这里记录证据结构、任务接口、实验方法和复现条件。实验结果与适用范围分别说明；模拟流程、开发样例和独立评测不合并统计。产品入口见[项目说明](../../README.md)，运行方式见[复现说明](../16-experiment-reproduction.md)。

| 任务 | 设计与接口 | 数据与验证 |
|---|---|---|
| 财报图像核对 | [字词证据](2026-10-25-word-evidence.md) | [固定图像退化](2026-10-25-visual-robustness.md)、[新公司参考](2026-10-25-transfer-reference.md)、[AMD参考](2026-10-25-amd-reference.md) |
| 三类研究任务 | [验收条件](2026-10-25-task-acceptance-core.md) | [开发案例](2026-10-25-task-acceptance-cases.md)、[模拟业务场景](2026-10-25-research-scenarios.md) |
| 材料与研究底稿 | [材料契约](2026-10-25-research-materials.md)、[提交快照](2026-10-25-research-handoff.md) | [登记适用性](preparation-applicability.md)、[原始文件恢复](2026-10-25-remote-data.md) |
| 现金流变化 | [会计分解](2026-10-25-cash-flow-bridge.md)、[公司解释](company-attribution-core.md) | [官方来源](2026-10-25-cash-flow-sources.md)、[解释核对案例](company-attribution-cases.md) |
| 金额与币种 | [符号规则](2026-10-25-currency-evidence.md) | [币种控制](2026-10-25-currency-controls.md)、[证据完整性](evidence-guard-controls.md) |
| 解释补充底稿 | [证据契约](explanation-evidence-contract.md) | [复核状态与恢复](explanation-review-workflow.md) |
| 跨页财务证据 | [接口与计算条件](cross-page-evidence-contract.md) | [登记材料与控制](cross-page-materials.md) |
| 表格行列取数 | [行级契约](table-lookup-contract.md)、[词级契约](word-table-lookup-contract.md)、[角色识别契约](role-table-lookup-contract.md) | [行级实验](table-lookup-experiment.md)、[词级实验](word-table-lookup-experiment.md)、[角色识别实验](role-table-lookup-experiment.md) |
| 同名行与选择核对 | [行选择契约](row-disambiguation-contract.md)、[导出契约](row-selection-verification-contract.md) | [候选路径实验](row-disambiguation-experiment.md)、[导出核对实验](row-selection-verification-experiment.md) |
| 单位适用性 | [完整表头与例外](unit-applicability-contract.md) | [固定材料与控制](unit-applicability-experiment.md) |
| 参考独立核对 | [观察与复核契约](reference-review-contract.md) | [存储与模拟复演](reference-review-workflow.md) |
| 毛利率变化 | [会计分解契约](margin-bridge-contract.md)、[公司说明契约](margin-claims-contract.md) | [分解实验](margin-bridge-experiment.md)、[说明核对实验](margin-claims-experiment.md) |
| 明确范围查询 | [查询契约](research-query-contract.md) | [开发案例](research-query-cases.md) |
| 写入与故障恢复 | [请求回执](research-write-receipts.md)、[浏览器日志契约](write-journal-contract.md) | [重试复演](research-write-retry.md)、[应用重建复演](research-restart-cases.md) |

当前尚无独立人工确认的完整问答测试集。公司明示解释保留归因主体，流程通过、数值一致与原因得到独立证实分别判断。时序预测、因子与组合的研究前提见[平台设计](../11-financial-research-platform.md)。

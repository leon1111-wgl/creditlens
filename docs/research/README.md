# 独立研究建议

这里保存对 CreditLens 产品与实验路线的独立复核。建议先写明核查的代码版本，再说明证据、取舍、验收条件和不确定性。研究建议不自动改变主分支计划，也不等于功能已经实现。

每轮优先回答一个会影响开发顺序的问题，复用注明日期的已有调研。只有影响决策的新结论才补查原始资料；不以论文数量、功能数量或模型大小代替有效成果。报告中的实验门槛是建议，只有经实际运行才能登记为结果。

| 日期 | 核查版本 | 问题与结论 |
|---|---|---|
| 2026-10-26 | 第39轮刷新恢复 | [第三十份审查](advisory-30.md)：身份、存储失败、恢复与当前版本隔离；[存储契约](write-journal-contract.md)与[应用重建复演](research-restart-cases.md)。 |
| 2026-10-26 | 第38轮明确查询 | [第二十九份审查](advisory-29.md)：范围与证据身份、公司解释限定、51项页面控制；[32项开发案例](research-query-cases.md)。 |
| 2026-10-26 | 第37轮写入恢复 | [第二十八份审查](advisory-28.md)：旧写入冲突复现、当前权限检查、回执与最新状态隔离、62项页面控制；[56项复演](research-write-retry.md)。 |
| 2026-10-26 | 第34轮表格取数 | [第二十五份审查](advisory-25.md)：角色分工、完整符号、双框、普通组漏符号修复、42记录复放与21页面控制。 |
| 2026-10-25 | 第25轮输入预检 | [第十六份审查](advisory-16.md)：旧NaN与重复资产复现、新入口与失败留痕、身份绑定及API边界。 |
| 2026-10-25 | 第24轮金额完整性 | [第十五份审查](advisory-15.md)、[固定控制](evidence-guard-controls.md)：三类误一致、完整与公开证据映射、语义篡改拒绝和原始坐标边界。 |
| 2026-10-25 | 第21轮任务验收 | [核心设计](2026-10-25-task-acceptance-core.md)、[开发题库](2026-10-25-task-acceptance-cases.md)、[第十二份审查](2026-10-25-advisory-12.md)：固定任务、当前版本、语义边界及真实缺口。 |
| 2026-10-25 | 第18轮新材料与交接模块 | [第九份独立审查](2026-10-25-advisory-09.md)：原PDF合并范围、实际期间与符号、临时DB控制、历史快照、身份清理及实际导出摘要修复。 |
| 2026-10-25 | `0a2611d` 之后的设计复核 | [实际产业合作](2026-10-25-hku-industry-projects.md)、[相关团队项目](2026-10-25-hku-team-projects.md)与[同行现金流任务](2026-10-25-enterprise-research-design.md)：核验交付状态与许可，连接任务、材料、比较和独立交接；设计尚未实现。 |
| 2026-10-25 | AMD冻结前 | [来源与固定规则审查](2026-10-25-advisory-08.md)：14原文金额、78位置区域、来源日期、版本冻结和预览目录保护。 |
| 2026-10-25 | 日期规则与诊断实现 | [日期关联与共享证据审查](2026-10-25-advisory-07.md)：拒绝错日期／错列，区分表头与事实、新关联与旧年份一致，公开片段与完整输入分别核验。 |
| 2026-10-25 | `297ea58` 及几何修复 | [参考坐标失效与诊断协议](2026-10-25-advisory-06.md)：确认 PDF 裁剪原点错误，要求保留无效首次运行、拒绝裁切符号，并以同一 OCR 重算隔离参考修复影响。 |
| 2026-10-25 | 新材料冻结前 | [NVIDIA 参考与日期边界](2026-10-25-advisory-05.md)：包装 PDF 与 10-K 日期分开，比较值不能当历史快照；后续几何更正另记。 |
| 2026-10-25 | `b70b290` | [安全复现与新文档任务冻结](2026-10-25-advisory-04.md) 优先修复重跑覆盖问题；定义未见公司、跨页和图表任务的来源、分组、标注与评分边界。 |
| 2026-10-25 | `69d7aad` | [工作台与历史数据，下一步先补什么？](2026-10-25-advisory-01.md) 本轮交付可操作的季度研究流程；后续研究的主要瓶颈是多公司时点数据与修订链。 |
| 2026-10-25 | `6b80030` | [怎样让模型实际看财报图片？](2026-10-25-advisory-02.md) 建议整页 OCR 后核对已知字段，固定模型与许可、保留不足。主开发采纳情况见 [第 10 轮](../iterations/2026-10-25-10.md)。 |
| 2026-10-25 | `e164d39` | [项目成果与证据审查](2026-10-25-advisory-03.md) 已有实现、证据范围与实验优先级；修正测试重复计数风险和架构图数据流。 |

- [字词坐标对照实验](2026-10-25-word-evidence.md)：原始行级与字词级完整对照、字符和符号保护、真实 CPU 结果。
- [固定图像退化实验](2026-10-25-visual-robustness.md)：两页四档全部结果与失败，不把合成变体当真实扫描集。

- [AMD参考准备](2026-10-25-amd-reference.md)：未读取OCR前选择两页14事实，区分重列、持续经营和首次可得时间。
- [港大相关课题与项目背景](2026-10-25-hku-research-context.md)：核验金融表格、OCR错误传递、具名产业案例与时序关系研究，区分公开参照、本人经历和本项目成果。

- [研究材料契约与比较](2026-10-25-research-materials.md) · [完整提交快照](2026-10-25-research-handoff.md)

- [经营现金流来源与提取](2026-10-25-cash-flow-sources.md) · [逐行会计分解](2026-10-25-cash-flow-bridge.md) · [独立来源与兼容审查](2026-10-25-advisory-10.md)

- [公开事实驱动的模拟业务场景](2026-10-25-research-scenarios.md) · [场景合理性与叙述审查](2026-10-25-scenario-advisory.md)

- [原始数据按需恢复与本地存储](2026-10-25-remote-data.md)


第20轮新增：[金额符号规则](2026-10-25-currency-evidence.md)、[固定反例与可复算报告](2026-10-25-currency-controls.md)、[第十一份独立审查](2026-10-25-advisory-11.md)。对应[功能说明](../23-currency-evidence.md)与[制作报告](../iterations/2026-10-25-20.md)。

第22轮新增：[公司解释来源与核心](company-attribution-core.md)、[固定开发控制](company-attribution-cases.md)、[独立审查](advisory-13.md)。公司陈述、数值舍入与独立核实分别处理，见[功能说明](../25-company-attribution.md)。

第23轮新增：[证据与离线契约](explanation-evidence-contract.md)、[独立交接状态机](explanation-review-workflow.md)、[第十四份独立审查](advisory-14.md)。实际操作与结果见[功能说明](../26-explanation-review.md)和[制作记录](../iterations/2026-10-25-23.md)。

第26轮新增：[第十七份独立审查](advisory-17.md)，检查整组输入、持久化、历史导出及UI失败重试；对应[材料准备](../29-document-preparation.md)和[制作报告](../iterations/2026-10-26-26.md)。

第27轮新增：[当前登记适用性](preparation-applicability.md)与[第十八份独立审查](advisory-18.md)。历史内部一致、当前材料是否匹配、实际内容是否正确分别处理，对应[功能](../30-preparation-history-and-registration.md)和[制作报告](../iterations/2026-10-26-27.md)。

第28轮新增：[跨页证据契约](cross-page-evidence-contract.md)、[登记材料与开发控制](cross-page-materials.md)、[第十九份独立审查](advisory-19.md)。把收入、CFO及范围短锚关联起来，仍与跨页续表解析分开，见[功能](../31-cross-page-evidence.md)和[制作报告](../iterations/2026-10-26-28.md)。

第29轮新增：[整页取数契约](table-lookup-contract.md)、[两方法对照实验](table-lookup-experiment.md)、[第二十份独立审查](advisory-20.md)。参考金额与位置不进入选择接口，真实材料与合成控制分别统计，见[功能](../32-table-lookup.md)和[制作记录](../iterations/2026-10-26-29.md)。

第30轮新增：[词级取数契约](word-table-lookup-contract.md)、[同输入对照实验](word-table-lookup-experiment.md)、[第二十一份独立审查](advisory-21.md)。父行和原词同时保留，42登记与40不同输入分开，4项回退完整公开；见[功能](../33-word-table-lookup.md)和[制作记录](../iterations/2026-10-26-30.md)。

第31轮新增：[参考契约](reference-review-contract.md)、[核对存储与复演](reference-review-workflow.md)、[第二十二份独立审查](advisory-22.md)。实现分别提交、第三身份复核、历史冻结与失败恢复，当前40组已观察问题只作流程开发，不升级为独立基准；见[功能](../34-reference-review.md)。

第32轮新增：[毛利桥契约](margin-bridge-contract.md)、[固定来源与独立算术对照](margin-bridge-experiment.md)、[第二十三份独立审查](advisory-23.md)。复用已保留原页，补28项AI辅助事实，明确近似定位与人工确认缺口；仅作会计分解，见[任务说明](../35-gross-margin-bridge.md)。

第33轮新增：[公司说明契约](margin-claims-contract.md)、[来源与算术对照](margin-claims-experiment.md)、[第二十四份独立审查](advisory-24.md)。依据官方管理层讨论核对范围与金额／比率，区别公司原话、会计分解和独立核实，见[任务说明](../36-margin-company-statements.md)。

第35轮新增：[同名行契约](row-disambiguation-contract.md)、[候选路径复演](row-disambiguation-experiment.md)、[第二十六份独立审查](advisory-26.md)。辅助选择与自动实验分开，不通过参考分区替用户选行，见[任务](../38-row-disambiguation.md)。

第36轮新增：[选择导出核对契约](row-selection-verification-contract.md)、[可复演控制与CLI](row-selection-verification-experiment.md)、[第二十七份独立审查](advisory-27.md)。检查与可信冻结响应的对应关系，明确无签名与人工参考的范围。

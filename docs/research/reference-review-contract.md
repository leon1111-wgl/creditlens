# 参考核对的来源目录与观察契约

`reference-review-1` 为两次独立填写提供数据契约：先看无标记原页、各自记录观察，再比较内容差异。它不自动代填参考，不检查填写值是否等于旧参考，也不把相同记录升级成人工金标准。身份权限、提交隔离、第三人复核和冻结由工作流负责，本模块不实现这些权限。

## 目录给填写者看什么

固定输入是整行与词级对照已经发布的 `word-table-lookup-report.json`，发布字节摘要为 `5526c6d496a304c877ebd2b9121ef314bdf3a36c67fa8023ae34505b68255460`。先使用原实验的保存结果验证器检查来源与版本，再按文档、PDF 摘要、物理页、规范化行名和年份归并：42 条旧记录形成 40 个不同查询。

`build_reference_catalog(root)` 只返回白名单字段：

```text
schema_version, contract_version, source_report_sha256, basis,
tasks[], catalog_sha256

task = id, doc_id, source{url,pdf_sha256,image_sha256}, page,
       width, height, coordinate_system, query{row_label,year},
       basis, task_sha256
```

任务 ID 是查询身份的摘要，不含旧事实 ID、流动／非流动分区或答案。目录不返回旧金额、参考框、单位答案、分区答案、OCR 段、模型候选、评分和结果。`load_reference_registry(root)` 另返回 `task_case_map`，仅供服务端保存 40 个任务与 42 条旧记录的关系；不能把这个对象直接发送给填写端。

这些是已参与开发、旧答案也已公开过的材料。`basis.material_status` 固定为 `previously_observed_development`，`independent_test` 与 `human_gold` 都为 `false`。本实验可以验证新界面的响应隐藏旧答案和另一人的填写内容，不能证明操作者从未看过答案，也不能把再核对称为新的独立盲测。

## 每次填写的内容

```python
validate_observation(task: dict, data: dict) -> dict
```

输入严格为八个字段，不接收模型候选、期待值、填写者身份、可修改年份或可修改页码：

| 字段 | 约束 |
| --- | --- |
| `status` | `answerable`、`ambiguous`、`unreadable` 或 `insufficient`。 |
| `raw_value` | 可回答时填写完整金额原文字面值；不删除负号、括号或货币符号。其余状态必须为空。 |
| `amount_bbox` | 可回答时提供 `x0/top/x1/bottom`，单位为 PDF 点、左上原点；有限数值、非退化且完整在任务页范围内。布尔值和数字字符串均拒绝。 |
| `unit_text` | 可回答时填写原文单位说明，包含例外限定；没有单位依据时应选不足，而不是补默认单位。 |
| `currency_text` | 原文币种依据，可为空。量级或美元符号不自动转换为已证实 USD。 |
| `section_text` | 所选分区的原文，可为空。 |
| `section_note` | 原页没有可抄录分区标题时解释所选范围；与 `section_text` 至少一项存在。 |
| `reason` | 非回答状态必填原因；可回答时可以为空。是记录，不是可执行指令。 |

非回答状态除 `status/reason` 外，其余六个字段必须为空，不能保留预填金额，也不能把缺失填成零。状态“材料不足”只说明本次核对不足，不替企业声明“没有披露”。

金额沿用现有完整金额语法，支持通常的分组逗号、小数、显式正负号、括号负数及 `$ / ¥ / ￥` 前缀。表达式、指数、百分号、破折号占位、多个金额和残缺符号拒绝；本实验不自动修补不支持的写法。Decimal 解析保留负零，原字面值始终单独保存。将 `1,234` 与 `1234` 解析成同一数值，不意味着两份原文填写一致。

输出保留八个输入字段，补充 `schema_version/rule_version/task_id/task_sha256/page/normalized_value/observation_sha256`。页码与年份来自已绑定任务，不能随观察改变。`validate_reference_task(task)` 可离线核对任务快照的结构及内容摘要；它不需要当前来源目录，也不认证原文。

## 两份观察怎样比较

```python
compare_observations(first: dict, second: dict) -> dict
```

输入必须是经过规范化的观察，且任务、任务摘要和页码相同。工作流仍应将保存记录的八个字段送回 `validate_observation(冻结任务,...)`，确认它们满足该页实际尺寸，不能仅依赖观察自带摘要。

比较字段固定为状态、金额原文、归一数值、金额框、单位原文、币种原文、分区原文与分区说明。坐标目前要求严格相同，不暗中加入容差；人工框有差异时显示差异后再处理。`section_note` 承担范围说明，不能作为无关备注忽略。自由 `reason` 不参与内容一致判定，只通过 `notes_differ` 提示，两份理由仍在各自记录及摘要中保留。

结果包含 `status=exact_agreement|disagreement`、`answer_status`、两份观察摘要、`compared_fields` 与逐字段 `differences`。两份都说“歧义”的记录可以内容一致，但 `answer_status` 仍是 `ambiguous`，不能展示成已经找到金额。两个不同状态的 `answer_status` 为空。无论哪种结果，`human_gold` 与 `automatic_approval` 都为 `false`。

摘要只支持内部一致性核查。自洽地重写内容并重算摘要不能被当作来源真实性、真实人类身份或独立工作证明。填写者是否为两个不同账号、是否为两位真人、是否真的阅读原页，是不同问题；本契约不把账号数量当作已完成独立人工标注。

## 验证与范围

22 项软件回归覆盖实际 40／42 清单、答案与 OCR 金丝雀不泄露、固定来源变更、完整符号解析与负零、全局 Decimal 低精度、坐标布尔值／字符串／非有限／越界、分区和单位缺失、非回答残留零值、额外候选／身份字段、任务摘要与观察摘要、不同任务、等值但字面／单位／分区／框不同、自由备注与非回答一致状态，以及不可执行文字。测试中的填写值是合成输入，不是真人对实际任务完成了核对。

没有下载原文、重跑 OCR 或模型，也没有向实际待填目录预填任何金额。双身份提交与第三身份冻结的运行验证见工作流测试，本契约测试不覆盖整条界面流程。

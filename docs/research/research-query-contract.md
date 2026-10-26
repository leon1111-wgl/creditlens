# 显式范围的研究查询

规则：`research-query-1`。日期：2026-10-26。此层新增只读查询，不修改原研究任务、三项冻结检查、解释补充稿或审批记录。

## 解决的问题

研究底稿允许自由填写标题，但既有检查使用固定的八项 CFO/PPE 现金支出材料。标题写“全部资本投入”或“毛利率为何下降”，不能让现金流卡片齐全等同问题已回答。

本层要求操作者明确选择公司、指标和年度，再选择查指标、比较变化或整理经营证据。标题不参与匹配，也不被返回为已回答的问题。当前材料仍是微软、苹果 FY2025 报告中的 FY2024/FY2025 比较列；不是实时获取，也没有历史时点查询。

## 请求及返回

`ResearchEvidenceQuery` 使用 Pydantic 严格类型及 `extra="forbid"`：

```json
{
  "revision": 2,
  "task_kind": "comparable_change",
  "observations": [
    {"issuer_id": "MSFT", "metric": "cfo", "year": 2024},
    {"issuer_id": "MSFT", "metric": "cfo", "year": 2025}
  ]
}
```

`revision` 是正整数；`year` 是 1–9999 的严格整数，布尔、数字字符串和小数均拒绝。公司、指标使用有长度上限的非空字符串，按精确标识匹配，不提供别名、大小写转换或文本语义推断。查指标恰好选一项，其余任务恰好选两项；保留选择次序。额外的 `question`、`as_of`、金额、证据 ID 或批准字段均不接受。

公开函数为 `build_research_query(task, request)`，接受请求字典或该模型，模型对象也会重新校验。调用方负责鉴权、租户隔离及从服务端读取当前任务。核心先执行 `build_task_acceptance`，核对整个任务的登记材料、保存的比较及可选会计桥；窄查询不能绕过其他保存字段的完整性检查。版本不一致抛出 `ResearchQueryError`，包含固定 `code/path/message`，不回显无效输入或未知键名。

返回顶层：

- `query/query_sha256`、`task_id/revision/bundle_id/bundle_sha256` 和 `input_binding` 绑定当前查询及材料版本。
- `selected_evidence` 按请求次序保留 `selector/status/card`；卡片来自已校验的旧检查，原值、符号、归一过程、单位、定义、期间、来源和采用状态不变。
- `source_bindings/source_bindings_sha256` 只列所选观察相关的财务来源；经营查询另列相关公司的会计资料和公司说明来源。目录或整包摘要可绑定共同登记版本，但不携带另一公司的正文或卡片。
- `calculation` 仅为选定年度比较；`operating_evidence` 仅为支持范围内的经营证据。
- `status/gaps` 分别表示当前查询结果与具体缺口。`result_sha256` 为不含自身字段的规范 JSON SHA-256；规范形式使用 `sort_keys=True`、UTF-8、`ensure_ascii=False`、紧凑分隔符及 `allow_nan=False`。

摘要用于识别内容，不是签名、身份认证或真实性证明。返回明确保持 `read_only=true`、`natural_language_question_understood=false`、`automatic_approval=false`、`included_in_frozen_snapshot=false`。关联旧 snapshot 只沿用既有身份说明，不表示本结果被旧审批涵盖。

## 状态与拒绝边界

| 查询情况 | 结果 | 数值及恢复方式 |
|---|---|---|
| 已登记指标，无论是否采用 | `evidence_available` | 原值可以查阅；未采用时 `usable_for_calculation=false` |
| 任一所选组合没有登记 | `not_registered` | 对应 `card=null`；不填零、不写“企业未披露”，不从另一年度借值 |
| 同公司同指标 FY2024→FY2025 且双方采用 | `computed` | 完整保留既有年度变化、输入、公式、单位、舍入规则及警告 |
| 未采用、重复选择、跨公司、不同指标或顺序不符 | `blocked` | 不产生可用的比较金额；已登记卡片仍可查看 |
| 经营查询选择已登记但不支持的范围 | `unsupported_scope` | 当前只支持同公司 FY2024→FY2025 的两期 CFO，不以 CFO 回答 capex 问题 |
| 支持的经营范围但未采用两期 CFO | `blocked` | 任务会计分解的 `calculation=null`；公司原文及独立数值核对可保留，关联状态仍等待或阻断 |
| 支持的经营范围且采用两期 CFO | `insufficient` | 可整理会计分解和公司说法，但独立核实仍缺失，不形成因果结论 |

跨公司选择如能对应既有并列项，会保留其原有财年边界、日历和非严格同行的阻断原因。不会计算排名、相互增长率或自动年化。PPE 现金支出保持原登记定义，不能代表全部资本投入。

## 经营证据分层

`operating_evidence` 包含：

- `accounting_decomposition`：所选公司的单个会计桥结果或 `null`。没有附桥的任务不会自动借用别的任务或注册表计算。未采用时保留阻断原因，隐藏任务可用计算；有采用且桥可用时保留原分项和恒等式。这是会计分解，不是经济原因。
- `company_statements`：只返回所选公司已经登记的说明；当前微软有一项，苹果目录未登记。保留全部限定、抵消项、来源、舍入数值核对和 `link_status`。这是公司陈述，其数值相容不证明陈述中的原因，也不等于新说明经过审批。
- `company_coverage`：只含所选公司的目录覆盖记录，苹果的 `registered=false` 不等于没有公开披露。
- `candidate_hypotheses`：按显式证据 ID 与本查询相交筛选，状态固定继承 `unverified_candidate`。混合引用保留 `selected_evidence_ids/out_of_query_evidence_ids/all_references_selected`，并提示外部引用不在本查询检查范围；不把文字“已经证明”当成结构状态。
- `independent_verification`：当前固定为 `missing`，独立核实和因果验证均为 false；`causal_conclusion=null`。

这一层没有执行文档文本、访问任意 URL、网络调用、模型调用、数据库写入或快照修改路径。会计与舍入检查复用已登记模块；新包装层隔离调用方 Decimal 上下文，避免外部低精度、舍入或 traps 改变投影结果。

## 验证与限制

专属测试覆盖八项登记事实、四项正确年际差额、未知公司/指标/年度、未采用与拒绝、重复/倒序/错指标/跨公司、两家公司经营证据、未附桥、混合假设引用、恶意标题、严格字段和类型、来源/比较/桥篡改、后续目录核查失败、输入不变及返回值不共享可变引用。调用者仍须另行验证鉴权与当前版本读取。

工作流采用意见和研究标题仅为合成软件控制。没有新标注人、客户反馈、独立财务金标准或模型问答准确率；公开财务事实继续来自原登记资料。没有重新下载 PDF、运行 OCR 或训练。

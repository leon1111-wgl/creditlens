# 公司毛利说明的结构化核对契约

`margin-claims-1` 将已登记的公司说明与上一轮毛利案例中的算术结果对应。它回答三个问题：是不是同一公司和同一期间，说明所用的范围是否有对应数据，以及数值或方向在披露精度下是否相容。

它不读取任意文章来理解公司说了什么，不验证转述语义，也不将“公司声称的因素”分配给上一轮会计分解的收入构成项或分类毛利率项。

## 输入

```python
evaluate_margin_claims(payload: dict) -> dict
```

输入顶层严格为 `schema_version:1`、`margin_case:{input,result}` 和 `claims`。`margin_case` 取自已经保存的毛利案例；核心重新执行旧 `evaluate_margin_bridge(input)`，要求结果逐字段规范摘要与传入 `result` 相同且状态为 `computed`，然后才使用其中的两期金额和比率。

这里重算的是旧输入的内部会计关系，不证明输入来自真实文件。服务端适配器仍须固定登记来源、旧报告、原数据与实现版本。纯核心没有权限认证或网络访问。

每条声明的结构如下：

```text
id, issuer_id, report_sha256, base_year, current_year,
periods{base{start,end},current{start,end}}, scope, metric, claim_type,
source{id,url,sha256,locator}, paraphrase_zh,
qualifiers{primary,partial_offset},
drivers[{role,text_zh}], direction, disclosures[]

disclosure = kind, value, unit, decimal_places
```

- `scope` 只支持 `consolidated_total/products/services/microsoft_cloud`。前三项可查找已有案例中的对应分类；最后一项明确返回范围缺口。未知类别拒绝输入，不自动改成全公司。
- `metric` 为毛利金额 `gross_profit_amount` 或毛利率 `gross_margin_rate`。不是营业利润率，也不是经营分部利润。
- `claim_type` 为 `company_explanation` 或 `numeric_only`。纯数值披露没有驱动因素和原因限定；公司解释至少有一个 `driver`。
- `primary` 记录原文的主要因素限定；`partial_offset` 对应登记的抵消因素。结构必须彼此一致，所有因素和转述都原样保留。核心不凭算术判断中文转述是否忠实。
- `source.sha256` 是登记的原文捕获摘要，可以标识规范化的工具提取文本，并不必然是完整 HTTP 响应的摘要。捕获方式、实际读取时间和原始字节核验范围应由来源登记另行保留。
- 方向为 `increase/decrease/unchanged` 或空。数值声明和方向至少有一项，不能只输入一段自由文字就得到“核对通过”。

本版只处理旧案例中的 FY2024、FY2025。完整起止日期仍须核对，不能只用相同的 FY 标签混配。公司、PDF 报告摘要或期间不一致时，声明结果为 `invalid`，不显示借用来的实际数值。

## 数值与舍入

金额声明支持：

| kind | 单位与计算 |
| --- | --- |
| `delta_amount` | `USD_million` 或 `USD_billion`；本期毛利减基期毛利，十亿美元值由百万美元除以1000。 |
| `growth_percent` | `percent`；金额变化除以基期毛利乘100，基期必须大于零。 |

毛利率声明支持：

| kind | 单位与计算 |
| --- | --- |
| `base_percent/current_percent` | `percent`；对应期毛利除以收入乘100。 |
| `delta_pp` | `percentage_point`；两期毛利率之差乘100。 |

百分比增长和百分点变化不能互换，也不能拿毛利金额的增长方向替代毛利率方向。毛利金额可以增加，同时毛利率下降。

`value` 必须是有界 Decimal 字符串，披露精度只允许0或1位小数，且与字面值一致。例如整数 `13` 必须登记0位，不能将 `13.0` 冒充原文披露的整数精度。核心使用独立的80位 Decimal 上下文，并以 `ROUND_HALF_UP` 将计算值舍入到披露精度。保留 `stated/exact/rounded`、单位、精度、转换公式及输入事实 ID。

`exact` 在本接口中指80位精度下尚未为展示而舍入的算术值，不表示无限精度有理数。数值相容只说明该精度下兼容，不声称披露的 `22.9` 与未舍入 `22.885` 精确相等。

两期分别披露的百分率还会保留 `rate_display_comparison`：例如一位小数的两个披露值相减，与未舍入毛利率变化可能不同。接口分别输出 `stated_delta_pp` 和 `exact_delta_pp`，不将显示差替换成未舍入差。

基期毛利为零或负时，本版常规同比增长率检查为 `invalid`，不会补零或取基期绝对值；其他有定义的方向或毛利率任务仍可独立处理。

## 范围缺口先于数值接近

`microsoft_cloud` 不属于旧案例的全公司、产品或服务分类。该说明直接返回 `scope_unavailable`，`actual/direction_check` 为空且 `numeric_checks` 为空。即使全公司毛利率碰巧也舍入成69%，也不能将其用作 Cloud 的支持数据。

原产品和服务标签继续使用旧公司案例的原名与定义。核心不把微软 `Service and other` 与苹果 `Services` 当成相同经济业务。

## 输出及状态

顶层返回版本、输入摘要、公司与报告绑定、旧案例摘要、`results`、按状态计数的 `summary` 和限制。

每条结果保留完整登记声明、绑定检查、原因、实际两期算术依据、方向检查和逐项数值检查。状态分为：

- `matched_numeric`：存在数值检查，全部数值及可选方向相容。
- `matched_direction`：本条只有方向，且方向相容。
- `scope_unavailable`：没有该范围的可比数，未借其他分类计算。
- `contradiction`：登记的方向或数值与当前绑定的算术不一致，需要回查原文、范围和登记。
- `invalid`：绑定、指标／单位、披露精度或检查定义不成立。

顶层结构损坏、布尔值冒充数字、非有限值、额外字段、未知枚举、重复声明 ID 或旧案例被改写时，抛出受控的 `MarginClaimsError(code,path)`。不回显输入内容。

所有结果的支持范围都仅为公司陈述：`support_scope=company_statement_only`。`independently_verified`、`causal_explanation`、`linked_to_accounting_effects`、`natural_language_understood` 和 `automatic_approval` 始终为 `false`。自由文字即使写着“已独立证实”也不能提升这些状态。核心只检查限定词与角色的结构关系；防止官方登记文案被删改还依赖服务端的目录版本与摘要核对，不能称为自动语义验证。

## 验证范围

22项核心回归覆盖：方向与数值分开、金额变化和相对增长、正负半位舍入、已舍入百分率与未舍入差、金额上升而比率下降、矛盾值、Cloud 不借配、产品／服务独立输入、公司／报告／完整期间、百分比与百分点错用、披露精度与严格类型、重复声明、零／负基期、纯数值无原因、限定项角色、恶意自由文字、旧案例篡改、缺数据、Decimal 上下文隔离及实际已登记两公司金额的算术兼容。

测试中的说明文字和多数数据是明确的合成控制；实际金额来自已经保存的开发案例，测试本身不构成人工原文核验或新的模型评测。正式公司说明的来源登记、八条声明报告及界面由另外的分支整合，本核心不提前声明它们已完成发布。

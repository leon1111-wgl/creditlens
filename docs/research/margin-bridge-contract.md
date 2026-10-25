# 产品、服务毛利率变化的对称会计分解

`margin-bridge-1` 处理同一公司、同一版报告中两个相邻年度的产品和服务分类。先核对收入、成本与披露毛利，再把总毛利率变化分成收入构成变化和分类毛利率变化两部分。这是一条可复算的会计恒等式，不能证明涨价、销量、费用管理或其他经营因素造成了变化。

产品和服务是收入分类，不是经营分部。微软的 `Service and other` 与苹果的 `Services` 保留各自原名，不能因接口使用相同的类别 ID 就称为同质业务或进行排名。

## 输入及来源

纯函数 `evaluate_margin_bridge(payload:dict)->dict` 不访问网络、数据库、PDF、OCR 或模型。调用方必须先从服务端登记材料构造输入。它检查内部来源绑定及口径关系，不认证原文或客户端权限。

```text
payload = schema_version:1, id, issuer_id, company,
          source{id,url,sha256}, statements[基期,本期]

statement = year, issuer_id, source_id, source_sha256, unit,
            period{start,end,days,kind,fiscal_calendar},
            scope{consolidation,statement_basis,operations_partition},
            classification{basis,definition}, categories[2],
            total_revenue:fact, total_cost:fact, gross_profit:fact

category = id, label, definition, revenue:fact, cost:fact

fact = id, label, raw_value, value, value_status,
       source_id, source_sha256, unit, period{start,end},
       location{page,locator,amount_bbox{x0,top,x1,bottom}|null}
```

每期七项披露事实，共十四项。类别 ID 必须唯一且恰好为 `products/services`；分类原行名须与其收入和成本事实的 `label` 一致。分类定义与标签必须跨年相同。这里检查的是调用方登记的“同一报告比较列与同名分类”，不能证明分类经济内涵从未变化。

金额是有界 Decimal 字符串，最多36位整数和12位小数，不接受浮点数、布尔值、指数或非有限值。`raw_value` 保留完整原文字面值，通过完整金额语法核对符号与数值；括号、负号和负零不会被取绝对值。显式 `¥/￥` 与登记美元单位冲突时拒算，`$` 本身也不单独证明 USD。

只支持 `USD_million` 和下列范围：`consolidated / as_reported_statement_total / unknown`。最后一项表示持续、终止经营等细分在本任务中保持未知，不将合并报表总额包装成某个更窄范围。

`value_status` 分为 `reported/not_disclosed/not_applicable/unreadable`。只有已披露、金额原文完整且有非退化定位框的值参与计算；其他状态均拒算，不填零。原页实际尺寸与登记材料真实性由上游适配器核对，本核心只检查坐标为有限非负值且框不退化，不宣称读取了原页。

## 计算前的门槛

每期必须精确满足：

```text
产品收入 + 服务收入 = 披露总收入
产品成本 + 服务成本 = 披露总成本
披露总收入 - 披露总成本 = 披露毛利金额
```

这些金额等式不使用舍入容差。一分钱或最小登记小数位的差异也不能被后面的比率容差掩盖。所有事实 ID、来源版本、单位及期间都分别检查；相同数值不能代替另一年或另一来源的事实。

两期按基期、本期输入，年度标签连续，并对应各自期间结束年。日期必须相接且为完整年度。日历年度允许365／366天，差异作为警告保留，不作按日年化；周历年度允许364／371天，但同一次比较的两年长度必须相同，本轮不把53周折算为52周。

总收入和每类收入必须大于零。成本大于收入产生的负毛利合法，按原符号计算并提示；成本为负则不在本契约支持范围，明确拒算。收入为零时分类毛利率没有定义，不能将它设置为零继续算。

## 对称分解

对类别 `i`、年度 `t` 定义：

```text
m[i,t] = (收入[i,t] - 成本[i,t]) / 收入[i,t]
w[i,t] = 收入[i,t] / 披露总收入[t]

收入构成贡献（百分点）
  = 100 × Σ[(w[i,1] - w[i,0]) × (m[i,1] + m[i,0]) / 2]

分类毛利率贡献（百分点）
  = 100 × Σ[(m[i,1] - m[i,0]) × (w[i,1] + w[i,0]) / 2]

总毛利率变化（百分点）
  = 100 × [披露毛利[1]/披露总收入[1] - 披露毛利[0]/披露总收入[0]]
```

两部分加总应等于总变化，交互部分被对称分配，而不是全部归给先计算的一项。分类毛利金额由收入减成本得到，输出命名为 `gross_profit_derived`；不能称为原表另行披露的金额。总毛利原文金额保存在 `gross_profit_reported`，比率和百分率另列。

计算使用新的 Decimal 上下文：80位有效精度、`ROUND_HALF_EVEN`，不继承调用者的精度、舍入模式或陷阱。输入金额位数和小数尺度有上界，加总关系在除法前精确核对。只有比率及其乘法产生的有限精度残差允许不超过 `1e-24` 个百分点；超过则拒算，不调容差修复来源问题。

显示采用完整计算结果直接 `ROUND_HALF_UP` 到六位小数，不先将两年毛利率舍入后再求变化。`residual_pp` 保存完整精度恒等式残差，`display.displayed_residual_pp` 保存显示后的总变化减去显示后的两部分之和；这两种残差不可混为一项“数据差异”。

## 返回与拒算

结构无效、额外字段、错误类型、未知 schema 或非法源 URL 抛出 `MarginBridgeError(code,path)`，不回显恶意输入。结构有效但资料不足或口径冲突返回 `status=blocked`、固定中文 `checks/reasons`、原始输入与引用，`calculation` 为空。

通过后返回 `status=computed`：

- `period_results`：两期总收入、总成本、披露及计算毛利、总毛利率、各分类收入／成本／派生毛利／权重和毛利率，含原事实引用。
- `components`：每类的基期、本期权重与毛利率、变化、平均值、构成贡献及类内贡献，全部为 Decimal 字符串。
- `calculation`：两期百分率、总变化、两种贡献、解释合计、残差、精度和容差。`display` 给出六位显示字符串及分项。
- `period_results[].display`：总毛利率和各分类权重、毛利率的六位百分率，供页面直接显示，无需在浏览器重新计算。
- `inputs/input_refs/input_sha256/formula`：冻结复算输入、原文定位、内容摘要及公式。

任何状态均保留 `human_gold=false`、`causal_explanation=false`、`cross_company_ranking=false` 和 `automatic_approval=false`。资料加总相等不等于已经通过来源真实性核查或独立人工审批。

## 本核心的验证

24项合成回归包括：只有构成变化、只有类内变化、用独立 Fraction 算术核对两项混合变化、负分类及总毛利、总变化为零但分项抵消、外部低精度和舍入陷阱、精确金额差、零与负收入、负成本、各类缺失、原文符号和币种冲突、缺类／重复类／分类事实交换、分类定义变化、跨公司／来源／期间／单位／范围、重复事实、年度次序和极端日期、52周同长度与52／53周拒算、坐标与严格类型，以及显示残差。

这些是软件行为控制。实际微软与苹果28项登记事实及两家公司分解由来源与实验分支复核并发布，本契约不将合成测试计入官方数据样本，不提前声称模型、人工金标准或经营原因已经验证。

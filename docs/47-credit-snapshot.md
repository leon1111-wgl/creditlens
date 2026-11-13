# 信用快照：任意 SEC 申报文件的信用指标，每个数字一键回到原格

v0.36.0（2026-11）。访客把 EDGAR 上的 10-K 或 10-Q（Inline XBRL `.htm`）拖进页面，浏览器内读取发行人自己打的 XBRL 标签，计算杠杆、利息覆盖、流动性、自由现金流、债务到期结构与租赁负债；点任何一个数字，申报文件在旁边的隔离视图里滚到那一格并高亮。位置来自标签本身，不经 OCR，没有读错这一环。没有服务器、没有模型、文件不上传。

这是信用视角的整理，不是评级，也不是投资建议。复核提示只指出“该看哪里”，不下结论。

本文件先写定义与预先登记的三项检查（在任何运行之前提交），结果在运行后追加到文末。

## 定义（credit-snapshot-def-1）

本节是完整的计算口径。另一个模型据此独立计算检查 (b) 的答案，所以写成可以照着做的步骤。

### 1. 事实

- **事实** = 文件里每一个 `ix:nonFraction` 元素（按文档顺序，含 `ix:hidden` 内的），`xsi:nil="true"` 的除外。
- **概念** = `name` 属性，前缀按文件根元素上的 `xmlns:` 声明解析；`us-gaap:X` 指命名空间以 `http://fasb.org/us-gaap/` 开头的 X。下文概念名不写前缀时都是 us-gaap。
- **数值** = 元素文字按 `format` 解析，乘以 10 的 `scale` 次方，`sign="-"` 时取负。格式按本地名识别（不论 ixt 版本前缀）：`num-dot-decimal`／`numdotdecimal`（去掉千分位逗号与空格）；`num-comma-decimal`／`numcommadecimal`（`.` 或空格为千分位，`,` 为小数点）；`fixed-zero`、`zerodash`、`fixed-empty` 为 0；`numwordsen` 只认 no／none／zero 为 0；没有 `format` 时按普通十进制数解析。其他格式的事实不解析，单独计数。
- **合并口径**：上下文没有 `segment` 与 `scenario`（没有 `explicitMember`／`typedMember`）。下文所有取值都只用合并口径的事实。
- **报告币种**：`Assets` 在 I（见下）的事实所用币种（单位为单一 `iso4217:` 量度）；金额只取这个币种的事实。
- 上下文按日期匹配，不按上下文编号：不同编号、相同期间的上下文视为同一期间。

### 2. 期间

- **D** = `dei:DocumentType` 这个事实所在上下文的结束日（`endDate`，或 `instant`）。
- **I**（时点）= D。
- **P**（期间）= 结束日为 D、长度不超过 400 天的合并口径期间里最长的一个，只考虑至少有一个报告币种金额事实的期间。10-K 通常是 12 个月；10-Q 是年初至今（YTD）。**月数** = round(天数 ÷ 30.44)。
- **取值**：概念 X 在 I 或 P 的值 = X 在该期间、合并口径、报告币种的事实。同一概念同一期间出现多次（报表与附注各一次）且数值相同，视为一个事实；数值不同时取 `decimals` 最大的（INF 最大），再相同取文档中最先出现的，并给出复核提示 Q13。
- **按顺序取第一个存在的**：下文“A | B | C”表示依次检查，取第一个在该期间有值的概念。

### 3. 支持的文件与模板

- `dei:DocumentType` 为 10-K、10-K/A、10-KT、10-Q、10-Q/A、10-QT 之一；否则“不支持的文件类型”（20-F、40-F 等）。
- 文件中须有 us-gaap 命名空间的事实；没有（如只用 ifrs-full）时“只支持美国公认会计准则（US GAAP）”。
- **银行**：`InterestAndDividendIncomeOperating` 在 P 有值，或 `Deposits` 在 I 的值不少于 `Assets` 在 I 的 20%（非银行公司也会用 `Deposits` 记押金，金额很小）。
- **保险**：`PremiumsEarnedNet`（P）、`PolicyholderBenefitsAndClaimsIncurredNet`（P）、`LiabilityForFuturePolicyBenefits`（I）、`LiabilityForUnpaidClaimsAndClaimsAdjustmentExpense`（I）任一有值。
- **投资公司／BDC**：`NetAssetValuePerShare`（I）有值，或 `InvestmentOwnedAtFairValue`（I）有值。
- 命中以上任何一类：模板不支持，不计算下列指标，只列出触发判断的事实（可点回原格）。依次判断银行、保险、投资公司。

### 4. 资产负债表上的行

债务与流动性按资产负债表上的行加总，因为发行人给同一笔债务用的概念各不相同（`LongTermDebt`、`LongTermDebtAndCapitalLeaseObligations`、`NotesAndLoansPayable`……），而报表上每一行只出现一次。资产负债表按**文档顺序**划定，不依赖 `<table>`（有的文件用绝对定位的 `<div>` 排版，没有表格）。

- 只看在 I、合并口径、报告币种的金额事实；“位置”是事实在文档中的先后顺序。同一概念在区段内出现多次时取第一次。
- **A** = 第一个 `Assets` 事实。**E** = A 之后第一个 `LiabilitiesAndStockholdersEquity`；没有则依次用 `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`、`StockholdersEquity`、`Liabilities`。没有 A 或 E：视为没有找到资产负债表。
- **负债一侧** = 位置在 A 与 E 之间的事实。**LC** = 负债一侧第一个 `LiabilitiesCurrent`；位置在 LC 之前的负债一侧事实属于流动负债区段，之后的属于非流动负债区段。没有 LC：资产负债表不分流动／非流动。
- **流动资产区段**：AC = A 之前最后一个 `AssetsCurrent`。从 AC（没有 AC 时从 A）往前找，遇到第一个期间型（有 startDate）上下文的事实就停（那是上一张报表的流量）；它与 AC（或 A）之间的事实属于流动资产区段。
- **债务行** = 负债一侧中，概念属于下面的债务概念集合的事实；另外，发行人自定义概念（不在 us-gaap 等标准命名空间）的本地名以 `SeniorNotes`、`SeniorSecuredNotes`、`SeniorUnsecuredNotes`、`ConvertibleNotes`、`ConvertibleSeniorNotes`、`SecuredNotes`、`UnsecuredNotes`、`Notes`、`NotesPayable`、`LongTermDebt`、`Debt`、`TermLoan`、`Borrowings`、`CreditFacility`、`RevolvingCreditFacility`、`LineOfCredit` 开头，且不含 `Interest`、`Accrued`、`Receivable`、`Issuance`、`Discount`、`Premium`、`FairValue`、`Cost`、`Guarant`、`Held`、`Asset`、`Securities`、`Lease` 的，也是债务行（Q12 列出）。
- **债务概念集合**：

  `ShortTermBorrowings`、`CommercialPaper`、`ShortTermBankLoansAndNotesPayable`、`ShortTermNonBankLoansAndNotesPayable`、`OtherShortTermBorrowings`、`BankOverdrafts`、`LinesOfCreditCurrent`、`NotesPayableCurrent`、`LoansPayableCurrent`、`NotesAndLoansPayableCurrent`、`NotesAndLoansPayable`、`DebtCurrent`、`LongTermDebtCurrent`、`LongTermDebtAndCapitalLeaseObligationsCurrent`、`ConvertibleNotesPayableCurrent`、`ConvertibleDebtCurrent`、`SecuredDebtCurrent`、`UnsecuredDebtCurrent`、`OtherLongTermDebtCurrent`、`SeniorNotesCurrent`、`LongTermNotesPayableCurrent`、`LongTermLoansPayableCurrent`、`NotesPayableRelatedPartiesClassifiedCurrent`、`LongTermDebtNoncurrent`、`LongTermDebt`、`LongTermDebtAndCapitalLeaseObligations`、`LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`、`DebtLongtermAndShorttermCombinedAmount`、`LongTermNotesPayable`、`LongTermLoansPayable`、`ConvertibleNotesPayable`、`ConvertibleDebtNoncurrent`、`ConvertibleLongTermNotesPayable`、`SeniorNotes`、`SeniorLongTermNotes`、`SecuredLongTermDebt`、`UnsecuredLongTermDebt`、`SecuredDebt`、`UnsecuredDebt`、`LongTermLineOfCredit`、`LineOfCredit`、`OtherLongTermDebtNoncurrent`、`OtherLongTermDebt`、`NotesPayable`、`LoansPayable`、`DebtInstrumentCarryingAmount`、`SubordinatedDebt`、`JuniorSubordinatedNotes`、`NotesPayableRelatedPartiesClassifiedNoncurrent`。

- **债务小计**：`DebtCurrent`、`ShortTermBorrowings`、`LongTermDebt`、`LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`、`DebtLongtermAndShorttermCombinedAmount` 是小计概念（它们也常作为普通行出现）。按文档顺序处理每个小计 S：从 S 前一行起往前数同一区段内、尚未被别的小计吸收的债务行（`DebtCurrent`、`ShortTermBorrowings` 的区段是 S 所在的流动或非流动负债区段；另外三个的区段是整个负债一侧），取最短的、从 S 前一行开始的连续若干行，使其之和恰好等于 S：这几行是 S 的组成行，计入 S，不计组成行。找不到这样的行时，S 当作普通行计入，前面的行照常计入，并给出复核提示 Q14。
- **流动性行** = 流动资产区段中，概念属于流动性概念集合的事实。现金类：`CashAndCashEquivalentsAtCarryingValue`、`Cash`、`CashAndDueFromBanks`、`CashEquivalentsAtCarryingValue`、`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`（同一区段已有 `CashAndCashEquivalentsAtCarryingValue` 时不计；计入时给 Q11）；短期投资类：`ShortTermInvestments`、`OtherShortTermInvestments`、`MarketableSecuritiesCurrent`、`MarketableSecurities`、`AvailableForSaleSecuritiesDebtSecuritiesCurrent`、`AvailableForSaleSecuritiesDebtSecurities`、`HeldToMaturitySecuritiesCurrent`、`ShortTermInvestmentsAndMarketableSecurities`；小计 `CashCashEquivalentsAndShortTermInvestments`。没有 AC（资产不分流动／非流动）时，只计现金类与名称含 Current 的概念。小计 `CashCashEquivalentsAndShortTermInvestments` 吸收它之前的全部流动性行：计入小计，不计这些行；它们之和不等于小计时给出 Q14。

### 5. 输入

| 输入 | 期间 | 规则（按顺序取第一个可用的） |
|---|---|---|
| 收入（只作背景） | P | `Revenues` \| `RevenueFromContractWithCustomerExcludingAssessedTax` \| `RevenueFromContractWithCustomerIncludingAssessedTax` \| `SalesRevenueNet` |
| 利息费用 INT | P | `InterestExpense` \| `InterestExpenseNonoperating` \| `InterestAndDebtExpense` \| `InterestExpenseDebt` \| `InterestCostsIncurred`（含资本化利息，Q20）；都没有时，`InterestIncomeExpenseNonoperatingNet` \| `InterestIncomeExpenseNet` 中第一个有值的，若为负（净支出）则 INT = 其相反数（Q20），为正或为 0 则 INT 未找到 |
| EBIT | P | E1 `OperatingIncomeLoss`；E2（没有 E1 时）税前利润 + INT，税前利润 = `IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest` \| `IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`，须有 INT（Q2） |
| 折旧摊销 DA | P | `DepreciationDepletionAndAmortization` \| `DepreciationAmortizationAndAccretionNet` \| `DepreciationAndAmortization` \| `Depreciation` + `AmortizationOfIntangibleAssets`（两者都有）\| `Depreciation`（只有折旧，Q12 注明未含摊销） |
| 经营现金流 CFO | P | `NetCashProvidedByUsedInOperatingActivities` \| `NetCashProvidedByUsedInOperatingActivitiesContinuingOperations` |
| 资本开支 CAPEX | P | `PaymentsToAcquirePropertyPlantAndEquipment` \| `PaymentsToAcquireProductiveAssets` \| `PaymentsForCapitalImprovements` \| `PaymentsToAcquireOilAndGasPropertyAndEquipment` \| `PaymentsToAcquireOilAndGasProperty` \| `PaymentsToAcquireOtherPropertyPlantAndEquipment`（按标签值，不取绝对值） |
| 流动资产、流动负债 | I | `AssetsCurrent`；`LiabilitiesCurrent` |
| 债务 DEBT、流动债务 CD | I | B（首选）：资产负债表负债一侧的债务行之和（第 4 节，含小计规则）；流动债务 CD = 只在流动负债区段内按同样规则处理的债务行之和（区段内没有债务行时为 0）；没有 `LiabilitiesCurrent` 时 CD 只计名称含 Current 或属于短期借款类（`ShortTermBorrowings`、`CommercialPaper`、`ShortTermBankLoansAndNotesPayable`、`ShortTermNonBankLoansAndNotesPayable`、`OtherShortTermBorrowings`、`BankOverdrafts`）的行。F（没有找到资产负债表，或负债一侧没有债务行）：F1 `LongTermDebt` + STB；F2 `DebtCurrent` + `LongTermDebtNoncurrent`（两者都有时）；F3 `LongTermDebtNoncurrent` + `LongTermDebtCurrent`（可无）+ STB；F4 `LongTermDebtAndCapitalLeaseObligations` + `LongTermDebtAndCapitalLeaseObligationsCurrent`（可无）+ STB；其中 STB = `ShortTermBorrowings` \| （`CommercialPaper`、`ShortTermBankLoansAndNotesPayable`、`OtherShortTermBorrowings` 中有值者之和，没有则为 0）。F 规则下 CD 不计算。都没有：债务“未找到”（不是 0），Q12 |
| 流动性 LIQ | I | 资产负债表流动资产区段的流动性行之和（第 4 节）；没有找到资产负债表或区段内没有流动性行时：（`CashAndCashEquivalentsAtCarryingValue` \| `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`）+（`ShortTermInvestments` \| `MarketableSecuritiesCurrent` \| `AvailableForSaleSecuritiesDebtSecuritiesCurrent` \| `OtherShortTermInvestments`，没有则 0） |
| 经营租赁负债 OL | I | `OperatingLeaseLiability` \| `OperatingLeaseLiabilityNoncurrent` + `OperatingLeaseLiabilityCurrent`（可无） |
| 融资租赁负债 FL | I | `FinanceLeaseLiability` \| `FinanceLeaseLiabilityNoncurrent` + `FinanceLeaseLiabilityCurrent`（可无） |
| 到期：未来 12 个月 | I | `LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths` \| `LongTermDebtMaturitiesRepaymentsOfPrincipalInNextRollingTwelveMonths` \| `LongTermDebtMaturitiesRepaymentsOfPrincipalRemainderOfFiscalYear`（最后一项只用于 10-Q，标为“本财年剩余”） |
| 到期：第 2–5 年 | I | 第 2、3、4、5 年各自取 `...InYearTwo` \| `...InRollingYearTwo`（三、四、五同理，前缀 `LongTermDebtMaturitiesRepaymentsOfPrincipal`），有值的相加；一年都没有为“未找到”，缺几年时注明 |
| 到期：5 年以后 | I | `LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive` \| `LongTermDebtMaturitiesRepaymentsOfPrincipalInRollingAfterYearFive` |

### 6. 指标

| 指标 | 公式 | 不计算的情况 |
|---|---|---|
| 总债务 | DEBT | 债务未找到 |
| 净债务 | DEBT − LIQ | 任一未找到 |
| EBITDA | EBIT + DA | 任一未找到 |
| 债务／EBITDA | DEBT ÷ EBITDA | P 少于 12 个月（10-Q）；EBITDA ≤ 0（Q3） |
| 净债务／EBITDA | (DEBT − LIQ) ÷ EBITDA | 同上 |
| 利息覆盖 | EBIT ÷ INT | INT 未找到或 ≤ 0（Q4） |
| 流动比率 | AssetsCurrent ÷ LiabilitiesCurrent | 任一未找到（不分流动／非流动的资产负债表） |
| 自由现金流 FCF | CFO − CAPEX | 任一未找到 |
| FCF／债务 | FCF ÷ DEBT | P 少于 12 个月；DEBT 未找到或为 0 |
| 含租赁的债务 | DEBT + OL + FL；债务行已含融资租赁（用了 `LongTermDebtAndCapitalLeaseObligations*`）时不再加 FL；只找到 OL、FL 中的一项时另一项按 0 | 债务未找到；需要加的租赁负债一项都没找到 |
| 到期结构 | 未来 12 个月、第 2–5 年、5 年以后三段，对照 LIQ 与 LIQ + FCF（10-K） | 三段都未找到 |

比率保留两位小数显示；比较与导出用未舍入的值。金额显示为原表量级（例如“12,345 百万美元”），不四舍五入。

### 7. 复核提示（提示，不是结论）

| 编号 | 触发 | 写法要点 |
|---|---|---|
| Q1 | 10-Q：P 少于 12 个月 | 流量为年初至今，未年化；杠杆与 FCF／债务不计算 |
| Q2 | EBIT 用 E2 | 未标营业利润，EBIT 由税前利润加利息费用得到 |
| Q3 | EBITDA ≤ 0 | 杠杆倍数没有意义 |
| Q4 | 没有利息费用标签 | 利息覆盖不计算；若只有净利息（`InterestIncomeExpenseNonoperatingNet` 等）则写明 |
| Q5 | EBIT < INT | 营业利润不足以覆盖利息费用，看利率与再融资附注 |
| Q6 | LIQ ≥ DEBT | 现金与短期投资超过债务（净现金） |
| Q7 | 流动比率 < 1 | 流动负债大于流动资产，看营运资本与授信额度 |
| Q8 | FCF < 0 | 本期自由现金流为负 |
| Q9 | CD > LIQ（没有 CD 时用未来 12 个月到期额） | 一年内到期的债务超过现金与短期投资，看流动性来源（循环额度、商业票据备用额度） |
| Q10 | 债务行用了 `LongTermDebtAndCapitalLeaseObligations*` | 债务已含融资租赁 |
| Q11 | 流动性计入了含受限现金的概念 | 现金含受限现金 |
| Q12 | 用了 F 规则；债务未找到；DA 只有折旧；债务含发行人自定义概念；或负债一侧有名称含 Debt／Borrowing／Notes／Loan／CommercialPaper／CreditFacility／LineOfCredit（且不含 Interest／Receivable／Lease／Asset／Securities）而未计入的概念 | 列出相关事实，指向资产负债表或附注 |
| Q13 | 同一概念同一期间数值不一致，且按其中最低的 decimals 舍入后仍不一致（只是精度不同的重复不算） | 列出概念 |
| Q14 | 小计与它前面的行对不上（债务小计因此按普通行计入；流动性小计仍吸收前面的行） | 可能有未识别的行，核对是否重复或遗漏 |
| Q15 | 到期三段之和与 DEBT 相差超过 10% | 到期表是本金，资产负债表是账面值；也可能只覆盖部分债务 |
| Q16 | `PaymentsToDevelopSoftware` 在 P 有值 | 资本化软件支出未从 FCF 中扣除 |
| Q17 | `RealEstateInvestmentPropertyNet` 在 I 有值 | 房地产公司常用 FFO、NOI 等口径，本页不计算 |
| Q18 | 文件类型带 /A | 修订文件，可能只含部分内容 |
| Q19 | 有合并口径、报告币种、文字非空、但格式本页不解析的金额事实 | 列出数量 |
| Q20 | INT 取自 `InterestCostsIncurred` 或利息收支净额 | 写明口径不同于利润表上的利息费用总额 |

## 预先登记的检查（在任何运行之前提交）

参数在 `configs/credit-snapshot.json`。三项检查都在 GitHub Actions 上运行；不向 SEC 发请求（SEC 对本项目声明的 User-Agent 返回 403，Actions 与开发机都是，探测运行 43477784386），文件取自互联网档案馆已有的 SEC 原地址抓取，按 CDX 摘要核对字节。

### 画廊怎样选

总体同 XBRL 对照的规则 b：SEC 2026q1 Financial Statement Data Set 中 2025-12-31 财年末、大型加速申报人的 10-K 原件。按 SIC 大类分 8 层（采矿、建筑、制造、运输通信公用事业、批发、零售、金融保险房地产、服务），每层按加盐 SHA-256 排序，取前 5 家有档案抓取、字节与摘要一致、文件自报 CIK 与类型相符、不超过 25 MB 的发行人。银行、保险与投资公司被抽到时保留，页面显示模板不支持。逐层报告覆盖情况，不手工挑选。

### (a) 解析器对照 SEC 数据

- 参照：同一 accession 在 SEC Financial Statement Data Set（num.txt）里的行。原计划用 SEC CompanyFacts，但 SEC 拒绝本项目的 User-Agent；FSDS 是 SEC 自己对同一份 XBRL 提交的逐 accession 提取，只覆盖财务报表正表，不含附注。
- 比较：num.txt 中 segments 与 coreg 都为空的行（合并口径），全部标签（含发行人自定义）。键 = 标签本地名、单位（量度本地名，除法写作 a/b）、ddate（上下文结束日或时点按 FSDS 的做法取最近的月末）、qtrs（时点为 0，否则 round(天数 ÷ 91.3125)）。值：程序的值按四舍六入五成双保留 4 位小数后精确相等。
- 度量：召回（FSDS 行在程序里找到同键事实的比例）、一致（找到的行中数值相等的比例）、冲突清单。
- **通过线**：全部画廊文件合计召回 ≥ 99.5%、一致 ≥ 99.9%，每个文件召回 ≥ 98%。首轮判定，不事后调整。

### (b) 指标对照独立模型

- 留出样本：SEC 2026q1 的 10-K（同画廊总体）8 份，加 2026q3 的 10-Q（2026-06-30 期末、大型加速申报人）2 份；排除画廊发行人与开发用发行人（苹果、微软、可口可乐），按另一个盐排序，取前几份可用的。选样运行只选样、取文件，不运行快照程序。
- 定义在 Sonnet 开始之前冻结（单独提交）。Sonnet 作为独立代理，只拿到本文件“定义”一节和文件的档案地址，没有看过程序、测试或程序输出，自己读文件计算。它的结果先提交，程序再在这些文件上运行。
- 比较：模板判断、D 与月数，以及第 6 节的全部指标和输入（见配置中的列表）。金额精确到美元；比率各自从自己的金额算出后相差不超过 0.005；“不计算”须双方一致。
- 报告首轮（带提交号）：逐指标、逐文件一致率，每个分歧归入一类：概念选择、期间或上下文、报表区段位置、算术、符号、量级、不计算与有值、独立读者出错。不设通过线，分类本身就是结果。

### (c) 点击定位

- 在 Actions 的 Playwright Chromium 1.49.1 上，对构建好的站点和全部画廊文件，点击快照上的每个数字链接。
- 每次点击须在隔离的申报文件视图中恰好标记一个元素，它就是该事实的锚点，位于框架可视区内；它的文字按该事实的 ixt 格式解析、乘以 10^scale、`sign="-"` 时取负，须等于页面显示的数值（显示文字按其单位解析，同时等于元素上记录的值）。
- **通过线**：100%。没有可见单元格的事实（`ix:hidden` 且正文没有 `-sec-ix-hidden` 显示）不做成链接，单独计数。

## 结果

（运行后追加。）

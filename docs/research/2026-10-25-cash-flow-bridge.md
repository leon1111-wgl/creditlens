# 经营现金流会计桥：把变化拆回原表

日期：2026-10-25。本轮在已有跨公司研究任务上增加一项有边界的研究材料：将经营现金流拆回报表原行，核对每年的加总，再展示两年变化由哪些原行构成。它是可重跑的会计计算，没有运行新 OCR、语言模型或预测模型。

研究员看到“利润增长、经营现金流下降”时，仅有两个总额不够继续调查。现在可以先看净利润、一般调节项、经营资产负债变动分别贡献多少，再打开对应原文，决定下一步查哪条附注。会计贡献能缩小调查范围，但不能替代原因验证。例如，“其他资产负债变动”仍可能混合多种事项，需要附注和管理层讨论进一步拆分。

## 材料和计算边界

输入来自同轮固定的微软、苹果 FY2025 官方报告及其中 FY2024 比较列，见 [来源与建立方法](2026-10-25-cash-flow-sources.md)、[cash_flow_sources.py](../../creditlens/cash_flow_sources.py) 与 [登记参考](../../data/research/cash-flow-bridge-v1.json)。微软每期包括 14 个贡献行和 1 个 CFO 总计，苹果每期包括 10 个贡献行和 1 个 CFO 总计；四期共 52 个登记事实。贡献行都有原始文字、符号、页面、定位框和来源哈希。

本轮来源包的规范 JSON SHA-256 为 `6e2835ffd2891258cc67aff2f46ff8f031c1000b9f9bbb04456655cc3e6d60a6`，与公开入口返回的 `source_bundle_sha256` 相同。

分组直接沿用报表区段：

- `net_income`：净利润。
- `reconciliation_adjustments`：净利润与现金流之间的一般调节项。
- `operating_assets_and_liabilities`：经营资产和负债变动。
- `cfo_total`：披露的经营现金流总计，只作核对目标，不再次加进贡献行。

一般调节项不能一律称为“非现金”，经营资产负债区段也不能一律称为“营运资本”。本轮不拆解报表中 `Other` 的业务组成，不补出尚未取得的因果说明。FY2024 是 FY2025 文件中的比较列，不能用作已证明的 FY2024 历史时点版本。

来源文件与参考建立由同轮材料分支负责；本模块读取固定登记 JSON，不在每次运行时重新鉴定 PDF 真实性。引用的原文、图像和已运行识别实验仍各自保留范围，算式正确不表示新行通过了独立 OCR 评测或真人双人标注。

## 如何计算和拒绝

[cash_flow_bridge.py](../../creditlens/cash_flow_bridge.py) 的公开入口是 `build_cash_flow_bridge(root: Path) -> dict`。它只加载服务端固定来源，拒绝任意客户端提供的来源替换。加载器检查参考摘要、完整清单和来源登记；计算层还核对原值符号、单位、期间、行身份和同一来源。

每期使用 `Decimal`，按原文的百万美元单位精确计算：

```text
计算 CFO = 净利润 + 一般调节项加总 + 经营资产负债变动加总
核对残差 = 计算 CFO − 报表 CFO

某行变化 = FY2025 该行原值 − FY2024 该行原值
变化残差 = 全部原行变化之和 − 报表 CFO 的年度变化
```

括号负号保留，不取绝对值。无数值、破折号或未知项不能填零。完整行清单独立于求和结果：即便删掉的行恰好为零，或同时删掉一正一负相互抵消的行，仍会因为清单不完整而被阻止。

结构合格但加总不一致时，结果保留计算值和非零残差，同时标为 `blocked`；不会把差额塞进一个虚构的“其他”项目。结构不合格时，不输出看似有效的派生值。

两期分别加总成功后，变化分解还要求公司、来源文件、单位、披露范围和原行定义相同，年度相邻且日历受支持。行集合、标签或分组改变时，不用同名匹配自动配对，需要先建立重分类依据。52 周与 53 周不能悄悄折成年化值；完整日历财年包含闰日差异时保留说明，不进行日均化。

不计算“贡献百分比”。经营现金流或变化可能为零、为负，正负贡献也可能互相抵消；只报告带符号金额及可追溯算式，不用一个容易误解的百分比制造结论。

## 实际固定材料的计算结果

以下均为百万美元。四期加总残差与两项变化分解残差都为零；含义仅是本次登记的完整原行满足会计恒等式。

| 公司 | FY2024 CFO | FY2025 CFO | CFO 变化 | 净利润变化贡献 | 一般调节项变化贡献 | 经营资产负债变化贡献 |
|---|---:|---:|---:|---:|---:|---:|
| Microsoft | 118,548 | 136,162 | +17,614 | +13,696 | +11,092 | −7,174 |
| Apple | 118,254 | 111,482 | −6,772 | +18,274 | +3,605 | −28,651 |

例如，苹果这一版本中净利润行的变化为正，而经营资产负债区段变化的合计贡献为负。研究员可据此进一步查看该区段各行和附注；不能仅凭这组分解断言客户回款恶化、经营质量下降或某项政策造成变化。两家公司也没有因此变成可以直接排名的同行样本。

每个 CFO 都按公司、文件哈希和原事实 ID 绑定到上一轮八项研究观察中的对应值，并再次比较金额、单位与实际期间。不同来源的同名 `cfo-2025` 不能替换它。

## 可冻结的输出契约

返回结果没有运行时当前日期，每次对相同登记来源和规则运行都相同，可整体记录 SHA-256 后加入新的研究任务快照。

```text
schema_version / id / rule_version
source_bundle / source_bundle_sha256
statements[]
  issuer_id / year / source_sha256 / reported_cfo_observation_id
  unit / period / scope / expected_row_keys
  rows[]：原行事实、符号、金额、页面与定位
  groups[]：原表分区、input_ids、value
  computed_cfo / reported_cfo / residual / status
  input_ids / contribution_input_ids / formula / reasons / warnings
changes[]
  issuer_id / from_year / to_year / previous_period / current_period
  rows[]：previous_id、current_id、previous_value、current_value、contribution
  groups[]：原表分区、input_ids、contribution
  reported_cfo_change / contribution_total / residual / status
  input_ids / reported_cfo_observation_ids / formula / reasons / warnings
limitations / scope / prediction=false / automatic_approval=false
```

研究任务接入由主分支负责。附件是整组登记行的会计核对，不冒充研究员已经逐项采用这 52 个事实，也不改变已有八项观察的采用决定。旧任务、旧冻结报告与旧规则保持原版本。

## 验收与复现

运行专属测试：

```bash
python -m unittest tests.test_cash_flow_bridge -v
```

17 项专属测试已通过，分为 14 项合成控制及 3 项真实注册材料联动。合成反例检查错符号、非零残差、缺失零值行及对消行、重复行、未知分组、非有限值、跨源、错单位、错期间、重分类、52／53 周差异与低精度外部环境；真实登记参考检查四期完整求和、两家公司逐行变化、旧 CFO 观察绑定，以及来源文件改变或删除时拒绝继续计算。合成资料只用于控制测试，不当作企业数据。

在仓库根目录可重跑计算，并自行将 JSON 保存在 `.run/` 中供检查：

```python
import json
from pathlib import Path
from creditlens.cash_flow_bridge import build_cash_flow_bridge

result = build_cash_flow_bridge(Path.cwd())
print(json.dumps(result, ensure_ascii=False, indent=2))
```

这份模块验证不证明通用财务表提取、自动解释质量或投资预测有效；没有收益、因果、模型准确率或用户节省工时的结论。下一步可以沿具体原行补附注证据和待核问题，再单独设计跨页、重分类以及新发行人的验证，避免把一个已经能复算的会计任务扩成无边界的研究平台。

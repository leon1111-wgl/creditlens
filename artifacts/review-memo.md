# Microsoft Corporation 信用研究复核底稿

状态：reviewed；版本：5
截止日期：2025-08-01

计算规则：cash-debt-review-2；当前规则：cash-debt-review-2
规则版本检查：当前版本。

本底稿由确定性规则生成；复核确认仅表示底稿检查完成，不表示授信审批或投资建议。

## 指标与计算

| 指标 | 值 | 单位 | 公式 | 事实引用 |
|---|---:|---|---|---|
| 流动资金 / 短期债务 | 31.53217739246415471823941314 | ratio | liquid_assets / (short_debt + current_debt) | liquid_assets-2025, short_debt-2025, current_debt-2025 |
| 经营现金流 / 净利润 | 1.337123890329169612695419907 | ratio | cfo / net_income | cfo-2025, net_income-2025 |
| 自由现金流（简化口径） | 71611 | USD_million | cfo - capex | cfo-2025, capex-2025 |
| 资本开支 / 经营现金流 | 0.4740749988983710580044358925 | ratio | capex / cfo | capex-2025, cfo-2025 |
| 流动比率 | 1.353446444504241668909062584 | ratio | current_assets / current_liabilities | current_assets-2025, current_liabilities-2025 |
| 经营现金流压力情景 | 44378.6 | USD_million | cfo * (1 - 20/100) - capex | cfo-2025, capex-2025 |

## 待核查事项与处理

### 资本开支占用较多经营现金流

资本开支 / 经营现金流超过演示规则 40%。需核查项目承诺、后续开支和融资安排；这不是违约判断。

处理：演示记录：已核对官网表格，现金可用性、租赁和资本承诺需补充原文；本演示不作授信结论。

### 债务与现金可用性仍需人工确认

本口径含现金、现金等价物和短期投资；债务覆盖不含全部租赁、担保、资本承诺和受限资金调整。公开披露不足以直接作授信决策。

处理：演示记录：已核对官网表格，现金可用性、租赁和资本承诺需补充原文；本演示不作授信结论。

## 来源

- Microsoft FY25 Q4 / balance-sheets：https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/balance-sheets
  披露日：2025-07-30；采集 SHA256：1f915adbdcbc36f265f59ecce7a2f7cccf842204023dbdd694648aa7bab1e886
- Microsoft FY25 Q4 / cash-flows：https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/cash-flows
  披露日：2025-07-30；采集 SHA256：163848f1431c763ca94e7e517b23e68aa48acf1b4b80f376d59c24a0951582c3
- Microsoft FY25 Q4 / income-statements：https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/income-statements
  披露日：2025-07-30；采集 SHA256：54a83af8f066c7a23b5b836f1cf501e8e656c7f6a3d7286156baaf4017806b0e

数据包 SHA256：c108699fe321af20c85de2fbd84a9c6358a0bae280a231a530b2b5bb4e7e3806

事件哈希链检查：True
本地哈希链不抵御拥有数据库修改权限的管理员重新计算整个链。

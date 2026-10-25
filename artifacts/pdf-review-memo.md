# Microsoft Corporation 信用研究复核底稿

状态：draft；版本：1
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

处理：尚未处理

### 债务与现金可用性仍需人工确认

本口径含现金、现金等价物和短期投资；债务覆盖不含全部租赁、担保、资本承诺和受限资金调整。公开披露不足以直接作授信决策。

处理：尚未处理

## 来源

- Microsoft FY2025 Form 10-K / official PDF：https://microsoft.gcs-web.com/static-files/9640e86d-a99f-4502-934d-1d3c883fa786
  披露日：2025-07-30；采集 SHA256：1d13a76168650bb5efb42f29c6da81aa673e44217d082357ec0815afc70abf2e

## PDF 原页定位

坐标为页面左上角起算的 PDF point；定位与一致性检查不证明真实性。

| 事实 | 原始显示值 | 页码 | 金额框 x0, top, x1, bottom | 年份 | 行标签 |
|---|---|---:|---|---:|---|
| revenue-2025 | 281,724 | 50 | 395.859, 161.758, 427.946, 169.757 | 2025 | Total revenue |
| revenue-2024 | 245,122 | 50 | 468.12, 161.758, 499.901, 169.595 | 2024 | Total revenue |
| liquid_assets-2025 | 94,565 | 52 | 473.157, 151.633, 500.115, 159.632 | 2025 | Total cash, cash equivalents, and short-term investments |
| liquid_assets-2024 | 75,543 | 52 | 545.517, 151.633, 572.205, 159.47 | 2024 | Total cash, cash equivalents, and short-term investments |
| short_debt-2025 | 0 | 52 | 495.754, 318.356, 499.938, 325.023 | 2025 | Short-term debt |
| short_debt-2024 | 6,693 | 52 | 550.435, 318.356, 572.201, 326.193 | 2024 | Short-term debt |
| current_debt-2025 | 2,999 | 52 | 478.093, 328.481, 499.967, 336.48 | 2025 | Current portion of long-term debt |
| current_debt-2024 | 2,249 | 52 | 550.354, 328.481, 572.21, 336.318 | 2024 | Current portion of long-term debt |
| long_debt-2025 | 40,152 | 52 | 473.04, 393.957, 499.935, 401.956 | 2025 | Long-term debt |
| long_debt-2024 | 42,688 | 52 | 545.211, 393.957, 572.214, 401.794 | 2024 | Long-term debt |
| current_assets-2025 | 191,131 | 52 | 468.57, 196.856, 498.921, 204.855 | 2025 | Total current assets |
| current_assets-2024 | 159,734 | 52 | 541.065, 196.856, 572.171, 204.693 | 2024 | Total current assets |
| current_liabilities-2025 | 141,218 | 52 | 468.57, 383.832, 499.973, 391.831 | 2025 | Total current liabilities |
| current_liabilities-2024 | 125,286 | 52 | 541.065, 383.832, 572.189, 391.669 | 2024 | Total current liabilities |
| cfo-2025 | 136,162 | 53 | 396.345, 283.258, 427.703, 291.257 | 2025 | Net cash from operations |
| cfo-2024 | 118,548 | 53 | 468.84, 283.258, 499.982, 291.095 | 2024 | Net cash from operations |
| capex-2025 | (64,551) | 53 | 398.116, 408.728, 430.678, 417.258 | 2025 | Additions to property and equipment |
| capex-2024 | (44,477) | 53 | 470.422, 408.728, 503.047, 417.258 | 2024 | Additions to property and equipment |
| net_income-2025 | 101,832 | 53 | 396.345, 116.535, 427.703, 124.534 | 2025 | Net income |
| net_income-2024 | 88,136 | 53 | 473.238, 116.535, 499.971, 124.372 | 2024 | Net income |

数据包 SHA256：bd776c972631cbc6ff6e0cba585cdab2c32d875d21e950ae43987ffc6a033850

事件哈希链检查：True
本地哈希链不抵御拥有数据库修改权限的管理员重新计算整个链。

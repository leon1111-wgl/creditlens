# 权威数据登记与适用边界

更新：2026-10-25。来源权威与任务适用分开判断：公司官网适合核对披露数值，不自动提供违约标签；论文基准适合特定评测，不等于完整企业尽调数据。

## 已接入

| 来源 | 实际内容 | 核验与用途 | 限制 |
|---|---|---|---|
| Microsoft Investor Relations | FY2025 Q4 三张财务表，FY2025/2024 共 20 项事实 | 官方 URL、概念定位、显示值、单位/期间、哈希及下载时间；复核案例 | 一家公司、专用 HTML 解析、不含完整附注 |
| FinQA 作者仓库 | dev 883 题 | 固定 commit、SHA-256、隔离 gold；检索与引用算术 | 不能评测中文全报告、授信或预测；缺公告日期 |

微软来源：[资产负债表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/balance-sheets)、[现金流量表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/cash-flows)、[利润表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/income-statements)。披露日期依据 [2025-07-30 新闻稿](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q4/press-release-webcast)。原网页保存在本机 data/raw/，仓库只保存最小数值事实、定位及 [来源 manifest](../data/examples/msft-provenance.json)。

`python scripts/build_company_pack.py` 重建数据；概念缺失、重复或年份不符即报错。抓取哈希证明本次材料版本，不证明 2025 年当天网页相同。FY2024 数值是 FY2025 披露中的比较数，不能倒填为 2024 年已知事实。

FinQA：[作者仓库](https://github.com/czyssrs/FinQA)、[EMNLP 论文](https://aclanthology.org/2021.emnlp-main.300/)、[固定版本登记](../data/manifest.json)。原始基准单独下载；仓库 MIT 与源报告权利分开处理。

## 下一批候选，尚未导入

1. **SEC EDGAR**：提交历史、XBRL 和原始 filing 建多公司面板，保留 accession、form、filed/accepted 时间、财务期间、单位和修订关系。今天聚合的 companyfacts 不能直接当成历史快照。[官方 API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
2. **交易所及公司公告**：中文财报、债券募集说明书、更正公告，逐份登记时间与使用范围；商业研报合集不作默认语料。
3. **OmniDocBench、TAT-QA、FinMME 作者基准**：分别测试版面表格、数值、多模态；先锁版本与核对数据许可。代码许可不自动覆盖数据和模型。
4. **贷款合同/内部授信**：需授权或明确标记的模拟材料，不使用泄露合同。

## 接入门槛

记录提供者、官方 URL、版本、检索日、哈希、期间、公告时间、币种数量单位、实体范围、许可、用途与缺失项。缺失不能置零，原始符号与归一转换保留。关键冲突阻断计算和提交。

训练与评测按公司、报告及时间隔离，控制重发、修订和近重复；人工答案记录证据及复核，模型输出仅作候选。预测另外需要合格标签、观测窗口、数据可得时点与幸存者偏差审查。

用户上传的事实包只做格式和一致性校验，**不自动证明来源或材料真实性**。界面明确 user_supplied，复核员需查原文。

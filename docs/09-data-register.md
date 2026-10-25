# 权威数据登记与适用边界

更新：2026-10-25。来源权威与任务适用分开判断：公司官网适合核对披露数值，不自动提供违约标签；论文基准适合特定评测，不等于完整企业尽调数据。

## 已接入

| 来源 | 实际内容 | 核验与用途 | 限制 |
|---|---|---|---|
| Microsoft Investor Relations | FY2025 Q4 三张财务表，FY2025/2024 共 20 项事实 | 官方 URL、概念定位、显示值、单位/期间、哈希及下载时间；复核案例 | 一家公司、专用 HTML 解析、不含完整附注 |
| Microsoft 官方 10-K PDF | FY2025 文件 158 页；固定第 50、52、53 页，20 项事实 | 页码与金额/表头坐标，哈希及发行人 filing 日期；对照 HTML | 同发行人两种格式，不是独立真值；已有开发页 OCR 核对，无通用表格识别 |
| Apple FY2025 官方 10-K | 80 页文件中物理页 32、34、36，22 项事实 | 固定 SHA-256、分行日期、重复行分区、周历财年；HTML 参考记录 | 开发者整理参考、无双人复核；不是保留测试或真实性鉴定 |
| FinQA 作者仓库 | dev 883 题 | 固定 commit、SHA-256、隔离 gold；检索与引用算术 | 不能评测中文全报告、授信或预测；缺公告日期 |
| Microsoft FY2025 Q1–Q4 官方现金流量表 | 18 项原始流量事实，覆盖单季和累计值 | 严格表头／年份／期间校验；四份现金流页面与四份新闻稿分别保存 URL、哈希、抓取日；公告后次日可用 | 当前网页抓取，不是历史归档；跨披露差分始终待复核，不能用于现阶段预测训练 |

微软来源：[资产负债表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/balance-sheets)、[现金流量表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/cash-flows)、[利润表](https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/income-statements)。披露日期依据 [2025-07-30 新闻稿](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q4/press-release-webcast)。原网页保存在本机 data/raw/，仓库只保存最小数值事实、定位及 [来源 manifest](../data/examples/msft-provenance.json)。

`python scripts/build_company_pack.py` 重建数据；概念缺失、重复或年份不符即报错。抓取哈希证明本次材料版本，不证明 2025 年当天网页相同。FY2024 数值是 FY2025 披露中的比较数，不能倒填为 2024 年已知事实。

FinQA：[作者仓库](https://github.com/czyssrs/FinQA)、[EMNLP 论文](https://aclanthology.org/2021.emnlp-main.300/)、[固定版本登记](../data/manifest.json)。原始基准单独下载；仓库 MIT 与源报告权利分开处理。

季度样例来源与日期：[Q1 / 2024-10-30](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q1/press-release-webcast)、[Q2 / 2025-01-29](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q2/press-release-webcast)、[Q3 / 2025-04-30](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q3/press-release-webcast)、[Q4 / 2025-07-30](https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q4/press-release-webcast)。原始流量表 URL 与各自哈希在 [事实包](../data/examples/msft-fy2025-temporal.json)，新闻稿与版本在 [登记](../data/examples/msft-temporal-provenance.json)。使用 `python scripts/build_temporal_pack.py --download` 重建；只有数字和定位进入公开仓库。公告当日排除是保守的数据政策，不意味着已取得精确发布时刻。与年度／PDF 样例存在重叠，不将 18 与 42 直接相加宣称独立事实总数。

## 下一批候选，尚未导入

1. **SEC EDGAR**：提交历史、XBRL 和原始 filing 建多公司面板，保留 accession、form、filed/accepted 时间、财务期间、单位和修订关系。今天聚合的 companyfacts 不能直接当成历史快照。[官方 API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
2. **交易所及公司公告**：中文财报、债券募集说明书、更正公告，逐份登记时间与使用范围；商业研报合集不作默认语料。
3. **OmniDocBench、TAT-QA、FinMME 作者基准**：分别测试版面表格、数值、多模态；先锁版本与核对数据许可。代码许可不自动覆盖数据和模型。
4. **贷款合同/内部授信**：需授权或明确标记的模拟材料，不使用泄露合同。

## 接入门槛

记录提供者、官方 URL、版本、检索日、哈希、期间、公告时间、币种数量单位、实体范围、许可、用途与缺失项。缺失不能置零，原始符号与归一转换保留。关键冲突阻断计算和提交。

训练与评测按公司、报告及时间隔离，控制重发、修订和近重复；人工答案记录证据及复核，模型输出仅作候选。预测另外需要合格标签、观测窗口、数据可得时点与幸存者偏差审查。

用户上传的事实包只做格式和一致性校验，**不自动证明来源或材料真实性**。界面明确 user_supplied，复核员需查原文。

## 控制场景数据

`scripts/evaluate_controls.py` 从官方事实包复制出 14 个人工变体，明确标记 `synthetic_counterfactual`，逐项记录输入哈希，不修改官方样本或本地原始材料。与 1 个官方参考共同构成 15 个回归场景；这些副本用于验证程序拒绝与阻断机制，不是实际公司披露，也不用于模型训练或真实错误率估计。

导入时已核对 `raw_value` 与归一 `value` 的数值和符号关系。若两者同时被错误修改仍可能通过，因此该检查不替代原页定位、来源核对或人工复核。

## 已接入 PDF 的登记

[官方 filing 元数据](https://microsoft.gcs-web.com/node/33951) 对应 [10-K PDF](https://microsoft.gcs-web.com/static-files/9640e86d-a99f-4502-934d-1d3c883fa786)。公开仓库保存 [最小事实与坐标](../data/examples/msft-fy2025-pdf.json)、[来源/许可限制](../data/examples/msft-pdf-provenance.json) 和 [逐项核对结果](../artifacts/pdf-crosscheck.json)，不保存完整 PDF 或渲染原页。抓取时间单独记录；filing 日期不是当前 PDF 字节版本的历史存在证明。

三个财务表共十个指标、两个年度。2023 列只用于检查表头与列布局，不进入案例包。数量级来自表头，USD 币种来自发行人报表口径配置。新发行人、版式或文件哈希需要重新核验。

苹果材料登记见 [来源与许可](../data/examples/aapl-pdf-provenance.json)，[参考值](../data/reference/aapl-fy2025-reference.json) 记录 10-K accession、HTML 哈希与获取日期；[核对结果](../artifacts/aapl-pdf-crosscheck.json) 保存旧表头适配器拒绝原因。披露日采用 SEC filing 的 2025-10-31，不能把较早新闻稿日期用于这份 10-K。物理页 32／34／36 与印刷页 29／31／33 分开。参考值在解析后用于核对，不进入运行时工作台或金额定位。

## 图像识别模型与衍生材料（2026-10-25）

v0.6 复用上述两个官方 PDF 的原始文件、公告日、财务期间及哈希，来源未更换。新增 [配置登记](../configs/document-ai.json) 和 [运行记录](../artifacts/document-ai-report.json)：RapidOCR 3.10.0，PP-OCRv6 small 检测／识别权重，SHA-256、制品 URL、Apache-2.0 模型归属声明、实际 CPU provider、包版本和渲染尺寸。

完整无标记页图、完整 OCR 结果保存在本地 .run/document-ai/；公开记录只保留必要字段观察与原页坐标。39 项一致、3 项不足是已有开发事实的核对结果，不是盲测或通用识别准确率。原始材料权利与模型许可独立，见 [第三方说明](../THIRD_PARTY_NOTICES.md)。后续新公司或扫描件必须另登记和标注，不把合成退化数据冒充真实材料。

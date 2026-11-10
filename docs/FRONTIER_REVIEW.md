# 前沿调研

调研按时间倒序排列：先是 v0.35.0（截图核对与标题规则修正），然后是 2026-11-10 的 v0.34.0（XBRL 对照）与“大公司在生产中怎样用”，最后是 2026-11-07 的 v0.33.0（页内问证据）原文，未改动。

## v0.35.0 · 截图核对（核对日期 2026-11-10）

本轮问题：v0.34.0 的候选 4（浏览器内 OCR + 勾稽校验）需要一组“答案已知”的页面来测误报率，XBRL 对照的已发布报表页正好是这样一组页面：数值和位置都由发行人的 XBRL 标签给出。

| 候选 | 来源与日期 | 结论 |
|---|---|---|
| 1. 浏览器内 PP-OCRv5 mobile + 按读数加总核对 | PaddlePaddle PP-OCRv5 mobile（Apache-2.0）；ONNX 版 `webnn/PP-OCRv5-ONNX`（2025-12）；onnxruntime-web 1.30.0（MIT，2026-09-14） | **采用**，按预先登记的门槛决定是否上线，见[截图核对](46-table-check.md) |
| 2. 过宽的股数／每股标题规则（v0.34.0 已知缺陷） | 本项目首轮记录 | **修正**为 score-2，在新的保留集（规则 c）上与 score-1 配对测量，见[XBRL 对照](45-xbrl-grounding.md) |
| 3. 多线程 WASM（coi-serviceworker 提供跨源隔离） | coi-serviceworker（MIT）；ORT Web 文档 | 不采用：单线程每页耗时已在门槛内；服务工作线程会改变整个站点的请求行为，收益不值这一层复杂度 |
| 4. WebGPU | ORT Web WebGPU EP | 不采用：Actions 运行器没有 GPU，无法验证；上线路径必须是测过的 WASM |
| 5. 直接从 Hugging Face 取权重 | — | 不采用：Hugging Face 的 WAF 对 Actions 上的无头浏览器返回人机验证页（HTTP 405，`x-amzn-waf-action: captcha`），这条路径无法测试；构建时从固定修订复制到本站并核对哈希，页面再核对一次 |
| 6. 表格结构模型（如 SLANet、TableFormer） | PaddleOCR 表格识别 | 不采用：额外下载与浏览器内后处理；本轮只处理干净的屏幕截图，行列可由框的几何关系重建，先测这条更简单的路径 |

## 大公司在生产中怎样用（v0.35.0 补充）

| 实践 | 来源（核对 2026-11-10） | 要点 |
|---|---|---|
| 审计中的重新计算（footing、cross-footing） | [IAASB ISA 500《审计证据》A19 段（IFAC 2013 手册版）](https://www.ifac.org/_flysystem/azure-private/publications/files/A022%202013%20IAASB%20Handbook%20ISA%20500.pdf) | “重新计算”是审计证据程序之一：核对文件或记录的数学准确性，可手工或电子执行。纵向加总（footing）与横向加总（cross-footing）是它最常见的形式 |
| AWS Bedrock Data Automation 的文档输出 | [标准输出文档，核对 2026-11-10](https://docs.aws.amazon.com/bedrock/latest/userguide/bda-output-documents.html)；[发布公告，2024-12-04](https://aws.amazon.com/about-aws/whats-new/2024/12/amazon-bedrock-data-automation-available-preview/) | 文档按所选粒度输出文本与表格，可选附带每个元素的定位框（bounding box）；公告称抽取附带视觉定位与置信度 |

**本轮对照的做法。** 截图核对把两件事放在一起：抽取的每个数字都带框（同 BDA 的“字段 + 定位框”），再对读出的数字做审计式的重新计算（纵向与横向加总、同比重算）。核对结果只说明“按读数算不平”，不推断哪个数字是对的。

**有意的不同。** 没有置信度阈值转人工的队列；OCR 分数只用来判断一格是否“读不出”。规则只认 Total／合计 等明确的合计行，不推断无标签的小计关系。只处理干净截图，手机照片、扫描件不在范围内。没有密钥、没有服务器：模型在访客浏览器里运行。

## v0.34.0 · XBRL 对照（核对日期 2026-11-10）

本轮问题：项目的文档取数只在微软、苹果等已经用来开发的材料上测过（[文档实验](13-document-ai-experiment.md)“下一步的验收条件”要求未见公司和独立答案键）。Inline XBRL 让每个 10-K 自带答案键：发行人给报表上的每个数字打了概念、期间、单位、decimals、scale 与 sign。只测“图像能否读回这些数字”，就能得到一个不依赖人工标注的保留集。

| 候选 | 来源与日期 | 结论 |
|---|---|---|
| 1. Inline XBRL 作答案键的定位基准 | Inline XBRL 1.1（2013-11）；SEC Financial Statement Data Sets（季度更新） | **采用**，见[XBRL 对照](45-xbrl-grounding.md) |
| 2. PaddleOCR-VL 0.9B 作第二取数器 | Hugging Face 模型卡（Apache-2.0，2025-10） | 不采用：首轮失败主要来自版面规则（并入的 $、分离括号、每股行量级），不是字符识别；没有测量理由支持在 Actions CPU 上加一个 0.9B 模型 |
| 3. Granite-Docling-258M | IBM 文档（Apache-2.0） | 不采用：输出 DocTags 结构，不直接给数字级定位框；理由同上 |
| 4. 浏览器内 OCR + 勾稽校验（访客上传表格截图） | PP-OCRv5 mobile，约 21 MB | 下一轮：需要先有本轮的已知答案页来测误报率 |
| 5. 问证据答案旁的 XBRL 交叉核对徽标 | SEC companyfacts API | 不采用：companyfacts 没有 CORS，且 SEC 拒绝 Actions 运行器（见下）；不是多模态 |
| 6. ColModernVBERT 视觉页面检索 | arXiv 2510.01149（MIT） | 不采用：浏览器端查询编码器没有 ONNX 版本，体积未测 |

**数据通道的一处变化。** SEC 对 GitHub Actions 运行器的请求一律返回 403“Undeclared Automated Tool”，与 User-Agent 和请求头无关（2026-11-09，运行 42710474268、42710749950）。因此基准不向 SEC 发请求：总体取自 SEC 自己发布的 2026q1 Financial Statement Data Set，文件取自互联网档案馆（Internet Archive）已有的 SEC 原地址抓取，逐个按 CDX 摘要核对字节，再核对文件内 dei:EntityCentralIndexKey 与文件类型。保留集规则在任何保留集运行之前提交（规则 a 于 `0354141`，因上述原因在运行前修订为规则 b 于 `1005fa2`）。

## 大公司在生产中怎样用（What large companies run in production）

| 实践 | 来源（核对 2026-11-10） | 要点 |
|---|---|---|
| AWS Bedrock Data Automation | [发布公告，2024-12-04](https://aws.amazon.com/about-aws/whats-new/2024/12/amazon-bedrock-data-automation-available-preview/)；[AWS 机器学习博客，2025-08-06](https://aws.amazon.com/blogs/machine-learning/process-multi-page-documents-with-human-review-using-amazon-bedrock-data-automation-and-amazon-sagemaker-ai) | 文档抽取附“visual grounding with confidence scores”；博客示例按字段置信度阈值（界面默认 70%）把低置信字段送到 SageMaker 人工复核 |
| Bloomberg 终端的文档问答 | [American Banker，2025-04-07](https://www.americanbanker.com/news/bloomberg-launches-ai-based-document-search-for-wall-street) | AI-Powered Document Insights：自然语言问公司文件，摘要与原文并排显示。研究笔记引用的 2025-06-16 Document Search & Analysis 新闻稿本次抓取被拒（403），未复核，不引用其数字 |
| ParseBench | [arXiv 2604.08538，2026-04-09](https://arxiv.org/abs/2604.08538) | 约 2,000 页人工核验的企业文档（保险、金融、政府），14 种方法，五个维度含视觉定位；作者结论：没有一种方法在五个维度上都稳定地强 |
| FinTagging | [arXiv 2505.20650，2025-05-27](https://arxiv.org/abs/2505.20650) | 用 XBRL 评测 LLM 的财务信息抽取与结构化：抽取表现尚可，细粒度概念对应（tagging）明显困难；arXiv 页未注明代码许可，这里只引用论文 |

**本轮对照的做法。** 生产系统卖的是“字段值 + 原文定位框 + 置信度，低于阈值转人工”。本轮按同一形状检查：每个 XBRL 事实一个框，图像读数附 OCR 片段、置信度和结果；规则读不完整时不作答（相当于转人工的那一档），而不是猜一个数。ParseBench 把视觉定位单列为一个维度，本轮的“定位召回”对应它；FinTagging 说明“打对标签”本身很难，所以本轮反过来把发行人自己的标签当答案键，只测图像读数。

**有意的不同。**

- 规模：6 家发行人、每家至多 4 页；数字聚集在少数公司里，不是独立样本。
- 方法：CPU 上的 RapidOCR 加确定性规则，没有大模型，没有经过校准的置信度；OCR 分数只作门槛。
- 材料：Chromium 渲染的干净页面，不是扫描件或手机照片；文件经互联网档案馆抓取取得，不直接来自 SEC。
- 复核：没有人工复核队列；“未作答”只说明规则停下了，并不代表有人看过。
- 无密钥、无付费 API：全部在 GitHub Actions 上运行，访客看到的是保存的运行结果。

### 本轮来源
1. Inline XBRL Part 1: Specification 1.1，2013-11-18：https://www.xbrl.org/specification/inlinexbrl-part1/rec-2013-11-18/inlinexbrl-part1-rec-2013-11-18.html
2. SEC Financial Statement Data Sets（季度更新；字段定义见 https://www.sec.gov/files/aqfs.pdf，afs 1-LAF = Large Accelerated）：https://www.sec.gov/data-research/sec-markets-data/financial-statement-data-sets
3. Amazon Bedrock Data Automation 预览公告，2024-12-04；AWS ML 博客，2025-08-06（链接见上表）
4. American Banker，2025-04-07（链接见上表）
5. ParseBench，arXiv 2604.08538；FinTagging，arXiv 2505.20650
6. PaddleOCR-VL 模型卡：https://huggingface.co/PaddlePaddle/PaddleOCR-VL ；Granite-Docling：https://www.ibm.com/granite/docs/models/docling ；ColModernVBERT：https://arxiv.org/abs/2510.01149
7. Internet Archive Wayback CDX 服务器 API：https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server

---

## v0.33.0 · 页内问证据（2026-11-07）

本轮范围：CreditLens 静态演示新增浏览器内问答框。访客输入中文问题，标签页内的 TypeScript 规则引擎以封闭词表解析，只从已登记的数字作答，并给出出处读数；不使用服务器、密钥或模型。
候选来源于三类：财报问答基准、财务数据的标准化标识（XBRL）、弃答与引用评测方法，另加两项“不采用”的对照（浏览器内小模型、文档 AI）。
每项均回到一手来源（arXiv、ACL Anthology、官方规范或官方仓库）核对；核对日期 2026-11-07。无法在本次抓取中直接核实的细节在各节和“来源”中单独标注。
本文不包含 CreditLens 的实测结果；“采用”指评测与展示方式的设计借鉴，实测数字及其测量对象见[页内问证据](44-browser-answers.md#评测)。

| 候选 | 来源与日期 | 要点 | 结论 |
|---|---|---|---|
| 1. TAT-QA | arXiv 2105.07624；ACL 2021（2021-08） | 答案带 scale 字段，评分器把数值与 scale 一并比较 | 采用 |
| 2. FinQA / DocFinQA | arXiv 2109.00122（EMNLP 2021，2021-09）；arXiv 2401.06915（2024-01） | 金标推理程序，逐步可解释 | 部分采用（只采用“展示程序”） |
| 3. FinanceBench | arXiv 2311.11944（2023-11） | 10-K 问答上 LLM 错答/拒答率高 | 采用（评测经验） |
| 4. XBRL / Inline XBRL / SEC 数据集 | XBRL 2.1（2013-02 勘误版）；Inline XBRL 1.1（2013-11）；SEC FSN 数据集（页面更新 2026-10-31） | 事实 = 概念 + 实体 + 期间 + 单位 + decimals | 采用（答案键的形状） |
| 5. TrustSQL / SQuAD 2.0 | arXiv 2403.15879（2024-03）；arXiv 1806.03822（2018-06） | 惩罚式可靠性分数；不可回答题 | 采用 |
| 6. ALCE | arXiv 2305.14627（EMNLP 2023，2023-05） | 带引用生成与引用质量评测 | 采用（严格形式） |
| 7. WebLLM 浏览器内小模型 | github.com/mlc-ai/web-llm，v0.2.87（2026-10，页面未标年份） | WebGPU 加速的浏览器端推理 | 不采用 |
| 8. ColPali / Docling | arXiv 2407.01449（2024-06）；arXiv 2408.09869（2024-08） | 页面图像检索；PDF 版面与表格转换 | 不采用 |

### 1. TAT-QA：答案同时带数值与 scale
- 是什么：Zhu 等人构建的财报表格加文本混合问答数据集，要求数值推理；论文发表于 ACL-IJCNLP 2021。其官方评测脚本把预测的 scale 与金标 scale 分别折算后比较，scale 错误会使 EM/F1 下降，并单独统计 scale 准确率（依据官方仓库 tatqa_metric.py；论文页面本身未描述 scale 字段，该细节取自仓库代码）。
- 结论：采用。本演示的核心风险正是“数字对、单位量级错”（千、百万、十亿美元），与 TAT-QA 的扣分逻辑一致。
- 落地：每条读数都带印刷单位表头（如 “(In millions)”）；访客要求的美元量级（亿美元、十亿美元、万美元、千美元、美元）用精确小数换算并与原值并列。盲测评分把“要求的量级”作为答案的一部分：数字对而量级不符记为错误（`web/tests/blind.ts`）。

### 2. FinQA / DocFinQA：程序化数值推理
- 是什么：FinQA 为财务专家撰写的问答集，每题带金标推理程序，使答案可逐步解释；DocFinQA 将 7,437 道 FinQA 题扩展为整份文档上下文的长文档任务。
- 结论：只采用“展示程序”的思想；不采用自由生成式推理。浏览器内没有模型，且财报数字错误代价高；规则引擎的计算本来就是固定公式。
- 落地：两期变化与毛利率都展示公式和每个输入的读数（页码、定位框、单位表头、OCR 核对结果），访客可以自己重算。公式固定在 Python 规则里（`research_materials._annual_change` 与新的时点规则），浏览器端是逐项一致的移植。

### 3. FinanceBench：10-K 问答的失败模式
- 是什么：含 10,231 道上市公司问答题及证据串的开放书基准；作者测试 16 种模型配置，报告带检索的 GPT-4-Turbo 有 81% 的题错答或拒答，并观察到幻觉。
- 结论：采用其评测教训，不采用其数据（本演示只覆盖两份年报）。
- 落地：盲测集由另一个模型只按文档、公司、期间和指标清单写成，58/146 题应拒答（币种、期间、口径、指标、主体、排名陷阱）；错答与弃答分开计数，不合并为一个“失败率”。

### 4. XBRL / Inline XBRL：答案键的形状
- 是什么：XBRL 2.1 规定 context 记录实体、期间、场景，单位由独立的 unit 元素给出，数值项带 decimals 属性；Inline XBRL 1.1 的数值标签带 contextRef、unitRef、decimals 与 scale（10 的指数，千元为 3）。SEC 的 Financial Statement and Notes 数据集抽取自 XBRL 附件，按月更新。
- 结论：采用其“形状”，不引入 XBRL 工具链或 SEC 数据。演示的数字来自已保存的 PDF 登记，并非 XBRL。
- 落地：答案键按“发行人 + 报表行 + 期间（时点或区间）+ 单位 + 范围”识别；资产负债表项用时点，利润表与现金流项用区间；范围（如仅母公司）对应维度；印刷量级相当于 scale/decimals。可比较性检查（同发行人、同报表行、相邻不重叠财年、同历法与长度、同单位）按这一键判定。
- 未核实项：以维度（legal-entity axis）表达母公司口径所依据的 XBRL Dimensions 规范，本次未单独抓取；此处仅引用 2.1 规范中 context 的 scenario/segment 结构。

### 5. 弃答感知评分：TrustSQL 与 SQuAD 2.0
- 是什么：TrustSQL 用惩罚式可靠性分数 RS(c)：可答题答对 +1、答错 −c、弃答 0；不可答题弃答 +1、作答 −c。论文评估 c = 0、10 与 N 三档。SQuAD 2.0 加入 5 万余道看似可答的不可回答题，要求系统在证据不足时弃答。
- 结论：采用 RS(0)/RS(10) 报告方式。
- 落地：评分规则先于首轮运行提交；首轮盲测标注所在提交，之后的重跑标为“已看过”。报告正确、错答、弃答以及 RS(0) 与 RS(10)；错答在 RS(10) 下被重罚，与“错数字是最坏结果”的原则一致。

### 6. ALCE：引用评测
- 是什么：Gao 等人提出的带引用生成基准，评估流畅度、正确性与引用质量；摘要指出最佳系统在 ELI5 上约一半时间缺乏完整的引用支持（引用精确率/召回率的具体定义在正文，本次只核对了摘要）。
- 结论：采用严格形式，不照搬其 NLI 自动判定（无模型）。
- 落地：答案标题里的每个数字，必须是某条读数的登记值（带页码、定位框、单位表头与 OCR 核对结果）、该值的精确量级换算、列出公式的派生值，或同一读数中的年份、日期、页码与天数；`web/tests/compose.test.ts` 对覆盖集的每个问题逐条检查，出现其他数字即失败。

### 7. 浏览器内小模型：WebLLM
- 是什么：MLC 的 WebLLM 是借助 WebGPU 加速的浏览器端推理引擎；官方文档称需要支持 WebGPU 的浏览器并推荐最新版 Chrome。其 model_list 含 Qwen3-0.6B-q4f16_1-MLC（约 1.4 GB 显存估计）。最新发布为 v0.2.87（发布页只标“30 Oct”，年份按上下文判断为 2026，未直接核实）。
- 结论：不采用。组合内另一项目的实际运行曾在无 GPU 的运行器上失败（约 11 分钟后语法匹配器拒绝已采样的 token），在 150 道盲测题中至多增益 1 题（该数据来自同一作品集的内部记录，非本文来源）。本演示中数字准确性优先，模型至多只能“挑选证据”，而封闭词表已覆盖挑选；加载上 GB 级权重且依赖 WebGPU，对访客收益不成比例。
- 落地：不加载模型，第二层（点击加载的浏览器模型）保持关闭。页面与答案标注“页内规则引擎 · 不是模型”。只有当某项测量能说明模型会改变结果（例如封闭词表的弃答中有可由模型安全“挑选”的部分）时才重新评估。

### 8. 文档 AI：ColPali 与 Docling
- 是什么：ColPali 用视觉语言模型对页面图像做多向量检索，并提出 ViDoRe 基准（ICLR 2025）；Docling 是 MIT 许可的 PDF 转换包，含版面分析与表格结构识别。
- 结论：不采用。本演示不发布发行人页面图像，也不重跑 OCR，数字来自已登记的读数；访客感知不到差别。
- 落地：无。

### 来源
1. TAT-QA: A Question Answering Benchmark on a Hybrid of Tabular and Textual Content in Finance，2021-05（ACL 2021，2021-08）：https://arxiv.org/abs/2105.07624 ；https://aclanthology.org/2021.acl-long.254/
2. TAT-QA 官方仓库（评测脚本 tatqa_metric.py，scale 处理）：https://github.com/NExTplusplus/TAT-QA
3. FinQA: A Dataset of Numerical Reasoning over Financial Data，2021-09（EMNLP 2021）：https://arxiv.org/abs/2109.00122
4. DocFinQA: A Long-Context Financial Reasoning Dataset，2024-01（v3 2025-10）：https://arxiv.org/abs/2401.06915
5. FinanceBench: A New Benchmark for Financial Question Answering，2023-11：https://arxiv.org/abs/2311.11944
6. XBRL 2.1 规范（勘误版 2013-02-20；原 Recommendation 2003-12-31）：https://www.xbrl.org/Specification/XBRL-2.1/REC-2003-12-31/XBRL-2.1-REC-2003-12-31+corrected-errata-2013-02-20.html ；规范索引：https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
7. Inline XBRL Part 1: Specification 1.1，2013-11-18：https://www.xbrl.org/specification/inlinexbrl-part1/rec-2013-11-18/inlinexbrl-part1-rec-2013-11-18.html
8. SEC Financial Statement and Notes Data Sets（页面更新 2026-10-31）：https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets
9. TrustSQL: Benchmarking Text-to-SQL Reliability with Penalty-Based Scoring，2024-03（v6 2024-07）：https://arxiv.org/abs/2403.15879 ；https://arxiv.org/html/2403.15879v6
10. Know What You Don't Know: Unanswerable Questions for SQuAD，2018-06（ACL 2018）：https://arxiv.org/abs/1806.03822
11. Enabling Large Language Models to Generate Text with Citations（ALCE），2023-05（EMNLP 2023）：https://arxiv.org/abs/2305.14627
12. WebLLM 仓库与发布页（v0.2.87）：https://github.com/mlc-ai/web-llm ；https://github.com/mlc-ai/web-llm/releases
13. WebLLM 官方文档（WebGPU 浏览器要求）：https://webllm.mlc.ai/docs/user/get_started.html
14. WebLLM model_list（含 Qwen3-0.6B-q4f16_1-MLC）：https://raw.githubusercontent.com/mlc-ai/web-llm/main/src/config.ts
15. ColPali: Efficient Document Retrieval with Vision Language Models，2024-06（ICLR 2025）：https://arxiv.org/abs/2407.01449
16. Docling Technical Report，2024-08：https://arxiv.org/abs/2408.09869

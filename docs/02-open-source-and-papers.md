# 开源技术与论文调研

调研日期：2026 年 10 月 25 日。GitHub stars 取自当次官方 REST API 返回值，是关注度快照，不代表安全性、适用性或企业成熟度。完整元数据见 repo-snapshot.json；README 用于确认功能和维护说明。下列项目尚未全部经过安装压测或逐行代码审计。

## 开源选型

| 项目 | Stars 快照 | 可借鉴方法 | 本项目决策 |
|---|---:|---|---|
| [vLLM](https://github.com/vllm-project/vllm) | 95,324 | PagedAttention、连续批处理、前缀缓存 | 本地推理服务候选；用真实长度和并发测容量 |
| [RAGFlow](https://github.com/infiniflow/ragflow) | 92,807 | 复杂文档解析、模板切块、检索和引用工作流 | 对照产品与解析参考；不把整套平台作为隐藏黑箱 |
| [MinerU](https://github.com/opendatalab/MinerU) | 82,417 | 中文复杂文档解析和稳定定位 | 与 Docling 在同一批中文报表上比较；逐组件审查授权 |
| [LlamaFactory](https://github.com/hiyouga/LlamaFactory) | 75,710 | 配置驱动 SFT、LoRA、QLoRA、DPO | SFT 主候选，固定依赖和模型版本 |
| [Docling](https://github.com/docling-project/docling) | 69,507 | 统一文档表示、布局、表格、OCR、XBRL | 首选解析候选；金融表格仍需要抽样验收 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 43,832 | 状态持久化、失败恢复、人工中断 | 完整 Agent 阶段使用；首版先把业务契约跑通 |
| [GraphRAG](https://github.com/microsoft/graphrag) | 36,433 | 实体关系、分层摘要、全局查询 | 只对担保关联等多跳问题做实验 |
| [Langfuse](https://github.com/langfuse/langfuse) | 36,351 | trace、prompt 版本、实验、标注反馈 | 后续可观测性候选，先核查各目录许可证 |
| [Qwen3](https://github.com/QwenLM/Qwen3) | 27,682 | 开源中文模型生态与部署示例 | 4B 作为可控训练起点，不声称最新最强 |
| [verl](https://github.com/verl-project/verl) | 24,001 | 训练与 rollout 解耦、可验证奖励、RL 数据流 | RL 扩展参考；分布式训练需由实际负载验证 |
| [FinGPT](https://github.com/AI4Finance-Foundation/FinGPT) | 21,456 | 金融数据处理、指令微调、任务基准 | 借鉴数据与实验组织，避免用情绪任务替代信用任务 |
| [TRL](https://github.com/huggingface/trl) | 19,596 | SFT、DPO、GRPO trainer 与 PEFT 集成 | 小规模对齐实验候选，与 LlamaFactory 分工 |
| [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot) | 8,294 | 金融工具、计算与叙述分离、研究流程 | 主要功能对照；突出信用口径与时点回放 |

选型中需要注意的当前变化：

- GraphRAG README 已说明主要进入维护阶段，不再常规接收新功能 PR；其索引成本也需要控制。关系图不是默认替代混合检索的方案。
- FinRobot README 区分了原始 AutoGen 教学实现、Web 版与桌面版。不能沿用旧教程就认为复现了它当前的系统。
- LlamaFactory 和 verl 的仓库地址已发生迁移，上表使用本次确认的地址。
- GitHub API 对 MinerU 和 Langfuse 返回的许可证标识为 NOASSERTION，不能等同于“没有许可限制”。在采用代码、权重与服务前分别核查 LICENSE、子目录和模型卡。
- stars、最后推送时间和 README 声明只能辅助筛选。最终选型还要过：授权、固定版本、单机安装、10 个真实文档、失败恢复和性能测试。

## 从项目中抽取的实现方法

| 方法 | 采用方式 | 必须补上的验证 |
|---|---|---|
| 检索前过滤元数据 | tenant、权限、公司、公告时间先过滤，再召回 | 越权证据不能进入 reranker、日志和模型上下文 |
| 结构感知切块 | 标题、段落、整行表格及表头保留；保存定位 | 跨页表头、负数括号、单位在附注中的情况 |
| 混合召回与重排 | BM25 与多语言 dense 检索用 RRF 合并，按任务 rerank | 同一评测集、上下文长度和模型比较 |
| 状态化工作流 | 任务解析、检索、抽取、计算、验证、人工复核 | 中断恢复、重试幂等、最大调用次数 |
| 确定性数值工具 | 模型提出操作，代码读取有来源的事实并计算 | 除零、币种冲突、期间不一致、数据缺失 |
| 可复现训练 | 数据 manifest、模型 revision、代码 commit、配置、seed 一并保存 | test 隔离、重复样本、污染与训练退化 |
| 可观测性 | 每个动作记录输入引用、输出、时延、token 与错误类型 | 敏感内容最小化记录；日志不能成为泄漏通道 |

## 论文与可执行实验

下表将成熟方法与近期预印本分开使用。没有核实正式录用状态的条目按 arXiv 论文处理；论文中的收益只适用于其数据和实验，不代表 CreditLens 的实测收益。

| 论文 | 时间／来源 | 与项目相关的方法 | 本项目实验及限制 |
|---|---|---|---|
| [FinQA](https://aclanthology.org/2021.emnlp-main.300/) | EMNLP 2021 | 用可执行程序评价财务数值推理 | 先做原始块检索基线；不能把 gold program 执行分数算作模型回答分数 |
| [TAT-QA](https://arxiv.org/abs/2105.07624) | 2021 | 表格和文本共同支撑答案 | 测跨表文证据完整率；切勿只留表格 |
| [FinanceBench](https://arxiv.org/abs/2311.11944) | 2023 | 带来源证据的金融问答 | 公开样例是 150 题，不是论文全部 10,231 题；作为外部检查集 |
| [Self-RAG](https://arxiv.org/abs/2310.11511) | 2023 | 学习检索与反思信号 | 做证据验证组件消融；普通提示词复核不能叫复现 Self-RAG 训练 |
| [QLoRA](https://arxiv.org/abs/2305.14314) | 2023 | 量化冻结基座并训练低秩适配器 | 单卡做 4B/7–8B 试验；论文显存结果不是任意上下文的保证 |
| [DPO](https://arxiv.org/abs/2305.18290) | 2023 | 从 chosen/rejected 偏好对学习 | 同题同证据比较正确引用、恰当拒答；过滤含金标泄漏的偏好对 |
| [GraphRAG](https://arxiv.org/abs/2404.16130) | 2024 | 图组织与面向查询的全局摘要 | 仅对关联担保等任务比较增益和索引成本 |
| [Search-R1](https://arxiv.org/abs/2503.09516) | 2025 | 多轮检索交互 RL，检索文本掩码 | 封闭语料工具环境试验；不能把一般 QA 增益外推到信用分析 |
| [DeepResearcher](https://arxiv.org/abs/2504.03160) | 2025 | 在真实搜索交互环境中训练研究 Agent | 环境设计参考；开放网络 rollout 的成本和可重复性需单独评估 |
| [Finance Agent Benchmark](https://arxiv.org/abs/2508.00828) | 2025 | 专家金融研究任务与工具环境 | 评价跨文档任务；保持工具调用约束一致，历史模型成绩不作当前排名 |
| [FinSearchComp](https://arxiv.org/abs/2509.13160) | 2025-09 | 635 题，时效查询、历史查询与复杂调查，含大中华区 | 后期检查中文金融检索；动态题保存抓取时间和快照 |
| [Search-R1++ 研究](https://arxiv.org/abs/2602.19526) | 2026-02 预印本 | 拆分 prompt、reward、policy 的作用，分析拒答和训练崩塌 | 训练同时记录覆盖率、搜索数和失败模式；不把 GRPO 默认当成最优算法 |
| [FinFIRST](https://arxiv.org/abs/2609.25192) | 2026-09 预印本 | 用原子标准同时检查答案、来源与计算 | 123 个专家任务；借鉴证据层评分，本项目尚未完成此基准 |
| [FinSkillBench](https://arxiv.org/abs/2610.03564) | 2026-10 预印本 | 区分程序化工具与流程文档对金融任务的贡献 | 对照“只有模型／加文档／加工具／两者都有”；其受控美股任务不覆盖全部信贷业务 |
| [FinanceGym](https://financegym.github.io/) | 2026 项目页 | 指定截止日期研究与事前、事后分开评分 | 学习时点回放；页面说明数据为非商业许可，企业版本不能直接混入 |

近期重点读了 FinFIRST 的证据层指标、FinSkillBench 的方法与局限、Search-R1++ 的奖励崩塌分析；其余条目以论文摘要、官方说明和相关章节建立技术路线。训练阶段再对选中的一两条路线精读并复现实验，不把文献数量当成实验完成度。

## 研究问题

拟研究：**在相同基座和检索资源约束下，结构化证据约束加验证器反馈，能否提高金融任务的可信完成率，并降低错误自信回答？**

实验组先固定为普通 RAG、加确定性工具、加证据验证、加 SFT、加 DPO。小模型 RL 是条件扩展。每组报告答案正确率、证据充分率、拒答／覆盖率、成本与时延。这个组合可能具有工程价值，但是否具有学术新意需要进一步对比及真实实验。

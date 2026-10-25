### [CreditLens · 多模态金融研究与风险分析平台](https://github.com/leon1111-wgl/creditlens)

面向投研和风险团队，研究一家公司的经营状况如何变化、哪些风险值得关注、什么信息有助于判断未来。项目正在把财报、公告、历史数据和 AI 分析接起来，让每个判断都能找到依据、每次实验都能重新验证。

**已经能用：** 把微软和苹果的官方现金流材料放到一项研究中，逐项确认采用哪些数字、查看各公司自身的年度变化、记录还没查清的解释，再固定底稿交给同事复核。退回修订会保留旧版，口径不同时说明原因。原有工作台还能计算财务指标、按历史日期看季度变化；让识别模型读取财报图片，对照数字、年份和单位，定位看错或看不清的地方，并比较不同识别方式的结果。目前可在本机运行。

**正在推进：** 补齐现金流调节项和附注解释，扩充口径可对齐的同行与多季度材料，逐步回答经营变化的原因；继续完善扫描件、跨页表格、图表理解和公告变化跟踪，再提供在线演示。

**研究方向：** 联合数字、文字和图表，研究现金流时序预测、金融因子、企业关系和风险变化，再探索计入交易成本的投资组合。先建立简单基线，再用未参与调参的数据检验 AI 方法的增量价值；论文依据、实验设计和失败结果持续公开。

当前使用历史披露样例，图像识别已在本机 CPU 实际运行；通用多模态问答、自动公告更新、实时行情和模型预测仍在建设或研究中。现有功能不需要租训练服务器。

[源码与复现](https://github.com/leon1111-wgl/creditlens) · [通俗制作报告](https://github.com/leon1111-wgl/creditlens/tree/main/docs/iterations) · [平台设计与论文分析](https://github.com/leon1111-wgl/creditlens/blob/main/docs/11-financial-research-platform.md)

[企业需求与技术实现：通俗版](https://github.com/leon1111-wgl/creditlens/blob/main/docs/12-project-story-and-technology.md) · [完整项目与技术描述](https://github.com/leon1111-wgl/creditlens/blob/main/docs/14-resume-and-interview.md) · [新公司文档诊断](https://github.com/leon1111-wgl/creditlens/blob/main/docs/17-new-issuer-transfer.md) · [日期关联与原始证据核验](https://github.com/leon1111-wgl/creditlens/blob/main/docs/18-date-association.md) · [固定规则后的AMD检查](https://github.com/leon1111-wgl/creditlens/blob/main/docs/19-issuer-evaluation.md) · [并行迭代](https://github.com/leon1111-wgl/creditlens/blob/main/docs/15-parallel-iteration.md)

[港大公开项目与产业合作参照](https://github.com/leon1111-wgl/creditlens/blob/main/docs/research/2026-10-25-hku-industry-projects.md) · [研究任务与版本交接](https://github.com/leon1111-wgl/creditlens/blob/main/docs/20-research-tasks.md)

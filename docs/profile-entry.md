### [CreditLens · 企业信用复核与证据智能](https://github.com/leon1111-wgl/creditlens)

把官方财务披露转成可追溯事实，校验数字口径，记录核查依据，再交给独立复核身份确认。当前 v0.3 包含官方 HTML/PDF 披露案例与原页定位、确定性计算、API-key 与租户约束、事件记录及 70 项测试与 15 个财务控制回归场景；通用多模态文档理解、真实模型评测与预测是后续研究主线。

`Financial evidence` · `Review workflow` · `Risk factors` · `Evaluation` · `FastAPI`

可在本地运行，无需 GPU。FinQA dev 883 题页内证据 Recall@5 为 82.43%，并非端到端回答准确率。可选模型接口已实现，真实模型质量与微调尚未评测。

[源码与复现](https://github.com/leon1111-wgl/creditlens) · [通俗制作报告](https://github.com/leon1111-wgl/creditlens/tree/main/docs/iterations) · [研究设计](https://github.com/leon1111-wgl/creditlens/blob/main/docs/08-document-ai-and-governance.md)

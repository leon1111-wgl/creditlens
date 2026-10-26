# 重跑实验，同时保留可展示的版本

这份说明区分两件事：重新运行模型，检查自己的结果；把经过审查的新结果发布给工作台。前者默认只写本地目录，不改变页面展示的冻结实验。

## 本地完整复现

从仓库根目录运行，已按 [模型与许可说明](13-document-ai-experiment.md) 安装可选依赖及固定模型权重：

```bash
python scripts/evaluate_document_ai.py --download
python scripts/evaluate_word_evidence.py
python scripts/evaluate_visual_robustness.py
python scripts/check_document_reproduction.py
```

顺序执行，每条命令成功后再继续。第一条在原始材料缺失时仅下载已登记的官方 PDF，再生成完整干净页图；后两条读取这批新页图，各自保留完整 OCR 与结果。不读取云模型、不产生付费调用。

| 内容 | 默认本地位置 |
|---|---|
| 行级重跑报告 | `.run/reproductions/document-ai-report.json` |
| 字词级重跑报告 | `.run/reproductions/document-ai-word-report.json` |
| 图像退化重跑报告 | `.run/reproductions/visual-robustness-report.json` |
| 新页图和完整行级 OCR | `.run/reproductions/document-ai/` |
| 完整字词结果 | `.run/reproductions/document-ai-words/` |
| 完整退化图像和 OCR | `.run/reproductions/visual-robustness/` |

以上目录被 Git 忽略。三个重跑报告和完整输出都与现有工作台使用的冻结文件隔离。脚本会在下载和模型推理前检查输出位置；不能把输出指向 `artifacts/`、`configs/`、`data/` 等发布或来源目录，也不能通过路径别名绕过。它是防误写检查，不是多用户操作系统沙箱。

## 怎样读对照结果

`check_document_reproduction.py` 只读三份候选报告与已发布实验，核对来源、配置、页面、事实和检查字段，再比较状态、观察文字、坐标、父行、保护规则及证据摘要。不能只因两次都得到“39 项一致”就称为相同。

- `matched`：被比较的公开证据和语义结果匹配；时间、耗时、实现或环境等运行记录的变化另外列出。
- `changed`：候选报告完整有效，但状态或证据有变化，需要解释后再决定是否发布。
- `invalid`：文件缺失，或来源、配置、完整性、统计等检查失败。

退出码分别为 0、2、1。`matched` 不代表吞吐一致、不同环境等价、算法正确或通用准确率。输入只有报告时，对照器没有读取完整原始 OCR，不能证明报告中的 OCR 哈希确由那些未提供的原始文件计算。需要核查原始输出时，应额外读取本地完整 OCR 逐字段复算。

可显式指定输入，以及一个尚不存在的本地输出文件：

```bash
python scripts/check_document_reproduction.py --output .run/reproduction-review.json
```

该命令不会修改冻结清单、替换网页数据或自动批准事实。结果发生变化时，保留所有失败，不挑选较好数字组成一份新成绩。

## 网页原页预览

`/experiments` 直接展示已发布记录，不需要模型或完整原页。`/documents` 的原页图片由本机 `.run/document-ai/` 提供，首次缺图时可显式准备：

```bash
python scripts/evaluate_document_ai.py --download --local-dir .run/document-ai
```

输出报告仍进入独立复现目录。登记预览应匹配已冻结的图像摘要；已有相同图片保留，已有不同图片不静默覆盖。遇到版本或渲染差异，先改用默认独立目录查原因，不通过更新清单掩盖问题。

## 新结果怎样进入发布版本

先核对原始材料和许可、固定参考事实与实验配置，完成模型运行、原始输出复算及独立审查。通过验收后，在明确的版本变更中更新公开最小记录和冻结清单，并运行 API 与必要回归。当前工具没有自动发布或自动接受候选结果的功能。

既有研究报告中的时间、路径与结果记录属于各自运行时的事实；本说明是当前的安全复现入口。旧开发样例的重复运行不产生新保留集，也不能增加独立公司样本数。下一阶段应先冻结未用于调规则的公司与报告，再开展新文档评测。

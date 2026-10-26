# 原始数据按需取得，不让本机常驻大副本

2026-10-25。新增 [远端来源索引](../../data/remote-assets.json) 和 [按需下载脚本](../../scripts/fetch_registered_sources.py)。GitHub 保留小型代码、事实参考、来源与哈希；完整公司 PDF 继续从官方地址获取，FinQA 使用作者 GitHub 中已固定版本，不再额外镜像一份。

登记对象为微软、苹果、NVIDIA、AMD 四份已使用的官方 PDF，以及 FinQA dev 固定 revision。索引沿用现有事实包和实验配置的 SHA-256，没有重新选择文件，也没有把申报事件日期当作精确文件的历史可得时间。公开访问不等于取得再分发或训练许可；完整 PDF、完整 OCR 与页图不因加入索引而获准上传 GitHub。

## 需要时如何恢复

只看目录，不会下载：

```bash
python scripts/fetch_registered_sources.py --list
```

明确选择一个文件才会开始网络请求：

```bash
python scripts/fetch_registered_sources.py --asset msft-fy2025
python scripts/fetch_registered_sources.py --asset aapl-fy2025
python scripts/fetch_registered_sources.py --asset nvda-fy2025
python scripts/fetch_registered_sources.py --asset amd-fy2025
python scripts/fetch_registered_sources.py --asset finqa-dev
```

默认保存到 `.run/downloads/<asset-id>/<登记文件名>`。可用 `--output` 指定该缓存目录之下的新路径；不允许写到其他 `.run` 实验、原始数据、配置、数据库或公开文件夹，也不覆盖现有文件。成功时打印实际路径、文件大小、请求及最终地址、SHA-256 和许可提示。

这个工具只恢复已登记文件。它不会自动搬到旧脚本的默认 `data/raw` 路径、修改事实包或清单、重跑 OCR，也不恢复模型权重和完整识别结果。其他工具需要原文件时，应显式使用已下载路径；尚未支持路径参数的旧入口仍需按其现有说明准备输入，不能把下载成功写成所有复现都已恢复。官方地址失效或返回另一版本时会停止，不会猜一个替代地址或更新冻结哈希。

## 下载控制

- 每个请求和重定向都必须是 HTTPS，且域名在该资产的预定名单中；不接受用户传入任意下载 URL。
- 同时检查声明大小和实际读取字节数。NVIDIA 上限 60,000,000 字节，其余文件按各自登记限额处理。
- 字节先在有上限的内存缓冲区校验 SHA-256，再检查 PDF 标记或 FinQA JSON、记录数及 Git blob；通过后才新建文件。
- 目标存在、路径逃出 `.run/downloads`、符号链接或目录别名被拒绝；写入使用独占创建，写入失败清理本次未完成文件。
- 不把当前下载时间当作历史可得证据，不调用收费 API，不加载模型，不读取账号密钥。

路径检查用于避免误写与已有目录别名，并不是抵御同机其他进程在检查后恶意替换目录的操作系统沙箱。

## 本实验验证与边界

运行 `python -m unittest tests.test_remote_assets -v`，11 项测试通过。测试使用模拟网络和临时目录，覆盖正确文件、SHA 不符、超限响应、非法重定向、终点域名复查、已存在文件、未知资产、缓存路径与别名、PDF/JSON 格式以及不选资产时禁止下载。另将索引逐项对照现有四个来源和 FinQA manifest。

本实验没有真实联网下载、删除本地文件或重新上传原始数据。恢复入口的 mock 测试不证明上游地址此刻仍在线。保留必要小型证据和来源索引可以减小常驻空间，但真实模型复现仍可能需要另外下载权重和重新生成识别结果。

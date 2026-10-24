# 运行、身份与本地模型接口

适用版本：v0.2，2026-10-25。当前验证范围是本机单进程原型；下述配置不等于完成金融机构生产部署验收。

## 运行模式

默认 `CREDITLENS_AUTH_MODE=demo`。请求头 X-Demo-Role 用于模拟 analyst、reviewer、viewer，共享 public-demo 工作区。只绑定 localhost，不把可切换角色的演示模式公开到网络。

`CREDITLENS_AUTH_MODE=api_key` 要求 CREDITLENS_PRINCIPALS 为 JSON 数组，每项包含 key_sha256、user_id、tenant_id、role。使用者在本地生成并安全分发随机密钥，服务器只配置 SHA-256，请求采用 Authorization: Bearer。不要使用简单密码，不在命令历史或 GitHub 保存真实密钥。示意见 [.env.example](../.env.example)；应用不会自动加载该文件。

服务端从密钥映射决定身份，客户端不能自报租户。缺配置时启动失败；错误或缺失密钥返回 401。访客不能写，分析员不能复核自己的工作，不同租户读取、修改和导出案件返回 404。API-key 模式关闭无租户业务语义的 FinQA 实验室接口。

生产扩展仍需 SSO/OIDC、密钥轮换、TLS、请求限流、可信代理配置、备份恢复和运维审查。TrustedHost 当前只允许 localhost、127.0.0.1 和测试主机名。

## 工作流接口

| 接口 | 用途 |
|---|---|
| GET /health、GET /api/session | 服务状态及已验证身份 |
| GET /api/reviews | 当前工作区案件 |
| POST /api/reviews/example | 从官方微软事实包建案 |
| POST /api/reviews | 严格 schema 导入事实包 |
| GET /api/reviews/{id} | 事实、计算、处置、事件与完整性 |
| POST /api/reviews/{id}/actions | disposition / submit / approve / return |
| GET /api/reviews/{id}/memo | Markdown 底稿 |
| GET /api/example-pack | 事实包模板 |

创建必须带 Idempotency-Key；同工作区同键同内容返回原案，不同内容返回 409。修改需带 revision；同一版本的并发写只有一个成功。待核查项需要至少 10 字依据，阻断性数据问题不能人工豁免。

复核完成仅表示底稿流程完成。复核员不能是创建、提交或参与处置的人。实质修正的事实包需重新建案，当前无自动修订关联。

## 存储与部署

CREDITLENS_STATE_DIR 默认 data/，保存 audit.sqlite 和 workflow.sqlite。数据库、原始材料、日志及密钥排除出 Git。事件链可检测未重算哈希的修改；数据库管理员可以重算全链，因此仍需外部不可覆盖日志或独立审计存储。

1 MB 请求检查在应用读取请求体后执行，部署还需网关流式大小限制与限流。基础 Host、跨来源写入与字段检查不代表安全审计完成。

[Dockerfile](../Dockerfile) 提供非 root 用户、状态目录与健康检查。当前环境无 Docker，**尚未构建或运行验证**。候选命令：`docker build -t creditlens .`，`docker run --rm -p 127.0.0.1:8765:8765 creditlens`。持久卷需允许 UID 10001 写入；上线前补测构建、持久化和恢复。

## 本地模型规划器

核心工作流不依赖模型。creditlens/planner.py 面向兼容 chat-completions / JSON Schema 的本地服务，例如 vLLM [structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs/)，具体版本兼容性需实测。

设置 CREDITLENS_MODEL_BASE_URL=http://127.0.0.1:8000/v1 与 CREDITLENS_MODEL 为实际服务模型名，重启后以 demo analyst 调用 POST /api/agent/analyze，输入 case_id、question、k 和可选 as_of。无配置返回 503，不自动下载模型、租卡或调用收费服务。

模型只能提出计算或证据不足；最多 12 步加减乘除。执行器重读原单元格，拒绝未召回来源、错误引用和任意代码；限制上下文、响应体积和时长。只接受 loopback HTTP，关闭环境代理与重定向。约束限制工具权限，不证明公式语义或金融判断正确。

模拟传输测试覆盖合法计划、虚构引用、非法操作、未来证据排除、坏响应和远程地址拒绝。**尚无真实模型推理分数或吞吐结果**。下一阶段固定模型 revision、提示词、工具预算和数据，比较规则、普通 RAG、受限工具基线，再决定是否微调。

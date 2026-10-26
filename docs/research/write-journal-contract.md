# 同一标签页的待确认写入恢复

规则：`research-write-journal-1`，2026-10-26。适用研究底稿、解释补充底稿的已有保存和动作接口。不涉及创建新任务，也没有新增服务端写接口。

## 为什么保留一条本地记录

服务端可能已经保存或批准，而浏览器没收到响应。上一轮的幂等回执允许原请求安全重试，但刷新页面会丢掉内存中的请求及幂等键。本轮在网络写请求前，将一条有界待确认记录放入当前标签页的 `sessionStorage`。刷新后重新连接同一身份、读取绑定底稿，再由操作者明确重试。

没有使用 `localStorage`，不承诺关闭标签页、跨设备或浏览器崩溃后的恢复。浏览器自身的会话恢复行为不由本模块保证。存储包含尚未确认的研究文字，页面应明确说明；它不是加密保险箱，也不能抵御同源脚本被攻破。

## 小型记录及白名单

`createResearchWriteJournal({namespace, storage?})` 的 `namespace` 只允许 `research` 或 `explanation`。生产默认懒读取 `sessionStorage`，测试可注入同接口存储。两页面的键分别为 `creditlens:research-write-journal:1:<namespace>`，彼此不覆盖。

记录精确包含：

```text
schema_version: 1
journal_version: research-write-journal-1
namespace: research | explanation
actor: {tenant_id, user_id, role}
intent: {id, path, readPath, method, label, revision, key, body}
```

`body` 保存原始 JSON 请求字符串，不重新生成或追随新读取的 revision。记录不保存 API key、认证头、token、会话对象的其他字段或服务端响应；操作者主动写在研究正文里的内容仍属于正文，不进行秘密扫描。

整个记录上限 64 KiB，原请求正文上限 48 KiB，按 UTF-8 编码长度计算。解析拒绝重复 JSON 键、非有限数、超过 12 层的嵌套、超过 4000 个节点以及未知字段。任务 ID 限安全字符，版本为正的安全整数。正文逐项检查已有两类 PATCH 和 action 模型的字段、列表长度、文字边界、动作、角色及复核快照摘要；无效字段的报错不回显上传正文。

目标仅允许以下四种组合，`readPath` 必须为相应对象的固定 GET 地址：

- `PATCH /api/research/tasks/{id}`
- `POST /api/research/tasks/{id}/actions`
- `PATCH /api/research/explanation-reviews/{id}`
- `POST /api/research/explanation-reviews/{id}/actions`

路径、对象 ID、操作名称及角色必须彼此一致，不接受绝对 URL、查询参数、编码路径、额外认证信息或其他 API。保存要求分析员；提交为分析员；确认和退回为复核员。浏览器的角色比较不是授权，服务端仍须重新鉴权及执行原有权限、贡献者隔离、版本和幂等规则。

## Journal 方法

`save(intent, actor)` 校验后先确认该页面键为空，再写入并回读完整原字符串。存储抛错、静默不写或回读不符均阻止当前网络请求。不覆盖已有待确认或损坏记录。

`read(actor)` 返回 `empty`、`ready`、`identity_mismatch`、`invalid` 或 `unavailable`。只有租户、用户、角色全部匹配，才返回有效请求内容；不匹配时不返回正文、原身份或任务元信息。错误及未知版本记录保留，不自动删除。

`remove(intent, actor)` 先比较完整规范记录，再删除并确认回读为空。旧完成响应不能删除已经被替换的新请求。`discard()` 是操作者明确清除本地记录的入口，也能清除损坏记录；删除失败继续报错，不能当成成功。清除本地记录不会撤销任何已完成服务端操作。

所有已知错误为 `ResearchWriteJournalError`，含固定 `code/message`。来源于浏览器的异常原文不直接拼入提示。

## Flow 接续接口

`createResearchWriteFlow` 增加可选 `journal`、`getActor`。未传 journal 的旧调用继续使用原内存流程；原62项页面控制保持通过。

- `start(...)`：先持久化，再发送原请求。保存失败会检查当前本地状态：确实为空时保留可编辑表单；存在有效原记录则恢复 pending；不可读、损坏或身份不匹配则阻断新写入，提供后续恢复或明确清除入口。此次 start 不发送网络请求。
- `restore()`：不发送请求。恢复成功返回 `{status:"restored",info}`，信息含 ID、GET 路径、操作名、原 revision、method、`restored/cleanupRequired/retryable`。坏记录返回受控状态，不包含正文。页面须先通过 `/api/session` 获取身份，再读取对应实体；不能只相信 storage 中的身份。
- `pendingInfo()`：只给上述概要，或者损坏/身份不匹配等状态。不得将旧请求中的 revision 显示成当前服务端版本。
- `retry()`：由明确用户动作触发，以原 key、原 body、原目标重试。收到回执后另行 GET 当前版本，回执本身不被渲染成最新底稿。
- `clear()`：身份切换丢弃活跃内存意图，并尝试仅清其对应记录。删除失败也清内存且不抛出，以免中断身份切换和旧页面清理；返回 `cleanup_failed` 供提示。若没有活跃 intent，不会暗删另一身份的或损坏的 journal。
- `discard()`：明确清本地记录并重置 pending。失败保留 pending，阻止新写入。

每次异步完成检查原 intent、页面 epoch 和当前身份。旧请求的迟到成功或失败不能清除新 intent，也不能用旧响应触发新身份的回读。`onCommit` 抛错时保留原请求；当前版本已回读并渲染但删除失败时仍保留 pending，提示安全重试原请求或明确清除本地记录。

## 能证明什么

本地校验只证明记录符合固定请求结构，不证明它最初由谁创建，也没有签名。持有同源存储写权限的人可生成另一条结构合法的记录；恢复后的写入仍依赖当前服务端授权，用户须明确重试。幂等语义由已有服务端事务回执提供，不是由 sessionStorage 提供。

验证脚本 `node scripts/check_research_write_journal.js` 直接加载实际两个 JavaScript 模块，使用 Node VM、模拟会话、存储故障及受控 Promise。47项控制涵盖八条写流程的刷新恢复、三项身份错配、坏路径/字段/版本/JSON、保存前失败与部分写入、读取/渲染失败、删除抛错或静默失败、迟到响应和双页面命名空间。它不是实际浏览器刷新、真实网络故障或真实用户验收；这些由整合页面另外验证。

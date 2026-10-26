/* Same-page recovery of an acknowledged or uncertain research write. */
'use strict';
function createResearchWriteFlow({api, getEpoch, onPending, onCommit, onNotice}) {
  let intent = null;
  const current = item => intent === item && item.epoch === getEpoch();
  function clear() { intent = null; onPending(false); }
  async function attempt() {
    const item = intent;
    if (!item) return;
    let stage = 'write';
    try {
      const receipt = await api(item.path, {
        method: item.method, headers: {'Idempotency-Key': item.key}, body: item.body
      });
      if (!current(item)) return;
      if (receipt.id !== item.id || !Number.isSafeInteger(receipt.revision)
          || receipt.revision <= item.revision) throw new Error('写入回执与本次请求不对应。');
      stage = 'read';
      const latest = await api(item.readPath);
      if (!current(item)) return;
      if (latest.id !== item.id || !Number.isSafeInteger(latest.revision)
          || latest.revision < receipt.revision) throw new Error('暂未读到已确认的服务端版本。');
      onCommit(latest);
      clear();
      onNotice('已确认“'+item.label+'”完成，当前显示服务端版本 '+latest.revision+'。'
        +(latest.revision > receipt.revision ? '此后还有更新；首次回执未覆盖新版本。' : ''));
      return latest;
    } catch (error) {
      if (!current(item)) return;
      const rejected = stage === 'write' && [400,401,403,404,409,413,415,422].includes(error.httpStatus);
      if (rejected) {
        clear();
        onNotice('本次请求被拒绝，本页草稿仍保留。若此前连接异常，仍需重新载入确认服务端版本；也可核对提示后修改。');
        throw error;
      }
      onPending(true);
      onNotice(stage === 'read' ? '操作已完成，但当前版本暂未读回。请重试原操作以确认结果。'
        : '尚未确认上次操作的结果。请重试原操作，避免重复保存或提交。');
      throw new Error('连接或响应异常；原请求已保留在当前页面，可点击“重试原操作”。');
    }
  }
  return {
    pending: () => intent !== null,
    clear,
    retry: attempt,
    async start({path, method, payload, readPath, id, label}) {
      if (intent) throw new Error('请先确认上次操作的结果。');
      intent = {path, method, body: JSON.stringify(payload), readPath, id, label,
        revision: payload.revision, key: crypto.randomUUID(), epoch: getEpoch()};
      onPending(true);
      return attempt();
    }
  };
}

/* Same-page recovery of an acknowledged or uncertain research write. */
'use strict';
function createResearchWriteFlow({api, getEpoch, onPending, onCommit, onNotice, journal=null, getActor=null}) {
  let intent = null, journalProblem=null;
  const current = item => intent === item && item.epoch === getEpoch();
  function stored(item) {
    return {id:item.id,path:item.path,readPath:item.readPath,method:item.method,label:item.label,
      revision:item.revision,key:item.key,body:item.body};
  }
  function pendingInfo() {
    if(!intent)return journalProblem?{...journalProblem,retryable:false}:null;
    return {status:intent.restored?'restored':'pending',id:intent.id,readPath:intent.readPath,label:intent.label,revision:intent.revision,
      method:intent.method,restored:!!intent.restored,cleanupRequired:!!intent.cleanupRequired,retryable:true};
  }
  function reset() { intent=null;journalProblem=null;onPending(false); }
  function clear() {
    const item=intent;let result={status:'cleared'};
    if(journal&&item)try{journal.remove(stored(item),item.actor);}catch(error){result={status:'cleanup_failed',code:error.code};}
    // Identity changes must erase active memory even if storage is inaccessible.
    reset();return result;
  }
  function finish(item) {if(journal)journal.remove(stored(item),item.actor);if(current(item))reset();}
  function sameIdentity(item) {
    if(journal&&!journal.sameActor(item.actor,getActor?.())){clear();throw new Error('当前身份已变化，原操作不能在此身份下重试。');}
  }
  function restore() {
    if(!journal)return {status:'empty'};
    if(intent){sameIdentity(intent);return {status:'restored',info:pendingInfo()};}
    const found=journal.read(getActor?.());
    if(found.status==='ready'){
      intent={...found.record.intent,actor:found.record.actor,epoch:getEpoch(),restored:true};
      journalProblem=null;onPending(true);return {status:'restored',info:pendingInfo()};
    }
    journalProblem=found.status==='empty'?null:{status:found.status,code:found.code,message:found.message};
    onPending(journalProblem!==null);return {...found};
  }
  async function attempt() {
    const item = intent;
    if (!item) {if(journalProblem)throw new Error('请先处理本地恢复记录，再发起写入。');return;}
    sameIdentity(item);
    let stage = 'write';
    try {
      const receipt = await api(item.path, {
        method: item.method, headers: {'Idempotency-Key': item.key}, body: item.body
      });
      if (!current(item)) return;
      sameIdentity(item);
      if (receipt.id !== item.id || !Number.isSafeInteger(receipt.revision)
          || receipt.revision <= item.revision) throw new Error('写入回执与本次请求不对应。');
      stage = 'read';
      const latest = await api(item.readPath);
      if (!current(item)) return;
      sameIdentity(item);
      if (latest.id !== item.id || !Number.isSafeInteger(latest.revision)
          || latest.revision < receipt.revision) throw new Error('暂未读到已确认的服务端版本。');
      await onCommit(latest);
      if(!current(item))return;
      stage='cleanup';finish(item);
      onNotice('已确认“'+item.label+'”完成，当前显示服务端版本 '+latest.revision+'。'
        +(latest.revision > receipt.revision ? '此后还有更新；首次回执未覆盖新版本。' : ''));
      return latest;
    } catch (error) {
      if (!current(item)) return;
      const rejected = stage === 'write' && [400,401,403,404,409,413,415,422].includes(error.httpStatus);
      if (rejected) {
        try{finish(item);}catch(cleanupError){item.cleanupRequired=true;onPending(true);
          onNotice('请求已被拒绝，但本地恢复记录未能清除；请明确清除本地记录后再编辑。清除不会撤销服务端操作。');throw cleanupError;}
        onNotice('本次请求被拒绝，本页草稿仍保留。若此前连接异常，仍需重新载入确认服务端版本；也可核对提示后修改。');
        throw error;
      }
      if(stage==='cleanup'){
        item.cleanupRequired=true;
        onPending(true);
        onNotice('当前服务端版本已读回，但本地恢复记录清除失败。可重试原操作安全确认，或明确清除本地记录；不要发起新写入。');
        throw new Error('本地恢复记录尚未清除，请重试或明确清除。');
      }
      onPending(true);
      onNotice(stage === 'read' ? '操作已完成，但当前版本暂未读回。请重试原操作以确认结果。'
        : '尚未确认上次操作的结果。请重试原操作，避免重复保存或提交。');
      throw new Error(journal?'连接或响应异常；原请求保留在本标签页会话中，可刷新连接同一身份后明确重试。'
        :'连接或响应异常；原请求已保留在当前页面，可点击“重试原操作”。');
    }
  }
  return {
    pending: () => intent !== null || journalProblem !== null,
    pendingInfo,
    clear,
    retry: attempt,
    restore,
    discard() {if(journal)journal.discard();reset();return {status:'cleared'};},
    async start({path, method, payload, readPath, id, label}) {
      if (intent||journalProblem) throw new Error('请先确认上次操作的结果。');
      const next = {path, method, body: JSON.stringify(payload), readPath, id, label,
        revision: payload.revision, key: crypto.randomUUID(), epoch: getEpoch()};
      if(journal){
        try{const saved=journal.save(stored(next),getActor?.());next.actor=saved.actor;}
        catch(error){restore();throw error;}
      }
      intent=next;
      onPending(true);
      return attempt();
    }
  };
}

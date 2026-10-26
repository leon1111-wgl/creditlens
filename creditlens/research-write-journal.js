/* Same-tab sessionStorage journal. This is not authorization or a signature. */
'use strict';
class ResearchWriteJournalError extends Error {
  constructor(code, message) { super(message); this.name='ResearchWriteJournalError'; this.code=code; }
}
function createResearchWriteJournal({namespace, storage}={}) {
  const version='research-write-journal-1', maxRecord=64*1024, maxBody=48*1024;
  const fail=(code,message)=>{throw new ResearchWriteJournalError(code,message);};
  if(!['research','explanation'].includes(namespace))fail('invalid_namespace','本地恢复记录的页面类型不受支持。');
  const key='creditlens:research-write-journal:1:'+namespace;
  const size=text=>new TextEncoder().encode(text).length;
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function fields(value,required,optional=[]) {
    if(!object(value)||required.some(k=>!Object.hasOwn(value,k))
        ||Object.keys(value).some(k=>!required.includes(k)&&!optional.includes(k)))
      fail('invalid_record','本地恢复记录的字段或结构无效。');
  }
  function text(value,max,min=0) {
    if(typeof value!=='string'||value.length>max||value.trim().length<min)
      fail('invalid_record','本地恢复记录的文字字段无效。');
  }
  function integer(value) {
    if(!Number.isSafeInteger(value)||value<1)fail('invalid_record','本地恢复记录的版本无效。');
  }
  function array(value,max) {
    if(!Array.isArray(value)||value.length>max)fail('invalid_record','本地恢复记录的列表无效。');
  }
  function unique(values) {
    if(new Set(values).size!==values.length)fail('invalid_record','本地恢复记录包含重复项目。');
  }
  // A bounded parser rejects duplicate keys, excessive nesting and nonfinite
  // numbers before any route or body is used. It never evaluates source text.
  function parse(raw,max) {
    if(typeof raw!=='string'||size(raw)>max)fail('record_too_large','本地恢复记录超过大小上限或不是文字。');
    let p=0,nodes=0;
    const ws=()=>{while(p<raw.length&&/[\t\n\r ]/.test(raw[p]))p++;};
    function string() {
      const start=p++;
      while(p<raw.length) {
        if(raw[p]==='\\'){p+=2;continue;}
        if(raw[p++]==='"')return JSON.parse(raw.slice(start,p));
      }
      throw new Error('string');
    }
    function value(depth) {
      if(depth>12||++nodes>4000)throw new Error('bounds');
      ws();const c=raw[p];
      if(c==='"')return string();
      if(c==='{') {
        p++;ws();const result=Object.create(null),seen=new Set();
        if(raw[p]==='}'){p++;return result;}
        while(true){ws();if(raw[p]!=='"')throw new Error('key');const name=string();
          if(seen.has(name))throw new Error('duplicate');seen.add(name);ws();if(raw[p++]!==':')throw new Error('colon');
          result[name]=value(depth+1);ws();const next=raw[p++];if(next==='}')return result;if(next!==',')throw new Error('comma');}
      }
      if(c==='[') {
        p++;ws();const result=[];if(raw[p]===']'){p++;return result;}
        while(true){result.push(value(depth+1));ws();const next=raw[p++];if(next===']')return result;if(next!==',')throw new Error('comma');}
      }
      for(const [token,result] of [['true',true],['false',false],['null',null]])
        if(raw.slice(p,p+token.length)===token){p+=token.length;return result;}
      const match=/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(raw.slice(p));
      if(!match)throw new Error('value');p+=match[0].length;const number=Number(match[0]);
      if(!Number.isFinite(number))throw new Error('finite');return number;
    }
    try {const result=value(0);ws();if(p!==raw.length)throw new Error('tail');return result;}
    catch(error){if(error instanceof ResearchWriteJournalError)throw error;fail('invalid_record','本地恢复记录不是受支持的严格 JSON。');}
  }
  function actor(value) {
    if(!object(value))fail('identity_unavailable','请先连接并核对当前服务端身份。');
    for(const field of ['tenant_id','user_id'])
      if(typeof value[field]!=='string'||!value[field].trim()||value[field].length>256)
        fail('identity_unavailable','请先连接并核对当前服务端身份。');
    if(!['analyst','reviewer','viewer'].includes(value.role))fail('identity_unavailable','当前服务端角色不可用。');
    return {tenant_id:value.tenant_id,user_id:value.user_id,role:value.role};
  }
  function sameActor(a,b) {
    try {const x=actor(a),y=actor(b);return ['tenant_id','user_id','role'].every(k=>x[k]===y[k]);}
    catch(_){return false;}
  }
  function bodyCheck(body,method,who) {
    const b=parse(body,maxBody);integer(b?.revision);
    if(method==='PATCH') {
      const required=namespace==='research'?['revision','decisions','hypotheses','coverage_note']:['revision','decisions','coverage_note'];
      fields(b,required,namespace==='research'?['bridge_review_note']:[]);
      if(who.role!=='analyst')fail('invalid_record','保存请求与登记角色不一致。');
      text(b.coverage_note,4000);array(b.decisions,namespace==='research'?8:100);
      const idField=namespace==='research'?'observation_id':'entry_id';
      const choices=namespace==='research'?['adopted','rejected']:['adopt_as_company_statement','rejected','needs_evidence'];
      for(const d of b.decisions){fields(d,[idField,'decision','note']);text(d[idField],256,1);
        if(!choices.includes(d.decision))fail('invalid_record','本地决定类型无效。');text(d.note,2000,namespace==='research'?1:10);}
      unique(b.decisions.map(d=>d[idField]));
      if(namespace==='research') {
        if(b.bridge_review_note!==undefined&&b.bridge_review_note!==null)text(b.bridge_review_note,4000);
        array(b.hypotheses,3);
        for(const h of b.hypotheses){fields(h,['id','text','evidence_ids','status','note']);
          text(h.id,80,1);if(!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(h.id))fail('invalid_record','假设标识无效。');
          text(h.text,4000,1);text(h.note,4000);array(h.evidence_ids,8);h.evidence_ids.forEach(id=>text(id,256,1));unique(h.evidence_ids);
          if(!['pending','not_assessed'].includes(h.status))fail('invalid_record','假设状态无效。');}
        unique(b.hypotheses.map(h=>h.id));
      }
    } else if(method==='POST') {
      fields(b,['revision','action','note'],['snapshot_sha256']);
      if(!['submit','approve','return'].includes(b.action))fail('invalid_record','本地动作无效。');
      text(b.note,2000,namespace==='research'?1:10);
      if(who.role!==(b.action==='submit'?'analyst':'reviewer'))fail('invalid_record','动作与登记角色不一致。');
      if(b.snapshot_sha256!==undefined&&b.snapshot_sha256!==null
          &&(typeof b.snapshot_sha256!=='string'||! /^[0-9a-f]{64}$/.test(b.snapshot_sha256)))
        fail('invalid_record','复核快照摘要无效。');
      if(b.action!=='submit'&&(typeof b.snapshot_sha256!=='string'||! /^[0-9a-f]{64}$/.test(b.snapshot_sha256)))
        fail('invalid_record','复核动作缺少快照绑定。');
    } else fail('invalid_record','不支持此本地请求方法。');
    return b;
  }
  function record(intent,who) {
    fields(intent,['id','path','readPath','method','label','revision','key','body']);
    if(typeof intent.id!=='string'||! /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(intent.id))fail('invalid_record','本地任务标识无效。');
    integer(intent.revision);
    if(typeof intent.key!=='string'||intent.key.length<8||intent.key.length>128||!intent.key.trim())
      fail('invalid_record','本地幂等键无效。');
    if(typeof intent.body!=='string')fail('invalid_record','缺少原始请求正文。');
    const bound=actor(who),b=bodyCheck(intent.body,intent.method,bound);
    if(b.revision!==intent.revision)fail('invalid_record','正文与本地版本绑定不一致。');
    const prefix=namespace==='research'?'/api/research/tasks/':'/api/research/explanation-reviews/';
    const readPath=prefix+intent.id,path=readPath+(intent.method==='POST'?'/actions':'');
    if(intent.path!==path||intent.readPath!==readPath)fail('invalid_record','本地请求目标不在固定端点范围内。');
    const label=intent.method==='PATCH'?(namespace==='research'?'保存草稿':'保存处理意见'):
      ({submit:'提交复核',approve:namespace==='research'?'复核确认':'确认处理意见',return:'退回补充'})[b.action];
    if(intent.label!==label)fail('invalid_record','本地操作名称与请求不一致。');
    return {schema_version:1,journal_version:version,namespace,actor:bound,
      intent:{id:intent.id,path,readPath,method:intent.method,label,revision:intent.revision,key:intent.key,body:intent.body}};
  }
  function validate(raw) {
    const parsed=parse(raw,maxRecord);fields(parsed,['schema_version','journal_version','namespace','actor','intent']);
    if(parsed.schema_version!==1||parsed.journal_version!==version||parsed.namespace!==namespace)
      fail('unsupported_record','本地恢复记录版本或页面类型不受支持。');
    fields(parsed.actor,['tenant_id','user_id','role']);return record(parsed.intent,parsed.actor);
  }
  function getStorage() {
    try {const s=storage===undefined?globalThis.sessionStorage:storage;
      if(!s||typeof s.getItem!=='function'||typeof s.setItem!=='function'||typeof s.removeItem!=='function')throw new Error('storage');return s;
    } catch(_){fail('storage_unavailable','无法访问本标签页的恢复存储，请检查浏览器设置。');}
  }
  function get(s) {try{return s.getItem(key);}catch(_){fail('storage_read_failed','读取本地恢复记录失败。');}}
  function removeRaw(s) {
    try{s.removeItem(key);}catch(_){fail('storage_clear_failed','清除本地恢复记录失败，请保留待确认状态并重试或明确清除。');}
    if(get(s)!==null)fail('storage_clear_failed','本地恢复记录尚未清除，请勿发起新的写入。');
  }
  return {
    key,version,sameActor,
    save(intent,who) {
      const r=record(intent,who),raw=JSON.stringify(r);
      if(size(raw)>maxRecord)fail('record_too_large','本地恢复记录超过大小上限。');
      const s=getStorage();if(get(s)!==null)fail('journal_occupied','本标签页已有待确认或损坏的记录，请先恢复或明确清除。');
      try{s.setItem(key,raw);}catch(_){fail('storage_write_failed','保存本地恢复记录失败；本次写请求尚未发送，表单仍保留。');}
      if(get(s)!==raw)fail('storage_write_failed','未能回读完整恢复记录；本次写请求尚未发送。');
      return r;
    },
    read(who) {
      try {
        const current=actor(who),raw=get(getStorage());if(raw===null)return {status:'empty'};
        const r=validate(raw);if(!sameActor(r.actor,current))return {status:'identity_mismatch',code:'identity_mismatch'};
        return {status:'ready',record:r};
      } catch(error) {
        return {status:error.code?.startsWith('storage_')||error.code==='identity_unavailable'?'unavailable':'invalid',
          code:error.code||'invalid_record',message:error instanceof ResearchWriteJournalError?error.message:'本地恢复记录无法读取。'};
      }
    },
    remove(intent,who) {
      const expected=record(intent,who),s=getStorage(),raw=get(s);if(raw===null)return;
      const current=validate(raw);
      if(JSON.stringify(expected)!==JSON.stringify(current))fail('journal_changed','本地记录已变化，不能清除另一项待确认操作。');
      removeRaw(s);
    },
    discard() {removeRaw(getStorage());}
  };
}

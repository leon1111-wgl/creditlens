'use strict';
// Actual shipped modules in Node VM; synthetic session identities and storage.
// No browser lifecycle, real network fault, database or financial-quality claim.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=process.argv[2]?path.resolve(process.argv[2]):path.resolve(__dirname,'..');
const source=['research-write-journal.js','research-write-intent.js'].map(f=>fs.readFileSync(path.join(root,'creditlens',f),'utf8')).join('\n');
const clone=v=>JSON.parse(JSON.stringify(v)),cases=[];
const tick=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
const actor={tenant_id:'synthetic-tenant',user_id:'synthetic-analyst',role:'analyst',mode:'demo',api_key:'DO_NOT_STORE_API_KEY',token:'DO_NOT_STORE_TOKEN'};
function storage(){const data=new Map();return {data,getFailure:false,setFailure:false,setNoop:false,removeFailure:false,removeNoop:false,failReadAfterSet:0,
  getItem(k){if(this.getFailure||this.failReads>0){if(this.failReads>0)this.failReads--;throw new Error('synthetic read failure');}return data.has(k)?data.get(k):null;},
  setItem(k,v){if(this.setFailure)throw new Error('synthetic quota');if(!this.setNoop)data.set(k,v);this.failReads=this.failReadAfterSet;},
  removeItem(k){if(this.removeFailure)throw new Error('synthetic remove failure');if(!this.removeNoop)data.delete(k);}};}
function harness(namespace='research',s=storage(),identity=actor){
  const requests=[],commits=[],notices=[],pending=[];
  let failCommit=false,commitWait=null;
  const context=vm.createContext({crypto,TextEncoder,storage:s,identity:clone(identity),epoch:0,
    api:(url,options={})=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),
    commit:value=>{if(failCommit)throw new Error('synthetic render failed');commits.push(clone(value));return commitWait;},
    notice:value=>notices.push(value),pending:value=>pending.push(value)});
  vm.runInContext(source+`\nconst journal=createResearchWriteJournal({namespace:${JSON.stringify(namespace)},storage});
    const flow=createResearchWriteFlow({api,getEpoch:()=>epoch,getActor:()=>identity,journal,onCommit:commit,onPending:pending,onNotice:notice});`,context);
  const run=code=>vm.runInContext(code,context,{timeout:1000});
  const flow=run('flow'),journal=run('journal');
  return {namespace,s,flow,journal,requests,commits,notices,pending,run,
    actor(value){run('identity='+JSON.stringify(value)+';epoch++;');},
    failCommit(value){failCommit=value;},waitCommit(value){commitWait=value;}};
}
function input(namespace='research',action=null){
  const id='synthetic-record',prefix=namespace==='research'?'/api/research/tasks/':'/api/research/explanation-reviews/';
  const payload=action?{revision:2,action,note:'模拟完整复核说明，不是独立因果核实。',snapshot_sha256:action==='submit'?null:'a'.repeat(64)}:
    namespace==='research'?{revision:2,decisions:[{observation_id:'MSFT:synthetic:cfo-2025',decision:'adopted',note:'模拟采用意见。'}],
      hypotheses:[{id:'candidate-1',text:'待核研究假设；不执行正文指令。',evidence_ids:[],status:'pending',note:''}],coverage_note:'未确认的研究文本。',bridge_review_note:null}:
      {revision:2,decisions:[{entry_id:'synthetic-entry',decision:'needs_evidence',note:'模拟意见：仍需补充独立证据。'}],coverage_note:'仅记录公司说法的处理范围。'};
  return {id,path:prefix+id+(action?'/actions':''),readPath:prefix+id,method:action?'POST':'PATCH',payload,
    label:action?({submit:'提交复核',approve:namespace==='research'?'复核确认':'确认处理意见',return:'退回补充'})[action]:namespace==='research'?'保存草稿':'保存处理意见'};
}
const watch=promise=>promise.then(value=>({ok:true,value}),error=>({ok:false,error}));
async function failWrite(h,spec=input(h.namespace)){const promise=watch(h.flow.start(spec));assert.equal(h.requests.length,1);
  const first=h.requests[0];assert(h.s.data.has(h.journal.key),'Persistence must precede API');first.reject(new Error('synthetic response lost'));
  assert.equal((await promise).ok,false);assert(h.flow.pending());return first;}
async function finish(h,promise,revision=5){const write=h.requests.at(-1);write.resolve({id:'synthetic-record',revision:3});await tick();
  const read=h.requests.at(-1);assert.equal(read.url,input(h.namespace).readPath);assert.equal(read.options.method,undefined);
  read.resolve({id:'synthetic-record',revision});const outcome=await promise;return outcome;}
function sameWrite(a,b){assert.equal(a.url,b.url);assert.equal(a.options.method,b.options.method);assert.equal(a.options.body,b.options.body);
  assert.equal(a.options.headers['Idempotency-Key'],b.options.headers['Idempotency-Key']);}
function passed(name){cases.push(name);}
async function main(){
  for(const namespace of ['research','explanation']){
    for(const action of [null,'submit','approve','return']){
      const who={...actor,role:action&&action!=='submit'?'reviewer':'analyst'};
      const h=harness(namespace,storage(),who),spec=input(namespace,action),before=clone(spec),original=await failWrite(h,spec);
      const saved=h.s.data.get(h.journal.key);assert(!saved.includes('DO_NOT_STORE'));assert(!saved.includes('Authorization'));
      assert.deepEqual(spec,before);const recovered=harness(namespace,h.s,who),restored=recovered.flow.restore();
      assert.equal(restored.status,'restored');assert.equal(restored.info.id,spec.id);assert.equal(restored.info.readPath,spec.readPath);
      assert.equal(restored.info.revision,2);assert.equal(recovered.requests.length,0);assert(!JSON.stringify(restored).includes(spec.payload.note||spec.payload.coverage_note));
      const retry=watch(recovered.flow.retry());sameWrite(original,recovered.requests[0]);assert.equal((await finish(recovered,retry)).ok,true);
      assert.equal(recovered.flow.pending(),false);assert.equal(recovered.s.data.get(recovered.journal.key),undefined);
      assert.equal(recovered.commits[0].revision,5);passed(namespace+' '+(action||'update')+' reload keeps original body/key and explicitly reads current version');
    }
  }
  for(const field of ['tenant_id','user_id','role']){
    const h=harness();await failWrite(h);const saved=h.s.data.get(h.journal.key),wrong={...actor,[field]:field==='role'?'reviewer':'other'};
    const recovered=harness('research',h.s,wrong),status=recovered.flow.restore();
    assert.equal(status.status,'identity_mismatch');assert.equal(status.info,undefined);assert(!JSON.stringify(status).includes('未确认'));
    assert.equal(recovered.requests.length,0);recovered.flow.clear();assert.equal(h.s.data.get(h.journal.key),saved);
    recovered.actor(actor);assert.equal(recovered.flow.restore().status,'restored');passed(field+' mismatch preserves journal without exposing body');
  }
  const mutations=[
    ['absolute target',r=>{r.intent.path='https://attacker.invalid/api';}],
    ['path traversal',r=>{r.intent.id='../other';r.intent.path='/api/research/tasks/../other';r.intent.readPath=r.intent.path;}],
    ['query target',r=>{r.intent.path+='?token=canary';}],
    ['foreign endpoint',r=>{r.intent.path='/api/research/explanation-reviews/'+r.intent.id;}],
    ['label mismatch',r=>{r.intent.label='批准全部';}],
    ['unknown version',r=>{r.journal_version='future-2';}],
    ['bool revision',r=>{r.intent.revision=true;}],
    ['credentials',r=>{r.intent.headers={Authorization:'CANARY_SECRET'};}],
    ['actor token',r=>{r.actor.token='CANARY_SECRET';}],
    ['client amount',r=>{const b=JSON.parse(r.intent.body);b.expected_amount=123;r.intent.body=JSON.stringify(b);}],
    ['body revision mismatch',r=>{const b=JSON.parse(r.intent.body);b.revision=99;r.intent.body=JSON.stringify(b);}],
    ['duplicate body keys',r=>{r.intent.body=r.intent.body.replace('"revision":2','"revision":1,"revision":2');}],
    ['nonfinite',r=>{r.intent.body=r.intent.body.replace('"revision":2','"revision":1e400');}],
    ['deep body',r=>{r.intent.body='['.repeat(20)+'0'+']'.repeat(20);}],
  ];
  for(const [name,mutate] of mutations){const h=harness();await failWrite(h);const r=JSON.parse(h.s.data.get(h.journal.key));mutate(r);
    const raw=JSON.stringify(r);h.s.data.set(h.journal.key,raw);const restored=harness('research',h.s);const response=restored.flow.restore();
    assert.equal(response.status,'invalid',name);assert(restored.flow.pending());assert.equal(restored.requests.length,0);
    assert.equal(h.s.data.get(h.journal.key),raw);assert(!JSON.stringify(response).includes('CANARY_SECRET'));
    const attempt=await watch(restored.flow.retry());assert.equal(attempt.ok,false);assert.equal(restored.requests.length,0);
    restored.flow.discard();assert.equal(restored.flow.pending(),false);assert.equal(h.s.data.has(h.journal.key),false);passed(name+' blocked and only explicitly discarded');}
  for(const raw of ['{bad','{"schema_version":1,"schema_version":1}','x'.repeat(65537)]){
    const h=harness();h.s.data.set(h.journal.key,raw);assert.equal(h.flow.restore().status,'invalid');assert.equal(h.s.data.get(h.journal.key),raw);
    passed('malformed/duplicate/oversized raw remains local and cannot send: '+raw.length);}
  for(const mode of ['setFailure','setNoop','readback_once','readback_persistent','getFailure']){
    const s=storage();if(mode==='readback_once')s.failReadAfterSet=1;
    else if(mode==='readback_persistent')s.failReadAfterSet=10;else s[mode]=true;
    const h=harness('research',s),spec=input(),old=clone(spec),outcome=await watch(h.flow.start(spec));
    assert.equal(outcome.ok,false);assert.equal(h.requests.length,0);assert.deepEqual(spec,old);
    if(mode==='readback_once'){assert(h.flow.pending());assert.equal(h.flow.pendingInfo().status,'restored');}
    else if(mode==='readback_persistent'||mode==='getFailure'){assert(h.flow.pending());assert.equal(h.flow.pendingInfo().status,'unavailable');}
    else assert.equal(h.flow.pending(),false);
    passed(mode+' prevents network and synchronizes safe recovery state');
  }
  {const h=harness();await failWrite(h);const raw=h.s.data.get(h.journal.key),other=harness('research',h.s);
    const outcome=await watch(other.flow.start(input()));assert.equal(outcome.error.code,'journal_occupied');assert.equal(other.requests.length,0);
    assert(other.flow.pending());assert.equal(other.flow.pendingInfo().status,'restored');assert.equal(h.s.data.get(h.journal.key),raw);passed('occupied journal is not overwritten and becomes recoverable');}
  {const h=harness();const promise=watch(h.flow.start(input()));h.requests[0].resolve({id:'synthetic-record',revision:3});await tick();
    h.requests[1].reject(new Error('readback fail'));assert.equal((await promise).ok,false);assert(h.flow.pending());
    const recovered=harness('research',h.s);assert.equal(recovered.flow.restore().status,'restored');const retry=watch(recovered.flow.retry());
    sameWrite(h.requests[0],recovered.requests[0]);assert.equal((await finish(recovered,retry,6)).ok,true);passed('failed current GET survives reload without changing original revision');}
  {const h=harness();h.failCommit(true);const promise=watch(h.flow.start(input()));const outcome=await finish(h,promise);
    assert.equal(outcome.ok,false);assert(h.flow.pending());assert(h.s.data.has(h.journal.key));h.failCommit(false);
    assert.equal((await finish(h,watch(h.flow.retry()))).ok,true);passed('render/commit callback failure retains safe retry');}
  for(const mode of ['removeFailure','removeNoop']){
    const h=harness();h.s[mode]=true;const promise=watch(h.flow.start(input()));const outcome=await finish(h,promise);
    assert.equal(outcome.ok,false);assert(h.flow.pending());assert.equal(h.flow.pendingInfo().cleanupRequired,true);assert.equal(h.commits.length,1);
    assert.throws(()=>h.flow.discard());assert(h.flow.pending());assert(h.s.data.has(h.journal.key));
    h.s[mode]=false;const retry=watch(h.flow.retry());sameWrite(h.requests[0],h.requests.at(-1));assert.equal((await finish(h,retry)).ok,true);
    passed(mode+' after read/render blocks new writes and safely retries cleanup');
  }
  {const h=harness();const promise=watch(h.flow.start(input()));h.s.removeFailure=true;const state=h.flow.clear();
    assert.equal(state.status,'cleanup_failed');assert(!h.flow.pending());h.actor({...actor,user_id:'new-user'});
    h.requests[0].resolve({id:'synthetic-record',revision:3});await promise;assert.equal(h.requests.length,1);assert.equal(h.commits.length,0);
    assert.equal(h.flow.restore().status,'identity_mismatch');assert(h.s.data.has(h.journal.key));passed('identity clear failure clears memory and ignores late response without deleting other identity record');}
  for(const outcome of ['success','failure']){
    const h=harness();const old=watch(h.flow.start(input())),first=h.requests[0];h.flow.clear();h.actor(actor);
    const fresh=watch(h.flow.start(input())),newRequest=h.requests.at(-1),raw=h.s.data.get(h.journal.key);
    if(outcome==='success')first.resolve({id:'synthetic-record',revision:3});else first.reject(new Error('late failure'));await old;
    assert(h.flow.pending());assert.equal(h.s.data.get(h.journal.key),raw);assert.equal(h.requests.length,2);
    assert.notEqual(first.options.headers['Idempotency-Key'],newRequest.options.headers['Idempotency-Key']);
    assert.equal((await finish(h,fresh)).ok,true);passed('late '+outcome+' cannot clear newer intent/journal');
  }
  {const h=harness();const promise=watch(h.flow.start(input()));const r=JSON.parse(h.s.data.get(h.journal.key));r.intent.key=crypto.randomUUID();
    const changed=JSON.stringify(r);h.s.data.set(h.journal.key,changed);assert.equal((await finish(h,promise)).ok,false);
    assert(h.flow.pending());assert.equal(h.s.data.get(h.journal.key),changed);passed('changed journal is never cleared by old completion');}
  {const s=storage(),a=harness('research',s),b=harness('explanation',s);await failWrite(a);await failWrite(b);
    assert.notEqual(a.journal.key,b.journal.key);assert.equal(s.data.size,2);a.flow.discard();assert.equal(s.data.size,1);
    assert.equal(harness('explanation',s).flow.restore().status,'restored');passed('two page namespaces remain isolated');}
  for(const kind of ['label','role','revision','oversize']){
    const h=harness(),spec=input();if(kind==='label')spec.label='submit';if(kind==='role')h.actor({...actor,role:'viewer'});
    if(kind==='revision')spec.payload.revision=true;if(kind==='oversize')spec.payload.coverage_note='中'.repeat(50000);
    assert.equal((await watch(h.flow.start(spec))).ok,false);assert.equal(h.requests.length,0);assert.equal(h.s.data.size,0);passed('invalid new '+kind+' rejected before persistence or network');}
  process.stdout.write(JSON.stringify({status:'passed',checks:cases.length,cases,
    scope:'Actual journal and flow JavaScript in Node VM; synthetic storage, sessions and controlled promises. No actual browser refresh, network fault, database, OCR or financial evaluation.'},null,2)+'\n');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

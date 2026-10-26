'use strict';

// Actual shipped pages, journal and write controller in fresh VM contexts sharing session storage.
// Registered fixture content is used for rendering; actors and failures are synthetic.
// This is not a browser, network-fault, database, or financial-quality evaluation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = process.argv[2] ? path.resolve(process.argv[2]) : path.resolve(__dirname, '..');
const controllerRoot=process.argv[3]?path.resolve(process.argv[3]):root;
const shared = fs.readFileSync(path.join(controllerRoot, 'creditlens/research-write-intent.js'), 'utf8');
const journal = fs.readFileSync(path.join(controllerRoot, 'creditlens/research-write-journal.js'), 'utf8');
const fixture = name => JSON.parse(fs.readFileSync(path.join(root, 'artifacts', name), 'utf8'));
const parentExport = fixture('research-task-demo.json');
const childExport = fixture('explanation-review-demo.json');
const clone = value => JSON.parse(JSON.stringify(value));
const results = [];
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const jsonReply = (value, status = 200) => ({ok:status >= 200 && status < 300, status,
  text:async () => JSON.stringify(value)});
function record(exported, kind, revision = 1, status = 'draft') {
  const content = clone(exported.snapshot.content);
  return {...content, id:kind === 'research' ? exported.task_id : exported.attachment_id,
    revision, status, events:[], review:null, snapshots:[], snapshot:null,
    snapshot_id:null, snapshot_sha256:null, created_at:'synthetic', updated_at:'synthetic',
    parent_contributors:[], contributors:['synthetic-author']};
}
function makeStorage(){
  const entries=new Map();
  return {entries,failGet:false,failSet:false,failRemove:false,noSet:false,noRemove:false,
    getItem(key){if(this.failGet)throw new Error('synthetic storage access denied');return entries.get(key)??null;},
    setItem(key,value){if(this.failSet)throw new Error('synthetic storage quota');if(!this.noSet)entries.set(key,String(value));},
    removeItem(key){if(this.failRemove)throw new Error('synthetic storage removal denied');if(!this.noRemove)entries.delete(key);}};
}
function harness(kind, storage = makeStorage()) {
  const file = kind === 'research' ? 'tasks.html' : 'explanation-reviews.html';
  const html = fs.readFileSync(path.join(root, 'creditlens', file), 'utf8');
  const start = html.indexOf('<script>'), end = html.indexOf('</script>', start);
  assert(start >= 0 && end > start);
  const full = html.slice(start + 8, end);
  const boot = full.lastIndexOf('run(async()=>{const health=');
  assert(boot >= 0, 'Cannot isolate shipped startup');
  const source = full.slice(0, boot);
  const nodes = {}, all = [], requests = [], downloads = [], urls = [];
  class Element {
    constructor(tag='div') {this.tagName=tag.toUpperCase();this.children=[];this.listeners={};
      this.style={};this.dataset={};this.value='';this.disabled=false;this.hidden=false;
      this.className='';this.id='';this._text='';this.classList={toggle:()=>{}};all.push(this);}
    get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('');}
    set textContent(v){this._text=String(v);this.children=[];}
    set innerHTML(_){throw new Error('Untrusted output must not use innerHTML');}
    append(...items){for(const item of items){if(typeof item==='string'){this._text+=item;continue;}
      item.parent=this;this.children.push(item);}}
    replaceChildren(...items){this._text='';this.children=[];this.append(...items);}
    setAttribute(k,v){this[k]=v;}
    addEventListener(event,fn){(this.listeners[event]??=[]).push(fn);}
    click(){if(this.tagName==='A')downloads.push({href:this.href,name:this.download});
      return Promise.all((this.listeners.click||[]).map(fn=>fn()));}
  }
  for (const match of html.slice(0,start).matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
    const attrs=match[2], id=/\bid="([^"]+)"/.exec(attrs), cls=/\bclass="([^"]+)"/.exec(attrs);
    if(!id && !cls && !/data-tab=/.test(attrs))continue;
    const n=new Element(match[1]);if(id){n.id=id[1];nodes[n.id]=n;}
    n.className=cls?.[1]||'';const tab=/\bdata-tab="([^"]+)"/.exec(attrs);if(tab)n.dataset.tab=tab[1];
  }
  const matchSimple=(node,selector)=>selector[0]==='#'?node.id===selector.slice(1)
    :selector[0]==='.'?node.className.split(' ').includes(selector.slice(1))
    :selector==='[data-tab]'?node.dataset.tab!==undefined:node.tagName===selector.toUpperCase();
  const matches=(node,selector)=>{const parts=selector.trim().split(/\s+/);if(!matchSimple(node,parts.pop()))return false;
    let ancestor=node.parent;while(parts.length){const wanted=parts.pop();while(ancestor&&!matchSimple(ancestor,wanted))ancestor=ancestor.parent;
      if(!ancestor)return false;ancestor=ancestor.parent;}return true;};
  const document={getElementById(id){if(!nodes[id]){nodes[id]=new Element();nodes[id].id=id;}return nodes[id];},
    createElement:tag=>new Element(tag),querySelectorAll:selector=>all.filter(n=>selector.split(',').some(s=>matches(n,s)))};
  class LocalURL extends URL {
    static createObjectURL(blob){const href='blob:synthetic-'+urls.length;urls.push({href,blob});return href;}
    static revokeObjectURL(){}
  }
  const sessionStorage=storage;
  const context=vm.createContext({document,Node:Element,Blob,URL:LocalURL,URLSearchParams,TextEncoder,sessionStorage,
    location:{search:''},crypto,structuredClone,Intl,setTimeout:fn=>fn(),
    fetch:(url,options={})=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
  const run=code=>vm.runInContext(code,context,{timeout:1000});
  run(journal);run(shared);run(source);
  const base=record(kind==='research'?parentExport:childExport,kind);
  const p=record(childExport.snapshot.content.evidence.parent_export,'research',7,'reviewed');
  run("$('role').value='analyst';$('key').value='synthetic-key';session={role:'analyst',user_id:'synthetic-author',tenant_id:'synthetic-tenant'};");
  if(kind==='explanation')run('parent='+JSON.stringify(p)+';');
  run((kind==='research'?'load':'loadReview')+'('+JSON.stringify(base)+');');
  function state(){return JSON.parse(run(`JSON.stringify({pending:writeFlow.pending(),busy,dirty,epoch,
    revision:${kind==='research'?'task':'review'}?.revision??null,
    id:${kind==='research'?'task':'review'}?.id??null,
    status:$('status').textContent,error:$('error').textContent,
    retryDisabled:$('retryWrite').disabled,coverageDisabled:$('coverage').disabled,
    discardHidden:$('discardDraft').hidden,questionDisabled:${kind==='research'?"$('question').disabled":'null'},
    journalIssue,recoveryReadRequired,
    coverage:$('coverage').value,recoveryHidden:$('writeRecovery').hidden})`));}
  const show=(revision,status='draft')=>{const item={...clone(base),revision,status};
    if(status==='in_review'){item.snapshot={id:'synthetic-snapshot',sha256:'a'.repeat(64)};item.snapshot_sha256=item.snapshot.sha256;}
    return item;};
  const edit=()=>run("$('coverage').value='SYNTHETIC_UNSAVED_CANARY';markDirty();");
  const save=()=>run('run(save)');
  const action=kindAction=>{const isReview=kindAction!=='submit';
    run(`session={role:'${isReview?'reviewer':'analyst'}',user_id:'synthetic-${isReview?'reviewer':'author'}',tenant_id:'synthetic-tenant'};`);
    run((kind==='research'?'load':'loadReview')+'('+JSON.stringify(show(1,isReview?'in_review':'draft'))+');');
    run("$('actionNote').value='SYNTHETIC_ACTION_NOTE with complete scope';");
    return run(`run(()=>${kind==='research'?'action':'act'}(${JSON.stringify(kindAction)}))`);};
  const retry=()=>nodes.retryWrite.click();
  const changeIdentity=()=>{nodes.key.value='different-key';return Promise.all((nodes.key.listeners.input||[]).map(fn=>fn()));};
  return {kind,run,state,requests,nodes,base,show,edit,save,action,retry,changeIdentity,downloads,urls,storage,parentFixture:p,
    prefix:kind==='research'?'/api/research/tasks/':'/api/research/explanation-reviews/'};
}

function next(app){return app.requests.at(-1);}
function sameWrite(first,second){assert.equal(second.url,first.url);assert.equal(second.options.method,first.options.method);
  assert.equal(second.options.body,first.options.body);assert.equal(second.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);}
async function seed(kind){
  const storage=makeStorage(), app=harness(kind,storage);app.edit();const op=app.save();
  assert.equal(app.requests.length,1);const write=next(app);
  assert.equal(storage.entries.size,1,'Persist before sending a new write');
  const serialized=[...storage.entries.values()][0];
  assert(serialized.includes('SYNTHETIC_UNSAVED_CANARY'));
  assert(!serialized.includes('synthetic-key'),'Never persist API credentials');
  write.reject(new Error('synthetic response loss'));await op;assert(app.state().pending);
  return {storage,first:write};
}
function readValue(app,url,{revision=4,actor={}}={}){
  if(url==='/api/session')return {mode:'api_key',role:'analyst',user_id:'synthetic-author',tenant_id:'synthetic-tenant',...actor};
  if(url==='/api/research/tasks')return app.kind==='explanation'?[app.parentFixture]:[];
  if(url===app.prefix+app.base.id)return app.show(revision);
  if(url==='/api/research/tasks/'+app.parentFixture.id)return app.parentFixture;
  if(url.endsWith('/explanation-reviews'))return [{id:app.base.id,revision,status:'draft'}];
  throw new Error('Unexpected read '+url);
}
async function reads(app,operation,{from=0,...options}={}){
  let done=false;operation.finally(()=>{done=true;});
  let index=from;
  for(let round=0;round<50;round++){
    await tick();
    while(index<app.requests.length){const request=app.requests[index++];
      assert(!request.options.method||request.options.method==='GET','Restore must never write automatically');
      request.resolve(jsonReply(readValue(app,request.url,options)));await tick();}
    if(done)return;
  }
  throw new Error('Read operation did not settle');
}
async function restore(app,options={}){app.run('clear()');const op=app.run('run(connect)');await reads(app,op,options);return op;}
async function awaitRestoreTarget(app){
  const answered=new Set();
  for(let round=0;round<50;round++){
    await tick();
    for(const request of app.requests){
      if(answered.has(request))continue;
      assert(!request.options.method||request.options.method==='GET','Restore must remain read only');
      if(request.url===app.prefix+app.base.id)return request;
      answered.add(request);request.resolve(jsonReply(readValue(app,request.url)));
    }
  }
  throw new Error('Restore target request did not arrive');
}
async function finishRetry(app,first){
  const op=app.retry(),write=next(app);sameWrite(first,write);
  write.resolve(jsonReply({id:app.base.id,revision:2}));await tick();
  await reads(app,op,{from:app.requests.indexOf(write)+1,revision:5});
  assert.equal(app.state().revision,5);assert(!app.state().pending);assert.equal(app.storage.entries.size,0);
}
async function main(){
  for(const kind of ['research','explanation']){
    const passed=name=>results.push(kind+': '+name);
    {
      const {storage,first}=await seed(kind),app=harness(kind,storage);await restore(app);
      assert(app.state().pending);assert.equal(app.state().revision,4);assert.equal(app.state().retryDisabled,false);
      assert(!app.state().coverage.includes('SYNTHETIC_UNSAVED_CANARY'),'Restored request body is not current saved content');
      assert(!app.nodes.recoveryMeta.textContent.includes('SYNTHETIC_UNSAVED_CANARY'));
      await finishRetry(app,first);passed('fresh VM restores metadata only, no auto write, original body/key retry, latest GET and cleanup');
    }
    for(const action of ['submit','approve','return']){
      const storage=makeStorage(),before=harness(kind,storage),firstOp=before.action(action),first=next(before);
      assert.equal(storage.entries.size,1);first.reject(new Error('synthetic action response loss'));await firstOp;
      const app=harness(kind,storage),isReview=action!=='submit';
      await restore(app,{actor:{role:isReview?'reviewer':'analyst',user_id:'synthetic-'+(isReview?'reviewer':'author')}});
      assert(app.state().pending);assert.equal(app.state().retryDisabled,false);await finishRetry(app,first);
      const body=JSON.parse(first.options.body);assert.equal(body.action,action);assert.equal(body.revision,1);
      assert(body.note.includes('SYNTHETIC_ACTION_NOTE'));if(isReview)assert.equal(body.snapshot_sha256,'a'.repeat(64));
      passed(action+' restored across fresh VM with original action, note, revision and snapshot');
    }
    for(const failure of ['failGet','failSet','noSet']){
      const storage=makeStorage(),app=harness(kind,storage);storage[failure]=true;app.edit();await app.save();
      assert.equal(app.requests.length,0,'Storage failure must block sending');assert(app.state().error);
      passed(failure+' before write sends no request');
    }
    for(const actor of [{user_id:'other-author'},{tenant_id:'other-tenant'},{role:'reviewer'}]){
      const {storage}=await seed(kind),original=[...storage.entries.values()][0],app=harness(kind,storage);
      await restore(app,{actor});assert.equal(app.state().journalIssue,'identity_mismatch');assert(app.state().pending);
      assert(app.state().retryDisabled);assert.equal(app.nodes.recoveryMeta.textContent,'');
      assert.equal([...storage.entries.values()][0],original);assert(!app.state().coverage.includes('SYNTHETIC_UNSAVED_CANARY'));
      passed('identity mismatch '+Object.keys(actor)[0]+' hides body and preserves record');
    }
    {
      const {storage}=await seed(kind),key=[...storage.entries.keys()][0];storage.entries.set(key,'{broken SYNTHETIC_PRIVATE_CANARY');
      const app=harness(kind,storage);await restore(app);assert.equal(app.state().journalIssue,'invalid');assert(app.state().retryDisabled);
      assert(!JSON.stringify(app.state()).includes('SYNTHETIC_PRIVATE_CANARY'));
      const count=app.requests.length;await app.nodes.clearJournal.click();assert.equal(app.requests.length,count);assert(storage.entries.has(key));
      app.nodes.confirmClearJournal.checked=true;const op=app.nodes.clearJournal.click();await reads(app,op,{from:count});
      assert.equal(storage.entries.size,0);passed('corrupt record has no echo or auto removal; explicit confirmed removal reads current state');
    }
    for(const failure of ['403','500','body','json','wrong_id']){
      const {storage}=await seed(kind),app=harness(kind,storage);app.run('clear()');const op=app.run('run(connect)');
      const target=await awaitRestoreTarget(app);assert.equal(target.url,app.prefix+app.base.id);
      if(failure==='body')target.resolve({ok:true,status:200,text:async()=>{throw Error('synthetic body error');}});
      else if(failure==='json')target.resolve({ok:true,status:200,text:async()=>'{bad'});
      else if(failure==='wrong_id')target.resolve(jsonReply({...app.show(4),id:'a'.repeat(32)}));
      else target.resolve(jsonReply({detail:'synthetic denied'},Number(failure)));
      await op;assert(app.state().pending);assert(app.state().recoveryReadRequired);assert(app.state().retryDisabled);
      const before=app.requests.length;await app.retry();assert.equal(app.requests.length,before);
      const retryRead=app.nodes.refreshRecovery.click();await reads(app,retryRead,{from:before});
      assert.equal(app.state().retryDisabled,false);passed('restore GET '+failure+' blocks retry until explicit read succeeds');
    }
    for(const failure of ['failRemove','noRemove']){
      const {storage,first}=await seed(kind),app=harness(kind,storage);await restore(app);
      storage[failure]=true;const op=app.retry(),write=next(app);sameWrite(first,write);
      write.resolve(jsonReply({id:app.base.id,revision:2}));await tick();
      await reads(app,op,{from:app.requests.indexOf(write)+1,revision:5});
      assert.equal(app.state().revision,5);assert(app.state().pending);assert(app.state().coverageDisabled);
      assert.equal(storage.entries.size,1);assert(app.nodes.recoveryMessage.textContent.includes('尚未清除'));
      const count=app.requests.length;await app.save();assert.equal(app.requests.length,count,'Cleanup failure must not start a second write');
      storage[failure]=false;await finishRetry(app,first);
      passed(failure+' after successful read blocks new write and retains original retry');
    }
    for(const change of ['external_path','read_path','body_extra','revision_mismatch','namespace','duplicate_key','nonfinite','deep','oversize']){
      const {storage}=await seed(kind),key=[...storage.entries.keys()][0],entry=JSON.parse(storage.entries.get(key));
      if(change==='external_path')entry.intent.path='https://example.invalid/exfiltrate';
      if(change==='read_path')entry.intent.readPath='/api/research/tasks/another';
      if(change==='body_extra'){const body=JSON.parse(entry.intent.body);body.Authorization='PRIVATE_CREDENTIAL_CANARY';entry.intent.body=JSON.stringify(body);}
      if(change==='revision_mismatch'){const body=JSON.parse(entry.intent.body);body.revision++;entry.intent.body=JSON.stringify(body);}
      if(change==='namespace')entry.namespace=kind==='research'?'explanation':'research';
      if(change==='duplicate_key')entry.intent.body=entry.intent.body.replace('{','{"revision":1,');
      if(change==='nonfinite')entry.intent.body=entry.intent.body.replace('"revision":1','"revision":1e400');
      if(change==='deep')entry.intent.body='['.repeat(20)+'0'+']'.repeat(20);
      let serialized=JSON.stringify(entry);if(change==='oversize')serialized+=' '.repeat(70000);
      storage.entries.set(key,serialized);const app=harness(kind,storage);await restore(app);
      assert.equal(app.state().journalIssue,'invalid');assert(app.state().retryDisabled);
      assert.equal(app.nodes.recoveryMeta.textContent,'');assert.equal(storage.entries.get(key),serialized);
      assert(!app.requests.some(request=>request.url.startsWith('https:')));
      assert(!JSON.stringify(app.state()).includes('PRIVATE_CREDENTIAL_CANARY'));
      passed('tampered '+change+' retained but never used or echoed');
    }
    {
      const {storage}=await seed(kind),key=[...storage.entries.keys()][0];storage.entries.set(key,'{broken');
      const app=harness(kind,storage);await restore(app);storage.failRemove=true;
      app.nodes.confirmClearJournal.checked=true;const count=app.requests.length;await app.nodes.clearJournal.click();
      assert.equal(app.requests.length,count);assert(app.state().pending);assert.equal(storage.entries.get(key),'{broken');
      assert(app.state().error);storage.failRemove=false;
      const operation=app.nodes.clearJournal.click();await reads(app,operation,{from:count});assert.equal(storage.entries.size,0);
      passed('explicit cleanup failure preserves blocking state until confirmed retry');
    }
    for(const stage of ['response','body']){
      const {storage}=await seed(kind),app=harness(kind,storage);app.run('clear()');const op=app.run('run(connect)');
      const target=await awaitRestoreTarget(app);let release;
      if(stage==='body'){target.resolve({ok:true,status:200,text:()=>new Promise(resolve=>{release=resolve;})});await tick();}
      await app.changeIdentity();
      if(stage==='response')target.resolve(jsonReply(app.show(4)));else release(JSON.stringify(app.show(4)));
      await op;assert.equal(app.state().id,null);assert.equal(app.state().revision,null);assert.equal(app.nodes.recoveryMeta.textContent,'');
      assert.equal(app.nodes.identityLabel.textContent,kind==='research'?'尚未连接身份':'尚未连接');
      passed('identity change discards late restore '+stage);
    }
  }
  process.stdout.write(JSON.stringify({passed:results.length,checks:results,scope:'Actual page/journal/flow code in fresh VM contexts with shared synthetic sessionStorage and controlled fetch; registered render fixtures. Not real browser persistence, server restart, identity proof, or financial/model evaluation.'},null,2)+'\n');
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});

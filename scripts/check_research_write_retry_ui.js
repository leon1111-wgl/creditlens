'use strict';

// Actual shipped page and shared-controller code, with controlled fetch promises.
// Registered fixture content is used for rendering; actors and failures are synthetic.
// This is not a browser, network-fault, database, or financial-quality evaluation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = process.argv[2] ? path.resolve(process.argv[2]) : path.resolve(__dirname, '..');
const shared = fs.readFileSync(path.join(root, 'creditlens/research-write-intent.js'), 'utf8');
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
function harness(kind) {
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
  const context=vm.createContext({document,Node:Element,Blob,URL:LocalURL,URLSearchParams,
    location:{search:''},crypto,structuredClone,Intl,setTimeout:fn=>fn(),
    fetch:(url,options={})=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))});
  const run=code=>vm.runInContext(code,context,{timeout:1000});
  run(shared);run(source);
  const base=record(kind==='research'?parentExport:childExport,kind);
  const p=record(childExport.snapshot.content.evidence.parent_export,'research',7,'reviewed');
  run("$('role').value='analyst';$('key').value='synthetic-key';session={role:'analyst',user_id:'synthetic-author'};");
  if(kind==='explanation')run('parent='+JSON.stringify(p)+';');
  run((kind==='research'?'load':'loadReview')+'('+JSON.stringify(base)+');');
  function state(){return JSON.parse(run(`JSON.stringify({pending:writeFlow.pending(),busy,dirty,epoch,
    revision:${kind==='research'?'task':'review'}?.revision??null,
    id:${kind==='research'?'task':'review'}?.id??null,
    status:$('status').textContent,error:$('error').textContent,
    retryDisabled:$('retryWrite').disabled,coverageDisabled:$('coverage').disabled,
    discardHidden:$('discardDraft').hidden,questionDisabled:${kind==='research'?"$('question').disabled":'null'},
    coverage:$('coverage').value,recoveryHidden:$('writeRecovery').hidden})`));}
  const show=(revision,status='draft')=>{const item={...clone(base),revision,status};
    if(status==='in_review'){item.snapshot={id:'synthetic-snapshot',sha256:'a'.repeat(64)};item.snapshot_sha256=item.snapshot.sha256;}
    return item;};
  const edit=()=>run("$('coverage').value='SYNTHETIC_UNSAVED_CANARY';markDirty();");
  const save=()=>run('run(save)');
  const action=kindAction=>{const isReview=kindAction!=='submit';
    run(`session={role:'${isReview?'reviewer':'analyst'}',user_id:'synthetic-${isReview?'reviewer':'author'}'};`);
    run((kind==='research'?'load':'loadReview')+'('+JSON.stringify(show(1,isReview?'in_review':'draft'))+');');
    run("$('actionNote').value='SYNTHETIC_ACTION_NOTE with complete scope';");
    return run(`run(()=>${kind==='research'?'action':'act'}(${JSON.stringify(kindAction)}))`);};
  const retry=()=>nodes.retryWrite.click();
  const changeIdentity=()=>{nodes.key.value='different-key';return Promise.all((nodes.key.listeners.input||[]).map(fn=>fn()));};
  return {kind,run,state,requests,nodes,base,show,edit,save,action,retry,changeIdentity,downloads,urls,
    prefix:kind==='research'?'/api/research/tasks/':'/api/research/explanation-reviews/'};
}
function next(app, index=-1){return app.requests.at(index);}
function sameWrite(a,b){assert.equal(b.url,a.url);assert.equal(b.options.method,a.options.method);
  assert.equal(b.options.body,a.options.body);assert.equal(b.options.headers['Idempotency-Key'],a.options.headers['Idempotency-Key']);}
async function finish(app,operation,revision=2,status='draft',receiptRevision=2){
  const write=next(app);assert(['PATCH','POST'].includes(write.options.method));
  write.resolve(jsonReply({id:app.base.id,revision:receiptRevision}));await tick();
  assert.equal(app.state().revision,1,'Receipt must not render as current record');
  const current=next(app);assert.equal(current.url,app.prefix+app.base.id);assert(!current.options.method);
  current.resolve(jsonReply(app.show(revision,status)));await tick();
  const list=next(app);assert.notEqual(list,current);list.resolve(jsonReply([]));await operation;
  assert.equal(app.state().pending,false);assert.equal(app.state().revision,revision);assert.equal(app.state().dirty,false);
}
async function main(){
  for(const kind of ['research','explanation']){
    const passed=name=>results.push(kind+': '+name);
    for(const failure of ['fetch','body','json','500']){
      const app=harness(kind);app.edit();const attempt=app.save(),original=next(app);
      if(failure==='fetch')original.reject(new Error('synthetic connection loss'));
      if(failure==='body')original.resolve({ok:true,status:200,text:async()=>{throw new Error('synthetic body loss');}});
      if(failure==='json')original.resolve({ok:true,status:200,text:async()=>'{bad'});
      if(failure==='500')original.resolve(jsonReply({detail:'synthetic failure'},500));
      await attempt;assert(app.state().pending);assert(app.state().dirty);assert(app.state().coverageDisabled);
      assert(!app.state().retryDisabled);assert(app.state().discardHidden);
      const n=app.requests.length;await app.save();assert.equal(app.requests.length,n,'Second write must not replace pending intent');
      const retry=app.retry();sameWrite(original,next(app));await finish(app,retry,5);
      assert.equal(app.state().coverageDisabled,false);if(kind==='research')assert.equal(app.state().questionDisabled,false);
      app.edit();const newWrite=app.save();assert.notEqual(next(app).options.headers['Idempotency-Key'],original.options.headers['Idempotency-Key']);
      next(app).resolve(jsonReply({detail:'synthetic rejected'},422));await newWrite;
      passed(failure+' preserves original body/key, then reads newer version');
    }
    for(const failure of ['fetch','body','403','wrong_revision']){
      const app=harness(kind);app.edit();const operation=app.save(),original=next(app);
      original.resolve(jsonReply({id:app.base.id,revision:2}));await tick();
      const read=next(app);if(failure==='fetch')read.reject(new Error('synthetic current GET loss'));
      if(failure==='body')read.resolve({ok:true,status:200,text:async()=>{throw new Error('synthetic GET body loss');}});
      if(failure==='403')read.resolve(jsonReply({detail:'synthetic read denied'},403));
      if(failure==='wrong_revision')read.resolve(jsonReply(app.show(1)));
      await operation;assert(app.state().pending);assert.equal(app.state().revision,1);
      const retry=app.retry();sameWrite(original,next(app));await finish(app,retry,4);
      passed('current GET '+failure+' retains acknowledged intent');
    }
    for(const status of [400,401,403,404,409,413,415,422]){
      const app=harness(kind);app.edit();const operation=app.save();next(app).resolve(jsonReply({detail:'synthetic definitive rejection'},status));
      await operation;assert(!app.state().pending);assert(app.state().dirty);assert.equal(app.state().revision,1);
      assert.equal(app.state().coverage,'SYNTHETIC_UNSAVED_CANARY');assert(!app.state().discardHidden);
      assert(!app.state().coverageDisabled);passed(status+' preserves draft and releases rejected intent');
    }
    for(const action of ['submit','approve','return']){
      const app=harness(kind),operation=app.action(action),original=next(app);
      assert.equal(JSON.parse(original.options.body).action,action);
      if(action!=='submit')assert.equal(JSON.parse(original.options.body).snapshot_sha256,'a'.repeat(64));
      original.reject(new Error('synthetic committed-but-unreceived'));await operation;
      const retry=app.retry();sameWrite(original,next(app));
      await finish(app,retry,2,{submit:'in_review',approve:'reviewed',return:'returned'}[action]);
      passed(action+' retry preserves action, note, revision and snapshot');
    }
    for(const stage of ['fetch_success','fetch_failure','body_success','body_failure','get_success','get_failure']){
      const app=harness(kind);app.edit();const operation=app.save();let pending=next(app),bodyResolve,bodyReject;
      if(stage.startsWith('body')){pending.resolve({ok:true,status:200,text:()=>new Promise((resolve,reject)=>{bodyResolve=resolve;bodyReject=reject;})});await tick();}
      if(stage.startsWith('get')){pending.resolve(jsonReply({id:app.base.id,revision:2}));await tick();pending=next(app);}
      await app.changeIdentity();assert.equal(app.state().id,null);assert(!app.state().pending);
      if(stage==='body_success')bodyResolve(JSON.stringify({id:app.base.id,revision:2}));
      else if(stage==='body_failure')bodyReject(new Error('late private body failure'));
      else if(stage.endsWith('failure'))pending.reject(new Error('late private transport failure'));
      else pending.resolve(jsonReply(stage.startsWith('get')?app.show(4):{id:app.base.id,revision:2}));
      await operation;assert.equal(app.state().id,null);assert(!app.state().pending);
      assert.equal(app.state().error,'');assert.equal(app.state().status,'');
      assert.equal(app.state().coverage,'');passed('identity change drops late '+stage);
    }
    for(const late of ['success','failure']){
      const app=harness(kind);app.edit();const oldOperation=app.save(),oldRequest=next(app);
      await app.changeIdentity();
      app.run("session={role:'analyst',user_id:'synthetic-new-author'};$('role').value='analyst';");
      if(kind==='explanation')app.run("parent={id:'synthetic-new-parent',status:'reviewed'};");
      app.run((kind==='research'?'load':'loadReview')+'('+JSON.stringify(app.show(1))+');');
      app.edit();const newOperation=app.save(),newRequest=next(app);
      if(late==='success')oldRequest.resolve(jsonReply({id:app.base.id,revision:2}));
      else oldRequest.reject(new Error('synthetic old identity failure'));
      await oldOperation;
      assert(app.state().busy);assert(app.state().pending);assert.equal(app.state().error,'');
      assert.equal(app.requests.length,2,'Old response must not start a read under the new identity');
      assert.notEqual(oldRequest.options.headers['Idempotency-Key'],newRequest.options.headers['Idempotency-Key']);
      await finish(app,newOperation,2);passed('old identity '+late+' cannot clear new pending write');
    }
    {
      const app=harness(kind);app.edit();const operation=app.nodes.discardDraft.click();
      assert(!next(app).options.method);next(app).resolve(jsonReply(app.show(4)));await tick();
      next(app).resolve(jsonReply([]));await operation;assert.equal(app.state().revision,4);assert(!app.state().dirty);
      assert(app.requests.every(r=>!r.options.method));passed('explicit discard performs only GET and replaces draft');
    }
    {
      const app=harness(kind);app.edit();const operation=app.nodes.discardDraft.click();next(app).reject(new Error('synthetic discard GET failed'));
      await operation;assert(app.state().dirty);assert.equal(app.state().coverage,'SYNTHETIC_UNSAVED_CANARY');passed('failed discard preserves draft');
    }
    {
      const app=harness(kind);app.edit();const operation=app.save();next(app).resolve(jsonReply({id:app.base.id,revision:2}));await tick();
      const updated=app.show(2);updated.coverage_note='<script>SYNTHETIC_NO_EXECUTION</script>';
      next(app).resolve(jsonReply(updated));await tick();next(app).reject(new Error('synthetic list failure'));
      await operation;assert(!app.state().pending);assert.equal(app.state().revision,2);assert(!app.state().dirty);
      assert.equal(app.state().coverage,updated.coverage_note);assert(app.state().retryDisabled);
      passed('list failure after known current version does not retry write');
    }
    {
      const app=harness(kind);app.edit();const operation=app.save();next(app).resolve(jsonReply({id:'other-record',revision:2}));await operation;
      assert(app.state().pending);assert.equal(app.requests.length,1);assert.equal(app.state().revision,1);
      passed('wrong receipt identity retains intent and does not load record');
    }
  }
  process.stdout.write(JSON.stringify({status:'passed',checks:results.length,cases:results,
    scope:'Actual two HTML scripts and shared write flow; registered fixture content, synthetic actors and controlled fetch/body promises. No browser, real network fault, database, OCR or model evaluation.'},null,2)+'\n');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';

// Actual shipped research page and shared controller, with controlled query promises.
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

const reportPath=process.argv[3]?path.resolve(process.argv[3]):path.join(root,'artifacts/research-query-report.json');
const report=JSON.parse(fs.readFileSync(reportPath,'utf8'));
assert(Array.isArray(report.cases)&&report.cases.length,'Formal query report required; no skipped real fixtures');
const real=report.cases.filter(c=>c.result&&!c.error);
const find=(kind,status,issuer)=>{
  const c=real.find(c=>c.result.query.task_kind===kind&&c.result.status===status&&(!issuer||c.result.query.observations[0].issuer_id===issuer));
  assert(c,'Missing formal fixture: '+[kind,status,issuer].join('/'));return c.result;
};
const lookup=find('metric_lookup','evidence_available','MSFT');
const compare=find('comparable_change','computed','MSFT');
const operating=find('operating_evidence','insufficient','MSFT');
const fixtureIds=new Set();
function setup(result=lookup){
  const app=harness('research'),exp=fixture('research-cash-flow-demo.json');
  const t=record(exp,'research',result.revision,'draft');t.id=result.task_id;t.include_cash_flow_bridge=true;
  assert.equal(t.bundle_sha256,result.bundle_sha256,'Task and query materials must match');
  app.run('load('+JSON.stringify(t)+');');app.task=t;
  app.qstate=()=>JSON.parse(app.run("JSON.stringify({raw:queryRaw,result:queryResult,loading:queryLoading,generation:queryGeneration,text:$('queryResults').textContent,status:$('queryState').textContent,exportDisabled:$('exportQuery').disabled,queryDisabled:$('queryEvidence').disabled,dirty,busy,pending:writeFlow.pending()})"));
  select(app,result.query);return app;
}
function select(app,q){
  const values={queryKind:q.task_kind,queryIssuer:q.observations[0].issuer_id,
    queryMetric:q.observations[0].metric,queryYear:String(q.observations[0].year),
    queryOtherIssuer:q.observations[1]?.issuer_id||q.observations[0].issuer_id,
    queryOtherYear:String(q.observations[1]?.year||2025)};
  for(const [id,value]of Object.entries(values))app.nodes[id].value=value;
  app.run('querySelectionChanged();');
}
function response(result){
  const raw=JSON.stringify(result).replace(/"page_height":792(?=[,}])/g,'"page_height":792.0')+'\n';
  return {raw,ok:true,status:200,text:async()=>raw};
}
async function success(app,result){
  const op=app.run('queryEvidence()'),request=app.requests.at(-1),reply=response(result);
  assert.equal(request.options.method,'POST');assert.deepEqual(JSON.parse(request.options.body),result.query);
  request.resolve(reply);await op;assert.equal(app.qstate().raw,reply.raw);assert.equal(app.qstate().loading,false);
  fixtureIds.add(result.query_sha256);return reply.raw;
}
function changed(app,type){
  if(type==='identity')return app.changeIdentity();
  if(type==='edit')return app.run('markDirty();');
  if(type==='saved_revision')return app.run('load('+JSON.stringify({...app.task,revision:app.task.revision+1})+');');
  if(type==='different_task')return app.run('load('+JSON.stringify({...app.task,id:'synthetic-other-task'})+');');
  if(type==='metric')return app.run("$('queryMetric').value='capex';querySelectionChanged();");
  if(type==='issuer')return app.run("$('queryIssuer').value='AAPL';querySelectionChanged();");
  if(type==='year')return app.run("$('queryYear').value='1900';querySelectionChanged();");
  if(type==='kind')return app.run("$('queryKind').value='comparable_change';querySelectionChanged(true);");
  throw Error(type);
}
async function main(){
  for(const result of [lookup,compare,operating,find('operating_evidence','insufficient','AAPL')]){
    const app=setup(result),raw=await success(app,result);
    assert(app.qstate().text.includes(result.query.observations[0].issuer_id));
    app.run('exportQuery()');assert.equal(app.downloads.length,1);assert.equal(await app.urls[0].blob.text(),raw);
    results.push('formal '+result.query.task_kind+'/'+result.query.observations[0].issuer_id+' renders and exports exact response');
    if(result===operating){
      const text=app.qstate().text;
      assert(text.includes('本期经营现金流'));assert(text.includes('经营现金流年度增量'));
      for(const item of result.operating_evidence.company_statements[0].numeric_checks){
        const formatted=String(item.exact_value).replace(/\B(?=(\d{3})+(?!\d))/g,',');
        assert(text.includes(formatted));assert(text.includes(item.reported_rounded_value));
      }
      results.push('company current amount and annual increment remain distinct and readable');
    }
  }
  {
    const missing=real.find(c=>c.result.status==='not_registered'&&c.result.query.observations[0].year===2023)?.result;
    assert(missing,'Formal unregistered-period fixture required');const app=setup(missing);await success(app,missing);
    assert(app.qstate().text.includes('不代表未披露'));assert.equal(app.qstate().result.selected_evidence[0].card,null);
    results.push('unregistered period keeps empty evidence and does not claim nondisclosure');
  }
  for(const type of ['metric','issuer','year','kind','edit','saved_revision','different_task','identity']){
    for(const stage of ['response','body']){
      const app=setup(),op=app.run('queryEvidence()'),req=app.requests.at(-1);let release;
      if(stage==='body'){req.resolve({ok:true,status:200,text:()=>new Promise(resolve=>{release=resolve;})});await tick();}
      await changed(app,type);assert.equal(app.qstate().raw,null);assert.equal(app.qstate().text,'');
      const statusAfterChange=app.qstate().status;
      if(stage==='body')release(response(lookup).raw);else req.resolve(response(lookup));
      await op;assert.equal(app.qstate().raw,null);assert.equal(app.qstate().text,'');
      assert.equal(app.qstate().status,statusAfterChange);assert(app.qstate().exportDisabled);
      results.push(type+' drops late '+stage);
    }
  }
  for(const failure of ['401','409','500','fetch','body','json']){
    const app=setup();await success(app,lookup);const op=app.run('queryEvidence()'),req=app.requests.at(-1);
    assert.equal(app.qstate().raw,null);
    if(failure==='fetch')req.reject(new Error('synthetic fetch failure'));
    else if(failure==='body')req.resolve({ok:true,status:200,text:async()=>{throw new Error('synthetic response body failure');}});
    else if(failure==='json')req.resolve({ok:true,status:200,text:async()=>'{invalid'});
    else req.resolve(jsonReply({detail:'synthetic request failure'},Number(failure)));
    await op;assert.equal(app.qstate().raw,null);assert.equal(app.qstate().text,'');assert(app.qstate().exportDisabled);
    assert(!app.qstate().loading);await success(app,lookup);results.push(failure+' clears prior query and allows retry');
  }
  const mutations=[
    ['wrong task',d=>d.task_id='other'],['wrong revision',d=>d.revision+=1],
    ['wrong explicit query',d=>d.query.observations[0].year=1900],['wrong bundle',d=>d.bundle_sha256='f'.repeat(64)],
    ['wrong selected selector',d=>d.selected_evidence[0].selector.issuer_id='AAPL'],
    ['wrong selected card',d=>d.selected_evidence[0].card.issuer_id='AAPL'],
    ['wrong binding task',d=>d.input_binding.task_id='different-task'],
    ['wrong binding revision',d=>d.input_binding.revision+=1],
    ['wrong binding bundle',d=>d.input_binding.bundle_sha256='a'.repeat(64)],
    ['wrong binding query',d=>d.input_binding.query_sha256='b'.repeat(64)],
    ['wrong binding source',d=>d.input_binding.source_bindings_sha256='c'.repeat(64)],
    ['automatic approval',d=>d.automatic_approval=true],
    ['claims language understanding',d=>d.natural_language_question_understood=true],
    ['extra card',d=>d.selected_evidence.push(clone(d.selected_evidence[0]))]
  ];
  for(const [name,mutate]of mutations){
    const app=setup(),d=clone(lookup);mutate(d);const op=app.run('queryEvidence()');app.requests.at(-1).resolve(response(d));await op;
    assert.equal(app.qstate().raw,null,'Unsafe response accepted: '+name);assert.equal(app.qstate().text,'');assert(app.qstate().exportDisabled);
    results.push(name+' response refused');
  }
  {
    const app=setup(),old=app.run('queryEvidence()'),oldRequest=app.requests.at(-1);
    select(app,compare.query);const newer=app.run('queryEvidence()'),newRequest=app.requests.at(-1);
    newRequest.resolve(response(compare));await newer;const saved=app.qstate().raw;
    oldRequest.reject(new Error('synthetic old query failure'));await old;
    assert.equal(app.qstate().raw,saved);assert.equal(app.qstate().result.query.task_kind,'comparable_change');
    results.push('older query failure cannot erase newer result');
  }
  {
    const app=setup();const op=app.run('queryEvidence()'),request=app.requests.at(-1);
    const writing=app.save(),write=app.requests.at(-1);assert.equal(write.options.method,'PATCH');
    assert(app.qstate().pending);assert.equal(app.qstate().raw,null);
    request.resolve(response(lookup));await op;assert.equal(app.qstate().raw,null);
    write.resolve(jsonReply({detail:'synthetic rejected write'},422));await writing;
    results.push('starting write invalidates pending read');
  }
  {
    const app=setup(operating),d=clone(operating);d.operating_evidence.company_statements[0].paraphrase_zh='<img src=x onerror=SYNTHETIC>';
    await success(app,d);assert(app.qstate().text.includes('<img src=x onerror=SYNTHETIC>'));
    results.push('untrusted statement text rendered literally without innerHTML');
  }
  for(const value of ['', '1.5', '0', '10000']){
    const app=setup();app.nodes.queryYear.value=value;const count=app.requests.length;
    await app.run('queryEvidence()');assert.equal(app.requests.length,count);assert.equal(app.qstate().raw,null);
    results.push('invalid year '+JSON.stringify(value)+' sends no request');
  }
  {
    const app=setup();await success(app,lookup);app.edit();assert.equal(app.qstate().raw,null);
    assert.throws(()=>app.run('exportQuery()'));assert(app.qstate().queryDisabled);
    results.push('dirty draft blocks query and old export');
  }
  {
    const app=setup();await success(app,lookup);app.nodes.queryYear.value='1900';
    assert.throws(()=>app.run('exportQuery()'));assert.equal(app.downloads.length,0);
    results.push('download rechecks actual selector values even without change event');
  }
  process.stdout.write(JSON.stringify({status:'passed',checks:results.length,cases:results,formal_query_fixture_count:fixtureIds.size,
    scope:'Shipped research HTML and shared write flow; formal query results and registered task content, controlled promises. Not browser, real network, human reference or model evaluation.'},null,2)+'\n');
}
main().catch(error=>{console.error(error);process.exitCode=1;});


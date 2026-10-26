'use strict';
// Actual HTML functions, controlled file reads and HTTP replies. This is not
// browser rendering, a human reference exercise, or a financial quality score.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),{execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),htmlPath=path.resolve(process.argv[2]||path.join(root,'creditlens/table-lookup.html'));
const fixtureRoot=path.resolve(process.argv[3]||root),checkPath=path.resolve(process.argv[4]||path.join(fixtureRoot,'artifacts/row-selection-verification-report.json'));
const read=p=>{const raw=fs.readFileSync(p);return {report:JSON.parse(raw),report_sha256:crypto.createHash('sha256').update(raw).digest('hex')};};
const parent=read(path.join(fixtureRoot,'artifacts/role-table-lookup-report.json')),rows=read(path.join(fixtureRoot,'artifacts/row-disambiguation-report.json'));
const q=rows.report.queries[0],first=parent.report.documents.find(d=>d.id===q.document_id).cases.find(c=>c.page===q.page&&c.query.row_label===q.query.row_label&&c.query.year===q.query.year);
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);assert(start>=0&&end>start);
const source=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
// Standard-library Python preserves int/float distinctions that JSON.parse +
// JSON.stringify would erase. The resulting bytes must match the frozen fixture
// hashes below before a recorded verification response is allowed to be used.
const fixtureCode=String.raw`
import json,hashlib,sys
from pathlib import Path
raw=Path(sys.argv[1]).read_bytes(); report=json.loads(raw); report_sha=hashlib.sha256(raw).hexdigest(); items=[]
for q in report['queries']:
 for p in q['paths']:
  value={'schema_version':1,'report_sha256':report_sha,'role_report_sha256':report['role_report_sha256'],
   'query_id':q['id'],'document_id':q['document_id'],'page':q['page'],'query':q['query'],'source':q['source'],
   'page_metadata':q['page_metadata'],'discovery':q['discovery'],'result':p['result'],'read_only':True,'persisted':False,
   'full_ocr_replayed':False,'automatic_approval':False,'selection_basis':'explicit_request_for_saved_line_layout_result'}
  text=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
  items.append({'query_id':q['id'],'row_index':p['row_index'],'raw':text})
print(json.dumps(items,ensure_ascii=True))
`;
const localPython=path.join(fixtureRoot,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const python=process.env.CREDITLENS_TEST_PYTHON||(fs.existsSync(localPython)?localPython:'python');
const exactFixtures=JSON.parse(execFileSync(python,['-c',fixtureCode,path.join(fixtureRoot,'artifacts/row-disambiguation-report.json')],{encoding:'utf8',maxBuffer:1024*1024,windowsHide:true}));
const original=exactFixtures[0].raw;
const matched=()=>({status:'matched',issues:[],issues_truncated:false,bound_selection:{query_id:q.id,row_index:q.paths[0].row_index},synthetic_height:792,
 scope:{persisted:false,human_gold:false,automatic_approval:false},limitations:['Synthetic state-control response']});
const reply=value=>{const raw=JSON.stringify(value).replace('"synthetic_height":792','"synthetic_height":792.0')+'\n';return {ok:true,status:200,text:async()=>raw,raw};};
const buffer=raw=>{const b=Buffer.isBuffer(raw)?raw:Buffer.from(raw);return b.buffer.slice(b.byteOffset,b.byteOffset+b.byteLength);};
function harness(){const nodes={},env={pending:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='',value='';return {tagName:tag.toUpperCase(),disabled:false,hidden:false,children:[],style:{},dataset:{},files:[],
  get value(){return value;},set value(v){value=String(v);if(this.type==='file'&&value==='')this.files=[];},
  get textContent(){return text+this.children.map(c=>c.textContent||'').join('');},set textContent(v){text=String(v);this.children=[];},
  set innerHTML(_){throw Error('Untrusted text may not become HTML');},append(...items){const empty=!this.children.length;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){if(!nodes[id]){nodes[id]=node(['role','issuer','filter','case','experiment','transitionFrom'].includes(id)?'select':id==='selectionFile'?'input':'div');if(id==='selectionFile')nodes[id].type='file';}return nodes[id];},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:verify-'+(env.created.length+1);env.created.push({value,blob});return value;}static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,TextEncoder,Uint8Array,setTimeout:fn=>fn(),fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject}))});
 const run=s=>vm.runInContext(s,context,{timeout:1000});run(source);run("$('role').value='viewer';$('filter').value='all';$('experiment').value='role';$('transitionFrom').value='line'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,verificationEpoch,verificationRaw,status:$('selectionCheckStatus').textContent,result:$('selectionCheckResult').textContent,
  disabled:$('verifySelection').disabled,exportDisabled:$('selectionCheckExport').disabled,text:$('selectionText').value,files:$('selectionFile').files.length})`));
 return {nodes,env,run,state};}
async function populated(){const app=harness();let p=app.run('load()');app.env.pending.at(-1).resolve(reply(parent));await p;
 app.run("$('case').value="+JSON.stringify(first.id)+";renderCase()");p=app.run('loadRowChoices()');app.env.pending.at(-1).resolve(reply(rows));await p;return app;}
function paste(app,text=original){app.nodes.selectionText.value=text;app.run("$('selectionText').oninput()");}
function file(app,raw=original,{defer=false,size}={}){let resolve,reject;const bytes=buffer(raw),promise=defer?new Promise((a,b)=>{resolve=a;reject=b;}):Promise.resolve(bytes);
 app.nodes.selectionFile.files=[{name:'control.json',size:size??bytes.byteLength,arrayBuffer:()=>promise}];app.run("$('selectionFile').onchange()");return {resolve:()=>resolve(bytes),reject};}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function started(app){const p=app.run('verifySelectionExport()');await tick();return {p,request:app.env.pending.at(-1)};}
async function complete(app,value=matched()){const {p,request}=await started(app),response=reply(value);request.resolve(response);await p;return response;}
function change(app,kind){if(kind==='identity')app.run("$('key').oninput()");else if(kind==='clear')app.run("$('clearSelectionCheck').onclick()");
 else if(kind==='query')app.run("$('case').value="+JSON.stringify(parent.report.documents[0].cases[0].id)+";renderCase()");
 else if(kind==='selection'){const p=app.run('selectRow('+q.paths[0].row_index+')');app.env.pending.at(-1).resolve(reply({result:q.paths[0].result}));return p;}
 else if(kind==='candidate_reload'){const p=app.run('loadRowChoices()');app.env.pending.at(-1).resolve(reply(rows));return p;}
 else if(kind==='text')paste(app,'new pending text');else if(kind==='file')file(app,'new pending file');}
async function downloaded(app){app.run("$('selectionCheckExport').onclick()");const d=app.env.downloads.at(-1);return {name:d.name,text:await app.env.created.find(x=>x.value===d.href).blob.text()};}
async function main(){const passed=[];
 {const app=await populated(),raw='{"x":1,"x":2,"height":792.0}\n';paste(app,raw);const {p,request}=await started(app);
  assert.equal(request.url,'/api/row-selection-verification');assert.equal(request.options.method,'POST');assert.equal(Buffer.from(request.options.body).toString('utf8'),raw);
  request.resolve({ok:false,status:400});await p;assert(app.state().exportDisabled);assert.equal(app.state().text,raw);passed.push('pasted_duplicate_keys_and_float_literal_are_sent_without_json_rewrite');}
 {const app=await populated(),bytes=Buffer.from([123,34,120,34,58,34,255,34,125]);file(app,bytes);const {p,request}=await started(app);
  assert.deepEqual(Buffer.from(request.options.body),bytes);request.resolve({ok:false,status:400});await p;passed.push('file_bytes_preserved_including_invalid_utf8');}
 for(const kind of ['identity','clear','query','selection','candidate_reload','text','file']){const app=await populated(),read=file(app,original,{defer:true}),before=app.env.pending.length,p=app.run('verifySelectionExport()');
  await change(app,kind);const expected=app.state(),count=app.env.pending.length;read.resolve();await p;assert.deepEqual(app.state(),expected);assert.equal(app.env.pending.length,count);assert(count>=before);
  passed.push('late_file_read_after_'+kind);}
 for(const kind of ['identity','clear','query','selection','candidate_reload','text','file']){const app=await populated();paste(app);const {p,request}=await started(app);
  await change(app,kind);const expected=app.state();request.resolve(reply(matched()));await p;assert.deepEqual(app.state(),expected);passed.push('late_verification_response_after_'+kind);}
 {const app=await populated();paste(app);const first=await started(app);paste(app,original+'\n');const second=await started(app);second.request.resolve(reply(matched()));await second.p;
  const expected=app.state();first.request.reject(Error('old failure'));await first.p;assert.deepEqual(app.state(),expected);passed.push('old_network_failure_cannot_clear_new_check');}
 {const app=await populated(),read=file(app,original,{defer:true}),before=app.env.pending.length,p=app.run('verifySelectionExport()');
  read.reject(Error('Synthetic file read failure'));await p;assert.equal(app.env.pending.length,before);assert(app.state().exportDisabled);assert(!app.state().disabled);
  file(app);await complete(app);assert(!app.state().exportDisabled);passed.push('failed_file_read_does_not_post_and_retry_can_succeed');}
 {const app=await populated();paste(app);const {p,request}=await started(app);let finish;
  request.resolve({ok:true,status:200,text:()=>new Promise(resolve=>{finish=resolve;})});await tick();assert.equal(typeof finish,'function');
  change(app,'identity');const expected=app.state();finish(reply(matched()).raw);await p;assert.deepEqual(app.state(),expected);passed.push('late_response_body_after_identity_is_ignored');}
 for(const failure of [401,409,500,'json','schema']){const app=await populated();paste(app);await complete(app);const {p,request}=await started(app);
  assert(app.state().exportDisabled);assert.equal(app.state().verificationRaw,null);
  request.resolve(failure==='json'?{ok:true,status:200,text:async()=>'{bad'}:failure==='schema'?reply({status:'unknown',issues:[]}):{ok:false,status:failure});await p;
  assert(app.state().exportDisabled);assert.equal(app.state().result,'');assert.equal(app.state().text,original);assert(!app.state().disabled);
  await complete(app);assert(!app.state().exportDisabled);passed.push(failure+'_clears_old_result_and_allows_same_input_retry');}
 for(const mode of ['file_metadata','file_actual','text_bytes']){const app=await populated(),before=app.env.pending.length;
  if(mode==='file_metadata')file(app,'x',{size:131073});else if(mode==='file_actual')file(app,Buffer.alloc(131073),{size:1});else paste(app,'汉'.repeat(50000));
  await app.run('verifySelectionExport()');assert.equal(app.env.pending.length,before);assert(app.state().exportDisabled);assert(app.state().status.includes('128 KiB'));passed.push('oversize_'+mode+'_blocked_before_post');}
 {const app=await populated();paste(app);const response=await complete(app);const out=await downloaded(app);assert.equal(out.text,response.raw);assert.equal(out.name,'creditlens-row-selection-check.json');assert(out.text.includes('792.0'));
  app.run("$('key').oninput()");assert.equal(app.state().verificationRaw,null);assert.equal(app.state().text,'');assert.equal(app.state().files,0);assert(app.state().exportDisabled);passed.push('verification_export_keeps_response_bytes_and_identity_clears_all_inputs');}
 {const app=await populated();paste(app);const value={status:'mismatch',issues:[{code:'synthetic',path:'<img src=x>',message:'<script>unsafe()</script>'}],issues_truncated:true};
  await complete(app,value);assert(app.state().result.includes('<script>'));assert(app.state().result.includes('前 16'));assert(!app.state().exportDisabled);passed.push('untrusted_result_text_is_inert_and_mismatch_is_not_approval');}
 let actualPaths=0;
 assert(fs.existsSync(checkPath),'Frozen verification report is required for all eight response fixtures');
 {const checks=JSON.parse(fs.readFileSync(checkPath,'utf8'));assert.equal(checks.registered_paths.cases.length,8);
  for(const item of checks.registered_paths.cases){const exact=exactFixtures.find(x=>x.query_id===item.query_id&&x.row_index===item.row_index);assert(exact);const text=exact.raw;
   assert.equal(Buffer.byteLength(text,'utf8'),item.input_bytes);assert.equal(crypto.createHash('sha256').update(text,'utf8').digest('hex'),item.input_sha256);
   const app=await populated();file(app,text);const {p,request}=await started(app);assert.equal(Buffer.from(request.options.body).toString('utf8'),text);const r=reply(item.observed.verification);request.resolve(r);await p;
   assert(app.state().status.includes('逐字段一致'));assert.equal((await downloaded(app)).text,r.raw);actualPaths++;
  }
  passed.push('actual_eight_export_paths_real_verification_schema_and_downloads');}
 console.log(JSON.stringify({scope:'Actual page script with controlled file/HTTP async; not browser or human-gold validation',passed:passed.length,actualPaths,controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Verification UI check did not settle');process.exit(1);},10000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

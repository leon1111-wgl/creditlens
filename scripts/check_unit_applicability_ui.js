'use strict';

// Runs the actual page script with controlled responses. This is not a browser,
// human annotation session, OCR run, or financial accuracy measurement.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const root=path.resolve(process.argv[2]||path.join(__dirname,'..'));
const htmlPath=path.join(root,'creditlens/table-lookup.html');
const rolePath=path.join(root,'artifacts/role-table-lookup-report.json');
const rowPath=path.join(root,'artifacts/row-disambiguation-report.json');
const unitReportPath=path.resolve(process.argv[3]||path.join(root,'artifacts/unit-applicability-report.json'));
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);
assert(start>=0&&end>start);const source=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
const clone=x=>JSON.parse(JSON.stringify(x)),roleBytes=fs.readFileSync(rolePath),roleReport=JSON.parse(roleBytes);
const rowBytes=fs.readFileSync(rowPath),rowReport=JSON.parse(rowBytes),unitBytes=fs.readFileSync(unitReportPath),unitReport=JSON.parse(unitBytes);
const digest=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const rowSnapshot={report_sha256:digest(rowBytes),role_report_sha256:digest(roleBytes),report:rowReport};
const unitHashes={report_sha256:digest(unitBytes),...Object.fromEntries(['role_report_sha256','table_report_sha256','row_report_sha256'].map(k=>[k,unitReport[k]]))};
assert.equal(unitHashes.role_report_sha256,digest(roleBytes));assert.equal(unitHashes.row_report_sha256,digest(rowBytes));
assert.equal(unitReport.cases.length,42);assert.equal(unitReport.selections.length,8);
const parent={report_sha256:crypto.createHash('sha256').update(roleBytes).digest('hex'),report:roleReport};
const allCases=roleReport.documents.flatMap(d=>d.cases.map(c=>({...c,document:d})));
const ordinary=allCases.find(c=>c.methods.role.extraction.status==='selected'&&c.query.row_label==='Total revenue');
assert(ordinary,'Registered selected revenue fixture missing');
const ambiguous=roleReport.documents.flatMap(d=>d.cases.filter(c=>c.methods.role.extraction.reason==='duplicate_row_label').map(c=>({...c,document:d})));
assert.equal(ambiguous.length,6);const initial=ambiguous[0];
function reply(value){const raw=JSON.stringify(value)+'\n';return {ok:true,status:200,text:async()=>raw,raw};}
function harness(){const nodes={},env={pending:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='';return {tagName:tag.toUpperCase(),value:'',disabled:false,hidden:false,children:[],style:{},dataset:{},
  get textContent(){return text+this.children.map(c=>c.textContent||'').join('');},set textContent(v){text=String(v);this.children=[];},
  set innerHTML(_){throw Error('Untrusted text must not become HTML');},
  append(...items){const empty=!this.children.length;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
  click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){return nodes[id]??=node(['role','issuer','filter','case','experiment','transitionFrom','unitMethod'].includes(id)?'select':'div');},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:row-control-'+(env.created.length+1);env.created.push({value,blob});return value;}static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject}))});
 const run=code=>vm.runInContext(code,context,{timeout:1000});run(source);run("$('role').value='viewer';$('filter').value='all';$('experiment').value='role';$('transitionFrom').value='line';$('unitMethod').value='role'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,rowEpoch,imageEpoch,rowSaved:!!rowSaved,rowQuery:rowQuery?.id||null,rowSelectionRaw,
  panelHidden:$('rowPanel').hidden,candidates:$('rowCandidates').textContent,candidateCount:$('rowCandidates').children.length,
  result:$('rowResult').textContent,status:$('rowStatus').textContent,unitEpoch,unitRaw,unitStatus:$('unitStatus').textContent,unitResult:$('unitResult').textContent,unitDisabled:$('unitExport').disabled,unitBusy:$('unitCheck').disabled,exportDisabled:$('rowExport').disabled,pageDisabled:$('rowPage').disabled,
  previewHidden:$('preview').hidden,previewNote:$('previewNote').textContent,imageUrl,frameCount:$('pageFrame').children.length})`));
 return {nodes,env,run,state};}
async function populated(caseId=ordinary.id){const app=harness(),p=app.run('load()');app.env.pending.at(-1).resolve(reply(parent));await p;
 app.run("$('case').value="+JSON.stringify(caseId)+";renderCase()");return app;}
async function rows(app,value){const p=app.run('loadRowChoices()');app.env.pending.at(-1).resolve(reply(value));await p;return value;}
async function picked(app,index,value){const response=reply(value),p=app.run('selectRow('+index+')');app.env.pending.at(-1).resolve(response);await p;return response;}
function change(app,type){if(type==='identity')app.run("$('key').oninput()");
 else if(type==='experiment')app.run("$('experiment').value='line';$('experiment').onchange()");
 else if(type==='issuer')app.run("$('issuer').value='msft';$('issuer').onchange()");
 else if(type==='query')app.run("$('case').value="+JSON.stringify(ambiguous.at(-1).id)+";$('case').onchange()");
 else if(type==='filter')app.run("$('filter').value='wrong';$('filter').onchange()");}
// Fixtures are projections of the saved, registered reports, not recomputed
// expected financial results. Only fault tests below deliberately alter them.
function automatic(caseId=ordinary.id,method='role'){
 const c=unitReport.cases.find(c=>c.id===caseId);assert(c);
 return {schema_version:1,...unitHashes,target:{kind:'automatic',case_id:caseId,method,document_id:c.document_id,page:c.page,query:clone(c.query)},unit_check:clone(c.methods[method].unit_check),legacy_extraction_sha256:c.methods[method].legacy_extraction_sha256,read_only:true,automatic_approval:false};
}
function explicit(s=unitReport.selections[0]){return {schema_version:1,...unitHashes,target:{kind:'explicit_selection',query_id:s.query_id,row_index:s.row_index,document_id:s.document_id,page:s.page,query:clone(s.query)},unit_check:clone(s.unit_check),legacy_extraction_sha256:s.legacy_extraction_sha256,read_only:true,automatic_approval:false};}
function rowPayload(q,index){const p=q.paths.find(p=>p.row_index===index);assert(p);return {schema_version:1,report_sha256:rowSnapshot.report_sha256,role_report_sha256:parent.report_sha256,query_id:q.id,document_id:q.document_id,page:q.page,query:clone(q.query),source:clone(q.source),page_metadata:clone(q.page_metadata),discovery:clone(q.discovery),result:clone(p.result),read_only:true,persisted:false,automatic_approval:false};}
async function withSelection(s=unitReport.selections[0]){const q=rowReport.queries.find(q=>q.id===s.query_id);assert(q);const c=allCases.find(c=>c.document.id===q.document_id&&c.page===q.page&&c.query.row_label===q.query.row_label&&c.query.year===q.query.year);assert(c);const app=await populated(c.id);await rows(app,rowSnapshot);await picked(app,s.row_index,rowPayload(q,s.row_index));app.run("$('unitMethod').value='selection';$('unitMethod').onchange()");return {app,q,s};}
async function check(app,payload=automatic()){const p=app.run('loadUnitCheck()'),request=app.env.pending.at(-1),response=reply(payload);request.resolve(response);await p;return {request,response};}
function empty(app){assert.equal(app.state().unitRaw,null);assert.equal(app.state().unitResult,'');assert.equal(app.state().unitDisabled,true);}
function ready(app){assert(app.state().unitRaw);assert.equal(app.state().unitDisabled,false);assert.equal(app.state().unitBusy,false);}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function flush(){await Promise.resolve();await Promise.resolve();}
const checks=[];
async function test(name,fn){await fn();checks.push(name);}

async function main(){
 for(const method of ['text','line','word','role'])await test('registered automatic '+method,async()=>{const app=await populated();app.run("$('unitMethod').value="+JSON.stringify(method));const value=automatic(ordinary.id,method),{request}=await check(app,value);ready(app);assert(request.url.includes('method='+method));assert(request.url.includes(parent.report_sha256));assert.equal(JSON.parse(app.state().unitRaw).unit_check.status,value.unit_check.status);assert.match(app.state().unitResult,/币种：未确认/);assert.match(app.state().unitResult,/完整期间：未确认/);assert.match(app.state().unitResult,/报表范围：未确认/);});
 await test('all eight registered explicit paths',async()=>{for(const s of unitReport.selections){const {app}=await withSelection(s);const {request}=await check(app,explicit(s));ready(app);assert(request.url.includes('row_index='+s.row_index));assert(request.url.includes(rowSnapshot.report_sha256));assert.equal(JSON.parse(app.state().unitRaw).target.row_index,s.row_index);}});
 await test('no implicit selected row',async()=>{const app=await populated(initial.id);await rows(app,rowSnapshot);app.run("$('unitMethod').value='selection';$('unitMethod').onchange()");const n=app.env.pending.length;await app.run('loadUnitCheck()');empty(app);assert.equal(app.env.pending.length,n);assert.match(app.state().unitStatus,/明确选择/);});
 await test('registered unselected candidate stays unselected',async()=>{const record=unitReport.cases.find(c=>c.methods.role.unit_check.status==='not_selected');assert(record);const app=await populated(record.id);await check(app,automatic(record.id));ready(app);assert.match(app.state().unitStatus,/原方法未选出金额/);assert.doesNotMatch(app.state().unitResult,/仅作数量级展开/);});
 await test('synthetic blocked exception keeps literal text and null expansion',async()=>{const app=await populated(),payload=automatic();Object.assign(payload.unit_check,{status:'blocked',unit_ready:false,scale:null,scale_factor:null,scaled_value:null,reasons:[{code:'unsupported_unit_statement',message:'<img src=x onerror=alert(1)> unknown exception'}]});await check(app,payload);ready(app);assert.match(app.state().unitResult,/<img src=x onerror=alert\(1\)>/);assert.doesNotMatch(app.state().unitResult,/仅作数量级展开/);});
 await test('download preserves raw response text',async()=>{const app=await populated(),payload=automatic(),rawText=JSON.stringify(payload,null,2)+'\r\n';const p=app.run('loadUnitCheck()');app.env.pending.at(-1).resolve({ok:true,status:200,text:async()=>rawText});await p;app.run("$('unitExport').onclick()");const d=app.env.downloads.at(-1);assert.equal(d.name,'creditlens-unit-check.json');assert.equal(await app.env.created.find(x=>x.value===d.href).blob.text(),rawText);assert(app.env.revoked.includes(d.href));});
 for(const kind of ['method','case','report','row'])await test('download rechecks binding without DOM events '+kind,async()=>{const app=await populated();await check(app);if(kind==='method')app.run("$('unitMethod').value='text'");if(kind==='case')app.run("$('case').value="+JSON.stringify(initial.id));if(kind==='report')app.run("saved.report_sha256='b'.repeat(64)");if(kind==='row')app.run('rowEpoch++');const n=app.env.downloads.length;app.run("$('unitExport').onclick()");assert.equal(app.env.downloads.length,n);empty(app);assert.match(app.state().unitStatus,/选择已改变/);});
 const mutations={identity:a=>change(a,'identity'),experiment:a=>change(a,'experiment'),issuer:a=>change(a,'issuer'),case:a=>change(a,'query'),filter:a=>change(a,'filter'),method:a=>a.run("$('unitMethod').value='line';$('unitMethod').onchange()"),row_reload:a=>a.run('clearRowChoices()')};
 for(const [name,mutate]of Object.entries(mutations))for(const phase of ['response','body'])await test('late '+phase+' after '+name,async()=>{const app=await populated(),p=app.run('loadUnitCheck()'),request=app.env.pending.at(-1),gate=deferred();if(phase==='body'){request.resolve({ok:true,status:200,text:()=>gate.promise});await flush();}mutate(app);const before=app.state().unitStatus;if(phase==='response')request.resolve(reply(automatic()));else gate.resolve(reply(automatic()).raw);await p;empty(app);assert.equal(app.state().unitStatus,before);});
 await test('second request wins over first late success',async()=>{const app=await populated(),p1=app.run('loadUnitCheck()'),r1=app.env.pending.at(-1);app.run("$('unitMethod').value='line';$('unitMethod').onchange()");const p2=app.run('loadUnitCheck()'),r2=app.env.pending.at(-1);r2.resolve(reply(automatic(ordinary.id,'line')));await p2;r1.resolve(reply(automatic()));await p1;ready(app);assert.equal(JSON.parse(app.state().unitRaw).target.method,'line');});
 await test('old error cannot replace newer success',async()=>{const app=await populated(),p1=app.run('loadUnitCheck()'),r1=app.env.pending.at(-1);await check(app);const text=app.state().unitRaw;r1.reject(Error('old failure'));await p1;assert.equal(app.state().unitRaw,text);ready(app);});
 await test('explicit row change invalidates unit reply',async()=>{const {app,q,s}=await withSelection(),p=app.run('loadUnitCheck()'),request=app.env.pending.at(-1);const next=q.paths.find(x=>x.row_index!==s.row_index);assert(next);const select=app.run('selectRow('+next.row_index+')');app.env.pending.at(-1).resolve(reply(rowPayload(q,next.row_index)));await select;request.resolve(reply(explicit(s)));await p;empty(app);});
 for(const status of [401,403,404,409,500])await test('HTTP '+status+' clears previous result and permits retry',async()=>{const app=await populated();await check(app);const p=app.run('loadUnitCheck()');app.env.pending.at(-1).resolve({ok:false,status,text:async()=>'<secret>'});await p;empty(app);assert.equal(app.state().unitBusy,false);assert.doesNotMatch(app.state().unitStatus,/<secret>/);await check(app);ready(app);});
 for(const failure of ['network','body','json'])await test(failure+' failure leaves no export and permits retry',async()=>{const app=await populated(),p=app.run('loadUnitCheck()'),request=app.env.pending.at(-1);if(failure==='network')request.reject(Error('Controlled offline'));else request.resolve({ok:true,status:200,text:failure==='body'?async()=>{throw Error('Controlled body failure');}:async()=>'{broken'});await p;empty(app);assert.equal(app.state().unitBusy,false);await check(app);ready(app);});
 const corruptions={case:p=>p.target.case_id+='bad',method:p=>p.target.method='text',document:p=>p.target.document_id='other',page:p=>p.target.page++,year:p=>p.target.query.year--,label:p=>p.target.query.row_label+=' changed',role_sha:p=>p.role_report_sha256='b'.repeat(64),report_sha:p=>p.report_sha256='bad',readonly:p=>p.read_only=false,approval:p=>p.automatic_approval=true,checkapproval:p=>p.unit_check.automatic_approval=true,originalvalue:p=>p.unit_check.original_candidate.raw_value='9',originalnormalized:p=>p.unit_check.original_candidate.normalized_value='9',originalindex:p=>p.unit_check.original_candidate.selected_span_index++,originalunit:p=>p.unit_check.original_candidate.unit.raw_text='In billions',status:p=>p.unit_check.status='approved',nanvalue:p=>p.unit_check.scaled_value='NaN',factor:p=>p.unit_check.scale_factor='100',selectedstatus:p=>p.unit_check.original_candidate.status='insufficient',blockedvalue:p=>{p.unit_check.status='blocked';p.unit_check.unit_ready=false;}};
 for(const[name,mutate]of Object.entries(corruptions))await test('reject response mismatch '+name,async()=>{const app=await populated(),payload=automatic();mutate(payload);await check(app,payload);empty(app);assert.match(app.state().unitStatus,/不同|对应/);});
 for(const[name,mutate]of Object.entries({row:p=>p.target.row_index++,query:p=>p.target.query_id+='bad',row_report:p=>p.row_report_sha256='e'.repeat(64)}))await test('reject explicit binding '+name,async()=>{const {app,s}=await withSelection(),payload=explicit(s);mutate(payload);await check(app,payload);empty(app);});
 await test('no unit request in old experiment',async()=>{const app=await populated();change(app,'experiment');const n=app.env.pending.length;await app.run('loadUnitCheck()');empty(app);assert.equal(app.env.pending.length,n);});
 console.log(JSON.stringify({passed:checks.length,checks,fixtures:{registered_records:unitReport.cases.length,explicit_paths:unitReport.selections.length,unit_report_sha256:unitHashes.report_sha256},scope:'Actual page functions with controlled responses; registered result fixtures plus explicitly synthetic faults. No browser, model, raw OCR or independent human annotation.'},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1;});

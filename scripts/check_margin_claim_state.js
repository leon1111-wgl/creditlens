'use strict';

// Run the actual page script with controlled responses. This is neither a
// browser test nor evidence of model quality, human annotation or live outages.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const htmlPath=process.argv[2]?path.resolve(process.argv[2]):path.resolve(__dirname,'../creditlens/margin.html');
const parentPath=process.argv[3]?path.resolve(process.argv[3]):path.resolve(__dirname,'../artifacts/margin-bridge-report.json');
const claimPath=process.argv[4]?path.resolve(process.argv[4]):path.resolve(__dirname,'../artifacts/margin-claims-report.json');
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);
assert(start>=0&&end>start);const script=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
const parentBytes=fs.readFileSync(parentPath),parentHash=crypto.createHash('sha256').update(parentBytes).digest('hex');
const parent={report_sha256:parentHash,report:JSON.parse(parentBytes)};
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture(prefix='synthetic'){
 return {margin_report_sha256:parentHash,report_sha256:'b'.repeat(64),page_height:792,read_only:true,report:{cases:
  parent.report.cases.flatMap((p,index)=>Array.from({length:index?5:3},(_,i)=>{
   const numeric=index===1&&i===4,cloud=index===0&&i===2;
   return {id:prefix+'-'+index+'-'+i,issuer_id:p.input.issuer_id,margin_case_id:p.id,
    claim:{id:prefix+'-'+index+'-'+i,scope:cloud?'microsoft_cloud':i%2?'products':'consolidated_total',metric:i%2?'gross_profit_amount':'gross_margin_rate',
     claim_type:numeric?'numeric_only':'company_explanation',paraphrase_zh:prefix+' statement '+index+' '+i,
     source:{url:'https://example.invalid/'+index,locator:'Synthetic chapter'},qualifiers:{primary:!numeric,partial_offset:!numeric}},
    result:{status:cloud?'scope_unavailable':numeric?'matched_numeric':'matched_direction',causal_explanation:false,
     numeric_checks:index===0&&i===0?[{kind:'delta_amount',stated:'22.9',rounded:'22.9',unit:'USD_billion'}]:[]},
    source_record:{capture_method:'synthetic_control',human_gold:false}};
  }))}};
}
function reply(value,status=200){const raw=JSON.stringify(value).replace('"page_height":792','"page_height":792.0')+'\n';return {ok:status>=200&&status<300,status,text:async()=>raw,raw};}
function harness(){
 const nodes={},env={requests:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='';return {tagName:tag.toUpperCase(),value:'',hidden:false,disabled:false,children:[],style:{},dataset:{},
  get textContent(){return text+this.children.map(n=>n.textContent||'').join('');},
  set textContent(v){text=String(v);this.children=[];},set innerHTML(_){throw Error('Use text nodes for source material');},
  append(...items){const empty=!this.children.length;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
  click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){return nodes[id]??=node(['role','company'].includes(id)?'select':'div');},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:controlled-'+(env.created.length+1);env.created.push({value,blob});return value;}
  static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),
  fetch:(url,options)=>new Promise((resolve,reject)=>env.requests.push({url,options,resolve,reject}))});
 const run=code=>vm.runInContext(code,context,{timeout:1000});run(script);run("$('role').value='viewer'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,claimEpoch,claimRaw,parentRaw:raw,hasParent:!!saved,
  company:$('company').value,rows:$('explanationRows').textContent,rowCount:$('explanationRows').children.length,
  tableHidden:$('claimTable').hidden,note:$('explanationNote').textContent,exportDisabled:$('claimExport').disabled,
  refreshDisabled:$('claimRefresh').disabled,parentExportDisabled:$('export').disabled})`));
 return {nodes,env,run,state};
}
async function populated(){const app=harness(),p=app.run('load()'),r=reply(parent);app.env.requests.at(-1).resolve(r);await p;return {app,parentReply:r};}
async function withClaims(value=fixture()){const setup=await populated(),p=setup.app.run('loadClaims()'),r=reply(value);setup.app.env.requests.at(-1).resolve(r);await p;return {...setup,claimReply:r};}
function descendants(node){return [node,...node.children.flatMap(descendants)];}
function clearAssert(app){const s=app.state();assert.equal(s.claimRaw,null);assert.equal(s.rowCount,0);assert(s.tableHidden);assert(s.exportDisabled);}
async function exported(app){app.run("$('claimExport').onclick()");const d=app.env.downloads.at(-1);return app.env.created.find(x=>x.value===d.href).blob.text();}
async function main(){const passed=[];
 {
  const {app}=await populated();assert.equal(app.env.requests.length,1);clearAssert(app);
  const p=app.run('loadClaims()');assert(app.env.requests.at(-1).url.includes(encodeURIComponent(parentHash)));
  assert.equal(app.env.requests.at(-1).options.cache,'no-store');const r=reply(fixture());app.env.requests.at(-1).resolve(r);await p;
  assert.equal(app.state().rowCount,3);assert(app.state().rows.includes('披露 22.9；复算 22.9 十亿美元'));
  assert.equal(await exported(app),r.raw);assert(r.raw.includes('792.0'));
  assert.notEqual(r.raw,app.state().parentRaw);passed.push('explicit_load_binds_parent_and_exports_original_claim_bytes');
 }
 for(const change of ['key','role','company','parent_reload']){
  const {app}=await populated(),p=app.run('loadClaims()');let reload;
  if(change==='key')app.run("$('key').value='new';$('key').oninput()");
  if(change==='role')app.run("$('role').value='analyst';$('role').onchange()");
  if(change==='company')app.run("$('company').value=saved.report.cases[1].id;$('company').onchange()");
  if(change==='parent_reload')reload=app.run('load()');
  const expected=app.state();app.env.requests[1].resolve(reply(fixture('obsolete')));await p;assert.deepEqual(app.state(),expected);clearAssert(app);
  if(reload){app.env.requests.at(-1).resolve(reply(parent));await reload;}
  passed.push('late_claims_after_'+change);
 }
 for(const failure of [false,true]){
  const {app}=await populated(),old=app.run('loadClaims()'),newer=app.run('loadClaims()');
  app.env.requests.at(-1).resolve(reply(fixture('current')));await newer;const expected=app.state();
  if(failure)app.env.requests.at(-2).reject(new Error('Obsolete failure'));else app.env.requests.at(-2).resolve(reply(fixture('old')));
  await old;assert.deepEqual(app.state(),expected);passed.push(failure?'late_failure_cannot_erase_current_claims':'late_report_cannot_replace_current_claims');
 }
 for(const failure of ['unauthorized','conflict','bad_json','wrong_parent','missing_company','partial_malformed']){
  const {app,parentReply}=await withClaims();assert.equal(app.state().rowCount,3);const p=app.run('loadClaims()');clearAssert(app);
  let r=reply(fixture());
  if(failure==='unauthorized')r={ok:false,status:401};
  if(failure==='conflict')r={ok:false,status:409};
  if(failure==='bad_json')r={ok:true,status:200,text:async()=>'<bad json>'};
  if(failure==='wrong_parent'){const f=fixture();f.margin_report_sha256='e'.repeat(64);r=reply(f);}
  if(failure==='missing_company'){const f=fixture();f.report.cases=f.report.cases.filter(c=>c.margin_case_id!==parent.report.cases[0].id);r=reply(f);}
  if(failure==='partial_malformed'){const f=fixture();delete f.report.cases[1].claim;r=reply(f);}
  app.env.requests.at(-1).resolve(r);await p;clearAssert(app);assert.equal(app.state().parentRaw,parentReply.raw);assert(app.state().hasParent);
  assert.equal(app.state().refreshDisabled,false);passed.push(failure+'_clears_claims_preserves_accounting');
 }
 {
  const {app}=await withClaims();app.run("$('company').value=saved.report.cases[1].id;$('company').onchange()");clearAssert(app);
  const p=app.run('loadClaims()'),r=reply(fixture());app.env.requests.at(-1).resolve(r);await p;
  assert.equal(app.state().rowCount,5);assert(!app.state().rows.includes('statement 0'));
  assert(app.state().rows.includes('仅登记表格数值'));passed.push('company_switch_requires_reload_and_preserves_numeric_only_scope');
 }
 {
  const f=fixture(),marker='<img src=x onerror=alert(1)>';f.report.cases[0].claim.paraphrase_zh=marker;
  f.report.cases[0].claim.source.url='javascript:alert(1)';const {app}=await withClaims(f);
  assert(app.state().rows.includes(marker));assert(!descendants(app.nodes.explanationRows).some(n=>n.href==='javascript:alert(1)'));
  assert(app.state().rows.includes('来源链接不可用'));passed.push('source_content_is_text_and_non_https_link_rejected');
 }
 {
  const {app}=await withClaims();app.run("$('key').oninput()");clearAssert(app);assert.equal(app.state().parentRaw,null);
  assert.equal(app.nodes.explanationRows.textContent,'');app.run("$('claimExport').onclick()");assert.equal(app.env.downloads.length,0);
  passed.push('identity_clears_hidden_claims_and_disables_export');
 }
 if(fs.existsSync(claimPath)){
  const report=JSON.parse(fs.readFileSync(claimPath,'utf8')),value={margin_report_sha256:parentHash,report_sha256:'f'.repeat(64),report,read_only:true};
  const {app}=await populated();assert.equal(report.cases.length,8);let checked=0;
  for(const c of parent.report.cases){app.run("$('company').value="+JSON.stringify(c.id)+";renderCase()");
   const p=app.run('loadClaims()'),r=reply(value);app.env.requests.at(-1).resolve(r);await p;
   const rows=report.cases.filter(x=>x.margin_case_id===c.id);assert.equal(app.state().rowCount,rows.length);
   for(const row of rows){assert(app.state().rows.includes(row.claim.paraphrase_zh));
    for(const n of row.result.numeric_checks||[])assert(app.state().rows.includes('披露 '+n.stated+'；复算 '+(n.rounded??'不可算')));
    checked++;}
   assert.equal(await exported(app),r.raw);
  }
  assert.equal(checked,8);passed.push('official_eight_record_schema_company_filter_and_raw_export');
 }
 console.log(JSON.stringify({scope:'Actual page script with controlled responses; not browser/model/human-quality results',passed:passed.length,
  officialReportExercised:fs.existsSync(claimPath),controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Controlled page test did not settle');process.exit(1);},5000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

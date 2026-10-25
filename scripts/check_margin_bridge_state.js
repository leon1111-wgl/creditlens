'use strict';

// Controlled responses exercise the actual page script. No browser, model,
// network fault injection, or independent human financial reference is claimed.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const htmlPath=process.argv[2]?path.resolve(process.argv[2]):path.resolve(__dirname,'../creditlens/margin.html');
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);
assert(start>=0&&end>start);const script=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture(prefix='synthetic'){
 const box={x0:20,top:30,x1:60,bottom:45};
 return {report_sha256:'a'.repeat(64),report:{cases:['msft','aapl'].map(doc=>{
  const f={id:prefix+'-'+doc+'-fact',role:'revenue',label:'Synthetic row',year:2025,raw_value:'100',
   amount_bbox:box,year_locator:{bbox:box},unit_locator:{bbox:box},amount_parent:{text:'100'}};
  const component={category_id:'products',label:'Synthetic product',mix_contribution_pp:'-1.000000',
   within_contribution_pp:'0.500000',total_contribution_pp:'-0.500000'};
  const display={base_margin_percent:'50.000000',current_margin_percent:'49.500000',total_change_pp:'-0.500000',
   mix_effect_pp:'-1.000000',within_effect_pp:'0.500000',displayed_residual_pp:'0',components:[component]};
  return {id:prefix+'-'+doc,company:'Synthetic '+doc,source:{url:'https://example.invalid/'+doc+'.pdf',published_at:'2025-10-01'},
   page:{page:1,width:612,height:792},registered_observations:[f],result:{status:'computed',
   calculation:{display,residual_pp:'0',residual_tolerance_pp:'0.000000000000000000000001'},components:[component],
   period_results:[2024,2025].map(year=>({year,period:{start:year+'-01-01',end:year+'-12-31',days:year===2024?366:365},
    categories:[{id:'products',label:'Synthetic product',revenue:'100',cost:'50',gross_profit_derived:'50'}],
    display:{categories:[{id:'products',weight_percent:'100.000000',margin_percent:'50.000000'}]}})),
   reasons:[],warnings:[]}};})}};
}
function jsonReply(value,status=200){const raw=JSON.stringify(value).replace('"height":792','"height":792.0')+'\n';
 return {ok:status>=200&&status<300,status,text:async()=>raw,raw};}
const imageReply=()=>({ok:true,status:200,blob:async()=>new Blob(['synthetic image'])});
function harness(){
 const nodes={},env={requests:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='';return {tagName:tag.toUpperCase(),value:'',hidden:false,disabled:false,children:[],style:{},dataset:{},
  get textContent(){return text+this.children.map(n=>n.textContent||'').join('');},
  set textContent(v){text=String(v);this.children=[];},set innerHTML(_){throw Error('Untrusted output must not use innerHTML');},
  append(...items){const empty=this.children.length===0;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
  click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){return nodes[id]??=node(['role','company'].includes(id)?'select':'div');},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:synthetic-'+(env.created.length+1);env.created.push({value,blob});return value;}
  static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),
  fetch:(url,options)=>new Promise((resolve,reject)=>env.requests.push({url,options,resolve,reject}))});
 const run=code=>vm.runInContext(code,context,{timeout:1000});run(script);run("$('role').value='viewer'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,imageEpoch,saved:!!saved,raw,imageUrl,selectedFact,
  company:$('company').value,contentHidden:$('content').hidden,exportDisabled:$('export').disabled,
  status:$('status').textContent,frameChildren:$('pageFrame').children.length,previewHidden:$('previewPanel').hidden,
  previewTitle:$('previewTitle').textContent,previewNote:$('previewNote').textContent,record:$('record').textContent,
  periodNote:$('periodNote').textContent,residual:$('residual').textContent,calculation:$('calculation').textContent,
  warnings:$('warnings').textContent,checks:$('checks').textContent,sourceMeta:$('sourceMeta').textContent})`));
 return {nodes,env,run,state};
}
async function populated(value=fixture()){const app=harness(),reply=jsonReply(value),p=app.run('load()');app.env.requests[0].resolve(reply);await p;return {app,reply};}
const show=app=>app.run('showPage(currentCase(),currentCase().registered_observations[0])');
async function main(){const passed=[];
 {
  const {app,reply}=await populated();assert(app.state().saved);assert.equal(app.state().exportDisabled,false);
  app.run("$('export').onclick()");const d=app.env.downloads.at(-1),b=app.env.created.find(x=>x.value===d.href).blob;
  assert.equal(await b.text(),reply.raw);assert(reply.raw.includes('792.0'));passed.push('successful_report_and_raw_byte_export');
 }
 {
  const app=harness(),p=app.run('load()');app.run("$('key').value='different';$('key').oninput()");const expected=app.state();
  app.env.requests[0].resolve(jsonReply(fixture('obsolete')));await p;assert.deepEqual(app.state(),expected);passed.push('late_report_after_identity_change');
 }
 for(const failure of [false,true]){
  const app=harness(),old=app.run('load()'),latest=app.run('load()');app.env.requests[1].resolve(jsonReply(fixture('latest')));await latest;
  const expected=app.state();if(failure)app.env.requests[0].reject(new Error('Obsolete failure'));else app.env.requests[0].resolve(jsonReply(fixture('old')));
  await old;assert.deepEqual(app.state(),expected);passed.push(failure?'late_failure_cannot_clear_latest_report':'late_report_cannot_replace_latest_report');
 }
 {
  const {app}=await populated();app.run("$('key').oninput()");assert.equal(app.state().saved,false);assert.equal(app.state().raw,null);
  for(const id of ['company','headline','components','periods','facts','source','periodNote','residual','calculation','checks','warnings','sourceMeta','record'])
   assert.equal(app.nodes[id].textContent,'',id+' must clear even when hidden');
  assert.equal(app.state().exportDisabled,true);passed.push('identity_clears_all_report_and_hidden_evidence');
 }
 for(const change of ['company','identity','close']){
  const {app}=await populated(),p=show(app);
  if(change==='company')app.run("$('company').value=saved.report.cases[1].id;$('company').onchange()");
  if(change==='identity')app.run("$('role').value='analyst';$('role').onchange()");
  if(change==='close')app.run("$('closePreview').onclick()");
  const expected=app.state();app.env.requests.at(-1).resolve(imageReply());await p;assert.deepEqual(app.state(),expected);
  assert.equal(app.env.created.length,0);passed.push('late_image_after_'+change);
 }
 {
  const {app}=await populated(),first=show(app),second=show(app);app.env.requests.at(-1).resolve(imageReply());await second;
  const expected=app.state();app.env.requests.at(-2).resolve(imageReply());await first;assert.deepEqual(app.state(),expected);
  assert.equal(app.env.created.length,1);passed.push('older_image_cannot_replace_newer_image');
 }
 {
  const {app}=await populated(),p=show(app);app.env.requests.at(-1).resolve(imageReply());await p;const old=app.state().imageUrl;
  const bad=show(app);app.env.requests.at(-1).resolve({ok:false,status:404});await bad;
  assert.equal(app.state().frameChildren,0);assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(old));
  assert(app.state().previewNote.includes('官方'));passed.push('image_http_failure_clears_old_image_and_boxes');
 }
 {
  const {app}=await populated(),p=show(app);app.env.requests.at(-1).resolve(imageReply());await p;const old=app.nodes.pageFrame.children[0];
  const newer=show(app);app.env.requests.at(-1).resolve(imageReply());await newer;const expected=app.state();old.onerror();assert.deepEqual(app.state(),expected);
  app.nodes.pageFrame.children[0].onerror();assert.equal(app.state().frameChildren,0);assert.equal(app.state().imageUrl,null);
  passed.push('decode_failure_clears_boxes_and_old_decoder_cannot_clear_new_image');
 }
 {
  const f=fixture(),marker='<img src=x onerror=alert(1)>';
  f.report.cases[0].company=marker;f.report.cases[0].registered_observations[0].label=marker;
  f.report.cases[0].source.url='javascript:alert(1)';const {app}=await populated(f);
  assert(app.nodes.facts.textContent.includes(marker));assert(app.nodes.record.textContent.includes(marker));
  assert.equal(app.nodes.source.children[0].tagName,'SPAN');assert.equal(app.nodes.source.children[0].href,undefined);
  passed.push('untrusted_text_uses_text_nodes_and_non_https_source_rejected');
 }
 {
  const f=fixture();f.report.cases[1].result={status:'blocked',reasons:[{message:'Synthetic missing evidence'}]};
  const {app}=await populated(f);assert(app.state().calculation);
  app.run("$('company').value=saved.report.cases[1].id;$('company').onchange()");
  for(const id of ['periodNote','residual','calculation','warnings'])assert.equal(app.nodes[id].textContent,'');
  assert.equal(app.nodes.headline.children.length,0);assert.equal(app.nodes.components.children.length,0);
  assert(app.state().checks.includes('Synthetic missing evidence'));
  app.run("$('company').value=saved.report.cases[0].id;$('company').onchange()");
  assert(app.state().calculation);assert(!app.state().status.includes('未满足'));passed.push('computed_blocked_computed_does_not_mix_old_calculations');
 }
 {
  const {app}=await populated();app.run("$('company').value='not-registered';renderCase()");
  for(const id of ['periodNote','residual','calculation','checks','warnings','sourceMeta','record'])assert.equal(app.nodes[id].textContent,'');
  passed.push('missing_case_clears_previous_evidence');
 }
 for(const mode of ['unauthorized','invalid_json']){
  const {app}=await populated(),p=app.run('load()');app.env.requests.at(-1).resolve(mode==='unauthorized'?{ok:false,status:401}:{ok:true,status:200,text:async()=>'<invalid>'});await p;
  assert.equal(app.state().saved,false);assert.equal(app.state().raw,null);assert.equal(app.state().exportDisabled,true);
  passed.push(mode+'_does_not_retain_old_report');
 }
 const reportPath=process.argv[3]?path.resolve(process.argv[3]):path.resolve(__dirname,'../artifacts/margin-bridge-report.json');
 if(fs.existsSync(reportPath)){
  const report=JSON.parse(fs.readFileSync(reportPath,'utf8')),value={report_sha256:'f'.repeat(64),report};
  const {app,reply}=await populated(value);assert.equal(report.cases.length,2);let checked=0;
  for(const c of report.cases){app.run("$('company').value="+JSON.stringify(c.id)+";renderCase()");
   assert.equal(app.nodes.facts.children.length,14);assert.equal(app.nodes.periods.children.length,4);
   assert(app.nodes.headline.textContent.includes(c.result.calculation.display.total_change_pp));
   for(let i=0;i<c.registered_observations.length;i++){
    const p=app.run('showPage(currentCase(),currentCase().registered_observations['+i+'])');app.env.requests.at(-1).resolve(imageReply());await p;
    assert.equal(app.state().frameChildren,4);assert.equal(app.state().selectedFact,c.registered_observations[i].id);checked++;
   }
  }
  app.run("$('export').onclick()");const d=app.env.downloads.at(-1);assert.equal(await app.env.created.find(x=>x.value===d.href).blob.text(),reply.raw);
  assert.equal(checked,28);passed.push('registered_two_company_28_fact_schema_and_image_overlay_paths');
 }
 console.log(JSON.stringify({scope:'Actual page script; controlled synthetic responses, not browser or model results',
  page:path.basename(htmlPath),passed:passed.length,registeredReportExercised:fs.existsSync(reportPath),controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Controlled page test did not settle');process.exit(1);},5000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

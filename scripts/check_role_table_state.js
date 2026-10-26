'use strict';

// Actual page code with controlled responses. No browser rendering, OCR calls,
// independent financial gold or real network-failure exercise is claimed.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const htmlPath=process.argv[2]?path.resolve(process.argv[2]):path.resolve(__dirname,'../creditlens/table-lookup.html');
const reportPath=process.argv[3]?path.resolve(process.argv[3]):path.resolve(__dirname,'../artifacts/role-table-lookup-report.json');
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);
assert(start>=0&&end>start);const source=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture(label='synthetic'){
 const one=(id,state,number='10')=>({score:{outcome:state},extraction:{status:state==='insufficient'?'insufficient':'selected',raw_value:state==='insufficient'?null:number,
  normalized_value:state==='insufficient'?null:number,selected_span_index:state==='insufficient'?null:4,reason:'synthetic',message:'Synthetic evidence control',unit:{raw_text:'In millions'},
  evidence:{amounts:[{span_index:4,span:{text:number,confidence:1,bbox:{x0:10,top:20,x1:30,bottom:40}}}],year_headers:[],row_label:[]},
  nonblocking_anchor_issues:id==='role'?[{code:'word_outside_parent',line_indices:[1],word_refs:[]}]:[],guard_issues:[]}});
 const rows=[['msft','only-third','matched','matched','insufficient'],['msft','amount-difference','matched','matched','matched'],['aapl','recovered','insufficient','insufficient','matched']];
 return {report_sha256:'a'.repeat(64),synthetic_height:792,report:{documents:['msft','aapl'].map(doc=>({id:doc,source:{url:'https://example.invalid/'+doc+'.pdf'},
  pages:[{page:1,width:100,height:100}],cases:rows.filter(r=>r[0]===doc).map(([d,id,line,word,role])=>({id:label+'-'+id,fact_id:id,page:1,
   query:{row_label:'Synthetic '+id,year:2025},reference:{raw_value:'999',unit:'USD_million',amount_bbox:{x0:80,top:80,x1:90,bottom:90}},
   methods:{line:one('line',line),word:one('word',word),role:one('role',role,id==='amount-difference'?'11':'10')}}))}))}};
}
function lineFixture(){const f=fixture();for(const d of f.report.documents)for(const c of d.cases)c.methods={text_sequence:c.methods.line,layout:c.methods.word};return f;}
function reply(value){const raw=JSON.stringify(value).replace('"synthetic_height":792','"synthetic_height":792.0')+'\n';return {ok:true,status:200,text:async()=>raw,raw};}
const imageReply=()=>({ok:true,status:200,blob:async()=>new Blob(['synthetic image'])});
function harness(){
 const nodes={},env={pending:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='';return {tagName:tag.toUpperCase(),value:'',disabled:false,hidden:false,children:[],style:{},dataset:{},open:false,
  get textContent(){return text+this.children.map(c=>c.textContent||'').join('');},set textContent(v){text=String(v);this.children=[];},
  set innerHTML(_){throw Error('Source text must not be interpreted as HTML');},
  append(...items){const empty=!this.children.length;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
  click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){return nodes[id]??=node(['role','issuer','filter','case','experiment','transitionFrom'].includes(id)?'select':'div');},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:synthetic-'+(env.created.length+1);env.created.push({value,blob});return value;}static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject}))});
 const run=code=>vm.runInContext(code,context,{timeout:1000});run(source);run("$('role').value='viewer';$('filter').value='all';$('experiment').value='role';$('transitionFrom').value='line'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,imageEpoch,apiBase,methodKeys,raw,imageUrl,saved:!!saved,caseIds:cases.map(c=>c.id),
  selected:$('case').value,methods:$('methods').textContent,methodCount:$('methods').children.length,
  exportDisabled:$('export').disabled,workspaceHidden:$('workspace').hidden,detailHidden:$('caseDetail').hidden,status:$('status').textContent,
  previewHidden:$('preview').hidden,previewNote:$('previewNote').textContent,frameChildren:$('pageFrame').children.length,
  transitionHidden:$('transitionPanel').hidden,transitionChoiceHidden:$('transitionChoice').hidden,
  transitionRows:$('transitions').children.map(r=>r.children.slice(1).map(n=>n.textContent)),caseDelta:$('caseDelta').textContent})`));
 return {nodes,env,run,state};
}
async function populated(f=fixture()){const app=harness(),r=reply(f),p=app.run('load()');app.env.pending.at(-1).resolve(r);await p;return {app,response:r};}
const show=app=>app.run("showPage(currentCase(),'role')");
async function exportText(app){app.run("$('export').onclick()");const d=app.env.downloads.at(-1);return {name:d.name,text:await app.env.created.find(e=>e.value===d.href).blob.text()};}
async function main(){const passed=[];
 {
  const {app,response}=await populated();assert.equal(app.env.pending[0].url,'/api/role-table-lookup');assert.deepEqual(app.state().methodKeys,['line','word','role']);
  assert.equal(app.state().methodCount,3);assert.equal(app.nodes.summary.children.length,3);assert(!app.state().transitionChoiceHidden);
  const e=await exportText(app);assert.equal(e.name,'creditlens-role-table-lookup.json');assert.equal(e.text,response.raw);assert(e.text.includes('792.0'));
  passed.push('three_methods_and_independent_raw_role_export');
 }
 {
  const {app}=await populated();assert.deepEqual(app.state().transitionRows,[['1','0','1'],['0','0','0'],['1','0','0']]);
  app.run("$('transitionFrom').value='word';$('transitionFrom').onchange()");assert.deepEqual(app.state().transitionRows,[['1','0','1'],['0','0','0'],['1','0','0']]);
  assert(app.nodes.transitionHeading.textContent.includes('词级'));assert(app.state().methods.includes('本次使用原整行锚点'));
  passed.push('word_to_role_matrix_and_nonblocking_anchor_explanation');
 }
 {
  const {app}=await populated();app.run("$('filter').value='difference';$('filter').onchange()");
  assert.deepEqual(app.nodes.case.children.map(x=>x.value),['synthetic-only-third','synthetic-amount-difference','synthetic-recovered']);
  assert.equal(app.state().caseIds.length,3);passed.push('third_method_only_status_or_amount_difference_is_not_filtered_out');
 }
 for(const change of ['identity','experiment']){
  const app=harness(),p=app.run('load()');if(change==='identity')app.run("$('key').oninput()");else app.run("$('experiment').value='line';$('experiment').onchange()");
  const expected=app.state();app.env.pending[0].resolve(reply(fixture('obsolete')));await p;assert.deepEqual(app.state(),expected);
  passed.push('late_role_report_after_'+change);
 }
 for(const fail of [false,true]){
  const app=harness(),old=app.run('load()'),latest=app.run('load()');app.env.pending[1].resolve(reply(fixture('latest')));await latest;const expected=app.state();
  if(fail)app.env.pending[0].reject(Error('obsolete failure'));else app.env.pending[0].resolve(reply(fixture('obsolete')));
  await old;assert.deepEqual(app.state(),expected);passed.push(fail?'stale_report_failure_cannot_clear_reload':'stale_report_cannot_replace_reload');
 }
 for(const change of ['case','issuer','filter','identity','experiment']){
  const {app}=await populated(),p=show(app);
  if(change==='case')app.run("$('case').value='synthetic-recovered';$('case').onchange()");
  if(change==='issuer')app.run("$('issuer').value='aapl';$('issuer').onchange()");
  if(change==='filter')app.run("$('filter').value='wrong';$('filter').onchange()");
  if(change==='identity')app.run("$('role').value='analyst';$('role').onchange()");
  if(change==='experiment')app.run("$('experiment').value='word';$('experiment').onchange()");
  const expected=app.state();app.env.pending.at(-1).resolve(imageReply());await p;assert.deepEqual(app.state(),expected);assert.equal(app.env.created.length,0);
  passed.push('late_role_image_after_'+change);
 }
 {
  const {app}=await populated(),a=show(app),b=app.run("showPage(currentCase(),'line')");app.env.pending.at(-1).resolve(imageReply());await b;const expected=app.state();
  app.env.pending.at(-2).resolve(imageReply());await a;assert.deepEqual(app.state(),expected);assert.equal(app.env.created.length,1);
  passed.push('latest_method_image_wins');
 }
 {
  const {app}=await populated(),p=show(app);assert(app.env.pending.at(-1).url.startsWith('/api/role-table-lookup/cases/'));
  assert(app.env.pending.at(-1).url.includes('a'.repeat(64)));app.env.pending.at(-1).resolve(imageReply());await p;
  const boxes=app.nodes.pageFrame.children.filter(n=>n.tagName==='SPAN');assert.equal(boxes[0].style.left,'10%');assert.notEqual(boxes[0].style.left,'80%');
  const url=app.state().imageUrl,bad=show(app);app.env.pending.at(-1).resolve({ok:false,status:404});await bad;
  assert.equal(app.state().frameChildren,0);assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(url));
  passed.push('image_is_bound_to_role_snapshot_and_extraction_boxes_not_reference');
 }
 {
  const f=fixture(),e=f.report.documents[0].cases[1].methods.role.extraction;
  e.raw_value='$11';e.prefix_associations=[{span_index:4,numeric_bbox:{x0:10,top:20,x1:30,bottom:40},
   display_bbox:{x0:4,top:20,x1:30,bottom:40},word_refs:[{line_index:1,word_index:0},{line_index:2,word_index:0}]}];
  const {app}=await populated(f);app.run("$('case').value='synthetic-amount-difference';renderCase()");
  assert(app.state().methods.includes('数值归列框'));const p=show(app);app.env.pending.at(-1).resolve(imageReply());await p;
  const numeric=app.nodes.pageFrame.children.find(n=>n.className==='box amount selected'),full=app.nodes.pageFrame.children.find(n=>n.className==='box currency-group');
  assert.equal(numeric.style.left,'10%');assert.equal(full.style.left,'4%');assert.equal(numeric.style.width,'20%');assert.equal(full.style.width,'26%');
  assert(app.state().previewNote.includes('包含完整货币符号与金额'));const url=app.state().imageUrl;
  app.run("$('key').oninput()");assert.equal(app.state().frameChildren,0);assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(url));
  passed.push('numeric_column_box_and_full_currency_display_box_are_distinct_and_both_clear');
 }
 {
  const {app}=await populated(),p=show(app);app.env.pending.at(-1).resolve(imageReply());await p;const oldImage=app.nodes.pageFrame.children[0];
  const next=show(app);app.env.pending.at(-1).resolve(imageReply());await next;const expected=app.state();oldImage.onerror();assert.deepEqual(app.state(),expected);
  const url=app.state().imageUrl;app.nodes.pageFrame.children[0].onerror();assert.equal(app.state().frameChildren,0);assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(url));
  passed.push('current_decode_error_clears_boxes_and_old_decode_error_is_ignored');
 }
 {
  const f=fixture(),marker='<img src=x onerror=alert(1)>';f.report.documents[0].source.url='javascript:alert(1)';f.report.documents[0].cases[0].query.row_label=marker;
  f.report.documents[0].cases[0].methods.role.extraction.message=marker;const {app}=await populated(f);
  assert(app.nodes.methods.textContent.includes(marker));assert.equal(app.nodes.source.children[0].tagName,'SPAN');
  assert.equal(app.nodes.source.children[0].href,undefined);passed.push('untrusted_source_text_is_inert_and_links_require_https');
 }
 for(const failure of ['unauthorized','invalid_json']){
  const {app}=await populated(),p=app.run('load()');app.env.pending.at(-1).resolve(failure==='unauthorized'?{ok:false,status:401}:{ok:true,status:200,text:async()=>'<bad>'});await p;
  assert(!app.state().saved);assert.equal(app.state().raw,null);assert(app.state().exportDisabled);assert.equal(app.state().methodCount,0);
  passed.push(failure+'_clears_role_data_and_export');
 }
 {
  const {app}=await populated();app.run("$('experiment').value='line';$('experiment').onchange()");const p=app.run('load()');app.env.pending.at(-1).resolve(reply(lineFixture()));await p;
  assert.deepEqual(app.state().methodKeys,['text_sequence','layout']);assert(app.state().transitionHidden);assert.equal(app.state().transitionRows.length,0);
  assert.equal((await exportText(app)).name,'creditlens-table-lookup.json');passed.push('returning_to_original_experiment_resets_role_matrix_and_export_name');
 }
 let actualCases=0;
 assert(fs.existsSync(reportPath),'Published role report is required for the 42-record verification');
 {
  const bytes=fs.readFileSync(reportPath),report=JSON.parse(bytes),value={report,report_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
  const {app,response}=await populated(value);assert.equal(app.state().caseIds.length,42);
  const full=report.documents.flatMap(d=>d.cases);
  for(const from of ['line','word']){app.run("$('transitionFrom').value="+JSON.stringify(from)+";renderTransitions()");
   const states=['matched','wrong','insufficient'],expected=states.map(before=>states.map(after=>String(full.filter(c=>c.methods[from].score.outcome===before&&c.methods.role.score.outcome===after).length)));
   assert.deepEqual(app.state().transitionRows,expected);
  }
  for(const id of app.state().caseIds){app.run("$('case').value="+JSON.stringify(id)+";renderCase()");assert.equal(app.state().methodCount,3);
   const p=show(app);app.env.pending.at(-1).resolve(imageReply());await p;
   for(const box of app.nodes.pageFrame.children.filter(n=>n.tagName==='SPAN'))assert(Object.values(box.style).every(v=>!String(v).includes('NaN')));
   actualCases++;
  }
  app.run("$('filter').value='common';filterCases()");assert.equal(app.nodes.case.children.length,8);assert.equal(app.state().caseIds.length,42);
  assert.equal(await exportText(app).then(x=>x.text),response.raw);passed.push('actual_42_records_three_methods_both_matrices_and_eight_case_filter');
 }
 console.log(JSON.stringify({scope:'Actual page script; controlled responses, not browser/model/human-gold results',passed:passed.length,actualCases,controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Controlled page test did not settle');process.exit(1);},8000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

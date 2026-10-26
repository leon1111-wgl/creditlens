'use strict';

// Runs the actual page script with controlled responses. This is not a browser,
// human annotation session, OCR run, or financial accuracy measurement.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const htmlPath=path.resolve(process.argv[2]||path.join(__dirname,'../creditlens/table-lookup.html'));
const rolePath=path.resolve(process.argv[3]||path.join(__dirname,'../artifacts/role-table-lookup-report.json'));
const rowPath=path.resolve(process.argv[4]||path.join(__dirname,'../artifacts/row-disambiguation-report.json'));
const html=fs.readFileSync(htmlPath,'utf8'),start=html.indexOf('<script>'),end=html.indexOf('</script>',start);
assert(start>=0&&end>start);const source=html.slice(start+8,end).replace(/load\(\);\s*$/,'');
const clone=x=>JSON.parse(JSON.stringify(x)),roleBytes=fs.readFileSync(rolePath),roleReport=JSON.parse(roleBytes);
const parent={report_sha256:crypto.createHash('sha256').update(roleBytes).digest('hex'),report:roleReport};
const ambiguous=roleReport.documents.flatMap(d=>d.cases.filter(c=>c.methods.role.extraction.reason==='duplicate_row_label').map(c=>({...c,document:d})));
assert.equal(ambiguous.length,6);const initial=ambiguous[0];
function span(text,x,y){return {text,confidence:.99,bbox:{x0:x,top:y,x1:x+90,bottom:y+12}};}
function fixture(){const rows=[13,32].map((index,n)=>({row_index:index,row:{span_index:index,span:span(initial.query.row_label,60,100+n*100)},
 context:{basis:'synthetic_context',above:[{span_index:index-1,span:span('Synthetic context <b>not markup</b>',50,80+n*100)}],below:[]}}));
 return {report_sha256:'d'.repeat(64),report:{queries:[{id:'synthetic-query',document_id:initial.document.id,page:initial.page,
  page_metadata:clone(initial.document.pages.find(p=>p.page===initial.page)),query:clone(initial.query),discovery:{candidate_count:rows.length,candidates:rows}}]}};}
function selection(index=13,text='200'){return {synthetic_height:792,report_sha256:'d'.repeat(64),query_id:'synthetic-query',
 result:{selection:{row_index:index,explicit:true},extraction:{status:'selected',raw_value:text,unit:{raw_text:'In millions'},message:'Synthetic control'},automatic_approval:false}};}
function reply(value){const raw=JSON.stringify(value).replace('"synthetic_height":792','"synthetic_height":792.0')+'\n';return {ok:true,status:200,text:async()=>raw,raw};}
const imageReply=()=>({ok:true,status:200,blob:async()=>new Blob(['synthetic image'])});
function harness(){const nodes={},env={pending:[],created:[],revoked:[],downloads:[]};
 function node(tag='div'){let text='';return {tagName:tag.toUpperCase(),value:'',disabled:false,hidden:false,children:[],style:{},dataset:{},
  get textContent(){return text+this.children.map(c=>c.textContent||'').join('');},set textContent(v){text=String(v);this.children=[];},
  set innerHTML(_){throw Error('Untrusted text must not become HTML');},
  append(...items){const empty=!this.children.length;this.children.push(...items);if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
  replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
  click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};}
 const document={getElementById(id){return nodes[id]??=node(['role','issuer','filter','case','experiment','transitionFrom'].includes(id)?'select':'div');},createElement:node};
 class LocalURL extends URL{static createObjectURL(blob){const value='blob:row-control-'+(env.created.length+1);env.created.push({value,blob});return value;}static revokeObjectURL(value){env.revoked.push(value);}}
 const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject}))});
 const run=code=>vm.runInContext(code,context,{timeout:1000});run(source);run("$('role').value='viewer';$('filter').value='all';$('experiment').value='role';$('transitionFrom').value='line'");
 const state=()=>JSON.parse(run(`JSON.stringify({epoch,rowEpoch,imageEpoch,rowSaved:!!rowSaved,rowQuery:rowQuery?.id||null,rowSelectionRaw,
  panelHidden:$('rowPanel').hidden,candidates:$('rowCandidates').textContent,candidateCount:$('rowCandidates').children.length,
  result:$('rowResult').textContent,status:$('rowStatus').textContent,exportDisabled:$('rowExport').disabled,pageDisabled:$('rowPage').disabled,
  previewHidden:$('preview').hidden,previewNote:$('previewNote').textContent,imageUrl,frameCount:$('pageFrame').children.length})`));
 return {nodes,env,run,state};}
async function populated(){const app=harness(),p=app.run('load()');app.env.pending.at(-1).resolve(reply(parent));await p;
 app.run("$('case').value="+JSON.stringify(initial.id)+";renderCase()");return app;}
async function rows(app,value=fixture()){const p=app.run('loadRowChoices()');app.env.pending.at(-1).resolve(reply(value));await p;return value;}
async function picked(app,index=13,value=selection(index)){const response=reply(value),p=app.run('selectRow('+index+')');app.env.pending.at(-1).resolve(response);await p;return response;}
function change(app,type){if(type==='identity')app.run("$('key').oninput()");
 else if(type==='experiment')app.run("$('experiment').value='line';$('experiment').onchange()");
 else if(type==='issuer')app.run("$('issuer').value='msft';$('issuer').onchange()");
 else if(type==='query')app.run("$('case').value="+JSON.stringify(ambiguous.at(-1).id)+";$('case').onchange()");
 else if(type==='filter')app.run("$('filter').value='wrong';$('filter').onchange()");}
async function exportText(app){app.run("$('rowExport').onclick()");const d=app.env.downloads.at(-1);return {name:d.name,text:await app.env.created.find(x=>x.value===d.href).blob.text()};}
async function main(){const passed=[];
 {const app=await populated();assert(!app.state().panelHidden);assert.equal(app.env.pending.length,1);assert.equal(app.state().rowSelectionRaw,null);assert(app.state().exportDisabled);
  await rows(app);assert.equal(app.state().candidateCount,2);assert(app.state().status.includes('没有默认选择'));assert.equal(app.env.pending.length,2);assert(app.state().exportDisabled);
  assert(app.env.pending.at(-1).url.includes(parent.report_sha256));passed.push('all_candidates_require_explicit_choice_and_bind_parent_report');}
 {const app=await populated();await rows(app);const r=await picked(app);assert(app.state().result.includes('200'));assert(app.state().result.includes('未采用为财务事实'));
  assert(app.env.pending.at(-1).url.includes('row_index=13'));assert(app.env.pending.at(-1).url.includes('d'.repeat(64)));
  const e=await exportText(app);assert.equal(e.name,'creditlens-row-selection.json');assert.equal(e.text,r.raw);assert(e.text.includes('792.0'));
  passed.push('selection_is_version_bound_and_download_preserves_raw_response');}
 for(const phase of ['discovery','selection','image'])for(const type of ['identity','experiment','issuer','query','filter']){
  const app=await populated();if(phase!=='discovery')await rows(app);
  const p=app.run(phase==='discovery'?'loadRowChoices()':phase==='selection'?'selectRow(13)':'showRowPage()'),request=app.env.pending.at(-1);
  change(app,type);const expected=app.state();request.resolve(phase==='discovery'?reply(fixture()):phase==='selection'?reply(selection()):imageReply());await p;
  assert.deepEqual(app.state(),expected);passed.push('late_'+phase+'_after_'+type);}
 {const app=await populated(),old=app.run('loadRowChoices()'),a=app.env.pending.at(-1),latest=app.run('loadRowChoices()');
  app.env.pending.at(-1).resolve(reply(fixture()));await latest;const expected=app.state();a.reject(Error('obsolete error'));await old;
  assert.deepEqual(app.state(),expected);passed.push('obsolete_discovery_failure_cannot_clear_reloaded_choices');}
 {const app=await populated();await rows(app);const old=app.run('selectRow(13)'),a=app.env.pending.at(-1),latest=app.run('selectRow(32)');
  app.env.pending.at(-1).resolve(reply(selection(32,'300')));await latest;const expected=app.state();a.resolve(reply(selection(13,'200')));await old;
  assert.deepEqual(app.state(),expected);assert(app.state().result.includes('300'));assert(!(await exportText(app)).text.includes('"raw_value":"200"'));
  passed.push('latest_explicit_choice_wins_and_export_cannot_revert');}
 for(const failure of ['unauthorized','stale','bad_json']){const app=await populated();await rows(app);await picked(app);
  const p=app.run('selectRow(32)');assert.equal(app.state().rowSelectionRaw,null);assert(app.state().exportDisabled);
  app.env.pending.at(-1).resolve(failure==='bad_json'?{ok:true,status:200,text:async()=>'{bad'}:{ok:false,status:failure==='unauthorized'?401:409});await p;
  assert.equal(app.state().rowSelectionRaw,null);assert(app.state().exportDisabled);assert.equal(app.state().result,'');passed.push(failure+'_clears_previous_selection_and_export');}
 {const app=await populated();await rows(app);let p=app.run('showRowPage()');app.env.pending.at(-1).resolve(imageReply());await p;
  assert.equal(app.state().frameCount,3);const oldImage=app.nodes.pageFrame.children[0],url=app.state().imageUrl;
  p=app.run('showRowPage()');app.env.pending.at(-1).resolve(imageReply());await p;assert(app.env.revoked.includes(url));const expected=app.state();oldImage.onerror();assert.deepEqual(app.state(),expected);
  const fresh=app.state().imageUrl;app.nodes.pageFrame.children[0].onerror();assert.equal(app.state().frameCount,0);assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(fresh));
  passed.push('all_original_rows_drawn_and_decode_failure_releases_current_image_only');}
 {const app=await populated();const f=fixture();f.report.queries[0].discovery.candidates[0].row.span.text='<img src=x onerror=alert(1)>';await rows(app,f);
  assert(app.state().candidates.includes('<img'));await picked(app,13,selection(13,'<script>unsafe()</script>'));assert(app.state().result.includes('<script>'));
  passed.push('source_and_candidate_text_remain_inert');}
 {const app=await populated();await rows(app);const value=selection();value.result.extraction={status:'insufficient',raw_value:null,message:'Synthetic missing evidence',unit:{raw_text:null}};
  await picked(app,13,value);assert(app.state().status.includes('不足'));assert(app.state().result.includes('Synthetic missing evidence'));assert(!app.state().exportDisabled);
  passed.push('insufficient_choice_remains_exportable_without_success_claim');}
 let actualQueries=0,actualCandidates=0,actualSelectionPaths=0;
 assert(fs.existsSync(rowPath),'Published row-choice report is required for the complete candidate check');
 {const bytes=fs.readFileSync(rowPath),report=JSON.parse(bytes),snapshot={report,report_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
  assert.equal(report.queries.length,4);
  for(const q of report.queries){const app=await populated();const c=ambiguous.find(c=>c.document.id===q.document_id&&c.page===q.page&&c.query.row_label===q.query.row_label&&c.query.year===q.query.year);assert(c);
   app.run("$('case').value="+JSON.stringify(c.id)+";renderCase()");await rows(app,snapshot);assert.equal(app.state().candidateCount,q.discovery.candidate_count);assert(app.state().exportDisabled);actualQueries++;
   const p=app.run('showRowPage()');app.env.pending.at(-1).resolve(imageReply());await p;assert.equal(app.state().frameCount,1+q.discovery.candidate_count);actualCandidates+=q.discovery.candidate_count;
   for(const chosen of q.paths){const value={schema_version:1,report_sha256:snapshot.report_sha256,role_report_sha256:report.role_report_sha256,
      query_id:q.id,document_id:q.document_id,page:q.page,query:q.query,source:q.source,page_metadata:q.page_metadata,
      discovery:q.discovery,result:chosen.result,read_only:true,persisted:false,full_ocr_replayed:false,automatic_approval:false,
      selection_basis:'explicit_request_for_saved_line_layout_result'};
    const response=await picked(app,chosen.row_index,value);assert(app.env.pending.at(-1).url.includes(encodeURIComponent(q.id)));
    assert(app.state().result.includes(chosen.result.extraction.raw_value||chosen.result.extraction.message));
    assert.equal((await exportText(app)).text,response.raw);actualSelectionPaths++;
   }
  }
  assert.equal(actualCandidates,8);assert.equal(actualSelectionPaths,8);passed.push('actual_four_queries_eight_candidates_selection_exports_and_original_row_boxes');}
 console.log(JSON.stringify({scope:'Actual HTML with controlled responses; no browser/model/human-gold claim',passed:passed.length,actualQueries,actualCandidates,actualSelectionPaths,controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Row UI control did not settle');process.exit(1);},10000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

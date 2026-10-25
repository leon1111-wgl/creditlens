'use strict';

// Execute the actual page script with synthetic report data and controlled promises.
// This checks UI state transitions, not a browser renderer, OCR, or real network faults.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const htmlPath = process.argv[2] ? path.resolve(process.argv[2])
  : path.resolve(__dirname, '../creditlens/table-lookup.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const start = html.indexOf('<script>'), end = html.indexOf('</script>', start);
assert(start >= 0 && end > start, 'Page script not found');
const source = html.slice(start + 8, end).replace(/load\(\);\s*$/, '');
assert(!source.endsWith('load();'), 'Initial request must be controlled');

function fixture(label='report') {
  const evidence = {span_index:4,span:{text:'10',confidence:1,bbox:{x0:10,top:20,x1:30,bottom:40}}};
  const extraction = {status:'selected',raw_value:'10',normalized_value:'10',reason:'synthetic',
    selected_span_index:4,unit:{raw_text:'In millions'},evidence:{amounts:[evidence],year_headers:[],row_label:[]}};
  return {report_sha256:'a'.repeat(64),synthetic_height:792,
    report:{documents:['msft','aapl'].map(id=>({id,source:{url:'https://example.invalid/'+id+'.pdf'},
      pages:[{page:1,width:100,height:100}],cases:[{id:label+'-'+id,page:1,
        query:{row_label:'Synthetic '+id,year:2025},reference:{raw_value:'10',unit:'USD_million'},
        methods:{text_sequence:{score:{outcome:id==='msft'?'matched':'wrong'},extraction},
        layout:{score:{outcome:'matched'},extraction}}}]}))}};
}
function wordFixture() {
  const result=fixture('word');result.report_sha256='b'.repeat(64);
  for(const doc of result.report.documents)for(const item of doc.cases) {
    item.methods={line:item.methods.layout,word:item.methods.text_sequence};
    item.methods.line.score.outcome=doc.id==='msft'?'insufficient':'matched';
    item.methods.word.score.outcome=doc.id==='msft'?'matched':'insufficient';
  }
  return result;
}
function jsonReply(value) {
  const raw=JSON.stringify(value).replace('"synthetic_height":792','"synthetic_height":792.0')+'\n';
  return {ok:true,status:200,text:async()=>raw,raw};
}
const imageReply=()=>({ok:true,status:200,blob:async()=>new Blob(['synthetic image'])});

function harness() {
  const nodes={},env={pending:[],created:[],revoked:[],downloads:[]};
  function node(tag='div') {
    return {tagName:tag.toUpperCase(),textContent:'',value:'',disabled:false,hidden:false,
      style:{},children:[],open:false,
      append(...items){const empty=this.children.length===0;this.children.push(...items);
        if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
      replaceChildren(...items){this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
      click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};
  }
  const document={getElementById(id){return nodes[id]??=node(
    ['role','issuer','filter','case','experiment'].includes(id)?'select':'div');},createElement:node};
  class LocalURL extends URL {
    static createObjectURL(blob){const value='blob:synthetic-'+(env.created.length+1);
      env.created.push({value,blob});return value;}
    static revokeObjectURL(value){env.revoked.push(value);}
  }
  const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),
    fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject})),env});
  vm.runInContext(source,context,{timeout:1000});
  vm.runInContext("$('role').value='analyst';$('filter').value='all';$('experiment').value='line';",context,{timeout:1000});
  return {env,nodes,run:code=>vm.runInContext(code,context,{timeout:1000}),
    state:()=>JSON.parse(vm.runInContext(`JSON.stringify({epoch,imageEpoch,raw,imageUrl,apiBase,methodKeys,
      savedPresent:saved!==null,caseIds:cases.map(c=>c.id),selected:$('case').value,
      exportDisabled:$('export').disabled,workspaceHidden:$('workspace').hidden,
      detailHidden:$('caseDetail').hidden,status:$('status').textContent,title:$('queryTitle').textContent,
      previewHidden:$('preview').hidden,previewTitle:$('previewTitle').textContent,
      previewNote:$('previewNote').textContent,frameChildren:$('pageFrame').children.length,
      transitionHidden:$('transitionPanel').hidden,transitionRows:$('transitions').children.length,
      caseDelta:$('caseDelta').textContent,experimentNote:$('experimentNote').textContent})`,context,{timeout:1000}))};
}
async function populated() {
  const app=harness(),response=jsonReply(fixture()),loading=app.run('load()');
  app.env.pending[0].resolve(response);await loading;return {app,response};
}

async function main() {
  const passed=[];
  const {app:success,response}=await populated();
  assert.equal(success.state().raw,response.raw);assert.equal(success.state().exportDisabled,false);
  success.run("$('export').onclick()");
  const download=success.env.downloads.at(-1),entry=success.env.created.find(x=>x.value===download.href);
  assert.equal(await entry.blob.text(),response.raw,'Export must preserve raw API bytes, including 792.0');
  passed.push('successful_report_and_export_preserve_raw_response');

  const identity=harness(),old=identity.run('load()');identity.run("$('key').value='new-key';$('key').oninput()");
  const cleared=identity.state();identity.env.pending[0].resolve(jsonReply(fixture('obsolete')));await old;
  assert.deepEqual(identity.state(),cleared);assert.equal(identity.state().savedPresent,false);
  passed.push('late_report_after_identity_change');

  const reload=harness(),first=reload.run('load()'),second=reload.run('load()');
  reload.env.pending[1].resolve(jsonReply(fixture('latest')));await second;const latest=reload.state();
  reload.env.pending[0].resolve(jsonReply(fixture('obsolete')));await first;
  assert.deepEqual(reload.state(),latest);
  passed.push('older_report_cannot_replace_reloaded_report');

  const failure=harness(),oldFailure=failure.run('load()'),newLoad=failure.run('load()');
  failure.env.pending[1].resolve(jsonReply(fixture('latest')));await newLoad;const afterLoad=failure.state();
  failure.env.pending[0].reject(new Error('Synthetic obsolete transport error'));await oldFailure;
  assert.deepEqual(failure.state(),afterLoad);
  passed.push('older_report_failure_cannot_clear_new_report');

  for(const change of ['case','filter','issuer','identity']) {
    const {app}=await populated(),pending=app.run("showPage(currentCase(),'layout')");
    if(change==='case')app.run("$('case').value='report-aapl';$('case').onchange()");
    if(change==='filter')app.run("$('filter').value='wrong';$('filter').onchange()");
    if(change==='issuer')app.run("$('issuer').value='aapl';$('issuer').onchange()");
    if(change==='identity')app.run("$('role').value='viewer';$('role').onchange()");
    const changed=app.state();app.env.pending[1].resolve(imageReply());await pending;
    assert.deepEqual(app.state(),changed);assert.equal(app.env.created.length,0);
    assert.equal(app.state().frameChildren,0);
    passed.push('late_image_after_'+change);
  }

  const {app:order}=await populated(),oldImage=order.run("showPage(currentCase(),'text_sequence')");
  const newImage=order.run("showPage(currentCase(),'layout')");
  order.env.pending[2].resolve(imageReply());await newImage;const newestImage=order.state();
  order.env.pending[1].resolve(imageReply());await oldImage;
  assert.deepEqual(order.state(),newestImage);assert.equal(order.env.created.length,1);
  passed.push('image_request_order_preserves_latest_method');

  const {app:http}=await populated(),shown=http.run("showPage(currentCase(),'layout')");
  http.env.pending[1].resolve(imageReply());await shown;const oldURL=http.state().imageUrl;
  const bad=http.run("showPage(currentCase(),'text_sequence')");
  http.env.pending[2].resolve({ok:false,status:404});await bad;
  assert.equal(http.state().frameChildren,0);assert.equal(http.state().imageUrl,null);
  assert(http.env.revoked.includes(oldURL));assert(http.state().previewNote.includes('官方文件链接'));
  passed.push('image_http_failure_clears_previous_image');

  const {app:decode}=await populated(),loadImage=decode.run("showPage(currentCase(),'layout')");
  decode.env.pending[1].resolve(imageReply());await loadImage;
  const image=decode.nodes.pageFrame.children.find(n=>n.tagName==='IMG'),url=decode.state().imageUrl;
  assert.equal(typeof image.onerror,'function');image.onerror();
  assert.equal(decode.state().frameChildren,0);assert.equal(decode.state().imageUrl,null);
  assert(decode.env.revoked.includes(url));assert(decode.state().previewNote.includes('官方文件链接'));
  passed.push('decode_failure_clears_overlay_and_releases_url');

  const {app:stale}=await populated(),a=stale.run("showPage(currentCase(),'text_sequence')");
  stale.env.pending[1].resolve(imageReply());await a;
  const previous=stale.nodes.pageFrame.children.find(n=>n.tagName==='IMG');
  const b=stale.run("showPage(currentCase(),'layout')");stale.env.pending[2].resolve(imageReply());await b;
  const active=stale.state();previous.onerror();assert.deepEqual(stale.state(),active);
  passed.push('obsolete_decode_failure_cannot_clear_current_image');

  const {app:logout}=await populated(),request=logout.run("showPage(currentCase(),'layout')");
  logout.run("$('key').oninput()");const reset=logout.state();
  logout.env.pending[1].reject(new Error('Synthetic old image error'));await request;
  assert.deepEqual(logout.state(),reset);passed.push('late_image_error_after_identity_change');

  const switching=harness(),obsolete=switching.run('load()');
  switching.run("$('experiment').value='word';$('experiment').onchange()");
  const switched=switching.state();switching.env.pending[0].resolve(jsonReply(fixture()));await obsolete;
  assert.deepEqual(switching.state(),switched);assert.equal(switching.state().raw,null);
  assert.equal(switching.state().apiBase,null);
  passed.push('experiment_change_invalidates_pending_report');

  const replacing=harness(),oldReport=replacing.run('load()');
  replacing.run("$('experiment').value='word';$('experiment').onchange()");
  const wordLoad=replacing.run('load()'),wordResponse=jsonReply(wordFixture());
  assert.equal(replacing.env.pending[1].url,'/api/word-table-lookup');
  replacing.env.pending[1].resolve(wordResponse);await wordLoad;
  const wordState=replacing.state();replacing.env.pending[0].resolve(jsonReply(fixture()));await oldReport;
  assert.deepEqual(replacing.state(),wordState);assert.equal(wordState.apiBase,'/api/word-table-lookup');
  assert.deepEqual(wordState.methodKeys,['line','word']);
  replacing.run("$('export').onclick()");
  const wordDownload=replacing.env.downloads.at(-1);
  assert.equal(wordDownload.name,'creditlens-word-table-lookup.json');
  assert.equal(await replacing.env.created.find(x=>x.value===wordDownload.href).blob.text(),wordResponse.raw);
  passed.push('new_experiment_keeps_new_endpoint_and_raw_export');

  const {app:imageSwitch}=await populated(),pendingOldImage=imageSwitch.run("showPage(currentCase(),'layout')");
  imageSwitch.run("$('experiment').value='word';$('experiment').onchange()");
  const readingWord=imageSwitch.run('load()');imageSwitch.env.pending[2].resolve(jsonReply(wordFixture()));await readingWord;
  const readingWordImage=imageSwitch.run("showPage(currentCase(),'word')");
  assert(imageSwitch.env.pending[3].url.startsWith('/api/word-table-lookup/cases/'));
  assert(imageSwitch.env.pending[3].url.includes('b'.repeat(64)));
  imageSwitch.env.pending[3].resolve(imageReply());await readingWordImage;
  const newestWordImage=imageSwitch.state();imageSwitch.env.pending[1].resolve(imageReply());await pendingOldImage;
  assert.deepEqual(imageSwitch.state(),newestWordImage);
  passed.push('experiment_change_isolates_old_image_and_report_binding');

  const transitionRows=replacing.nodes.transitions.children;
  assert.deepEqual(transitionRows.map(row=>row.children.slice(1).map(cell=>cell.textContent)),
    [['0','0','1'],['0','0','0'],['1','0','0']],
    'Transition table must retain one regression and one improvement, not only a zero net change');
  assert.equal(replacing.state().transitionHidden,false);assert(replacing.state().caseDelta.includes('→'));
  replacing.run("$('filter').value='wrong';filterCases()");
  assert.equal(replacing.state().detailHidden,true);assert.equal(replacing.state().caseDelta,'');
  assert.equal(replacing.nodes.transitions.children.length,3);
  replacing.run("$('experiment').value='line';$('experiment').onchange()");
  assert.equal(replacing.state().transitionHidden,true);assert.equal(replacing.state().transitionRows,0);
  assert.equal(replacing.state().raw,null);assert.equal(replacing.state().exportDisabled,true);
  passed.push('transitions_preserve_regression_and_clear_on_experiment_change');

  const reportPath=process.argv[3] ? path.resolve(process.argv[3])
    : path.resolve(__dirname,'../artifacts/table-lookup-report.json');
  let savedCasesRendered=0,wordCasesRendered=0;
  if(fs.existsSync(reportPath)) {
    const reportBytes=fs.readFileSync(reportPath),payload=JSON.parse(reportBytes);
    const saved=payload.report ? payload : {report:payload,
      report_sha256:crypto.createHash('sha256').update(reportBytes).digest('hex')};
    const real=harness(),loading=real.run('load()');
    real.env.pending[0].resolve(jsonReply(saved));await loading;
    assert.equal(real.state().savedPresent,true,'Saved report must fit the real page contract');
    assert.equal(real.state().caseIds.length,42);
    for(const id of real.state().caseIds) {
      real.run("$('case').value="+JSON.stringify(id)+";renderCase()");
      assert.equal(real.state().detailHidden,false);
      for(const method of ['text_sequence','layout']) {
        const request=real.run('showPage(currentCase(),'+JSON.stringify(method)+')');
        real.env.pending.at(-1).resolve(imageReply());await request;
        assert(real.nodes.pageFrame.children.some(n=>n.tagName==='IMG'));
        for(const box of real.nodes.pageFrame.children.filter(n=>n.tagName==='SPAN')) {
          assert(Object.values(box.style).every(value=>!String(value).includes('NaN')),
            'Saved extraction evidence must have usable page-relative coordinates');
        }
      }
      savedCasesRendered++;
    }
    real.run("$('filter').value='common';filterCases()");
    assert.equal(real.nodes.case.children.length,8,'Common subset must contain exactly eight fixed cases');
    assert.equal(real.state().caseIds.length,42,'Filter must not replace the complete development denominator');
    passed.push('saved_report_all_cases_and_common_subset_fit_page');
  }
  const wordReportPath=process.argv[4] ? path.resolve(process.argv[4])
    : path.resolve(__dirname,'../artifacts/word-table-lookup-report.json');
  if(fs.existsSync(wordReportPath)) {
    const bytes=fs.readFileSync(wordReportPath),payload=JSON.parse(bytes);
    const saved=payload.report ? payload : {report:payload,
      report_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
    const real=harness();real.run("$('experiment').value='word';$('experiment').onchange()");
    const loading=real.run('load()');real.env.pending[0].resolve(jsonReply(saved));await loading;
    assert.equal(real.state().savedPresent,true);assert.equal(real.state().caseIds.length,42);
    assert.equal(real.state().transitionHidden,false);
    assert.equal(real.nodes.transitions.children.flatMap(row=>row.children.slice(1))
      .reduce((sum,cell)=>sum+Number(cell.textContent),0),42);
    for(const id of real.state().caseIds) {
      real.run("$('case').value="+JSON.stringify(id)+";renderCase()");
      assert.equal(real.state().detailHidden,false);
      for(const method of ['line','word']) {
        const request=real.run('showPage(currentCase(),'+JSON.stringify(method)+')');
        real.env.pending.at(-1).resolve(imageReply());await request;
        assert(real.nodes.pageFrame.children.some(n=>n.tagName==='IMG'));
        for(const box of real.nodes.pageFrame.children.filter(n=>n.tagName==='SPAN'))
          assert(Object.values(box.style).every(value=>!String(value).includes('NaN')));
      }
      wordCasesRendered++;
    }
    real.run("$('filter').value='common';filterCases()");assert.equal(real.nodes.case.children.length,8);
    passed.push('saved_word_report_all_cases_and_transition_matrix_fit_page');
  }

  process.stdout.write(JSON.stringify({status:'passed',checks:passed.length,cases:passed,
    saved_cases_rendered:savedCasesRendered,saved_word_cases_rendered:wordCasesRendered,
    scope:'Actual page script in isolated VM with synthetic report and controlled promises; no real browser, decoder, HTTP fault injection or model execution.'},null,2)+'\n');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

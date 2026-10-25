'use strict';

// Run the actual page script with synthetic responses and controlled promises.
// This is not browser rendering, real network fault injection, or human annotation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const htmlPath = process.argv[2] ? path.resolve(process.argv[2])
  : path.resolve(__dirname, '../creditlens/reference-review.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const start = html.indexOf('<script>'), end = html.indexOf('</script>', start);
assert(start >= 0 && end > start, 'Page script not found');
const source = html.slice(start + 8, end);
const copy = value => JSON.parse(JSON.stringify(value));
const task = {id:'rq-synthetic',doc_id:'msft',page:1,width:612,height:792,
  task_sha256:'a'.repeat(64),query:{row_label:'Synthetic financial row',year:2025},
  source:{url:'https://example.invalid/synthetic.pdf',pdf_sha256:'b'.repeat(64),image_sha256:'c'.repeat(64)}};
const answer = {status:'answerable',raw_value:'(777)',normalized_value:'-777',
  unit_text:'SYNTHETIC_UNIT_CANARY',currency_text:null,section_text:'SYNTHETIC_SECTION',
  section_note:null,reason:'SYNTHETIC_OWN_REASON',amount_bbox:{x0:1,top:2,x1:30,bottom:40},
  observation_sha256:'d'.repeat(64)};
function inputObservation(){const value=copy(answer);delete value.normalized_value;delete value.observation_sha256;return value;}
function review(id='synthetic-round', status='awaiting_first') {
  const r={id,task:copy(task),task_sha256:task.task_sha256,round:1,revision:1,status,
    observations:[],comparison:null,human_gold:false};
  if(status!=='awaiting_first')r.observations.push({slot:1,submitted_by:'synthetic-peer',
    visible:false,observation:null});
  if(['agreed','frozen','disagreed'].includes(status)) {
    r.revision=3;r.observations[0].visible=true;r.observations[0].observation=copy(answer);
    r.observations.push({slot:2,submitted_by:'synthetic-second',visible:true,observation:copy(answer)});
    r.comparison={status:status==='disagreed'?'disagreement':'exact_agreement',human_gold:false,
      differences:status==='disagreed'?[{field:'raw_value',first:'(777)',second:'777'}]:[],notes_differ:false};
  }
  return r;
}
function catalog(user='synthetic-observer', role='analyst') {
  return {catalog:{catalog_sha256:'e'.repeat(64),tasks:[copy(task)]},
    identity:{user_id:user,role},auth_mode:'api_key',human_gold:false};
}
function jsonReply(value,status=200) {
  const raw=JSON.stringify(value).replace('"height":792','"height":792.0')+'\n';
  return {status,ok:status>=200&&status<300,text:async()=>raw,raw};
}
const imageReply=()=>({ok:true,status:200,blob:async()=>new Blob(['synthetic image'])});
async function tick(){for(let i=0;i<8;i++)await Promise.resolve();}

function harness() {
  const nodes={},env={requests:[],created:[],revoked:[],downloads:[]};
  function node(tag='div') {
    let text='';
    return {tagName:tag.toUpperCase(),value:'',hidden:false,disabled:false,children:[],style:{},
      get textContent(){return text+this.children.map(x=>x.textContent||'').join('');},
      set textContent(value){text=String(value);this.children=[];},
      append(...items){const empty=this.children.length===0;this.children.push(...items);for(const item of items)item.parent=this;
        if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
      replaceChildren(...items){text='';this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
      querySelectorAll(selector){return this.children.filter(x=>selector==='.'+x.className);},
      remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);},
      getBoundingClientRect(){return {left:10,top:20,width:306,height:396};},
      click(){if(this.tagName==='A')env.downloads.push({href:this.href,name:this.download});}};
  }
  const document={getElementById(id){return nodes[id]??=node(
    ['role','task','reviewList','answerStatus'].includes(id)?'select':'div');},createElement:node};
  class LocalURL extends URL {
    static createObjectURL(blob){const value='blob:synthetic-'+(env.created.length+1);env.created.push({value,blob});return value;}
    static revokeObjectURL(value){env.revoked.push(value);}
  }
  const context=vm.createContext({document,URL:LocalURL,Blob,crypto,setTimeout:fn=>fn(),
    fetch:(url,options)=>new Promise((resolve,reject)=>env.requests.push({url,options,resolve,reject}))});
  const run=code=>vm.runInContext(code,context,{timeout:1000});
  vm.runInContext(source,context,{timeout:1000});run("$('role').value='analyst';");
  function state(){return JSON.parse(run(`JSON.stringify({epoch,selectionEpoch,imageEpoch,
    catalogPresent:catalog!==null,identity,currentId:current&&current.id,currentRevision:current&&current.revision,
    busy,pending,uncertain:typeof uncertain==='undefined'?false:uncertain,imageUrl,
    status:$('status').textContent,detailHidden:$('detail').hidden,
    retryDisabled:$('retry').disabled,submitDisabled:$('submit').disabled,
    freezeDisabled:$('freeze').disabled,closeDisabled:$('close').disabled,
    frameChildren:$('pageFrame').children.length,record:$('record').textContent,
    observations:$('observations').textContent,comparison:$('comparison').textContent})`));}
  return {nodes,env,run,state};
}
async function loaded(user='synthetic-observer',role='analyst') {
  const app=harness(),loading=app.run('refresh()');
  app.env.requests[0].resolve(jsonReply(catalog(user,role)));
  app.env.requests[1].resolve(jsonReply([review()]));await loading;return app;
}
function show(app,r=review()){app.run('renderReview('+JSON.stringify(r)+')');}
async function successfulWrite(app,operation,r) {
  const post=app.env.requests.at(-1);post.resolve(jsonReply(r));await tick();
  const [latest,rows]=app.env.requests.slice(-2);
  assert(latest.url.endsWith('/reviews/'+encodeURIComponent(r.id)),'Write should reread the current record');
  assert(rows.url.endsWith('/reviews'),'Write should refresh list');
  latest.resolve(jsonReply(r));rows.resolve(jsonReply([r]));await operation;
}

async function main() {
  const passed=[];
  {
    const app=harness(),p=app.run('refresh()');app.run("$('key').value='new';$('key').oninput()");
    const cleared=app.state();app.env.requests[0].resolve(jsonReply(catalog()));
    app.env.requests[1].resolve(jsonReply([review()]));await p;assert.deepEqual(app.state(),cleared);
    passed.push('late_catalog_after_identity_change');
  }
  {
    const app=harness(),old=app.run('refresh()'),latest=app.run('refresh()');
    app.env.requests[2].resolve(jsonReply(catalog('latest')));app.env.requests[3].resolve(jsonReply([]));await latest;
    const expected=app.state();app.env.requests[0].resolve(jsonReply(catalog('old')));
    app.env.requests[1].resolve(jsonReply([review('old')]));await old;assert.deepEqual(app.state(),expected);
    passed.push('older_catalog_cannot_replace_reloaded_catalog');
  }
  for(const change of ['identity','selection']) {
    const app=await loaded(),p=app.run('loadReview()');
    app.run(change==='identity'?"$('role').value='viewer';$('role').onchange()":"$('reviewList').value='another';$('reviewList').onchange()");
    const expected=app.state();app.env.requests.at(-1).resolve(jsonReply(review('obsolete','frozen')));
    await p;assert.deepEqual(app.state(),expected);passed.push('late_review_after_'+change);
  }
  {
    const app=await loaded();show(app,review('secret-record','frozen'));
    app.run("$('createReason').value='PRIVATE_CREATE_REASON';$('reason').value='PRIVATE_DRAFT';$('key').oninput()");
    for(const id of ['identity','taskMeta','taskSource','reviewTitle','reviewMeta','source','coordinates','comparison','record','observations'])
      assert.equal(app.nodes[id].textContent,'',id+' must be cleared even when hidden');
    for(const id of ['createReason','rawValue','reason','actionNote','sectionText','sectionNote','unitText','currencyText','x0','top','x1','bottom'])
      assert.equal(app.nodes[id].value,'',id+' draft must be cleared');
    assert.equal(app.state().currentId,null);passed.push('identity_clears_private_and_hidden_dom');
  }
  {
    const app=await loaded();show(app,review('first-only','awaiting_second'));
    assert(!app.state().record.includes('observation_sha256'));
    assert(!app.state().observations.includes('CANARY'));
    assert.equal(app.state().freezeDisabled,true);assert.equal(app.state().submitDisabled,false);
    assert.equal(app.nodes.entry.hidden,false);
    show(app,review('revealed','agreed'));assert(app.state().record.includes('SYNTHETIC_UNIT_CANARY'));
    assert.equal(app.nodes.entry.hidden,true);
    passed.push('server_hidden_first_view_not_prefilled_reveal_only_on_second');
  }
  {
    const own=await loaded('synthetic-peer','reviewer');show(own,review('owned','agreed'));
    assert(own.state().freezeDisabled&&own.state().closeDisabled);assert.equal(own.nodes.entry.hidden,true);
    const third=await loaded('synthetic-third','reviewer');show(third,review('third','agreed'));
    assert.equal(third.state().freezeDisabled,false);
    const viewer=await loaded('synthetic-viewer','viewer');show(viewer,review('viewer','agreed'));
    assert(viewer.state().freezeDisabled&&viewer.state().submitDisabled&&viewer.state().closeDisabled);
    assert.equal(viewer.nodes.entry.hidden,true);
    passed.push('ui_self_review_and_role_controls');
  }
  for(const change of ['identity','selection']) {
    const app=await loaded();show(app);const p=app.run('showPage()');
    app.run(change==='identity'?"$('key').oninput()":"$('reviewList').onchange()");
    const expected=app.state();app.env.requests.at(-1).resolve(imageReply());await p;
    assert.deepEqual(app.state(),expected);assert.equal(app.env.created.length,0);
    passed.push('late_image_after_'+change);
  }
  {
    const app=await loaded();show(app);const first=app.run('showPage()');
    app.env.requests.at(-1).resolve(imageReply());await first;
    const oldImage=app.nodes.pageFrame.children[0],oldUrl=app.state().imageUrl;
    const second=app.run('showPage()');app.env.requests.at(-1).resolve(imageReply());await second;
    const expected=app.state();oldImage.onerror();assert.deepEqual(app.state(),expected);
    app.nodes.pageFrame.children[0].onerror();assert.equal(app.state().frameChildren,0);
    assert.equal(app.state().imageUrl,null);assert(app.env.revoked.includes(oldUrl));
    passed.push('image_decode_error_clears_and_late_decoder_cannot_clear_new_image');
  }
  {
    const app=await loaded();show(app);const p=app.run('showPage()');
    app.env.requests.at(-1).resolve(imageReply());await p;
    assert.equal(app.nodes.pageFrame.children.length,1,'No reference box may be prefilled');
    const img=app.nodes.pageFrame.children[0];img.onclick({clientX:20,clientY:30});
    assert.equal(app.nodes.x0.value,'','A first corner must not submit or choose an amount');
    img.onclick({clientX:40,clientY:50});
    assert.deepEqual(['x0','top','x1','bottom'].map(id=>app.nodes[id].value),['20','20','60','60']);
    assert.equal(app.nodes.pageFrame.querySelectorAll('.selection-box').length,1);
    assert.equal(app.nodes.rawValue.value,'');assert.equal(app.nodes.unitText.value,'');
    app.run("$('x0').value='22';$('x0').oninput()");
    assert.equal(app.nodes.pageFrame.querySelectorAll('.selection-box').length,0);
    passed.push('manual_two_corners_use_page_geometry_without_prefilling_reference');
  }
  {
    const app=await loaded();show(app);const p=app.run('showPage()');
    app.env.requests.at(-1).resolve(imageReply());await p;
    const img=app.nodes.pageFrame.children[0];img.onclick({clientX:20,clientY:30});
    app.run("$('key').oninput()");const expected=app.state();
    img.onclick({clientX:40,clientY:50});assert.deepEqual(app.state(),expected);
    assert.deepEqual(['x0','top','x1','bottom'].map(id=>app.nodes[id].value),['','','','']);
    passed.push('late_manual_corner_after_identity_change_cannot_fill_coordinates');
  }
  {
    const app=await loaded();show(app,review('frozen','frozen'));const p=app.run('download()');
    const reply=jsonReply({content:review('frozen','frozen'),human_gold:false});
    app.env.requests.at(-1).resolve(reply);await p;
    const dl=app.env.downloads.at(-1),blob=app.env.created.find(x=>x.value===dl.href).blob;
    assert.equal(await blob.text(),reply.raw);assert(reply.raw.includes('792.0'));
    passed.push('export_preserves_raw_response_bytes');
  }
  {
    const app=await loaded();show(app,review('old','frozen'));const p=app.run('download()');
    app.run("$('key').oninput()");app.env.requests.at(-1).resolve(jsonReply({private:'obsolete'}));
    await p;assert.equal(app.env.downloads.length,0);passed.push('late_export_after_identity_change');
  }
  for(const mode of ['transport','http500','invalid_json_200']) {
    const app=await loaded();show(app);const body=JSON.stringify({revision:1,observation:inputObservation()});
    const intent={path:'/api/reference-review/reviews/synthetic-round/observations',body,key:'stable-'+mode};
    const p=app.run('execute('+JSON.stringify(intent)+')'),first=app.env.requests.at(-1);
    if(mode==='transport')first.reject(new Error('Synthetic response loss'));
    if(mode==='http500')first.resolve(jsonReply({detail:'Synthetic post-commit failure'},500));
    if(mode==='invalid_json_200')first.resolve({ok:true,status:200,text:async()=>'<invalid>'});
    await p;assert.deepEqual(app.state().pending,intent);assert.equal(app.state().retryDisabled,false);
    app.run("$('rawValue').value='CHANGED_LOCAL_DRAFT';current.revision=999;");
    const retried=app.run('execute(pending)'),second=app.env.requests.at(-1);
    assert.equal(second.options.body,first.options.body);
    assert.equal(second.options.headers['Idempotency-Key'],first.options.headers['Idempotency-Key']);
    await successfulWrite(app,retried,review('saved','awaiting_second'));
    assert.equal(app.state().pending,null);assert.equal(app.state().busy,false);
    passed.push('uncertain_'+mode+'_retries_exact_body_and_key');
  }
  {
    const app=await loaded();app.run("$('createReason').value='Synthetic creation note';create(false)");
    const first=app.env.requests.at(-1),body=first.options.body,key=first.options.headers['Idempotency-Key'];
    assert.equal(JSON.parse(body).reason,'Synthetic creation note');
    assert.equal(JSON.parse(body).task_id,task.id);assert(key);
    first.reject(new Error('Synthetic create response loss'));await tick();
    app.run("$('createReason').value='New draft must not alter old request'");
    const retried=app.run('execute(pending)'),second=app.env.requests.at(-1);
    assert.equal(second.options.body,body);assert.equal(second.options.headers['Idempotency-Key'],key);
    await successfulWrite(app,retried,review('created'));
    assert.equal(app.state().currentId,'created');assert.equal(app.state().pending,null);
    passed.push('create_response_loss_retries_original_intent_despite_edited_note');
  }
  {
    const app=await loaded();show(app);const p=app.run("execute({path:'/synthetic',body:'{}',key:'rejected'})");
    app.env.requests.at(-1).resolve(jsonReply({detail:'Synthetic rejected input'},422));await p;
    assert.equal(app.state().pending,null);assert.equal(app.state().retryDisabled,true);
    passed.push('definite_rejection_releases_write_intent');
  }
  {
    const app=await loaded('synthetic-third','reviewer');show(app,review('agreed','agreed'));
    const p=app.run("execute({path:'/synthetic/freeze',body:'{}',key:null})");
    app.env.requests.at(-1).reject(new Error('Synthetic unknown action outcome'));await p;
    assert.equal(app.state().uncertain,true);assert.equal(app.state().freezeDisabled,true);
    const before=app.env.requests.length;app.run("mutate('/synthetic/freeze',{},false)");
    assert.equal(app.env.requests.length,before);
    const reread=app.run('loadReview()');app.env.requests.at(-1).resolve(jsonReply(review('frozen','frozen')));await reread;
    assert.equal(app.state().uncertain,false);assert.equal(app.state().currentId,'frozen');
    passed.push('non_idempotent_unknown_action_requires_state_read');
  }
  {
    const app=await loaded();show(app);const p=app.run("execute({path:'/synthetic',body:'{}',key:'old'})");
    app.run("$('key').oninput()");const expected=app.state();
    app.env.requests.at(-1).resolve(jsonReply(review('obsolete','frozen')));await p;
    assert.deepEqual(app.state(),expected);passed.push('late_mutation_after_identity_change');
  }
  {
    const app=await loaded();show(app);const p=app.run("execute({path:'/synthetic',body:'{}',key:'saved'})");
    app.env.requests.at(-1).resolve(jsonReply(review('saved')));await tick();
    app.env.requests.at(-2).resolve(jsonReply(review('saved')));
    app.env.requests.at(-1).reject(new Error('Synthetic list refresh failure'));await p;
    assert.equal(app.state().currentId,'saved');assert.equal(app.state().pending,null);
    assert.equal(app.state().busy,false);assert.equal(app.state().uncertain,true);
    assert(app.state().status.includes('已保存'));assert(app.state().status.includes('操作返回版本'));
    passed.push('confirmed_save_survives_followup_list_failure');
  }
  {
    const app=await loaded();show(app);const p=app.run("execute({path:'/synthetic',body:'{}',key:'original-submit'})");
    const old=review('saved','awaiting_second');old.revision=2;
    app.env.requests.at(-1).resolve(jsonReply(old));await tick();
    const latest=review('saved','agreed');latest.revision=3;
    app.env.requests.at(-2).resolve(jsonReply(latest));app.env.requests.at(-1).resolve(jsonReply([latest]));await p;
    assert.equal(app.state().currentRevision,3);assert.equal(app.state().uncertain,false);
    assert(app.state().record.includes('exact_agreement'));assert.equal(app.state().pending,null);
    passed.push('old_idempotent_operation_response_followed_by_current_version');
  }
  {
    const app=await loaded();show(app);const p=app.run("execute({path:'/synthetic',body:'{}',key:'saved-read-failed'})");
    const old=review('synthetic-round','awaiting_second');old.revision=2;
    app.env.requests.at(-1).resolve(jsonReply(old));await tick();
    app.env.requests.at(-2).reject(new Error('Synthetic current record read failure'));
    app.env.requests.at(-1).resolve(jsonReply([review('synthetic-round','agreed')]));await p;
    assert.equal(app.state().currentRevision,2);assert.equal(app.state().uncertain,true);
    assert.equal(app.state().pending,null);assert.equal(app.state().submitDisabled,true);
    const before=app.env.requests.length;app.run("mutate('/synthetic',{},true)");assert.equal(app.env.requests.length,before);
    const read=app.run('loadReview()');app.env.requests.at(-1).resolve(jsonReply(review('synthetic-round','agreed')));await read;
    assert.equal(app.state().currentRevision,3);assert.equal(app.state().uncertain,false);
    passed.push('post_save_current_read_failure_blocks_new_write_until_reload');
  }
  console.log(JSON.stringify({scope:'Actual page script with synthetic response controls; not a browser or human-gold result',
    page:path.basename(htmlPath),passed:passed.length,controls:passed},null,2));
}
const deadline=setTimeout(()=>{console.error('Controlled page test did not settle');process.exit(1);},5000);
main().then(()=>clearTimeout(deadline)).catch(error=>{clearTimeout(deadline);console.error(error);process.exitCode=1;});

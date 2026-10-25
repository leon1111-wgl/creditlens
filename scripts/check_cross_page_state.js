'use strict';

// Load the real page script, replacing only browser surfaces and transport.
// These synthetic state controls do not execute a model or actual HTTP request.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const htmlPath = process.argv[2] ? path.resolve(process.argv[2])
  : path.resolve(__dirname, '../creditlens/cross-page.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const start = html.indexOf('<script>'), end = html.indexOf('</script>', start);
assert(start >= 0 && end > start, 'Page script not found');
const source = html.slice(start + 8, end).replace(/loadCatalog\(\);\s*$/, '');
assert(!source.endsWith('loadCatalog();'), 'Initial request must be controlled');

function card(issuer, role) {
  return {id:issuer+'-'+role,issuer_id:issuer,year:2025,role,label:role,
    raw_value:'10',unit:'USD_million',period:{start:'2024-01-01',end:'2024-12-31'},
    source:{url:'https://example.invalid/registered.pdf'},provenance:{reference_method:'synthetic control'},
    location:{page:role==='revenue'?2:1,page_width:100,page_height:100,locator:'synthetic',
      amount_bbox:{x0:10,top:20,x1:30,bottom:40},year_bbox:null,unit_bbox:null}};
}
function fixture() {
  const roles=['cfo','revenue','consolidation_scope'];
  return {tasks:['A','B'].map(id=>({id,issuer_id:id,year:2025,
    period:{start:'2024-01-01',end:'2024-12-31'},
    required_role_ids:Object.fromEntries(roles.map(role=>[role,id+'-'+role]))})),
    cards:['A','B'].flatMap(id=>roles.map(role=>card(id,role)))};
}
function harness() {
  const nodes={}, env={pending:[],created:[],revoked:[],catalog:fixture()};
  function node(tag='div') {
    return {tagName:tag.toUpperCase(),textContent:'',value:'',disabled:false,hidden:false,
      style:{},children:[],open:false,
      append(...items){const empty=this.children.length===0;this.children.push(...items);
        if(this.tagName==='SELECT'&&empty&&items.length)this.value=items[0].value;},
      replaceChildren(...items){this.children=[];if(this.tagName==='SELECT')this.value='';this.append(...items);},
      click(){}};
  }
  const document={getElementById(id){return nodes[id]??=node(
    ['task','role','cfo','revenue','consolidation_scope'].includes(id)?'select':'div');},createElement:node};
  class LocalURL extends URL {
    static createObjectURL(){const value='blob:synthetic-'+(env.created.length+1);env.created.push(value);return value;}
    static revokeObjectURL(value){env.revoked.push(value);}
  }
  const context=vm.createContext({document,URL:LocalURL,Blob,setTimeout:fn=>fn(),
    fetch:(url,options)=>new Promise((resolve,reject)=>env.pending.push({url,options,resolve,reject})),env});
  vm.runInContext(source,context,{timeout:1000});
  vm.runInContext("catalog=env.catalog;catalogSha='a'.repeat(64);$('task').value='A';changeTask();",context,{timeout:1000});
  return {env,nodes,run:code=>vm.runInContext(code,context,{timeout:1000}),
    state:()=>JSON.parse(vm.runInContext(`JSON.stringify({epoch,imageEpoch,resultRaw,imageUrl,
      catalogPresent:catalog!==null,exportDisabled:$('export').disabled,resultHidden:$('result').hidden,
      formula:$('formula').textContent,status:$('status').textContent,resultStatus:$('resultStatus').textContent,
      workspaceHidden:$('workspace').hidden,taskMeta:$('taskMeta').textContent,
      previewHidden:$('preview').hidden,previewTitle:$('previewTitle').textContent,
      previewNote:$('previewNote').textContent,frameChildren:$('pageFrame').children.length})`,context,{timeout:1000}))};
}
function jsonReply(value) {
  const raw=JSON.stringify(value).replace('"synthetic_height":792','"synthetic_height":792.0')+'\n';
  return {ok:true,status:200,text:async()=>raw,raw};
}
function checkReply() {
  return jsonReply({synthetic_height:792,result:{status:'computed',checks:[],
    calculation:{inputs:{cfo:{normalized_value:'10'},revenue:{normalized_value:'20'}},
      result:{value:'50.000000'},interpretation:'synthetic ratio',decimal_places:6}}});
}
const imageReply=()=>({ok:true,status:200,blob:async()=>({synthetic:true})});

async function main() {
  const passed=[];
  const success=harness(), response=checkReply(), done=success.run('check()');
  success.env.pending[0].resolve(response);await done;
  assert.equal(success.state().resultRaw,response.raw);
  assert.equal(success.state().exportDisabled,false);
  assert.equal(success.state().resultHidden,false);
  passed.push('successful_check_preserves_raw_export');

  for(const change of ['edit','task','identity']) {
    const app=harness(), pending=app.run('check()');
    if(change==='edit')app.run("$('revenue').value='';handleEdit()");
    if(change==='task')app.run("$('task').value='B';changeTask()");
    if(change==='identity')app.run('reset()');
    const afterChange=app.state();
    app.env.pending[0].resolve(checkReply());await pending;
    assert.deepEqual(app.state(),afterChange,'Late check must not restore a stale result');
    assert.equal(afterChange.resultRaw,null);assert.equal(afterChange.formula,'');
    assert.equal(afterChange.exportDisabled,true);
    if(change==='identity')assert.equal(afterChange.taskMeta,'');
    passed.push('late_check_after_'+change);
  }

  const loading=harness(), catalogLoad=loading.run('loadCatalog()');
  loading.run('reset()');const resetState=loading.state();
  loading.env.pending[0].resolve(jsonReply({catalog:fixture(),catalog_sha256:'b'.repeat(64)}));
  await catalogLoad;assert.deepEqual(loading.state(),resetState);
  passed.push('late_catalog_after_identity_change');

  const duringCheck=harness(), imageLoad=duringCheck.run("showPage(catalog.cards[0])");
  const checking=duringCheck.run('check()');
  assert.equal(duringCheck.state().previewHidden,true,'Check must cancel any pending image view');
  assert.equal(duringCheck.state().previewNote,'');
  duringCheck.env.pending[0].resolve(imageReply());await imageLoad;
  assert.equal(duringCheck.state().frameChildren,0);
  duringCheck.env.pending[1].resolve(checkReply());await checking;
  passed.push('check_cancels_pending_image_without_stuck_loading');

  const reordered=harness(), oldImage=reordered.run('showPage(catalog.cards[0])');
  const newImage=reordered.run('showPage(catalog.cards[3])');
  reordered.env.pending[1].resolve(imageReply());await newImage;
  const latestImage=reordered.state();
  reordered.env.pending[0].resolve(imageReply());await oldImage;
  assert.deepEqual(reordered.state(),latestImage);
  assert.equal(reordered.env.created.length,1,'Obsolete response must not create a blob URL');
  passed.push('out_of_order_image_preserves_latest');

  const failedImage=harness(), first=failedImage.run('showPage(catalog.cards[0])');
  failedImage.env.pending[0].resolve(imageReply());await first;
  const oldURL=failedImage.state().imageUrl;
  const unavailable=failedImage.run('showPage(catalog.cards[3])');
  assert(failedImage.env.revoked.includes(oldURL));
  failedImage.env.pending[1].resolve({ok:false,status:404});await unavailable;
  assert.equal(failedImage.state().frameChildren,0);assert.equal(failedImage.state().imageUrl,null);
  assert(failedImage.state().previewNote.includes('官方文件链接'));
  passed.push('image_http_failure_clears_old_page');

  const decode=harness(), decoded=decode.run('showPage(catalog.cards[0])');
  decode.env.pending[0].resolve(imageReply());await decoded;
  const badImage=decode.nodes.pageFrame.children.find(n=>n.tagName==='IMG');
  assert.equal(typeof badImage.onerror,'function','A decode failure needs a guarded handler');
  const badURL=decode.state().imageUrl;badImage.onerror();
  assert.equal(decode.state().frameChildren,0);assert.equal(decode.state().imageUrl,null);
  assert(decode.env.revoked.includes(badURL));assert(decode.state().previewNote.includes('官方文件链接'));
  passed.push('image_decode_failure_clears_overlays_and_releases_url');

  const staleError=harness(), oldLoad=staleError.run('showPage(catalog.cards[0])');
  staleError.env.pending[0].resolve(imageReply());await oldLoad;
  const oldElement=staleError.nodes.pageFrame.children.find(n=>n.tagName==='IMG');
  const replacement=staleError.run('showPage(catalog.cards[3])');
  staleError.env.pending[1].resolve(imageReply());await replacement;
  const replacedState=staleError.state();oldElement.onerror();
  assert.deepEqual(staleError.state(),replacedState);
  passed.push('obsolete_image_error_cannot_clear_new_page');

  const identityImage=harness(), identityLoad=identityImage.run('showPage(catalog.cards[0])');
  identityImage.run('reset()');const cleared=identityImage.state();
  identityImage.env.pending[0].reject(new Error('Synthetic obsolete transport error'));await identityLoad;
  assert.deepEqual(identityImage.state(),cleared);
  passed.push('late_image_error_after_identity_change');

  process.stdout.write(JSON.stringify({status:'passed',checks:passed.length,cases:passed,
    scope:'Actual page script in isolated VM with synthetic catalog and controlled promises; no real browser, image decoder or HTTP fault injection.'},null,2)+'\n');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';

// Run the shipped view handlers with controlled promises. No browser, HTTP,
// database, document bytes or model are used by these asynchronous controls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const htmlPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.resolve(__dirname, '../creditlens/preparation.html');
const html = fs.readFileSync(htmlPath, 'utf8');
function slice(start, end) {
  const first = html.indexOf(start);
  const last = html.indexOf(end, first + start.length);
  assert(first >= 0 && last > first, 'Cannot locate shipped handler: ' + start);
  return html.slice(first, last);
}
const source = slice('function reset(', 'async function request')
  + slice('function clearCurrent(', 'function renderTask(')
  + slice('async function loadAttempt(', 'function sample(');

function harness() {
  const env = { pending: [] };
  const context = vm.createContext({ env });
  vm.runInContext(`
    let generation=1,viewEpoch=1,attemptEpoch=1,refreshEpoch=1,currentEpoch=1;
    let task={id:'task-a'},currentRaw=null,currentAttempt=null,exportRaw=null;
    let examples=null,writeIntent=null,busy=false,canWrite=true;
    const nodes=new Proxy({}, {get(target,key) {
      return target[key] ??= {textContent:'',disabled:false,value:'',hidden:false,
        children:[],replaceChildren(...items){this.children=items;},append(...items){this.children.push(...items);}};
    }});
    const $=id=>nodes[id];
    const controls=()=>{};
    const el=(tag,text)=>({tag,text,append(){}});
    const request=route=>new Promise((resolve,reject)=>env.pending.push({route,resolve,reject}));
    ${source}
  `, context, { timeout: 1000 });
  return {
    pending: env.pending,
    run: code => vm.runInContext(code, context, { timeout: 1000 }),
    state: () => JSON.parse(vm.runInContext(`JSON.stringify({generation,attemptEpoch,currentEpoch,
      currentRaw,currentAttempt,exportRaw,currentStatus:$('currentStatus').textContent,
      currentDetails:$('currentDetails').textContent,currentChanges:$('currentChanges').textContent,
      currentDisabled:$('exportCurrent').disabled,historyDisabled:$('export').disabled,
      recheckDisabled:$('recheck').disabled})`, context, { timeout: 1000 }))
  };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function current(revision, tag) {
  return { value: { verification: { revision }, applicability: {
    status: 'matching_registration', ready_for_content_review: false,
    reason_code: tag, changed_fields: []
  } }, raw: '{"synthetic":"' + tag + '","height":792.0}\n' };
}
function historical(revision) {
  return { value: { attempt: { revision, report: {
    status:'blocked',expected_pages:[1,2],summary:{submitted_page_count:0},
    missing_pages:[1,2],duplicate_pages:[],unexpected_pages:[],page_results:[],errors:[],
    material_binding:{},versions:{},scope:{},queryable:false,automatic_approval:false
  } } }, raw: '{"synthetic_history_revision":' + revision + '}\n' };
}

async function main() {
  const passed = [];
  const failedCurrent = harness();
  const historyLoad = failedCurrent.run("loadAttempt('attempt-a')");
  failedCurrent.pending[0].resolve(historical(1));
  await tick();
  assert.equal(failedCurrent.pending.length, 2);
  failedCurrent.pending[1].reject(new Error('Synthetic current check failure'));
  assert.equal(await historyLoad, true, 'Current check failure must not fail historical readback');
  let state = failedCurrent.state();
  assert.equal(state.exportRaw, historical(1).raw);
  assert.equal(state.historyDisabled, false);
  assert.equal(state.currentRaw, null);
  assert.equal(state.currentDisabled, true);
  assert.equal(state.recheckDisabled, false);
  passed.push('current_failure_preserves_historical_readback');

  for (const oldOutcome of ['success', 'error']) {
    const app = harness();
    const older = app.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
    const newer = app.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
    app.pending[1].resolve(current(2, 'newer'));
    assert.equal(await newer, true);
    const before = app.state();
    if (oldOutcome === 'success') app.pending[0].resolve(current(1, 'older'));
    else app.pending[0].reject(new Error('Synthetic obsolete error'));
    assert.equal(await older, false);
    assert.deepEqual(app.state(), before, 'Older completion must not overwrite current view');
    assert.equal(before.currentRaw, current(2, 'newer').raw, 'Export must retain exact response text');
    passed.push('out_of_order_' + oldOutcome);
  }

  const switched = harness();
  const oldLoad = switched.run("loadAttempt('old-attempt')");
  switched.pending[0].resolve(historical(1));
  await tick();
  const newLoad = switched.run("loadAttempt('new-attempt')");
  state = switched.state();
  assert.equal(state.currentRaw, null);
  assert.equal(state.exportRaw, null);
  assert.equal(state.currentDisabled, true);
  switched.pending[2].resolve(historical(2));
  await tick();
  switched.pending[3].resolve(current(2, 'new-attempt'));
  assert.equal(await newLoad, true);
  const beforeOldCompletion = switched.state();
  switched.pending[1].resolve(current(1, 'old-attempt'));
  await oldLoad;
  assert.deepEqual(switched.state(), beforeOldCompletion);
  passed.push('attempt_switch_clears_and_blocks_old_result');

  const identity = harness();
  const identityLoad = identity.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
  identity.run('reset()');
  const afterReset = identity.state();
  identity.pending[0].resolve(current(1, 'previous-identity'));
  assert.equal(await identityLoad, false);
  assert.deepEqual(identity.state(), afterReset);
  assert.equal(afterReset.currentRaw, null);
  assert.equal(afterReset.currentDisabled, true);
  passed.push('identity_reset_blocks_old_result');

  const changedTask = harness();
  const priorTask = changedTask.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
  changedTask.run("clearAttempt(); task={id:'task-b'}");
  const afterTaskChange = changedTask.state();
  changedTask.pending[0].resolve(current(1, 'previous-task'));
  assert.equal(await priorTask, false);
  assert.deepEqual(changedTask.state(), afterTaskChange);
  passed.push('task_switch_blocks_old_result');

  const recheck = harness();
  recheck.run("exportRaw='saved-history'; currentRaw='old-current'; $('export').disabled=false");
  const checking = recheck.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
  state = recheck.state();
  assert.equal(state.currentRaw, null);
  assert.equal(state.currentDisabled, true);
  assert.equal(state.exportRaw, 'saved-history');
  assert.equal(state.historyDisabled, false);
  recheck.pending[0].resolve(current(1, 'rechecked'));
  assert.equal(await checking, true);
  passed.push('recheck_only_clears_current_result');

  for (const [reason, title] of [
    ['historical_page_set_mismatch', '历史页集与当前登记页集不一致'],
    ['historical_page_inventory_mismatch', '历史页登记与当前清单不一致'],
    ['preparation_versions_changed', '准备规则版本已变化']
  ]) {
    const app = harness();
    const checked = app.run("loadCurrent('task-a','attempt-a',generation,attemptEpoch)");
    app.pending[0].resolve(current(1, reason));
    assert.equal(await checked, true);
    assert(app.state().currentStatus.includes(title), 'Binding match must not hide a page/rule mismatch');
    assert(app.state().currentDetails.includes('不能'), 'Mismatch must not permit content review');
    passed.push('reason_title_' + reason);
  }

  process.stdout.write(JSON.stringify({status:'passed',checks:passed.length,cases:passed,
    scope:'Shipped handlers with controlled promises in isolated VM; no browser or actual HTTP fault injection.'},null,2)+'\n');
}
main().catch(error => { console.error(error); process.exitCode=1; });

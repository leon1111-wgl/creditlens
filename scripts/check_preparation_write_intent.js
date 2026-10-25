'use strict';

// Exercise the shipped save handler, with only its browser/HTTP dependencies
// replaced. This is a control of request intent, not a browser/network test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const htmlPath = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.resolve(__dirname, '../creditlens/preparation.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const saveStart = html.indexOf('async function save(');
const saveEnd = html.indexOf("$('key').", saveStart);
assert(saveStart >= 0 && saveEnd > saveStart, 'Cannot locate shipped save handler');
const saveSource = html.slice(saveStart, saveEnd);
const editStart = html.indexOf("$('payload').oninput=", saveEnd);
const editEnd = html.indexOf(";$('create')", editStart);
assert(editStart >= 0 && editEnd > editStart, 'Cannot locate shipped edit handler');
const editSource = html.slice(editStart, editEnd + 1);

function harness(failureStage) {
  const context = vm.createContext({});
  vm.runInContext(`
    let busy = false, canWrite = true, generation = 1;
    let task = { id: 'synthetic-task', revision: 1 }, writeIntent = null;
    let failureStage = ${JSON.stringify(failureStage)};
    let currentRevision = 1, sequence = 0;
    const posted = [], remembered = new Map();
    const nodes = {
      payload: { value: '{"pages":[]}' },
      status: { textContent: '' },
      draftStatus: { textContent: '' }
    };
    const $ = id => nodes[id];
    const crypto = { randomUUID: () => 'synthetic-key-' + (++sequence) };
    const controls = () => {};
    const renderTask = value => { task = value; };
    const loadAttempt = async () => failureStage !== 'export';
    const refresh = async () => true;
    const request = async (route, options) => {
      if (options?.method === 'POST') {
        const key = options.headers['Idempotency-Key'];
        posted.push({ route, key, raw: options.body });
        if (!remembered.has(key)) {
          currentRevision += 1;
          remembered.set(key, {
            id: 'synthetic-task',
            latest_attempt: { id: 'synthetic-attempt-' + currentRevision }
          });
        }
        return { value: remembered.get(key) };
      }
      if (failureStage === 'current') throw new Error('Synthetic current read failure');
      return { value: { id: 'synthetic-task', revision: currentRevision } };
    };
    ${saveSource}
    ${editSource}
  `, context, { timeout: 1000 });
  return {
    save: retry => vm.runInContext(`save(${retry})`, context, { timeout: 1000 }),
    edit: value => vm.runInContext(
      `nodes.payload.value=${JSON.stringify(value)}; nodes.payload.oninput();`,
      context, { timeout: 1000 }
    ),
    state: () => JSON.parse(vm.runInContext(
      'JSON.stringify({posted,writeIntent,task,currentRevision,busy,status:nodes.status.textContent})',
      context, { timeout: 1000 }
    ))
  };
}

async function main() {
  const passed = [];
  for (const retry of [false, true]) {
    for (const failureStage of ['current', 'export']) {
      const app = harness(failureStage);
      await app.save(retry);
      const first = app.state();
      assert(first.writeIntent, 'Read failure must preserve write intent');
      assert.equal(first.busy, false);
      await app.save(retry);
      const final = app.state();
      assert.equal(final.posted.length, 2);
      assert.deepEqual(final.posted[1], final.posted[0], 'Retry must keep route, key and exact body');
      assert.equal(final.currentRevision, 2, 'Read retry must not create another attempt');
      if (retry) assert.equal(JSON.parse(final.posted[1].raw).revision, 1);
      if (failureStage === 'export') assert.equal(final.task.revision, 2);
      passed.push(`${retry ? 'retry' : 'create'}_${failureStage}_read_failure`);
    }
  }

  const success = harness('none');
  await success.save(true);
  assert.equal(success.state().writeIntent, null, 'Successful readback must clear intent');
  await success.save(true);
  const afterSuccess = success.state();
  assert.notEqual(afterSuccess.posted[0].key, afterSuccess.posted[1].key);
  assert.equal(JSON.parse(afterSuccess.posted[1].raw).revision, 2);
  passed.push('successful_readback_clears_intent');

  const edited = harness('export');
  await edited.save(true);
  const newInput = '{ "pages": [] }';
  edited.edit(newInput);
  assert.equal(edited.state().writeIntent, null, 'Shipped edit handler must discard old intent');
  await edited.save(true);
  const afterEdit = edited.state();
  assert.notEqual(afterEdit.posted[0].key, afterEdit.posted[1].key);
  assert.equal(afterEdit.posted[1].raw, '{"revision":2,"payload":' + newInput + '}');
  passed.push('edit_starts_new_intent');

  process.stdout.write(JSON.stringify({
    status: 'passed',
    checks: passed.length,
    cases: passed,
    scope: 'Shipped save/edit handlers in isolated VM; simulated reads; no browser, HTTP, database or model execution.'
  }, null, 2) + '\n');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});

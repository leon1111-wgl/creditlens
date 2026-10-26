'use strict';

// Synthetic initial-load failures against the actual pages and write controllers.
// The reusable VM harness renders registered fixtures; this does not test a real
// browser, network outage, database, or financial result.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const htmlRoot = process.argv[2] ? path.resolve(process.argv[2]) : root;
const controllerRoot = process.argv[3] ? path.resolve(process.argv[3]) : htmlRoot;
const harnessPath = path.join(root, 'scripts/check_research_reload_ui.js');
const harnessSource = fs.readFileSync(harnessPath, 'utf8');
const marker = 'async function main(){';
assert.equal(harnessSource.split(marker).length, 2, 'Reusable harness boundary must be unique');
const support = vm.runInNewContext(harnessSource.split(marker)[0] +
  '\n({harness,makeStorage,clone,tick,jsonReply,sameWrite});', {
    require, __dirname: path.dirname(harnessPath),
    process: {argv: ['node', harnessPath, htmlRoot, controllerRoot]},
    URL, URLSearchParams, Blob, TextEncoder, structuredClone,
  });
const {harness, makeStorage, clone, tick, jsonReply, sameWrite} = support;
const results = [];
const otherId = 'b'.repeat(32);
const storageBytes = storage => JSON.stringify([...storage.entries.entries()]);
const writeRequests = app => app.requests.filter(r => !['GET', 'HEAD'].includes(r.options.method || 'GET'));
const badJson = () => ({ok: true, status: 200, text: async () => '{synthetic invalid JSON'});

function state(app) {
  const s = app.state();
  return {pending: s.pending, journal_issue: s.journalIssue,
    recovery_visible: !s.recoveryHidden, retry_enabled: !s.retryDisabled,
    read_required: s.recoveryReadRequired, create_enabled: !app.nodes.create.disabled,
    target_revision: s.revision};
}

// Requests are classified by their identity, never by arrival order. A pending
// restore is allowed to skip an irrelevant list entirely.
function route(app, request, policy, trace) {
  const method = request.options.method || 'GET';
  if (method !== 'GET') {
    trace.writes++;
    if (policy.retry) {
      sameWrite(policy.retry, request);
      trace.exact_retries++;
      return jsonReply(app.show(2)); // An old receipt, followed by a fresh GET.
    }
    trace.unexpected_writes++;
    return jsonReply({detail: 'Synthetic write was not authorized by this control'}, 409);
  }
  const url = request.url;
  let category, value;
  if (url === '/api/session') {
    category = 'session';
    value = {mode: 'api_key', role: 'analyst', user_id: 'synthetic-author',
      tenant_id: 'synthetic-tenant', ...(policy.actor || {})};
  } else if (url === '/api/research/tasks') {
    category = 'task_list';
    value = app.kind === 'explanation' ? [app.parentFixture]
      : [app.show(4), {...app.show(4), id: otherId, question: 'Synthetic other task'}];
  } else if (url === app.prefix + app.base.id) {
    category = 'target'; value = app.show(policy.revision || 4);
  } else if (url === '/api/research/tasks/' + app.parentFixture.id) {
    category = 'parent'; value = app.parentFixture;
  } else if (url === '/api/research/tasks/' + app.parentFixture.id + '/explanation-reviews') {
    category = 'review_list';
    value = [{id: app.base.id, revision: 4, status: 'draft'},
      {id: otherId, revision: 4, status: 'draft'}];
  } else if (url === app.prefix + otherId) {
    category = 'other_target'; value = {...app.show(4), id: otherId};
  } else {
    throw new Error('Unexpected GET outside the synthetic route registry');
  }
  trace.reads[category] = (trace.reads[category] || 0) + 1;
  const fault = category === 'target' ? policy.targetFault
    : category === 'task_list' ? policy.listFault
      : category === 'review_list' ? policy.reviewListFault : null;
  if (fault) {
    trace.faults[category + ':' + fault] = (trace.faults[category + ':' + fault] || 0) + 1;
    return fault === 'invalid_json' ? badJson()
      : jsonReply({detail: 'Synthetic unavailable read'}, 503);
  }
  return jsonReply(value);
}

async function drive(app, operation, from, policy = {}) {
  const trace = {reads: {}, faults: {}, writes: 0, unexpected_writes: 0, exact_retries: 0};
  const settled = new Set(app.requests.slice(0, from));
  let done = false, failure = null;
  Promise.resolve(operation).then(() => {done = true;}, error => {failure = error; done = true;});
  for (let round = 0; round < 100; round++) {
    await tick();
    for (const request of app.requests.filter(item => !settled.has(item))) {
      settled.add(request);
      request.resolve(route(app, request, policy, trace));
      await tick();
    }
    if (done && app.requests.every(item => settled.has(item))) {
      if (failure) throw failure;
      return trace;
    }
  }
  throw new Error('Synthetic operation did not settle');
}

async function execute(app, code, policy = {}) {
  const from = app.requests.length;
  return drive(app, app.run(code), from, policy);
}
async function userClick(app, id, policy = {}) {
  if (app.nodes[id].disabled) return {skipped_disabled: true, writes: 0};
  const from = app.requests.length;
  return drive(app, app.nodes[id].click(), from, policy);
}
async function connect(app, policy = {}, clear = true) {
  if (clear) app.run('clear()');
  return execute(app, 'run(connect)', policy);
}
async function seed(kind) {
  const storage = makeStorage(), app = harness(kind, storage);
  await connect(app);
  app.run((kind === 'research' ? 'load' : 'loadReview') + '(' + JSON.stringify(app.base) + ');');
  app.edit();
  const before = app.requests.length, operation = app.save();
  await tick();
  assert.equal(app.requests.length, before + 1, 'Seed must issue one explicit save');
  const first = app.requests.at(-1);
  assert.equal(first.options.method, 'PATCH');
  assert.equal(storage.entries.size, 1, 'Request must be journalled before sending');
  first.reject(new Error('Synthetic discarded save response'));
  await operation;
  assert(app.state().pending);
  return {storage, first};
}
function assertNoBodyEcho(app) {
  const visible = JSON.stringify(app.state()) + app.nodes.recoveryMeta.textContent;
  assert(!visible.includes('SYNTHETIC_UNSAVED_CANARY'), 'Stored request body must not populate visible fields');
  assert(!visible.includes('SYNTHETIC_PRIVATE_CANARY'), 'Corrupt journal body must not be echoed');
}
function assertPendingGate(app, issue = null) {
  assert(app.state().pending, 'Pending journal must be restored despite list failure');
  assert(!app.state().recoveryHidden, 'Recovery entry must remain visible');
  assert(app.nodes.create.disabled, 'Create must remain gated by pending recovery');
  if (issue) assert.equal(app.state().journalIssue, issue);
  if (app.kind === 'explanation') {
    assert(app.nodes.parentSelect.disabled, 'Parent switching must be gated');
    assert(app.nodes.reviewSelect.disabled, 'Review switching must be gated');
  } else {
    assert(app.run("[...document.querySelectorAll('.queue button')].every(b=>b.disabled)"),
      'Task queue switching must be gated');
  }
  assertNoBodyEcho(app);
}
async function retryAndCheck(app, first, evidence) {
  const from = writeRequests(app).length;
  const trace = await userClick(app, 'retryWrite', {retry: first, revision: 5});
  evidence.retry = trace;
  assert.equal(trace.exact_retries, 1, 'Explicit retry must preserve original body, URL, method and key');
  assert.equal(writeRequests(app).length, from + 1);
  assert.equal(app.state().revision, 5, 'Fresh GET must supersede old revision 2 receipt');
  assert(!app.state().pending);
  assert.equal(app.storage.entries.size, 0, 'Confirmed retry must clear only the pending journal');
}
async function scenario(kind, id, operation) {
  const evidence = {};
  try {
    await operation(evidence);
    results.push({id: kind + ':' + id, passed: true, evidence});
  } catch (error) {
    results.push({id: kind + ':' + id, passed: false,
      failure: error instanceof assert.AssertionError ? error.message.split('\n')[0]
        : 'Synthetic harness operation failed', evidence});
  }
}

async function main() {
  for (const kind of ['research', 'explanation']) {
    for (const fault of ['503', 'invalid_json']) {
      await scenario(kind, 'pending_list_' + fault, async evidence => {
        const {storage, first} = await seed(kind), original = storageBytes(storage);
        const app = harness(kind, storage);
        evidence.initial_reads = await connect(app, {listFault: fault});
        evidence.initial = state(app);
        // Browser-style disabled controls do not dispatch user input. An old
        // page that leaves Create enabled will actually issue a rejected POST.
        evidence.create_attempt = await userClick(app, 'create');
        evidence.automatic_or_unguarded_writes = writeRequests(app).length;
        assert.equal(storageBytes(storage), original, 'Initial failure must preserve original journal bytes');
        assertPendingGate(app);
        assert.equal(writeRequests(app).length, 0, 'List failure must not permit creating a different task');
        assert(!app.state().recoveryReadRequired, 'Successful target read must not depend on auxiliary list');
        assert(!app.state().retryDisabled);
        evidence.reconnect = await connect(app, {}, false);
        assertPendingGate(app);
        assert.equal(storageBytes(storage), original);
        assert.equal(writeRequests(app).length, 0, 'Reconnect must remain read only');
        await retryAndCheck(app, first, evidence);
      });
      await scenario(kind, 'target_' + fault, async evidence => {
        const {storage, first} = await seed(kind), original = storageBytes(storage);
        const app = harness(kind, storage);
        evidence.initial_reads = await connect(app, {targetFault: fault});
        evidence.initial = state(app);
        assertPendingGate(app);
        assert(app.state().recoveryReadRequired, 'Target failure must prevent replay until reread');
        assert(app.state().retryDisabled);
        await userClick(app, 'retryWrite');
        assert.equal(writeRequests(app).length, 0, 'Failed target read must not send pending write');
        assert.equal(storageBytes(storage), original);
        evidence.recovery = await userClick(app, 'refreshRecovery');
        assert(!app.state().recoveryReadRequired);
        assert.equal(storageBytes(storage), original);
        await retryAndCheck(app, first, evidence);
      });
      await scenario(kind, 'empty_list_' + fault, async evidence => {
        const storage = makeStorage(), app = harness(kind, storage);
        evidence.initial_reads = await connect(app, {listFault: fault});
        evidence.initial = state(app);
        assert(!app.state().pending, 'Empty storage must not invent a pending request');
        assert(app.state().recoveryHidden);
        assert.equal(storage.entries.size, 0);
        assert.equal(writeRequests(app).length, 0);
        evidence.reconnect = await connect(app, {}, false);
        assert(!app.nodes.create.disabled, 'Successful reconnect with no pending write restores ordinary create');
        assert.equal(writeRequests(app).length, 0, 'An empty journal must not auto-create');
      });
    }

    for (const variant of ['corrupt', 'different_user', 'different_role', 'different_tenant', 'unreadable']) {
      await scenario(kind, variant + '_and_list_failure', async evidence => {
        const {storage} = await seed(kind);
        if (variant === 'corrupt') storage.entries.set([...storage.entries.keys()][0], '{SYNTHETIC_PRIVATE_CANARY');
        if (variant === 'unreadable') storage.failGet = true;
        const original = storageBytes(storage), app = harness(kind, storage);
        const actor = variant === 'different_user' ? {user_id: 'synthetic-other'}
          : variant === 'different_role' ? {role: 'reviewer'}
            : variant === 'different_tenant' ? {tenant_id: 'synthetic-other-tenant'} : {};
        evidence.initial_reads = await connect(app, {listFault: '503', actor});
        evidence.initial = state(app);
        const issue = variant === 'corrupt' ? 'invalid' : variant === 'unreadable' ? 'unavailable' : 'identity_mismatch';
        assertPendingGate(app, issue);
        assert(app.state().retryDisabled);
        await userClick(app, 'create'); await userClick(app, 'retryWrite');
        assert.equal(writeRequests(app).length, 0);
        assert.equal(storageBytes(storage), original, 'Invalid or foreign journal must not be overwritten');
        assert.equal(app.nodes.recoveryMeta.textContent, '', 'Foreign or unreadable journal metadata stays hidden');
      });
    }

    await scenario(kind, 'ordinary_refresh_after_failed_initial_list', async evidence => {
      const {storage} = await seed(kind), original = storageBytes(storage), app = harness(kind, storage);
      evidence.initial_reads = await connect(app, {listFault: '503'});
      evidence.refresh = await userClick(app, 'refresh');
      evidence.after_refresh = state(app);
      evidence.create_attempt = await userClick(app, 'create');
      evidence.unguarded_writes = writeRequests(app).length;
      assertPendingGate(app);
      assert.equal(writeRequests(app).length, 0, 'Ordinary refresh cannot bypass pending recovery');
      assert.equal(storageBytes(storage), original);
    });
    await scenario(kind, 'switching_and_direct_create_guard', async evidence => {
      const {storage} = await seed(kind), original = storageBytes(storage), app = harness(kind, storage);
      await connect(app);
      assertPendingGate(app);
      const before = app.requests.length;
      if (kind === 'research') {
        await app.run("Promise.all([...document.querySelectorAll('.queue button')].filter(b=>!b.disabled).map(b=>b.click()))");
      } else {
        for (const id of ['parentSelect', 'reviewSelect']) {
          if (!app.nodes[id].disabled) {
            app.nodes[id].value = otherId;
            await drive(app, Promise.all((app.nodes[id].listeners.change || []).map(fn => fn())), before);
          }
        }
      }
      assert.equal(app.requests.length, before, 'Pending recovery must gate task switching');
      // Element.click deliberately bypasses disabled state in this harness, so
      // this separately checks the create handler's own recovery precondition.
      evidence.direct_create = await drive(app, app.nodes.create.click(), before);
      assert.equal(writeRequests(app).length, 0, 'Create handler itself must refuse while recovery is pending');
      assert.equal(storageBytes(storage), original);
    });

    if (kind === 'explanation') for (const fault of ['503', 'invalid_json']) {
      await scenario(kind, 'auxiliary_review_list_' + fault, async evidence => {
        const {storage, first} = await seed(kind), original = storageBytes(storage), app = harness(kind, storage);
        evidence.initial_reads = await connect(app, {reviewListFault: fault});
        evidence.initial = state(app);
        assertPendingGate(app);
        assert(!app.state().recoveryReadRequired, 'Child list failure is not a failed target or parent read');
        assert(!app.state().retryDisabled);
        assert.equal(storageBytes(storage), original);
        assert.equal(writeRequests(app).length, 0);
        await retryAndCheck(app, first, evidence);
      });
    }
  }
  const hash = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
  const report = {schema_version: 1, suite: 'research-initial-recovery-ui',
    provenance: 'synthetic_vm_fault_controls',
    scope: {actual_page_and_controller_sources: true, fresh_vm_shared_session_storage: true,
      synthetic_dom: true, startup_invoked_explicitly: true,
      request_routing: 'url_and_method_not_arrival_order',
      real_browser: false, real_network_fault: false, database_written: false,
      financial_quality_evaluated: false, automatic_approval: false},
    inputs: {hash_basis: 'raw_file_sha256',
      harness_sha256: hash(harnessPath),
      script_sha256: hash(__filename),
      research_html_sha256: hash(path.join(htmlRoot, 'creditlens/tasks.html')),
      explanation_html_sha256: hash(path.join(htmlRoot, 'creditlens/explanation-reviews.html')),
      controller_sha256: hash(path.join(controllerRoot, 'creditlens/research-write-intent.js')),
      journal_sha256: hash(path.join(controllerRoot, 'creditlens/research-write-journal.js'))},
    summary: {controls: results.length, passed: results.filter(r => r.passed).length,
      failed: results.filter(r => !r.passed).length}, results};
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.summary.failed) process.exitCode = 1;
}
main().catch(() => { process.stderr.write('Initial recovery UI control setup failed.\n'); process.exitCode = 2; });

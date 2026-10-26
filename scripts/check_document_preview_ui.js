'use strict';

// Execute the actual document page against a synthetic DOM/fetch/Blob boundary.
// Registered report data is a rendering fixture, not a financial evaluation.
// Aborts are recorded but deliberately do not cancel mock responses: late
// response/body controls must be safe even when cancellation is ineffective.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = process.argv[2] ? path.resolve(process.argv[2]) : path.resolve(__dirname, '..');
const htmlPath = path.join(root, 'creditlens/documents.html');
const fixturePath = path.join(root, 'artifacts/document-ai-report.json');
const html = fs.readFileSync(htmlPath, 'utf8');
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const clone = value => JSON.parse(JSON.stringify(value));
const results = [];
const tick = async () => {for (let i = 0; i < 16; i++) await Promise.resolve();};
const parsed = request => new URL(request.url, 'http://synthetic.invalid');
const isImage = request => /\/pages\/\d+\.png$/.test(parsed(request).pathname);
const jsonReply = (value, status = 200) => ({ok: status >= 200 && status < 300,
  status, json: async () => clone(value)});

function harness() {
  const start = html.indexOf('<script>'), end = html.indexOf('</script>', start);
  assert(start >= 0 && end > start, 'Document inline script must exist');
  const nodes = {}, requests = [], urls = [], revoked = [], downloads = [];
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {};
      this.attributes = {}; this.style = {}; this.hidden = false; this.disabled = false;
      this.className = ''; this._text = ''; this._value = ''; this.parent = null;
    }
    set textContent(value) {this._text = String(value); this.children = [];}
    get textContent() {return this._text + this.children.map(item => item.textContent || '').join('');}
    set innerHTML(_) {throw new Error('Rendering must not insert HTML from response data');}
    get value() {
      if (this.tagName !== 'SELECT' || this._value) return this._value;
      return this.children.find(item => item.tagName === 'OPTION')?.value || '';
    }
    set value(value) {this._value = String(value);}
    set href(value) {this.attributes.href = String(value);}
    get href() {return this.attributes.href || '';}
    append(...items) {for (const item of items) {
      if (typeof item === 'string') {this._text += item; continue;}
      item.parent = this; this.children.push(item);
    }}
    replaceChildren(...items) {this._text = ''; this.children = [];
      if (this.tagName === 'SELECT') this._value = ''; this.append(...items);}
    setAttribute(key, value) {this.attributes[key] = String(value);}
    removeAttribute(key) {delete this.attributes[key];}
    addEventListener(event, callback) {(this.listeners[event] ??= []).push(callback);}
    remove() {if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this);}
    async dispatch(event) {if (this.disabled) return;
      if (event === 'click' && this.tagName === 'A') downloads.push({href: this.href, name: this.download});
      await Promise.all((this.listeners[event] || []).map(callback => callback()));}
    click() {return this.dispatch('click');}
  }
  const head = html.slice(0, start);
  for (const match of head.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*)>/gi)) {
    const attrs = match[2], id = /\bid="([^"]+)"/.exec(attrs);
    if (!id) continue;
    const node = new Element(match[1]); node.id = id[1]; nodes[node.id] = node;
    node.hidden = /\bhidden(?:\s|=|$)/.test(attrs);
    node.disabled = /\bdisabled(?:\s|=|$)/.test(attrs);
  }
  for (const match of head.matchAll(/<select\b[^>]*\bid="([^"]+)"[^>]*>([\s\S]*?)<\/select>/gi)) {
    for (const option of match[2].matchAll(/<option\b[^>]*value="([^"]+)"[^>]*>([^<]*)<\/option>/gi)) {
      const item = new Element('option'); item.value = option[1]; item.textContent = option[2];
      nodes[match[1]].append(item);
    }
  }
  const body = new Element('body');
  const document = {getElementById: id => {assert(nodes[id], 'Unknown DOM id'); return nodes[id];},
    createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), body};
  class LocalURL extends URL {
    static createObjectURL(blob) {const url = 'blob:synthetic-document-' + urls.length;
      urls.push({url, blob}); return url;}
    static revokeObjectURL(url) {revoked.push(url);}
  }
  const context = vm.createContext({document, Node: Element, URL: LocalURL, URLSearchParams,
    Blob, AbortController, setTimeout: callback => callback(),
    fetch: (url, options = {}) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject}))});
  vm.runInContext(html.slice(start + 8, end), context, {timeout: 1000});
  return {nodes, requests, urls, revoked, downloads,
    imageRequests: () => requests.filter(isImage),
    svg: () => nodes.viewer.children.find(item => item.tagName === 'SVG'),
    imageHref: () => nodes.viewer.children.find(item => item.tagName === 'SVG')?.children
      .find(item => item.tagName === 'IMAGE')?.attributes.href || null};
}

async function boot() {
  const app = harness();
  assert.equal(app.requests.length, 1);
  assert.equal(parsed(app.requests[0]).pathname, '/health');
  app.requests[0].resolve(jsonReply({auth_mode: 'api_key'}));
  await tick();
  assert(!app.nodes.load.disabled);
  app.nodes.key.value = 'synthetic-preview-key';
  await app.nodes.key.dispatch('input');
  assert.equal(app.imageRequests().length, 0);
  return app;
}
async function load(app, report = fixture, documentId = null) {
  if (documentId) app.nodes.document.value = documentId;
  const before = app.requests.length, operation = app.nodes.load.click();
  await tick();
  const added = app.requests.slice(before);
  assert.equal(added.length, 1, 'Explicit load starts one report GET');
  assert.equal(parsed(added[0]).pathname, '/api/documents');
  added[0].resolve(jsonReply(report)); await operation; await tick();
  return app.requests.slice(before).find(isImage) || null;
}
async function select(app, id, value) {
  const before = app.requests.length;
  app.nodes[id].value = value; await app.nodes[id].dispatch('change'); await tick();
  return app.requests.slice(before).find(isImage) || null;
}
async function retryImage(app) {
  assert(app.nodes.retryImage, 'Explicit image retry control must exist');
  const before = app.requests.length;
  await app.nodes.retryImage.click(); await tick();
  const added = app.requests.slice(before);
  assert.equal(added.length, 1, 'Explicit image retry starts exactly one request');
  assert(isImage(added[0]), 'Image retry must not reload reports or start model work');
  return added[0];
}
async function imageReply(request, label = 'synthetic image', status = 200) {
  assert(request, 'Expected a page image request');
  request.resolve({ok: status >= 200 && status < 300, status,
    blob: async () => new Blob([label], {type: 'image/png'})});
  await tick();
}
async function deferredBody(request) {
  let release;
  request.resolve({ok: true, status: 200, blob: () => new Promise(resolve => {release = resolve;})});
  await tick(); assert.equal(typeof release, 'function', 'Image body must be awaiting bytes');
  return () => release(new Blob(['synthetic late image'], {type: 'image/png'}));
}
function assertBinding(request, doc, fact) {
  assert(request, 'Valid current page needs an image request');
  const url = parsed(request), page = doc.pages.find(item => item.page === fact.page);
  assert.equal(url.pathname, '/api/documents/' + doc.id + '/pages/' + fact.page + '.png');
  assert.equal(url.searchParams.get('expected_sha256'), page.image_sha256,
    'Page request must bind the current report image SHA');
  assert.equal(url.searchParams.getAll('expected_sha256').length, 1);
  assert.deepEqual([...url.searchParams.keys()], ['expected_sha256']);
  assert(!request.url.includes('synthetic-preview-key'), 'Credentials cannot enter image URL');
  assert.equal(request.options.headers.Authorization, 'Bearer synthetic-preview-key');
  assert.equal(request.options.cache, 'no-store');
}
function assertTextRetained(app, doc, fact) {
  assert.equal(app.nodes.workspace.hidden, false);
  assert(app.nodes['fact-detail'].textContent.includes(fact.raw_value));
  assert(app.nodes['source-link'].href.startsWith(doc.source.url));
  assert(app.nodes['source-link'].href.endsWith('#page=' + fact.page));
  assert(!app.nodes.export.disabled);
}
function assertReadOnly(app) {
  assert(app.requests.every(request => (request.options.method || 'GET') === 'GET'),
    'Preview never starts an OCR, repair, or write request');
}
async function control(id, operation) {
  try {await operation(); results.push({id, passed: true});}
  catch (error) {results.push({id, passed: false,
    failure: error instanceof assert.AssertionError ? error.message.split('\n')[0]
      : 'Synthetic page control failed'});}
}

async function main() {
  for (const doc of fixture.documents) for (const page of doc.pages) {
    await control('registered_page_binding:' + doc.id + ':' + page.page, async () => {
      const app = await boot(), fact = doc.facts.find(item => item.page === page.page);
      let request = await load(app, fixture, doc.id);
      if (doc.facts[0].fact_id !== fact.fact_id) request = await select(app, 'fact', fact.fact_id);
      assertBinding(request, doc, fact); await imageReply(request);
      assert(app.imageHref()); assertTextRetained(app, doc, fact); assertReadOnly(app);
    });
  }
  const doc = fixture.documents[0], firstFact = doc.facts[0];
  const mutations = [
    ['missing', page => {delete page.image_sha256;}],
    ['null', page => {page.image_sha256 = null;}],
    ['empty', page => {page.image_sha256 = '';}],
    ['short', page => {page.image_sha256 = 'a'.repeat(63);}],
    ['nonhex', page => {page.image_sha256 = 'g'.repeat(64);}],
    ['uppercase', page => {page.image_sha256 = 'A'.repeat(64);}],
    ['number', page => {page.image_sha256 = 7;}],
    ['boolean', page => {page.image_sha256 = true;}],
  ];
  for (const [name, mutate] of mutations) await control('invalid_hash:' + name, async () => {
    const report = clone(fixture), page = report.documents[0].pages.find(item => item.page === firstFact.page);
    mutate(page);
    const app = await boot(); await load(app, report);
    assert.equal(app.imageRequests().length, 0, 'Missing or invalid image hash must fail before requesting bytes');
    assert.equal(app.imageHref(), null); assertTextRetained(app, doc, firstFact);
    assert(app.nodes.retryImage?.disabled, 'Invalid metadata cannot be retried with the same hash');
    assert(app.nodes.viewer.textContent, 'Unavailable preview must explain its condition'); assertReadOnly(app);
  });
  for (const code of [404, 409, 401, 422, 429, 500]) await control('image_error_and_explicit_retry:' + code, async () => {
    const app = await boot(), request = await load(app);
    await imageReply(request, 'synthetic error', code);
    assert.equal(app.imageHref(), null); assertTextRetained(app, doc, firstFact);
    const message = app.nodes.viewer.textContent;
    if (code === 404) assert(/本机|本地/.test(message) && /页图|图像/.test(message));
    if (code === 409) {
      assert(/重新载入|重新加载/.test(message), 'Hash conflict must request a fresh record load');
      assert(!/重新生成|重建|重新运行/.test(message), 'Hash conflict must not tell users to regenerate evidence');
    }
    if (code === 401) assert(/身份|密钥|无权/.test(message));
    assert.equal(app.imageRequests().length, 1, 'Image error must not automatically retry or regenerate');
    let retry;
    if ([401, 409, 422].includes(code)) {
      assert(app.nodes.retryImage?.disabled, 'Identity or version error requires loading records again');
      const before = app.requests.length; await app.nodes.retryImage.click();
      assert.equal(app.requests.length, before, 'Blocked retry cannot reuse an untrusted page binding');
      retry = await load(app);
    } else {
      assert(app.nodes.retryImage && !app.nodes.retryImage.disabled, 'Recoverable read failure permits explicit retry');
      retry = await retryImage(app);
    }
    assertBinding(retry, doc, firstFact);
    await imageReply(retry, 'synthetic explicit retry'); assert(app.imageHref()); assertReadOnly(app);
  });
  for (const stage of ['response', 'body']) await control('transport_failure_and_retry:' + stage, async () => {
    const app = await boot(), request = await load(app);
    if (stage === 'response') request.reject(new Error('Synthetic fetch failed'));
    else request.resolve({ok: true, status: 200, blob: async () => {throw new Error('Synthetic image body failed');}});
    await tick(); assert.equal(app.imageHref(), null); assertTextRetained(app, doc, firstFact);
    assert.equal(app.imageRequests().length, 1, 'Transport failure must not trigger automatic retry');
    const retry = await retryImage(app); assertBinding(retry, doc, firstFact);
    await imageReply(retry); assert(app.imageHref()); assertReadOnly(app);
  });
  for (const code of [429, 503]) await control('error_body_not_consumed_or_echoed:' + code, async () => {
    const app = await boot(), request = await load(app); let consumed = 0;
    const body = async () => {consumed++; return 'SYNTHETIC_PRIVATE_RESPONSE_CANARY';};
    request.resolve({ok: false, status: code, text: body, json: body, blob: body}); await tick();
    assert.equal(consumed, 0, 'An image error response body is not needed for the fixed error message');
    assert(!app.nodes.viewer.textContent.includes('SYNTHETIC_PRIVATE_RESPONSE_CANARY'));
    assertTextRetained(app, doc, firstFact); assertReadOnly(app);
  });
  await control('retry_disabled_initially_and_while_loading', async () => {
    const app = await boot(); assert(app.nodes.retryImage?.disabled, 'No page binding means no retry');
    const request = await load(app); assert(app.nodes.retryImage.disabled, 'Loading page must disable retry');
    const before = app.requests.length; await app.nodes.retryImage.click();
    assert.equal(app.requests.length, before, 'Repeated clicks must not create concurrent image loads');
    await Promise.all((app.nodes.retryImage.listeners.click || []).map(callback => callback())); await tick();
    assert.equal(app.requests.length, before, 'Retry handler must enforce its own loading guard');
    await imageReply(request, 'synthetic missing', 404);
    const retry = await retryImage(app); assert(app.nodes.retryImage.disabled);
    await app.nodes.retryImage.click(); assert.equal(app.imageRequests().length, 2);
    await imageReply(retry); assert(app.imageHref()); assertReadOnly(app);
  });
  await control('late_response_does_not_consume_stale_image_body', async () => {
    const app = await boot(), request = await load(app); let consumed = 0;
    app.nodes.key.value = 'synthetic-other-identity'; await app.nodes.key.dispatch('input');
    request.resolve({ok: true, status: 200, blob: async () => {consumed++; return new Blob(['synthetic stale']);}});
    await tick(); assert.equal(consumed, 0, 'Stale response must be rejected before its body is read');
    assert.equal(app.urls.length, 0); assert.equal(app.imageHref(), null); assertReadOnly(app);
  });
  for (const stage of ['response', 'body']) for (const change of ['same_page_fact', 'other_page_fact', 'document', 'identity']) {
    await control('late_' + stage + ':' + change, async () => {
      const app = await boot(), old = await load(app);
      const release = stage === 'body' ? await deferredBody(old) : null;
      let current = null, currentFact = firstFact, currentDoc = doc;
      if (change === 'identity') {
        app.nodes.key.value = 'synthetic-other-identity'; await app.nodes.key.dispatch('input');
      } else if (change === 'document') {
        currentDoc = fixture.documents[1]; currentFact = currentDoc.facts[0];
        current = await select(app, 'document', currentDoc.id);
      } else {
        currentFact = doc.facts.find(item => item.fact_id !== firstFact.fact_id &&
          (change === 'same_page_fact' ? item.page === firstFact.page : item.page !== firstFact.page));
        current = await select(app, 'fact', currentFact.fact_id);
      }
      assert(old.options.signal.aborted, 'Selection or identity change should cancel the previous request');
      if (current) await imageReply(current, 'synthetic current image');
      const href = app.imageHref(), count = app.urls.length;
      if (release) {release(); await tick();} else await imageReply(old, 'synthetic stale response');
      assert.equal(app.urls.length, count, 'Stale image bytes must not become a new object URL');
      assert.equal(app.imageHref(), href, 'Late image must not replace current content');
      if (change === 'identity') {
        assert.equal(href, null); assert(app.nodes.workspace.hidden); assert(app.nodes.export.disabled);
      } else assertTextRetained(app, currentDoc, currentFact);
      assertReadOnly(app);
    });
  }
  for (const change of ['fact', 'identity']) await control('late_error:' + change, async () => {
    const app = await boot(), old = await load(app);
    if (change === 'fact') {
      const next = await select(app, 'fact', doc.facts[1].fact_id); await imageReply(next);
    } else {app.nodes.key.value = 'synthetic-other-identity'; await app.nodes.key.dispatch('input');}
    const before = app.nodes.viewer.textContent, href = app.imageHref();
    await imageReply(old, 'late synthetic conflict', 409);
    assert.equal(app.nodes.viewer.textContent, before); assert.equal(app.imageHref(), href); assertReadOnly(app);
  });
  await control('repeated_load_current_hash_and_url_reclamation', async () => {
    const app = await boot(), first = await load(app); await imageReply(first);
    const oldUrl = app.imageHref(), report = clone(fixture);
    report.documents[0].pages.find(item => item.page === firstFact.page).image_sha256 = 'c'.repeat(64);
    const second = await load(app, report);
    assert.deepEqual(app.revoked, [oldUrl], 'Reload must revoke the previous image URL once');
    assertBinding(second, report.documents[0], firstFact);
    await imageReply(second); const current = app.imageHref(); assert.notEqual(current, oldUrl);
    app.nodes.key.value = 'synthetic-other-identity'; await app.nodes.key.dispatch('input');
    assert.deepEqual(app.revoked, [oldUrl, current]);
    await app.nodes.key.dispatch('input'); assert.deepEqual(app.revoked, [oldUrl, current]);
    assert.equal(app.imageHref(), null); assertReadOnly(app);
  });
  await control('bad_hash_after_success_reclaims_prior_image', async () => {
    const app = await boot(), first = await load(app); await imageReply(first); const oldUrl = app.imageHref();
    const report = clone(fixture); delete report.documents[0].pages[0].image_sha256;
    await load(app, report);
    assert.equal(app.imageRequests().length, 1, 'Invalid replacement metadata must not start another image request');
    assert.deepEqual(app.revoked, [oldUrl]); assert.equal(app.imageHref(), null);
    assertTextRetained(app, doc, firstFact); assertReadOnly(app);
  });
  await control('zoom_reuses_current_url_without_fetch', async () => {
    const app = await boot(), request = await load(app); await imageReply(request);
    const before = app.requests.length, href = app.imageHref(); await select(app, 'zoom', 'page');
    assert.equal(app.requests.length, before); assert.equal(app.imageHref(), href);
    assert.equal(app.revoked.length, 0); assertReadOnly(app);
  });
  await control('image_decode_error_clears_overlay_and_reclaims_url', async () => {
    const app = await boot(), request = await load(app); await imageReply(request);
    const href = app.imageHref(), image = app.svg().children.find(item => item.tagName === 'IMAGE');
    await image.dispatch('error'); await tick();
    assert.equal(app.imageHref(), null, 'Failed image decoding must not leave a claimed page image');
    assert.equal(app.svg(), undefined, 'Overlay boxes must be removed when the underlying image is unavailable');
    assert.deepEqual(app.revoked, [href], 'Decode failure must revoke its object URL');
    assertTextRetained(app, doc, firstFact);
    await select(app, 'zoom', 'page'); assert.equal(app.svg(), undefined, 'Zoom cannot repaint a failed image');
    assert.equal(app.imageRequests().length, 1, 'Decode failure must not start automatic regeneration');
    const retry = await retryImage(app); assertBinding(retry, doc, firstFact);
    await imageReply(retry); assert(app.imageHref()); assertReadOnly(app);
  });
  await control('late_decode_error_does_not_clear_new_fact_image', async () => {
    const app = await boot(), first = await load(app); await imageReply(first);
    const oldImage = app.svg().children.find(item => item.tagName === 'IMAGE');
    const second = await select(app, 'fact', doc.facts[1].fact_id); await imageReply(second);
    const currentUrl = app.imageHref(), revoked = [...app.revoked];
    await oldImage.dispatch('error'); await tick();
    assert.equal(app.imageHref(), currentUrl, 'An old image decode error cannot clear the current fact');
    assert.deepEqual(app.revoked, revoked, 'An old image event cannot revoke a new image URL');
    assertTextRetained(app, doc, doc.facts[1]); assertReadOnly(app);
  });
  await control('failed_image_keeps_exact_report_export', async () => {
    const app = await boot(), request = await load(app); await imageReply(request, 'synthetic missing', 404);
    await app.nodes.export.click(); assert.equal(app.downloads.length, 1);
    const download = app.downloads[0], exported = app.urls.find(item => item.url === download.href);
    assert.deepEqual(JSON.parse(await exported.blob.text()), fixture);
    assert(app.revoked.includes(download.href)); assert.equal(app.imageHref(), null); assertReadOnly(app);
  });
  const hash = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
  const report = {schema_version: 1, suite: 'document-preview-ui',
    provenance: 'synthetic_ui_controls_with_registered_render_fixture',
    source_sha256: {script: hash(__filename), html: hash(htmlPath), report_fixture: hash(fixturePath)},
    hash_basis: 'raw_file_sha256',
    scope: {actual_inline_script: true, synthetic_dom_fetch_and_image_bytes: true,
      real_browser: false, backend_hash_validation_executed: false, network_requests: 0,
      ocr_calls: 0, model_accuracy_evaluated: false, financial_quality_evaluated: false},
    summary: {controls: results.length, passed: results.filter(item => item.passed).length,
      failed: results.filter(item => !item.passed).length}, results};
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  if (report.summary.failed) process.exitCode = 1;
}
main().catch(() => {process.stderr.write('Document preview control setup failed.\n'); process.exitCode = 2;});

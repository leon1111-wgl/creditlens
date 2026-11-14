'use strict';

// Check an exported static demo: every recorded route must resolve through the real shim
// exactly as pages request it, writes must be refused, and pages must not keep server paths.
// Runs the shim in a VM with a stub fetch; it does not render pages or contact a network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const site = path.resolve(process.argv[2] || 'site');
const manifest = JSON.parse(fs.readFileSync(path.join(site, 'data/manifest.json'), 'utf8'));
const shimSource = fs.readFileSync(path.join(site, 'assets/static-demo.js'), 'utf8');
const origin = 'https://leon1111-wgl.github.io';
const base = origin + '/creditlens/';

function fileResponse(url) {
  const relative = url.slice(base.length).split('?')[0];
  const file = path.join(site, relative);
  if (!file.startsWith(site) || !fs.existsSync(file)) return new Response('missing', {status: 404});
  return new Response(fs.readFileSync(file));
}
const listeners = {};
const context = {
  URL, Response, Headers, Request, JSON, Promise, Object, Array, String, Number, console,
  setTimeout, clearTimeout,
  location: {href: base + 'tasks.html', origin, pathname: '/creditlens/tasks.html'},
  document: {
    currentScript: {src: base + 'assets/static-demo.js'}, body: null, readyState: 'loading',
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    getElementById() { return null; }, querySelector() { return null; }, createElement() { return {}; },
  },
};
context.window = context;
context.fetch = async (input) => fileResponse(String(input));
vm.createContext(context);
vm.runInContext(shimSource, context);
const fetch = context.window.fetch;

function parse(key) {
  const role = key.match(/ \|role=(\w+)$/)?.[1] || null;
  const plain = role ? key.slice(0, key.lastIndexOf(' |role=')) : key;
  const [method, rest] = [plain.slice(0, plain.indexOf(' ')), plain.slice(plain.indexOf(' ') + 1)];
  const bodyAt = rest.indexOf(' body=');
  const target = bodyAt >= 0 ? rest.slice(0, bodyAt) : rest;
  const body = bodyAt >= 0 ? JSON.parse(rest.slice(bodyAt + 6)) : undefined;
  const [pathname, query = ''] = target.split('?');
  const params = query ? query.split('&').map(pair => [pair.slice(0, pair.indexOf('=')), pair.slice(pair.indexOf('=') + 1)]) : [];
  return {method, pathname, params, body, role};
}
function shuffledBody(value) {
  if (Array.isArray(value)) return value.map(shuffledBody);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).reverse().map(key => [key, shuffledBody(value[key])]));
  return value;
}

(async () => {
  let checked = 0;
  for (const [key, entry] of Object.entries(manifest.routes)) {
    const {method, pathname, params, body, role} = parse(key);
    // Pages encode each path segment and may order parameters differently.
    const encoded = pathname.split('/').map(encodeURIComponent).join('/');
    const query = new URLSearchParams(params.slice().reverse()).toString();
    const url = encoded + (query ? '?' + query : '');
    const init = {method, headers: {'X-Demo-Role': role || 'analyst'}};
    if (body !== undefined) init.body = JSON.stringify(shuffledBody(body));
    const response = await fetch(url, init);
    assert.equal(response.status, entry.status, key);
    assert.equal(response.headers.get('content-type'), entry.type, key);
    assert(fs.existsSync(path.join(site, entry.file)), entry.file);
    checked++;
  }
  for (const method of ['POST', 'PATCH', 'PUT', 'DELETE']) {
    const response = await fetch('/api/research/tasks', {method, body: '{}', headers: {'X-Demo-Role': 'analyst'}});
    assert.equal(response.status, 403, method + ' must be refused');
    assert.match((await response.json()).detail, /静态演示不保存写入/);
  }
  const missing = await fetch('/api/research/quarters?as_of=1999-01-01&dataset=msft-fy2025');
  assert.equal(missing.status, 404);
  for (const page of Object.values(manifest.pages)) {
    const html = fs.readFileSync(path.join(site, page), 'utf8');
    assert(!/\b(?:href|src|action)="\/(?!\/)/.test(html), page + ' still references a server path');
    for (const [, code] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(code, {filename: page});
    const firstScript = html.match(/<script[^>]*>/)[0];
    assert.equal(firstScript, '<script src="assets/static-demo.js">', page + ' must load the shim first');
  }
  assert(!fs.readdirSync(path.join(site, 'data')).some(name => /\.(png|jpe?g|webp|pdf)$/i.test(name)), 'No page images or PDFs may be published');
  // The only page images allowed: the saved XBRL grounding run's rendered SEC 10-K statement pages,
  // each listed in its index with a hash, an SEC filing URL and an accession number, at most 4 per issuer.
  const imageFiles = [];
  const walk = dir => { for (const entry of fs.readdirSync(dir, {withFileTypes: true})) { const p = path.join(dir, entry.name); if (entry.isDirectory()) walk(p); else if (/\.(png|jpe?g|webp|gif|pdf)$/i.test(entry.name)) imageFiles.push(path.relative(site, p)); } };
  walk(site);
  const groundingDir = path.join(site, 'assets/grounding');
  const listed = new Set();
  if (fs.existsSync(path.join(groundingDir, 'index.json'))) {
    const index = JSON.parse(fs.readFileSync(path.join(groundingDir, 'index.json'), 'utf8'));
    let bytes = 0;
    for (const issuer of index.issuers) {
      assert.match(issuer.filing.accession, /^\d{10}-\d{2}-\d{6}$/, issuer.id + ' accession');
      assert(issuer.filing.document_url.startsWith('https://www.sec.gov/Archives/edgar/data/'), issuer.id + ' filing URL');
      assert(issuer.pages.length >= 1 && issuer.pages.length <= 4, issuer.id + ' must publish 1 to 4 pages');
      for (const page of issuer.pages) {
        const raw = fs.readFileSync(path.join(groundingDir, page.image));
        assert.equal(require('node:crypto').createHash('sha256').update(raw).digest('hex'), page.sha256, page.image);
        bytes += raw.length;
        listed.add(path.join('assets/grounding', page.image));
      }
    }
    assert(bytes < 20 * 1024 * 1024, 'Published page images must stay well under 20 MB');
  }
  for (const file of imageFiles) assert(listed.has(file), 'Unlisted image or PDF in the site: ' + file);
  // 信用快照 gallery: every gzipped filing is listed in gallery.json with its hash, an SEC address and an accession.
  const snapshotDir = path.join(site, 'assets/snapshot');
  if (fs.existsSync(path.join(snapshotDir, 'gallery.json'))) {
    const gallery = JSON.parse(fs.readFileSync(path.join(snapshotDir, 'gallery.json'), 'utf8'));
    const listedFilings = new Set();
    let gzBytes = 0;
    for (const entry of gallery.entries) {
      assert.match(entry.accession, /^\d{10}-\d{2}-\d{6}$/, entry.id + ' accession');
      assert(entry.document_url.startsWith('https://www.sec.gov/Archives/edgar/data/'), entry.id + ' SEC address');
      assert(entry.archive_url.startsWith('https://web.archive.org/web/'), entry.id + ' archive address');
      const raw = fs.readFileSync(path.join(snapshotDir, entry.file));
      assert.equal(require('node:crypto').createHash('sha256').update(raw).digest('hex'), entry.gz_sha256, entry.file);
      assert.equal(require('node:crypto').createHash('sha256').update(require('node:zlib').gunzipSync(raw)).digest('hex'), entry.sha256, entry.file + ' content');
      assert(entry.snapshot && entry.snapshot.status, entry.id + ' has no computed snapshot');
      gzBytes += raw.length;
      listedFilings.add(entry.file);
    }
    for (const name of fs.readdirSync(path.join(snapshotDir, 'filings'))) assert(listedFilings.has('filings/' + name), 'Unlisted gallery file: ' + name);
    assert(gzBytes < 80 * 1024 * 1024, 'Gallery filings must stay under 80 MB compressed');
  }
  let siteBytes = 0;
  const size = dir => { for (const entry of fs.readdirSync(dir, {withFileTypes: true})) { const p = path.join(dir, entry.name); if (entry.isDirectory()) size(p); else siteBytes += fs.statSync(p).size; } };
  size(site);
  assert(siteBytes < 300 * 1024 * 1024, 'The Pages site must stay under 300 MB');
  console.log(JSON.stringify({status: 'ok', routes: checked, pages: Object.keys(manifest.pages).length}));
})().catch(error => { console.error(error); process.exit(1); });

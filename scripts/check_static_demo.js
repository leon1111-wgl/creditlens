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
    assert(!/\b(?:href|src)="\/(?!\/)/.test(html), page + ' still references a server path');
    for (const [, code] of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(code, {filename: page});
    const firstScript = html.match(/<script[^>]*>/)[0];
    assert.equal(firstScript, '<script src="assets/static-demo.js">', page + ' must load the shim first');
  }
  assert(!fs.readdirSync(path.join(site, 'data')).some(name => /\.(png|jpe?g|pdf)$/i.test(name)), 'No page images or PDFs may be published');
  console.log(JSON.stringify({status: 'ok', routes: checked, pages: Object.keys(manifest.pages).length}));
})().catch(error => { console.error(error); process.exit(1); });

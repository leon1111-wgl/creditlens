// Credit snapshot in a real browser, on GitHub Actions only (Playwright Chromium 1.49.1).
//   --mode compute  open each listed filing through the page's own file input, save what the page computed
//                   (snapshot + facts) and run the click-to-cell check (c) on every figure link
//   --mode check    the visitor flow at 1440 and 375 px, light and dark (drop, wrong types, bank filing,
//                   click-to-cell, exports, gallery, sandbox), then check (c) on every committed gallery filing
//   --mode probe    compute + exports + screenshots for filings a reviewer names
// The site is the static export (scripts/export_static_demo.py) served under /creditlens/ like GitHub Pages.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []));
const mode = args.mode, site = path.resolve(args.site || 'site'), out = path.resolve(args.out || 'snapshot-out');
mkdirSync(out, { recursive: true });
const summary = [];
const failures = [];
const note = (line) => { summary.push(line); console.log(line); };
const fail = (line) => { failures.push(line); console.log('FAIL ' + line); };

// ---------- static server (Pages layout) + fixtures ----------
const fixtures = new Map();
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.gz': 'application/gzip', '.svg': 'image/svg+xml', '.webp': 'image/webp' };
const server = createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname.startsWith('/__fixture/')) {
    const body = fixtures.get(decodeURIComponent(url.pathname.slice(11)));
    if (!body) { res.writeHead(404); return res.end(); }
    res.writeHead(200, { 'content-type': 'application/octet-stream' }); return res.end(body);
  }
  if (!url.pathname.startsWith('/creditlens/')) { res.writeHead(404); return res.end(); }
  let file = path.join(site, decodeURIComponent(url.pathname.slice('/creditlens/'.length)) || 'index.html');
  if (!file.startsWith(site) || !existsSync(file) || statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const BASE = `http://127.0.0.1:${server.address().port}/creditlens/`;
const PAGE = BASE + 'snapshot.html';

const browser = await chromium.launch();
async function newPage({ width = 1440, height = 900, scheme = 'light' } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: scheme, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  const offsite = [];
  page.on('request', r => { if (!r.url().startsWith(`http://127.0.0.1:${server.address().port}/`) && !r.url().startsWith('blob:') && !r.url().startsWith('data:') && r.url() !== 'about:srcdoc') offsite.push(r.url()); });
  page.on('console', m => { if (m.type() === 'error' && !/Blocked script execution in 'about:srcdoc'/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(PAGE);
  await until(page, () => !/正在载入/.test(document.getElementById('snGalleryNote')?.textContent || '正在载入'));
  return { page, context, errors, offsite };
}
// The page's CSP has no 'unsafe-eval', so Playwright's waitForFunction (string evaluation) is refused; poll with evaluate.
async function until(page, fn, timeout = 120_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await page.evaluate(fn)) return; await page.waitForTimeout(100); }
  throw new Error('timed out waiting for the page');
}
async function waitResult(page) {
  await until(page, () => { const s = document.getElementById('snStatus'); return !!s && !s.hidden && /\b(ok|error)\b/.test(s.className); });
  const cls = await page.getAttribute('#snStatus', 'class');
  if (/\bok\b/.test(cls)) await until(page, () => document.getElementById('snFrame').dataset.ready === '1');
  return { ok: /\bok\b/.test(cls), text: await page.innerText('#snStatus') };
}
// Clear the previous result first, so waitResult cannot read the last file's status or frame.
const resetResult = page => page.evaluate(() => { const s = document.getElementById('snStatus'); s.hidden = true; s.className = 'sn-status'; document.getElementById('snFrame').dataset.ready = '0'; });
async function chooseFile(page, name, buffer, mimeType = 'text/html') {
  await resetResult(page);
  await page.setInputFiles('#snFile', { name, mimeType, buffer });
  return waitResult(page);
}
async function dropFile(page, name) {
  await resetResult(page);
  await page.evaluate(async (name) => {
    const blob = await (await fetch('/__fixture/' + encodeURIComponent(name))).blob();
    const dt = new DataTransfer(); dt.items.add(new File([blob], name, { type: 'text/html' }));
    document.getElementById('snDrop').dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, name);
  return waitResult(page);
}

// ---------- check (c): click every figure link ----------
function canonical(text, format, scale, sign) {
  // Independent of the page code: digits by ixt format, times 10^scale, negated for sign="-".
  const t = text.replace(/[\s ]+/g, '');
  let digits;
  if (/^(fixed-zero|zerodash|fixed-empty|numdash)$/.test(format)) digits = '0';
  else if (/^(num-comma-decimal|numcommadecimal)$/.test(format)) digits = t.replace(/\./g, '').replace(/,/g, '.');
  else digits = t.replace(/,/g, '');
  if (!/^\d+(\.\d*)?$|^\.\d+$/.test(digits)) return null;
  const [i, f = ''] = digits.split('.');
  let int = BigInt((i || '0') + f), exp = -f.length + Number(scale || 0);
  if (sign === '-') int = -int;
  while (exp < 0 && int % 10n === 0n) { int /= 10n; exp++; }
  if (int === 0n) return '0';
  return exp >= 0 ? (int * 10n ** BigInt(exp)).toString() : (() => { const neg = int < 0n; let s = (neg ? -int : int).toString().padStart(-exp + 1, '0'); return (neg ? '-' : '') + s.slice(0, s.length + exp) + '.' + s.slice(s.length + exp); })();
}
function displayed(text) {
  const m = /^(−|-)?([\d,]+(?:\.\d+)?)\s*(千|百万|十亿)?/.exec(text.trim());
  if (!m) return null;
  const scale = m[3] === '千' ? 3 : m[3] === '百万' ? 6 : m[3] === '十亿' ? 9 : 0;
  return canonical(m[2], 'num-dot-decimal', scale, m[1] ? '-' : '');
}
async function clickCheck(page, label) {
  const buttons = await page.$$eval('button.sn-fig[data-fact]', bs => bs.map((b, k) => ({ k, fact: b.dataset.fact, value: b.dataset.value, text: b.textContent }))).then(list => list.filter(b => b.value !== undefined));
  const hiddenSpans = await page.$$eval('span.sn-fig.is-hidden', s => s.length);
  let ok = 0;
  for (const b of buttons) {
    const r = await page.evaluate(async ({ k, fact }) => {
      const button = document.querySelectorAll('button.sn-fig[data-fact]')[k];
      button.click();
      await new Promise(res => requestAnimationFrame(() => requestAnimationFrame(res)));
      const doc = document.getElementById('snFrame').contentDocument;
      const hits = [...doc.querySelectorAll('.cl-hit')];
      if (hits.length !== 1) return { error: `marked ${hits.length} elements` };
      const hit = hits[0];
      const isAnchor = hit.getAttribute('data-clf') === fact || hit.getAttribute('data-clh') === fact;
      const source = hit.getAttribute('data-clf') === fact ? hit : doc.querySelector(`[data-clf="${fact}"]`);
      const rect = hit.getBoundingClientRect(), vh = doc.defaultView.innerHeight, vw = doc.defaultView.innerWidth;
      return { isAnchor, inView: rect.bottom > 0 && rect.top < vh && rect.right > 0 && rect.left < vw && rect.height > 0,
        text: hit.textContent, format: (source.getAttribute('format') || '').split(':').pop().toLowerCase(), scale: source.getAttribute('scale') || '0', sign: source.getAttribute('sign') || '' };
    }, b);
    if (r.error) { fail(`${label}: figure ${b.fact} ${r.error}`); continue; }
    const cell = canonical(r.text, r.format, r.scale, r.sign);
    const shown = displayed(b.text);
    if (!r.isAnchor) fail(`${label}: figure ${b.fact} marked a different element`);
    else if (!r.inView) fail(`${label}: figure ${b.fact} cell not in the frame viewport`);
    else if (cell === null || cell !== b.value || shown !== b.value) fail(`${label}: figure ${b.fact} cell ${JSON.stringify(r.text)}→${cell}, shown ${JSON.stringify(b.text)}→${shown}, value ${b.value}`);
    else ok++;
  }
  return { clicks: buttons.length, ok, hidden: hiddenSpans };
}

async function computeOne(page, entry, bytes) {
  const opened = await chooseFile(page, entry.document || entry.id + '.htm', bytes);
  if (!opened.ok) return { id: entry.id, error: opened.text };
  const data = await page.evaluate(() => window.__creditSnapshot());
  const facts = data.facts.map(f => ({ i: f.i, name: f.name, local: f.local, ns: f.ns, ctx: f.ctx, unit: f.unit, value: f.value, nil: f.nil, decimals: f.decimals, scale: f.scale, sign: f.sign, display: f.display, id: f.id }));
  return { id: entry.id, snapshot: data.snapshot, facts, contexts: data.contexts, units: data.units };
}

async function computeList(entries, dir, { clicks = true, exports = false } = {}) {
  const { page, errors } = await newPage();
  const results = [];
  mkdirSync(path.join(out, 'computed'), { recursive: true });
  for (const entry of entries) {
    const bytes = gunzipSync(readFileSync(path.join(dir, entry.file)));
    const result = await computeOne(page, entry, bytes);
    writeFileSync(path.join(out, 'computed', entry.id + '.json'), JSON.stringify(result));
    let c = null;
    if (!result.error && clicks) c = await clickCheck(page, entry.id);
    if (exports && !result.error) await saveExports(page, entry.id);
    results.push({ id: entry.id, name: entry.name, status: result.error ? 'error: ' + result.error : result.snapshot.status, template: result.snapshot?.template.kind, clicks: c });
    note(`${entry.id} ${entry.name}: ${result.error ? 'ERROR ' + result.error : result.snapshot.status + '/' + result.snapshot.template.kind}${c ? ` · clicks ${c.ok}/${c.clicks} (hidden ${c.hidden})` : ''}`);
  }
  if (errors.length) fail('console errors: ' + errors.slice(0, 5).join(' | '));
  return results;
}

// On a phone the filing view is a bottom sheet; a visitor closes it before using buttons it covers.
const closeSheet = page => page.evaluate(() => { if (document.querySelector('.sn-viewer')?.classList.contains('open')) document.getElementById('snSheet').click(); });
async function saveExports(page, label) {
  await closeSheet(page);
  const files = {};
  for (const [id, ext] of [['#snExportMd', 'md'], ['#snExportCsv', 'csv'], ['#snExportDocx', 'docx']]) {
    const [download] = await Promise.all([page.waitForEvent('download'), page.click(id)]);
    const target = path.join(out, 'exports', `${label}.${ext}`);
    mkdirSync(path.dirname(target), { recursive: true });
    await download.saveAs(target);
    files[ext] = readFileSync(target);
  }
  return files;
}

// ---------- modes ----------
if (mode === 'compute' || mode === 'probe') {
  const listing = JSON.parse(readFileSync(args.list, 'utf8'));
  // A partial probe must not read as clean: every filing the fetch step could not get is listed as a failure.
  for (const f of listing.failures || []) fail(`not fetched: ${f.cik ?? ''}:${f.accession ?? ''}:${f.document ?? ''} ${f.name ?? ''} — ${f.reason}`);
  const results = await computeList(listing.entries, args.dir, { clicks: true, exports: mode === 'probe' });
  if (mode === 'probe') {
    const { page } = await newPage();
    for (const entry of listing.entries) {
      const bytes = gunzipSync(readFileSync(path.join(args.dir, entry.file)));
      await chooseFile(page, entry.document, bytes);
      await page.screenshot({ path: path.join(out, `${entry.id}-1440.png`), fullPage: true });
    }
  }
  writeFileSync(path.join(out, 'results.json'), JSON.stringify(results, null, 1));
}

if (mode === 'check') {
  const gallery = JSON.parse(readFileSync(path.join(site, 'assets/snapshot/gallery.json'), 'utf8'));
  const general = gallery.entries.find(e => e.snapshot?.status === 'ok');
  const bank = gallery.entries.find(e => e.snapshot?.template === 'bank') || gallery.entries.find(e => e.snapshot?.status === 'unsupported');
  const gz = e => gunzipSync(readFileSync(path.join(site, 'assets/snapshot', e.file)));
  fixtures.set('general.htm', gz(general));
  const evil = readFileSync(path.resolve('tests/fixtures/snapshot-hostile.htm'));
  for (const [width, scheme] of [[1440, 'light'], [1440, 'dark'], [375, 'light'], [375, 'dark']]) {
    const label = `${width}-${scheme}`;
    const { page, context, errors, offsite } = await newPage({ width, height: width > 500 ? 900 : 812, scheme });
    const rows = await page.$$eval('#snGalleryTable tbody tr', t => t.length);
    if (rows !== gallery.entries.length) fail(`${label}: gallery shows ${rows} rows, index has ${gallery.entries.length}`);
    // wrong file types
    for (const [name, buffer, mime, expect] of [
      ['annual-report.pdf', Buffer.from('%PDF-1.7\n%âãÏÓ\n1 0 obj\n<<>>\nendobj\n'), 'application/pdf', /PDF/],
      ['chart.png', Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]), 'image/png', /二进制|文本/],
      ['page.htm', Buffer.from('<!doctype html><html><body><table><tr><td>1,234</td></tr></table></body></html>'), 'text/html', /Inline XBRL/],
      ['filing.zip', Buffer.from('PK\u0003\u0004rest'), 'application/zip', /压缩包/],
      ['empty.htm', Buffer.alloc(0), 'text/html', /空/],
    ]) {
      const r = await chooseFile(page, name, buffer, mime);
      if (r.ok || !expect.test(r.text)) fail(`${label}: ${name} → ${JSON.stringify(r.text)}`);
    }
    // a file over the limit is refused before it is read
    const big = await chooseFile(page, 'big.htm', Buffer.alloc(31 * 1024 * 1024, 32));
    if (big.ok || !/30 MB/.test(big.text)) fail(`${label}: oversize → ${big.text}`);
    // hostile iXBRL: scripts, handlers and links must not run or load
    const hostile = await chooseFile(page, 'hostile.htm', evil);
    if (!hostile.ok) fail(`${label}: hostile fixture did not open: ${hostile.text}`);
    const sandbox = await page.evaluate(() => {
      const d = document.getElementById('snFrame').contentDocument;
      return { pwned: window.__pwned || d.defaultView.__pwned || null, scripts: d.querySelectorAll('script,iframe,object,embed').length,
        handlers: [...d.querySelectorAll('*')].filter(e => [...e.attributes].some(a => /^on/i.test(a.name))).length,
        external: [...d.querySelectorAll('[href],[src]')].filter(e => !/^#|^data:image\/(png|jpe?g|gif)/i.test(e.getAttribute('href') || e.getAttribute('src') || '')).length,
        csp: d.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content'), sandbox: document.getElementById('snFrame').getAttribute('sandbox') };
    });
    await page.waitForTimeout(500);
    if (offsite.length) fail(`${label}: the page or the filing view made off-site requests: ${offsite.slice(0, 3).join(' ')}`);
    if (sandbox.pwned || sandbox.scripts || sandbox.handlers || sandbox.external || !/default-src 'none'/.test(sandbox.csp || '') || sandbox.sandbox !== 'allow-same-origin') fail(`${label}: sandbox ${JSON.stringify(sandbox)}`);
    // drop a real filing (the drag-and-drop path), with its EDGAR address typed in as a visitor would
    await page.fill('#snUrl', general.document_url);
    const dropped = await dropFile(page, 'general.htm');
    if (!dropped.ok) fail(`${label}: drop → ${dropped.text}`);
    const c = await clickCheck(page, `${label} drop`);
    if (c.ok !== c.clicks || !c.clicks) fail(`${label}: drop click check ${c.ok}/${c.clicks}`);
    await page.locator('button.sn-fig[data-fact]').first().dispatchEvent('click');
    const readout = await page.innerText('#snReadout');
    if (!/概念/.test(readout) || !/上下文/.test(readout)) fail(`${label}: readout ${readout.slice(0, 80)}`);
    await page.screenshot({ path: path.join(out, `flow-${label}-dropped.png`), fullPage: false });
    // exports
    const files = await saveExports(page, `flow-${label}`);
    const md = files.md.toString('utf8'), csv = files.csv.toString('utf8');
    if (!md.includes(general.accession) || !/`us-gaap:/.test(md) || !md.includes(general.document_url + '#')) fail(`${label}: memo lacks accession, concepts or cell links`);
    if (!csv.startsWith('﻿kind,key,label') || !csv.includes('input_term') || !csv.includes(general.accession)) fail(`${label}: csv header/rows`);
    if (files.docx.subarray(0, 2).toString() !== 'PK' || !files.docx.includes(Buffer.from('word/document.xml')) || !files.docx.includes(Buffer.from(general.accession))) fail(`${label}: docx package`);
    // bank filing from the gallery
    if (bank) {
      await resetResult(page);
      await closeSheet(page);
      await page.click(`#snGalleryTable tr[data-gallery="${bank.id}"] button.sn-open`);
      const r = await waitResult(page);
      const text = await page.innerText('#snCues');
      if (!r.ok || !/模板不支持/.test(text)) fail(`${label}: bank → ${r.text} / ${text.slice(0, 80)}`);
      if (!(await page.$('#snCues button.sn-fig'))) fail(`${label}: bank trigger facts are not clickable`);
      await page.screenshot({ path: path.join(out, `flow-${label}-bank.png`) });
    } else note(`${label}: no unsupported template in the gallery`);
    // open a gallery filing
    await resetResult(page);
    await closeSheet(page);
    await page.click(`#snGalleryTable tr[data-gallery="${general.id}"] button.sn-open`);
    const g = await waitResult(page);
    if (!g.ok || !page.url().includes('#g=')) fail(`${label}: gallery open → ${g.text} ${page.url()}`);
    // Changing the hash in place must load that gallery filing (0.36.1).
    const other = gallery.entries.find(e => e.snapshot?.status === 'ok' && e.id !== general.id);
    await resetResult(page);
    await page.evaluate(id => { location.hash = '#g=' + encodeURIComponent(id); }, other.id);
    const h = await waitResult(page);
    const shown = await page.innerText('#snCompany h2');
    if (!h.ok || shown.trim().toUpperCase() !== other.name.trim().toUpperCase()) fail(`${label}: hash change to ${other.id} shows ${JSON.stringify(shown)}`);
    const overflow = await page.evaluate(() => {
      const iw = document.documentElement.clientWidth;
      const leaves = [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > iw + 1 && ![...e.children].some(c => c.getBoundingClientRect().right > iw + 1));
      return { px: document.documentElement.scrollWidth - iw, culprits: leaves.slice(0, 4).map(e => `${e.tagName}.${e.className} right=${Math.round(e.getBoundingClientRect().right)} "${(e.textContent || '').slice(0, 40)}"`) };
    });
    if (overflow.px > 1) fail(`${label}: horizontal overflow ${overflow.px}px: ${overflow.culprits.join(' | ')}`);
    await page.screenshot({ path: path.join(out, `flow-${label}-gallery.png`), fullPage: true });
    if (errors.length) fail(`${label}: console errors ${errors.slice(0, 3).join(' | ')}`);
    note(`${label}: flow done (gallery rows ${rows}, drop clicks ${c.ok}/${c.clicks})`);
    await context.close();
  }
  // check (c) and gallery parity over every committed gallery filing
  const results = await computeList(gallery.entries, path.join(site, 'assets/snapshot'), { clicks: true });
  let clicks = 0, ok = 0, hidden = 0;
  for (const r of results) {
    if (r.clicks) { clicks += r.clicks.clicks; ok += r.clicks.ok; hidden += r.clicks.hidden; }
    const entry = gallery.entries.find(e => e.id === r.id);
    const live = JSON.parse(readFileSync(path.join(out, 'computed', r.id + '.json'), 'utf8'));
    if (live.error) { fail(`${r.id}: ${live.error}`); continue; }
    const m = live.snapshot.metrics || {};
    const now = { status: live.snapshot.status, template: live.snapshot.template.kind, debt_to_ebitda: m.debt_to_ebitda?.value ?? null, interest_coverage: m.interest_coverage?.value ?? null,
      current_ratio: m.current_ratio?.value ?? null, fcf_to_debt: m.fcf_to_debt?.value ?? null, debt: m.debt?.value ?? null };
    for (const [k, v] of Object.entries(now)) if (JSON.stringify(v) !== JSON.stringify(entry.snapshot[k])) fail(`${r.id}: gallery index ${k}=${JSON.stringify(entry.snapshot[k])}, page computes ${JSON.stringify(v)}`);
  }
  note(`check (c): ${ok}/${clicks} clicks landed on the right cell across ${results.length} gallery filings; ${hidden} figures without a visible cell (not links)`);
  writeFileSync(path.join(out, 'check-c.json'), JSON.stringify({ clicks, ok, hidden, filings: results, failures }, null, 1));
}

await browser.close();
server.close();
writeFileSync(path.join(out, 'summary.md'), `## Credit snapshot · ${mode}\n\n${summary.map(s => '- ' + s).join('\n')}\n\n${failures.length ? '### Failures\n\n' + failures.map(f => '- ' + f).join('\n') : 'No failures.'}\n`);
// compute/probe record click failures in their reports and summary; the pass/fail gate for check (c) is --mode check.
if (failures.length && mode === 'check') { console.error(`${failures.length} failures`); process.exit(1); }

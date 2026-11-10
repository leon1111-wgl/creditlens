// 截图核对 · GitHub Actions only. Serves the exported static demo, drives Playwright Chromium and runs the
// real in-browser OCR (onnxruntime-web WASM + PP-OCRv5 mobile, downloaded on the runner):
//   --mode check : the visitor flow at 1440 and 375 px, light and dark; hidden when SIMD is missing;
//                  tampered runtime refused; cancel; no requests before the click
//   --mode eval  : --split dev|test, scored against XBRL fact boxes (docs/46-table-check.md 评测协议)
//   --mode probe : --urls "u1 u2", public image URLs; screenshots and readings for a reviewer
// It refuses to run outside Actions: no model inference happens on a developer machine.
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

if (!process.env.GITHUB_ACTIONS) { console.error('Runs real OCR in a browser; GitHub Actions only.'); process.exit(2); }
const { chromium } = await import('playwright');
const arg = (name, fallback = null) => { const i = process.argv.indexOf(`--${name}`); return i > 0 ? process.argv[i + 1] : fallback; };
const mode = arg('mode'), site = path.resolve(arg('site', 'site')), out = path.resolve(arg('out', 'table-out'));
fs.mkdirSync(out, { recursive: true });
const config = JSON.parse(fs.readFileSync('configs/table-check.json', 'utf8'));
const extra = new Map(); // url path -> absolute file

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const url = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  let file = extra.get(url);
  if (!file && url.startsWith('/creditlens/')) file = path.join(site, url.slice('/creditlens/'.length) || 'index.html');
  if (!file || !file.startsWith(site) && !extra.has(url) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end('missing'); return; }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file).toLowerCase()] ?? 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(8765, '127.0.0.1', r));
const ORIGIN = 'http://127.0.0.1:8765';
const PAGE = `${ORIGIN}/creditlens/table-check.html`;
const browser = await chromium.launch();
const results = { mode, commit: process.env.GITHUB_SHA, run_id: process.env.GITHUB_RUN_ID, engine: config.engine_version, checks: [] };
const fail = [];
const ok = (name, pass, detail = null) => { results.checks.push({ name, pass: Boolean(pass), detail }); if (!pass) fail.push(name); console.log(`${pass ? 'ok  ' : 'FAIL'} ${name}${detail ? ` · ${JSON.stringify(detail).slice(0, 300)}` : ''}`); };

async function open(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light', ...options });
  const page = await context.newPage();
  const log = { errors: [], hosts: new Set(), early: [] };
  page.on('pageerror', (e) => log.errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error') log.errors.push(m.text()); });
  page.on('request', (r) => log.hosts.add(new URL(r.url()).host));
  return { context, page, log };
}
async function readyRun(page) {
  await page.waitForFunction(() => !document.getElementById('tcRun').hidden || /失败|不符|中断|无法|取消/.test(document.getElementById('tcStatus')?.textContent ?? ''), null, { timeout: 900_000 });
  const status = await page.textContent('#tcStatus');
  if (await page.isHidden('#tcRun')) console.log('load did not finish:', status);
  return status;
}

if (mode === 'check') {
  const { context, page, log } = await open();
  await page.goto(PAGE);
  await page.waitForFunction(() => /MB/.test(document.getElementById('tcLoad')?.textContent ?? ''));
  ok('no request leaves the site before the click', [...log.hosts].every((h) => h === '127.0.0.1:8765'), [...log.hosts]);
  ok('download size shown before download', /约 3\d\.\d MB/.test(await page.textContent('#tcLoad')), await page.textContent('#tcLoad'));
  const t0 = Date.now();
  await page.click('#tcLoad');
  const loadStatus = await readyRun(page);
  if (await page.isHidden('#tcRun')) { console.log('page errors:', log.errors); }
  results.load_seconds = (Date.now() - t0) / 1000;
  ok('model loads and reports ready', /已就绪/.test(await page.textContent('#tcStatus')), await page.textContent('#tcStatus'));
  const allowed = new Set(['127.0.0.1:8765', 'cdn.jsdelivr.net']);
  const hosts = [...log.hosts].filter((h) => !allowed.has(h));
  ok('requests go only to this site (weights) and jsDelivr (runtime)', hosts.length === 0, [...log.hosts]);
  await page.click('#tcExample');
  await page.waitForSelector('#tcResult:not([hidden])', { timeout: 300_000 });
  const checks = await page.$$eval('.tc-check', (b) => b.map((x) => ({ cls: x.className, text: x.textContent })));
  results.example = checks;
  const bad = checks.filter((c) => c.cls.includes('does_not_foot'));
  ok('example: the one subtotal that does not foot is flagged with its formula',
    bad.length === 1 && /Total current assets/.test(bad[0].text) && /1,240 \+ 615 \+ 388 = 2,243/.test(bad[0].text) && /2,253/.test(bad[0].text), checks);
  ok('example: the 2024 subtotal and total foot', checks.filter((c) => c.cls.includes(' foots')).length >= 2, checks.length);
  ok('every number is boxed', (await page.$$eval('.tc-box', (r) => r.length)) >= 12);
  await page.click('.tc-check.does_not_foot');
  ok('choosing a check marks its target and terms', (await page.$$('.tc-box.target.does_not_foot')).length === 1 && (await page.$$('.tc-box.term')).length === 3);
  for (const [w, h, scheme] of [[1440, 900, 'light'], [1440, 900, 'dark'], [375, 812, 'dark'], [375, 812, 'light']]) {
    await page.setViewportSize({ width: w, height: h });
    await page.emulateMedia({ colorScheme: scheme });
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    ok(`no horizontal overflow at ${w}px ${scheme}`, overflow <= 1, overflow);
    await page.screenshot({ path: path.join(out, `check-${w}-${scheme}.png`), fullPage: true });
  }
  ok('no page or console errors in the visitor flow', log.errors.length === 0, log.errors);
  await context.close();

  const u = await open();
  await u.page.addInitScript(() => { WebAssembly.validate = () => false; });
  await u.page.goto(PAGE);
  await u.page.waitForSelector('#tcUnsupported:not([hidden])');
  ok('without WebAssembly SIMD the controls are hidden and the reason is shown',
    await u.page.isHidden('#tcControls') && /SIMD/.test(await u.page.textContent('#tcUnsupported')));
  await u.page.screenshot({ path: path.join(out, 'check-unsupported.png') });
  await u.context.close();

  const t = await open();
  await t.page.route('**/ort-wasm-simd-threaded.mjs', async (route) => {
    const r = await route.fetch();
    await route.fulfill({ response: r, body: (await r.text()) + '\n// tampered\n' });
  });
  await t.page.goto(PAGE);
  await t.page.click('#tcLoad');
  await t.page.waitForFunction(() => /校验不符/.test(document.getElementById('tcStatus')?.textContent ?? ''), null, { timeout: 120_000 });
  ok('a tampered runtime file is refused and nothing runs', await t.page.evaluate(() => typeof window.ort === 'undefined') && await t.page.isHidden('#tcRun'));
  ok('the load button is offered again after a refusal', await t.page.isEnabled('#tcLoad'));
  await t.context.close();

  const c = await open();
  await c.page.route('**/*.onnx', async (route) => { await new Promise((r) => setTimeout(r, 4000)); await route.continue(); });
  await c.page.goto(PAGE);
  await c.page.click('#tcLoad');
  await c.page.waitForFunction(() => /\.onnx/.test(document.getElementById('tcStatus')?.textContent ?? '') || /ort-wasm/.test(document.getElementById('tcStatus')?.textContent ?? ''), null, { timeout: 120_000 });
  await c.page.click('#tcCancel');
  await c.page.waitForFunction(() => /已取消/.test(document.getElementById('tcStatus')?.textContent ?? ''), null, { timeout: 60_000 });
  ok('cancel stops the download and offers the button again', await c.page.isEnabled('#tcLoad') && await c.page.isHidden('#tcRun'));
  await c.context.close();
}

// ---------------------------------------------------------------------------
// Evaluation and probe
// ---------------------------------------------------------------------------

const coverage = (t, o) => Math.max(0, Math.min(t.x1, o.x1) - Math.max(t.x0, o.x0)) * Math.max(0, Math.min(t.y1, o.y1) - Math.max(t.y0, o.y0))
  / Math.max((t.x1 - t.x0) * (t.y1 - t.y0), 1e-9);
const selected = (fact, tok) => coverage(fact, tok) >= 0.35 || (fact.x0 <= (tok.x0 + tok.x1) / 2 && (tok.x0 + tok.x1) / 2 <= fact.x1 && fact.y0 <= (tok.y0 + tok.y1) / 2 && (tok.y0 + tok.y1) / 2 <= fact.y1);
const digits = (s) => String(s).replace(/[^0-9.]/g, '').replace(/^0+(?=\d)/, '').replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
const quantile = (xs, q) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.ceil(q * s.length) - 1)] : null; };

function items(split) {
  if (split === 'test') {
    const dir = path.join(site, 'assets/grounding');
    const index = JSON.parse(fs.readFileSync(path.join(dir, 'index.json'), 'utf8'));
    return index.issuers.flatMap((issuer) => {
      const facts = JSON.parse(fs.readFileSync(path.join(dir, issuer.facts_file), 'utf8')).facts;
      return issuer.pages.map((p) => ({
        id: `${issuer.id}-p${p.page}`, url: `${ORIGIN}/creditlens/assets/grounding/${p.image}`, widthPt: p.width, statements: p.statements,
        facts: facts.filter((f) => f.page === p.page).map((f) => ({
          box: f.box, text: f.text, negative: f.printed_negative, dash: f.flags.includes('dash_zero'),
          scored: f.outcome !== 'not_scored' && !f.flags.includes('in_text_cell') && !f.flags.includes('in_text_block') })),
      }));
    });
  }
  const dir = path.resolve(arg('dev-dir'));
  const report = JSON.parse(fs.readFileSync(path.join(dir, 'report.json'), 'utf8'));
  const records = new Map(report.records.map((r) => [`${r.issuer}:${r.page}:${r.fact}`, r]));
  return report.issuers.flatMap((issuer) => issuer.images.map((img) => {
    const file = path.join(dir, 'pages', img.png);
    extra.set(`/creditlens/eval-dev/${img.png}`, file);
    return {
      id: `${issuer.id}-p${img.page}`, url: `${ORIGIN}/creditlens/eval-dev/${img.png}`, widthPt: img.width, statements: img.statements,
      facts: issuer.facts.filter((f) => f.page === img.page).map((f) => {
        const r = records.get(`${issuer.id}:${img.page}:${f.seq}`);
        return { box: f.box, text: f.text, negative: r.key.printed_negative, dash: r.dash_zero,
          scored: r.outcome !== 'not_scored' && !r.in_text_cell && !r.in_text_block };
      }),
    };
  }));
}

function score(item, r) {
  const k = r.width / item.widthPt;
  const facts = item.facts.map((f) => ({ ...f, px: { x0: f.box.x0 * k, y0: f.box.top * k, x1: f.box.x1 * k, y1: f.box.bottom * k } }));
  const cells = r.tokens.filter((t) => t.kind !== 'text');
  const counts = { facts: 0, correct: 0, wrong: 0, missed: 0, dash_facts: 0, dash_correct: 0 };
  const wrongs = [];
  const factOf = new Map();
  for (const f of facts) {
    const hit = cells.filter((t) => selected(f.px, t.box));
    for (const t of hit) if (!factOf.has(t.id)) factOf.set(t.id, f);
    if (!f.scored) continue;
    counts.facts += 1;
    if (f.dash) counts.dash_facts += 1;
    if (!hit.length) { counts.missed += 1; continue; }
    const t = hit[0];
    const right = hit.length === 1 && (f.dash ? t.kind === 'dash' : t.kind === 'amount' && digits(t.text) === digits(f.text) && Boolean(t.negative) === Boolean(f.negative));
    if (right) { counts.correct += 1; if (f.dash) counts.dash_correct += 1; } else { counts.wrong += 1; wrongs.push({ expected: f.text, negative: f.negative, read: hit.map((x) => x.text) }); }
  }
  const footing = { foots: 0, does_not_foot: 0, not_checked: 0, by_kind: {}, alarms: [] };
  for (const c of r.checks) {
    footing[c.status] += 1;
    footing.by_kind[c.kind] = (footing.by_kind[c.kind] ?? 0) + 1;
    if (c.status !== 'does_not_foot') continue;
    const tokens = [c.target, ...c.terms.map((t) => t.token)].map((id) => factOf.get(id));
    let cause = 'unmapped';
    if (tokens.every(Boolean)) {
      const val = (f) => (f.dash ? 0 : Number(digits(f.text))) * (f.negative ? -1 : 1);
      const sum = c.terms.reduce((s, t) => s + t.sign * val(factOf.get(t.token)), 0);
      cause = c.kind === 'total' || c.kind === 'cross'
        ? (Math.abs(sum - val(tokens[0])) <= 0.5 * (c.terms.length + 1) + 1e-9 ? 'reading' : 'structure') : 'not_classified';
    }
    footing.alarms.push({ kind: c.kind, row: r.rows[c.row]?.fullLabel, formula: c.formula, read: c.read, computed: c.computed, cause });
  }
  return { counts, wrongs: wrongs.slice(0, 40), footing };
}

if (mode === 'eval') {
  const split = arg('split');
  const list = items(split);
  const variants = split === 'test' ? ['clean', 'jpeg_q60', 'blur_1px'] : ['webp_grey_q68'];
  const { context, page, log } = await open();
  await page.goto(PAGE);
  const loaded = await page.evaluate(() => window.__tableCheck.load());
  results.init = loaded;
  if (!loaded.ok) { console.log('load failed:', loaded.error, log.errors); process.exit(1); }
  results.split = split;
  results.pages = [];
  for (const variant of variants) {
    for (const item of list) {
      const r = await page.evaluate(async ([url, kind]) => {
        const x = await window.__tableCheck.runUrl(url, kind);
        return { width: x.width, height: x.height, timings: x.timings, checks: x.checks,
          rows: x.table.rows.map((r) => ({ fullLabel: r.fullLabel, kind: r.kind })), columns: x.table.columns.map((c) => ({ year: c.year, header: c.header })),
          tokens: x.table.tokens.map((t) => ({ id: t.id, text: t.text, kind: t.kind, negative: Boolean(t.negative), box: t.box, conf: t.conf })) };
      }, [item.url, variant]);
      const s = score(item, r);
      results.pages.push({ id: item.id, variant, statements: item.statements, timings: r.timings, ...s });
      console.log(JSON.stringify({ page: item.id, variant, seconds: r.timings.total, ...s.counts, footing: { f: s.footing.foots, x: s.footing.does_not_foot, n: s.footing.not_checked } }));
    }
  }
  results.errors = log.errors;
  await context.close();
  const agg = {};
  for (const v of variants) {
    const ps = results.pages.filter((p) => p.variant === v);
    const sum = (f) => ps.reduce((n, p) => n + f(p), 0);
    const facts = sum((p) => p.counts.facts), correct = sum((p) => p.counts.correct), wrong = sum((p) => p.counts.wrong);
    const foots = sum((p) => p.footing.foots), alarms = sum((p) => p.footing.does_not_foot);
    agg[v] = { pages: ps.length, facts, correct, wrong, missed: sum((p) => p.counts.missed), cell_value_accuracy: facts ? correct / facts : null,
      wrong_value_rate: facts ? wrong / facts : null, dash_facts: sum((p) => p.counts.dash_facts), dash_correct: sum((p) => p.counts.dash_correct),
      checks_performed: foots + alarms, foots, does_not_foot: alarms, not_checked: sum((p) => p.footing.not_checked),
      false_alarm_rate: foots + alarms ? alarms / (foots + alarms) : null,
      alarm_causes: ps.flatMap((p) => p.footing.alarms.map((a) => a.cause)).reduce((m, c) => ({ ...m, [c]: (m[c] ?? 0) + 1 }), {}),
      seconds_p50: quantile(ps.map((p) => p.timings.total), 0.5), seconds_p95: quantile(ps.map((p) => p.timings.total), 0.95),
      seconds_max: Math.max(...ps.map((p) => p.timings.total)) };
  }
  results.aggregate = agg;
  const bar = config.evaluation.bar;
  const c = agg.clean ?? agg.webp_grey_q68;
  results.bar = {
    cell_value_accuracy: c.cell_value_accuracy >= bar.cell_value_accuracy_min, wrong_value_rate: c.wrong_value_rate <= bar.wrong_value_rate_max,
    checks_performed: c.checks_performed >= bar.footing_checks_performed_min, false_alarm_rate: c.false_alarm_rate !== null && c.false_alarm_rate <= bar.footing_false_alarm_rate_max,
    latency_p95: c.seconds_p95 <= bar.latency_p95_seconds_per_page_max, init: loaded.initSeconds <= bar.init_seconds_max, download: loaded.downloadedBytes <= bar.download_bytes_max,
  };
  results.bar.all = Object.values(results.bar).every(Boolean);
  results.bar.applies = split === 'test' ? 'test split, clean variant: the pre-registered decision' : 'dev split: for development only';
}

if (mode === 'probe') {
  const urls = (arg('urls', '') || '').split(/\s+/).filter(Boolean).slice(0, 12);
  const { context, page, log } = await open();
  await page.goto(PAGE);
  results.init = await page.evaluate(() => window.__tableCheck.load());
  if (!results.init.ok) {
    console.log('load failed:', results.init.error, log.errors);
    const u = config.weights.base_url + 'ch_PP-OCRv5_det.onnx';
    const b = await page.evaluate(async (x) => { const r = await fetch(x, { credentials: 'omit' }); return { status: r.status, url: r.url, headers: [...r.headers].slice(0, 30), body: (await r.text()).slice(0, 400) }; }, u);
    console.log('browser fetch:', JSON.stringify(b));
    const n = await fetch(u, { redirect: 'manual', headers: { 'user-agent': await page.evaluate(() => navigator.userAgent), origin: ORIGIN } });
    console.log('node fetch:', n.status, JSON.stringify([...n.headers].slice(0, 30)), (await n.text()).slice(0, 300));
    console.log('user agent:', await page.evaluate(() => navigator.userAgent));
    process.exit(1);
  }
  results.items = [];
  for (const [i, url] of urls.entries()) {
    const entry = { url };
    try {
      if (!/^https:\/\//.test(url)) throw new Error('only https URLs');
      const response = await fetch(url, { redirect: 'follow', headers: { 'user-agent': 'CreditLens table-check probe (GitHub Actions)' } });
      const type = response.headers.get('content-type') ?? '';
      const body = Buffer.from(await response.arrayBuffer());
      if (!response.ok || !type.startsWith('image/') || body.length > 20e6) throw new Error(`HTTP ${response.status} ${type} ${body.length} bytes`);
      const file = path.join(out, `probe-${i + 1}${({ 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif' })[type.split(';')[0]] ?? '.img'}`);
      fs.writeFileSync(file, body);
      extra.set(`/creditlens/probe/${i + 1}`, file);
      const r = await page.evaluate(async (u) => {
        const x = await window.__tableCheck.runUrl(u, 'clean', true);
        return { timings: x.timings, numbers: x.table.tokens.filter((t) => t.kind === 'amount' || t.kind === 'dash').length,
          garbled: x.table.tokens.filter((t) => t.kind === 'garbled').map((t) => t.text), rows: x.table.rows.length, columns: x.table.columns.length,
          checks: x.checks.map((c) => ({ kind: c.kind, status: c.status, reason: c.reason, row: x.table.rows[c.row]?.fullLabel, formula: c.formula, read: c.read, computed: c.computed, difference: c.difference })) };
      }, `${ORIGIN}/creditlens/probe/${i + 1}`);
      Object.assign(entry, r);
      await page.screenshot({ path: path.join(out, `probe-${i + 1}-screen.png`), fullPage: true });
    } catch (error) { entry.error = String(error).slice(0, 300); }
    results.items.push(entry);
    console.log(JSON.stringify({ url, numbers: entry.numbers, checks: entry.checks?.length, error: entry.error }));
  }
  results.errors = log.errors;
  await context.close();
}

await browser.close();
server.close();
results.failed = fail;
fs.writeFileSync(path.join(out, `${mode}.json`), JSON.stringify(results, null, 1));
const md = [`## 截图核对 · ${mode}${results.split ? ` · ${results.split}` : ''}`, '', `Commit \`${results.commit}\` · engine ${results.engine}`, ''];
if (mode === 'check') md.push(...results.checks.map((c) => `- ${c.pass ? '✅' : '❌'} ${c.name}`));
if (results.aggregate) {
  md.push('| variant | pages | facts | correct | wrong | missed | accuracy | wrong rate | checks | not foot | false-alarm | P50 s | P95 s |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|');
  for (const [v, a] of Object.entries(results.aggregate)) {
    md.push(`| ${v}${v === 'clean' || v === 'webp_grey_q68' ? '' : ' (SYNTHETIC)'} | ${a.pages} | ${a.facts} | ${a.correct} | ${a.wrong} | ${a.missed} | ${(a.cell_value_accuracy * 100).toFixed(1)}% | ${(a.wrong_value_rate * 100).toFixed(2)}% | ${a.checks_performed} | ${a.does_not_foot} | ${a.false_alarm_rate === null ? '—' : (a.false_alarm_rate * 100).toFixed(1) + '%'} | ${a.seconds_p50?.toFixed(1)} | ${a.seconds_p95?.toFixed(1)} |`);
  }
  md.push('', `Init ${results.init.initSeconds?.toFixed(1)} s · downloaded ${results.init.downloadedBytes} bytes · bar (${results.bar.applies}): ${JSON.stringify(results.bar)}`);
}
if (results.items) for (const it of results.items) md.push(`- ${it.url}: ${it.error ?? `${it.numbers} numbers, ${it.rows} rows, ${it.columns} columns; checks ${it.checks.map((c) => c.status).join(', ') || 'none'}`}`);
fs.writeFileSync(path.join(out, 'summary.md'), md.join('\n') + '\n');
console.log(md.join('\n'));
process.exit(fail.length ? 1 : 0);

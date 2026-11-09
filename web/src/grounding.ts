/* DOM layer for creditlens/grounding.html (XBRL 对照). Displays one saved benchmark run:
   rendered 10-K statement pages, a box for every Inline XBRL fact coloured by how the
   image-only reading compared, and a brass loupe. All text is set with textContent. */
import {
  countOutcomes, filterFacts, groupDigits, OUTCOME_LONG, OUTCOME_TEXT, percent, readingOrder, readoutRows, reasonText,
  STATEMENT_TEXT, type Fact, type Filter, type Index, type IssuerIndex, type PageInfo, type Totals,
} from './grounding-model';

const SVG = 'http://www.w3.org/2000/svg';
const script = document.currentScript as HTMLScriptElement | null;
const base = new URL('grounding/', script?.src ?? location.href);
const $ = (id: string) => document.getElementById(id) as HTMLElement;
const BOX_COLOUR: Record<string, string> = { match: '#1d8a4e', conflict: '#c4302b', abstain: '#c07a0c', not_scored: '#7a8094' };

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, string> = {}, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}
function svg(tag: string, attrs: Record<string, string | number>): SVGElement {
  const node = document.createElementNS(SVG, tag) as SVGElement;
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

interface State {
  index: Index; issuer: IssuerIndex; page: PageInfo; facts: Fact[]; filter: Filter; selected: Fact | null;
  hovering: boolean; nodes?: { sheet: HTMLElement; img: HTMLImageElement; overlay: SVGElement; lens: HTMLElement; zoomImg: HTMLImageElement; zoomOverlay: SVGElement; handle: HTMLElement };
}
let state: State | null = null;
const factCache = new Map<string, Promise<Fact[]>>();

function loadFacts(issuer: IssuerIndex): Promise<Fact[]> {
  if (!factCache.has(issuer.id)) {
    factCache.set(issuer.id, fetch(new URL(issuer.facts_file, base), { cache: 'no-cache' })
      .then(r => r.ok ? r.json() : Promise.reject(new Error('facts ' + r.status)))
      .then((data: { facts: Fact[] }) => data.facts));
  }
  return factCache.get(issuer.id)!;
}

function roleText(role: string): string {
  return role === 'heldout' ? '保留集' : role === 'dev' ? '开发集（已见）' : '另加（非保留集）';
}

function summaryTable(index: Index): HTMLElement {
  const table = el('table', { class: 'gr-table' });
  const head = el('thead'); const hr = el('tr');
  for (const h of ['发行人', '事实', '一致', '冲突', '未作答', '定位召回', '数值全对', '年份列对']) hr.append(el('th', { scope: 'col' }, h));
  head.append(hr); table.append(head);
  const body = el('tbody');
  const row = (name: string, sub: string, t: Totals, cls = '') => {
    const tr = el('tr', cls ? { class: cls } : {});
    const th = el('th', { scope: 'row' }); th.append(el('span', {}, name)); if (sub) th.append(el('small', {}, sub));
    tr.append(th);
    const cells: [string, string, string?][] = [
      ['事实', String(t.facts_scored)],
      ['一致', String(t.outcomes.match), 'ok'],
      ['冲突', String(t.outcomes.conflict), t.outcomes.conflict ? 'bad' : ''],
      ['未作答', String(t.outcomes.abstain), 'warn'],
      ['定位召回', percent(t.box_recall.rate)],
      ['数值全对', `${percent(t.exact_value.rate_of_all)}（读出 ${t.exact_value.read} 项中 ${t.exact_value.correct}）`],
      ['年份列对', `${percent(t.period_column.rate_of_all)}（读出 ${t.period_column.read} 项中 ${t.period_column.correct}）`],
    ];
    for (const [label, value, c] of cells) tr.append(el('td', { 'data-label': label, ...(c ? { class: c } : {}) }, value));
    body.append(tr);
  };
  for (const issuer of index.issuers) row(issuer.name, `${roleText(issuer.role)} · ${issuer.pages.length} 页`, index.by_issuer[issuer.id]);
  row('合计', `${index.issuers.length} 家 · ${index.issuers.reduce((n, i) => n + i.pages.length, 0)} 页`, index.summary, 'total');
  table.append(body);
  return table;
}

function reasonsList(t: Totals): HTMLElement {
  const wrap = el('div', { class: 'gr-reasons' });
  const list = (title: string, entries: [string, number][]) => {
    if (!entries.length) return;
    wrap.append(el('h3', {}, title));
    const ul = el('ul');
    for (const [reason, n] of entries.sort((a, b) => b[1] - a[1])) ul.append(el('li', {}, `${reasonText({ outcome: 'abstain', reason })} · ${n}`));
    wrap.append(ul);
  };
  list('未作答的原因', Object.entries(t.abstain_reasons));
  list('冲突的分项（一项事实可同时不符多项）', Object.entries(t.conflict_components));
  return wrap;
}

function renderTabs() {
  if (!state) return;
  const issuers = $('grIssuers'); issuers.replaceChildren();
  for (const issuer of state.index.issuers) {
    const b = el('button', { type: 'button', 'aria-pressed': String(issuer.id === state.issuer.id) }, issuer.name);
    b.addEventListener('click', () => selectIssuer(issuer));
    issuers.append(b);
  }
  const pages = $('grPages'); pages.replaceChildren();
  for (const page of state.issuer.pages) {
    const label = page.statements.map(s => STATEMENT_TEXT[s] ?? s).join('、') || '报表页';
    const b = el('button', { type: 'button', 'aria-pressed': String(page.page === state.page.page) }, `${label} · 第 ${page.page} 页`);
    b.addEventListener('click', () => selectPage(page));
    pages.append(b);
  }
}

function renderSource() {
  if (!state) return;
  const { issuer, page } = state, f = issuer.filing;
  const p = $('grSource'); p.replaceChildren();
  p.append(`${issuer.name} · Form ${f.form} · 提交 ${f.filed} · accession ${f.accession} · 渲染页 ${page.page}。来源：`);
  const a = el('a', { href: f.document_url, rel: 'noopener' }, 'SEC EDGAR 原文件'); p.append(a, ' · ');
  p.append(el('a', { href: f.index_url, rel: 'noopener' }, '提交索引'));
  if (f.archive_url && f.capture_timestamp) {
    const t = f.capture_timestamp;
    p.append(`。文件取自互联网档案馆 ${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)} 的抓取（`);
    p.append(el('a', { href: f.archive_url, rel: 'noopener' }, '档案副本'), '，已按抓取摘要核对字节；SEC 拒绝 Actions 运行器的直接请求）');
  }
  p.append('。Chromium 渲染的 Inline XBRL 页面，不是发行人 PDF，也不是扫描件；图下方灰条印有 SEC 地址与 accession。');
}

function pageFacts(): Fact[] {
  return state ? readingOrder(state.facts.filter(f => f.page === state!.page.page)) : [];
}

function drawOverlay(target: SVGElement, crisp: boolean) {
  if (!state) return;
  const { page } = state;
  const sx = page.pixel_width / page.width, sy = page.content_pixel_height / page.height;
  target.replaceChildren();
  const visible = new Set(filterFacts(pageFacts(), state.filter).map(f => f.id));
  for (const f of pageFacts()) {
    const colour = BOX_COLOUR[f.outcome];
    const on = visible.has(f.id), chosen = state.selected?.id === f.id;
    const pad = 1.5;
    const rect = svg('rect', {
      x: f.box.x0 * sx - pad, y: f.box.top * sy - pad, width: (f.box.x1 - f.box.x0) * sx + pad * 2, height: (f.box.bottom - f.box.top) * sy + pad * 2,
      rx: 2, fill: colour, 'fill-opacity': on ? (chosen ? .12 : .08) : 0, stroke: colour, 'stroke-width': chosen ? 4 : crisp ? 2.4 : 2,
      'stroke-opacity': on ? 1 : .18, 'data-id': f.id,
    });
    if (f.outcome === 'abstain') rect.setAttribute('stroke-dasharray', '6 4');
    target.append(rect);
    if (chosen) target.append(svg('rect', {
      x: f.box.x0 * sx - pad - 5, y: f.box.top * sy - pad - 5, width: (f.box.x1 - f.box.x0) * sx + pad * 2 + 10,
      height: (f.box.bottom - f.box.top) * sy + pad * 2 + 10, rx: 4, fill: 'none', stroke: '#d5ac5f', 'stroke-width': 3,
    }));
  }
}

function geometry() {
  const sheet = state!.nodes!.sheet, width = sheet.clientWidth, height = width * state!.page.pixel_height / state!.page.pixel_width;
  const d = Math.round(Math.min(Math.max(width * .3, 108), 184));
  const zoom = Math.max(2.2, Math.min(3.2, 920 / Math.max(width, 1)));
  return { width, height, d, zoom };
}

function placeLens(px: number, py: number) {
  if (!state?.nodes) return;
  const { lens, zoomImg, zoomOverlay, handle } = state.nodes, g = geometry();
  lens.style.setProperty('--d', g.d + 'px');
  lens.style.left = (px - g.d / 2) + 'px'; lens.style.top = (py - g.d / 2) + 'px';
  for (const node of [zoomImg, zoomOverlay] as (HTMLElement | SVGElement)[]) {
    node.style.width = (g.width * g.zoom) + 'px'; node.style.height = (g.height * g.zoom) + 'px';
    node.style.left = (g.d / 2 - px * g.zoom) + 'px'; node.style.top = (g.d / 2 - py * g.zoom) + 'px';
  }
  const r = g.d / 2, a = 42 * Math.PI / 180;
  handle.style.left = (px + Math.cos(a) * (r + 2)) + 'px'; handle.style.top = (py + Math.sin(a) * (r + 2) - 4) + 'px';
}

function focusOnSelected() {
  if (!state?.nodes || !state.selected) return;
  const g = geometry(), p = state.page, b = state.selected.box;
  placeLens(((b.x0 + b.x1) / 2) / p.width * g.width, ((b.top + b.bottom) / 2) / p.height * (g.height * p.content_pixel_height / p.pixel_height));
}

function renderReadout() {
  const dl = $('grReadout'); dl.replaceChildren();
  if (!state?.selected) { dl.append(el('dt', {}, '提示'), el('dd', {}, '点选页面上的框或下方列表中的一项，查看 XBRL 概念、上下文和图像读数。')); return; }
  for (const row of readoutRows(state.selected)) {
    dl.append(el('dt', {}, row.label));
    const dd = el('dd', row.cls ? { class: row.cls } : {}, row.value);
    if (row.label === '结果') dd.title = OUTCOME_LONG[state.selected.outcome];
    dl.append(dd);
  }
  const facts = filterFacts(pageFacts(), state.filter);
  const at = facts.findIndex(f => f.id === state!.selected!.id);
  $('grPos').textContent = at >= 0 ? `${at + 1} / ${facts.length}` : `— / ${facts.length}`;
}

function renderList() {
  if (!state) return;
  const list = $('grList'); list.replaceChildren();
  const facts = filterFacts(pageFacts(), state.filter);
  $('grCount').textContent = `${facts.length} 项`;
  for (const f of facts) {
    const li = el('li');
    const b = el('button', { type: 'button', class: 'gr-item ' + f.outcome, 'aria-current': String(state.selected?.id === f.id) });
    b.append(el('span', { class: 'gr-dot', 'aria-hidden': 'true' }));
    b.append(el('span', { class: 'gr-item-concept' }, f.concept.split(':').pop() ?? f.concept));
    b.append(el('span', { class: 'gr-item-meta' }, `${f.year ?? '—'} · ${f.printed_negative ? '(' + f.text + ')' : f.text} · OCR ${f.ocr ? '“' + f.ocr.text + '”' : '无'} · ${OUTCOME_TEXT[f.outcome]}`));
    b.addEventListener('click', () => select(f, true));
    li.append(b); list.append(li);
  }
}

function renderFilter() {
  if (!state) return;
  const host = $('grFilter'); host.replaceChildren();
  const counts = countOutcomes(pageFacts());
  const options: [Filter, string][] = [['all', `全部 ${pageFacts().length}`], ['match', `一致 ${counts.match}`], ['abstain', `未作答 ${counts.abstain}`], ['conflict', `冲突 ${counts.conflict}`]];
  if (counts.not_scored) options.push(['not_scored', `不计分 ${counts.not_scored}`]);
  for (const [value, label] of options) {
    const b = el('button', { type: 'button', class: 'gr-chip ' + value, 'aria-pressed': String(state.filter === value) }, label);
    b.addEventListener('click', () => { state!.filter = value; const first = filterFacts(pageFacts(), value)[0] ?? null; select(first, false); renderFilter(); });
    host.append(b);
  }
}

function select(fact: Fact | null, announce: boolean) {
  if (!state) return;
  state.selected = fact;
  drawOverlay(state.nodes!.overlay, false); drawOverlay(state.nodes!.zoomOverlay, true);
  if (!state.hovering) focusOnSelected();
  renderReadout(); renderList();
  if (announce && fact) $('grStatus').textContent = `${fact.concept}，${fact.year ?? ''}，${OUTCOME_TEXT[fact.outcome]}${fact.reason ? '，' + reasonText(fact) : ''}`;
}

function step(delta: number) {
  if (!state) return;
  const facts = filterFacts(pageFacts(), state.filter);
  if (!facts.length) return;
  const at = facts.findIndex(f => f.id === state!.selected?.id);
  select(facts[(at + delta + facts.length) % facts.length], true);
}

function buildSheet() {
  if (!state) return;
  const sheet = $('grSheet'); sheet.replaceChildren();
  const src = new URL(state.page.image, base).href;
  const alt = `${state.issuer.name} 10-K 渲染页 ${state.page.page}（${state.page.statements.map(s => STATEMENT_TEXT[s] ?? s).join('、')}），框为 XBRL 事实的位置`;
  const img = el('img', { src, alt, width: String(state.page.pixel_width), height: String(state.page.pixel_height), decoding: 'async' });
  const overlay = svg('svg', { viewBox: `0 0 ${state.page.pixel_width} ${state.page.pixel_height}`, class: 'gr-overlay', 'aria-hidden': 'true' });
  const lens = el('div', { class: 'gr-lens', 'aria-hidden': 'true' });
  const zoomImg = el('img', { src, alt: '', decoding: 'async' });
  const zoomOverlay = svg('svg', { viewBox: `0 0 ${state.page.pixel_width} ${state.page.pixel_height}` });
  lens.append(zoomImg, zoomOverlay);
  const handle = el('div', { class: 'gr-lens-handle', 'aria-hidden': 'true' });
  img.addEventListener('error', () => { sheet.replaceChildren(el('p', { class: 'gr-fallback' }, '页面图片暂不可用。来源文件仍可从下方的 SEC 链接打开。')); });
  sheet.append(img, overlay, handle, lens);
  state.nodes = { sheet, img, overlay, lens, zoomImg, zoomOverlay, handle };
}

function nearestFact(px: number, py: number): Fact | null {
  if (!state?.nodes) return null;
  const g = geometry(), p = state.page;
  const x = px / g.width * p.width, y = py / (g.height * p.content_pixel_height / p.pixel_height) * p.height;
  const slack = 12 / g.width * p.width;
  let best: Fact | null = null, bestD = Infinity;
  for (const f of filterFacts(pageFacts(), state.filter)) {
    const dx = Math.max(f.box.x0 - x, 0, x - f.box.x1), dy = Math.max(f.box.top - y, 0, y - f.box.bottom);
    const d = Math.hypot(dx, dy);
    if (d < bestD && d <= slack) { best = f; bestD = d; }
  }
  return best;
}

async function selectPage(page: PageInfo) {
  if (!state) return;
  state.page = page; state.selected = null;
  renderTabs(); renderSource(); buildSheet(); renderFilter();
  const facts = filterFacts(pageFacts(), state.filter);
  select(facts.find(f => f.outcome === 'conflict') ?? facts[0] ?? null, false);
}

async function selectIssuer(issuer: IssuerIndex) {
  if (!state) return;
  $('grStatus').textContent = '正在载入 ' + issuer.name + ' 的标签…';
  try {
    state.facts = await loadFacts(issuer);
  } catch {
    $('grStatus').textContent = issuer.name + ' 的标签文件暂不可用，请刷新重试。';
    return;
  }
  state.issuer = issuer;
  await selectPage(issuer.pages[0]);
  $('grStatus').textContent = '';
}

function wireSheet() {
  const sheet = $('grSheet');
  sheet.addEventListener('pointermove', event => {
    if (event.pointerType !== 'mouse' || !state?.nodes) return;
    state.hovering = true;
    const r = sheet.getBoundingClientRect();
    sheet.classList.add('free'); placeLens(event.clientX - r.left, event.clientY - r.top);
  });
  sheet.addEventListener('pointerleave', () => { if (!state) return; state.hovering = false; sheet.classList.remove('free'); focusOnSelected(); });
  sheet.addEventListener('click', event => {
    if (!state?.nodes) return;
    const r = sheet.getBoundingClientRect();
    const f = nearestFact(event.clientX - r.left, event.clientY - r.top);
    if (f) select(f, true);
  });
  $('grPrev').addEventListener('click', () => step(-1));
  $('grNext').addEventListener('click', () => step(1));
  addEventListener('resize', () => { if (state?.nodes && !state.hovering) focusOnSelected(); });
}

async function main() {
  const status = $('grStatus');
  let index: Index;
  try {
    const response = await fetch(new URL('index.json', base), { cache: 'no-cache' });
    if (!response.ok) throw new Error(String(response.status));
    index = await response.json();
  } catch {
    $('grSummary').replaceChildren(el('p', { class: 'gr-fallback' }, '保存的基准运行结果暂不可用。请确认 creditlens/grounding/ 已随代码检出，或按 README 重跑。'));
    status.textContent = '';
    return;
  }
  const runLine = $('grRunLine');
  runLine.replaceChildren(`一次 GitHub Actions 运行（${index.run.generated_at.slice(0, 10)}，提交 ${(index.run.commit ?? '').slice(0, 7)}）的保存结果 · `);
  if (index.run.run_url) runLine.append(el('a', { href: index.run.run_url, rel: 'noopener' }, '运行记录'));
  $('grSummary').replaceChildren(summaryTable(index), reasonsList(index.summary));
  $('grRule').textContent = index.benchmark_version + ' · ' + index.score_rule_version;
  const issuer = index.issuers[0];
  if (!issuer || !issuer.pages.length) { $('grSheet').replaceChildren(el('p', { class: 'gr-fallback' }, '保存的运行没有可显示的页面。')); return; }
  state = { index, issuer, page: issuer.pages[0], facts: [], filter: 'all', selected: null, hovering: false };
  wireSheet();
  await selectIssuer(issuer);
  const total = index.summary.facts_scored;
  $('grHeadline').textContent = `${index.issuers.length} 家发行人、${index.issuers.reduce((n, i) => n + i.pages.length, 0)} 页、${total} 个计分事实：`
    + `一致 ${index.summary.outcomes.match}、冲突 ${index.summary.outcomes.conflict}、未作答 ${index.summary.outcomes.abstain}；`
    + `RS(0) ${index.summary.rs_0}，RS(10) ${index.summary.rs_10}。图片合计 ${groupDigits(String(Math.round(index.image_bytes / 1024)))} KB。`;
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { void main(); });
else void main();

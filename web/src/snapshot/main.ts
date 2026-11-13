/* DOM layer for creditlens/snapshot.html (信用快照). A visitor drops an EDGAR 10-K/10-Q Inline XBRL
   file or opens a gallery filing; the page reads the tags (ixbrl.ts), computes the snapshot
   (model.ts), shows the filing in a sandboxed frame and moves to the tagged cell of any figure the
   visitor clicks. Every text from the filing is set with textContent; nothing is uploaded. */
import { FilingError, parseFiling, sniff, type Fact, type Parsed } from './ixbrl';
import { computeSnapshot, type Input, type Metric, type Snapshot, type Term } from './model';
import { sanitize, serialize } from './sanitize';
import { amountText, inScale, unitWord } from './format';
import { anchorOf, exportBaseName, memoDocx, memoMarkdown, metricText, snapshotCsv, type SourceInfo } from './export';

declare const __APP_VERSION__: string;
const APP_VERSION = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'dev';
const script = document.currentScript as HTMLScriptElement | null;
const base = new URL('snapshot/', script?.src ?? location.href);
const $ = (id: string) => document.getElementById(id) as HTMLElement;
const SVG = 'http://www.w3.org/2000/svg';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, string> = {}, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}
function svg(tag: string, attrs: Record<string, string | number>, text?: string): SVGElement {
  const node = document.createElementNS(SVG, tag) as SVGElement;
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text !== undefined) node.textContent = text;
  return node;
}

export interface GalleryEntry {
  id: string; cik: number; name: string; ticker?: string; sic: string; stratum: string; stratum_label: string; form: string;
  accession: string; document: string; period: string; filed: string; document_url: string; archive_url: string; capture_timestamp: string;
  file: string; bytes: number; gz_bytes: number; sha256: string; gz_sha256: string;
  snapshot: { status: string; template: string; debt_to_ebitda: number | null; interest_coverage: number | null; current_ratio: number | null; fcf_to_debt: number | null; debt: string | null; currency: string; display_scale: number; cues: string[] };
}
interface Gallery { rule_id: string; generated_by: string; run: string; entries: GalleryEntry[]; strata: { id: string; label: string; taken: number; examined: number; rejected: Record<string, number> }[] }

interface State { parsed: Parsed; snapshot: Snapshot; source: SourceInfo; byIndex: Map<number, Fact>; frame: HTMLIFrameElement; frameDoc: Document | null; selected: number | null }
let state: State | null = null;
let gallery: Gallery | null = null;
let loading = 0;

// ---------- status ----------
function status(kind: 'busy' | 'error' | 'ok' | 'none', text = '', detail = '') {
  const box = $('snStatus');
  box.hidden = kind === 'none';
  box.className = 'sn-status ' + kind;
  box.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  box.replaceChildren();
  if (kind === 'none') return;
  box.append(el('strong', {}, text));
  if (detail) box.append(el('span', {}, detail));
}
const nextPaint = () => new Promise<void>(r => requestAnimationFrame(() => setTimeout(r, 0)));

async function sha256hex(bytes: Uint8Array): Promise<string> {
  if (!crypto?.subtle) return '（浏览器不支持 SHA-256）';
  const digest = await crypto.subtle.digest('SHA-256', bytes as unknown as ArrayBuffer);
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
}

/** EDGAR address or accession the visitor typed, or one recorded in a "saved from url" comment. */
export function readSource(text: string): { accession: string | null; documentUrl: string | null } {
  const t = text.trim();
  const url = /https:\/\/www\.sec\.gov\/Archives\/edgar\/data\/(\d{1,10})\/(\d{18})\/([A-Za-z0-9._-]+\.html?)/.exec(t);
  if (url) { const a = url[2]; return { accession: `${a.slice(0, 10)}-${a.slice(10, 12)}-${a.slice(12)}`, documentUrl: url[0] }; }
  const acc = /\b(\d{10}-\d{2}-\d{6})\b/.exec(t);
  return { accession: acc ? acc[1] : null, documentUrl: null };
}

// ---------- open ----------
async function openBytes(bytes: Uint8Array, source: Omit<SourceInfo, 'sha256' | 'bytes'>) {
  const ticket = ++loading;
  status('busy', '正在读取文件…', `${(bytes.length / 1024 / 1024).toFixed(1)} MB，只在本页内处理`);
  $('snResult').hidden = true;
  await nextPaint();
  try {
    sniff(bytes);
    const text = new TextDecoder('utf-8').decode(bytes);
    const parsed = parseFiling(text);
    const snapshot = computeSnapshot(parsed);
    const sha256 = await sha256hex(bytes);
    if (ticket !== loading) return;
    const saved = readSource(text.slice(0, 4000));
    const typed = readSource(($('snUrl') as HTMLInputElement).value || '');
    const full: SourceInfo = {
      ...source, sha256, bytes: bytes.length,
      accession: source.accession || typed.accession || saved.accession,
      documentUrl: source.documentUrl || typed.documentUrl || saved.documentUrl,
    };
    sanitize(parsed.doc);
    const html = serialize(parsed.doc);
    render(parsed, snapshot, full, html);
    status('ok', snapshot.status === 'ok' ? '已计算。点任一数字，申报文件视图会滚到那一格。' : '已读取，但这一模板不计算信用指标。', `${snapshot.meta.registrant} · ${snapshot.meta.form} · ${parsed.facts.length.toLocaleString('en-US')} 个数字标签`);
  } catch (error) {
    if (ticket !== loading) return;
    const e = error as Error;
    status('error', e instanceof FilingError ? '无法读取这个文件' : '读取时出错', e.message || String(e));
  }
}

async function openFile(file: File) {
  if (file.size > 30 * 1024 * 1024) { status('error', '无法读取这个文件', '文件超过 30 MB 上限。'); return; }
  const bytes = new Uint8Array(await file.arrayBuffer());
  await openBytes(bytes, { kind: 'file', fileName: file.name, accession: null, documentUrl: null });
}

async function openGallery(entry: GalleryEntry) {
  status('busy', `正在载入 ${entry.name}…`, `压缩后 ${(entry.gz_bytes / 1024 / 1024).toFixed(1)} MB`);
  try {
    if (typeof DecompressionStream === 'undefined') throw new Error('这个浏览器不支持 DecompressionStream，无法解压画廊文件；请用较新的浏览器，或直接拖入 EDGAR 文件。');
    const response = await fetch(new URL(entry.file, base));
    if (!response.ok || !response.body) throw new Error('载入失败：HTTP ' + response.status);
    const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
    const bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    await openBytes(bytes, { kind: 'gallery', fileName: entry.document, accession: entry.accession, documentUrl: entry.document_url, archiveUrl: entry.archive_url, captured: entry.capture_timestamp });
    if (state && state.source.sha256 !== entry.sha256) status('error', '画廊文件的哈希与登记不符', `登记 ${entry.sha256.slice(0, 12)}…，实际 ${state.source.sha256.slice(0, 12)}…`);
    try { history.replaceState(null, '', '#g=' + encodeURIComponent(entry.id)); } catch { /* sandboxed or file: */ }
  } catch (error) {
    status('error', '无法载入画廊文件', (error as Error).message);
  }
}

// ---------- render ----------
function factButton(t: Term | Fact, s: Snapshot, label?: string): HTMLElement {
  const index = 'fact' in t ? t.fact : t.i;
  const f = state?.byIndex.get(index);
  const display = f ? f.display : ('display' in t ? t.display : false);
  const text = label ?? amountText(t.value!, t.scale, s.meta.currency);
  if (!display) {
    const span = el('span', { class: 'sn-fig is-hidden', title: '这个标签在 ix:hidden 中，正文没有可见的单元格' }, text);
    return span;
  }
  const b = el('button', { type: 'button', class: 'sn-fig', 'data-fact': String(index), 'data-value': t.value!, 'data-scale': String(t.scale), 'aria-label': `${text}，定位到原文` }, text);
  b.addEventListener('click', () => select(index, b));
  return b;
}

function termList(input: Input, s: Snapshot): HTMLElement {
  const box = el('div', { class: 'sn-terms' });
  if (!input.terms.length) { box.append(el('span', { class: 'sn-missing' }, input.missing || '未找到')); return box; }
  input.terms.forEach((t, k) => {
    const row = el('div', { class: 'sn-term' });
    row.append(el('span', { class: 'sn-op' }, k === 0 && t.op === '+' ? '' : t.op));
    row.append(factButton(t, s));
    const meta = el('span', { class: 'sn-concept' }, t.concept.replace(/^us-gaap:/, ''));
    if (t.note) meta.append(el('small', {}, ' · ' + t.note));
    row.append(meta);
    box.append(row);
  });
  if (input.rule) box.append(el('p', { class: 'sn-rule' }, '规则：' + input.rule));
  return box;
}

function metricRow(m: Metric, s: Snapshot, opts: { big?: boolean } = {}): HTMLElement {
  const row = el('div', { class: 'sn-metric' + (opts.big ? ' big' : '') + (m.value === null ? ' na' : ''), 'data-metric': m.key });
  row.append(el('span', { class: 'sn-mlabel' }, m.label));
  const value = el('span', { class: 'sn-mvalue' }, metricText(m, s));
  if (m.value !== null) value.setAttribute('data-raw', String(m.value));
  row.append(value);
  row.append(el('code', { class: 'sn-formula' }, m.formula));
  if (m.reason) row.append(el('span', { class: 'sn-reason' }, m.reason));
  return row;
}

function inputBlock(input: Input | undefined, s: Snapshot, title?: string): HTMLElement {
  const box = el('div', { class: 'sn-input', 'data-input': input?.key || '' });
  if (!input) return box;
  const head = el('div', { class: 'sn-ihead' });
  head.append(el('span', {}, title || input.label));
  head.append(el('strong', {}, input.value === null ? '未找到' : amountText(input.value, s.meta.display_scale, s.meta.currency)));
  box.append(head, termList(input, s));
  return box;
}

function group(title: string, accent: string, children: HTMLElement[]): HTMLElement {
  const section = el('section', { class: 'sn-group ' + accent });
  section.append(el('h3', {}, title), ...children);
  return section;
}

function ladderChart(s: Snapshot): HTMLElement {
  const box = el('div', { class: 'sn-ladder' });
  const l = s.ladder!;
  const n = (x: string | null) => x === null ? 0 : Number(x);
  const liq = s.inputs.liquidity?.value ?? null;
  const fcf = s.metrics.fcf?.value as string | null;
  const annual = (s.meta.months ?? 0) >= 12;
  const bars: [string, string | null][] = [[l.next12_label, l.next12.value], ['第 2–5 年', l.years2to5.value], ['5 年以后', l.after5.value]];
  const lines: [string, number, string][] = [];
  if (liq !== null) lines.push(['现金与短期投资', n(liq), 'liq']);
  if (liq !== null && fcf !== null && annual) lines.push(['＋一年自由现金流', n(liq) + n(fcf), 'liqfcf']);
  const max = Math.max(1, ...bars.map(b => n(b[1])), ...lines.map(x => x[1]));
  const W = 420, H = 170, top = 14, bottom = 26, left = 6, bw = 92, gap = 46;
  const scaleY = (v: number) => H - bottom - (Math.max(0, v) / max) * (H - top - bottom);
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'sn-ladder-svg', role: 'img', 'aria-label': '债务到期结构柱状图' });
  bars.forEach(([label, value], k) => {
    const x = left + k * (bw + gap);
    if (value !== null) chart.append(svg('rect', { x, y: scaleY(n(value)), width: bw, height: Math.max(1, H - bottom - scaleY(n(value))), rx: 2, class: 'bar' }));
    else chart.append(svg('rect', { x, y: H - bottom - 2, width: bw, height: 2, class: 'bar none' }));
    chart.append(svg('text', { x: x + bw / 2, y: H - 8, 'text-anchor': 'middle', class: 'lab' }, label));
  });
  for (const [label, value, cls] of lines) {
    const y = scaleY(value);
    chart.append(svg('line', { x1: 0, x2: W, y1: y, y2: y, class: 'ref ' + cls }));
    chart.append(svg('text', { x: W - 4, y: y - 4, 'text-anchor': 'end', class: 'reflab ' + cls }, label));
  }
  box.append(chart);
  const legend = el('div', { class: 'sn-ladder-rows' });
  for (const [title, input] of [[l.next12_label, l.next12], ['第 2–5 年' + (l.missing_years.length ? `（缺第 ${l.missing_years.join('、')} 年）` : ''), l.years2to5], ['5 年以后', l.after5]] as [string, Input][]) {
    legend.append(inputBlock(input, s, title));
  }
  box.append(legend);
  box.append(el('p', { class: 'sn-note' }, '到期表是本金（附注），资产负债表债务是账面值，两者可能不同。参考线：现金与短期投资' + (annual ? '，以及再加一年自由现金流' : '') + '。'));
  return box;
}

function render(parsed: Parsed, s: Snapshot, source: SourceInfo, html: string) {
  const byIndex = new Map(parsed.facts.map(f => [f.i, f] as [number, Fact]));
  const frame = $('snFrame') as HTMLIFrameElement;
  state = { parsed, snapshot: s, source, byIndex, frame, frameDoc: null, selected: null };
  const result = $('snResult');
  result.hidden = false;
  result.dataset.status = s.status;

  // company header
  const head = $('snCompany');
  head.replaceChildren();
  head.append(el('h2', {}, s.meta.registrant || '（未标注公司名）'));
  const facts = el('p', { class: 'sn-meta' });
  const bits = [s.meta.form, `期末 ${s.meta.period_end}`, s.meta.months ? `流量 ${s.meta.months} 个月` : '', `CIK ${s.meta.cik || '—'}`, s.meta.ticker ? `代码 ${s.meta.ticker}` : '',
    source.accession ? `accession ${source.accession}` : 'accession 未提供', `金额单位 ${unitWord(s.meta.currency, s.meta.display_scale)}`];
  for (const b of bits.filter(Boolean)) facts.append(el('span', {}, b));
  head.append(facts);
  const prov = el('p', { class: 'sn-prov' });
  prov.append(el('span', {}, `${source.kind === 'gallery' ? '画廊文件' : '你拖入的文件'}：${source.fileName}（${source.bytes.toLocaleString('en-US')} 字节，SHA-256 ${source.sha256.slice(0, 16)}…）`));
  if (source.documentUrl) { const a = el('a', { href: source.documentUrl, rel: 'noopener noreferrer', target: '_blank' }, 'SEC 原文'); prov.append(' · ', a); }
  if (source.archiveUrl) { const a = el('a', { href: source.archiveUrl, rel: 'noopener noreferrer', target: '_blank' }, `档案抓取 ${source.captured || ''}`); prov.append(' · ', a); }
  head.append(prov);

  // cues
  const cues = $('snCues');
  cues.replaceChildren();
  if (s.status !== 'ok') {
    const box = el('div', { class: 'sn-unsupported', role: 'note' });
    box.append(el('strong', {}, '模板不支持'), el('p', {}, s.template.reason));
    if (s.template.facts.length) {
      const row = el('p', { class: 'sn-trigger' }, '触发判断的标签：');
      for (const i of s.template.facts) { const f = byIndex.get(i)!; row.append(factButton(f, s), el('span', { class: 'sn-concept' }, f.name + ' ')); }
      box.append(row);
    }
    cues.append(box);
  } else if (s.cues.length) {
    cues.append(el('h3', {}, `复核提示（${s.cues.length}）`));
    const list = el('ul', { class: 'sn-cue-list' });
    for (const c of s.cues) {
      const li = el('li', { 'data-cue': c.id });
      li.append(el('b', {}, c.id), el('span', {}, c.text));
      const shown = c.facts.filter(i => byIndex.get(i)?.display).slice(0, 6);
      for (const i of shown) { const f = byIndex.get(i)!; const b = factButton(f, s); b.title = f.name; li.append(b); }
      list.append(li);
    }
    cues.append(list, el('p', { class: 'sn-note' }, '复核提示只说明该看哪里，不是结论，也不是评级。'));
  }

  // metric groups
  const panels = $('snPanels');
  panels.replaceChildren();
  if (s.status === 'ok') {
    const m = s.metrics, i = s.inputs;
    panels.append(group('杠杆', 'g-lev', [metricRow(m.debt_to_ebitda, s, { big: true }), metricRow(m.net_debt_to_ebitda, s), inputBlock(i.debt, s), metricRow(m.net_debt, s),
      el('h4', {}, 'EBITDA 的构成'), metricRow(m.ebitda, s), inputBlock(i.ebit, s), inputBlock(i.da, s)]));
    panels.append(group('利息覆盖', 'g-cov', [metricRow(m.interest_coverage, s, { big: true }), inputBlock(i.interest, s)]));
    panels.append(group('流动性', 'g-liq', [metricRow(m.current_ratio, s, { big: true }), inputBlock(i.liquidity, s), inputBlock(i.current_debt, s), inputBlock(i.assets_current, s), inputBlock(i.liabilities_current, s)]));
    panels.append(group('自由现金流', 'g-fcf', [metricRow(m.fcf_to_debt, s, { big: true }), metricRow(m.fcf, s), inputBlock(i.cfo, s), inputBlock(i.capex, s)]));
    panels.append(group('债务到期结构', 'g-lad', s.ladder ? [ladderChart(s)] : [el('p', { class: 'sn-missing' }, '没有找到债务到期标签（LongTermDebtMaturitiesRepaymentsOfPrincipal…）。')]));
    panels.append(group('租赁负债（单列，不计入总债务）', 'g-lease', [inputBlock(i.operating_leases, s), inputBlock(i.finance_leases, s), metricRow(m.debt_with_leases, s)]));
  }
  $('snExport').hidden = false;

  // filing frame
  const note = $('snFrameNote');
  note.textContent = '正在载入原文…';
  frame.onload = () => {
    if (!state || state.frame !== frame) return;
    state.frameDoc = frame.contentDocument;
    note.textContent = '已载入 · 点数字定位，或直接点原文中带下划线的数字';
    state.frameDoc?.addEventListener('click', event => {
      const target = (event.target as Element | null)?.closest?.('[data-clf],[data-clh]');
      if (!target) return;
      event.preventDefault();
      const i = Number(target.getAttribute('data-clf') ?? target.getAttribute('data-clh'));
      if (Number.isFinite(i)) select(i, null, false);
    });
    frame.dataset.ready = '1';
  };
  frame.dataset.ready = '0';
  frame.srcdoc = html;
  $('snReadout').replaceChildren(el('dt', {}, '读数'), el('dd', {}, '点任一数字查看它的 XBRL 概念、期间、上下文、scale 与 sign。'));
}

function occurrences(f: Fact): Fact[] {
  if (!state) return [f];
  const c = state.parsed.contexts[f.ctx];
  const same = (g: Fact) => g.name === f.name && g.value === f.value && g.unit === f.unit && (() => { const d = state!.parsed.contexts[g.ctx]; return d && d.instant === c?.instant && d.start === c?.start && d.end === c?.end && d.dims.length === c?.dims.length; })();
  return state.parsed.facts.filter(g => g.display && same(g));
}

function select(i: number, button: HTMLElement | null, scroll = true) {
  if (!state) return;
  const f = state.byIndex.get(i);
  if (!f) return;
  state.selected = i;
  document.querySelectorAll('.sn-fig[aria-pressed="true"]').forEach(b => b.setAttribute('aria-pressed', 'false'));
  document.querySelectorAll(`.sn-fig[data-fact="${i}"]`).forEach(b => b.setAttribute('aria-pressed', 'true'));
  const doc = state.frame.contentDocument;
  if (doc) {
    doc.querySelectorAll('.cl-hit').forEach(n => n.classList.remove('cl-hit'));
    const target = f.hidden ? doc.querySelector(`[data-clh="${i}"]`) : doc.querySelector(`[data-clf="${i}"]`);
    if (target) {
      target.classList.add('cl-hit');
      if (scroll) {
        if (narrow.matches) sheet(true);
        else {
          const frameBox = state.frame.getBoundingClientRect();
          if (frameBox.top < 0 || frameBox.bottom > innerHeight) state.frame.scrollIntoView({ block: 'nearest' });
        }
        // Scroll only the frame's own document, so the page itself does not jump.
        const win = doc.defaultView, r = target.getBoundingClientRect();
        if (win) win.scrollBy({ top: r.top - win.innerHeight / 2 + r.height / 2, left: r.left < 0 || r.right > win.innerWidth ? r.left - 24 : 0, behavior: 'instant' as ScrollBehavior });
      }
    }
  }
  readout(f);
  const st = $('snSr');
  st.textContent = `已定位 ${f.name}，${amountText(f.value ?? '0', f.scale, state.snapshot.meta.currency)}`;
  if (button) button.focus();
}

const narrow = matchMedia('(max-width: 1080px)');
function sheet(open: boolean) {
  const viewer = document.querySelector('.sn-viewer');
  viewer?.classList.toggle('open', open);
  const toggle = $('snSheet');
  toggle.setAttribute('aria-expanded', String(open));
  toggle.textContent = open ? '收起' : '展开';
}

function readout(f: Fact) {
  const s = state!.snapshot, c = state!.parsed.contexts[f.ctx];
  const dl = $('snReadout');
  dl.replaceChildren();
  const row = (k: string, v: string, cls = '') => { dl.append(el('dt', {}, k)); dl.append(el('dd', cls ? { class: cls } : {}, v)); };
  row('概念', f.name);
  row('数值', f.value === null ? '（未解析）' : `${amountText(f.value, f.scale, s.meta.currency)} = ${inScale(f.value, 0)}`, 'value');
  row('原文文字', f.text || '（空）');
  row('期间', c ? (c.instant ? `时点 ${c.instant}` : `${c.start} 至 ${c.end}`) : f.ctx);
  row('上下文', `${f.ctx}${c && c.dims.length ? ' · ' + c.dims.map(d => d.join('=')).join('; ') : ' · 合并口径（无维度）'}`);
  row('单位', state!.parsed.units[f.unit] || f.unit);
  row('decimals／scale', `${f.decimals ?? '—'} ／ ${f.scale}`);
  row('sign', f.sign === '-' ? '-（标签值取负）' : '无');
  row('格式', f.format || '（无）');
  const a = anchorOf({ id: f.id, fact: f.i }, state!.source);
  row('锚点', a.label + (f.hidden ? '（ix:hidden，按 -sec-ix-hidden 显示处定位）' : ''));
  const occ = occurrences(f);
  if (occ.length > 1) {
    dt('出现', dl);
    const dd = el('dd');
    const at = occ.findIndex(g => g.i === f.i);
    dd.append(`${at + 1} / ${occ.length} 处 `);
    const next = el('button', { type: 'button', class: 'sn-next' }, '下一处 ›');
    next.addEventListener('click', () => select(occ[(at + 1) % occ.length].i, null));
    dd.append(next);
    dl.append(dd);
  }
  if (a.url) { dt('SEC 原文', dl); const dd = el('dd'); dd.append(el('a', { href: a.url, target: '_blank', rel: 'noopener noreferrer' }, '在 SEC 打开这一格')); dl.append(dd); }
}
const dt = (text: string, dl: HTMLElement) => dl.append(el('dt', {}, text));

// ---------- export ----------
function download(name: string, data: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = el('a', { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
function exportAs(kind: 'md' | 'csv' | 'docx') {
  if (!state) return;
  const ctx = { snapshot: state.snapshot, source: state.source, generatedAt: new Date().toISOString().replace(/\.\d+Z$/, 'Z'), appVersion: APP_VERSION };
  const name = exportBaseName(state.snapshot);
  if (kind === 'md') download(name + '.md', memoMarkdown(ctx), 'text/markdown;charset=utf-8');
  else if (kind === 'csv') download(name + '.csv', snapshotCsv(ctx), 'text/csv;charset=utf-8');
  else download(name + '.docx', memoDocx(ctx) as BlobPart, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  $('snSr').textContent = '已生成下载文件';
}

// ---------- gallery ----------
function galleryTable(g: Gallery): HTMLElement {
  const wrap = el('div', { class: 'sn-gwrap' });
  const table = el('table', { class: 'sn-gtable' });
  const head = el('thead'); const hr = el('tr');
  for (const h of ['公司', '行业层', '债务／EBITDA', '利息覆盖', '流动比率', 'FCF／债务', '提示', '']) hr.append(el('th', { scope: 'col' }, h));
  head.append(hr); table.append(head);
  const body = el('tbody');
  const fmt = (x: number | null, kind: 'x' | 'r' | 'p') => x === null ? '—' : kind === 'x' ? `${x.toFixed(2)}×` : kind === 'p' ? `${(x * 100).toFixed(1)}%` : x.toFixed(2);
  for (const e of g.entries) {
    const tr = el('tr', { 'data-gallery': e.id });
    const th = el('th', { scope: 'row' }); th.append(el('span', {}, e.name)); th.append(el('small', {}, `${e.ticker ? e.ticker + ' · ' : ''}${e.form} · ${e.period}`));
    tr.append(th);
    tr.append(el('td', { 'data-label': '行业层' }, e.stratum_label.replace(/\s*\(.*\)$/, '')));
    if (!e.snapshot) {
      tr.append(el('td', { colspan: '5', class: 'na', 'data-label': '状态' }, '尚未计算'));
    } else if (e.snapshot.status !== 'ok') {
      const td = el('td', { colspan: '5', class: 'na', 'data-label': '状态' }, '模板不支持（' + ({ bank: '银行', insurer: '保险', investment_company: '投资公司' } as Record<string, string>)[e.snapshot.template] + '）');
      tr.append(td);
    } else {
      tr.append(el('td', { 'data-label': '债务／EBITDA' }, fmt(e.snapshot.debt_to_ebitda, 'x')));
      tr.append(el('td', { 'data-label': '利息覆盖' }, fmt(e.snapshot.interest_coverage, 'x')));
      tr.append(el('td', { 'data-label': '流动比率' }, fmt(e.snapshot.current_ratio, 'r')));
      tr.append(el('td', { 'data-label': 'FCF／债务' }, fmt(e.snapshot.fcf_to_debt, 'p')));
      tr.append(el('td', { 'data-label': '提示', class: 'cues' }, e.snapshot.cues.join(' ') || '—'));
    }
    const td = el('td', { class: 'open' });
    const b = el('button', { type: 'button', class: 'sn-open' }, '打开');
    b.addEventListener('click', () => { openGallery(e); $('snResultAnchor').scrollIntoView({ block: 'start' }); });
    td.append(b); tr.append(td);
    body.append(tr);
  }
  table.append(body); wrap.append(table);
  return wrap;
}

async function loadGallery() {
  const box = $('snGalleryTable');
  try {
    const r = await fetch(new URL('gallery.json', base), { cache: 'no-cache' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    gallery = await r.json() as Gallery;
    box.replaceChildren(galleryTable(gallery));
    const supported = gallery.entries.filter(e => e.snapshot?.status === 'ok').length;
    $('snGalleryNote').textContent = `${gallery.entries.length} 家，按预先提交的规则（${gallery.rule_id}）从 8 个行业层各取 5 家；${supported} 家可计算，${gallery.entries.length - supported} 家为不支持的模板。表中数字是同一程序在 GitHub Actions 上对这些文件的计算结果（运行 ${gallery.run}），打开后在你的浏览器里重新计算。`;
    const want = /#g=([^&]+)/.exec(location.hash);
    if (want) { const e = gallery.entries.find(x => x.id === decodeURIComponent(want[1])); if (e) openGallery(e); }
  } catch (error) {
    box.replaceChildren(el('p', { class: 'sn-missing' }, '画廊暂不可用（' + (error as Error).message + '）。拖入文件的功能不受影响。'));
  }
}

// ---------- wiring ----------
function init() {
  const drop = $('snDrop'), input = $('snFile') as HTMLInputElement;
  input.addEventListener('change', () => { const f = input.files?.[0]; if (f) openFile(f); input.value = ''; });
  drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } });
  drop.addEventListener('click', e => { if ((e.target as HTMLElement).closest('label,input')) return; input.click(); });
  for (const type of ['dragenter', 'dragover']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add('over'); });
  for (const type of ['dragleave', 'drop']) drop.addEventListener(type, () => drop.classList.remove('over'));
  drop.addEventListener('drop', e => {
    e.preventDefault();
    const files = (e as DragEvent).dataTransfer?.files;
    if (!files || !files.length) { status('error', '没有收到文件', '请从文件夹拖入 .htm 文件，或点“选择文件”。'); return; }
    if (files.length > 1) { status('error', '一次只读一个文件', '请只拖入一份 10-K 或 10-Q 主文件。'); return; }
    openFile(files[0]);
  });
  // A file dropped outside the drop zone must not navigate away from the page.
  window.addEventListener('dragover', e => e.preventDefault());
  window.addEventListener('drop', e => { if (!(e.target as HTMLElement).closest?.('#snDrop')) e.preventDefault(); });
  $('snSheet').addEventListener('click', () => sheet(!document.querySelector('.sn-viewer')?.classList.contains('open')));
  $('snExportMd').addEventListener('click', () => exportAs('md'));
  $('snExportCsv').addEventListener('click', () => exportAs('csv'));
  $('snExportDocx').addEventListener('click', () => exportAs('docx'));
  loadGallery();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

// Test hook for the Actions checks (read-only view of what the page computed).
(window as unknown as { __creditSnapshot: () => unknown }).__creditSnapshot = () => state && { snapshot: state.snapshot, source: state.source, facts: state.parsed.facts.map(f => ({ ...f })), contexts: state.parsed.contexts, units: state.parsed.units };

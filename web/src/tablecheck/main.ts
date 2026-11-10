/* DOM layer for creditlens/table-check.html (截图核对). All text is set with textContent. The OCR
   files download only after the visitor clicks; nothing is sent anywhere — the image stays in the tab. */
import { canRun, DOWNLOAD_BYTES, type Engine, LoadError, loadEngine, recognise, RUNTIME_FILES, WEIGHT_FILES } from './ocr';
import { analyse, type Check, type Table } from './table';

const $ = (id: string) => document.getElementById(id) as HTMLElement;
const SVG = 'http://www.w3.org/2000/svg';
const MB = (n: number) => `${(n / 1e6).toFixed(1)} MB`;
const STATUS_TEXT: Record<string, string> = { foots: '相符', does_not_foot: '不相符', not_checked: '未核对' };
const KIND_TEXT: Record<string, string> = { total: '合计行', cross: '横向合计', change: '同比变动' };
const REASON_TEXT: Record<string, string> = {
  unreadable_cell: '所需的格没有读出完整数字', percent_cell: '所需的格是百分比', depends_on_unfooted_subtotal: '依赖的小计未相符或未核对',
  fewer_than_two_terms: '上方可加总的行少于 2 行', missing_cell: '上方某行在本列没有读到数字（同行其他列有）', missing_year_cell: '年份列的格没有读出', prior_is_zero: '上年为零，不能算百分比',
};
const LOAD_TEXT: Record<string, string> = {
  http: '下载失败（服务器返回错误）', hash: '文件校验不符，已拒绝运行', network: '网络中断', runtime: '运行时无法启动', cancelled: '已取消',
};

interface Result {
  width: number; height: number; table: Table; checks: Check[];
  timings: { decode: number; det: number; rec: number; structure: number; total: number; lines: number; boxes: number };
}
const state: { engine: Engine | null; loading: AbortController | null; result: Result | null; selected: number | null; imageUrl: string | null; error: string | null } =
  { engine: null, loading: null, result: null, selected: null, imageUrl: null, error: null };

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
function say(text: string) { $('tcStatus').textContent = text; }

async function toCanvas(blob: Blob): Promise<HTMLCanvasElement> {
  const bitmap = await createImageBitmap(blob);
  const scale = Math.min(1, 4000 / Math.max(bitmap.width, bitmap.height));
  const c = document.createElement('canvas');
  c.width = Math.round(bitmap.width * scale); c.height = Math.round(bitmap.height * scale);
  const ctx = c.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, c.width, c.height);
  ctx.drawImage(bitmap, 0, 0, c.width, c.height);
  bitmap.close();
  return c;
}

export async function runBlob(blob: Blob): Promise<Result> {
  if (!state.engine) throw new Error('model not loaded');
  const t0 = performance.now();
  const source = await toCanvas(blob);
  const t1 = performance.now();
  const { lines, timings } = await recognise(state.engine, source);
  const t2 = performance.now();
  const { table, checks } = analyse(lines);
  const t3 = performance.now();
  return { width: source.width, height: source.height, table, checks,
    timings: { decode: (t1 - t0) / 1000, det: timings.det, rec: timings.rec, structure: (t3 - t2) / 1000, total: (t3 - t0) / 1000,
      lines: timings.lines, boxes: timings.boxes } };
}

/** SYNTHETIC variants used by the Actions evaluation (and the example button). */
export async function variant(url: string, kind: 'clean' | 'webp_grey_q68' | 'jpeg_q60' | 'blur_1px'): Promise<Blob> {
  const blob = await (await fetch(url)).blob();
  if (kind === 'clean') return blob;
  const c = await toCanvas(blob);
  const out = document.createElement('canvas');
  out.width = c.width; out.height = c.height;
  const ctx = out.getContext('2d') as CanvasRenderingContext2D;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, out.width, out.height);
  if (kind === 'blur_1px') ctx.filter = 'blur(1px)';
  if (kind === 'webp_grey_q68') ctx.filter = 'grayscale(1)';
  ctx.drawImage(c, 0, 0);
  const type = kind === 'jpeg_q60' ? 'image/jpeg' : kind === 'webp_grey_q68' ? 'image/webp' : 'image/png';
  const quality = kind === 'jpeg_q60' ? 0.6 : kind === 'webp_grey_q68' ? 0.68 : undefined;
  return new Promise((resolve, reject) => out.toBlob((b) => (b ? resolve(b) : reject(new Error('encode'))), type, quality));
}

/** A small SYNTHETIC statement drawn in the tab, with one subtotal that does not foot. */
export function exampleBlob(): Promise<Blob> {
  const c = document.createElement('canvas');
  c.width = 900; c.height = 420;
  const ctx = c.getContext('2d') as CanvasRenderingContext2D;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, c.width, c.height);
  ctx.fillStyle = '#111111';
  ctx.font = 'bold 20px Arial, Helvetica, sans-serif';
  ctx.fillText('LEON SAMPLE CO. (SYNTHETIC) - BALANCE SHEET', 30, 40);
  ctx.font = '18px Arial, Helvetica, sans-serif';
  ctx.fillText('(In thousands)', 30, 68);
  const rows: [string, string, string, number][] = [
    ['', '2025', '2024', 0], ['Cash and cash equivalents', '1,240', '1,105', 1], ['Accounts receivable, net', '615', '590', 1],
    ['Inventories', '388', '402', 1], ['Total current assets', '2,253', '2,097', 2], ['Property and equipment, net', '1,870', '1,764', 0],
    ['Total assets', '4,113', '3,861', 2],
  ];
  rows.forEach(([label, a, b, indent], i) => {
    const y = 110 + i * 40;
    ctx.textAlign = 'left'; ctx.fillText(label, 30 + indent * 14, y);
    ctx.textAlign = 'right'; ctx.fillText(a, 690, y); ctx.fillText(b, 850, y);
  });
  return new Promise((resolve, reject) => c.toBlob((b) => (b ? resolve(b) : reject(new Error('encode'))), 'image/png'));
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render(result: Result, imageUrl: string) {
  const stage = $('tcSheet');
  stage.replaceChildren();
  const img = el('img', { src: imageUrl, alt: '上传的表格截图（只在本页处理）' });
  const overlay = svg('svg', { class: 'tc-overlay', viewBox: `0 0 ${result.width} ${result.height}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
  stage.append(img, overlay);
  const { table, checks } = result;
  const flagged = new Set(checks.filter((c) => c.status === 'does_not_foot').map((c) => c.row));
  for (const r of table.rows) {
    if (flagged.has(r.index)) overlay.append(svg('rect', { x: 0, y: r.y0 - 2, width: result.width, height: r.y1 - r.y0 + 4, class: 'tc-row-bad' }));
  }
  const sel = state.selected === null ? null : checks[state.selected];
  const termIds = new Set(sel ? sel.terms.map((t) => t.token) : []);
  for (const t of table.tokens) {
    if (t.kind !== 'amount' && t.kind !== 'dash' && t.kind !== 'garbled') continue;
    const cls = sel && sel.target === t.id ? `tc-box target ${sel.status}` : termIds.has(t.id) ? 'tc-box term' : t.kind === 'garbled' ? 'tc-box garbled' : 'tc-box';
    overlay.append(svg('rect', { x: t.box.x0 - 2, y: t.box.y0 - 1, width: t.box.x1 - t.box.x0 + 4, height: t.box.y1 - t.box.y0 + 2, class: cls }));
  }
  const counts = { foots: 0, does_not_foot: 0, not_checked: 0 } as Record<string, number>;
  checks.forEach((c) => { counts[c.status] += 1; });
  const numbers = table.tokens.filter((t) => t.kind === 'amount' || t.kind === 'dash').length;
  $('tcSummary').textContent = `读到 ${numbers} 个数字、${table.rows.length} 行、${table.columns.length} 列；核对 ${checks.length} 条：不相符 ${counts.does_not_foot}，相符 ${counts.foots}，未核对 ${counts.not_checked}。用时 ${result.timings.total.toFixed(1)} 秒。`;
  const list = $('tcChecks');
  list.replaceChildren();
  const order = [...checks].sort((a, b) => ['does_not_foot', 'not_checked', 'foots'].indexOf(a.status) - ['does_not_foot', 'not_checked', 'foots'].indexOf(b.status));
  for (const c of order) {
    const row = table.rows[c.row];
    const col = table.columns[c.column];
    const li = el('li');
    const button = el('button', { type: 'button', class: `tc-check ${c.status}`, 'aria-pressed': String(state.selected === c.id) });
    button.append(el('span', { class: 'tc-check-status' }, STATUS_TEXT[c.status]),
      el('span', { class: 'tc-check-label' }, `${KIND_TEXT[c.kind]} · ${row.fullLabel || '（无行名）'}${col?.year ? ` · ${col.year}` : col?.header ? ` · ${col.header}` : ''}`));
    let detail = '';
    if (c.status === 'not_checked') detail = REASON_TEXT[c.reason ?? ''] ?? '未核对';
    else if (c.kind === 'change') detail = `按读数重算：${c.formula} = ${c.computed}；表中读到 ${c.read}`;
    else detail = `按读数加总：${c.formula} = ${c.computed}；表中读到 ${c.read}${c.status === 'does_not_foot' ? `，相差 ${c.difference}` : ''}`;
    button.append(el('span', { class: 'tc-check-formula' }, detail));
    button.addEventListener('click', () => { state.selected = state.selected === c.id ? null : c.id; render(result, imageUrl); });
    li.append(button);
    list.append(li);
  }
  if (!checks.length) list.append(el('li', { class: 'tc-empty' }, '没有找到可以核对的合计行、合计列或同比列；数字已全部框出。'));
  $('tcResult').hidden = false;
}

async function handle(blob: Blob) {
  if (!state.engine) return;
  if (blob.size > 25e6) { say('图片超过 25 MB，请截取表格部分。'); return; }
  if (state.imageUrl) URL.revokeObjectURL(state.imageUrl);
  state.imageUrl = URL.createObjectURL(blob);
  state.selected = null;
  say('正在识别…（单线程 WebAssembly，整页报表约需十几秒）');
  $('tcRun').setAttribute('aria-busy', 'true');
  try {
    state.result = await runBlob(blob);
    render(state.result, state.imageUrl);
    say('识别完成。');
  } catch (error) {
    say(`无法读取这张图片：${String((error as Error).message ?? error).slice(0, 120)}`);
  } finally {
    $('tcRun').removeAttribute('aria-busy');
  }
}

async function load() {
  if (state.engine || state.loading) return;
  state.loading = new AbortController();
  const button = $('tcLoad') as HTMLButtonElement;
  button.disabled = true;
  $('tcCancel').hidden = false;
  const bar = $('tcProgress') as HTMLProgressElement;
  bar.hidden = false;
  try {
    state.engine = await loadEngine((loaded, total, file) => { bar.max = total; bar.value = loaded; say(`正在下载 ${file}：${MB(loaded)} / ${MB(total)}`); }, state.loading.signal);
    say(`识别模型已就绪（初始化 ${state.engine.initSeconds.toFixed(1)} 秒）。粘贴、拖入或选择一张表格截图。`);
    $('tcRun').hidden = false;
    $('tcLoadPanel').classList.add('done');
    button.textContent = '识别模型已加载';
  } catch (error) {
    const code = error instanceof LoadError ? error.code : 'runtime';
    state.error = String((error as Error)?.message ?? error).slice(0, 500);
    say(`${LOAD_TEXT[code]}${error instanceof LoadError && code !== 'cancelled' ? `：${error.file}` : ''}。可以重试。`);
    button.disabled = false;
  } finally {
    state.loading = null;
    $('tcCancel').hidden = true;
    bar.hidden = true;
  }
}

function init() {
  const support = canRun();
  const files = [...RUNTIME_FILES, ...WEIGHT_FILES];
  $('tcFiles').replaceChildren(...files.map(([name, f]) => el('li', {}, `${name} · ${MB(f.bytes)}`)));
  $('tcLoad').textContent = `加载识别模型（约 ${MB(DOWNLOAD_BYTES)}）`;
  if (!support.ok) {
    $('tcControls').hidden = true;
    $('tcUnsupported').hidden = false;
    $('tcUnsupported').textContent = {
      no_wasm: '这个浏览器不支持 WebAssembly，截图核对无法运行。',
      no_simd: '这个浏览器不支持 WebAssembly SIMD，截图核对无法运行。请使用较新的 Chrome、Edge、Firefox 或 Safari。',
      insecure: '页面不在安全连接（HTTPS）下，无法校验模型文件，截图核对不运行。',
      no_canvas: '这个浏览器不能在页面里处理图片，截图核对无法运行。',
    }[support.reason ?? 'no_wasm'];
    return;
  }
  $('tcLoad').addEventListener('click', () => { void load(); });
  $('tcCancel').addEventListener('click', () => state.loading?.abort());
  const input = $('tcFile') as HTMLInputElement;
  input.addEventListener('change', () => { const f = input.files?.[0]; if (f) void handle(f); input.value = ''; });
  $('tcExample').addEventListener('click', () => { void exampleBlob().then(handle); });
  const drop = $('tcDrop');
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault(); drop.classList.remove('over');
    const f = e.dataTransfer?.files?.[0];
    if (f && f.type.startsWith('image/')) void handle(f);
  });
  document.addEventListener('paste', (e) => {
    const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith('image/'));
    const f = item?.getAsFile();
    if (f && state.engine) void handle(f);
  });
}

// Test hook for the Actions evaluation, check and probe jobs (no network beyond the pinned files).
(window as unknown as Record<string, unknown>).__tableCheck = {
  canRun, load: () => load().then(() => ({ ok: Boolean(state.engine), initSeconds: state.engine?.initSeconds, downloadedBytes: state.engine?.downloadedBytes, error: state.error })),
  runBlob, variant, exampleBlob,
  runUrl: async (url: string, kind: 'clean' | 'webp_grey_q68' | 'jpeg_q60' | 'blur_1px' = 'clean', show = false) => {
    const blob = await variant(url, kind);
    const r = await runBlob(blob);
    if (show) { if (state.imageUrl) URL.revokeObjectURL(state.imageUrl); state.imageUrl = URL.createObjectURL(blob); state.result = r; state.selected = null; render(r, state.imageUrl); }
    return r;
  },
  example: async () => { const b = await exampleBlob(); await handle(b); return state.result; },
};

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

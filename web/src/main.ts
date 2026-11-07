/* DOM layer for creditlens/ask.html. All text is set with textContent; nothing the visitor
   types is ever parsed as HTML. The pack is fetched next to this script (assets/ask-pack.json). */
import { compose, type Card, type Readout } from './compose';
import type { Box, Pack } from './types';

const SVG = 'http://www.w3.org/2000/svg';
const script = document.currentScript as HTMLScriptElement | null;
const packUrl = new URL('ask-pack.json', script?.src ?? location.href);

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<Record<string, string>> = {}, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) if (v !== undefined) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

const STATUS: Record<string, [string, 'ok' | 'warn' | 'stop']> = {
  answered: ['已作答', 'ok'], computed: ['已计算', 'ok'], side_by_side_only: ['只能并列', 'warn'], blocked: ['不能计算', 'stop'],
  not_registered: ['未登记', 'stop'], unit_unavailable: ['单位不可用', 'stop'], scope_unavailable: ['口径未登记', 'stop'],
};

function preview(pack: Pack, r: Readout): HTMLElement | null {
  if (!r.preview) return null;
  const page = pack.pages[r.preview.key];
  if (!page) return null;
  const wrap = el('div', { class: 'ask-drawer' });
  wrap.append(el('p', {}, `${r.preview.key.replace(':', ' 第 ')} 页 · 重建示意：只按仓库中保存的识别片段绘制，不是原页图像。黄框为登记定位，虚线为 OCR 片段。核对原文请打开官方文件对应页。`));
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', `0 0 ${page.width} ${page.height}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `${r.issuer} ${r.label} 所在页的重建示意`);
  const boxes = [r.preview.amount, r.preview.ocr].filter(Boolean) as Box[];
  const top = Math.max(0, Math.min(...boxes.map(b => b.top), page.height) - 120);
  const bottom = Math.min(page.height, Math.max(...boxes.map(b => b.bottom), 0) + 120);
  if (boxes.length) svg.setAttribute('viewBox', `0 ${top} ${page.width} ${Math.max(bottom - top, 160)}`);
  const bg = document.createElementNS(SVG, 'rect');
  Object.entries({ x: '0', y: '0', width: String(page.width), height: String(page.height), fill: '#fbfaf6' }).forEach(([k, v]) => bg.setAttribute(k, v));
  svg.append(bg);
  for (const [text, x0, t, x1, b] of page.spans) {
    const h = Math.max(b - t, 4);
    const node = document.createElementNS(SVG, 'text');
    node.setAttribute('x', String(x0));
    node.setAttribute('y', String(b - h * 0.22));
    node.setAttribute('font-size', String(h * 0.74));
    node.setAttribute('textLength', String(Math.max(x1 - x0, 1)));
    node.setAttribute('lengthAdjust', 'spacingAndGlyphs');
    node.setAttribute('fill', '#25231d');
    node.setAttribute('font-family', 'Times New Roman, Times, Songti SC, serif');
    node.textContent = text;
    svg.append(node);
  }
  const mark = (box: Box | null, dashed: boolean) => {
    if (!box) return;
    const rect = document.createElementNS(SVG, 'rect');
    Object.entries({ x: String(box.x0 - 2), y: String(box.top - 2), width: String(box.x1 - box.x0 + 4), height: String(box.bottom - box.top + 4),
      fill: dashed ? 'none' : '#d5ac5f33', stroke: dashed ? '#5a48c2' : '#b07d22', 'stroke-width': dashed ? '1' : '1.4', rx: '1.5' })
      .forEach(([k, v]) => rect.setAttribute(k, v));
    if (dashed) rect.setAttribute('stroke-dasharray', '3 2');
    svg.append(rect);
  };
  mark(r.preview.amount, false);
  mark(r.preview.ocr, true);
  wrap.append(svg);
  return wrap;
}

function readoutNode(pack: Pack, r: Readout): HTMLElement {
  const box = el('section', { class: 'ask-readout' });
  box.append(el('h3', { class: 'ask-readout-title' }, `${r.issuer} · ${r.label} · ${r.period}`));
  const dl = el('dl');
  const row = (k: string, v: string, cls?: string) => { dl.append(el('dt', {}, k), el('dd', cls ? { class: cls } : {}, v)); };
  row('数值', r.value + (r.requested ? `  =  ${r.requested}` : ''), 'value');
  row('页码', r.page);
  row('定位框', `${r.bbox}（PDF 点，左上为原点）`);
  row('单位', r.unit);
  row('核对', r.outcome, r.outcome_tone);
  row('来源', r.source);
  box.append(dl);
  const drawer = preview(pack, r);
  if (drawer) {
    const toggle = el('button', { type: 'button', class: 'ask-preview-toggle', 'aria-expanded': 'false' }, '查看重建示意');
    drawer.hidden = true;
    toggle.addEventListener('click', () => {
      drawer.hidden = !drawer.hidden;
      toggle.setAttribute('aria-expanded', String(!drawer.hidden));
      toggle.textContent = drawer.hidden ? '查看重建示意' : '收起重建示意';
    });
    box.append(toggle, drawer);
  }
  return box;
}

function cardNode(pack: Pack, card: Card): HTMLElement {
  const node = el('article', { class: 'ask-card' });
  const head = el('div', { class: 'ask-card-head' });
  const [label, tone] = STATUS[card.status] ?? [card.status, 'stop'];
  head.append(el('span', { class: `ask-badge ${card.tone === 'stop' ? 'stop' : tone}` }, label), el('h2', {}, card.label));
  node.append(head, el('p', { class: 'ask-headline' }, card.headline));
  if (card.checks.length) {
    const ul = el('ul', { class: 'ask-checks', 'aria-label': '可比性与口径检查' });
    for (const c of card.checks) ul.append(el('li', { class: c.tone }, c.text));
    node.append(ul);
  }
  if (card.formula) node.append(el('p', { class: 'ask-formula' }, card.formula));
  if (card.rows.length) {
    const list = el('div', { class: 'ask-readouts' });
    for (const r of card.rows) list.append(readoutNode(pack, r));
    node.append(list);
  }
  for (const n of card.notes) node.append(el('p', { class: 'ask-card-notes' }, n));
  node.append(el('p', { class: 'ask-engine-line' }, card.engine));
  return node;
}

function nextList(questions: string[], ask: (q: string) => void, title: string): HTMLElement | null {
  if (!questions.length) return null;
  const wrap = el('div', { class: 'ask-next-wrap' });
  wrap.append(el('h3', {}, title));
  const ul = el('ul', { class: 'ask-next' });
  for (const q of questions) {
    const b = el('button', { type: 'button' }, q);
    b.addEventListener('click', () => ask(q));
    const li = el('li'); li.append(b); ul.append(li);
  }
  wrap.append(ul);
  return wrap;
}

function scope(pack: Pack): HTMLElement {
  const body = el('div');
  const table = el('table');
  const thead = el('thead'); const tr = el('tr');
  ['指标', '类型', '登记情况', '说明'].forEach(h => tr.append(el('th', {}, h))); thead.append(tr); table.append(thead);
  const tbody = el('tbody');
  for (const [id, m] of Object.entries(pack.metrics)) {
    const issuers = ['MSFT', 'AAPL'].filter(i => pack.observations.some(o => o.issuer_id === i && o.metric === id) || pack.percents.some(o => o.issuer_id === i && o.metric === id));
    const row = el('tr');
    row.append(el('td', {}, m.label), el('td', {}, m.kind === 'percent' ? '登记计算（%）' : m.kind === 'instant' ? '财年末余额' : '财年发生额'),
      el('td', {}, issuers.map(i => (i === 'MSFT' ? '微软' : '苹果')).join('、') + ' · FY2024、FY2025'), el('td', {}, m.note ?? ''));
    tbody.append(row);
  }
  table.append(tbody);
  const ul = el('ul');
  for (const l of pack.limitations) ul.append(el('li', {}, l));
  ul.append(el('li', {}, '本页只读：不保存问题，不写入研究任务或复核记录。'));
  body.append(ul, table);
  return body;
}

async function boot() {
  const form = document.getElementById('askForm') as HTMLFormElement;
  const input = document.getElementById('question') as HTMLInputElement;
  const out = document.getElementById('askOut') as HTMLElement;
  const status = document.getElementById('askStatus') as HTMLElement;
  let pack: Pack;
  try {
    const response = await fetch(packUrl, { cache: 'no-cache' });
    if (!response.ok) throw new Error(String(response.status));
    pack = await response.json();
  } catch {
    status.textContent = '登记材料载入失败，无法作答。请刷新页面重试。';
    (document.getElementById('askButton') as HTMLButtonElement).disabled = true;
    return;
  }
  status.textContent = `已载入登记材料：${pack.observations.length} 个年度数字、${pack.percents.length} 个毛利率登记计算。`;
  document.getElementById('scopeBody')?.replaceWith(scope(pack));

  const ask = (question: string) => {
    input.value = question;
    const composed = compose(pack, question);
    out.replaceChildren();
    if (composed.type !== 'plans') {
      const box = el('section', { class: `ask-refusal ${composed.type === 'clarify' ? 'clarify' : ''}` });
      box.append(el('h2', {}, composed.type === 'clarify' ? '需要说明一下' : '不作答'));
      box.append(el('p', {}, composed.message ?? ''));
      if (composed.unknown.length) box.append(el('p', {}, '未识别：'), el('p', { class: 'ask-unknown' }, composed.unknown.join('  ·  ')));
      const next = nextList(composed.suggestions, ask, composed.type === 'clarify' ? '可以这样问：' : '可以试试：');
      if (next) box.append(next);
      out.append(box);
      status.textContent = composed.type === 'clarify' ? '需要补充信息。' : '没有作答，原因见下方。';
    } else {
      if (composed.reading) out.append(el('p', { class: 'ask-reading' }, `读作 · ${composed.reading}`));
      if (composed.notes.length) {
        const ul = el('ul', { class: 'ask-notes', 'aria-label': '理解说明' });
        for (const n of composed.notes) ul.append(el('li', {}, n));
        out.append(ul);
      }
      for (const card of composed.cards) out.append(cardNode(pack, card));
      const next = nextList(composed.suggestions, ask, '接着问：');
      if (next) out.append(next);
      status.textContent = `已作答：${composed.cards.length} 组结果，均在本标签页计算。`;
    }
    try { history.replaceState(null, '', `${location.pathname}?q=${encodeURIComponent(question)}`); } catch { /* file:// or sandboxed */ }
  };

  form.addEventListener('submit', event => { event.preventDefault(); ask(input.value); });
  document.querySelectorAll<HTMLButtonElement>('.ask-examples button').forEach(b => b.addEventListener('click', () => ask(b.dataset.q ?? '')));
  const initial = new URLSearchParams(location.search).get('q');
  if (initial) ask(initial.slice(0, 200));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { void boot(); }, { once: true });
else void boot();

/* Inline XBRL reader for the credit snapshot (docs/47-credit-snapshot.md §1).
   Deterministic: DOMParser builds an inert document (no scripts run, nothing loads), and every
   number comes from an ix:nonFraction tag: its printed text, ixt format, scale and sign. Each fact
   element is stamped with data-clf="<index>" so the sanitized copy shown in the sandboxed frame can
   be searched for exactly that cell. No model, no OCR, no network. */
import { parse as parseDec, scaleb, type Dec } from '../decimal';

export interface Context { id: string; dims: [string, string][]; instant?: string; start?: string; end?: string }
export interface Fact {
  i: number; name: string; ns: string; local: string; ctx: string; unit: string;
  decimals: string | null; scale: number; sign: '' | '-'; format: string; text: string;
  value: string | null; nil: boolean; id: string | null; table: number; hidden: boolean; display: boolean;
}
export interface Parsed {
  facts: Fact[]; contexts: Record<string, Context>; units: Record<string, string>;
  dei: Record<string, { text: string; ctx: string }>; namespaces: Record<string, string>;
  unparsed: number; nil: number; doc: Document;
}
export class FilingError extends Error {
  constructor(public code: 'empty' | 'too_large' | 'pdf' | 'zip' | 'binary' | 'viewer' | 'not_ixbrl' | 'no_contexts' | 'no_document_type', message: string) { super(message); }
}

export const MAX_BYTES = 30 * 1024 * 1024;
// tagName keeps the prefix in browsers (HTML parser: 'IX:NONFRACTION') and in test DOMs that split it off.
const lname = (el: Element) => (el.tagName || el.nodeName || '').toLowerCase();
const tail = (el: Element) => lname(el).split(':').pop() as string;
const clean = (s: string | null | undefined) => (s || '').replace(/[\s ]+/g, ' ').trim();

/** Refuse what is not an Inline XBRL HTML document before parsing it (cheap byte checks). */
export function sniff(bytes: Uint8Array): void {
  if (bytes.length === 0) throw new FilingError('empty', '文件是空的。');
  if (bytes.length > MAX_BYTES) throw new FilingError('too_large', `文件超过 ${MAX_BYTES / 1024 / 1024} MB 上限。`);
  const head = new TextDecoder('latin1').decode(bytes.subarray(0, 8));
  if (head.startsWith('%PDF')) throw new FilingError('pdf', '这是 PDF。本页读取 Inline XBRL 的 .htm 主文件，不读 PDF。');
  if (head.startsWith('PK\u0003\u0004')) throw new FilingError('zip', '这是压缩包。请解压后拖入其中的 10-K／10-Q 主文件（.htm）。');
  const probe = bytes.subarray(0, Math.min(bytes.length, 65536));
  let nul = 0;
  for (const b of probe) if (b === 0) nul++;
  if (nul > 0) throw new FilingError('binary', '这不是文本文件（含二进制内容）。');
}

const SMALL: Record<string, number> = { no: 0, none: 0, zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19,
  twenty: 20, thirty: 30, forty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };
/** ixt-sec:numwordsen for whole numbers written in English words ("twenty-one", "one hundred"). */
function wordsToNumber(text: string): string | null {
  const words = text.toLowerCase().replace(/-/g, ' ').replace(/\band\b/g, ' ').trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  let total = 0, current = 0;
  for (const w of words) {
    if (w in SMALL) current += SMALL[w];
    else if (w === 'hundred') current = (current || 1) * 100;
    else if (w === 'thousand' || w === 'million' || w === 'billion') { total += (current || 1) * (w === 'thousand' ? 1e3 : w === 'million' ? 1e6 : 1e9); current = 0; }
    else return null;
  }
  return String(total + current);
}

function parseNumber(text: string, format: string): string | null {
  const t = text.replace(/[\s ]+/g, '');
  switch (format) {
    case 'num-dot-decimal': case 'numdotdecimal': case 'numcommadot': {
      const v = t.replace(/,/g, '');
      return /^(\d+(\.\d*)?|\.\d+)$/.test(v) ? v : null;
    }
    case 'num-comma-decimal': case 'numcommadecimal': case 'numdotcomma': {
      const v = t.replace(/\./g, '').replace(/,/g, '.');
      return /^(\d+(\.\d*)?|\.\d+)$/.test(v) ? v : null;
    }
    case 'fixed-zero': case 'zerodash': case 'fixed-empty': case 'numdash':
      return '0';
    case 'numwordsen': case 'num-word-en':
      return wordsToNumber(text);
    case '':
      return /^-?(\d+(\.\d*)?|\.\d+)$/.test(t) ? t : null;
    default:
      return null;
  }
}

export function canon(d: Dec): string {
  let int = d.int, exp = d.exp;
  if (int === 0n) return '0';
  while (exp < 0 && int % 10n === 0n) { int /= 10n; exp++; }
  const neg = int < 0n; let s = (neg ? -int : int).toString();
  if (exp > 0) s += '0'.repeat(exp);
  else if (exp < 0) { s = s.padStart(-exp + 1, '0'); s = s.slice(0, s.length + exp) + '.' + s.slice(s.length + exp); }
  return (neg && s !== '0' ? '-' : '') + s;
}

/** The value a tagged cell stands for: printed text by format × 10^scale, negated for sign="-". */
export function factValue(text: string, format: string, scale: number, sign: string): string | null {
  const raw = parseNumber(text, format);
  if (raw === null) return null;
  let d = scaleb(parseDec(raw), scale);
  if (sign === '-') d = { int: -d.int, exp: d.exp };
  return canon(d);
}

const VIEWER = /ixviewer|ix\?doc=|id="app-inline-xbrl"|Inline XBRL Viewer/i;

export function parseFiling(text: string, Parser: { new(): DOMParser } = DOMParser): Parsed {
  if (!/<ix:header[\s>]/i.test(text) || !/<ix:nonfraction[\s>]/i.test(text)) {
    if (VIEWER.test(text.slice(0, 20000))) throw new FilingError('viewer', '这是 EDGAR 的 iXBRL 查看器页面，不是申报文件本身。请在查看器菜单里打开 “Open as HTML”，或打开 Archives 下的 .htm 主文件，再另存。');
    throw new FilingError('not_ixbrl', '没有找到 Inline XBRL 标签（ix:header／ix:nonFraction）。本页只读 2019 年以后 SEC 要求的 Inline XBRL 10-K／10-Q 主文件。');
  }
  // <base> would retarget relative URLs while parsing (and the page CSP refuses it); it is dropped anyway.
  const doc = new Parser().parseFromString(text.replace(/<base\b[^>]*>/gi, ''), 'text/html');
  const root = doc.documentElement;
  const namespaces: Record<string, string> = {};
  for (const el of [root, doc.body, ...Array.from(doc.getElementsByTagName('*')).filter(e => tail(e) === 'header' || tail(e) === 'xbrl')]) {
    if (!el) continue;
    for (const attr of Array.from(el.attributes)) {
      const n = attr.name.toLowerCase();
      if (n.startsWith('xmlns:')) namespaces[n.slice(6)] = attr.value.trim();
    }
  }
  const contexts: Record<string, Context> = {};
  const units: Record<string, string> = {};
  const all = Array.from(doc.getElementsByTagName('*'));
  // A file must not be able to plant its own anchors: drop any data-clf / data-clh it already carries.
  for (const el of all) { el.removeAttribute('data-clf'); el.removeAttribute('data-clh'); }
  for (const el of all) {
    const name = lname(el);
    if (!name.includes(':')) continue;
    const t = tail(el);
    if (t === 'context') {
      const c: Context = { id: el.getAttribute('id') || '', dims: [] };
      for (const child of Array.from(el.getElementsByTagName('*'))) {
        const ct = tail(child);
        if (ct === 'instant') c.instant = clean(child.textContent);
        else if (ct === 'startdate') c.start = clean(child.textContent);
        else if (ct === 'enddate') c.end = clean(child.textContent);
        else if (ct === 'explicitmember' || ct === 'typedmember') c.dims.push([child.getAttribute('dimension') || '', clean(child.textContent)]);
      }
      if (c.id) contexts[c.id] = c;
    } else if (t === 'unit') {
      const measures = Array.from(el.getElementsByTagName('*')).filter(c => tail(c) === 'measure').map(c => clean(c.textContent));
      const divide = Array.from(el.getElementsByTagName('*')).some(c => tail(c) === 'divide');
      const id = el.getAttribute('id');
      if (id) units[id] = divide && measures.length === 2 ? measures[0] + '/' + measures[1] : measures.join('*');
    }
  }
  if (!Object.keys(contexts).length) throw new FilingError('no_contexts', '文件里没有 XBRL 上下文（xbrli:context），可能不完整或已损坏。');

  // Tables in document order; ix:hidden; -sec-ix-hidden displays.
  const tables = new Map<Element, number>();
  all.forEach(el => { if (lname(el) === 'table') tables.set(el, tables.size); });
  const hiddenDisplay = new Map<string, Element>();
  for (const el of all) {
    const style = el.getAttribute && el.getAttribute('style');
    if (style && style.includes('-sec-ix-hidden')) {
      const m = /-sec-ix-hidden\s*:\s*([^;"]+)/.exec(style);
      if (m && !hiddenDisplay.has(m[1].trim())) hiddenDisplay.set(m[1].trim(), el);
    }
  }
  const facts: Fact[] = [];
  const dei: Parsed['dei'] = {};
  let unparsed = 0, nil = 0;
  const resolve = (name: string) => {
    const at = name.indexOf(':');
    const prefix = at > 0 ? name.slice(0, at).toLowerCase() : '';
    return { ns: namespaces[prefix] || '', local: at > 0 ? name.slice(at + 1) : name };
  };
  let index = 0;
  for (const el of all) {
    const t = lname(el);
    if (t === 'ix:nonnumeric') {
      const name = el.getAttribute('name') || '';
      const { ns, local } = resolve(name);
      if (ns.startsWith('http://xbrl.sec.gov/dei/') && !(local in dei)) dei[local] = { text: clean(el.textContent), ctx: el.getAttribute('contextref') || '' };
      continue;
    }
    if (t !== 'ix:nonfraction') continue;
    const i = index++;
    el.setAttribute('data-clf', String(i));
    const name = el.getAttribute('name') || '';
    const { ns, local } = resolve(name);
    const isNil = /^(true|1)$/i.test(el.getAttribute('xsi:nil') || '');
    if (isNil) nil++;
    const format = (el.getAttribute('format') || '').split(':').pop()!.toLowerCase();
    const scale = Number(el.getAttribute('scale') || '0') || 0;
    const sign = el.getAttribute('sign') === '-' ? '-' : '';
    const text = clean(el.textContent);
    const value = isNil ? null : factValue(text, format, scale, sign);
    if (!isNil && value === null) unparsed++;
    let hidden = false, table = -1;
    for (let p = el.parentElement; p; p = p.parentElement) {
      const pn = lname(p);
      if (pn === 'ix:hidden') hidden = true;
      if (pn === 'table' && table < 0) table = tables.get(p) ?? -1;
    }
    const id = el.getAttribute('id');
    let display = !hidden;
    if (hidden && id && hiddenDisplay.has(id)) {
      const shown = hiddenDisplay.get(id)!;
      if (!shown.hasAttribute('data-clh')) shown.setAttribute('data-clh', String(i));
      display = true;
      for (let p: Element | null = shown; p; p = p.parentElement) if (lname(p) === 'table') { table = tables.get(p) ?? -1; break; }
    }
    facts.push({
      i, name, ns, local, ctx: el.getAttribute('contextref') || '', unit: el.getAttribute('unitref') || '',
      decimals: el.getAttribute('decimals'), scale, sign, format, text, value: isNil ? null : value, nil: isNil, id, table, hidden, display,
    });
  }
  if (!dei.DocumentType) throw new FilingError('no_document_type', '文件没有 dei:DocumentType 标签，无法判断是 10-K 还是 10-Q。');
  return { facts, contexts, units, dei, namespaces, unparsed, nil, doc };
}

/** Display element for a fact inside a (sanitized copy of the) parsed document. */
export function anchorElement(doc: Document, fact: Pick<Fact, 'i' | 'hidden'>): Element | null {
  return fact.hidden ? doc.querySelector(`[data-clh="${fact.i}"]`) : doc.querySelector(`[data-clf="${fact.i}"]`);
}

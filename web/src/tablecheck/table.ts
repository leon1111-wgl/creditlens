/* 截图核对 · deterministic part. Rebuilds a table from OCR line boxes and runs footing checks
   on the numbers it read. Nothing here sees a reference value: input is OCR text + boxes only.
   It never states a "true" value; a failed check reports what was read, where, and the formula. */
import { add, cmp, type Dec, formatF, parse, sub } from '../decimal';

export interface Box { x0: number; y0: number; x1: number; y1: number }
export interface OcrLine { text: string; conf: number; box: Box }

export type TokenKind = 'amount' | 'dash' | 'garbled' | 'text';
export interface Token {
  id: number; line: number; text: string; kind: TokenKind; box: Box; conf: number;
  value?: Dec; negative?: boolean; dp?: number; percent?: boolean; year?: number;
  row?: number; column?: number;
}
export interface Column { index: number; x0: number; x1: number; year: number | null; header: string; tokens: number[] }
export interface Row {
  index: number; y0: number; y1: number; tokens: number[]; label: string; fullLabel: string; labelX0: number | null;
  kind: 'data' | 'header' | 'label'; continuation: boolean; cells: Record<number, number[]>;
}
export interface Table { tokens: Token[]; rows: Row[]; columns: Column[]; lineHeight: number }

export type CheckStatus = 'foots' | 'does_not_foot' | 'not_checked';
export interface Term { row: number; token: number; value: string; sign: 1 | -1 }
export interface Check {
  id: number; kind: 'total' | 'cross' | 'change'; status: CheckStatus; reason: string | null;
  row: number; column: number; target: number | null; terms: Term[]; formula: string;
  read: string | null; computed: string | null; difference: string | null; tolerance: string | null;
}

// ---------------------------------------------------------------------------
// Amount grammar (strict, like table-lookup-1): thousands separators, optional currency mark,
// parentheses or a leading minus for negatives, optional decimals and percent.
// ---------------------------------------------------------------------------

const AMOUNT = /^(?:US\$|\$|¥|RMB|HK\$)?\s*(\()?\s*([-−–])?\s*(?:US\$|\$|¥)?\s*((?:\d{1,3}(?:,\d{3})+)|\d+)(\.\d+)?\s*(%)?\s*(\))?\s*(%)?$/;
const DASH = /^(?:\$|¥)?\s*[-—–−]{1,3}$/;
const YEAR = /^(?:19|20)\d{2}$/;

export function normalise(text: string): string {
  return text.normalize('NFKC').replace(/[−‒–]/g, '–').replace(/\s+/g, ' ').trim();
}

export function readAmount(raw: string): Pick<Token, 'kind' | 'value' | 'negative' | 'dp' | 'percent' | 'year'> {
  const text = normalise(raw);
  if (DASH.test(text)) return { kind: 'dash' };
  const m = AMOUNT.exec(text);
  if (!m || Boolean(m[1]) !== Boolean(m[6]) || (m[5] && m[7])) {
    return { kind: /\d/.test(text) ? 'garbled' : 'text' };
  }
  const digits = m[3].replace(/,/g, '') + (m[4] ?? '');
  const negative = Boolean(m[1]) || Boolean(m[2]);
  const value = parse(digits);
  const year = !m[1] && !m[2] && !m[4] && !m[5] && !m[7] && YEAR.test(m[3]) && !/[$¥]/.test(text) ? Number(m[3]) : undefined;
  return { kind: 'amount', value, negative, dp: m[4] ? m[4].length - 1 : 0, percent: Boolean(m[5] || m[7]), year };
}

export function signed(token: Token): Dec {
  const v = token.kind === 'dash' ? parse('0') : (token.value as Dec);
  return token.negative ? { int: -v.int, exp: v.exp } : v;
}

export function show(value: Dec): string {
  const negative = value.int < 0n;
  const abs = formatF({ int: negative ? -value.int : value.int, exp: value.exp });
  const [i, f] = abs.split('.');
  const grouped = i.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const body = f ? `${grouped}.${f}` : grouped;
  return negative ? `(${body})` : body;
}

// ---------------------------------------------------------------------------
// Tokens: split each OCR line into a label part and trailing numeric words, with boxes
// estimated from character offsets (OCR boxes are per line, numbers are right-aligned).
// ---------------------------------------------------------------------------

export function tokenize(lines: OcrLine[]): Token[] {
  const tokens: Token[] = [];
  lines.forEach((line, li) => {
    const text = normalise(line.text);
    if (!text) return;
    const words: { s: number; e: number; t: string }[] = [];
    const re = /\S+/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text))) words.push({ s: m.index, e: m.index + m[0].length, t: m[0] });
    // Re-join currency marks and lone parentheses with their number ("$ 1,234", "( 1,234 )").
    const merged: typeof words = [];
    for (const w of words) {
      const prev = merged[merged.length - 1];
      if (prev && (/^(?:US\$|\$|¥|\(|\$\(|[-–])$/.test(prev.t) || /^[)%]$/.test(w.t))) {
        merged[merged.length - 1] = { s: prev.s, e: w.e, t: text.slice(prev.s, w.e) };
      } else merged.push(w);
    }
    let cut = merged.length;
    while (cut > 0) {
      const kind = readAmount(merged[cut - 1].t).kind;
      if (kind === 'amount' || kind === 'dash') cut -= 1;
      else break;
    }
    // A garbled numeric word at the right end (e.g. "1,2B4") is kept as its own token so a
    // check that needs it says "unreadable" instead of skipping it.
    if (cut > 0 && cut === merged.length && readAmount(merged[cut - 1].t).kind === 'garbled' && cut > 1) cut -= 1;
    const width = line.box.x1 - line.box.x0;
    const at = (s: number, e: number): Box => ({
      x0: line.box.x0 + (width * s) / text.length, x1: line.box.x0 + (width * e) / text.length, y0: line.box.y0, y1: line.box.y1,
    });
    if (cut > 0) {
      const s = merged[0].s, e = merged[cut - 1].e;
      const t = text.slice(s, e);
      const parsed = cut === 1 ? readAmount(t) : { kind: 'text' as TokenKind };
      tokens.push({ id: tokens.length, line: li, text: t, conf: line.conf, box: at(s, e), ...parsed });
    }
    for (const w of merged.slice(cut)) {
      tokens.push({ id: tokens.length, line: li, text: w.t, conf: line.conf, box: at(w.s, w.e), ...readAmount(w.t) });
    }
  });
  return tokens;
}

// ---------------------------------------------------------------------------
// Rows and columns
// ---------------------------------------------------------------------------

const cy = (b: Box) => (b.y0 + b.y1) / 2;
const h = (b: Box) => b.y1 - b.y0;

function sameRow(a: Box, b: Box): boolean {
  const overlap = Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  return overlap / Math.max(Math.min(h(a), h(b)), 1e-9) >= 0.5;
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

const numeric = (t: Token) => t.kind === 'amount' || t.kind === 'dash';

export function buildTable(lines: OcrLine[]): Table {
  const tokens = tokenize(lines);
  const lineHeight = median(lines.map((l) => h(l.box))) || 10;
  // Rows: chain tokens in reading order by vertical overlap with the row's first token.
  const order = [...tokens].sort((a, b) => cy(a.box) - cy(b.box) || a.box.x0 - b.box.x0);
  const rows: Row[] = [];
  for (const t of order) {
    const row = rows[rows.length - 1];
    const anchor = row ? tokens[row.tokens[0]] : null;
    if (row && anchor && sameRow(anchor.box, t.box)) {
      row.tokens.push(t.id); row.y0 = Math.min(row.y0, t.box.y0); row.y1 = Math.max(row.y1, t.box.y1);
    } else {
      rows.push({ index: rows.length, y0: t.box.y0, y1: t.box.y1, tokens: [t.id], label: '', fullLabel: '', labelX0: null,
        kind: 'label', continuation: false, cells: {} });
    }
  }
  rows.forEach((r) => { r.tokens.sort((a, b) => tokens[a].box.x0 - tokens[b].box.x0); r.tokens.forEach((i) => { tokens[i].row = r.index; }); });

  // Columns: numeric tokens whose horizontal extents overlap belong to one column. Wide tokens
  // (merged text) do not join columns. A column needs tokens in at least two rows.
  const nums = tokens.filter(numeric);
  const widths = median(nums.map((t) => t.box.x1 - t.box.x0)) || lineHeight * 3;
  const candidates = nums.filter((t) => t.box.x1 - t.box.x0 <= Math.max(widths * 3, lineHeight * 8));
  const parent = new Map<number, number>(candidates.map((t) => [t.id, t.id]));
  const find = (x: number): number => { let p = parent.get(x) as number; while (p !== x) { x = p; p = parent.get(x) as number; } return x; };
  const sorted = [...candidates].sort((a, b) => a.box.x0 - b.box.x0);
  for (let i = 0; i < sorted.length; i++) {
    for (let j = i + 1; j < sorted.length && sorted[j].box.x0 < sorted[i].box.x1; j++) {
      const a = sorted[i].box, b = sorted[j].box;
      const overlap = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
      if (overlap > 0.25 * Math.min(a.x1 - a.x0, b.x1 - b.x0)) parent.set(find(sorted[j].id), find(sorted[i].id));
    }
  }
  const groups = new Map<number, Token[]>();
  for (const t of candidates) { const r = find(t.id); groups.set(r, [...(groups.get(r) ?? []), t]); }
  const columns: Column[] = [];
  for (const g of groups.values()) {
    if (new Set(g.map((t) => t.row)).size < 2) continue;
    columns.push({ index: 0, x0: Math.min(...g.map((t) => t.box.x0)), x1: Math.max(...g.map((t) => t.box.x1)), year: null, header: '',
      tokens: g.map((t) => t.id) });
  }
  columns.sort((a, b) => a.x0 - b.x0).forEach((c, i) => { c.index = i; c.tokens.forEach((id) => { tokens[id].column = i; }); });
  // Garbled numeric words and leftover numerics are placed in the column they overlap, if any.
  for (const t of tokens) {
    if (t.column !== undefined || !(t.kind === 'garbled' || numeric(t))) continue;
    const c = columns.find((col) => Math.min(col.x1, t.box.x1) - Math.max(col.x0, t.box.x0) > 0.4 * (t.box.x1 - t.box.x0));
    if (c) { t.column = c.index; c.tokens.push(t.id); }
  }
  const firstX = columns.length ? columns[0].x0 : Infinity;

  for (const r of rows) {
    for (const id of r.tokens) {
      const t = tokens[id];
      if (t.column !== undefined) (r.cells[t.column] ||= []).push(id);
    }
    const labelTokens = r.tokens.map((i) => tokens[i]).filter((t) => t.column === undefined && t.box.x1 <= firstX + lineHeight * 0.5);
    r.label = labelTokens.map((t) => t.text).join(' ');
    r.labelX0 = labelTokens.length ? Math.min(...labelTokens.map((t) => t.box.x0)) : null;
    const cellTokens = Object.values(r.cells).flat().map((i) => tokens[i]);
    if (!cellTokens.length) r.kind = 'label';
    else if (cellTokens.every((t) => t.year !== undefined) && /^[\p{L}\s,.]*$/u.test(r.label)) r.kind = 'header';
    else r.kind = 'data';
  }
  // Column headers: years and words above the column's first data cell that overlap it.
  for (const c of columns) {
    const firstData = rows.find((r) => r.kind === 'data' && r.cells[c.index]);
    const words: string[] = [];
    for (const r of rows) {
      if (firstData && r.index >= firstData.index) break;
      for (const id of r.tokens) {
        const t = tokens[id];
        const overlap = Math.min(c.x1 + lineHeight, t.box.x1) - Math.max(c.x0 - lineHeight, t.box.x0);
        if (overlap <= 0) continue;
        if (t.year !== undefined && r.kind === 'header') c.year = t.year;
        else if (t.kind === 'text') words.push(t.text);
      }
    }
    c.header = words.join(' ');
  }
  // Wrapped labels: a label-only line directly above a row whose label starts in lower case
  // (or that has no label) is the first part of that row's label, not a heading.
  for (const r of rows) r.fullLabel = r.label;
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i], prev = rows[i - 1];
    if (r.kind !== 'data' || prev.kind !== 'label' || !prev.label) continue;
    const gap = r.y0 - prev.y1;
    if (gap > lineHeight * 0.8) continue;
    if (r.label && !/^[a-z(]/.test(r.label)) continue;
    let start = i - 1;
    const parts = [prev.label];
    while (start > 0 && rows[start - 1].kind === 'label' && /^[a-z(]/.test(rows[start].label)
           && rows[start].y0 - rows[start - 1].y1 <= lineHeight * 0.8) { start -= 1; parts.unshift(rows[start].label); }
    for (let k = start; k < i; k++) rows[k].continuation = true;
    r.fullLabel = [...parts, r.label].filter(Boolean).join(' ');
  }
  return { tokens, rows, columns, lineHeight };
}

// ---------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------

const TOTAL = /^(?:total\b|totals\b|合计|总计|小计|共计|net cash (?:provided|used|from)\b|net (?:increase|decrease|change)\b.*\bcash\b)/i;
const LESS = /^(?:less\b|减[:：])/i;
const CHANGE = /(?:change|increase|decrease|增减|变动|变化)/i;
const TOTAL_COLUMN = /^(?:total|合计|总计)$/i;

function unit(dp: number): Dec { return { int: 5n, exp: -dp - 1 }; }
function times(d: Dec, n: number): Dec { return { int: d.int * BigInt(n), exp: d.exp }; }
function abs(d: Dec): Dec { return d.int < 0n ? { int: -d.int, exp: d.exp } : d; }

export function runChecks(table: Table): Check[] {
  const { rows, tokens, columns } = table;
  const checks: Check[] = [];
  const cell = (r: Row, c: number): Token[] => (r.cells[c] ?? []).map((i) => tokens[i]);
  const blockStart = new Map<string, number>(); // `${row}:${col}` -> first row of a total's matched block

  for (const col of columns) {
    for (const r of rows) {
      if (r.kind !== 'data' || !TOTAL.test(r.fullLabel)) continue;
      const own = cell(r, col.index);
      if (own.length !== 1 || !numeric(own[0])) continue;
      const target = own[0];
      const base: Omit<Check, 'status' | 'reason'> = {
        id: checks.length, kind: 'total', row: r.index, column: col.index, target: target.id, terms: [], formula: '',
        read: show(signed(target)), computed: null, difference: null, tolerance: null,
      };
      let sum = parse('0');
      let dp = target.dp ?? 0;
      const terms: Term[] = [];
      let status: CheckStatus | null = null;
      let reason: string | null = null;
      for (let i = r.index - 1; i >= 0; i--) {
        const above = rows[i];
        if (above.kind === 'header') break;
        if (above.kind === 'label') continue;
        const here = cell(above, col.index);
        if (!here.length) continue;
        if (here.length > 1 || here[0].kind === 'garbled') { status = 'not_checked'; reason = 'unreadable_cell'; break; }
        const t = here[0];
        if (t.percent) { status = 'not_checked'; reason = 'percent_cell'; break; }
        const sign: 1 | -1 = LESS.test(above.fullLabel) ? -1 : 1;
        const v = signed(t);
        sum = sign === 1 ? add(sum, v) : sub(sum, v);
        dp = Math.max(dp, t.dp ?? 0);
        terms.push({ row: above.index, token: t.id, value: show(v), sign });
        if (TOTAL.test(above.fullLabel)) {
          const start = blockStart.get(`${above.index}:${col.index}`);
          if (start === undefined) { status = 'not_checked'; reason = 'depends_on_unfooted_subtotal'; break; }
          i = start;
        }
        const tol = times(unit(dp), terms.length + 1);
        if (terms.length >= 2 && cmp(abs(sub(sum, signed(target))), tol) <= 0) {
          status = 'foots';
          blockStart.set(`${r.index}:${col.index}`, i); // after a subtotal's jump, i is its block's first row
          break;
        }
      }
      if (status === null) status = terms.length >= 2 ? 'does_not_foot' : 'not_checked';
      if (status === 'not_checked' && !reason) reason = 'fewer_than_two_terms';
      const shown = status === 'does_not_foot' ? naturalBlock(table, r, col.index, blockStart) : terms;
      let shownSum = parse('0');
      for (const t of shown) shownSum = t.sign === 1 ? add(shownSum, signed(tokens[t.token])) : sub(shownSum, signed(tokens[t.token]));
      const tol = times(unit(dp), shown.length + 1);
      checks.push({
        ...base, status, reason, terms: [...shown].reverse(),
        formula: '',
        computed: shown.length ? show(shownSum) : null,
        difference: shown.length ? show(sub(signed(target), shownSum)) : null,
        tolerance: show(tol),
      });
    }
  }
  // Formula text with each term as printed, e.g. "1,200 + (300) + 45"; a Less row is subtracted.
  for (const c of checks) c.formula = c.terms.map((t, k) => `${k ? (t.sign === -1 ? ' − ' : ' + ') : t.sign === -1 ? '− ' : ''}${t.value}`).join('');

  // Cross-footing: a column headed "Total" against the other numeric columns of the same row.
  const totalCols = columns.filter((c) => TOTAL_COLUMN.test(c.header.trim()));
  for (const tc of totalCols) {
    const others = columns.filter((c) => c.index < tc.index && c.year === null && !TOTAL_COLUMN.test(c.header.trim()));
    if (others.length < 2) continue;
    for (const r of rows) {
      if (r.kind !== 'data') continue;
      const tgt = cell(r, tc.index);
      if (tgt.length !== 1 || !numeric(tgt[0])) continue;
      const parts = others.map((c) => cell(r, c.index));
      if (parts.some((p) => p.length > 1 || (p[0] && p[0].kind === 'garbled'))) {
        checks.push({ id: checks.length, kind: 'cross', status: 'not_checked', reason: 'unreadable_cell', row: r.index, column: tc.index,
          target: tgt[0].id, terms: [], formula: '', read: show(signed(tgt[0])), computed: null, difference: null, tolerance: null });
        continue;
      }
      const used = parts.filter((p) => p.length === 1).map((p) => p[0]);
      if (used.length < 2) continue;
      let sum = parse('0');
      let dp = tgt[0].dp ?? 0;
      for (const t of used) { sum = add(sum, signed(t)); dp = Math.max(dp, t.dp ?? 0); }
      const tol = times(unit(dp), used.length + 1);
      const diff = sub(signed(tgt[0]), sum);
      checks.push({ id: checks.length, kind: 'cross', status: cmp(abs(diff), tol) <= 0 ? 'foots' : 'does_not_foot', reason: null,
        row: r.index, column: tc.index, target: tgt[0].id,
        terms: used.map((t) => ({ row: r.index, token: t.id, value: show(signed(t)), sign: 1 as const })),
        formula: used.map((t) => show(signed(t))).join(' + '), read: show(signed(tgt[0])), computed: show(sum), difference: show(diff), tolerance: show(tol) });
    }
  }

  // Year-on-year change: a column headed Change / % change / 增减 / 变动 next to two year columns.
  for (const cc of columns) {
    if (!CHANGE.test(cc.header) || cc.year !== null) continue;
    const years = columns.filter((c) => c.index < cc.index && c.year !== null).slice(-2);
    if (years.length !== 2 || years[0].year === years[1].year) continue;
    const [cur, prior] = (years[0].year as number) > (years[1].year as number) ? years : [years[1], years[0]];
    const pctColumn = /%|percent|百分/.test(cc.header);
    for (const r of rows) {
      if (r.kind !== 'data') continue;
      const tgt = cell(r, cc.index), a = cell(r, cur.index), b = cell(r, prior.index);
      if (tgt.length !== 1 || !numeric(tgt[0])) continue;
      const base = { id: checks.length, kind: 'change' as const, row: r.index, column: cc.index, target: tgt[0].id, read: show(signed(tgt[0])) };
      if (a.length !== 1 || b.length !== 1 || !numeric(a[0]) || !numeric(b[0])) {
        checks.push({ ...base, status: 'not_checked', reason: 'missing_year_cell', terms: [], formula: '', computed: null, difference: null, tolerance: null });
        continue;
      }
      const va = signed(a[0]), vb = signed(b[0]);
      const terms: Term[] = [{ row: r.index, token: a[0].id, value: show(va), sign: 1 }, { row: r.index, token: b[0].id, value: show(vb), sign: -1 }];
      const isPct = pctColumn || Boolean(tgt[0].percent);
      if (!isPct) {
        const dp = Math.max(tgt[0].dp ?? 0, a[0].dp ?? 0, b[0].dp ?? 0);
        const diff = sub(va, vb);
        const tol = times(unit(dp), 3);
        const d = sub(signed(tgt[0]), diff);
        checks.push({ ...base, status: cmp(abs(d), tol) <= 0 ? 'foots' : 'does_not_foot', reason: null, terms,
          formula: `${show(va)} − ${show(vb)}`, computed: show(diff), difference: show(d), tolerance: show(tol) });
        continue;
      }
      if (vb.int === 0n) {
        checks.push({ ...base, status: 'not_checked', reason: 'prior_is_zero', terms, formula: '', computed: null, difference: null, tolerance: null });
        continue;
      }
      // Percent: (a − b) / |b| × 100, compared at the printed decimals; inputs' rounding widens the band.
      const fa = Number(formatF(va)), fb = Number(formatF(vb));
      const expected = ((fa - fb) / Math.abs(fb)) * 100;
      const printed = Number(formatF(signed(tgt[0])));
      const pdp = tgt[0].dp ?? 0;
      const band = 0.5 * 10 ** -pdp + (100 * 0.5 * 10 ** -Math.max(a[0].dp ?? 0, b[0].dp ?? 0) * (1 + Math.abs(fa / fb))) / Math.abs(fb);
      const ok = Math.abs(printed - expected) <= band + 1e-9;
      checks.push({ ...base, status: ok ? 'foots' : 'does_not_foot', reason: null, terms,
        formula: `(${show(va)} − ${show(vb)}) ÷ |${show(vb)}| × 100`, computed: expected.toFixed(pdp), difference: (printed - expected).toFixed(pdp + 1),
        tolerance: band.toFixed(pdp + 1) });
    }
  }
  return checks;
}

/** For a total that does not foot: the rows from just below the nearest heading line, with matched
    subtotals standing in for their own blocks. This is the formula the page shows. */
function naturalBlock(table: Table, total: Row, column: number, blockStart: Map<string, number>): Term[] {
  const terms: Term[] = [];
  for (let i = total.index - 1; i >= 0; i--) {
    const r = table.rows[i];
    if (r.kind === 'header' || (r.kind === 'label' && !r.continuation)) break;
    if (r.kind === 'label') continue;
    const here = (r.cells[column] ?? []).map((k) => table.tokens[k]);
    if (here.length !== 1 || !numeric(here[0])) continue;
    terms.push({ row: r.index, token: here[0].id, value: show(signed(here[0])), sign: LESS.test(r.fullLabel) ? -1 : 1 });
    if (TOTAL.test(r.fullLabel)) {
      const start = blockStart.get(`${r.index}:${column}`);
      if (start !== undefined) i = start;
    }
  }
  return terms;
}

export function analyse(lines: OcrLine[]): { table: Table; checks: Check[] } {
  const table = buildTable(lines);
  return { table, checks: runChecks(table) };
}

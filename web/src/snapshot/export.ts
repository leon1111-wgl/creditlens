/* Take-away files for the credit snapshot: a credit memo draft (Markdown and Word .docx) and a CSV.
   Every figure carries the filing's accession, the XBRL concept, period, context and the anchor of
   its tagged cell (a link into the SEC document when its address is known). The .docx is a minimal
   WordprocessingML package written here (stored zip, no library). */
import type { Input, Metric, Snapshot, Term } from './model';
import { LABELS } from './model';
import { amountText, multiple, percentText, ratioText } from './format';

export interface SourceInfo {
  kind: 'file' | 'gallery'; fileName: string; sha256: string; bytes: number;
  accession: string | null; documentUrl: string | null; archiveUrl?: string | null; captured?: string | null;
  /** Where the accession came from: the gallery index, the visitor's own entry, or a "saved from" note inside the file (unverified). */
  accessionFrom?: 'gallery' | 'typed' | 'file-note' | null;
}
export interface ExportContext { snapshot: Snapshot; source: SourceInfo; generatedAt: string; appVersion: string }

const METRIC_ORDER = ['debt', 'net_debt', 'ebitda', 'debt_to_ebitda', 'net_debt_to_ebitda', 'interest_coverage', 'current_ratio', 'fcf', 'fcf_to_debt', 'debt_with_leases'];
const INPUT_ORDER = ['debt', 'current_debt', 'liquidity', 'ebit', 'da', 'interest', 'assets_current', 'liabilities_current', 'cfo', 'capex', 'operating_leases', 'finance_leases', 'revenue'];

export function metricText(m: Metric, s: Snapshot): string {
  if (m.value === null) return '不计算';
  if (m.kind === 'amount') return amountText(m.value as string, s.meta.display_scale, s.meta.currency);
  const x = m.value as number;
  return m.key === 'fcf_to_debt' ? percentText(x) : m.key === 'current_ratio' ? ratioText(x) : multiple(x);
}
export function anchorOf(t: Pick<Term, 'id' | 'fact'>, source: SourceInfo): { label: string; url: string | null } {
  if (t.id) return { label: '#' + t.id, url: source.documentUrl ? `${source.documentUrl}#${encodeURIComponent(t.id)}` : null };
  return { label: `第 ${t.fact + 1} 个 ix:nonFraction（无 id）`, url: null };
}
const termAmount = (t: Term, s: Snapshot) => amountText(t.value, t.scale, s.meta.currency);
const allInputs = (s: Snapshot): Input[] => {
  const list = INPUT_ORDER.map(k => s.inputs[k]).filter(Boolean);
  if (s.ladder) list.push(s.ladder.next12, s.ladder.years2to5, s.ladder.after5);
  return list;
};
// Text from the filing is data: escape Markdown so a registrant name cannot become a link, image or HTML.
const md = (x: string) => x.replace(/[\\`*_{}\[\]()<>#!|]/g, c => '\\' + c).replace(/\n/g, ' ');

function header(ctx: ExportContext): string[] {
  const { snapshot: s, source } = ctx;
  return [
    `- 公司：${md(s.meta.registrant || '（未标注）')}，CIK ${md(s.meta.cik || '—')}${s.meta.ticker ? `，代码 ${md(s.meta.ticker)}` : ''}`,
    `- 文件：${s.meta.form}，期末 ${s.meta.period_end}${s.meta.period_start ? `；流量期间 ${s.meta.period_start} 至 ${s.meta.period_end}（${s.meta.months} 个月）` : ''}`,
    `- Accession：${source.accession ? md(source.accession) + (source.accessionFrom === 'file-note' ? '（取自文件内的保存地址注释，未核实）' : source.accessionFrom === 'typed' ? '（访客填写）' : '') : '未提供（拖入的文件本身不含 accession；可在页面上填写 EDGAR 地址）'}`,
    `- SEC 地址：${source.documentUrl || '未提供'}${source.archiveUrl ? `；取自互联网档案馆抓取 ${source.archiveUrl}` : ''}`,
    `- 文件：${md(source.fileName)}，${source.bytes.toLocaleString('en-US')} 字节，SHA-256 ${source.sha256}`,
    `- 口径：${s.definitions}；合并口径（无维度）上下文；币种 ${s.meta.currency}`,
    `- 生成：${ctx.generatedAt}，CreditLens ${ctx.appVersion}`,
  ];
}

export function memoMarkdown(ctx: ExportContext): string {
  const { snapshot: s, source } = ctx;
  const out: string[] = [];
  out.push(`# 信用快照备忘录（草稿）· ${md(s.meta.registrant || s.meta.cik)} · ${md(s.meta.form)} · ${s.meta.period_end}`, '');
  out.push('> 由 CreditLens 信用快照根据发行人自己的 Inline XBRL 标签计算。这是信用视角的整理，不是评级，也不是投资建议；复核提示只指出需要查看的地方。每个数字都给出 XBRL 概念、期间、上下文与原文锚点。', '');
  out.push(...header(ctx), '');
  if (s.status !== 'ok') {
    out.push('## 模板不支持', '', s.template.reason, '');
    return out.join('\n');
  }
  out.push('## 指标', '', '| 指标 | 数值 | 公式 | 说明 |', '|---|---:|---|---|');
  for (const k of METRIC_ORDER) { const m = s.metrics[k]; if (m) out.push(`| ${md(m.label)} | ${md(metricText(m, s))} | \`${md(m.formula)}\` | ${md(m.reason || '')} |`); }
  if (s.ladder) {
    out.push('', '## 债务到期结构（本金）', '', '| 区间 | 金额 |', '|---|---:|');
    const l = s.ladder;
    out.push(`| ${l.next12_label} | ${l.next12.value === null ? '未标注' : amountText(l.next12.value, s.meta.display_scale, s.meta.currency)} |`);
    out.push(`| 第 2–5 年${l.missing_years.length ? `（缺第 ${l.missing_years.join('、')} 年）` : ''} | ${l.years2to5.value === null ? '未标注' : amountText(l.years2to5.value, s.meta.display_scale, s.meta.currency)} |`);
    out.push(`| 5 年以后 | ${l.after5.value === null ? '未标注' : amountText(l.after5.value, s.meta.display_scale, s.meta.currency)} |`);
  }
  out.push('', '## 复核提示', '');
  if (!s.cues.length) out.push('- （无）');
  for (const c of s.cues) out.push(`- **${c.id}** ${md(c.text)}`);
  out.push('', '## 输入与来源', '', '| 输入 | 规则 | 数值 | 概念 | 期间 | 上下文 | scale | sign | 原文文字 | 锚点 |', '|---|---|---:|---|---|---|---:|---|---:|---|');
  for (const input of allInputs(s)) {
    if (!input.terms.length) { out.push(`| ${md(input.label)} | ${md(input.missing || input.rule || '')} | 未找到 | | | | | | | |`); continue; }
    for (const t of input.terms) {
      const a = anchorOf(t, source);
      out.push(`| ${md(input.label)} | ${md(input.rule)}${t.note ? '；' + md(t.note) : ''} | ${md(termAmount(t, s))} | \`${t.concept.replace(/`/g, '')}\` | ${md(t.period)} | ${md(t.context)} | ${t.scale} | ${t.sign || '+'} | ${md(t.text)} | ${a.url ? `[${md(a.label)}](${encodeURI(a.url)})` : md(a.label)} |`);
    }
  }
  out.push('', '## 口径', '', '定义见 CreditLens 仓库 docs/47-credit-snapshot.md（credit-snapshot-def-1）。债务与现金按资产负债表上的行加总；租赁负债单列，不计入总债务；10-Q 的流量为年初至今，不计算杠杆倍数。', '');
  return out.join('\n');
}

// Spreadsheet formula injection: a text cell starting with = + - @ (or a tab/CR) is prefixed with ' unless it is a number.
const csvCell = (x: string | number | null | undefined) => {
  let s = x === null || x === undefined ? '' : String(x);
  if (/^[=+\-@\t\r]/.test(s) && !/^-?\d+(\.\d+)?$/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};
export function snapshotCsv(ctx: ExportContext): string {
  const { snapshot: s, source } = ctx;
  const head = ['kind', 'key', 'label', 'value', 'display', 'unit', 'formula_or_rule', 'concept', 'period', 'context', 'decimals', 'scale', 'sign', 'printed_text', 'accession', 'anchor', 'anchor_url', 'fact_index', 'cik', 'form', 'period_end', 'file_sha256'];
  const rows: (string | number | null)[][] = [head];
  const common = [source.accession, null, null, null, s.meta.cik, s.meta.form, s.meta.period_end, source.sha256];
  for (const k of METRIC_ORDER) {
    const m = s.metrics[k]; if (!m) continue;
    rows.push(['metric', m.key, m.label, m.value === null ? '' : m.value, metricText(m, s), m.kind === 'amount' ? s.meta.currency : 'ratio', m.formula + (m.reason ? ` — ${m.reason}` : ''), null, null, null, null, null, null, null, ...common]);
  }
  for (const input of allInputs(s)) {
    if (!input.terms.length) { rows.push(['input', input.key, input.label, '', ['next12', 'years2to5', 'after5'].includes(input.key) ? '未标注' : '未找到', s.meta.currency, input.missing || input.rule, null, null, null, null, null, null, null, ...common]); continue; }
    for (const t of input.terms) {
      const a = anchorOf(t, source);
      rows.push(['input_term', input.key, input.label, (t.op === '−' ? '-' : '') + t.value, termAmount(t, s), t.unit, input.rule + (t.note ? `; ${t.note}` : ''), t.concept, t.period, t.context, t.decimals, t.scale, t.sign || '+', t.text,
        source.accession, a.label, a.url, t.fact, s.meta.cik, s.meta.form, s.meta.period_end, source.sha256]);
    }
  }
  for (const c of s.cues) rows.push(['cue', c.id, c.text, null, null, null, null, null, null, null, null, null, null, null, ...common]);
  return '﻿' + rows.map(r => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
}

// ---------- .docx (stored zip, minimal WordprocessingML) ----------
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(b: Uint8Array): number { let c = 0xffffffff; for (const x of b) c = CRC[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
export function zipStore(files: [string, string][]): Uint8Array {
  const enc = new TextEncoder();
  const parts: Uint8Array[] = []; const central: Uint8Array[] = []; let offset = 0;
  for (const [name, text] of files) {
    const nameBytes = enc.encode(name), data = enc.encode(text), crc = crc32(data);
    const local = new Uint8Array(30 + nameBytes.length); const dv = new DataView(local.buffer);
    dv.setUint32(0, 0x04034b50, true); dv.setUint16(4, 20, true); dv.setUint16(6, 0x0800, true); dv.setUint16(8, 0, true);
    dv.setUint16(10, 0, true); dv.setUint16(12, 0x21, true); dv.setUint32(14, crc, true); dv.setUint32(18, data.length, true); dv.setUint32(22, data.length, true);
    dv.setUint16(26, nameBytes.length, true); dv.setUint16(28, 0, true); local.set(nameBytes, 30);
    const cd = new Uint8Array(46 + nameBytes.length); const cv = new DataView(cd.buffer);
    cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true); cv.setUint16(6, 20, true); cv.setUint16(8, 0x0800, true); cv.setUint16(10, 0, true);
    cv.setUint16(12, 0, true); cv.setUint16(14, 0x21, true); cv.setUint32(16, crc, true); cv.setUint32(20, data.length, true); cv.setUint32(24, data.length, true);
    cv.setUint16(28, nameBytes.length, true); cv.setUint32(42, offset, true); cd.set(nameBytes, 46);
    parts.push(local, data); central.push(cd); offset += local.length + data.length;
  }
  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const end = new Uint8Array(22); const ev = new DataView(end.buffer);
  ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true); ev.setUint32(12, cdSize, true); ev.setUint32(16, offset, true);
  const all = [...parts, ...central, end]; const out = new Uint8Array(all.reduce((n, p) => n + p.length, 0));
  let at = 0; for (const p of all) { out.set(p, at); at += p.length; }
  return out;
}
const xml = (x: string) => x.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '');
const run = (text: string, opts: { bold?: boolean; size?: number; mono?: boolean } = {}) =>
  `<w:r><w:rPr>${opts.mono ? '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>' : ''}${opts.bold ? '<w:b/>' : ''}${opts.size ? `<w:sz w:val="${opts.size}"/>` : ''}</w:rPr><w:t xml:space="preserve">${xml(text)}</w:t></w:r>`;
const para = (text: string, opts: { bold?: boolean; size?: number; mono?: boolean } = {}) => `<w:p>${run(text, opts)}</w:p>`;
function table(rows: string[][]): string {
  const cell = (t: string, head: boolean) => `<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr><w:p>${run(t, { bold: head, size: 16 })}</w:p></w:tc>`;
  const cols = rows[0]?.length || 1;
  return `<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>${['top', 'left', 'bottom', 'right', 'insideH', 'insideV'].map(b => `<w:${b} w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>`).join('')}</w:tblBorders></w:tblPr>`
    + `<w:tblGrid>${Array.from({ length: cols }, () => `<w:gridCol w:w="${Math.floor(9638 / cols)}"/>`).join('')}</w:tblGrid>`
    + rows.map((r, i) => `<w:tr>${r.map(c => cell(c, i === 0)).join('')}</w:tr>`).join('') + '</w:tbl>';
}
export function memoDocx(ctx: ExportContext): Uint8Array {
  const { snapshot: s, source } = ctx;
  const body: string[] = [];
  body.push(para(`信用快照备忘录（草稿）· ${s.meta.registrant || s.meta.cik} · ${s.meta.form} · ${s.meta.period_end}`, { bold: true, size: 32 }));
  body.push(para('由 CreditLens 信用快照根据发行人自己的 Inline XBRL 标签计算。这是信用视角的整理，不是评级，也不是投资建议；复核提示只指出需要查看的地方。', { size: 18 }));
  for (const line of header(ctx)) body.push(para(line.replace(/^- /, ''), { size: 18 }));
  if (s.status !== 'ok') {
    body.push(para('模板不支持', { bold: true, size: 26 }), para(s.template.reason));
  } else {
    body.push(para('指标', { bold: true, size: 26 }));
    body.push(table([['指标', '数值', '公式', '说明'], ...METRIC_ORDER.filter(k => s.metrics[k]).map(k => { const m = s.metrics[k]; return [m.label, metricText(m, s), m.formula, m.reason || '']; })]));
    if (s.ladder) {
      const l = s.ladder; const a = (x: string | null) => x === null ? '未标注' : amountText(x, s.meta.display_scale, s.meta.currency);
      body.push(para('债务到期结构（本金）', { bold: true, size: 26 }));
      body.push(table([['区间', '金额'], [l.next12_label, a(l.next12.value)], ['第 2–5 年' + (l.missing_years.length ? `（缺第 ${l.missing_years.join('、')} 年）` : ''), a(l.years2to5.value)], ['5 年以后', a(l.after5.value)]]));
    }
    body.push(para('复核提示', { bold: true, size: 26 }));
    if (!s.cues.length) body.push(para('（无）'));
    for (const c of s.cues) body.push(para(`${c.id}  ${c.text}`));
    body.push(para('输入与来源', { bold: true, size: 26 }));
    const rows: string[][] = [['输入', '数值', '概念', '期间', '上下文', 'scale', 'sign', '锚点']];
    for (const input of allInputs(s)) {
      if (!input.terms.length) { rows.push([input.label, '未找到', input.missing || '', '', '', '', '', '']); continue; }
      for (const t of input.terms) { const an = anchorOf(t, source); rows.push([input.label + (t.note ? `（${t.note}）` : ''), termAmount(t, s), t.concept, t.period, t.context, String(t.scale), t.sign || '+', an.url || an.label]); }
    }
    body.push(table(rows));
  }
  body.push(para(`口径：${s.definitions}，见 CreditLens 仓库 docs/47-credit-snapshot.md。`, { size: 16 }));
  const doc = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body.join('')}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>`;
  return zipStore([
    ['[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'],
    ['_rels/.rels', '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'],
    ['word/document.xml', doc],
  ]);
}
export const exportBaseName = (s: Snapshot) => `creditlens-snapshot-${(s.meta.ticker || s.meta.cik || 'filing').toLowerCase().replace(/[^a-z0-9]+/g, '-')}-${s.meta.form.replace(/\//g, '').toLowerCase()}-${s.meta.period_end}`;
export { LABELS };

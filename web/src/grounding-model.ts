/* Pure helpers for creditlens/grounding.html (XBRL 对照). The page only displays a saved
   benchmark run: every outcome was computed on GitHub Actions by scripts/xbrl_grounding.py,
   and nothing here re-scores or changes it. */

export type Outcome = 'match' | 'conflict' | 'abstain' | 'not_scored';
export interface Box { x0: number; top: number; x1: number; bottom: number }
export interface Period { instant?: string; start?: string; end?: string }
export interface Fact {
  page: number; id: number; concept: string; context: string; period: Period | null; dims: [string, string][];
  unit: string | null; decimals: string | null; scale: string | null; sign: string | null; format: string | null;
  text: string; printed_negative: boolean; box: Box; year: number | null;
  printed_value: string | null; xbrl_value: string | null; outcome: Outcome; reason: string | null;
  checks: Partial<Record<'digits' | 'sign' | 'scale' | 'period', boolean>> | null;
  ocr: { text: string; confidence: number; box: Box; hits: number } | null;
  read: { magnitude: string; negative: boolean; scale: number | null; year: number | null; unit_text: string | null; column_text: string | null } | null;
  flags: string[];
}
export interface PageInfo {
  page: number; statements: string[]; image: string; sha256: string; bytes: number; width: number; height: number;
  pixel_width: number; pixel_height: number; content_pixel_height: number; ocr_lines: number;
}
export interface Filing {
  form: string; accession: string; filed: string; report_date: string | null; document_url: string; index_url: string;
  document_sha256: string; retrieved_at: string;
}
export interface IssuerIndex {
  id: string; name: string; cik: number; role: string; sic_description: string | null; filing: Filing;
  pages: PageInfo[]; facts_file: string; facts_file_sha256: string;
}
export interface Totals {
  facts_scored: number; outcomes: Record<'match' | 'conflict' | 'abstain', number>;
  abstain_reasons: Record<string, number>; conflict_components: Record<string, number>;
  box_recall: { hit: number; of: number; rate: number | null };
  exact_value: { correct: number; read: number; of: number; rate_of_read: number | null; rate_of_all: number | null };
  period_column: { correct: number; read: number; of: number; rate_of_read: number | null; rate_of_all: number | null };
  rs_0: number | null; rs_10: number | null; not_scored: Record<string, number>;
}
export interface Index {
  schema_version: number; benchmark_version: string; score_rule_version: string; mode: string; label: string; scope: string;
  run: { commit: string | null; run_id: string | null; run_url: string | null; generated_at: string };
  summary: Totals; by_issuer: Record<string, Totals>; issuers: IssuerIndex[]; image_bytes: number;
}

export const OUTCOME_TEXT: Record<Outcome, string> = {
  match: '一致', conflict: '冲突', abstain: '未作答', not_scored: '不计分',
};
export const OUTCOME_LONG: Record<Outcome, string> = {
  match: '图像读数的数值、括号符号、单位量级与年份列都与这条 XBRL 事实相符',
  conflict: '图像读数给出了完整读数，但至少一项与这条 XBRL 事实不符',
  abstain: '图像读数不完整，规则没有给出数值；不算错，也不算对',
  not_scored: '这条标签的显示格式不在评分范围内，只列出不计分',
};
export const REASON_TEXT: Record<string, string> = {
  no_spatial_evidence: '该位置没有 OCR 片段',
  low_ocr_confidence: 'OCR 片段置信度低于 0.90',
  merged_or_misaligned_region: 'OCR 片段超出标签框 8 pt 以上（常见：并入了相邻的 $ 或文字）',
  ambiguous_cell: '同一格选中多个 OCR 片段',
  detached_numeric_fragment_near_cell: '格旁有分离的括号、负号或数字碎片',
  unreadable_numeric_cell: '格内文字不是完整金额（如破折号、脚注号或粘连文字）',
  unit_missing: '上方没有可读的单位表头（如 “(In millions)”）',
  share_or_per_share_row: '行名或小标题涉及股数、每股或面值，印刷单位没有写明其量级',
  no_year_header: '上方没有可读的年份列表头',
  duplicate_header_year: '年份表头重复，列不唯一',
  outside_header_columns: '数字不在年份列范围内',
  cell_crosses_column: '数字跨越年份列边界',
  unsupported_format: '显示格式不在评分范围内',
  no_period: '上下文没有可读的期间',
  digits: '数位不同', sign: '括号符号不同', scale: '量级不同', period: '年份列不同',
};
export const STATEMENT_TEXT: Record<string, string> = {
  income: '利润表', comprehensive_income: '综合收益表', balance: '资产负债表', cash_flow: '现金流量表',
};
export const FLAG_TEXT: Record<string, string> = {
  in_text_cell: '数字嵌在文字中', in_text_block: '位于文本块标签内', nested: '嵌套标签', via_hidden: 'ix:hidden 事实的显示值',
  repeated_on_pages: '同一事实在所选页上重复出现', sign_differs: '印刷括号与 XBRL sign 属性不同', dash_zero: '破折号表示零',
};

export function reasonText(fact: Pick<Fact, 'outcome' | 'reason'>): string {
  if (!fact.reason) return '';
  return fact.reason.split('+').map(part => REASON_TEXT[part] ?? part).join('、');
}

export function formatPeriod(period: Period | null): string {
  if (!period) return '无期间';
  if (period.instant) return `时点 ${period.instant}`;
  return `${period.start ?? '?'} 至 ${period.end ?? '?'}`;
}

/** Group digits of a plain decimal string ("-1234000" -> "-1,234,000"); never rounds. */
export function groupDigits(value: string): string {
  const match = /^(-?)(\d+)(\.\d+)?$/.exec(value);
  if (!match) return value;
  return match[1] + match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (match[3] ?? '');
}

const SUPERSCRIPT: Record<string, string> = { '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '-': '⁻' };
export function power(scale: number | string | null): string {
  const n = Number(scale ?? 0);
  return n ? ' × 10' + String(n).split('').map(c => SUPERSCRIPT[c] ?? c).join('') : '';
}

export function unitText(unit: string | null): string {
  if (!unit) return '无单位';
  return unit.replace(/iso4217:/g, '').replace(/xbrli:/g, '').replace('/', ' / ');
}

export interface Row { label: string; value: string; cls?: string }

/** The readout shown when a box is selected: key first, then what the image reading gave. */
export function readoutRows(fact: Fact): Row[] {
  const rows: Row[] = [
    { label: '结果', value: `${OUTCOME_TEXT[fact.outcome]}${fact.reason ? '：' + reasonText(fact) : ''}`, cls: fact.outcome },
    { label: 'XBRL 概念', value: fact.concept },
    { label: '上下文', value: `${fact.context} · ${formatPeriod(fact.period)}` },
  ];
  if (fact.dims.length) rows.push({ label: '维度', value: fact.dims.map(([axis, member]) => `${axis} = ${member}`).join('；') });
  rows.push(
    { label: '单位', value: `${unitText(fact.unit)} · decimals ${fact.decimals ?? '—'} · scale ${fact.scale ?? '0'} · sign ${fact.sign ?? '无'}` },
    { label: '原文', value: `${fact.printed_negative ? '(' + fact.text + ')' : fact.text}${fact.printed_negative ? ' · 印有括号' : ''}` },
    { label: '答案值', value: fact.printed_value === null ? '不计分' : `${groupDigits(fact.printed_value)}${fact.year ? ' · 年份列 ' + fact.year : ''}`, cls: 'value' },
  );
  if (fact.xbrl_value !== null && fact.xbrl_value !== fact.printed_value)
    rows.push({ label: 'XBRL 值', value: `${groupDigits(fact.xbrl_value)}（标签符号与印刷括号不同，按印刷对照图像）` });
  if (fact.ocr) rows.push({ label: 'OCR 片段', value: `“${fact.ocr.text}” · 置信度 ${fact.ocr.confidence.toFixed(3)}${fact.ocr.hits > 1 ? ` · 选中 ${fact.ocr.hits} 段` : ''}` });
  else rows.push({ label: 'OCR 片段', value: '该位置没有识别片段' });
  if (fact.read) {
    const sign = fact.read.negative ? '-' : '';
    rows.push({
      label: '图像读数',
      value: `${sign}${groupDigits(fact.read.magnitude)}${fact.read.scale === null ? ' · 量级未定' : power(fact.read.scale)}${fact.read.year === null ? ' · 年份列未定' : ' · ' + fact.read.year + ' 列'}`,
    });
    if (fact.read.unit_text) rows.push({ label: '单位表头', value: fact.read.unit_text });
    if (fact.read.column_text) rows.push({ label: '列表头', value: fact.read.column_text });
  }
  rows.push({ label: '标签框', value: `x ${fact.box.x0.toFixed(1)}–${fact.box.x1.toFixed(1)} · y ${fact.box.top.toFixed(1)}–${fact.box.bottom.toFixed(1)} pt` });
  if (fact.flags.length) rows.push({ label: '备注', value: fact.flags.map(f => FLAG_TEXT[f] ?? f).join('；') });
  return rows;
}

export type Filter = 'all' | Outcome;
export function filterFacts(facts: Fact[], filter: Filter): Fact[] {
  return filter === 'all' ? facts : facts.filter(f => f.outcome === filter);
}

export function countOutcomes(facts: Fact[]): Record<Outcome, number> {
  const counts: Record<Outcome, number> = { match: 0, conflict: 0, abstain: 0, not_scored: 0 };
  for (const f of facts) counts[f.outcome] += 1;
  return counts;
}

export function percent(rate: number | null): string {
  return rate === null ? '—' : (rate * 100).toFixed(1) + '%';
}

/** Facts in reading order (top to bottom, left to right) for keyboard stepping. */
export function readingOrder(facts: Fact[]): Fact[] {
  return [...facts].sort((a, b) => (Math.round(a.box.top / 3) - Math.round(b.box.top / 3)) || (a.box.x0 - b.box.x0));
}

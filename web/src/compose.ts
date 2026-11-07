/* Turns answers into display cards. Every number in a headline comes from a
   readout row (registered value, page, bbox, unit, OCR outcome) or from a shown
   formula over those rows; compose.test.ts checks that no other number appears. */
import { answerPlan, indexOf } from './answer';
import { understand, type PlannedItem, type Understood } from './understand';
import type { Answer, Box, ChangeRow, Observation, Pack, PercentObservation } from './types';

export const ENGINE_LABEL = '页内规则引擎 evidence-answer-1 · 在本标签页计算 · 不是模型，也不是录制回放';
const SCALE_LABEL: Record<string, string> = { one: '美元', thousand: '千美元', ten_thousand: '万美元', million: '百万美元', hundred_million: '亿美元', billion: '十亿美元' };
const ZH: Record<string, string> = { MSFT: '微软', AAPL: '苹果' };
const OCR_REASON: Record<string, string> = {
  duplicate_row_label: '同页有同名行，需要显式选择', anchor_line_low_confidence: '行名锚点识别置信度低',
  selected: '已选出', ai_assisted_transcription: 'AI 辅助转写，未经独立人工确认',
};

export interface Readout {
  observation_id: string; issuer: string; label: string; period: string; value: string; requested: string | null;
  page: string; bbox: string; unit: string; outcome: string; outcome_tone: 'ok' | 'warn'; source: string;
  preview: { key: string; amount: Box | null; ocr: Box | null } | null; numbers: string[];
}
export interface Card {
  label: string; status: string; tone: 'ok' | 'warn' | 'stop'; headline: string; rows: Readout[];
  checks: { text: string; tone: 'ok' | 'warn' | 'stop' }[]; notes: string[]; formula: string | null;
  derived: string[]; engine: string; answer: Answer | null;
}
export interface Composed {
  type: Understood['type']; cards: Card[]; message: string | null; unknown: string[]; suggestions: string[]; notes: string[];
  reading: string | null;
}

export function group(text: string): string {
  const negative = text.startsWith('-');
  const body = negative ? text.slice(1) : text;
  const [int, frac] = body.split('.');
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (negative ? '-' : '') + grouped + (frac !== undefined ? '.' + frac : '');
}
const abs = (text: string) => (text.startsWith('-') ? text.slice(1) : text);
const LINE_NAMED = new Set(['short_debt', 'current_debt', 'long_debt', 'liquid_assets', 'cash_equivalents', 'short_investments']);
/** Metric label, with the issuer's own statement line where the two issuers' lines differ. */
const labelOf = (o: Observation) => (LINE_NAMED.has(o.metric) ? `${o.label}（报表行 ${o.row_label}）` : o.label);
/** The figure the rules compute with; a bracketed outflow is shown as its registered absolute amount. */
const amountOf = (o: Observation) => (o.raw_signed_value !== o.normalized_value ? `${group(o.normalized_value)} 百万美元（原文 ${o.raw_value}，括号表示现金流出，按登记规则取绝对额）` : `${o.raw_value} 百万美元`);
const shortAmount = (o: Observation) => group(o.normalized_value);
const fmtBox = (b: Box | null) => (b ? `${b.x0.toFixed(1)},${b.top.toFixed(1)} → ${b.x1.toFixed(1)},${b.bottom.toFixed(1)}` : '—');

function periodText(o: { year: number; period: { start: string | null; end: string; days: number | null; kind: string } }): string {
  return o.period.kind === 'instant' ? `FY${o.year} 期末（${o.period.end}）` : `FY${o.year}（${o.period.start} 至 ${o.period.end}，${o.period.days} 天）`;
}

function readout(pack: Pack, o: Observation, requestedScale: string | null, requestedValue: string | null): Readout {
  const page = o.source.printed_page ? `第 ${o.source.page} 页（印刷页 ${o.source.printed_page}）` : `第 ${o.source.page} 页`;
  let outcome: string, tone: 'ok' | 'warn';
  if (o.ocr.outcome === 'matched') { outcome = `OCR 复核一致：${o.ocr.method} 读到“${o.ocr.raw_value}”`; tone = 'ok'; }
  else if (o.ocr.outcome === 'registered_span') { outcome = `登记自 OCR 片段“${o.ocr.span?.text ?? ''}”（${OCR_REASON[o.ocr.reason]}）`; tone = 'warn'; }
  else { outcome = `OCR 未选出（${OCR_REASON[o.ocr.reason] ?? o.ocr.reason}）；数值以登记事实为准`; tone = 'warn'; }
  const unitRule = o.ocr.unit_check === 'unit_ready' ? '；单位规则适用于此行' : '';
  const source = o.evidence.method === 'pdf_text_layer' ? `PDF 文本层定位（${o.evidence.parser_version}）` : 'OCR 片段与 AI 辅助转写（定位框为近似）';
  return {
    observation_id: o.id, issuer: ZH[o.issuer_id], label: o.label, period: periodText(o),
    value: amountOf(o), requested: requestedScale && requestedValue ? `${group(requestedValue)} ${SCALE_LABEL[requestedScale]}` : null,
    page, bbox: fmtBox(o.source.amount_bbox), unit: `${o.source.unit_header ?? '登记单位：百万美元'}${unitRule}`,
    outcome, outcome_tone: tone, source,
    preview: pack.pages[`${o.issuer_id}:${o.source.page}`] ? { key: `${o.issuer_id}:${o.source.page}`, amount: o.source.amount_bbox, ocr: o.ocr.span?.bbox ?? null } : null,
    numbers: [o.normalized_value, ...(requestedValue ? [requestedValue] : [])],
  };
}

function percentRows(pack: Pack, p: PercentObservation): Readout[] {
  const { observations } = indexOf(pack);
  return p.input_ids.map(id => readout(pack, observations.get(id) as Observation, null, null));
}

function changeChecks(row: ChangeRow): Card['checks'] {
  const checks: Card['checks'] = [];
  if (row.status === 'computed') checks.push({ text: '可比性检查通过：同一公司、同一报表行、同一来源与单位、口径依据齐全、两期相邻且不重叠。', tone: 'ok' });
  for (const r of row.reasons) checks.push({ text: r.message, tone: 'stop' });
  for (const w of row.warnings) checks.push({ text: w.message, tone: 'warn' });
  return checks;
}

export function composeAnswer(pack: Pack, item: PlannedItem, notes: string[] = []): Card {
  const answer = answerPlan(pack, item.plan);
  const { observations, percents } = indexOf(pack);
  const card: Card = { label: item.label, status: answer.status, tone: 'stop', headline: '', rows: [], checks: [], notes: [...notes], formula: null, derived: [], engine: ENGINE_LABEL, answer };
  const metricLabel = pack.metrics[item.plan.items[0].metric]?.label ?? item.plan.items[0].metric;
  const note = pack.metrics[item.plan.items[0].metric]?.note;
  if (note && answer.status !== 'not_registered') card.notes.push(note);
  if (answer.gaps.length && !['answered', 'computed', 'side_by_side_only'].includes(answer.status)) {
    card.headline = `不作答：${answer.gaps.map(g => g.message).join(' ')}`;
    return card;
  }
  const values = answer.values;
  const obs = values.map(v => observations.get(v.observation_id) ?? percents.get(v.observation_id)) as (Observation | PercentObservation)[];
  if (answer.status === 'answered') {
    const o = obs[0], v = values[0];
    card.tone = 'ok';
    if (o.kind === 'percent') {
      const p = o as PercentObservation;
      card.rows = percentRows(pack, p);
      const [a, b] = card.rows;
      card.headline = `${ZH[p.issuer_id]} ${periodText(p)}${p.label}：${p.percent_2dp}%（6 位小数为 ${p.percent_6dp}%）。`;
      card.formula = `${p.label} = ${p.metric === 'gross_margin_pct' ? '毛利 ÷ 营业收入' : '(收入 − 成本) ÷ 收入'}，输入为下列登记值（${a.label} ${a.value.split(' ')[0]}，${b.label} ${b.value.split(' ')[0]}），登记计算 ${p.rule_version}。`;
      if (card.rows.some(r => r.outcome.startsWith('登记自 OCR'))) card.tone = 'warn';
      card.derived = [p.percent_2dp, p.percent_6dp];
    } else {
      const ob = o as Observation;
      card.rows = [readout(pack, ob, v.requested?.scale ?? null, v.requested?.value ?? null)];
      card.headline = `${ZH[ob.issuer_id]} ${periodText(ob)}${labelOf(ob)}：${amountOf(ob)}` + (v.requested ? `，折合 ${group(v.requested.value)} ${SCALE_LABEL[v.requested.scale]}（按 1 百万美元精确换算，未四舍五入）` : '') + '。';
      if (ob.ocr.outcome !== 'matched') card.tone = 'warn';
    }
    return card;
  }
  if (answer.status === 'side_by_side_only') {
    card.tone = 'warn';
    const parts = obs.map((o, i) => o.kind === 'percent' ? `${ZH[o.issuer_id]} ${periodText(o)}${o.label} ${(o as PercentObservation).percent_2dp}%`
      : `${ZH[o.issuer_id]} ${periodText(o)}${labelOf(o as Observation)} ${amountOf(o as Observation)}`
        + (values[i].requested ? `（= ${group(values[i].requested!.value)} ${SCALE_LABEL[values[i].requested!.scale]}）` : ''));
    card.rows = obs.flatMap((o, i) => o.kind === 'percent' ? percentRows(pack, o as PercentObservation)
      : [readout(pack, o as Observation, values[i].requested?.scale ?? null, values[i].requested?.value ?? null)]);
    card.derived = obs.filter(o => o.kind === 'percent').flatMap(o => [(o as PercentObservation).percent_2dp]);
    card.headline = (item.ranking_refused ? '不排名、不相减：两家公司不是登记的严格同行，演示不判断谁高谁低，也不计算差额。' : '只能并列，不能直接比较。') + `并列数值：${parts.join('；')}。`;
    card.checks = (answer.cross_company?.reasons ?? []).map(r => ({ text: r.message, tone: 'stop' as const }));
    return card;
  }
  const row = answer.comparison as ChangeRow;
  card.checks = changeChecks(row);
  const [first, second] = obs;
  if (first?.kind === 'percent' && row.status === 'computed') {
    card.checks = [{ text: '两期毛利率来自同一份登记的毛利率分解（margin-bridge-1）：同一公司、同一来源与单位、合并口径、完整年度均已检查。', tone: 'ok' }];
  }
  if (answer.status === 'blocked') {
    card.headline = `不能计算${metricLabel}的变化：${answer.gaps.map(g => g.message).join(' ')}`;
    card.rows = obs.flatMap(o => o.kind === 'percent' ? [] : [readout(pack, o as Observation, null, null)]);
    return card;
  }
  card.tone = 'ok';
  if (first.kind === 'percent') {
    const a = first as PercentObservation, b = second as PercentObservation;
    const pp = row.change_pp_2dp as string, pp6 = row.change_pp_6dp as string;
    const dir = pp.startsWith('-') ? '下降' : pp === '0.00' ? '持平，变动' : '上升';
    card.rows = [...percentRows(pack, a), ...percentRows(pack, b)];
    card.headline = `${ZH[a.issuer_id]}${a.label} FY${a.year} → FY${b.year}：${a.percent_2dp}% → ${b.percent_2dp}%，${dir} ${abs(pp)} 个百分点（6 位小数为 ${pp6} 个百分点）。`;
    card.formula = '变动 = 本期毛利率 − 上期毛利率；两期毛利率均为登记计算，输入见下列登记值。';
    card.derived = [a.percent_2dp, b.percent_2dp, pp, pp6];
    return card;
  }
  const a = first as Observation, b = second as Observation;
  card.rows = [readout(pack, a, null, null), readout(pack, b, null, null)];
  const change = row.absolute_change as string;
  const dir = change.startsWith('-') ? '减少' : change === '0' ? '持平，变化' : '增加';
  const growth = row.growth_rate_percent as string | null;
  const requested = row.requested_change as { scale: string; value: string } | null;
  const outflow = a.raw_signed_value !== a.normalized_value;
  card.headline = `${ZH[a.issuer_id]}${labelOf(a)} FY${a.year} → FY${b.year}：${shortAmount(a)} → ${shortAmount(b)} 百万美元${outflow ? '（现金流出绝对额）' : ''}，${dir} ${group(abs(change))} 百万美元`
    + (requested ? `（折合 ${group(abs(requested.value))} ${SCALE_LABEL[requested.scale]}）` : '')
    + (growth !== null ? `，变动率 ${growth.startsWith('-') ? '' : '+'}${growth}%` : '，基期不为正数，不计算变动率') + '。';
  card.formula = `差额 = ${shortAmount(b)} − ${shortAmount(a)}；变动率 = 差额 ÷ ${shortAmount(a)}，保留 6 位小数（四舍六入五成双）。${outflow ? `原文为括号负值 ${a.raw_value}、${b.raw_value}，按登记规则取现金流出绝对额后计算。` : ''}未经分析员采用，不写入研究底稿。`;
  card.derived = [change, ...(growth !== null ? [growth, row.growth_rate as string] : []), ...(requested ? [requested.value] : [])];
  if (row.warnings.length) card.tone = 'warn';
  return card;
}

const READING_KIND: Record<string, string> = { lookup: '查数', change: '两期变化', comparable: '可比性', rank: '排名（不提供）', diff: '跨公司差额（不提供）' };
export function compose(pack: Pack, question: string): Composed {
  const understood = understand(pack, question);
  if (understood.type !== 'plans') {
    return { type: understood.type, cards: [], message: understood.detail, unknown: understood.unknown ?? [], suggestions: understood.suggestions, notes: [], reading: null };
  }
  const r = understood.reading;
  const reading = [
    `公司：${r.issuers.map(i => ZH[i]).join('、')}`, `指标：${r.metrics.map(m => pack.metrics[m]?.label ?? m).join('、')}`,
    `期间：${r.years.length ? r.years.map(y => `FY${y}`).join('、') : '未指定'}`, `类型：${READING_KIND[r.intent]}`,
    `单位：${r.unit ? (r.unit.currency === 'USD' ? (r.unit.scale ? SCALE_LABEL[r.unit.scale] : '美元（按报表百万美元列示）') : r.unit.currency) : r.metrics.every(m => pack.metrics[m]?.kind === 'percent') ? '百分比' : '按报表（百万美元）'}`,
    `口径：${r.scope === 'parent_only' ? '母公司' : '合并'}`,
  ].join(' · ');
  const cards = understood.plans.map(item => composeAnswer(pack, item));
  return { type: 'plans', cards, message: null, unknown: [], suggestions: nextSteps(pack, understood), notes: r.notes, reading };
}

function nextSteps(pack: Pack, understood: Extract<Understood, { type: 'plans' }>): string[] {
  const { issuers, metrics, intent } = understood.reading;
  const label = pack.metrics[metrics[0]]?.label ?? metrics[0];
  const out: string[] = [];
  const issuer = ZH[issuers[0]];
  const { observations, percents } = indexOf(pack);
  const has = (i: string) => observations.has(`${i}:${metrics[0]}:2025`) || percents.has(`${i}:${metrics[0]}:2025`);
  const amount = pack.metrics[metrics[0]]?.kind !== 'percent';
  if (!has(issuers[0])) return [];
  const statuses = understood.plans.map(p => answerPlan(pack, p.plan).status);
  if (statuses.includes('unit_unavailable') && amount) out.push(`${issuer}FY${understood.reading.years[0] ?? 2025} ${label}是多少亿美元`);
  if (intent === 'lookup' && issuers.length === 1) out.push(`${issuer}FY2024 到 FY2025 ${label}变化了多少`);
  if (issuers.length === 1 && has('MSFT') && has('AAPL')) out.push(`微软和苹果 FY2025 的${label}能直接比吗`);
  if (intent !== 'lookup' && issuers.length === 1 && amount) out.push(`${issuer}FY2025 ${label}是多少亿美元`);
  return out.slice(0, 3);
}

/* Port of the comparability rules:
   - annualChange  = creditlens/research_materials.py::_annual_change
   - instantChange = creditlens/evidence_answers.py::_instant_change
   - crossReasons  = creditlens/evidence_answers.py::_cross_reasons
   parity.test.ts compares these with the Python on every pair and on perturbed inputs. */
import { cmp, div40, formatF, parse, quantize, sub } from './decimal';
import type { ChangeRow, Reason } from './types';

type Obs = Record<string, any>;
const reason = (code: string, message: string): Reason => ({ code, message });
const BASIS = ['consolidation_scope', 'operations_scope', 'capex_components', 'measurement_basis', 'definition'];
const EVIDENCE = ['consolidation_scope', 'operations_scope', 'capex_components'];

class InvalidDate extends Error {}

/** Strict YYYY-MM-DD (the only form the registered data uses), as days since epoch. */
function isoDay(value: unknown): number {
  if (typeof value !== 'string') throw new InvalidDate();
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) throw new InvalidDate();
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = Date.UTC(y, mo - 1, d);
  const back = new Date(t);
  if (y < 1 || back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) throw new InvalidDate();
  return t / 86400000;
}
const monthDay = (value: string) => value.slice(5);

function baseRow(previous: Obs, current: Obs, inputIds: string[]): ChangeRow {
  return {
    issuer_id: current.issuer_id, company: current.company, metric: current.metric,
    from_year: previous.year, to_year: current.year, input_ids: inputIds,
    candidate_observation_ids: [previous.id, current.id], status: 'blocked', reasons: [], warnings: [],
    absolute_change: null, growth_rate: null, growth_rate_status: 'blocked',
    formula: { absolute_change: 'current.normalized_value - previous.normalized_value',
               growth_rate: 'absolute_change / previous.normalized_value' },
    unit: current.unit,
  };
}

function identityAndBasis(previous: Obs, current: Obs, reasons: Reason[]) {
  for (const field of ['issuer_id', 'metric', 'unit', 'source_version_id', 'transformation']) {
    if (previous[field] !== current[field]) reasons.push(reason(`${field}_mismatch`, `两期的 ${field} 不一致。`));
  }
  for (const field of BASIS) {
    const values = [previous, current].map(x => x.basis?.[field]);
    if (values.some(v => v === null || v === undefined || v === '' || v === 'unknown')) reasons.push(reason('unknown_basis', `${field} 没有可核验口径。`));
    else if (values[0] !== values[1]) reasons.push(reason('basis_mismatch', `两期的 ${field} 不一致。`));
  }
}

function compute(row: ChangeRow, previous: Obs, current: Obs) {
  const base = parse(previous.normalized_value), value = parse(current.normalized_value);
  const change = sub(value, base);
  row.status = 'computed';
  row.absolute_change = formatF(change);
  if (cmp(base, { int: 0n, exp: 0 }) > 0) {
    row.growth_rate = formatF(quantize(div40(change, base), -6));
    row.growth_rate_status = 'computed';
  } else {
    row.growth_rate_status = 'not_computed_nonpositive_base';
    row.warnings.push(reason('nonpositive_base', '基期为零或负值，仅展示绝对变化，不给常规增长率。'));
  }
}

export function annualChange(previous: Obs, current: Obs, adopted: Set<string>): ChangeRow {
  const candidates = [previous.id, current.id];
  const row = baseRow(previous, current, candidates.filter(x => adopted.has(x)));
  const { reasons, warnings } = row;
  if (candidates.some(x => !adopted.has(x))) reasons.push(reason('observation_not_adopted', '两期事实必须都明确采用；拒绝或未评估的数据不参与计算。'));
  identityAndBasis(previous, current, reasons);
  for (const item of [previous, current]) {
    for (const field of EVIDENCE) {
      const ok = (item.basis?.scope_evidence ?? []).some((e: Obs) => e.field === field && e.value === item.basis?.[field]
        && e.source_sha256 === item.source.sha256 && e.page && e.quote);
      if (!ok) reasons.push(reason('basis_evidence_missing', `${item.fact_id} 的 ${field} 缺少同来源口径依据。`));
    }
  }
  const p = previous.period, c = current.period;
  try {
    const [ps, pe, cs, ce] = [p.start, p.end, c.start, c.end].map(isoDay);
    const lengths = [pe - ps + 1, ce - cs + 1];
    if (lengths[0] !== p.days || lengths[1] !== c.days || Math.min(...lengths) <= 0) reasons.push(reason('duration_mismatch', '登记天数与实际日期不一致。'));
    if (current.year !== previous.year + 1 || cs !== pe + 1 || ce <= pe) reasons.push(reason('annual_order_mismatch', '两期必须为相邻且不重叠的连续年度。'));
    const calendar = p.fiscal_calendar;
    if (p.kind !== 'annual' || c.kind !== 'annual' || calendar !== c.fiscal_calendar) reasons.push(reason('calendar_mismatch', '两期必须使用相同年度日历。'));
    else if (calendar === 'calendar_year') {
      if (!(lengths.every(n => n === 365 || n === 366) && monthDay(p.start) === monthDay(c.start) && monthDay(p.end) === monthDay(c.end))) {
        reasons.push(reason('annual_length_mismatch', '日历年度长度或起止边界不一致。'));
      } else if (lengths[0] !== lengths[1]) {
        warnings.push(reason('leap_year_length_difference', '完整日历年度包含闰日差异；按报表原值比较，未日均化或年化。'));
      }
    } else if (calendar === 'week_52_53') {
      if (!(lengths[0] === 364 || lengths[0] === 371) || lengths[0] !== lengths[1]) reasons.push(reason('annual_length_mismatch', '周历年度须同为 52 周或同为 53 周，不自动折算。'));
    } else reasons.push(reason('unknown_calendar', '年度日历未经支持。'));
  } catch (error) {
    if (!(error instanceof InvalidDate)) throw error;
    reasons.push(reason('invalid_period', '年度日期或天数无效。'));
  }
  if (reasons.length) return row;
  compute(row, previous, current);
  return row;
}

export function instantChange(previous: Obs, current: Obs): ChangeRow {
  const row = baseRow(previous, current, [previous.id, current.id]);
  const { reasons } = row;
  identityAndBasis(previous, current, reasons);
  const p = previous.period, c = current.period;
  if (p.kind !== 'instant' || c.kind !== 'instant') reasons.push(reason('period_kind_mismatch', '时点余额只能与时点余额比较。'));
  else {
    const pe = isoDay(p.end), ce = isoDay(c.end);
    if (current.year !== previous.year + 1 || ce <= pe) reasons.push(reason('annual_order_mismatch', '两期必须为相邻财年末，按时间先后排列。'));
    else if (p.fiscal_calendar !== c.fiscal_calendar) reasons.push(reason('calendar_mismatch', '两期必须使用相同年度日历。'));
    else if (p.fiscal_calendar === 'calendar_year') {
      if (monthDay(p.end) !== monthDay(c.end)) reasons.push(reason('year_end_mismatch', '两期财年末的月日不同。'));
    } else if (p.fiscal_calendar === 'week_52_53') {
      if (!(ce - pe === 364 || ce - pe === 371)) reasons.push(reason('year_end_mismatch', '周历财年末之间须相隔 52 或 53 周。'));
    } else reasons.push(reason('unknown_calendar', '年度日历未经支持。'));
  }
  if (reasons.length) return row;
  compute(row, previous, current);
  return row;
}

export function crossReasons(first: Obs, second: Obs): Reason[] {
  const reasons = [reason('not_strict_peer_benchmark', '微软/苹果为跨公司流程样例，未建立严格同行比较契约，不排名、不相减。')];
  if (first.metric !== second.metric) reasons.push(reason('metric_mismatch', '两项不是同一指标。'));
  const p = first.period, q = second.period;
  if (p.start !== q.start || p.end !== q.end) reasons.push(reason('actual_period_mismatch', '相同 FY 标签不代表相同实际期间；两项的起止日或时点不同。'));
  if (p.fiscal_calendar !== q.fiscal_calendar || p.days !== q.days) reasons.push(reason('annual_calendar_mismatch', '日历年度与周历年度长度不同，不自动年化。'));
  if ((first.row_label ?? null) !== (second.row_label ?? null) && first.kind !== 'percent') reasons.push(reason('line_definition_differs', '两家报表行名不同，定义未经逐项对齐。'));
  return reasons;
}

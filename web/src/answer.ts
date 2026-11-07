/* Port of creditlens/evidence_answers.py::answer_plan (and _normalize_plan, _project, _scaled).
   Deterministic and read-only; parity.test.ts checks it against the Python on every exported plan. */
import { formatF, parse, plain, quantize, scaleb, sub } from './decimal';
import { annualChange, crossReasons, instantChange } from './rules';
import type { Answer, Observation, Pack, PercentObservation, Plan, Reason, Value } from './types';

export const ANSWER_RULE_VERSION = 'evidence-answer-1';
const KINDS = ['lookup', 'change', 'comparable_check', 'cross_company'];
const SCOPES = ['consolidated', 'parent_only'];
const gap = (code: string, message: string): Reason => ({ code, message });

export class AnswerError extends Error {
  constructor(public code: string, message: string) { super(`${code}: ${message}`); }
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const keysWithin = (v: Record<string, unknown>, allowed: string[]) => Object.keys(v).every(k => allowed.includes(k));
const keysExactly = (v: Record<string, unknown>, allowed: string[]) => keysWithin(v, allowed) && allowed.every(k => k in v);

export function normalizePlan(pack: Pack, plan: unknown): Required<Plan> {
  if (!isObject(plan) || !keysWithin(plan, ['kind', 'items', 'unit', 'scope'])) throw new AnswerError('invalid_plan', '计划只接受 kind、items、unit、scope。');
  const { kind, items } = plan as Record<string, any>;
  if (!KINDS.includes(kind) || !Array.isArray(items) || items.length !== (kind === 'lookup' ? 1 : 2)) {
    throw new AnswerError('invalid_plan', '查询类型或所选项数量不符合固定契约。');
  }
  const clean = items.map((item: unknown) => {
    if (!isObject(item) || !keysExactly(item, ['issuer_id', 'metric', 'year']) || typeof item.issuer_id !== 'string'
        || typeof item.metric !== 'string' || typeof item.year !== 'number' || !Number.isInteger(item.year)) {
      throw new AnswerError('invalid_plan', '每项须为公司、指标和整数年度。');
    }
    return { issuer_id: item.issuer_id, metric: item.metric, year: item.year };
  });
  const unit = (plan as Record<string, any>).unit ?? null;
  if (unit !== null && (!isObject(unit) || !keysExactly(unit, ['currency', 'scale']) || typeof unit.currency !== 'string'
      || !(unit.scale === null || (typeof unit.scale === 'string' && unit.scale in pack.scales)))) {
    throw new AnswerError('invalid_plan', '单位须为币种与量级。');
  }
  const scope = 'scope' in plan ? (plan as Record<string, any>).scope : 'consolidated';
  if (!SCOPES.includes(scope)) throw new AnswerError('invalid_plan', '口径只支持合并或母公司。');
  return { kind, items: clean, unit: unit === null ? null : { currency: unit.currency, scale: unit.scale }, scope };
}

export function scaled(pack: Pack, value: string, scale: string): string {
  return plain(scaleb(parse(value), pack.scales[pack.registered_scale] - pack.scales[scale]));
}

function project(pack: Pack, observation: Observation | undefined, percent: PercentObservation | undefined, unit: Plan['unit']): Value {
  if (percent) {
    return { observation_id: percent.id, kind: 'percent', percent_2dp: percent.percent_2dp, percent_6dp: percent.percent_6dp, requested: null };
  }
  const o = observation as Observation;
  let requested = null;
  if (unit && unit.scale !== null && unit.scale !== pack.registered_scale) requested = { scale: unit.scale, value: scaled(pack, o.normalized_value, unit.scale) };
  return { observation_id: o.id, kind: o.kind, raw_value: o.raw_value, normalized_value: o.normalized_value, unit: o.unit, requested };
}

export interface Index { observations: Map<string, Observation>; percents: Map<string, PercentObservation> }
const indexes = new WeakMap<Pack, Index>();
export function indexOf(pack: Pack): Index {
  let index = indexes.get(pack);
  if (!index) {
    index = { observations: new Map(pack.observations.map(o => [o.id, o])), percents: new Map(pack.percents.map(o => [o.id, o])) };
    indexes.set(pack, index);
  }
  return index;
}

export function answerPlan(pack: Pack, input: unknown): Answer {
  const plan = normalizePlan(pack, input);
  const { observations, percents } = indexOf(pack);
  const gaps: Reason[] = [];
  const selected: Answer['selected'] = [];
  const found: [Observation | undefined, PercentObservation | undefined][] = [];
  const unit = plan.unit;
  for (const item of plan.items) {
    const key = `${item.issuer_id}:${item.metric}:${item.year}`;
    const hitO = observations.get(key), hitP = percents.get(key);
    const issuer = pack.issuers[item.issuer_id], metric = pack.metrics[item.metric];
    if (!issuer) gaps.push(gap('issuer_not_registered', `演示没有登记 ${item.issuer_id} 的年报。`));
    else if (!metric) gaps.push(gap('metric_not_registered', `演示没有登记指标 ${item.metric}。`));
    else if (!pack.years.includes(item.year)) gaps.push(gap('year_not_registered', `FY${item.year} 未登记；演示只有 FY2024、FY2025 年度列。`));
    else if (!hitO && !hitP) gaps.push(gap('observation_not_registered', `${issuer.name_zh}没有登记“${metric.label}”这一行；不补值，也不代表公司未披露。`));
    selected.push({ selector: { ...item }, status: hitO || hitP ? 'registered' : 'not_registered' });
    found.push([hitO, hitP]);
  }
  if (plan.scope === 'parent_only') gaps.push(gap('scope_not_registered', '只登记了合并报表；未登记母公司（单体）报表，不能用合并数代替。'));
  if (unit && unit.currency !== pack.currency) gaps.push(gap('currency_not_registered', `报表以美元列示，未登记汇率，不能换算为 ${unit.currency}。`));
  if (unit && found.some(([, p]) => p)) gaps.push(gap('unit_not_applicable', '毛利率是百分比，不能用金额单位表示。'));
  const result: Answer = {
    schema_version: 1, rule_version: ANSWER_RULE_VERSION, plan, selected, values: [], comparison: null, cross_company: null,
    status: '', gaps, read_only: true, adopted_by_analyst: false, ranking: null,
  };
  if (gaps.length) {
    const codes = new Set(gaps.map(g => g.code));
    result.status = codes.has('scope_not_registered') ? 'scope_unavailable'
      : (codes.has('currency_not_registered') || codes.has('unit_not_applicable')) && selected.every(s => s.status === 'registered') ? 'unit_unavailable'
      : 'not_registered';
    return result;
  }
  result.values = found.map(([o, p]) => project(pack, o, p, unit));
  if (plan.kind === 'lookup') { result.status = 'answered'; return result; }
  const first = (found[0][0] ?? found[0][1]) as any, second = (found[1][0] ?? found[1][1]) as any;
  if (first.id === second.id) {
    result.status = 'blocked'; result.gaps = [gap('duplicate_selection', '两期不能重复选择同一项。')]; return result;
  }
  if (first.issuer_id !== second.issuer_id || plan.kind === 'cross_company') {
    if (first.issuer_id === second.issuer_id) {
      result.status = 'blocked'; result.gaps = [gap('same_issuer', '跨公司并列需要两家不同公司。')]; return result;
    }
    result.status = 'side_by_side_only';
    result.cross_company = { reasons: crossReasons(first, second), ranking: null, difference: null };
    return result;
  }
  if (first.metric !== second.metric) {
    result.status = 'blocked'; result.gaps = [gap('metric_mismatch', '不同指标不能作为同一指标的基期和本期。')]; return result;
  }
  if (first.kind === 'percent') {
    if (first.year !== 2024 || second.year !== 2025) {
      result.status = 'blocked'; result.gaps = [gap('annual_order_mismatch', '两期必须为相邻财年，按时间先后排列。')]; return result;
    }
    const delta = sub(parse(second.exact_percent), parse(first.exact_percent));
    result.comparison = {
      status: 'computed', metric: first.metric, from_year: first.year, to_year: second.year, reasons: [], warnings: [],
      change_pp_6dp: formatF(quantize(delta, -6)), change_pp_2dp: formatF(quantize(delta, -2)),
      formula: 'current.percent - previous.percent',
    };
  } else if (first.kind === 'instant') {
    result.comparison = instantChange(first, second);
  } else {
    result.comparison = annualChange(first, second, new Set([first.id, second.id]));
  }
  const row = result.comparison;
  if (row.status !== 'computed') {
    result.status = 'blocked';
    result.gaps = row.reasons.map(r => gap(r.code, r.message));
    return result;
  }
  if (first.kind !== 'percent') {
    row.growth_rate_percent = row.growth_rate === null ? null : plain(scaleb(parse(row.growth_rate as string), 2));
    row.requested_change = null;
    if (unit && unit.scale !== null && unit.scale !== pack.registered_scale) {
      row.requested_change = { scale: unit.scale, value: scaled(pack, row.absolute_change as string, unit.scale) };
    }
  }
  result.status = 'computed';
  return result;
}

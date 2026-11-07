/* Scorer for tests/browser_answers/blind-set.json (written by another model from BLIND_SCOPE.md only).
   Rules, fixed before the first run:
   - The engine "states" a case when any plan answers with a value (status answered / computed /
     side_by_side_only). Refusals, clarifications and not_registered / unit_unavailable / scope_unavailable /
     blocked answers state no value.
   - expect=answer: correct when it states values and the stated (issuer, metric, fiscal year) set equals the
     expected set (empty expected years accept either registered year), the kind family matches
     (lookup/multi_lookup → lookup; change/comparable_check → change or comparable_check; cross_company →
     cross_company) and the requested scale matches. Stating anything else is WRONG; stating nothing is ABSTAINED.
   - expect=refuse: correct when nothing is stated, or — for trap "ranking" only — when every plan is a
     side-by-side view with the ranking and difference refused. Stating a value otherwise is WRONG.
   - RS(c) as in TrustSQL: +1 correct, 0 abstained, −c wrong, averaged over all cases. */
import { answerPlan } from '../src/answer';
import { understand } from '../src/understand';
import type { Pack } from '../src/types';

export interface BlindCase {
  id: string; question: string; expect: 'answer' | 'refuse'; kind: string; issuers: string[]; metrics: string[];
  fiscal_years: number[]; requested_unit: string | null; trap: string | null; wording: string;
}
const STATED = new Set(['answered', 'computed', 'side_by_side_only']);
const UNIT: Record<string, string | null> = { USD_million: 'million', USD_100m: 'hundred_million', USD_10k: 'ten_thousand', USD_thousand: 'thousand', USD: 'one', USD_billion: 'billion' };
const FAMILY: Record<string, string> = { lookup: 'lookup', multi_lookup: 'lookup', change: 'change', comparable_check: 'change', cross_company: 'cross_company', comparable: 'change' };

export function scoreCase(pack: Pack, c: BlindCase) {
  const u = understand(pack, c.question);
  const plans = u.type === 'plans' ? u.plans : [];
  const answers = plans.map(p => answerPlan(pack, p.plan));
  const stated = answers.some(a => STATED.has(a.status));
  const items = new Set<string>();
  const kinds = new Set<string>();
  const scales = new Set<string | null>();
  plans.forEach((p, i) => {
    if (!STATED.has(answers[i].status)) return;
    p.plan.items.forEach(it => items.add(`${it.issuer_id}:${it.metric}:${it.year}`));
    kinds.add(FAMILY[p.plan.kind === 'comparable_check' ? 'change' : p.plan.kind]);
    scales.add(p.plan.unit?.scale ?? null);
  });
  let verdict: 'correct' | 'wrong' | 'abstained';
  let why = '';
  if (c.expect === 'answer') {
    if (!stated) { verdict = 'abstained'; why = u.type !== 'plans' ? `${u.type}:${u.reason}` : answers.map(a => a.status).join(','); }
    else {
      const years = c.fiscal_years.length ? c.fiscal_years : [2024, 2025];
      const expected = new Set<string>();
      for (const i of c.issuers) for (const m of c.metrics) for (const y of years) expected.add(`${i}:${m}:${y}`);
      const sameItems = c.fiscal_years.length ? [...items].sort().join() === [...expected].sort().join()
        : [...items].every(x => expected.has(x)) && c.issuers.every(i => c.metrics.every(m => [...items].some(x => x.startsWith(`${i}:${m}:`))));
      const kindOk = kinds.size === 1 && kinds.has(FAMILY[c.kind] ?? c.kind);
      const want = c.requested_unit ? UNIT[c.requested_unit] : null;
      const scaleOk = [...scales].every(s => (s ?? 'million') === (want ?? 'million'));
      verdict = sameItems && kindOk && scaleOk ? 'correct' : 'wrong';
      if (verdict === 'wrong') why = `items=${[...items].join('|')} kinds=${[...kinds].join('|')} scales=${[...scales].join('|')}`;
    }
  } else {
    const rankingOnly = c.trap === 'ranking' && plans.length > 0 && plans.every((p, i) => p.plan.kind === 'cross_company' && p.ranking_refused && answers[i].status === 'side_by_side_only');
    verdict = !stated || rankingOnly ? 'correct' : 'wrong';
    if (verdict === 'wrong') why = `stated ${answers.map(a => a.status).join(',')} items=${[...items].join('|')}`;
  }
  return { id: c.id, expect: c.expect, kind: c.kind, trap: c.trap, wording: c.wording, verdict, engine: u.type === 'plans' ? answers.map(a => a.status).join(',') : `${u.type}:${u.reason}`, why };
}

export function scoreSet(pack: Pack, cases: BlindCase[]) {
  const rows = cases.map(c => scoreCase(pack, c));
  const count = (f: (r: typeof rows[number]) => boolean) => rows.filter(f).length;
  const n = rows.length;
  const correct = count(r => r.verdict === 'correct'), wrong = count(r => r.verdict === 'wrong'), abstained = count(r => r.verdict === 'abstained');
  const rs = (c: number) => Number(((correct - c * wrong) / n).toFixed(4));
  const by = (key: 'expect' | 'trap' | 'wording' | 'kind') => {
    const out: Record<string, { n: number; correct: number; wrong: number; abstained: number }> = {};
    for (const r of rows) {
      const k = String(r[key]);
      out[k] ??= { n: 0, correct: 0, wrong: 0, abstained: 0 };
      out[k].n += 1; out[k][r.verdict] += 1;
    }
    return out;
  };
  return { n, correct, wrong, abstained, rs0: rs(0), rs10: rs(10), by_expect: by('expect'), by_trap: by('trap'), by_wording: by('wording'), by_kind: by('kind'), rows };
}

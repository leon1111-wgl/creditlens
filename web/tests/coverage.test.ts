/* Every metric and question kind the demo claims, in two wordings, plus the trap classes.
   Regression cases written by the developer (not a blind measurement). */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { answerPlan } from '../src/answer';
import { understand } from '../src/understand';
import { loadPack } from './helpers';

const pack = loadPack();
const coverage = JSON.parse(readFileSync(new URL('./coverage.json', import.meta.url), 'utf8'));
const planKey = (p: { kind: string; items: { issuer_id: string; metric: string; year: number }[] }) =>
  [p.kind, ...p.items.flatMap(i => [i.issuer_id, i.metric, String(i.year)])].join(' ').replace(/ (MSFT|AAPL) (\w+) (\d{4}) \1 \2 (\d{4})$/, ' $1 $2 $3 $4');

describe('coverage: two wordings per claimed metric, kind and trap', () => {
  it('names every registered metric at least twice', () => {
    const seen: Record<string, number> = {};
    for (const c of coverage.cases) for (const p of c.plans ?? []) { const m = p.split(' ')[2]; seen[m] = (seen[m] ?? 0) + 1; }
    for (const metric of Object.keys(pack.metrics)) expect(seen[metric] ?? 0, metric).toBeGreaterThanOrEqual(2);
  });
  for (const c of coverage.cases) {
    it(c.q, () => {
      const u = understand(pack, c.q);
      if (c.refuse || c.clarify) {
        expect(u.type, JSON.stringify(u)).toBe(c.refuse ? 'refuse' : 'clarify');
        if (u.type !== 'plans') expect(u.reason).toBe(c.refuse ?? c.clarify);
        return;
      }
      expect(u.type, JSON.stringify(u)).toBe('plans');
      if (u.type !== 'plans') return;
      expect(u.plans.map(p => planKey(p.plan))).toEqual(c.plans);
      expect(u.plans.map(p => answerPlan(pack, p.plan).status)).toEqual(c.status);
      if (c.unit) expect(u.plans[0].plan.unit?.scale).toBe(c.unit);
      if (c.ranking_refused !== undefined) expect(u.plans.every(p => p.ranking_refused === c.ranking_refused)).toBe(true);
    });
  }
});

/* The developer's own visitor probe (tests/probe.json): regression, not a measurement. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { answerPlan } from '../src/answer';
import { understand } from '../src/understand';
import { loadPack } from './helpers';

const pack = loadPack();
const probe = JSON.parse(readFileSync(new URL('./probe.json', import.meta.url), 'utf8'));
describe('own visitor probe', () => {
  for (const c of probe.cases) it(c.q, () => {
    const u = understand(pack, c.q);
    if (c.items) {
      expect(u.type).toBe('plans');
      if (u.type !== 'plans') return;
      expect(u.plans.map(p => `${p.plan.kind} ${p.plan.items.map(i => `${i.issuer_id}:${i.metric}:${i.year}`).join(' ')}${p.plan.unit ? ' ' + p.plan.unit.currency + ':' + p.plan.unit.scale : ''}${p.ranking_refused ? ' ranking_refused' : ''}`)).toEqual(c.items);
      expect(u.plans.map(p => answerPlan(pack, p.plan).status)).toEqual(c.status);
    } else {
      expect(u.type).toBe(c.refuse ? 'refuse' : 'clarify');
      if (u.type !== 'plans') expect(u.reason).toBe(c.refuse ?? c.clarify);
    }
  });
});

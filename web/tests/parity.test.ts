/* Python ⇄ TypeScript parity. The fixture is generated (gitignored):
   python scripts/export_answer_parity.py  →  web/tests/fixtures/parity.json */
import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { AnswerError, answerPlan } from '../src/answer';
import { annualChange, instantChange } from '../src/rules';
import { loadPack } from './helpers';

const FIXTURE = new URL('./fixtures/parity.json', import.meta.url);
const pack = loadPack();

describe('parity with creditlens/evidence_answers.py', () => {
  it('has the generated fixture (run scripts/export_answer_parity.py first)', () => {
    expect(existsSync(FIXTURE)).toBe(true);
  });
  const fixture = existsSync(FIXTURE) ? JSON.parse(readFileSync(FIXTURE, 'utf8')) : { answers: [], rules: [] };

  it(`answers every exported plan identically (${fixture.answers.length} plans)`, () => {
    expect(fixture.answers.length).toBeGreaterThan(10000);
    const mismatches: unknown[] = [];
    for (const item of fixture.answers) {
      let actual: unknown;
      try { actual = { answer: answerPlan(pack, item.plan) }; }
      catch (error) { actual = { error: error instanceof AnswerError ? error.code : String(error) }; }
      const expected = 'error' in item ? { error: item.error } : { answer: item.answer };
      if (JSON.stringify(sortKeys(actual)) !== JSON.stringify(sortKeys(expected))) mismatches.push({ plan: item.plan, actual, expected });
      if (mismatches.length > 3) break;
    }
    expect(mismatches).toEqual([]);
  });

  it(`applies both comparability rules identically on perturbed pairs (${fixture.rules.length} pairs)`, () => {
    expect(fixture.rules.length).toBeGreaterThan(400);
    for (const item of fixture.rules) {
      const actual = item.rule === 'instant' ? instantChange(item.previous, item.current)
        : annualChange(item.previous, item.current, new Set(item.adopted));
      expect(sortKeys(actual)).toEqual(sortKeys(item.result));
    }
  });
});

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value as object).sort().map(k => [k, sortKeys((value as Record<string, unknown>)[k])]));
  }
  return value;
}

/* Citation discipline: every number a headline states is a registered value shown in a
   readout row, an exact scale conversion of one, or a derived value listed with its formula;
   the rest are years, dates, page numbers and day counts from the same rows. */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { compose, group } from '../src/compose';
import { loadPack } from './helpers';

const pack = loadPack();
const coverage = JSON.parse(readFileSync(new URL('./coverage.json', import.meta.url), 'utf8'));
const strip = (s: string) => s.replace(/,/g, '').replace(/^-/, '');

function allowed(card: ReturnType<typeof compose>['cards'][number]): Set<string> {
  const out = new Set<string>();
  const add = (s: string | null | undefined) => { if (s) for (const n of s.match(/\d[\d,]*(?:\.\d+)?/g) ?? []) out.add(strip(n)); };
  for (const r of card.rows) { r.numbers.forEach(n => out.add(strip(n))); add(r.value); add(r.requested); add(r.period); add(r.page); }
  card.derived.forEach(d => out.add(strip(d)));
  for (const n of card.derived) out.add(strip(group(n)));
  ['1', '6', '2024', '2025', '2023', '2026', '100'].forEach(n => out.add(n));
  return out;
}

describe('every stated number is cited', () => {
  const questions: string[] = coverage.cases.map((c: { q: string }) => c.q);
  for (const q of questions) {
    it(q, () => {
      const composed = compose(pack, q);
      for (const card of composed.cards) {
        const ok = allowed(card);
        for (const n of card.headline.match(/\d[\d,]*(?:\.\d+)?/g) ?? []) expect(ok.has(strip(n)), `${n} in ${card.headline}`).toBe(true);
        for (const r of card.rows) expect(r.page).toMatch(/第 \d+ 页/);
        expect(card.engine).toContain('不是模型');
      }
    });
  }
  it('formats grouped exact values', () => {
    expect(group('281724000000')).toBe('281,724,000,000');
    expect(group('2817.24')).toBe('2,817.24');
    expect(group('-6772')).toBe('-6,772');
  });
});

describe('suggested next questions answer', () => {
  it('every suggestion shown after an answer or refusal is itself answerable', () => {
    const seen = new Set<string>();
    for (const c of coverage.cases) for (const s of compose(pack, c.q).suggestions) seen.add(s);
    expect(seen.size).toBeGreaterThan(5);
    for (const s of seen) {
      const composed = compose(pack, s);
      expect(composed.type, s).toBe('plans');
      for (const card of composed.cards) expect(['answered', 'computed', 'side_by_side_only'], `${s}: ${card.status}`).toContain(card.status);
    }
  });
});

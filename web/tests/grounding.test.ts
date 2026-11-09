import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  countOutcomes, filterFacts, formatPeriod, groupDigits, power, readingOrder, readoutRows, reasonText, type Fact, type Index,
} from '../src/grounding-model';

const base: Fact = {
  page: 3, id: 7, concept: 'us-gaap:NetCashProvidedByUsedInInvestingActivities', context: 'c-4',
  period: { start: '2025-01-01', end: '2025-12-31' }, dims: [], unit: 'iso4217:USD', decimals: '-6', scale: '6', sign: '-',
  format: 'ixt:num-dot-decimal', text: '1,234', printed_negative: true, box: { x0: 400, top: 120, x1: 425, bottom: 130 },
  year: 2025, printed_value: '-1234000000', xbrl_value: '-1234000000', outcome: 'match', reason: null,
  checks: { digits: true, sign: true, scale: true, period: true },
  ocr: { text: '(1,234)', confidence: 0.991, box: { x0: 397, top: 120, x1: 428, bottom: 130 }, hits: 1 },
  read: { magnitude: '1234', negative: true, scale: 6, year: 2025, unit_text: '(In millions)', column_text: '2025' }, flags: [],
};

describe('grounding readout', () => {
  it('shows the key and the image reading separately', () => {
    const rows = readoutRows(base);
    const get = (label: string) => rows.find(r => r.label === label)?.value;
    expect(get('结果')).toBe('一致');
    expect(get('XBRL 概念')).toBe('us-gaap:NetCashProvidedByUsedInInvestingActivities');
    expect(get('上下文')).toBe('c-4 · 2025-01-01 至 2025-12-31');
    expect(get('原文')).toBe('(1,234) · 印有括号');
    expect(get('答案值')).toBe('-1,234,000,000 · 年份列 2025');
    expect(get('图像读数')).toBe('-1,234 × 10⁶ · 2025 列');
    expect(rows.some(r => r.label === 'XBRL 值')).toBe(false);
  });

  it('names a presentation sign difference without calling the reading wrong', () => {
    const fact: Fact = { ...base, sign: null, xbrl_value: '1234000000', flags: ['sign_differs'] };
    const rows = readoutRows(fact);
    expect(rows.find(r => r.label === 'XBRL 值')?.value).toContain('标签符号与印刷括号不同');
    expect(rows.find(r => r.label === '结果')?.value).toBe('一致');
  });

  it('explains abstentions and conflict components in words', () => {
    expect(reasonText({ outcome: 'abstain', reason: 'merged_or_misaligned_region' })).toContain('8 pt');
    expect(reasonText({ outcome: 'conflict', reason: 'sign+period' })).toBe('括号符号不同、年份列不同');
    const abstain: Fact = { ...base, outcome: 'abstain', reason: 'share_or_per_share_row', read: { ...base.read!, scale: null } };
    expect(readoutRows(abstain).find(r => r.label === '图像读数')?.value).toContain('量级未定');
  });

  it('formats without rounding', () => {
    expect(groupDigits('-1234567.125')).toBe('-1,234,567.125');
    expect(power(-3)).toBe(' × 10⁻³');
    expect(power('0')).toBe('');
    expect(formatPeriod({ instant: '2025-12-31' })).toBe('时点 2025-12-31');
  });

  it('filters, counts and orders facts', () => {
    const facts: Fact[] = [
      { ...base, id: 1, outcome: 'abstain', box: { x0: 300, top: 200, x1: 320, bottom: 210 } },
      { ...base, id: 2, outcome: 'conflict', box: { x0: 100, top: 100, x1: 120, bottom: 110 } },
      { ...base, id: 3, box: { x0: 50, top: 201, x1: 70, bottom: 211 } },
    ];
    expect(filterFacts(facts, 'conflict').map(f => f.id)).toEqual([2]);
    expect(countOutcomes(facts)).toEqual({ match: 1, conflict: 1, abstain: 1, not_scored: 0 });
    expect(readingOrder(facts).map(f => f.id)).toEqual([2, 3, 1]);
  });
});

// The published run (creditlens/grounding/) must agree with itself: every fact file and image
// matches its hash, outcomes add up to the saved per-issuer totals, boxes lie on their page.
const folder = resolve(__dirname, '../../creditlens/grounding');
describe.skipIf(!existsSync(resolve(folder, 'index.json')))('published grounding run', () => {
  const load = (): Index => JSON.parse(readFileSync(resolve(folder, 'index.json'), 'utf8'));
  it('has provenance on every issuer and at most four pages each', () => {
    const index = load();
    for (const issuer of index.issuers) {
      expect(issuer.filing.accession).toMatch(/^\d{10}-\d{2}-\d{6}$/);
      expect(issuer.filing.document_url.startsWith('https://www.sec.gov/Archives/edgar/data/')).toBe(true);
      // The bytes came from an Internet Archive capture of that SEC URL; the page shows when and where.
      expect(issuer.filing.archive_url).toBe(`https://web.archive.org/web/${issuer.filing.capture_timestamp}id_/${issuer.filing.document_url}`);
      expect(issuer.pages.length).toBeGreaterThan(0);
      expect(issuer.pages.length).toBeLessThanOrEqual(4);
    }
    expect(index.image_bytes).toBeLessThan(20 * 1024 * 1024);
  });
  it('matches the saved totals and hashes', () => {
    const index = load();
    for (const issuer of index.issuers) {
      const raw = readFileSync(resolve(folder, issuer.facts_file));
      expect(createHash('sha256').update(raw).digest('hex')).toBe(issuer.facts_file_sha256);
      const facts: Fact[] = JSON.parse(raw.toString('utf8')).facts;
      const counts = countOutcomes(facts);
      const totals = index.by_issuer[issuer.id];
      expect({ match: counts.match, conflict: counts.conflict, abstain: counts.abstain }).toEqual(totals.outcomes);
      expect(counts.match + counts.conflict + counts.abstain).toBe(totals.facts_scored);
      for (const page of issuer.pages) {
        const image = readFileSync(resolve(folder, page.image));
        expect(createHash('sha256').update(image).digest('hex')).toBe(page.sha256);
        for (const f of facts.filter(x => x.page === page.page)) {
          expect(f.box.x0).toBeGreaterThanOrEqual(0);
          expect(f.box.x1).toBeLessThanOrEqual(page.width + 0.01);
          expect(f.box.bottom).toBeLessThanOrEqual(page.height + 0.01);
          if (f.outcome === 'match') expect(f.checks).toEqual({ digits: true, sign: true, scale: true, period: true });
          if (f.outcome === 'conflict') expect(Object.values(f.checks ?? {})).toContain(false);
        }
      }
    }
  });
});

// @vitest-environment happy-dom
import { describe, expect, test } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { factValue, FilingError, parseFiling, sniff } from '../src/snapshot/ixbrl';
import { computeSnapshot } from '../src/snapshot/model';
import { sanitize, serialize } from '../src/snapshot/sanitize';
import { amountText, parseAmountText } from '../src/snapshot/format';
import { memoDocx, memoMarkdown, snapshotCsv, zipStore, type SourceInfo } from '../src/snapshot/export';
import { hostileFiling, syntheticFiling } from './snapshot-fixture';

const snap = (html: string) => computeSnapshot(parseFiling(html));
const val = (s: ReturnType<typeof snap>, k: string) => s.inputs[k]?.value ?? null;
const enc = (s: string) => new TextEncoder().encode(s);

describe('reading tags', () => {
  test('ixt formats, scale and sign', () => {
    expect(factValue('1,234', 'num-dot-decimal', 6, '')).toBe('1234000000');
    expect(factValue('1.234,5', 'num-comma-decimal', 3, '')).toBe('1234500');
    expect(factValue('—', 'fixed-zero', 6, '')).toBe('0');
    expect(factValue('25', 'num-dot-decimal', 6, '-')).toBe('-25000000');
    expect(factValue('13.7', 'num-dot-decimal', 9, '')).toBe('13700000000');
    expect(factValue('0.00001', '', 0, '')).toBe('0.00001');
    expect(factValue('twenty-one', 'numwordsen', 0, '')).toBe('21');
    expect(factValue('no', 'numwordsen', 0, '')).toBe('0');
    expect(factValue('(5)', 'num-dot-decimal', 0, '')).toBeNull();
    expect(factValue('12', 'date-monthname-day-en', 0, '')).toBeNull();
  });
  test('refuses what is not an Inline XBRL filing, with the reason', () => {
    const code = (fn: () => unknown) => { try { fn(); } catch (e) { return (e as FilingError).code; } return 'none'; };
    expect(code(() => sniff(new Uint8Array()))).toBe('empty');
    expect(code(() => sniff(enc('%PDF-1.7 ...')))).toBe('pdf');
    expect(code(() => sniff(enc('PK\u0003\u0004...')))).toBe('zip');
    expect(code(() => sniff(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0, 0, 0])))).toBe('binary');
    expect(code(() => sniff(new Uint8Array(31 * 1024 * 1024)))).toBe('too_large');
    expect(code(() => parseFiling('<html><body><table><tr><td>1</td></tr></table></body></html>'))).toBe('not_ixbrl');
    expect(code(() => parseFiling('<html><head><title>Inline XBRL Viewer</title></head><body><div id="app-inline-xbrl"></div></body></html>'))).toBe('viewer');
    expect(code(() => parseFiling('<html><body><ix:header></ix:header><ix:nonFraction name="us-gaap:Assets">1</ix:nonFraction></body></html>'))).toBe('no_contexts');
  });
  test('hidden facts are anchored at their -sec-ix-hidden display', () => {
    const p = parseFiling(syntheticFiling());
    const ol = p.facts.find(f => f.local === 'OperatingLeaseLiability')!;
    expect(ol.hidden).toBe(true);
    expect(ol.display).toBe(true);
    expect(p.doc.querySelector(`[data-clh="${ol.i}"]`)?.textContent).toBe('200');
    expect(p.facts.every(f => p.doc.querySelector(`[data-clf="${f.i}"]`))).toBe(true);
  });
});

describe('definitions credit-snapshot-def-1 on the SYNTHETIC filing', () => {
  const s = snap(syntheticFiling());
  test('period, template, currency', () => {
    expect(s.status).toBe('ok');
    expect(s.meta).toMatchObject({ form: '10-K', period_end: '2025-12-31', period_start: '2025-01-01', months: 12, currency: 'USD', display_scale: 6, cik: '9999999', ticker: 'LSYN' });
  });
  test('debt = balance-sheet debt rows; a subtotal that adds up absorbs its rows; an extension borrowing is a cue, not debt', () => {
    expect(val(s, 'debt')).toBe('2250000000');
    expect(s.inputs.debt.terms.map(t => t.concept)).toEqual(['us-gaap:DebtCurrent', 'us-gaap:LongTermDebtNoncurrent']);
    expect(val(s, 'current_debt')).toBe('250000000');
    expect(s.cues.find(c => c.id === 'Q12')?.text).toMatch(/lsi:OtherBorrowingsFromAffiliates/);
  });
  test('liquidity subtotal absorbs cash and short-term investments', () => {
    expect(val(s, 'liquidity')).toBe('1500000000');
    expect(s.inputs.liquidity.terms).toHaveLength(1);
  });
  test('flows, leases, ratios', () => {
    expect([val(s, 'ebit'), val(s, 'da'), val(s, 'interest'), val(s, 'cfo'), val(s, 'capex')]).toEqual(['1100000000', '450000000', '120000000', '1400000000', '600000000']);
    expect(s.metrics.ebitda.value).toBe('1550000000');
    expect(s.metrics.net_debt.value).toBe('750000000');
    expect(s.metrics.fcf.value).toBe('800000000');
    expect(s.metrics.debt_to_ebitda.value as number).toBeCloseTo(2250 / 1550, 10);
    expect(s.metrics.net_debt_to_ebitda.value as number).toBeCloseTo(750 / 1550, 10);
    expect(s.metrics.interest_coverage.value as number).toBeCloseTo(1100 / 120, 10);
    expect(s.metrics.current_ratio.value as number).toBeCloseTo(2600 / 790, 10);
    expect(s.metrics.fcf_to_debt.value as number).toBeCloseTo(800 / 2250, 10);
    expect([val(s, 'operating_leases'), val(s, 'finance_leases')]).toEqual(['200000000', '50000000']);
    expect(s.metrics.debt_with_leases.value).toBe('2500000000');
  });
  test('maturity ladder with a missing year', () => {
    expect(s.ladder).toMatchObject({ next12: { value: '150000000' }, years2to5: { value: '900000000' }, after5: { value: '1100000000' }, missing_years: [4] });
  });
  test('cues are facts to look at, never verdicts', () => {
    expect(s.cues.map(c => c.id)).toEqual(['Q12', 'Q16']);
    for (const c of s.cues) expect(c.text).not.toMatch(/评级|建议买|卖出|违约风险高|投资级/);
  });
});

describe('templates and fallbacks', () => {
  test('a bank filing is not supported, with the triggering tag', () => {
    const s = snap(syntheticFiling({ bank: true }));
    expect(s.status).toBe('unsupported');
    expect(s.template.kind).toBe('bank');
    expect(s.template.facts.length).toBe(1);
  });
  test('unsupported form type', () => {
    expect(snap(syntheticFiling({ form: '20-F' })).template.kind).toBe('unsupported_form');
  });
  test('10-Q: year-to-date flows, no leverage multiples', () => {
    const s = snap(syntheticFiling({ form: '10-Q', ytdMonths: 6 }));
    expect(s.meta.months).toBe(6);
    expect(s.meta.period_start).toBe('2026-01-01');
    expect(s.metrics.debt_to_ebitda.value).toBeNull();
    expect(s.metrics.debt_to_ebitda.reason).toMatch(/6 个月/);
    expect(s.metrics.fcf_to_debt.value).toBeNull();
    expect(s.metrics.interest_coverage.value).not.toBeNull();
    expect(s.cues[0].id).toBe('Q1');
  });
  test('no balance sheet table: F1 from the notes, with a cue', () => {
    const s = snap(syntheticFiling({ noBalanceSheet: true }));
    expect(s.inputs.debt.rule).toMatch(/^F1/);
    expect(val(s, 'debt')).toBe('2150000000');
    expect(s.cues.some(c => c.id === 'Q12')).toBe(true);
    expect(s.metrics.current_ratio.value).toBeNull();
  });
  test('a subtotal concept that does not add up counts as a line, with Q14', () => {
    const extra = `<tr><td>Short-term borrowings</td><td>${'<ix:nonFraction unitRef="usd" contextRef="c-i" decimals="-6" name="us-gaap:ShortTermBorrowings" format="ixt:num-dot-decimal" scale="6">60</ix:nonFraction>'}</td></tr>`;
    const s = snap(syntheticFiling({ extraBalanceRows: extra }));
    expect(val(s, 'debt')).toBe('2310000000');
    expect(s.cues.some(c => c.id === 'Q14')).toBe(true);
  });
});

describe('sandboxed view', () => {
  test('removes scripts, frames, handlers, external links and loads; keeps in-document links; puts a CSP first', () => {
    const p = parseFiling(hostileFiling());
    sanitize(p.doc);
    const html = serialize(p.doc);
    expect(html).not.toMatch(/<script|<iframe|<object|<embed|<base|<link[\s>]|http-equiv="refresh"|javascript:|onerror|onload|onclick|example\.invalid|<set|@import/i);
    expect(html).toMatch(/href="#notes"/);
    expect(p.doc.head.firstElementChild?.getAttribute('http-equiv')).toBe('Content-Security-Policy');
    expect(p.doc.head.firstElementChild?.getAttribute('content')).toMatch(/default-src 'none'/);
    // The test DOM mis-parses SVG foreign content; Chromium on Actions opens this file and computes it (credit-snapshot.yml).
  });
  test('the committed Actions fixture equals the generator', () => {
    expect(readFileSync(resolve(process.cwd(), '../tests/fixtures/snapshot-hostile.htm'), 'utf8')).toBe(hostileFiling());
  });
});

describe('display and exports', () => {
  test('amounts are exact in their stated scale and parse back', () => {
    expect(amountText('98657000000', 6, 'USD')).toBe('98,657 百万美元');
    expect(amountText('-1234500', 3, 'USD')).toBe('−1,234.5 千美元');
    expect(parseAmountText('98,657 百万美元')).toBe('98657000000');
    expect(parseAmountText('−1,234.5 千美元')).toBe('-1234500');
  });
  const s = snap(syntheticFiling());
  const source: SourceInfo = { kind: 'gallery', fileName: 'lsyn-20251231.htm', sha256: 'ab'.repeat(32), bytes: 1234, accession: '0009999999-26-000001', documentUrl: 'https://www.sec.gov/Archives/edgar/data/9999999/000999999926000001/lsyn-20251231.htm' };
  const ctx = { snapshot: s, source, generatedAt: '2026-11-14T00:00:00Z', appVersion: 'test' };
  test('memo carries accession, concept, period and a link to the tagged cell', () => {
    const md = memoMarkdown(ctx);
    expect(md).toContain('0009999999-26-000001');
    expect(md).toContain('`us-gaap:DebtCurrent`');
    expect(md).toContain('2025-12-31');
    expect(md).toMatch(/不是评级，也不是投资建议/);
    const ol = s.inputs.operating_leases.terms[0];
    expect(md).toContain(`${source.documentUrl}#${ol.id}`);
  });
  test('CSV rows for every input term', () => {
    const csv = snapshotCsv(ctx);
    expect(csv.startsWith('﻿kind,key,label,value')).toBe(true);
    const terms = csv.split('\r\n').filter(l => l.startsWith('input_term'));
    expect(terms.length).toBe(Object.values(s.inputs).reduce((n, i) => n + i.terms.length, 0) + s.ladder!.next12.terms.length + s.ladder!.years2to5.terms.length + s.ladder!.after5.terms.length);
    expect(terms.every(l => l.includes('0009999999-26-000001'))).toBe(true);
  });
  test('docx is a valid stored zip with the document part', () => {
    const bytes = memoDocx(ctx);
    const view = new DataView(bytes.buffer);
    const end = bytes.length - 22;
    expect(view.getUint32(end, true)).toBe(0x06054b50);
    expect(view.getUint16(end + 10, true)).toBe(3);
    const text = new TextDecoder().decode(bytes);
    expect(text).toContain('word/document.xml');
    expect(text).toContain('Leon Synthetic Industries Inc.');
    expect(zipStore([['a.txt', 'x']]).length).toBe(30 + 5 + 1 + 46 + 5 + 22);
  });
});

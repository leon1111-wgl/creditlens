/* Findings of the Fable review at 5d221e8, written as failing tests before the fixes.
   P1: off-target answers (correctly labelled numbers that were not asked). P2: messages and port edge cases. */
import { describe, expect, it } from 'vitest';
import { compose } from '../src/compose';
import { formatF, parse, quantize } from '../src/decimal';
import { understand } from '../src/understand';
import { loadPack } from './helpers';

const pack = loadPack();
const read = (q: string) => {
  const u = understand(pack, q);
  return u.type === 'plans' ? u.plans.map(p => p.plan.items.map(i => `${i.issuer_id}:${i.metric}:${i.year}`).join('+')) : [`${u.type}:${u.reason}`];
};
const answered = (q: string) => understand(pack, q).type === 'plans';

describe('P1 off-target answers', () => {
  it.each(['Apple total cash FY2025', '苹果2025年现金合计', '苹果2025年现金总额'])('total cash is not the cash-and-equivalents line: %s', q => expect(answered(q)).toBe(false));
  it('a component of the combined MSFT line is not answered with the total', () => expect(answered('微软2025年现金及短期投资合计中现金是多少')).toBe(false));
  it.each(['微软2025年不动产、厂房和设备', '微软2025年厂房设备', 'Microsoft PP&E FY2025'])('a PP&E balance is not the capex flow: %s', q => expect(answered(q)).toBe(false));
  it('purchase wording still maps to capex', () => {
    expect(read('微软2025财年购买固定资产(PP&E)付了多少现金')).toEqual(['MSFT:capex:2025']);
    expect(read('苹果2025年买厂房设备花了多少钱')).toEqual(['AAPL:capex:2025']);
  });
  it.each(['微软2025年收入和2024年净利润', '微软2025年收入和苹果2025年净利润', '微软2025年收入比苹果2024年收入', '微软FY2025 vs 苹果FY2024 净利润'])
    ('per-clause periods or companies are not cross-multiplied: %s', q => expect(answered(q)).toBe(false));
  it('a plain multi-metric or multi-year lookup still answers', () => {
    expect(read('微软2025年的收入和净利润')).toEqual(['MSFT:revenue:2025', 'MSFT:net_income:2025']);
    expect(read('微软 FY2025 和 FY2024 的净利润分别是多少')).toEqual(['MSFT:net_income:2024', 'MSFT:net_income:2025']);
  });
  it.each(['微软2024到2025毛利率增长率', '微软2024到2025毛利率变化率', '微软2024到2025毛利率增长了百分之多少'])
    ('a relative growth rate of a percentage is not replaced by a point change: %s', q => expect(answered(q)).toBe(false));
  it('a percentage-point change of a margin still answers', () => expect(read('微软2024到2025毛利率变化了多少')).toEqual(['MSFT:gross_margin_pct:2024+MSFT:gross_margin_pct:2025']));
  it.each([['微软FY2025年报中的2024年收入', ['MSFT:revenue:2024']], ['苹果FY2025年报中FY2024净利润', ['AAPL:net_income:2024']], ['苹果2025年报的净利润', ['AAPL:net_income:2025']]])
    ('the report year names the document, not a second period: %s', (q, want) => expect(read(q)).toEqual(want));
});

describe('P2', () => {
  it('quantize keeps the sign of a negative value that rounds to zero, as Python does', () => {
    expect(formatF(quantize(parse('-0.004'), -2))).toBe('-0.00');
    expect(formatF(quantize(parse('-0.5'), 0))).toBe('-0');
  });
  it('capex is stated as the outflow the formula uses, with the printed bracket shown', () => {
    const card = compose(pack, '微软FY2024到FY2025资本开支变化了多少').cards[0];
    expect(card.headline).toContain('44,477 → 64,551');
    expect(card.formula).toContain('64,551 − 44,477');
  });
  it('day/month order is read or refused, never garbled', () => {
    const u = understand(pack, 'Apple cash as of 27/09/2025');
    expect(u.type === 'plans' ? String(u.plans[0].plan.items[0].year) : u.detail).not.toMatch(/2025-27/);
    expect(read('Apple cash as of 27/09/2025')).toEqual(['AAPL:cash_equivalents:2025']);
    expect(understand(pack, 'Apple cash as of 05/06/2025').type).toBe('refuse');
  });
  it('a month with a flow says monthly data is not registered', () => {
    const u = understand(pack, '微软2025年1月收入');
    expect(u.type).toBe('refuse');
    if (u.type !== 'plans') expect(u.detail).toContain('月度');
  });
  it.each(['微软2025年收入多少万亿美元', '微软2025年收入多少千万美元'])('an unsupported scale is named, not reported as two units: %s', q => {
    const u = understand(pack, q);
    expect(u.type).toBe('refuse');
    if (u.type !== 'plans') expect(u.reason).toBe('unit_not_supported');
  });
  it.each(['微软和苹果收入合计', '两家公司2025年收入总和', '苹果2025年现金及现金等价物和有价证券合计'])('a sum of several items is refused, not silently listed: %s', q => {
    const u = understand(pack, q);
    expect(u.type).not.toBe('plans');
  });
  it('percentage points asked on an amount are not answered as a percent change', () => expect(answered('微软2024到2025收入增长了多少个百分点')).toBe(false));
  it('both categories with one base metric answer both', () => expect(read('苹果2025年产品和服务收入分别是多少')).toEqual(['AAPL:product_revenue:2025', 'AAPL:service_revenue:2025']));
  it('Chinese numeral years 二〇二四 read', () => expect(read('微软二〇二四至二〇二五年营收变化')).toEqual(['MSFT:revenue:2024+MSFT:revenue:2025']));
  it.each([['How much cash does Apple have at fiscal 2025 year end', ['AAPL:cash_equivalents:2025']], ['Apple FY2025 cash balance', ['AAPL:cash_equivalents:2025']]])('%s', (q, want) => expect(read(q)).toEqual(want));
  it('Apple short-term debt headline names the line (commercial paper)', () => {
    expect(compose(pack, '苹果FY2025短期债务').cards[0].headline).toContain('Commercial paper');
  });
});

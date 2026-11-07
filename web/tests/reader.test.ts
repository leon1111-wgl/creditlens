/* Failure classes found by the blind first run at a05f450, as classes with look-alike negatives.
   These were fixed after the run, so the blind set no longer measures them (see docs/44-browser-answers.md). */
import { describe, expect, it } from 'vitest';
import { answerPlan } from '../src/answer';
import { understand } from '../src/understand';
import { loadPack } from './helpers';

const pack = loadPack();
const items = (q: string) => {
  const u = understand(pack, q);
  return u.type === 'plans' ? u.plans.flatMap(p => p.plan.items.map(i => `${i.issuer_id}:${i.metric}:${i.year}`)) : [`${u.type}:${u.reason}`];
};

describe('a word that changes the metric is never dropped', () => {
  it.each([
    ['苹果2025年硬件产品那块一共卖了多少钱', ['AAPL:product_revenue:2025']],
    ['苹果2024年服务业务卖了多少钱呀', ['AAPL:service_revenue:2024']],
    ['微软服务及其他2024财年毛利率是多少', ['MSFT:service_gross_margin_pct:2024']],
    ['苹果2025年产品的成本', ['AAPL:product_cost:2025']],
    ['苹果2025年产品和服务的收入', ['AAPL:product_revenue:2025', 'AAPL:service_revenue:2025']],
    ['苹果2025年产品净利润', ['clarify:ambiguous_metric']],
    ['苹果2025年产品毛利', ['refuse:metric_not_registered']],
    ['苹果2025年硬件', ['clarify:ambiguous_metric']],
  ])('%s', (q, want) => expect(items(q)).toEqual(want));
});

describe('verb phrases yield to a named metric', () => {
  it.each([
    ['苹果2024年毛利润赚了多少', ['AAPL:gross_profit:2024']],
    ['苹果2024年赚了多少', ['AAPL:net_income:2024']],
    ['微软FY2025卖了多少钱', ['MSFT:revenue:2025']],
    ['微软FY2025经营现金流赚了多少', ['MSFT:cfo:2025']],
    ['苹果2024年收入和净利润', ['AAPL:revenue:2024', 'AAPL:net_income:2024']],
  ])('%s', (q, want) => expect(items(q)).toEqual(want));
});

describe('requested scale is read even after a filler', () => {
  it.each([
    ['微软2025财年净利润是多少美元,写完整数字', 'one'],
    ['苹果2025年营收为多少亿美元', 'hundred_million'],
    ['苹果2025年营收是几亿', 'hundred_million'],
    ['苹果2025年营收折合多少万美元', 'ten_thousand'],
  ])('%s', (q, scale) => {
    const u = understand(pack, q);
    expect(u.type).toBe('plans');
    if (u.type === 'plans') expect(u.plans[0].plan.unit?.scale).toBe(scale);
  });
  it('a CNY amount after the same filler is still refused', () => {
    const u = understand(pack, '苹果2025年营收是多少亿元');
    expect(u.type).toBe('plans');
    if (u.type === 'plans') expect(answerPlan(pack, u.plans[0].plan).status).toBe('unit_unavailable');
  });
});

describe('typo repair is unique, reported and never invents a metric', () => {
  it('repairs a one-character typo to the only readable name and says so', () => {
    const u = understand(pack, '微软2025年营业收人');
    expect(u.type).toBe('plans');
    if (u.type === 'plans') {
      expect(u.plans[0].plan.items[0].metric).toBe('revenue');
      expect(u.reading.notes.join()).toContain('疑似错字');
    }
  });
  it('repairs two Latin typos', () => expect(items('microsfot revenu fy2025')).toEqual(['MSFT:revenue:2025']));
  it('keeps the meaning of the readable part: 净利 + typo repairs to 净利润, never to 净利率', () => {
    expect(items('微软2025财年净利闰是多少')).toEqual(['MSFT:net_income:2025']);
  });
  it('never rewrites a readable word into another metric (现金多 is not 现金流)', () => {
    expect(understand(pack, '微软和苹果谁的现金多').type).not.toBe('plans');
    expect(understand(pack, '苹果的现金多不多 2025年').type).not.toBe('plans');
  });
  it.each([
    ['微软2025年营业利闰'],       // nearest name is unregistered
    ['微软2025年总利闰'],
    ['微软2025年收入是不是2817亿'],
  ])('refuses %s', q => expect(understand(pack, q).type).not.toBe('plans'));
  it('a typo of an unregistered metric is refused, not mapped elsewhere', () => {
    const u = understand(pack, '苹果2025年营业利闰');
    expect(u.type).not.toBe('plans');
  });
});

describe('new wordings from the blind run (now seen)', () => {
  it.each([
    ['What was Apple’s net income for fiscal 2025?', ['AAPL:net_income:2025']],
    ['Show Microsoft and Apple FY2025 operating cash flow side by side. Are they comparable?', ['MSFT:cfo:2025', 'AAPL:cfo:2025']],
    ['苹果2025财年末流动的定期债务(Term debt, current)是多少', ['AAPL:current_debt:2025']],
    ['苹果2024财年末非流动的定期债务余额', ['AAPL:long_debt:2024']],
    ['微软两年的短期投资加现金合计是同一口径吗', ['MSFT:liquid_assets:2024', 'MSFT:liquid_assets:2025']],
    ['微软2025财年购买固定资产(PP&E)付了多少现金', ['MSFT:capex:2025']],
  ])('%s', (q, want) => expect(items(q)).toEqual(want));
});

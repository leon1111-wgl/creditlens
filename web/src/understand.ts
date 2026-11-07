/* Closed reading of a visitor's question into explicit answer plans.

   Every character run must be accounted for by the vocabulary below: an issuer, a
   registered metric, a fiscal period, a unit, a scope word, an intent word or a
   listed filler. Anything else is named back and the question is refused, so a
   word the engine does not understand can never be silently dropped. Words that
   change the meaning (other companies, segments, parent-only, quarters, calendar
   years, other metrics, other currencies, ranking) are recognised explicitly and
   refused with the reason. The program, not a model, states every number. */
import type { Pack, Plan, Unit } from './types';

export type Intent = 'lookup' | 'change' | 'comparable' | 'rank' | 'diff';
export interface Reading {
  issuers: string[]; metrics: string[]; years: number[]; unit: Unit | null; scope: 'consolidated' | 'parent_only';
  intent: Intent; notes: string[];
}
export interface PlannedItem { plan: Plan; ranking_refused?: boolean; label: string }
export type Understood =
  | { type: 'plans'; reading: Reading; plans: PlannedItem[] }
  | { type: 'refuse' | 'clarify'; reason: string; detail: string; unknown?: string[]; suggestions: string[]; reading?: Partial<Reading> };

type Token =
  | { t: 'issuer'; v: string } | { t: 'both' } | { t: 'other_issuer'; text: string }
  | { t: 'metric'; v: string; note?: string; soft?: boolean } | { t: 'ambiguous'; family: string; text: string }
  | { t: 'unsupported_metric'; text: string; why?: string }
  | { t: 'year'; v: number; end?: 'calendar' | 'fiscal' } | { t: 'years'; v: number[] }
  | { t: 'date'; y: number; m: number; d: number | null }
  | { t: 'period_unsupported'; text: string; why: string }
  | { t: 'relative'; v: 'prev' | 'latest' | 'unanchored'; text: string }
  | { t: 'unit'; currency: string | null; scale: string | null; text: string }
  | { t: 'percent' } | { t: 'scope'; v: string; text: string }
  | { t: 'intent'; v: Intent | 'multi' | 'compare' | 'growth' }
  | { t: 'filler' } | { t: 'ended' } | { t: 'sum'; text: string } | { t: 'pp' } | { t: 'document'; v: number };

/* ---------- normalisation ---------- */
const TRAD: Record<string, string> = {'蘋': '苹', '軟': '软', '營': '营', '淨': '净', '潤': '润', '現': '现', '負': '负', '債': '债', '資': '资', '產': '产', '長': '长', '經': '经', '銷': '销', '開': '开', '億': '亿', '萬': '万', '財': '财', '變': '变', '較': '较', '對': '对', '務': '务', '動': '动', '額': '额', '業': '业', '與': '与', '還': '还', '這': '这', '個': '个', '幾': '几', '麼': '么', '嗎': '吗', '報': '报', '費': '费', '稅': '税', '價': '价', '總': '总', '計': '计', '貨': '货', '幣': '币', '點': '点', '減': '减', '兩': '两', '間': '间', '時': '时', '據': '据', '於': '于', '為': '为', '們': '们', '說': '说', '請': '请', '問': '问', '詢': '询', '給': '给', '訴': '诉', '發': '发', '賺': '赚', '錢': '钱', '賣': '卖', '買': '买', '設': '设', '備': '备', '購': '购', '權': '权', '虧': '亏', '損': '损', '專': '专', '團': '团', '體': '体', '單': '单', '區': '区', '場': '场', '華': '华', '國': '国', '際': '际', '東': '东', '歐': '欧', '鎊': '镑', '韓': '韩', '漲': '涨', '齊': '齐', '別': '别', '種': '种', '類': '类', '裡': '里', '裏': '里', '樣': '样', '簡': '简', '獲': '获', '純': '纯', '證': '证', '劵': '券', '準': '准', '啟': '启', '實': '实', '層': '层', '項': '项', '結': '结', '餘': '余', '彙': '汇', '匯': '汇', '臺': '台', '灣': '湾', '戶': '户', '帳': '账', '賬': '账', '圓': '圆', '訂': '订', '內': '内', '兌': '兑', '換': '换', '標': '标', '舊': '旧', '預': '预', '測': '测', '來': '来', '將': '将', '鬥': '斗', '達': '达', '隻': '只', '歷': '历', '屆': '届'};

export function normalise(text: string): string {
  let s = text.normalize('NFKC').toLowerCase();
  s = Array.from(s, ch => TRAD[ch] ?? ch).join('');
  s = s.replace(/([a-z])['’]s(?![a-z])/g, '$1');
  return s.replace(/[‐-―−]/g, '-').replace(/[“”"「」『』‘’`]/g, '').replace(/\s+/g, ' ').trim();
}

/* ---------- vocabulary ---------- */
const CN_YEAR: Record<string, number> = { '二〇二三': 2023, '二零二三': 2023, '二〇二四': 2024, '二零二四': 2024, '二〇二五': 2025, '二零二五': 2025, '二〇二六': 2026, '二零二六': 2026, '二〇二二': 2022, '二零二二': 2022 };
const EN_MONTH: Record<string, number> = { january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12, jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12 };
const SCALE: Record<string, string> = { '百万': 'million', '亿': 'hundred_million', '万': 'ten_thousand', '千': 'thousand', '十亿': 'billion', million: 'million', millions: 'million', mn: 'million', billion: 'billion', billions: 'billion', bn: 'billion', thousand: 'thousand', thousands: 'thousand' };
const year2 = (v: string) => (v.length === 2 ? 2000 + Number(v) : CN_YEAR[v] ?? Number(v));
const W = (body: string) => `(?<![a-z0-9])(?:${body})(?![a-z0-9])`; // English word boundaries

type Rule = [RegExp, (m: RegExpExecArray) => Token | null];
const rules: Rule[] = [];
const rule = (source: string, make: (m: RegExpExecArray) => Token | null) => rules.push([new RegExp(source, 'y'), make]);
export const LITERALS: string[] = [];
const record = (list: string[], make: (m: RegExpExecArray) => Token | null) => {
  for (const raw of list) {
    const plural = /^([a-z][a-z -]*)s\?$/.exec(raw);
    for (const text of plural ? [plural[1], plural[1] + 's'] : [raw]) record1(text, make);
  }
};
const record1 = (text: string, make: (m: RegExpExecArray) => Token | null) => {
  {
    if (/[\\()?|[\]*+]/.test(text)) return;
    const token = make([text] as unknown as RegExpExecArray);
    if (token && ['metric', 'issuer', 'unsupported_metric', 'other_issuer'].includes(token.t)) LITERALS.push(text);
  }
};
const words = (list: string[], make: (m: RegExpExecArray) => Token | null) => { record(list, make); rule(`(?:${list.join('|')})`, make); };
const english = (list: string[], make: (m: RegExpExecArray) => Token | null) => { record(list, make); rule(W(list.join('|')), make); };
const metric = (v: string, note?: string) => () => ({ t: 'metric', v, note } as Token);

// Issuers.
words(['微软公司', '微软集团', '微软', '巨硬'], () => ({ t: 'issuer', v: 'MSFT' }));
english(['microsoft corporation', 'microsoft corp', 'microsoft', 'msft'], () => ({ t: 'issuer', v: 'MSFT' }));
words(['苹果公司', '苹果集团', '苹果'], () => ({ t: 'issuer', v: 'AAPL' }));
english(['apple inc\\.?', 'apple', 'aapl'], () => ({ t: 'issuer', v: 'AAPL' }));
words(['这两家公司', '两家公司', '这两家', '两家', '两个公司', '两者', '二者', '双方'], () => ({ t: 'both' }));
english(['both companies', 'both'], () => ({ t: 'both' }));
words(['谷歌', '字节', '亚马逊', '英伟达', '特斯拉', '脸书', '腾讯', '阿里巴巴', '阿里', '百度', '华为', '小米', '三星', '台积电', '甲骨文', '英特尔', '高通', '思科', '奈飞', '超微', '茅台', '比亚迪', '索尼', '联想', '戴尔', '惠普', '京东', '美团', '拼多多', '网易'],
  m => ({ t: 'other_issuer', text: m[0] }));
english(['google', 'alphabet', 'amazon', 'nvidia', 'nvda', 'amd', 'tesla', 'meta', 'facebook', 'tencent', 'alibaba', 'ibm', 'oracle', 'intel', 'qualcomm', 'cisco', 'netflix', 'samsung', 'tsmc', 'openai', 'goog', 'amzn', 'tsla'],
  m => ({ t: 'other_issuer', text: m[0] }));

// Registered metrics (longest alias wins).
const CAT_P = '(?:产品|硬件产品|硬件|products?)';
const CAT_S = '(?:服务及其他|服务和其他|服务与其他|服务|service and other|services?)';
const JOIN = '(?:的|类|方面|部分|业务|分类| )?';
const REV = '(?:营业收入|销售收入|收入|营收|销售额|revenues?|net sales|sales)';
const COST = '(?:营业成本|销售成本|收入成本|成本|cost of (?:revenue|sales))';
rule(`${CAT_P}${JOIN}${REV}`, metric('product_revenue'));
rule(`${CAT_S}${JOIN}${REV}`, metric('service_revenue'));
rule(`${CAT_P}${JOIN}${COST}`, metric('product_cost'));
rule(`${CAT_S}${JOIN}${COST}`, metric('service_cost'));
rule(`${CAT_P}${JOIN}(?:毛利率|gross margin (?:percentage|rate|%))`, metric('product_gross_margin_pct'));
rule(`${CAT_S}${JOIN}(?:毛利率|gross margin (?:percentage|rate|%))`, metric('service_gross_margin_pct'));
rule(`${CAT_P}${JOIN}(?:毛利润|毛利额|毛利)|${CAT_S}${JOIN}(?:毛利润|毛利额|毛利)`, m => ({ t: 'unsupported_metric', text: m[0], why: '分类毛利金额是由收入减成本推导的，未作为披露金额登记；可以问产品或服务毛利率。' }));
words(['营业总收入', '营业收入', '总营收', '营收', '总收入', '收入总额', '收入', '销售收入', '净销售额', '销售总额', '总销售额', '销售额', '营业额'], metric('revenue'));
words(['一共卖了多少钱', '卖了多少钱', '卖了多少', '卖出多少钱', '卖了', '卖出了', '销量'], () => ({ t: 'metric', v: 'revenue', soft: true } as Token));
english(['total net sales', 'net sales', 'total revenues?', 'revenues?', 'sales', 'top line', 'turnover'], metric('revenue'));
words(['营业成本', '营收成本', '销售成本', '收入成本', '主营业务成本', '营业总成本', '总营业成本', '总销售成本', '营业成本合计', '销售成本合计'], metric('cost_of_revenue'));
english(['total cost of revenue', 'total cost of sales', 'cost of revenue', 'cost of sales', 'cogs'], metric('cost_of_revenue'));
words(['成本'], metric('cost_of_revenue', '“成本”按营业成本（cost of revenue / cost of sales）理解，不含经营费用。'));
words(['销售毛利率', '综合毛利率', '整体毛利率', '毛利润率', '毛利率'], metric('gross_margin_pct'));
english(['gross margin percentage', 'gross margin rate', 'gross margin %', 'gross margin ratio', 'gross profit margin'], metric('gross_margin_pct'));
words(['毛利润', '毛利额', '毛利金额', '毛利'], metric('gross_profit'));
english(['gross profit', 'gross margin'], metric('gross_profit', '按报表 Gross margin 行（金额）理解；毛利率请问“毛利率”。'));
words(['净利润', '净利', '纯利润', '纯利', '净收益', '税后利润'], metric('net_income'));
words(['净赚', '赚了多少钱', '赚了多少', '赚多少钱', '赚多少', '赚了', '赚的钱', '赚钱', '挣了多少钱', '挣了多少', '挣了', '盈利了多少', '盈利'], () => ({ t: 'metric', v: 'net_income', soft: true } as Token));
english(['net income', 'net profit', 'net earnings', 'earnings', 'bottom line'], metric('net_income'));
words(['经营活动产生的现金流量净额', '经营活动现金流量净额', '经营活动产生的现金流量', '经营活动现金净流量', '经营活动净现金流', '经营活动现金流净额', '经营活动的现金流', '经营活动现金流', '经营活动现金流量', '经营现金流量', '经营现金流', '经营性现金流', '经营净现金流', '营运现金流'], metric('cfo'));
english(['net cash from operations', 'cash generated by operating activities', 'cash from operations', 'operating cash flows?', 'cash flow from operations', 'cfo', 'ocf'], metric('cfo'));
words(['现金流'], metric('cfo', '“现金流”按经营活动现金流净额理解；投资、筹资活动现金流未登记。'));
words(['资本开支', '资本支出', '资本性支出', '购建固定资产支付的现金', '购建固定资产的现金', '购建固定资产', '固定资产购建支出', '固定资产购建现金流出', '固定资产购建', '固定资产投资', '固定资产支出', '购置固定资产', '购买固定资产', '买固定资产', '花在固定资产上', '买设备', '买厂房设备', '购买厂房设备', '购置厂房设备', '购买物业厂房及设备', '固定资产付款', '固定资产现金支出'], metric('capex'));
english(['capex', 'capital expenditures?', 'pp ?& ?e payments?', 'ppe payments?', 'purchases of property(?:, plant)? and equipment', 'additions to property and equipment', 'payments for acquisition of property, plant and equipment', 'ppe'], metric('capex'));
words(['短期债务', '短期借款', '短债', '商业票据', '短期票据'], metric('short_debt'));
english(['commercial paper', 'short-term debt', 'short term debt'], metric('short_debt'));
words(['流动的定期债务', '流动部分的定期债务', '定期债务的流动部分', '定期债务(流动)', '定期债务(流动部分)', '一年内到期的长期债务', '一年内到期长期债务', '一年内到期的长期借款', '长期债务的流动部分', '长期债务流动部分', '一年内到期的定期债务', '一年内到期的债务', '当期到期的长期债务', '流动部分的长期债务'], metric('current_debt'));
english(['current portion of long-term debt', 'current portion of term debt', 'current term debt', 'term debt, current', 'term debt \\(current\\)'], metric('current_debt'));
words(['非流动长期债务', '长期债务', '长期借款', '长债', '非流动债务', '非流动的定期债务', '非流动定期债务', '非流动部分的定期债务', '定期债务的非流动部分', '定期债务(非流动)', '定期债务(非流动部分)'], metric('long_debt'));
rule('长期债务 ?\\(非流动(?:部分)?\\)|长期债务的非流动部分|长期债务非流动部分', metric('long_debt'));
english(['long-term debt', 'long term debt', 'non-current term debt', 'term debt, non-current', 'term debt \\(non-current\\)'], metric('long_debt'));
words(['流动资产合计', '流动资产总额', '流动资产'], metric('current_assets'));
english(['total current assets', 'current assets'], metric('current_assets'));
words(['流动负债合计', '流动负债总额', '流动负债'], metric('current_liabilities'));
english(['total current liabilities', 'current liabilities'], metric('current_liabilities'));
rule('短期投资(?:加|和|与|及|、)现金(?:及现金等价物|和现金等价物)?(?:合计|总额|总计)?|现金(?:、| ?和 ?| ?及 ?| ?与 ?)?现金等价物(?:、| ?和 ?| ?及 ?| ?与 ?|加)短期投资(?:合计|总额|总计)?|现金(?:、| ?和 ?| ?及 ?| ?与 ?|加)短期投资(?:合计|总额|总计)?', metric('liquid_assets'));
english(['total cash, cash equivalents,? and short-term investments', 'cash, cash equivalents,? and short-term investments', 'cash and short-term investments'], metric('liquid_assets'));
words(['现金及现金等价物', '现金和现金等价物', '现金与现金等价物', '现金、现金等价物', '现金等价物'], metric('cash_equivalents'));
english(['cash and cash equivalents'], metric('cash_equivalents'));
words(['流动有价证券', '流动性有价证券', '流动的有价证券', '短期有价证券', '有价证券', '短期投资'], metric('short_investments'));
english(['current marketable securities', 'marketable securities, current', 'marketable securities \\(current\\)', 'marketable securities', 'short-term investments'], metric('short_investments'));

// Ambiguous words: answered only when a specific metric of the same family is also present.
words(['利润', '利润额', '盈利能力'], m => ({ t: 'ambiguous', family: 'profit', text: m[0] }));
english(['profit', 'profits'], m => ({ t: 'ambiguous', family: 'profit', text: m[0] }));
words(['现金', '账上的钱', '手上的钱', '钱'], m => ({ t: 'ambiguous', family: 'cash', text: m[0] }));
english(['cash'], m => ({ t: 'ambiguous', family: 'cash', text: m[0] }));
words(['债务', '负债', '借款', '借了多少钱', '借了多少', '借了', '欠了多少钱', '欠了多少', '欠了', '欠债', '短期负债', '长期负债', '定期债务'], m => ({ t: 'ambiguous', family: 'debt', text: m[0] }));
english(['debt', 'borrowings', 'term debt'], m => ({ t: 'ambiguous', family: 'debt', text: m[0] }));
words(['净收入'], m => ({ t: 'ambiguous', family: 'income', text: m[0] }));
words(['不动产、厂房和设备', '物业、厂房及设备', '不动产厂房设备', '厂房和设备', '厂房设备', '固定资产净额', '固定资产'], m => ({ t: 'ambiguous', family: 'ppe', text: m[0] }));
english(['pp&e', 'ppe', 'property, plant and equipment', 'property and equipment'], m => ({ t: 'ambiguous', family: 'ppe', text: m[0] }));
words(['投资'], m => ({ t: 'ambiguous', family: 'investment', text: m[0] }));
words(['硬件产品', '产品', '硬件', '服务及其他', '服务和其他', '服务与其他', '服务'], m => ({ t: 'ambiguous', family: 'category', text: m[0] }));

// Metrics that are not registered (named back).
words(['营业利润', '经营利润', '主营利润', '利润总额', '税前利润', '息税前利润', '归母净利润', '归属于母公司的净利润', '扣非净利润', '扣非', '营业费用', '经营费用', '运营费用', '研发费用', '研发支出', '研发投入', '研发', '销售费用', '管理费用', '销售及管理费用', '所得税', '税费', '利息支出', '利息', '折旧', '摊销', '每股收益', '每股', '股息', '分红', '回购', '市值', '股价', '总资产', '资产总额', '资产合计', '资产', '总负债', '负债合计', '负债总额', '股东权益', '所有者权益', '净资产', '权益', '自由现金流', '净债务', '净负债', '有息负债', '总债务', '债务总额', '全部债务', '流动比率', '速动比率', '资产负债率', '负债率', '净利率', '净利润率', '营业利润率', '经营利润率', '投资回报率', '净资产收益率', '杠杆', '投资活动现金流', '投资活动', '筹资活动现金流', '筹资活动', '融资活动', '现金净增加', '存货', '库存', '应收账款', '应收', '应付账款', '应付', '商誉', '无形资产', '员工', '人数', '全部资本投入', '资本投入总额', '总资本投入', '全部资本开支', '所有资本投资', '资本投入', '资本投资', '订单', '积压', '剩余履约义务', '收入确认', '营运资本', '营运资金', '运营资本'],
  m => ({ t: 'unsupported_metric', text: m[0] }));
english(['operating income', 'operating profit', 'ebitda', 'ebit', 'eps', 'earnings per share', 'r&d', 'research and development', 'opex', 'operating expenses', 'total assets', 'total liabilities', 'equity', "shareholders'? equity", 'free cash flow', 'fcf', 'net debt', 'total debt', 'current ratio', 'quick ratio', 'roe', 'roa', 'dividends?', 'buybacks?', 'market cap', 'share price', 'working capital', 'net margin', 'operating margin', 'inventory', 'goodwill', 'income tax'],
  m => ({ t: 'unsupported_metric', text: m[0] }));

// Periods.
const Y = '(20\\d{2}|\\d{2}|二[〇零]二[二三四五六])';
rule(`${Y} ?(?:-|~|—|至|到|和|与|及|跟|、|,|/) ?${Y} ?(?:财年|会计年度|年度|年)`, m => ({ t: 'years', v: [year2(m[1]), year2(m[2])] }));
rule(`(?:fy|财年|会计年度) ?${Y} ?(?:-|~|—|至|到|和|与|及|跟|、|,|/) ?(?:fy|财年)? ?${Y}`, m => ({ t: 'years', v: [year2(m[1]), year2(m[2])] }));
rule(`(?<![0-9])(20\\d{2})(?: ?年)? ?(?:-|~|—|至|到|和|与|及|跟|、|,|/|and|vs\\.?) ?(20\\d{2})(?![0-9])`, m => ({ t: 'years', v: [Number(m[1]), Number(m[2])] }));
rule(`(?:fy|财年|会计年度|fiscal year|fiscal) ?${Y}(?![0-9]) ?(?:财年|年度|年)?( ?(?:末|底|终|结束时?|year-end|year end))?`, m => ({ t: 'year', v: year2(m[1]), end: m[2] ? 'fiscal' : undefined }));
rule(`(?<![0-9])${Y} ?(?:财年|会计年度|fy)( ?(?:末|底|终|结束时?))?`, m => ({ t: 'year', v: year2(m[1]), end: m[2] ? 'fiscal' : undefined }));
// "FY2025 年报" names the document; its year is a period only when no other year is asked.
rule(`(?:fy|财年|fiscal year|fiscal)? ?${Y} ?(?:财年|年度|年)? ?(?:年度报告|年报|10-?k|form 10-?k|annual report|报告)(?:里面|里|中|内|上)?`, m => ({ t: 'document', v: year2(m[1]) }));
rule(`(?<![0-9])${Y} ?(?:年度|年)(末|底|终|年末|年底)?`, m => ({ t: 'year', v: year2(m[1]), end: m[2] ? 'calendar' : undefined }));
rule(`(?<![0-9])(20\\d{2})(?![0-9])`, m => ({ t: 'year', v: Number(m[1]) }));
rule(`(?<![0-9])(20\\d{2}) ?年 ?(\\d{1,2}) ?月 ?(\\d{1,2}) ?(?:日|号)`, m => ({ t: 'date', y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) }));
rule(`(?<![0-9])(20\\d{2})[-/.](\\d{1,2})[-/.](\\d{1,2})(?![0-9])`, m => ({ t: 'date', y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) }));
rule(`(?<![0-9])(\\d{1,2})/(\\d{1,2})/(20\\d{2})(?![0-9])`, m => {
  const [a, b] = [Number(m[1]), Number(m[2])];
  if (a > 12 && b <= 12) return { t: 'date', y: Number(m[3]), m: b, d: a };          // day/month/year
  if (a <= 12 && b > 12) return { t: 'date', y: Number(m[3]), m: a, d: b };          // month/day/year
  if (a === b && a <= 12) return { t: 'date', y: Number(m[3]), m: a, d: b };
  return { t: 'period_unsupported', text: m[0], why: 'ambiguous_date' };
});
rule(`(?<![a-z])(${Object.keys(EN_MONTH).join('|')}) (\\d{1,2}),? (20\\d{2})(?![0-9])`, m => ({ t: 'date', y: Number(m[3]), m: EN_MONTH[m[1]], d: Number(m[2]) }));
rule(`(?<![0-9])(20\\d{2}) ?年 ?(\\d{1,2}) ?月(?:份)?(末|底)?`, m => ({ t: 'date', y: Number(m[1]), m: Number(m[2]), d: null }));
words(['自然年度', '自然年', '日历年度', '日历年', '公历年', '1-12月', '1月至12月', '1月到12月', '一月到十二月', '全年1-12月', '12月31日', '十二月三十一日'], m => ({ t: 'period_unsupported', text: m[0], why: 'calendar' }));
english(['calendar year', 'calendar'], m => ({ t: 'period_unsupported', text: m[0], why: 'calendar' }));
words(['年底', '年末', '年终', '岁末'], m => ({ t: 'period_unsupported', text: m[0], why: 'year_end' }));
rule('(?:第 ?[一二三四1-4] ?(?:财季|财务季度|季度|季)|财季|[一二三四1-4] ?季度|q ?[1-4](?![0-9])|单季度?|季度|季报|前三季度|上半年|下半年|半年报|半年度|半年|h[12](?![0-9])|月度|月份|单月|本月|上月|每月|ttm|过去 ?12 ?个月|近 ?12 ?个月|最近 ?12 ?个月|滚动 ?12 ?个月|环比)', m => ({ t: 'period_unsupported', text: m[0], why: 'sub_annual' }));
english(['quarter', 'quarterly', 'trailing twelve months', 'last twelve months', 'ltm', 'half-year', 'year to date', 'ytd'], m => ({ t: 'period_unsupported', text: m[0], why: 'sub_annual' }));
words(['与上一年相比', '和上一年相比', '跟上一年相比', '与上年相比', '和上年相比', '跟上年相比', '与去年相比', '和去年相比', '跟去年相比', '相比上一年', '相比上年', '相比去年', '相较上年', '相较去年', '较上一年', '较上年', '较去年', '比上一年', '比上年', '比去年', '对比上年', '对比去年', '上年同期', '去年同期'], m => ({ t: 'relative', v: 'prev', text: m[0] }));
words(['上一年度', '上一财年', '上一年', '上年度', '上年', '前一年', '去年'], m => ({ t: 'relative', v: 'unanchored', text: m[0] }));
words(['最新一个财年', '最近一个财年', '最近的财年', '最新财年', '最近财年', '最新一年', '最新年度', '最新年报', '最新的', '最新', '最近一期年报'], m => ({ t: 'relative', v: 'latest', text: m[0] }));
words(['今年', '本年度', '本年', '明年', '前年', '当年', '近一年', '最近一年', '近几年', '这几年', '过去一年', '历年', '近年来', '近年', '未来', '预计', '预测', '预期', '将来', '展望', '指引'], m => ({ t: 'relative', v: 'unanchored', text: m[0] }));
english(['this year', 'last year', 'next year', 'forecast', 'guidance', 'latest'], m => ({ t: 'relative', v: m[0] === 'latest' ? 'latest' : 'unanchored', text: m[0] }));

// Units.
rule('(?:是|为|有|合|折合)?(?:多少|几) ?(百万|亿|万|千|十亿)? ?(?:美元|美金|美刀|刀)', m => ({ t: 'unit', currency: 'USD', scale: m[1] ? SCALE[m[1]] : 'one', text: m[0] }));
rule('(百万|亿|万|千|十亿)? ?(?:美元|美金|美刀)', m => ({ t: 'unit', currency: 'USD', scale: m[1] ? SCALE[m[1]] : null, text: m[0] }));
rule(W('(?:in )?(?:us ?\\$|usd|\\$|dollars?) ?(millions?|billions?|thousands?|mn|bn)?') + '|' + W('(?:in )?(millions?|billions?|thousands?|mn|bn)(?: of)?(?: (?:us )?(?:dollars|usd))?'),
  m => ({ t: 'unit', currency: 'USD', scale: (m[1] || m[2]) ? SCALE[m[1] || m[2]] : null, text: m[0] }));
rule('\\$', () => ({ t: 'unit', currency: 'USD', scale: null, text: '$' }));
rule('(百万|亿|万|千|十亿|万亿|千万)? ?(?:人民币|元|块钱|块|rmb|cny|¥|￥)', m => ({ t: 'unit', currency: 'CNY', scale: m[1] && SCALE[m[1]] ? SCALE[m[1]] : null, text: m[0] }));
rule('(百万|亿|万|千|十亿)? ?(?:港元|港币|欧元|日元|英镑|韩元|新台币|台币|卢布)', m => ({ t: 'unit', currency: m[0].includes('港') ? 'HKD' : m[0].includes('欧') ? 'EUR' : m[0].includes('日') ? 'JPY' : m[0].includes('英') ? 'GBP' : 'OTHER', scale: m[1] ? SCALE[m[1]] : null, text: m[0] }));
english(['hkd', 'eur', 'euros?', 'jpy', 'yen', 'gbp', 'pounds?', 'rmb', 'cny', 'yuan'], m => ({ t: 'unit', currency: ['rmb', 'cny', 'yuan'].includes(m[0]) ? 'CNY' : m[0].toUpperCase().startsWith('H') ? 'HKD' : 'OTHER', scale: null, text: m[0] }));
rule('(?:是|为|有|合|折合)?(?:多少|几)? ?(?:万亿|千万|百亿|千亿|trillion) ?(?:美元|美金|元|人民币)?', m => ({ t: 'unit', currency: null, scale: 'unsupported', text: m[0] }));
rule('(?:是|为|有|合|折合)?(?:多少|几) ?(百万|亿|万|千|十亿)', m => ({ t: 'unit', currency: null, scale: SCALE[m[1]], text: m[0] }));
rule('(百万|十亿|亿)', m => ({ t: 'unit', currency: null, scale: SCALE[m[1]], text: m[0] }));
rule('(?:%|百分比|百分之几|百分之多少)', () => ({ t: 'percent' }));
rule('(?:多少个百分点|几个百分点|个百分点|百分点|pp(?![a-z]))', () => ({ t: 'pp' } as Token));

// Scope.
words(['合并报表', '合并口径', '合并数', '合并', '集团口径', '集团整体', '公司整体', '整体', '全公司'], () => ({ t: 'scope', v: 'consolidated', text: '合并' }));
english(['consolidated'], () => ({ t: 'scope', v: 'consolidated', text: 'consolidated' }));
words(['母公司报表', '母公司', '单体报表', '单体', '本部', '母公司口径'], m => ({ t: 'scope', v: 'parent_only', text: m[0] }));
english(['parent company', 'parent-only', 'parent only', 'standalone'], m => ({ t: 'scope', v: 'parent_only', text: m[0] }));
words(['持续经营', '终止经营', '非持续经营'], m => ({ t: 'scope', v: 'continuing', text: m[0] }));
english(['continuing operations', 'discontinued operations'], m => ({ t: 'scope', v: 'continuing', text: m[0] }));
words(['子公司', '分公司', '中国区', '大中华区', '中国市场', '美国市场', '中国', '美洲', '欧洲', '日本', '亚太', '地区', '区域', '国内', '海外', '国际业务', '分部', '业务分部', '板块', '事业部', '部门', '业务线', '智能云', '云业务', '云计算', '云服务', '云', '生产力', '个人计算', '游戏', '领英', '必应', '苹果手机', '手机', '电脑', '平板', '可穿戴', '家居', '配件', '应用商店', '广告'],
  m => ({ t: 'scope', v: 'segment', text: m[0] }));
english(['azure', 'intelligent cloud', 'productivity and business processes', 'more personal computing', 'windows', 'office', 'xbox', 'linkedin', 'bing', 'github', 'iphone', 'ipad', 'mac', 'wearables', 'app store', 'segments?', 'greater china', 'americas', 'europe', 'japan', 'rest of asia pacific', 'china', 'subsidiar(?:y|ies)', 'cloud'],
  m => ({ t: 'scope', v: 'segment', text: m[0] }));

// Intent.
words(['同比增长率', '同比增速', '同比增幅', '同比变化率', '同比变动率', '同比降幅', '增长率', '增速', '增幅', '降幅', '变动率', '变化率', '涨幅', '跌幅', '增长百分比', '变化幅度', '变动幅度'], () => ({ t: 'intent', v: 'growth' }));
english(['growth rate', 'growth', 'yoy', 'year over year', 'year-over-year', 'percent change'], () => ({ t: 'intent', v: 'growth' }));
words(['同比增长', '同比增加', '同比下降', '同比减少', '同比变化', '同比', '变化', '变动', '增长了', '增长', '增加了', '增加', '减少了', '减少', '下降了', '下降', '下滑', '上升', '提升', '提高', '降低', '增减', '涨了', '跌了', '涨', '跌', '多了', '少了', '变了', '变成', '走势', '趋势', '怎么变', '升了', '降了', '升', '降', '翻了'], () => ({ t: 'intent', v: 'change' }));
english(['change', 'changed', 'increase', 'increased', 'decrease', 'decreased', 'grow', 'grew', 'rise', 'rose', 'fall', 'fell', 'decline', 'declined', 'trend'], () => ({ t: 'intent', v: 'change' }));
words(['可比不可比', '可不可比', '能直接对比', '可以直接对比', '能不能直接对比', '能否直接对比', '直接对比', '放一起比较', '放一起比', '能不能放一起比', '能不能比较', '能不能直接比较', '能不能直接比', '能否直接比较', '能否直接比', '可以直接比较', '可以直接比', '可不可以比较', '可不可以比', '能不能比较', '能不能比', '能否比较', '能比较', '能比', '可比较', '可比性', '可比', '直接比较', '直接比', '口径一致', '口径一样', '口径相同', '同口径', '同一口径', '放在一起比较', '放在一起比', '横向比较', '一起比较', '一样吗', '一致吗', '相同吗', '可以比较吗', '比较吗'], () => ({ t: 'intent', v: 'comparable' }));
english(['comparable', 'apples to apples', 'like for like', 'compare directly'], () => ({ t: 'intent', v: 'comparable' }));
rule('(?:谁|哪家|哪个|哪一家|哪一个|哪边)(?:公司)?(?:的)?(?:更|比较)?(?:高|多|大|低|少|小|强|好|厉害|赚得多|赚得更多|领先)?', () => ({ t: 'intent', v: 'rank' }));
words(['高吗', '低吗', '多吗', '少吗', '大吗', '高', '低', '更高', '更多', '更大', '更低', '更少', '更小', '更强', '排名', '排序', '排行', '领先', '高于', '低于', '超过', '胜过', '赢', '谁多', '谁高', '谁大'], () => ({ t: 'intent', v: 'rank' }));
english(['which is higher', 'which is larger', 'which is bigger', 'which company', 'higher', 'larger', 'bigger', 'more than', 'less than', 'rank', 'ranking', 'outperform'], () => ({ t: 'intent', v: 'rank' }));
words(['多赚了多少', '多赚了', '少赚了多少', '少赚了', '多花了多少', '多花了', '少花了多少', '多多少', '少多少', '高多少', '低多少', '差多少', '相差多少', '差了多少', '差多大', '差距', '差额', '相差', '差了', '多出', '多了多少', '少了多少', '大多少', '小多少', '减去', '减'], () => ({ t: 'intent', v: 'diff' }));
english(['difference', 'gap', 'minus'], () => ({ t: 'intent', v: 'diff' }));
words(['对比一下', '对比', '比较一下', '比较', '比一比', '比一下', '相比', '相较于', '相较', '相对于', '相对', '比起', '较', '对照', '比', '并排', '并列', '放一起看', '放在一起看', '一起看', '放一起', '放在一起', '列出来', '各列出来'], () => ({ t: 'intent', v: 'compare' }));
english(['versus', 'vs\\.?', 'compare', 'compared', 'comparison', 'side by side', 'side-by-side'], () => ({ t: 'intent', v: 'compare' }));
words(['分别是', '分别为', '分别', '各自', '各是', '各为', '各', '两年', '这两年', '两个财年', '两个年度', '两期', '两份'], () => ({ t: 'intent', v: 'multi' }));
words(['和', '与', '及', '以及', '跟', '同', '、', '还有', '加上'], () => ({ t: 'intent', v: 'multi' }));
english(['and', '&'], () => ({ t: 'intent', v: 'multi' }));

// Fillers: words that carry no financial meaning here.
words(['请问一下', '请问', '麻烦你', '麻烦', '帮我查一下', '帮我查查', '帮我查', '帮我看看', '帮我看一下', '帮我', '帮忙', '我想知道', '想知道', '我想问', '想问一下', '想问', '问一下', '问下', '我想', '我要', '查一下', '查查', '查询', '查', '看一下', '看看', '看下', '告诉我', '告诉', '给我', '说说', '说一下', '讲讲', '列出', '显示', '报一下', '一下', '下',
  '是多少', '为多少', '有多少', '多少', '是几', '几', '多大', '几何', '怎么样', '如何', '怎样', '情况', '水平', '规模', '数据', '数字', '数值', '金额', '一共', '总共', '大概', '大约', '约', '左右', '具体', '到底', '究竟', '是否', '是不是', '有没有', '能否', '能不能', '可以', '能', '吗', '呢', '吧', '呀', '啊', '嘛', '哈', '哦', '噢', '呗', '啦',
  '的', '了', '是', '为', '在', '于', '中', '里面', '里', '上面', '上', '内', '这个', '那个', '这', '那', '其', '它', '它们', '他们', '公司', '企业', '集团', '财报', '年报', '报表', '报告', '表里', '表上', '表中', '利润表', '损益表', '资产负债表', '现金流量表', '财务报表', '10-k', '10k', 'form 10-k', '披露', '公布', '列示', '报告期', '期末', '期间', '期内', '时', '时候', '那年', '这年', '全年', '年度', '财年', '年', '会计年度', '一年', '整年', '对应', '相应', '账上', '手上', '手里', '兜里', '账面', '口径', '按', '用', '以', '计', '计算', '表示', '单位', '换算成', '换算', '折合', '折算成', '折算', '合多少', '算', '算一下', '数额', '共', '实际', '原文', '原值', '报的', '的话', '呢?', '谢谢', '多谢', '你好', '您好', '请', '一', '个', '笔', '期'],
  () => ({ t: 'filler' }));
words(['合计', '总额', '总计', '总和', '之和', '加起来', '合起来', '加总', '相加', '加在一起', '合在一起'], m => ({ t: 'sum', text: m[0] } as Token));
english(['total', 'sum', 'combined', 'altogether'], m => ({ t: 'sum', text: m[0] } as Token));
words(['截至', '截止到', '截止', '截至到', '止年度', '止的年度', '止的财年', '止财年', '止的', '止'], () => ({ t: 'ended' } as Token));
english(['year ended', 'ended', 'ending'], () => ({ t: 'ended' } as Token));
words(['从', '由', '到', '至', '之间', '期间的', '期间长度', '长度', '天数', '直接', '业务', '方面', '总', '把', '写完整数字', '完整数字', '完整的数字', '写完整', '全部数字', '报给我', '报', '付了多少现金', '支付的现金', '付了多少', '付了', '支付了', '付款', '支付', '经营活动', '多少钱', '花了多少钱', '花了多少', '花了', '花的', '花', '余额', '卖的', '卖', '这块', '那块', '这一块', '这部分', '那部分', '财年末', '财年底', '年度末', '有', '一共有', '分别有'], () => ({ t: 'filler' }));
english(['what', "what's", 'whats', 'is', 'was', 'were', 'are', 'the', 'of', 'in', 'for', 'how', 'much', 'many', 'did', 'does', 'do', 'make', 'made', 'please', 'show', 'me', 'tell', 'give', 'between', 'from', 'to', 'its', "it's", 'it', 'a', 'an', 'on', 'at', 'as', 'by', 'with', 'reported', 'report', 'annual', 'amount', 'have', 'has', 'had', 'balance', 'holdings', 'hold', 'value', 'figure', 'year', 'fiscal', 'end', 'as of', 'fy', 'can', 'i', 'we', 'you', 'they', 'their', 'company', 'inc', 'corp', 'statement', 'balance sheet', 'income statement', 'cash flow statement', 'lookup', 'look up', 'find', 'get', 'number', 'about', 'than', 'were'],
  () => ({ t: 'filler' }));
rule('[\\s,，。.!！?？;；:：()（）\\[\\]【】<>《》/\\\\\'"·~—_-]+', () => ({ t: 'filler' }));

/* ---------- tokenizer ---------- */
export function tokenize(question: string): ReturnType<typeof tokenizeNormalised> {
  return tokenizeNormalised(normalise(question));
}
function tokenizeNormalised(s: string): { tokens: Token[]; unknown: string[]; unknownAt: number[]; spans: { start: number; end: number; token: Token | null }[] } {
  const tokens: Token[] = [];
  const spans: { start: number; end: number; token: Token | null }[] = [];
  const unknown: string[] = [];
  const unknownAt: number[] = [];
  let pending = '';
  let i = 0;
  while (i < s.length) {
    let best: { len: number; token: Token | null } | null = null;
    for (const [re, make] of rules) {
      re.lastIndex = i;
      const m = re.exec(s);
      if (m && m[0].length > 0 && (!best || m[0].length > best.len)) best = { len: m[0].length, token: make(m) };
    }
    if (!best) { if (s[i] !== ' ') unknownAt.push(i); pending += s[i]; i += 1; continue; }
    if (pending.trim()) unknown.push(pending.trim());
    pending = '';
    if (best.token && best.token.t !== 'filler') tokens.push(best.token);
    spans.push({ start: i, end: i + best.len, token: best.token });
    i += best.len;
  }
  if (pending.trim()) unknown.push(pending.trim());
  return { tokens, unknown, unknownAt, spans };
}

/* Typo repair: at most two corrections, each one character (CJK substitution; Latin
   substitution, deletion, insertion or adjacent transposition) to a known issuer or metric name of
   3+ CJK / 5+ Latin characters, overlapping an unreadable character, and unique: if two corrections
   would both make the question readable, nothing is repaired. Each repair is reported. */
function variants(sub: string, target: string): boolean {
  if (sub === target) return false;
  if (sub.length === target.length) {
    let diff = 0;
    for (let k = 0; k < sub.length; k++) if (sub[k] !== target[k]) diff++;
    if (diff === 1) return true;
    if (diff === 2 && /^[a-z]/.test(target)) {
      for (let k = 0; k + 1 < sub.length; k++) if (sub[k] === target[k + 1] && sub[k + 1] === target[k] && sub.slice(0, k) === target.slice(0, k) && sub.slice(k + 2) === target.slice(k + 2)) return true;
    }
    return false;
  }
  if (!/^[a-z]/.test(target) || Math.abs(sub.length - target.length) !== 1) return false;
  const [short, long] = sub.length < target.length ? [sub, target] : [target, sub];
  for (let k = 0; k < long.length; k++) if (long.slice(0, k) + long.slice(k + 1) === short) return true;
  return false;
}
export function repair(s: string): { text: string; fixes: [string, string][] } | null {
  let current = s;
  const fixes: [string, string][] = [];
  for (let round = 0; round < 2; round++) {
    const { unknownAt, spans } = tokenizeNormalised(current);
    if (!unknownAt.length) break;
    const options = new Map<string, [string, string]>();
    for (const target of LITERALS) {
      const latin = /^[a-z]/.test(target);
      if (target.length < (latin ? 5 : 3)) continue;
      for (const len of latin ? [target.length - 1, target.length, target.length + 1] : [target.length]) {
        for (const at of unknownAt) for (let start = Math.max(0, at - len + 1); start <= at && start + len <= current.length; start++) {
          const sub = current.slice(start, start + len);
          if (!variants(sub, target)) continue;
          if (latin && (/[a-z0-9]/.test(current[start - 1] ?? '') || /[a-z0-9]/.test(current[start + len] ?? ''))) continue;
          // Only unreadable characters, or a recognised word with the same meaning, may be rewritten:
          // "现金多" must not become "现金流", and fillers are never consumed.
          const meaning = JSON.stringify(rules.map(([re, make]) => { re.lastIndex = 0; const m = new RegExp(re.source).exec(target); return m && m[0] === target ? make(m) : null; }).find(Boolean) ?? null);
          const overlapping = spans.filter(sp => sp.start < start + len && sp.end > start && sp.token?.t !== 'filler' || (sp.start < start + len && sp.end > start && sp.token?.t === 'filler' && current.slice(sp.start, sp.end).trim()));
          if (overlapping.some(sp => sp.start < start || sp.end > start + len || JSON.stringify(sp.token) !== meaning)) continue;
          const next = current.slice(0, start) + target + current.slice(start + len);
          const after = tokenizeNormalised(next).unknownAt.length;
          if (after < unknownAt.length) options.set(next, [sub, target]);
        }
      }
    }
    if (!options.size) return null;
    const scored = [...options.entries()].map(([text, fix]) => ({ text, fix, left: tokenizeNormalised(text).unknownAt.length }));
    const best = Math.min(...scored.map(x => x.left));
    const winners = scored.filter(x => x.left === best);
    if (winners.length !== 1) return null;
    current = winners[0].text;
    fixes.push(winners[0].fix);
  }
  return tokenizeNormalised(current).unknownAt.length ? null : { text: current, fixes };
}

/* ---------- reading ---------- */
const FY_END: Record<string, Record<number, string>> = {
  MSFT: { 2024: '2024-06-30', 2025: '2025-06-30' }, AAPL: { 2024: '2024-09-28', 2025: '2025-09-27' },
};
const pad = (n: number) => String(n).padStart(2, '0');
const ZH: Record<string, string> = { MSFT: '微软', AAPL: '苹果' };
const fy = (y: number) => `FY${y}`;

export function suggestion(pack: Pack, issuer: string, metricId: string, years: number[], extra = ''): string {
  const label = pack.metrics[metricId]?.label ?? metricId;
  const period = years.length === 2 ? `${fy(years[0])} 到 ${fy(years[1])}` : years.length ? fy(years[0]) : 'FY2025';
  const verb = years.length === 2 ? '变化了多少' : '是多少';
  return `${ZH[issuer] ?? issuer}${period}${label}${extra}${verb}`;
}

const GENERAL = ['微软 FY2025 营业收入是多少', '苹果 FY2024 到 FY2025 经营活动现金流变化了多少', '微软和苹果 FY2025 的经营现金流能直接比吗'];

export function understand(pack: Pack, question: string): Understood {
  const text = question.trim();
  if (!text) return { type: 'clarify', reason: 'empty', detail: '请输入一个关于微软或苹果 FY2024、FY2025 年报数字的问题。', suggestions: GENERAL };
  if (text.length > 200) return { type: 'refuse', reason: 'too_long', detail: '问题超过 200 字。请一次只问一个数字、一组两期变化或一组可比性。', suggestions: GENERAL };
  let { tokens, unknown } = tokenize(text);
  const notes: string[] = [];
  if (unknown.length) {
    const fixed = repair(normalise(text));
    if (!fixed) {
      return { type: 'refuse', reason: 'unknown_words', unknown,
        detail: `没有读懂：${unknown.map(u => `“${u}”`).join('、')}。为避免给出不对应的数字，整句不作答。`, suggestions: GENERAL };
    }
    ({ tokens, unknown } = tokenizeNormalised(fixed.text));
    for (const [from, to] of fixed.fixes) notes.push(`“${from}”按“${to}”理解（疑似错字）；如不是这个意思，请改写后再问。`);
  }
  const of = <K extends Token['t']>(t: K) => tokens.filter(x => x.t === t) as Extract<Token, { t: K }>[];

  const others = of('other_issuer');
  if (others.length) return { type: 'refuse', reason: 'issuer_not_registered', detail: `演示只登记了微软和苹果的 FY2025 年报，没有“${others[0].text}”的材料。`, suggestions: GENERAL };
  const segment = of('scope').find(s => s.v === 'segment' || s.v === 'continuing');
  if (segment) {
    const why = segment.v === 'continuing' ? '只登记了报表列示总额，持续/终止经营的分解未登记。' : '只登记了合并报表的总额行，分部、产品线、地区或子公司数据未登记，不能用合并数代替。';
    return { type: 'refuse', reason: 'scope_not_registered', detail: `“${segment.text}”不在登记范围：${why}`, suggestions: GENERAL };
  }
  const unsupported = of('unsupported_metric');
  if (unsupported.length) {
    return { type: 'refuse', reason: 'metric_not_registered',
      detail: unsupported[0].why ?? `“${unsupported[0].text}”未登记。演示只登记了收入、成本、毛利与毛利率、净利润、经营现金流、固定资产购建支出、三类债务、流动资产、流动负债和现金类项目。`,
      suggestions: GENERAL };
  }
  const badPeriod = of('period_unsupported');
  if (badPeriod.length) {
    const p = badPeriod[0];
    const detail = p.why === 'ambiguous_date'
      ? `“${p.text}”的日月顺序有歧义（可以读作 月/日 或 日/月）。请写成“2025年9月27日”或“2025-09-27”。`
      : p.why === 'calendar'
      ? `“${p.text}”是自然年口径。两家公司的财年都不是自然年（微软截至 6 月 30 日，苹果截至 9 月最后一个周六），演示没有按自然年重排的数字。`
      : p.why === 'year_end'
        ? `“${p.text}”通常指 12 月 31 日，但两家的财年末分别是 6 月 30 日（微软）和 9 月下旬（苹果）。请改问“FY2025 期末”。`
        : `“${p.text}”不在登记范围：演示只有 FY2024、FY2025 两个完整财年的年度列，没有季度、半年、月度或滚动 12 个月数据。`;
    return { type: 'refuse', reason: 'period_not_registered', detail, suggestions: GENERAL };
  }
  const unanchored = of('relative').find(r => r.v === 'unanchored');
  if (unanchored) {
    return { type: 'refuse', reason: 'relative_period', detail: `“${unanchored.text}”无法对应到一个登记财年（演示只有 FY2024、FY2025，也不做预测）。请直接写财年，例如 FY2025。`, suggestions: GENERAL };
  }

  // Issuers.
  let issuers = [...new Set(of('issuer').map(x => x.v))];
  if (of('both').length) issuers = ['MSFT', 'AAPL'];

  // Units.
  const units = of('unit');
  if (units.some(u => u.scale === 'unsupported')) return { type: 'refuse', reason: 'unit_not_supported', detail: `“${units.find(u => u.scale === 'unsupported')!.text}”这一量级未提供换算；可以问百万、亿、十亿、万、千美元或美元。`, suggestions: GENERAL };
  const currencies = [...new Set(units.map(u => u.currency).filter(Boolean))] as string[];
  const scales = [...new Set(units.map(u => u.scale).filter(Boolean))] as string[];
  if (currencies.length > 1 || scales.length > 1) return { type: 'clarify', reason: 'unit_conflict', detail: '问题里出现了不止一种单位，请只保留一种。', suggestions: GENERAL };
  let unit: Unit | null = null;
  if (units.length) {
    unit = { currency: currencies[0] ?? 'USD', scale: scales[0] ?? null };
    if (!currencies.length) notes.push('未写币种：报表以美元列示，按美元换算。');
  }
  const wantsPercent = of('percent').length > 0;

  // Scope.
  const scope = of('scope').some(s => s.v === 'parent_only') ? 'parent_only' : 'consolidated';

  // Metrics, resolving ambiguous words.
  const allMetricTokens = of('metric');
  const firm = allMetricTokens.filter(m => !m.soft);
  // Verb phrases such as 赚了 / 卖了 name a metric only when no metric is named outright.
  const metricTokens = firm.length ? firm : allMetricTokens;
  let metrics = [...new Set(metricTokens.map(m => m.v))];
  const categoryWords = of('ambiguous').filter(a => a.family === 'category');
  if (categoryWords.length) {
    const kinds = new Set<string>(categoryWords.map(a => (a.text.startsWith('服务') ? 'service' : 'product')));
    for (const m of metrics) { const c = /^(product|service)_/.exec(m); if (c) kinds.add(c[1]); }
    const map: Record<string, string> = { revenue: 'revenue', cost_of_revenue: 'cost', gross_margin_pct: 'gross_margin_pct' };
    let base = metrics.filter(m => !/^(product|service)_/.test(m));
    if (kinds.size > 1 && metrics.length === 1) {
      // "产品和服务收入": both categories of one base metric.
      const root = base[0] ?? ({ product_revenue: 'revenue', service_revenue: 'revenue', product_cost: 'cost_of_revenue', service_cost: 'cost_of_revenue',
        product_gross_margin_pct: 'gross_margin_pct', service_gross_margin_pct: 'gross_margin_pct' } as Record<string, string>)[metrics[0]];
      const both: Record<string, string[]> = { revenue: ['product_revenue', 'service_revenue'], cost_of_revenue: ['product_cost', 'service_cost'], gross_margin_pct: ['product_gross_margin_pct', 'service_gross_margin_pct'] };
      if (root && both[root]) { metrics = both[root]; base = []; }
    }
    if (base.length === 0 && metrics.length === 2 && kinds.size > 1) { /* resolved above */ }
    else if (kinds.size > 1 || metrics.length !== 1 || (base.length === 1 && !map[base[0]] && base[0] !== 'gross_profit')) {
      return { type: 'clarify', reason: 'ambiguous_metric', detail: '“产品/服务”需要和一个收入、成本或毛利率指标连在一起问，例如“苹果 FY2025 服务收入”。', suggestions: GENERAL };
    }
    if (base[0] === 'gross_profit') return { type: 'refuse', reason: 'metric_not_registered', detail: '分类毛利金额是由收入减成本推导的，未作为披露金额登记；可以问产品或服务毛利率。', suggestions: GENERAL };
    if (base.length === 1) {
      const cat = [...kinds][0];
      metrics = [base[0] === 'revenue' ? `${cat}_revenue` : base[0] === 'cost_of_revenue' ? `${cat}_cost` : `${cat}_gross_margin_pct`];
    } else if (metrics.length === 1 && !metrics[0].startsWith([...kinds][0])) {
      return { type: 'clarify', reason: 'ambiguous_metric', detail: '问题里的产品/服务分类前后不一致，请只问一类。', suggestions: GENERAL };
    }
  }
  for (const m of metricTokens) if (m.note && !notes.includes(m.note)) notes.push(m.note);
  const families: Record<string, string[]> = {
    profit: ['net_income', 'gross_profit', 'gross_margin_pct'], cash: ['cfo', 'capex'], ppe: ['capex'],
    debt: ['short_debt', 'current_debt', 'long_debt'], income: ['net_income', 'revenue'], investment: ['capex', 'short_investments', 'liquid_assets'],
    category: ['product_revenue', 'service_revenue', 'product_cost', 'service_cost', 'product_gross_margin_pct', 'service_gross_margin_pct'],
  };
  for (const amb of of('ambiguous').filter(a => a.family !== 'category')) {
    if (metrics.some(m => families[amb.family].includes(m))) continue;
    if (['钱', '账上的钱', '手上的钱'].includes(amb.text) && metrics.length) continue;
    if (amb.family === 'ppe') {
      return { type: 'refuse', reason: 'metric_not_registered', detail: `“${amb.text}”的余额未登记：演示登记的是现金流量表中购建固定资产的现金支出（发生额），不是资产负债表上的固定资产余额。`, suggestions: [suggestion(pack, issuers[0] ?? 'MSFT', 'capex', [])] };
    }
    if (amb.family === 'cash' && of('sum').length) {
      return { type: 'refuse', reason: 'metric_not_registered', detail: issuers.includes('MSFT') ? '微软只登记了“现金、现金等价物与短期投资”合计一行；没有其他口径的现金合计。' : '苹果没有登记“现金合计”一行：现金及现金等价物与流动有价证券分别列示，演示不把它们相加。', suggestions: GENERAL };
    }
    if (amb.family === 'cash' && metrics.some(m => ['liquid_assets', 'cash_equivalents', 'short_investments'].includes(m))) {
      return { type: 'clarify', reason: 'ambiguous_metric', detail: '问题里既有一个登记的现金类项目，又单独提到“现金”。微软没有单列现金及现金等价物；苹果的现金及现金等价物与有价证券分列。请只问一项。', suggestions: GENERAL };
    }
    if (amb.family === 'cash' && issuers.length === 1 && issuers[0] === 'AAPL' && !metrics.length) {
      metrics.push('cash_equivalents');
      notes.push('“现金”按苹果资产负债表的“现金及现金等价物”理解；流动有价证券另列，未合并。');
      continue;
    }
    const options: Record<string, string[]> = {
      profit: ['net_income', 'gross_profit', 'gross_margin_pct'], cash: issuers.includes('MSFT') ? ['liquid_assets'] : ['cash_equivalents', 'short_investments'],
      debt: ['short_debt', 'current_debt', 'long_debt'], income: ['net_income', 'revenue'], investment: ['capex', 'short_investments'],
      category: ['product_revenue', 'service_revenue', 'product_gross_margin_pct'], ppe: ['capex'],
    };
    const issuer = issuers[0] ?? 'MSFT';
    const yrs = of('year').map(y => y.v).filter(y => pack.years.includes(y));
    const detail = {
      profit: '“利润”可以指净利润、毛利或毛利率（营业利润未登记）。请指明。',
      cash: issuers.includes('MSFT') ? '微软资产负债表只登记了“现金、现金等价物与短期投资”合计一行，没有单列现金及现金等价物；不能把合计当作现金。' : '“现金”可以指现金及现金等价物，或加上流动有价证券；请指明公司和项目。',
      debt: '“债务”可以指短期债务、一年内到期的长期债务或非流动长期债务；总债务没有登记，演示不自行相加。',
      income: '“净收入”可能指净利润，也可能指收入。请指明。',
      investment: '“投资”可以指固定资产购建支出，或苹果的流动有价证券；请指明。',
      category: '请说明是产品/服务的收入、成本还是毛利率。', ppe: '',
    }[amb.family] as string;
    return { type: 'clarify', reason: 'ambiguous_metric', detail, suggestions: options[amb.family].filter(m => pack.observations.some(o => o.issuer_id === issuer && o.metric === m) || pack.percents.some(o => o.issuer_id === issuer && o.metric === m)).map(m => suggestion(pack, issuer, m, yrs.slice(0, 1))) };
  }

  // Intent.
  const intents = new Set(of('intent').map(i => i.v));
  const relPrev = of('relative').some(r => r.v === 'prev');
  const latest = of('relative').some(r => r.v === 'latest');

  // Years, from fiscal-year words and dates.
  const years: number[] = [];
  for (const y of of('year')) {
    if (y.end === 'calendar') {
      return { type: 'refuse', reason: 'period_not_registered', detail: `“${y.v}年末/年底”通常指 ${y.v}-12-31，但两家的财年末是 6 月 30 日（微软）和 9 月下旬（苹果），演示没有 12 月 31 日的余额。请改问“FY${y.v} 期末”。`, suggestions: GENERAL };
    }
    years.push(y.v);
  }
  for (const y of of('years')) years.push(...y.v);
  for (const d of of('date')) {
    const targets = issuers.length ? issuers : ['MSFT', 'AAPL'];
    const flowsAsked = metrics.filter(m => pack.metrics[m]?.kind !== 'instant');
    if (flowsAsked.length && d.d === null) {
      return { type: 'refuse', reason: 'period_not_registered', detail: `“${d.y}年${d.m}月”的${pack.metrics[flowsAsked[0]]?.label ?? flowsAsked[0]}是月度口径；演示只有完整财年的发生额，没有月度数据。`, suggestions: GENERAL };
    }
    let matched: number | null = null;
    for (const issuer of targets) {
      for (const [yy, end] of Object.entries(FY_END[issuer])) {
        const [ey, em, ed] = end.split('-').map(Number);
        if (d.y === ey && d.m === em && (d.d === null || d.d === ed)) matched = Number(yy);
      }
    }
    const asked = d.d === null ? `${d.y}-${pad(d.m)}` : `${d.y}-${pad(d.m)}-${pad(d.d)}`;
    if (matched === null || targets.length > 1 && targets.some(issuer => !Object.values(FY_END[issuer]).some(end => end.startsWith(asked)))) {
      const ends = targets.map(i => `${ZH[i]} ${FY_END[i][2024]}、${FY_END[i][2025]}`).join('；');
      return { type: 'refuse', reason: 'date_not_registered', detail: `${asked} 不是登记的财年末（${ends}）。演示只有财年末余额和完整财年的发生额，不做日期换算。`, suggestions: GENERAL };
    }
    const flows = metrics.filter(m => pack.metrics[m]?.kind !== 'instant');
    if (flows.length && d.d === null) {
      return { type: 'refuse', reason: 'period_not_registered', detail: `“${d.y}年${d.m}月”的${pack.metrics[flows[0]]?.label ?? flows[0]}是月度口径；演示只有完整财年的发生额，没有月度数据。`, suggestions: [suggestion(pack, issuers[0] ?? 'MSFT', flows[0], [matched])] };
    }
    if (flows.length && !of('ended').length) {
      return { type: 'refuse', reason: 'period_not_registered', detail: `${asked} 是一个时点；${pack.metrics[flows[0]]?.label ?? flows[0]}是整个财年的发生额。请问“FY${matched}”或“截至 ${asked} 止的财年”。`, suggestions: [suggestion(pack, issuers[0] ?? 'MSFT', flows[0], [matched])] };
    }
    if (d.d === null) notes.push(`“${d.y}年${d.m}月”按 FY${matched} 期末（${targets.map(i => FY_END[i][matched!]).join('、')}）理解。`);
    years.push(matched);
  }
  if (!years.length && of('document').length) years.push(...of('document').map(d => d.v));
  if (latest && !years.length) { years.push(2025); notes.push('“最新”按演示登记的最新年报 FY2025 理解。'); }
  for (const y of years) if (y < 2000 || y > 2099) return { type: 'refuse', reason: 'unknown_words', detail: `“${y}”不是可识别的财年。`, suggestions: GENERAL };
  let uniqueYears = [...new Set(years)].sort((a, b) => a - b);

  if (!issuers.length) {
    if (!metrics.length) return { type: 'clarify', reason: 'no_subject', detail: '请说明公司（微软或苹果）和指标，例如营业收入、净利润、经营现金流。', suggestions: GENERAL };
    return { type: 'clarify', reason: 'no_issuer', detail: '请说明是微软还是苹果的数字。', suggestions: ['MSFT', 'AAPL'].map(i => suggestion(pack, i, metrics[0], uniqueYears.slice(0, 1))) };
  }
  if (!metrics.length) {
    return { type: 'clarify', reason: 'no_metric', detail: '请说明要查的指标，例如营业收入、净利润、经营现金流、长期债务或毛利率。', suggestions: ['revenue', 'net_income', 'cfo'].map(m => suggestion(pack, issuers[0], m, uniqueYears.slice(0, 1))) };
  }
  if (metrics.length > 3) return { type: 'refuse', reason: 'too_many', detail: '一次最多问三个指标。', suggestions: GENERAL };

  // Kind.
  let intent: Intent = 'lookup';
  const twoIssuers = issuers.length === 2;
  const wantsChange = intents.has('change') || intents.has('growth') || relPrev;
  if (!twoIssuers && metrics.length >= 2 && !wantsChange && (intents.has('diff') || intents.has('compare') || intents.has('rank') || intents.has('comparable'))) {
    return { type: 'refuse', reason: 'derived_not_supported', detail: '演示不计算不同指标之间的差额、比值或排序（例如流动资产减流动负债、资本开支比经营现金流）；可以分别查询每一项。',
      suggestions: metrics.slice(0, 2).map(m => suggestion(pack, issuers[0], m, uniqueYears.slice(0, 1))) };
  }
  if (intents.has('rank')) intent = 'rank';
  else if (intents.has('diff') && twoIssuers) intent = 'diff';
  else if (intents.has('comparable')) intent = 'comparable';
  else if (wantsChange || intents.has('diff')) intent = 'change';
  else if (intents.has('compare') && !twoIssuers) intent = 'change';
  if ((intent === 'rank' || intent === 'diff') && !twoIssuers) {
    // "2025 年比 2024 年高多少" on one issuer is a two-year change.
    intent = uniqueYears.length === 2 || relPrev || intents.has('compare') ? 'change' : intent;
    if (intent === 'rank' || intent === 'diff') return { type: 'clarify', reason: 'rank_needs_two', detail: '要比较的另一项是什么？可以问同一公司 FY2024 到 FY2025 的变化。', suggestions: [suggestion(pack, issuers[0], metrics[0], [2024, 2025])] };
  }
  if (wantsPercent && intent === 'lookup' && !metrics.every(m => pack.metrics[m]?.kind === 'percent')) {
    if (uniqueYears.length <= 1 && (intents.has('change') || intents.has('growth'))) intent = 'change';
    else if (!(intents.has('growth'))) return { type: 'refuse', reason: 'unit_not_applicable', detail: '金额指标没有百分比形式；要看增长率请问“FY2024 到 FY2025 的增长率”。', suggestions: [suggestion(pack, issuers[0], metrics[0], [2024, 2025])] };
  }
  if (intent === 'comparable' && !twoIssuers && !uniqueYears.length) {
    uniqueYears = [2024, 2025];
    notes.push('未指定年度：按演示仅有的两期 FY2024 → FY2025 核对。');
  }
  if (intent === 'change' && uniqueYears.length <= 1) {
    const anchor = uniqueYears[0] ?? 2025;
    if (!uniqueYears.length) notes.push('未指定年度：按演示仅有的两期 FY2024 → FY2025 计算。');
    uniqueYears = [anchor - 1, anchor];
  }
  if (relPrev && intent === 'lookup') {
    return { type: 'refuse', reason: 'relative_period', detail: '“去年/上年”需要一个基准财年；请直接写 FY2024 或 FY2025。', suggestions: GENERAL };
  }
  if (of('sum').length && (issuers.length > 1 || metrics.length > 1)) {
    return { type: 'refuse', reason: 'sum_not_supported', detail: '演示不把不同公司或不同项目相加：登记的是各自的报表行，相加后的口径没有登记。可以分别查询每一项。', suggestions: GENERAL };
  }
  if (of('pp').length && metrics.some(m => pack.metrics[m]?.kind !== 'percent')) {
    return { type: 'clarify', reason: 'unit_not_applicable', detail: '金额指标的变动用差额和变动率（%）表示，不用百分点；百分点只用于毛利率这类百分比的变动。', suggestions: [suggestion(pack, issuers[0], metrics[0], [2024, 2025])] };
  }
  if (metrics.some(m => pack.metrics[m]?.kind === 'percent') && (intents.has('growth') || (wantsPercent && intent === 'change'))) {
    return { type: 'refuse', reason: 'percent_growth_not_supported', detail: '毛利率本身是百分比：演示给出两期之差（百分点），不计算毛利率的相对增长率，以免把两种数字混淆。', suggestions: [suggestion(pack, issuers[0], metrics.find(m => pack.metrics[m]?.kind === 'percent')!, [2024, 2025])] };
  }
  // Per-clause periods or companies ("2025年收入和2024年净利润", "微软FY2025 vs 苹果FY2024") must not be cross-multiplied.
  const explicitYears = [...new Set(years)].length;
  const pairKinds = intent === 'change' || (intent === 'comparable' && !twoIssuers);
  const varying = [issuers.length > 1, metrics.length > 1, !pairKinds && explicitYears > 1].filter(Boolean).length;
  if (varying >= 2) {
    return { type: 'clarify', reason: 'per_clause', detail: '这个问题把不同的公司、指标或年度配在了一起。为避免把没问的组合也列出来，请一次问一组。',
      suggestions: [suggestion(pack, issuers[0], metrics[0], uniqueYears.slice(-1)), suggestion(pack, issuers[issuers.length - 1], metrics[metrics.length - 1], uniqueYears.slice(0, 1))] };
  }
  if (uniqueYears.length > 2) return { type: 'refuse', reason: 'too_many_years', detail: `问了 ${uniqueYears.map(fy).join('、')}：演示只有 FY2024、FY2025，未登记的年度不补值。`, suggestions: GENERAL };

  const reading: Reading = { issuers, metrics, years: uniqueYears, unit, scope, intent, notes };
  const plans: PlannedItem[] = [];
  const item = (issuer_id: string, metricId: string, year: number) => ({ issuer_id, metric: metricId, year });
  const yearsOrDefault = uniqueYears.length ? uniqueYears : [2024, 2025];
  if (!uniqueYears.length && intent === 'lookup') notes.push('未指定年度：列出演示登记的 FY2024 和 FY2025 两期。');
  const base = { unit, scope };
  if (twoIssuers && (intent === 'rank' || intent === 'diff' || intent === 'comparable' || intent === 'lookup')) {
    const yrs = intent === 'lookup' ? yearsOrDefault : uniqueYears.length ? uniqueYears : [2025];
    if (!uniqueYears.length && intent !== 'lookup') notes.push('未指定年度：按 FY2025 并列。');
    for (const metricId of metrics) for (const year of yrs) {
      plans.push({ plan: { kind: 'cross_company', items: [item('MSFT', metricId, year), item('AAPL', metricId, year)], ...base },
        ranking_refused: intent === 'rank' || intent === 'diff', label: `${pack.metrics[metricId]?.label ?? metricId} · ${fy(year)} · 微软与苹果并列` });
    }
  } else {
    for (const issuer of issuers) for (const metricId of metrics) {
      if (intent === 'change' || intent === 'comparable') {
        if (uniqueYears.length !== 2) return { type: 'clarify', reason: 'need_two_years', detail: '请给出两期，例如 FY2024 和 FY2025。', suggestions: [suggestion(pack, issuer, metricId, [2024, 2025])] };
        plans.push({ plan: { kind: intent === 'change' ? 'change' : 'comparable_check', items: [item(issuer, metricId, uniqueYears[0]), item(issuer, metricId, uniqueYears[1])], ...base },
          label: `${ZH[issuer]} · ${pack.metrics[metricId]?.label ?? metricId} · ${fy(uniqueYears[0])} → ${fy(uniqueYears[1])}` });
      } else {
        for (const year of yearsOrDefault) {
          plans.push({ plan: { kind: 'lookup', items: [item(issuer, metricId, year)], ...base }, label: `${ZH[issuer]} · ${pack.metrics[metricId]?.label ?? metricId} · ${fy(year)}` });
        }
      }
    }
  }
  if (plans.length > 6) return { type: 'refuse', reason: 'too_many', detail: '这个问题会展开成太多组结果，请拆成更具体的问题。', suggestions: GENERAL };
  return { type: 'plans', reading, plans };
}

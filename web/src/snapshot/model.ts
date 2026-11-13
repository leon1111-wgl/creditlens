/* Credit snapshot computation (docs/47-credit-snapshot.md, definitions credit-snapshot-def-1).
   Pure data in, pure data out: facts from ixbrl.ts, no DOM. Amounts are exact decimals; ratios
   are computed from the exact amounts. Every input keeps the facts it came from, so each number
   can be traced to its tagged cell. This is a credit view, not a rating. */
import { add, cmp, parse as parseDec, sub, type Dec } from '../decimal';
import { canon, type Context, type Fact, type Parsed } from './ixbrl';

export const DEFINITIONS_VERSION = 'credit-snapshot-def-1';
const DAY = 86_400_000;

export type NsClass = 'us-gaap' | 'dei' | 'srt' | 'ifrs' | 'ext';
export function nsClass(ns: string): NsClass {
  if (ns.startsWith('http://fasb.org/us-gaap/')) return 'us-gaap';
  if (ns.startsWith('http://xbrl.sec.gov/dei/')) return 'dei';
  if (ns.startsWith('http://fasb.org/srt/')) return 'srt';
  if (/xbrl\.ifrs\.org/.test(ns)) return 'ifrs';
  return 'ext';
}

export interface Term {
  fact: number; concept: string; value: string; op: '+' | '−'; period: string; context: string; unit: string;
  decimals: string | null; scale: number; sign: string; text: string; id: string | null; display: boolean; note?: string;
}
export interface Input { key: string; label: string; value: string | null; rule: string; terms: Term[]; missing?: string }
export interface Metric { key: string; label: string; kind: 'amount' | 'ratio'; value: string | number | null; formula: string; inputs: string[]; reason?: string }
export interface Cue { id: string; text: string; facts: number[] }
export interface Snapshot {
  definitions: string; status: 'ok' | 'unsupported';
  template: { kind: 'general' | 'bank' | 'insurer' | 'investment_company' | 'unsupported_form' | 'not_us_gaap'; reason: string; facts: number[] };
  meta: {
    form: string; registrant: string; cik: string; ticker: string; period_end: string; period_start: string | null; months: number | null;
    currency: string; display_scale: number; facts: number; unparsed: number; amended: boolean;
  };
  inputs: Record<string, Input>; metrics: Record<string, Metric>; cues: Cue[];
  ladder: { next12: Input; years2to5: Input; after5: Input; next12_label: string; missing_years: number[] } | null;
  balance_sheet: { tables: number[]; found: boolean };
}

const MONETARY = /^iso4217:([A-Za-z]{3})$/;
const DEBT = ['ShortTermBorrowings', 'CommercialPaper', 'ShortTermBankLoansAndNotesPayable', 'ShortTermNonBankLoansAndNotesPayable', 'OtherShortTermBorrowings', 'BankOverdrafts', 'LinesOfCreditCurrent', 'NotesPayableCurrent', 'LoansPayableCurrent', 'NotesAndLoansPayableCurrent', 'NotesAndLoansPayable', 'DebtCurrent', 'LongTermDebtCurrent', 'LongTermDebtAndCapitalLeaseObligationsCurrent', 'ConvertibleNotesPayableCurrent', 'ConvertibleDebtCurrent', 'SecuredDebtCurrent', 'UnsecuredDebtCurrent', 'OtherLongTermDebtCurrent', 'SeniorNotesCurrent', 'LongTermNotesPayableCurrent', 'LongTermLoansPayableCurrent', 'NotesPayableRelatedPartiesClassifiedCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt', 'LongTermDebtAndCapitalLeaseObligations', 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities', 'DebtLongtermAndShorttermCombinedAmount', 'LongTermNotesPayable', 'LongTermLoansPayable', 'ConvertibleNotesPayable', 'ConvertibleDebtNoncurrent', 'ConvertibleLongTermNotesPayable', 'SeniorNotes', 'SeniorLongTermNotes', 'SecuredLongTermDebt', 'UnsecuredLongTermDebt', 'SecuredDebt', 'UnsecuredDebt', 'LongTermLineOfCredit', 'LineOfCredit', 'OtherLongTermDebtNoncurrent', 'OtherLongTermDebt', 'NotesPayable', 'LoansPayable', 'DebtInstrumentCarryingAmount', 'SubordinatedDebt', 'JuniorSubordinatedNotes', 'NotesPayableRelatedPartiesClassifiedNoncurrent'];
const DEBT_SET = new Set(DEBT);
const SHORT_TERM = new Set(['ShortTermBorrowings', 'CommercialPaper', 'ShortTermBankLoansAndNotesPayable', 'ShortTermNonBankLoansAndNotesPayable', 'OtherShortTermBorrowings', 'BankOverdrafts']);
const SECTION_SUBTOTALS = new Set(['DebtCurrent', 'ShortTermBorrowings']);
const SIDE_SUBTOTALS = new Set(['LongTermDebt', 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities', 'DebtLongtermAndShorttermCombinedAmount']);
const WITH_FINANCE_LEASES = new Set(['LongTermDebtAndCapitalLeaseObligations', 'LongTermDebtAndCapitalLeaseObligationsCurrent', 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities']);
const CASH = ['CashAndCashEquivalentsAtCarryingValue', 'Cash', 'CashAndDueFromBanks', 'CashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'];
const SHORT_INV = ['ShortTermInvestments', 'OtherShortTermInvestments', 'MarketableSecuritiesCurrent', 'MarketableSecurities', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecurities', 'HeldToMaturitySecuritiesCurrent', 'ShortTermInvestmentsAndMarketableSecurities'];
const LIQ_SUBTOTAL = 'CashCashEquivalentsAndShortTermInvestments';
const LIQ_SET = new Set([...CASH, ...SHORT_INV, LIQ_SUBTOTAL]);
const ANCHORS = ['AssetsCurrent', 'Assets', 'LiabilitiesCurrent', 'Liabilities', 'LiabilitiesAndStockholdersEquity'];
export const SUPPORTED_FORMS = new Set(['10-K', '10-K/A', '10-KT', '10-Q', '10-Q/A', '10-QT']);

export const LABELS: Record<string, string> = {
  revenue: '收入', interest: '利息费用', ebit: 'EBIT', da: '折旧摊销', cfo: '经营现金流', capex: '资本开支',
  assets_current: '流动资产', liabilities_current: '流动负债', debt: '总债务', current_debt: '流动债务', liquidity: '现金与短期投资',
  operating_leases: '经营租赁负债', finance_leases: '融资租赁负债',
  net_debt: '净债务', ebitda: 'EBITDA', debt_to_ebitda: '债务／EBITDA', net_debt_to_ebitda: '净债务／EBITDA', interest_coverage: '利息覆盖（EBIT／利息）',
  current_ratio: '流动比率', fcf: '自由现金流', fcf_to_debt: 'FCF／债务', debt_with_leases: '含租赁负债的债务',
  next12: '未来 12 个月到期', years2to5: '第 2–5 年到期', after5: '5 年以后到期',
};

const days = (a: string, b: string) => Math.round((Date.parse(b + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / DAY);
const isDim = (c: Context | undefined) => !c || c.dims.length > 0;
const decRank = (d: string | null) => d === null ? -1e9 : /^inf$/i.test(d) ? 1e9 : Number(d);
const dec = (s: string) => parseDec(s);
const sumOf = (values: string[]) => canon(values.reduce<Dec>((acc, v) => add(acc, dec(v)), { int: 0n, exp: 0 }));
const num = (s: string) => Number(s);

interface Pick { value: string; chosen: Fact; occurrences: Fact[]; inconsistent: boolean }

export function computeSnapshot(parsed: Parsed): Snapshot {
  const { facts, contexts, units, dei } = parsed;
  const form = (dei.DocumentType?.text || '').toUpperCase().replace(/\s+/g, '');
  const docCtx = contexts[dei.DocumentType?.ctx || ''];
  const D = docCtx?.end || docCtx?.instant || '';
  const usgaap = facts.filter(f => nsClass(f.ns) === 'us-gaap');
  const currencyOf = (f: Fact) => MONETARY.exec(units[f.unit] || '')?.[1]?.toUpperCase() || null;
  const consolidated = (f: Fact) => f.value !== null && !isDim(contexts[f.ctx]);

  const assetsFact = usgaap.find(f => f.local === 'Assets' && consolidated(f) && contexts[f.ctx]?.instant === D && currencyOf(f));
  let currency = assetsFact ? currencyOf(assetsFact)! : '';
  if (!currency) {
    const counts = new Map<string, number>();
    for (const f of facts) { const c = currencyOf(f); if (c) counts.set(c, (counts.get(c) || 0) + 1); }
    currency = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || 'USD';
  }
  const money = (f: Fact) => consolidated(f) && currencyOf(f) === currency;

  // P: longest consolidated duration ending at D (≤ 400 days) carrying a report-currency amount.
  let P: { start: string; end: string; days: number } | null = null;
  for (const f of facts) {
    if (!money(f)) continue;
    const c = contexts[f.ctx];
    if (!c.start || c.end !== D) continue;
    const n = days(c.start, c.end);
    if (n <= 400 && (!P || n > P.days)) P = { start: c.start, end: c.end, days: n };
  }
  const months = P ? Math.round(P.days / 30.44) : null;
  const annual = months !== null && months >= 12;

  const index = new Map<string, Fact[]>();
  for (const f of usgaap) { if (!money(f)) continue; (index.get(f.local) || index.set(f.local, []).get(f.local)!).push(f); }
  const inI = (f: Fact) => contexts[f.ctx].instant === D;
  const inP = (f: Fact) => !!P && contexts[f.ctx].start === P.start && contexts[f.ctx].end === P.end;
  const inconsistent = new Set<string>();
  function pick(local: string, when: 'I' | 'P'): Pick | null {
    const list = (index.get(local) || []).filter(when === 'I' ? inI : inP);
    if (!list.length) return null;
    const values = new Set(list.map(f => f.value!));
    let chosen = list[0];
    if (values.size > 1) {
      inconsistent.add(local);
      chosen = list.slice().sort((a, b) => decRank(b.decimals) - decRank(a.decimals) || a.i - b.i)[0];
    }
    const same = list.filter(f => f.value === chosen.value);
    const shown = same.find(f => f.display) || same[0];
    return { value: chosen.value!, chosen: shown, occurrences: list, inconsistent: values.size > 1 };
  }
  const periodText = (f: Fact) => { const c = contexts[f.ctx]; return c.instant ? c.instant : `${c.start}..${c.end}`; };
  const term = (f: Fact, op: '+' | '−' = '+', note?: string): Term => ({
    fact: f.i, concept: f.name, value: f.value!, op, period: periodText(f), context: f.ctx, unit: units[f.unit] || f.unit,
    decimals: f.decimals, scale: f.scale, sign: f.sign, text: f.text, id: f.id, display: f.display, ...(note ? { note } : {}),
  });
  const valueOf = (terms: Term[]) => canon(terms.reduce<Dec>((acc, t) => t.op === '+' ? add(acc, dec(t.value)) : sub(acc, dec(t.value)), { int: 0n, exp: 0 }));
  const input = (key: string, rule: string, terms: Term[], missing?: string): Input =>
    terms.length || !missing ? { key, label: LABELS[key], value: terms.length ? valueOf(terms) : null, rule, terms } : { key, label: LABELS[key], value: null, rule, terms: [], missing };
  function firstOf(key: string, locals: string[], when: 'I' | 'P'): Input {
    for (const l of locals) { const p = pick(l, when); if (p) return input(key, l, [term(p.chosen)]); }
    return input(key, '', [], `没有 ${locals.join(' | ')}`);
  }
  const has = (local: string, when: 'I' | 'P') => !!pick(local, when);

  const meta: Snapshot['meta'] = {
    form, registrant: dei.EntityRegistrantName?.text || '', cik: (dei.EntityCentralIndexKey?.text || '').replace(/^0+/, ''),
    ticker: dei.TradingSymbol?.text || '', period_end: D, period_start: P?.start || null, months, currency,
    display_scale: 0, facts: facts.length, unparsed: parsed.unparsed, amended: form.endsWith('/A'),
  };
  const cues: Cue[] = [];
  const cue = (id: string, text: string, fs: number[] = []) => cues.push({ id, text, facts: fs });
  const empty = (kind: Snapshot['template']['kind'], reason: string, fs: number[]): Snapshot => ({
    definitions: DEFINITIONS_VERSION, status: 'unsupported', template: { kind, reason, facts: fs }, meta, inputs: {}, metrics: {}, cues, ladder: null,
    balance_sheet: { tables: [], found: false },
  });

  // §3 template
  if (!SUPPORTED_FORMS.has(form)) return empty('unsupported_form', `文件类型是 ${form || '未知'}；本页支持 10-K、10-Q 及其修订版。`, []);
  if (!usgaap.length) return empty('not_us_gaap', '文件里没有 us-gaap 概念：本页只支持按美国公认会计准则（US GAAP）编制的报表。', []);
  const trigger = (rules: [string, 'I' | 'P', boolean?][]) => {
    const out: number[] = [];
    for (const [l, w, positive] of rules) { const p = pick(l, w); if (p && (!positive || cmp(dec(p.value), { int: 0n, exp: 0 }) > 0)) out.push(p.chosen.i); }
    return out;
  };
  const bank = trigger([['Deposits', 'I', true], ['InterestAndDividendIncomeOperating', 'P']]);
  if (bank.length) return empty('bank', '这份文件按银行的报表模板编制（有存款负债或利息收入经营科目）。杠杆、EBITDA 与自由现金流不是衡量银行信用的口径，本页不支持银行模板。', bank);
  const insurer = trigger([['PremiumsEarnedNet', 'P'], ['PolicyholderBenefitsAndClaimsIncurredNet', 'P'], ['LiabilityForFuturePolicyBenefits', 'I'], ['LiabilityForUnpaidClaimsAndClaimsAdjustmentExpense', 'I']]);
  if (insurer.length) return empty('insurer', '这份文件按保险公司的报表模板编制（有已赚保费或保单负债）。本页不支持保险模板。', insurer);
  const fund = trigger([['NetAssetValuePerShare', 'I'], ['InvestmentOwnedAtFairValue', 'I']]);
  if (fund.length) return empty('investment_company', '这份文件按投资公司／BDC 的模板编制（有每股净资产或按公允价值计量的投资组合）。本页不支持这一模板。', fund);

  const inputs: Record<string, Input> = {};
  const bsFacts = usgaap.filter(f => money(f) && inI(f) && f.table >= 0);
  const anchorTables = new Map<number, Set<string>>();
  for (const f of bsFacts) if (ANCHORS.includes(f.local)) (anchorTables.get(f.table) || anchorTables.set(f.table, new Set()).get(f.table)!).add(f.local);
  const bsTables = new Set([...anchorTables.entries()].filter(([, s]) => s.size >= 2).map(([t]) => t));
  const inBs = facts.filter(f => money(f) && inI(f) && bsTables.has(f.table));
  const firstPos = (local: string) => inBs.find(f => nsClass(f.ns) === 'us-gaap' && f.local === local)?.i;
  const posAC = firstPos('AssetsCurrent'), posA = firstPos('Assets'), posLC = firstPos('LiabilitiesCurrent'), posLSE = firstPos('LiabilitiesAndStockholdersEquity');
  const liabSide = (f: Fact) => (posA === undefined || f.i > posA) && (posLSE === undefined || f.i < posLSE);
  const firstByConcept = (list: Fact[]) => { const seen = new Set<string>(); return list.filter(f => !seen.has(f.local) && seen.add(f.local)); };

  // §4 debt rows on the balance sheet
  interface Row { f: Fact; section: 'current' | 'noncurrent' }
  const debtRows: Row[] = firstByConcept(inBs.filter(f => nsClass(f.ns) === 'us-gaap' && DEBT_SET.has(f.local) && liabSide(f)))
    .map(f => ({ f, section: posLC !== undefined ? (f.i < posLC ? 'current' : 'noncurrent') : (/Current/.test(f.local) || SHORT_TERM.has(f.local) ? 'current' : 'noncurrent') }));
  const subtotalNotes: number[] = [];
  function collapse(rows: Row[], wholeSide: boolean): { counted: Row[]; absorbed: Map<number, Row[]> } {
    const absorbedBy = new Map<number, Row[]>();
    const gone = new Set<number>();
    for (let k = 0; k < rows.length; k++) {
      const s = rows[k];
      const isSub = SECTION_SUBTOTALS.has(s.f.local) || SIDE_SUBTOTALS.has(s.f.local);
      if (!isSub) continue;
      const region = (r: Row) => wholeSide || SIDE_SUBTOTALS.has(s.f.local) || r.section === s.section;
      let acc: Dec = { int: 0n, exp: 0 };
      const taken: Row[] = [];
      let found = false;
      for (let j = k - 1; j >= 0; j--) {
        const r = rows[j];
        if (gone.has(r.f.i) || !region(r)) continue;
        acc = add(acc, dec(r.f.value!)); taken.push(r);
        if (cmp(acc, dec(s.f.value!)) === 0) { found = true; break; }
      }
      if (found) { taken.forEach(r => gone.add(r.f.i)); absorbedBy.set(s.f.i, taken.reverse()); }
      else if (taken.length) subtotalNotes.push(s.f.i);
    }
    return { counted: rows.filter(r => !gone.has(r.f.i)), absorbed: absorbedBy };
  }
  const rowTerms = (rows: Row[], absorbed: Map<number, Row[]>) => rows.map(r => {
    const parts = absorbed.get(r.f.i);
    return term(r.f, '+', (r.section === 'current' ? '流动负债' : '非流动负债') + (parts ? `；小计，含 ${parts.map(p => p.f.local).join(' + ')}` : ''));
  });
  const stbFallback = (): Term[] => {
    const st = pick('ShortTermBorrowings', 'I');
    if (st) return [term(st.chosen)];
    return ['CommercialPaper', 'ShortTermBankLoansAndNotesPayable', 'OtherShortTermBorrowings'].map(l => pick(l, 'I')).filter((p): p is Pick => !!p).map(p => term(p.chosen));
  };
  let debtUsesFinanceLeases = false;
  if (debtRows.length) {
    const all = collapse(debtRows, false);
    inputs.debt = input('debt', 'B · 资产负债表债务行', rowTerms(all.counted, all.absorbed));
    const cur = collapse(debtRows.filter(r => r.section === 'current'), true);
    inputs.current_debt = cur.counted.length ? input('current_debt', 'B · 流动负债区段', rowTerms(cur.counted, cur.absorbed))
      : { key: 'current_debt', label: LABELS.current_debt, value: '0', rule: 'B · 流动负债区段没有债务行', terms: [] };
    debtUsesFinanceLeases = all.counted.some(r => WITH_FINANCE_LEASES.has(r.f.local));
  } else {
    const p = (l: string) => pick(l, 'I');
    const ltd = p('LongTermDebt'), ltdN = p('LongTermDebtNoncurrent'), ltdC = p('LongTermDebtCurrent');
    const lcl = p('LongTermDebtAndCapitalLeaseObligations'), lclC = p('LongTermDebtAndCapitalLeaseObligationsCurrent');
    const dc = p('DebtCurrent');
    let rule = '', terms: Term[] = [];
    if (ltd) { rule = 'F1 · LongTermDebt + 短期借款'; terms = [term(ltd.chosen), ...stbFallback()]; }
    else if (dc && ltdN) { rule = 'F2 · DebtCurrent + LongTermDebtNoncurrent'; terms = [term(dc.chosen), term(ltdN.chosen)]; }
    else if (ltdN) { rule = 'F3 · LongTermDebtNoncurrent + LongTermDebtCurrent + 短期借款'; terms = [term(ltdN.chosen), ...(ltdC ? [term(ltdC.chosen)] : []), ...stbFallback()]; }
    else if (lcl) { rule = 'F4 · LongTermDebtAndCapitalLeaseObligations (+ Current) + 短期借款'; terms = [term(lcl.chosen), ...(lclC ? [term(lclC.chosen)] : []), ...stbFallback()]; debtUsesFinanceLeases = true; }
    inputs.debt = terms.length ? input('debt', rule, terms) : input('debt', '', [], bsTables.size ? '资产负债表上没有可识别的债务行，附注里也没有债务合计标签' : '没有找到资产负债表，也没有债务合计标签');
    inputs.current_debt = { key: 'current_debt', label: LABELS.current_debt, value: null, rule: '', terms: [], missing: 'F 规则下不计算流动债务' };
    if (terms.length) cue('Q12', `资产负债表上没有可识别的债务行，债务取自附注或其他位置的合计标签（${rule.split(' · ')[0]}）。核对资产负债表上的借款行。`, terms.map(t => t.fact));
  }
  if (inputs.debt.value === null) cue('Q12', '没有找到债务：资产负债表上没有可识别的债务行，也没有债务合计标签。若公司确实没有借款，债务为 0；请在资产负债表上核对。', []);
  const strange = firstByConcept(inBs.filter(f => liabSide(f) && !(nsClass(f.ns) === 'us-gaap' && DEBT_SET.has(f.local))
    && /Debt|Borrowing|NotesPayable|LoansPayable|CommercialPaper|Notes$|Loans$/i.test(f.local) && !/Interest|Receivable|Lease|Asset|Securities/i.test(f.local)));
  if (strange.length) cue('Q12', `负债一侧有未计入债务的相关科目：${strange.map(f => f.name).join('、')}。核对它们是否是借款。`, strange.map(f => f.i));
  if (debtUsesFinanceLeases) cue('Q10', '债务行用了 LongTermDebtAndCapitalLeaseObligations：债务已含融资租赁负债。', inputs.debt.terms.filter(t => /CapitalLease/.test(t.concept)).map(t => t.fact));

  // Liquidity
  const liqRows = firstByConcept(inBs.filter(f => nsClass(f.ns) === 'us-gaap' && LIQ_SET.has(f.local)
    && (posAC !== undefined ? f.i < posAC : (CASH.includes(f.local) || /Current/.test(f.local)))));
  if (liqRows.length) {
    const gone = new Set<number>(); const parts = new Map<number, Fact[]>();
    for (let k = 0; k < liqRows.length; k++) {
      const s = liqRows[k];
      if (s.local !== LIQ_SUBTOTAL) continue;
      const before = liqRows.slice(0, k).filter(r => !gone.has(r.i));
      if (before.length) {
        before.forEach(r => gone.add(r.i)); parts.set(s.i, before);
        if (sumOf(before.map(r => r.value!)) !== canon(dec(s.value!))) { subtotalNotes.push(s.i); }
      }
    }
    const counted = liqRows.filter(r => !gone.has(r.i));
    inputs.liquidity = input('liquidity', '资产负债表流动资产区段的现金与短期投资行', counted.map(f => term(f, '+', parts.has(f.i) ? `小计，含 ${parts.get(f.i)!.map(p => p.local).join(' + ')}` : undefined)));
  } else {
    const cash = pick('CashAndCashEquivalentsAtCarryingValue', 'I') || pick('CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 'I');
    const st = ['ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent', 'OtherShortTermInvestments'].map(l => pick(l, 'I')).find((x): x is Pick => x !== null);
    inputs.liquidity = cash ? input('liquidity', '后备规则：现金 + 短期投资标签', [term(cash.chosen), ...(st ? [term(st.chosen)] : [])]) : input('liquidity', '', [], '没有现金标签');
  }
  if (inputs.liquidity.terms.some(t => /RestrictedCash/.test(t.concept))) cue('Q11', '现金一项含受限现金（CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents）。', inputs.liquidity.terms.filter(t => /RestrictedCash/.test(t.concept)).map(t => t.fact));
  if (subtotalNotes.length) cue('Q14', '有小计与它前面的行对不上：可能有未识别的行，核对是否重复或遗漏。', subtotalNotes);

  // §5 flows and other inputs
  inputs.revenue = firstOf('revenue', ['Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax', 'SalesRevenueNet'], 'P');
  inputs.interest = firstOf('interest', ['InterestExpense', 'InterestExpenseNonoperating', 'InterestAndDebtExpense', 'InterestExpenseDebt'], 'P');
  const op = pick('OperatingIncomeLoss', 'P');
  if (op) inputs.ebit = input('ebit', 'E1 · OperatingIncomeLoss', [term(op.chosen)]);
  else {
    const pre = pick('IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest', 'P') || pick('IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments', 'P');
    if (pre && inputs.interest.value !== null) {
      inputs.ebit = input('ebit', 'E2 · 税前利润 + 利息费用', [term(pre.chosen), ...inputs.interest.terms]);
      cue('Q2', '没有营业利润标签（OperatingIncomeLoss）：EBIT 由税前利润加利息费用得到，含营业外收支。', inputs.ebit.terms.map(t => t.fact));
    } else inputs.ebit = input('ebit', '', [], pre ? '没有营业利润标签，也没有利息费用可用于 E2' : '没有营业利润或税前利润标签');
  }
  const daRules: [string, string[]][] = [['DepreciationDepletionAndAmortization', ['DepreciationDepletionAndAmortization']], ['DepreciationAmortizationAndAccretionNet', ['DepreciationAmortizationAndAccretionNet']],
    ['DepreciationAndAmortization', ['DepreciationAndAmortization']], ['Depreciation + AmortizationOfIntangibleAssets', ['Depreciation', 'AmortizationOfIntangibleAssets']], ['Depreciation（只有折旧）', ['Depreciation']]];
  inputs.da = input('da', '', [], '没有折旧摊销标签');
  for (const [rule, locals] of daRules) {
    const picks = locals.map(l => pick(l, 'P'));
    if (picks.every(Boolean)) {
      inputs.da = input('da', rule, picks.map(p => term(p!.chosen)));
      if (rule.startsWith('Depreciation（')) cue('Q12', '折旧摊销只找到折旧（Depreciation），未含无形资产摊销。', inputs.da.terms.map(t => t.fact));
      break;
    }
  }
  inputs.cfo = firstOf('cfo', ['NetCashProvidedByUsedInOperatingActivities', 'NetCashProvidedByUsedInOperatingActivitiesContinuingOperations'], 'P');
  inputs.capex = firstOf('capex', ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets', 'PaymentsForCapitalImprovements', 'PaymentsToAcquireOilAndGasPropertyAndEquipment', 'PaymentsToAcquireOilAndGasProperty', 'PaymentsToAcquireOtherPropertyPlantAndEquipment'], 'P');
  inputs.assets_current = firstOf('assets_current', ['AssetsCurrent'], 'I');
  inputs.liabilities_current = firstOf('liabilities_current', ['LiabilitiesCurrent'], 'I');
  const pair = (key: string, whole: string, nonCurrent: string, current: string): Input => {
    const w = pick(whole, 'I');
    if (w) return input(key, whole, [term(w.chosen)]);
    const n = pick(nonCurrent, 'I'), c = pick(current, 'I');
    if (n) return input(key, `${nonCurrent} + ${current}`, [term(n.chosen), ...(c ? [term(c.chosen)] : [])]);
    return input(key, '', [], `没有 ${whole} 或 ${nonCurrent}`);
  };
  inputs.operating_leases = pair('operating_leases', 'OperatingLeaseLiability', 'OperatingLeaseLiabilityNoncurrent', 'OperatingLeaseLiabilityCurrent');
  inputs.finance_leases = pair('finance_leases', 'FinanceLeaseLiability', 'FinanceLeaseLiabilityNoncurrent', 'FinanceLeaseLiabilityCurrent');

  // Ladder
  const M = 'LongTermDebtMaturitiesRepaymentsOfPrincipal';
  let next12 = firstOf('next12', [M + 'InNextTwelveMonths', M + 'InNextRollingTwelveMonths'], 'I');
  let next12Label = '未来 12 个月';
  if (next12.value === null && !annual) {
    const rem = firstOf('next12', [M + 'RemainderOfFiscalYear'], 'I');
    if (rem.value !== null) { next12 = rem; next12Label = '本财年剩余'; }
  }
  const yearTerms: Term[] = []; const missingYears: number[] = [];
  for (const [n, word] of [[2, 'Two'], [3, 'Three'], [4, 'Four'], [5, 'Five']] as [number, string][]) {
    const p = pick(M + 'InYear' + word, 'I') || pick(M + 'InRollingYear' + word, 'I');
    if (p) yearTerms.push(term(p.chosen)); else missingYears.push(n);
  }
  const years = yearTerms.length ? input('years2to5', '第 2–5 年各年相加', yearTerms) : input('years2to5', '', [], '没有第 2–5 年的到期标签');
  const after5 = firstOf('after5', [M + 'AfterYearFive', M + 'InRollingAfterYearFive'], 'I');
  const ladder = next12.value === null && years.value === null && after5.value === null ? null
    : { next12, years2to5: years, after5, next12_label: next12Label, missing_years: yearTerms.length ? missingYears : [] };

  // §6 metrics
  const v = (k: string) => inputs[k]?.value ?? null;
  const metrics: Record<string, Metric> = {};
  const amount = (key: string, formula: string, ins: string[], value: string | null, reason?: string) => { metrics[key] = { key, label: LABELS[key], kind: 'amount', value, formula, inputs: ins, ...(value === null ? { reason: reason || ins.filter(i => v(i) === null).map(i => LABELS[i] + '未找到').join('；') } : {}) }; };
  const ratio = (key: string, formula: string, ins: string[], value: number | null, reason?: string) => { metrics[key] = { key, label: LABELS[key], kind: 'ratio', value, formula, inputs: ins, ...(value === null ? { reason: reason || ins.filter(i => v(i) === null && !metrics[i]).map(i => LABELS[i] + '未找到').join('；') } : {}) }; };
  const mv = (k: string) => (metrics[k]?.value as string | null) ?? v(k);
  amount('debt', 'DEBT', ['debt'], v('debt'));
  amount('net_debt', 'DEBT − LIQ', ['debt', 'liquidity'], v('debt') !== null && v('liquidity') !== null ? canon(sub(dec(v('debt')!), dec(v('liquidity')!))) : null);
  amount('ebitda', 'EBIT + DA', ['ebit', 'da'], v('ebit') !== null && v('da') !== null ? canon(add(dec(v('ebit')!), dec(v('da')!))) : null);
  const ebitda = mv('ebitda');
  const ebitdaPositive = ebitda !== null && num(ebitda) > 0;
  const leverage = (key: string, numerator: string | null, formula: string, ins: string[]) => {
    if (!annual) return ratio(key, formula, ins, null, `流量只覆盖 ${months ?? '?'} 个月（10-Q 年初至今），不计算杠杆倍数`);
    if (numerator === null || ebitda === null) return ratio(key, formula, ins, null, [numerator === null ? '分子未找到' : '', ebitda === null ? 'EBITDA 未找到' : ''].filter(Boolean).join('；'));
    if (!ebitdaPositive) return ratio(key, formula, ins, null, 'EBITDA ≤ 0，倍数没有意义');
    ratio(key, formula, ins, num(numerator) / num(ebitda));
  };
  leverage('debt_to_ebitda', mv('debt'), 'DEBT ÷ EBITDA', ['debt', 'ebitda']);
  leverage('net_debt_to_ebitda', mv('net_debt'), '(DEBT − LIQ) ÷ EBITDA', ['net_debt', 'ebitda']);
  const intv = v('interest');
  if (v('ebit') === null || intv === null) ratio('interest_coverage', 'EBIT ÷ INT', ['ebit', 'interest'], null);
  else if (num(intv) <= 0) ratio('interest_coverage', 'EBIT ÷ INT', ['ebit', 'interest'], null, '利息费用 ≤ 0');
  else ratio('interest_coverage', 'EBIT ÷ INT', ['ebit', 'interest'], num(v('ebit')!) / num(intv));
  const ac = v('assets_current'), lc = v('liabilities_current');
  if (ac === null || lc === null) ratio('current_ratio', 'AssetsCurrent ÷ LiabilitiesCurrent', ['assets_current', 'liabilities_current'], null, '资产负债表不分流动与非流动（或标签缺失）');
  else ratio('current_ratio', 'AssetsCurrent ÷ LiabilitiesCurrent', ['assets_current', 'liabilities_current'], num(lc) === 0 ? null : num(ac) / num(lc), num(lc) === 0 ? '流动负债为 0' : undefined);
  amount('fcf', 'CFO − CAPEX', ['cfo', 'capex'], v('cfo') !== null && v('capex') !== null ? canon(sub(dec(v('cfo')!), dec(v('capex')!))) : null);
  const fcf = mv('fcf'), debt = v('debt');
  if (!annual) ratio('fcf_to_debt', 'FCF ÷ DEBT', ['fcf', 'debt'], null, `流量只覆盖 ${months ?? '?'} 个月，不计算`);
  else if (fcf === null || debt === null) ratio('fcf_to_debt', 'FCF ÷ DEBT', ['fcf', 'debt'], null, [fcf === null ? 'FCF 未找到' : '', debt === null ? '债务未找到' : ''].filter(Boolean).join('；'));
  else if (num(debt) === 0) ratio('fcf_to_debt', 'FCF ÷ DEBT', ['fcf', 'debt'], null, '债务为 0');
  else ratio('fcf_to_debt', 'FCF ÷ DEBT', ['fcf', 'debt'], num(fcf) / num(debt));
  if (debt !== null) {
    const parts = [debt, v('operating_leases') ?? '0', debtUsesFinanceLeases ? '0' : (v('finance_leases') ?? '0')];
    amount('debt_with_leases', debtUsesFinanceLeases ? 'DEBT + OL（债务已含融资租赁）' : 'DEBT + OL + FL', ['debt', 'operating_leases', 'finance_leases'], sumOf(parts));
  } else amount('debt_with_leases', 'DEBT + OL + FL', ['debt', 'operating_leases', 'finance_leases'], null);

  // §7 cues (wording: where to look, never a verdict)
  if (!annual) cue('Q1', `这是 ${form}：流量是年初至今 ${months ?? '?'} 个月，未年化；杠杆倍数与 FCF／债务不计算。`, []);
  if (ebitda !== null && !ebitdaPositive) cue('Q3', 'EBITDA ≤ 0：杠杆倍数没有意义，看现金消耗与流动性。', [...(inputs.ebit.terms.map(t => t.fact)), ...inputs.da.terms.map(t => t.fact)]);
  if (intv === null) {
    const net = ['InterestIncomeExpenseNonoperatingNet', 'InterestIncomeExpenseNet', 'InterestRevenueExpenseNet'].map(l => pick(l, 'P')).find((x): x is Pick => x !== null);
    cue('Q4', net ? `没有利息费用（总额）标签，只有净利息（${net.chosen.local}）：利息覆盖不计算，到附注里找利息费用。` : '没有利息费用标签：利息覆盖不计算，到附注里找利息费用。', net ? [net.chosen.i] : []);
  } else if (v('ebit') !== null && cmp(dec(v('ebit')!), dec(intv)) < 0) cue('Q5', 'EBIT 小于利息费用：营业利润不足以覆盖本期利息，看利率、再融资与契约条款的附注。', [...inputs.ebit.terms, ...inputs.interest.terms].map(t => t.fact));
  if (debt !== null && v('liquidity') !== null && cmp(dec(v('liquidity')!), dec(debt)) >= 0) cue('Q6', '现金与短期投资不少于总债务（净现金）。', [...inputs.liquidity.terms, ...inputs.debt.terms].map(t => t.fact));
  if (metrics.current_ratio.value !== null && (metrics.current_ratio.value as number) < 1) cue('Q7', '流动比率低于 1：流动负债大于流动资产，看营运资本构成与可用授信额度。', [...inputs.assets_current.terms, ...inputs.liabilities_current.terms].map(t => t.fact));
  if (fcf !== null && num(fcf) < 0) cue('Q8', '本期自由现金流为负。', [...inputs.cfo.terms, ...inputs.capex.terms].map(t => t.fact));
  const due = inputs.current_debt.value !== null ? { value: inputs.current_debt.value, terms: inputs.current_debt.terms, what: '流动负债里的债务' } : next12.value !== null ? { value: next12.value, terms: next12.terms, what: next12Label + '到期的本金' } : null;
  if (due && v('liquidity') !== null && cmp(dec(due.value), dec(v('liquidity')!)) > 0) cue('Q9', `${due.what}超过现金与短期投资：看流动性来源（循环信贷额度、商业票据备用额度）与再融资安排。`, [...due.terms, ...inputs.liquidity.terms].map(t => t.fact));
  if (inconsistent.size) cue('Q13', `同一概念同一期间有不同数值：${[...inconsistent].join('、')}；取 decimals 最高的一个。`, []);
  if (ladder && debt !== null) {
    const total = sumOf([next12.value, years.value, after5.value].filter((x): x is string => x !== null));
    const diff = Math.abs(num(total) - num(debt)) / Math.max(Math.abs(num(debt)), 1);
    if (diff > 0.10) cue('Q15', `到期表三段合计（本金）与资产负债表债务（账面值）相差 ${(diff * 100).toFixed(0)}%：到期表可能只覆盖部分债务，或有未摊销折价与发行费用。`, [...next12.terms, ...years.terms, ...after5.terms].map(t => t.fact));
  }
  if (has('PaymentsToDevelopSoftware', 'P')) cue('Q16', '有资本化软件支出（PaymentsToDevelopSoftware），未从自由现金流中扣除。', [pick('PaymentsToDevelopSoftware', 'P')!.chosen.i]);
  if (has('RealEstateInvestmentPropertyNet', 'I')) cue('Q17', '这是房地产类公司：实务中常用 FFO、NOI 等口径，本页不计算。', [pick('RealEstateInvestmentPropertyNet', 'I')!.chosen.i]);
  if (meta.amended) cue('Q18', '这是修订文件（/A），可能只含部分内容。', []);
  const unread = facts.filter(f => f.value === null && !f.nil && !isDim(contexts[f.ctx]) && currencyOf(f) === currency);
  if (unread.length) cue('Q19', `有 ${unread.length} 个合并口径的金额标签格式本页不解析，未参与计算。`, unread.slice(0, 20).map(f => f.i));
  const order = (c: Cue) => Number(c.id.slice(1));
  cues.sort((a, b) => order(a) - order(b));

  const scales = new Map<number, number>();
  for (const i of Object.values(inputs)) for (const t of i.terms) scales.set(t.scale, (scales.get(t.scale) || 0) + 1);
  meta.display_scale = [...scales.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ?? 0;
  return {
    definitions: DEFINITIONS_VERSION, status: 'ok', template: { kind: 'general', reason: '', facts: [] }, meta, inputs, metrics, cues, ladder,
    balance_sheet: { tables: [...bsTables].sort((a, b) => a - b), found: bsTables.size > 0 },
  };
}

/* Credit snapshot computation (docs/47-credit-snapshot.md, definitions credit-snapshot-def-1).
   Pure data in, pure data out: facts from ixbrl.ts, no DOM. Amounts are exact decimals; ratios
   are computed from the exact amounts. Every input keeps the facts it came from, so each number
   can be traced to its tagged cell. This is a credit view, not a rating. */
import { add, cmp, parse as parseDec, quantize, sub, type Dec } from '../decimal';
import { canon, type Context, type Fact, type Parsed } from './ixbrl';

export const DEFINITIONS_VERSION = 'credit-snapshot-def-3';
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
  balance_sheet: { found: boolean; assets?: number | null; end?: number | null; liabilities_current?: number | null; assets_current?: number | null; start?: number | null };
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
// Company-specific (extension) concepts counted as debt when they sit on the liabilities side and their local name says so.
const EXT_DEBT = /^(SeniorNotes|SeniorSecuredNotes|SeniorUnsecuredNotes|ConvertibleNotes|ConvertibleSeniorNotes|SecuredNotes|UnsecuredNotes|Notes|NotesPayable|LongTermDebt|Debt|TermLoan|Borrowings|CreditFacility|RevolvingCreditFacility|LineOfCredit|Mortgage)/;
const EXT_NOT_DEBT = /Interest|Accrued|Receivable|Issuance|Discount|Premium|FairValue|Cost|Guarant|Held|Asset|Securities/;
// def-2: a name that also mentions leases ("LongTermDebtAndLeaseObligationNet") is still debt; it is flagged like the
// us-gaap debt-and-lease concepts (Q10).
const LEASE_IN_NAME = /Lease/;
// "…NetOfUnamortizedDiscount", "…NetDeferredFinancingCostsNet" describe how a debt line is measured, not what it is.
const NET_OF = /Net(Of)?(Unamortized|Deferred|Debt|Financing|Issuance|Original|Issue)*(Costs?|Discounts?|Premiums?|Fees)(Net)?/g;
const extIsDebt = (local: string) => EXT_DEBT.test(local) && !EXT_NOT_DEBT.test(local.replace(NET_OF, ''));
// def-3: a us-gaap concept on the liabilities side whose standard name says borrowing is debt, listed or not
// (Oracle's LongTermNotesAndLoans was missing from the list).
const BORROWING_WORD = /Notes|Loan|Debt|Borrowing|CommercialPaper|LinesOfCredit|LineOfCredit|Debenture|Bond/;
const usIsDebt = (local: string) => BORROWING_WORD.test(local) && !EXT_NOT_DEBT.test(local.replace(NET_OF, '')) && !/^(Operating|Finance|Capital)Lease/.test(local)
  && !/Covenant|Capacity|Maximum|Minimum|Remaining|Percentage|Rate|Weighted/.test(local);
// Axes that split a whole company into parts (segments, legal entities); instrument-level axes do not.
const PART_AXIS = /Segment|LegalEntity|ConsolidationItems|ConsolidatedEntities|Subsidiar/;
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

  // P (def-2): the consolidated duration ending at D (≤ 400 days) that carries the most report-currency amounts;
  // ties go to the longer one. One stray trailing-twelve-month fact in a 10-Q no longer becomes the period.
  let P: { start: string; end: string; days: number } | null = null;
  const perPeriod = new Map<string, number>();
  for (const f of facts) {
    if (!money(f)) continue;
    const c = contexts[f.ctx];
    if (!c.start || c.end !== D || days(c.start, c.end) > 400) continue;
    perPeriod.set(c.start, (perPeriod.get(c.start) || 0) + 1);
  }
  for (const [start, count] of perPeriod) {
    const n = days(start, D);
    const best = P ? perPeriod.get(P.start)! : -1;
    if (count > best || (count === best && n > P!.days)) P = { start, end: D, days: n };
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
      chosen = list.slice().sort((a, b) => decRank(b.decimals) - decRank(a.decimals) || a.i - b.i)[0];
      // Duplicates that agree once rounded to the least precise one's decimals are consistent (XBRL); only real
      // differences become cue Q13.
      const coarsest = Math.min(...list.map(f => decRank(f.decimals)));
      const rounded = new Set(list.map(f => Number.isFinite(coarsest) && Math.abs(coarsest) < 1e8 ? canon(quantize(dec(f.value!), -coarsest)) : f.value!));
      if (rounded.size > 1) inconsistent.add(local);
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
    balance_sheet: { found: false },
  });

  // §3 template
  if (!SUPPORTED_FORMS.has(form)) return empty('unsupported_form', `文件类型是 ${form || '未知'}；本页支持 10-K、10-Q 及其修订版。`, []);
  if (!usgaap.length) return empty('not_us_gaap', '文件里没有 us-gaap 概念：本页只支持按美国公认会计准则（US GAAP）编制的报表。', []);
  const trigger = (rules: [string, 'I' | 'P', boolean?][]) => {
    const out: number[] = [];
    for (const [l, w, positive] of rules) { const p = pick(l, w); if (p && (!positive || cmp(dec(p.value), { int: 0n, exp: 0 }) > 0)) out.push(p.chosen.i); }
    return out;
  };
  const assetsValue = pick('Assets', 'I');
  const deposits = pick('Deposits', 'I');
  const bank = trigger([['InterestAndDividendIncomeOperating', 'P']]);
  if (deposits && assetsValue && num(deposits.value) >= 0.2 * num(assetsValue.value)) bank.unshift(deposits.chosen.i);
  if (bank.length) return empty('bank', '这份文件按银行的报表模板编制（有存款负债或利息收入经营科目）。杠杆、EBITDA 与自由现金流不是衡量银行信用的口径，本页不支持银行模板。', bank);
  const insurer = trigger([['PremiumsEarnedNet', 'P'], ['PolicyholderBenefitsAndClaimsIncurredNet', 'P'], ['LiabilityForFuturePolicyBenefits', 'I'], ['LiabilityForUnpaidClaimsAndClaimsAdjustmentExpense', 'I']]);
  if (insurer.length) return empty('insurer', '这份文件按保险公司的报表模板编制（有已赚保费或保单负债）。本页不支持保险模板。', insurer);
  const fund = trigger([['NetAssetValuePerShare', 'I'], ['InvestmentOwnedAtFairValue', 'I']]);
  if (fund.length) return empty('investment_company', '这份文件按投资公司／BDC 的模板编制（有每股净资产或按公允价值计量的投资组合）。本页不支持这一模板。', fund);

  const inputs: Record<string, Input> = {};
  // §4: the balance sheet as a stretch of the document, bounded by its own total rows (works for table and
  // absolutely positioned layouts alike). Positions are fact indices in document order.
  const atI = facts.filter(f => money(f) && inI(f));
  const usAt = (local: string, after = -1, before = Infinity) => atI.find(f => nsClass(f.ns) === 'us-gaap' && f.local === local && f.i > after && f.i < before)?.i;
  const posA = usAt('Assets');
  const posE = posA === undefined ? undefined : (usAt('LiabilitiesAndStockholdersEquity', posA) ?? usAt('StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', posA) ?? usAt('StockholdersEquity', posA) ?? usAt('Liabilities', posA));
  const found = posA !== undefined && posE !== undefined;
  const posLC = found ? usAt('LiabilitiesCurrent', posA!, posE!) : undefined;
  const lastBefore = (local: string, before: number) => [...atI].reverse().find(f => nsClass(f.ns) === 'us-gaap' && f.local === local && f.i < before)?.i;
  const posAC = found ? lastBefore('AssetsCurrent', posA!) : undefined;
  // The current-asset stretch starts after the nearest fact before it that has a duration context (the previous statement).
  const stretchStart = (from: number) => { for (let k = from - 1; k >= 0; k--) { const c = contexts[facts[k].ctx]; if (c && c.start) return k; } return -1; };
  const posS = found ? stretchStart(posAC ?? posA!) : undefined;
  const liabSide = (f: Fact) => found && f.i > posA! && f.i < posE!;
  const assetSide = (f: Fact) => found && f.i > posS! && f.i < (posAC ?? posA!);
  const inBs = atI.filter(f => liabSide(f) || assetSide(f));
  const firstByConcept = (list: Fact[]) => { const seen = new Set<string>(); return list.filter(f => !seen.has(f.local) && seen.add(f.local)); };
  const firstByKey = (list: Fact[]) => { const seen = new Set<string>(); return list.filter(f => { const k = f.name + '|' + JSON.stringify(contexts[f.ctx]?.dims); return !seen.has(k) && !!seen.add(k); }); };

  // §4 debt rows on the balance sheet
  interface Row { f: Fact; section: 'current' | 'noncurrent' }
  const isDebtRow = (f: Fact) => (nsClass(f.ns) === 'us-gaap' && (DEBT_SET.has(f.local) || usIsDebt(f.local))) || (nsClass(f.ns) === 'ext' && extIsDebt(f.local));
  const nameIsCurrent = (local: string) => /Current$/.test(local) || SHORT_TERM.has(local);
  const debtRows: Row[] = firstByConcept(inBs.filter(f => isDebtRow(f) && liabSide(f)))
    .map(f => ({ f, section: posLC !== undefined ? (f.i < posLC ? 'current' : 'noncurrent') : (nameIsCurrent(f.local) ? 'current' : 'noncurrent') }));
  const subtotalNotes: number[] = [];
  function collapse(rows: Row[], wholeSide: boolean): { counted: Row[]; absorbed: Map<number, Row[]> } {
    const absorbedBy = new Map<number, Row[]>();
    const gone = new Set<number>();
    for (let k = 0; k < rows.length; k++) {
      const s = rows[k];
      const isSub = SECTION_SUBTOTALS.has(s.f.local) || SIDE_SUBTOTALS.has(s.f.local);
      if (!isSub || cmp(dec(s.f.value!), { int: 0n, exp: 0 }) === 0) continue;
      const region = (r: Row) => wholeSide || SIDE_SUBTOTALS.has(s.f.local) || r.section === s.section;
      let acc: Dec = { int: 0n, exp: 0 };
      const taken: Row[] = [];
      let found = false;
      for (let j = k - 1; j >= 0; j--) {
        const r = rows[j];
        if (gone.has(r.f.i) || !region(r) || cmp(dec(r.f.value!), { int: 0n, exp: 0 }) === 0) continue;
        acc = add(acc, dec(r.f.value!)); taken.push(r);
        if (cmp(acc, dec(s.f.value!)) === 0) { found = true; break; }
      }
      // def-2: one row from another section that happens to equal a whole-side subtotal is not its breakdown.
      if (found && taken.length === 1 && taken[0].section !== s.section) found = false;
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
    // Absorbed rows count too: a debt-and-lease line inside a subtotal still puts finance leases in the total.
    debtUsesFinanceLeases = debtRows.some(r => WITH_FINANCE_LEASES.has(r.f.local) || (nsClass(r.f.ns) === 'ext' && LEASE_IN_NAME.test(r.f.local)));
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
    inputs.debt = terms.length ? input('debt', rule, terms) : input('debt', '', [], found ? '资产负债表上没有可识别的债务行，附注里也没有债务合计标签' : '没有找到资产负债表，也没有债务合计标签');
    inputs.current_debt = { key: 'current_debt', label: LABELS.current_debt, value: null, rule: '', terms: [], missing: 'F 规则下不计算流动债务' };
    if (terms.length) cue('Q12', `资产负债表上没有可识别的债务行，债务取自附注或其他位置的合计标签（${rule.split(' · ')[0]}）。核对资产负债表上的借款行。`, terms.map(t => t.fact));
  }
  // def-3: debt that exists only with dimensions (by segment, by legal entity) is shown as parts, never hinted to be zero.
  const dimDebt = inputs.debt.value === null ? facts.filter(f => f.value !== null && num(f.value) !== 0 && isDim(contexts[f.ctx]) && contexts[f.ctx]?.instant === D && currencyOf(f) === currency
    && contexts[f.ctx].dims.every(([axis]) => PART_AXIS.test(axis))
    && ((nsClass(f.ns) === 'us-gaap' && DEBT_SET.has(f.local)) || (nsClass(f.ns) === 'ext' && extIsDebt(f.local)))) : [];
  if (dimDebt.length) {
    const shown = firstByKey(dimDebt).slice(0, 8);
    inputs.debt = { ...inputs.debt, missing: '只有按分部或其他维度标注的债务，没有合并口径的合计（见 Q24）' };
    cue('Q24', `债务只按分部或其他维度标注（${dimDebt.length} 个事实，如 ${shown[0].name}），没有合并口径的合计：本页不把各部分相加，也不当作没有借款。点下面的数字查看各部分。`, shown.map(f => f.i));
  } else if (inputs.debt.value === null) cue('Q12', '没有找到债务：资产负债表上没有可识别的债务行，也没有债务合计标签，也没有按分部标注的债务。若公司确实没有借款，债务为 0；请在资产负债表上核对。', []);
  const strange = firstByConcept(inBs.filter(f => liabSide(f) && !isDebtRow(f)
    && /Debt|Borrowing|Notes|Loan|CommercialPaper|CreditFacility|LineOfCredit|Mortgage/i.test(f.local) && !/Interest|Receivable|Asset|Securities|^(Operating|Finance|Capital)Lease/i.test(f.local)));
  if (strange.length) cue('Q12', `负债一侧有名称像借款、但未计入债务的科目：${strange.map(f => f.name).join('、')}。核对它们是否是借款。`, strange.map(f => f.i));
  // def-3 guard: reasons to think the stated debt is incomplete. With any of them, net debt and every debt-based
  // multiple are withheld (Q23) and "net cash" (Q6) is never stated.
  const incomplete: string[] = [];
  const counted = inputs.debt.value === null ? 0 : num(inputs.debt.value);
  if (strange.length) {
    const uncounted = strange.reduce((n, f) => n + Math.abs(num(f.value!)), 0);
    if (counted === 0 || uncounted >= 0.1 * counted) incomplete.push(`负债一侧未计入的借款类科目合计约为已计入债务的 ${counted ? Math.round(uncounted / counted * 100) + '%' : '全部'}`);
  }
  // Q21 (def-2): a debt total tagged elsewhere (notes) that is well above the balance-sheet debt.
  if (inputs.debt.value !== null && inputs.debt.rule.startsWith('B')) {
    const totals = ['LongTermDebt', 'DebtInstrumentCarryingAmount', 'DebtLongtermAndShorttermCombinedAmount', 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities']
      .map(l => pick(l, 'I')).filter((x): x is Pick => x !== null).sort((a, b) => num(b.value) - num(a.value));
    if (totals.length && num(totals[0].value) > 1.1 * num(inputs.debt.value)) {
      const top = totals[0];
      incomplete.push(`${top.chosen.local} 比资产负债表债务多 ${Math.round((num(top.value) / Math.max(num(inputs.debt.value), 1) - 1) * 100)}%`);
      cue('Q21', `附注或其他位置的债务合计（${top.chosen.name}）比资产负债表上计入的债务多 ${Math.round((num(top.value) / Math.max(num(inputs.debt.value), 1) - 1) * 100)}%：资产负债表上可能有未识别的借款行，依赖债务的倍数可能偏低。`, [top.chosen.i]);
    }
  }
  const extCounted = inputs.debt.terms.filter(t => !t.concept.startsWith('us-gaap:'));
  if (extCounted.length && inputs.debt.rule.startsWith('B')) cue('Q12', `债务含发行人自定义概念（按名称规则计入）：${extCounted.map(t => t.concept).join('、')}。`, extCounted.map(t => t.fact));
  if (debtUsesFinanceLeases) cue('Q10', '债务行包含租赁（LongTermDebtAndCapitalLeaseObligations 或名称含 Lease 的债务科目）：债务已含融资租赁负债。', debtRows.filter(r => WITH_FINANCE_LEASES.has(r.f.local) || LEASE_IN_NAME.test(r.f.local)).map(r => r.f.i));

  // Liquidity
  let liqRows = firstByConcept(inBs.filter(f => nsClass(f.ns) === 'us-gaap' && LIQ_SET.has(f.local) && assetSide(f)
    && (posAC !== undefined || CASH.includes(f.local) || nameIsCurrent(f.local))));
  // def-2: cash and cash equivalents is the total of Cash and CashEquivalents; with it present, its parts and the
  // restricted-cash total are not added again.
  if (liqRows.some(f => f.local === 'CashAndCashEquivalentsAtCarryingValue')) liqRows = liqRows.filter(f => !['CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents', 'Cash', 'CashEquivalentsAtCarryingValue'].includes(f.local));
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
  inputs.interest = firstOf('interest', ['InterestExpense', 'InterestExpenseNonoperating', 'InterestAndDebtExpense', 'InterestExpenseDebt', 'InterestCostsIncurred'], 'P');
  let interestIsNet = false;
  if (inputs.interest.value === null) {
    for (const l of ['InterestIncomeExpenseNonoperatingNet', 'InterestIncomeExpenseNet']) {
      const p = pick(l, 'P');
      if (p && num(p.value) < 0) { inputs.interest = input('interest', `${l}（净额，取负）`, [term(p.chosen, '−')]); interestIsNet = true; break; }
      if (p) break;
    }
  }
  if (inputs.interest.rule === 'InterestCostsIncurred') cue('Q20', '利息费用取自“利息成本发生额”（InterestCostsIncurred，含资本化利息），不是利润表上的利息费用。', inputs.interest.terms.map(t => t.fact));
  if (interestIsNet) cue('Q20', `没有利息费用总额标签：利息费用取自利息收支净额（${inputs.interest.terms[0].concept}），已扣除利息收入。`, inputs.interest.terms.map(t => t.fact));
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
  inputs.capex = firstOf('capex', ['PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets', 'PaymentsToAcquireOtherProductiveAssets', 'PaymentsForCapitalImprovements', 'PaymentsToAcquireOilAndGasPropertyAndEquipment', 'PaymentsToAcquireOilAndGasProperty', 'PaymentsToAcquireOtherPropertyPlantAndEquipment'], 'P');
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
  const leaseFormula = debtUsesFinanceLeases ? 'DEBT + OL（债务已含融资租赁）' : 'DEBT + OL + FL';
  if (debt === null) amount('debt_with_leases', leaseFormula, ['debt', 'operating_leases', 'finance_leases'], null);
  else if (v('operating_leases') === null && (debtUsesFinanceLeases || v('finance_leases') === null)) amount('debt_with_leases', leaseFormula, ['debt', 'operating_leases', 'finance_leases'], null, '租赁负债未找到');
  else amount('debt_with_leases', leaseFormula, ['debt', 'operating_leases', 'finance_leases'], sumOf([debt, v('operating_leases') ?? '0', debtUsesFinanceLeases ? '0' : (v('finance_leases') ?? '0')]));

  if (ladder && debt !== null) {
    const ladderTotal = [next12.value, years.value, after5.value].filter((x): x is string => x !== null).reduce((n, x) => n + num(x), 0);
    if (ladderTotal > 1.25 * Math.max(num(debt), 1)) incomplete.push(`到期表本金合计比资产负债表债务多 ${Math.round((ladderTotal / Math.max(num(debt), 1) - 1) * 100)}%`);
  }
  if (incomplete.length && debt !== null) {
    for (const k of ['net_debt', 'debt_to_ebitda', 'net_debt_to_ebitda', 'fcf_to_debt', 'debt_with_leases']) {
      metrics[k] = { ...metrics[k], value: null, reason: '债务可能不完整（见 Q23），不计算' };
    }
    metrics.debt = { ...metrics.debt, reason: '可能不完整，只是已识别的部分（见 Q23）' };
    cue('Q23', `债务可能不完整：${incomplete.join('；')}。净债务与依赖债务的倍数不计算，也不判断是否净现金；到资产负债表与债务附注核对。`, []);
  }
  // def-3: for a real-estate company, property acquisitions are not in the capex tags, so FCF overstates free cash.
  if (has('RealEstateInvestmentPropertyNet', 'I')) {
    if (metrics.fcf.value !== null) metrics.fcf = { ...metrics.fcf, reason: '房地产公司：物业收购不在资本开支标签里，这个 FCF 偏高' };
    metrics.fcf_to_debt = { ...metrics.fcf_to_debt, value: null, reason: '房地产公司：物业收购不在资本开支标签里，不计算' };
  }

  // §7 cues (wording: where to look, never a verdict)
  if (!annual) cue('Q1', `这是 ${form}：流量是年初至今 ${months ?? '?'} 个月，未年化；杠杆倍数与 FCF／债务不计算。`, []);
  if (ebitda !== null && !ebitdaPositive) cue('Q3', 'EBITDA ≤ 0：杠杆倍数没有意义，看现金消耗与流动性。', [...(inputs.ebit.terms.map(t => t.fact)), ...inputs.da.terms.map(t => t.fact)]);
  if (intv === null) {
    const net = ['InterestIncomeExpenseNonoperatingNet', 'InterestIncomeExpenseNet'].map(l => pick(l, 'P')).find((x): x is Pick => x !== null);
    cue('Q4', net ? `没有利息费用标签，利息收支净额（${net.chosen.local}）为净收入：利息覆盖不计算，到附注里找利息费用。` : '没有利息费用标签：利息覆盖不计算，到附注里找利息费用。', net ? [net.chosen.i] : []);
  } else if (v('ebit') !== null && cmp(dec(v('ebit')!), dec(intv)) < 0) cue('Q5', 'EBIT 小于利息费用：营业利润不足以覆盖本期利息，看利率、再融资与契约条款的附注。', [...inputs.ebit.terms, ...inputs.interest.terms].map(t => t.fact));
  if (debt !== null && !incomplete.length && v('liquidity') !== null && cmp(dec(v('liquidity')!), dec(debt)) >= 0) cue('Q6', '现金与短期投资不少于总债务（净现金）。', [...inputs.liquidity.terms, ...inputs.debt.terms].map(t => t.fact));
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
  const unread = facts.filter(f => f.value === null && !f.nil && f.text !== '' && !isDim(contexts[f.ctx]) && currencyOf(f) === currency);
  if (unread.length) cue('Q19', `有 ${unread.length} 个合并口径的金额标签格式本页不解析，未参与计算。`, unread.slice(0, 20).map(f => f.i));
  const impairments = ['GoodwillImpairmentLoss', 'AssetImpairmentCharges', 'ImpairmentOfLongLivedAssetsHeldForUse', 'ImpairmentOfIntangibleAssetsExcludingGoodwill', 'GoodwillAndIntangibleAssetImpairment']
    .map(l => pick(l, 'P')).filter((x): x is Pick => x !== null && num(x.value) > 0);
  if (impairments.length && inputs.ebit.value !== null) cue('Q22', `本期有减值（${impairments.map(p => p.chosen.local).join('、')}），通常计入营业利润：EBIT、EBITDA 与覆盖倍数含这些一次性项目。`, impairments.map(p => p.chosen.i));
  const order = (c: Cue) => Number(c.id.slice(1));
  cues.sort((a, b) => order(a) - order(b));

  const scales = new Map<number, number>();
  for (const i of Object.values(inputs)) for (const t of i.terms) scales.set(t.scale, (scales.get(t.scale) || 0) + 1);
  meta.display_scale = [...scales.entries()].sort((a, b) => b[1] - a[1] || b[0] - a[0])[0]?.[0] ?? 0;
  return {
    definitions: DEFINITIONS_VERSION, status: 'ok', template: { kind: 'general', reason: '', facts: [] }, meta, inputs, metrics, cues, ladder,
    balance_sheet: { found, assets: posA ?? null, end: posE ?? null, liabilities_current: posLC ?? null, assets_current: posAC ?? null, start: posS ?? null },
  };
}

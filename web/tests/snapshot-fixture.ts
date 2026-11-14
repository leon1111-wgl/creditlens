/* SYNTHETIC Inline XBRL filings for the credit snapshot tests (fictional issuer, made-up numbers).
   `hostile` adds markup that must never run or load in the sandboxed view. The committed copy
   tests/fixtures/snapshot-hostile.htm (used by the Actions visitor flow) must equal hostileFiling(). */

export interface FixtureOptions {
  form?: string; bank?: boolean; hostile?: boolean; noBalanceSheet?: boolean; ytdMonths?: number; extraBalanceRows?: string; divLayout?: boolean; netInterest?: boolean;
}

const NS = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12" xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:srt="http://fasb.org/srt/2025" xmlns:lsi="http://leon-synthetic.example/20251231"';

const nf = (name: string, ctx: string, text: string, extra = '') =>
  `<ix:nonFraction unitRef="usd" contextRef="${ctx}" decimals="-6" name="${name}" format="ixt:num-dot-decimal" scale="6" ${extra}>${text}</ix:nonFraction>`;

export function syntheticFiling(o: FixtureOptions = {}): string {
  const form = o.form ?? '10-K';
  const start = o.ytdMonths ? '2026-01-01' : '2025-01-01';
  const end = o.ytdMonths ? `2026-0${o.ytdMonths}-30` : '2025-12-31';
  const prior = o.ytdMonths ? '2025-12-31' : '2024-12-31';
  const row = (label: string, name: string, now: string, before: string, extra = '') =>
    `<tr><td>${label}</td><td>$</td><td style="text-align:right">${nf(name, 'c-i', now, extra)}</td><td style="text-align:right">${nf(name, 'c-p', before, extra)}</td></tr>`;
  const bank = o.bank ? row('Deposits', 'us-gaap:Deposits', '9,000', '8,500') : '';
  const balanceHtml = o.noBalanceSheet ? '' : `
<p style="page-break-before:always"><b>LEON SYNTHETIC INDUSTRIES INC. — CONSOLIDATED BALANCE SHEETS (SYNTHETIC)</b> (In millions)</p>
<table>
<tr><td>Cash and cash equivalents</td><td>$</td><td>${nf('us-gaap:CashAndCashEquivalentsAtCarryingValue', 'c-i', '1,200')}</td><td>${nf('us-gaap:CashAndCashEquivalentsAtCarryingValue', 'c-p', '1,000')}</td></tr>
${row('Short-term investments', 'us-gaap:ShortTermInvestments', '300', '250')}
${row('Total cash, cash equivalents and short-term investments', 'us-gaap:CashCashEquivalentsAndShortTermInvestments', '1,500', '1,250')}
${row('Accounts receivable', 'us-gaap:AccountsReceivableNetCurrent', '800', '700')}
${row('Total current assets', 'us-gaap:AssetsCurrent', '2,600', '2,200')}
${row('Property and equipment, net', 'us-gaap:PropertyPlantAndEquipmentNet', '4,000', '3,800')}
${row('Total assets', 'us-gaap:Assets', '6,600', '6,000')}
${row('Accounts payable', 'us-gaap:AccountsPayableCurrent', '500', '480')}
${bank}
${row('Commercial paper', 'us-gaap:CommercialPaper', '100', '90')}
${row('Current portion of long-term debt', 'us-gaap:LongTermDebtCurrent', '150', '140')}
${row('Total short-term debt', 'us-gaap:DebtCurrent', '250', '230')}
${row('Operating lease liabilities, current', 'us-gaap:OperatingLeaseLiabilityCurrent', '40', '35')}
${row('Total current liabilities', 'us-gaap:LiabilitiesCurrent', '790', '745')}
${row('Long-term debt', 'us-gaap:LongTermDebtNoncurrent', '2,000', '1,900')}
${row('Operating lease liabilities', 'us-gaap:OperatingLeaseLiabilityNoncurrent', '160', '150')}
${row('Finance lease liabilities', 'us-gaap:FinanceLeaseLiabilityNoncurrent', '50', '45')}
${row('Borrowings from affiliates', 'lsi:OtherBorrowingsFromAffiliates', '75', '0')}
${o.extraBalanceRows ?? ''}
${row('Total liabilities', 'us-gaap:Liabilities', '3,075', '2,840')}
<tr><td>Accumulated other comprehensive loss</td><td></td><td>(${nf('us-gaap:AccumulatedOtherComprehensiveIncomeLossNetOfTax', 'c-i', '25', 'sign="-"')})</td><td>(${nf('us-gaap:AccumulatedOtherComprehensiveIncomeLossNetOfTax', 'c-p', '20', 'sign="-"')})</td></tr>
${row('Total shareholders’ equity', 'us-gaap:StockholdersEquity', '3,525', '3,160')}
${row('Total liabilities and shareholders’ equity', 'us-gaap:LiabilitiesAndStockholdersEquity', '6,600', '6,000')}
</table>`;
  // Some filing agents lay statements out with absolutely positioned <div>s and no <table> at all.
  const balance = o.divLayout ? balanceHtml.replace(/<table>/g, '<div class="page">').replace(/<\/table>/g, '</div>').replace(/<tr>/g, '<div>').replace(/<\/tr>/g, '</div>').replace(/<td[^>]*>/g, '<span>').replace(/<\/td>/g, '</span>') : balanceHtml;
  const hostile = o.hostile ? `
<script>window.__pwned = 1; try { parent.__pwned = 1 } catch (e) {}</script>
<img src="https://example.invalid/pixel.png" onerror="parent.__pwned = 1" alt="">
<a href="javascript:parent.__pwned=1">javascript link</a> <a href="https://example.invalid/away">external link</a> <a href="#notes">in-document link</a>
<iframe src="https://example.invalid/frame"></iframe><object data="https://example.invalid/x.swf"></object><embed src="https://example.invalid/y">
<div style="background:url(https://example.invalid/bg.png)" onclick="parent.__pwned = 1">styled block</div>
<svg width="10" height="10"><script>parent.__pwned = 1</script><a xlink:href="javascript:parent.__pwned=1"><text x="0" y="9">s</text></a><set attributeName="href" to="javascript:parent.__pwned=1"></set></svg>
<form action="https://example.invalid/post"><input name="q"></form>
<!-- comment with <script>parent.__pwned=1</script> -->` : '';
  return `<?xml version="1.0" encoding="utf-8"?>
<html ${NS}><head><title>SYNTHETIC ${form}</title>${o.hostile ? '<meta http-equiv="refresh" content="0;url=https://example.invalid/"><base href="https://example.invalid/"><link rel="stylesheet" href="https://example.invalid/s.css">' : ''}<style>td { padding: 2px 6px }${o.hostile ? ' @import url(https://example.invalid/i.css);' : ''}</style></head>
<body${o.hostile ? ' onload="parent.__pwned = 1"' : ''}>
<div style="display:none"><ix:header><ix:hidden>
${nf('us-gaap:OperatingLeaseLiability', 'c-i', '200', 'id="hidden-ol"')}
<ix:nonNumeric name="dei:AmendmentFlag" contextRef="c-d">false</ix:nonNumeric>
</ix:hidden><ix:references><link:schemaRef xmlns:link="http://www.xbrl.org/2003/linkbase" xlink:type="simple" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="lsi-20251231.xsd"></link:schemaRef></ix:references>
<ix:resources>
<xbrli:context id="c-d"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009999999</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>${start}</xbrli:startDate><xbrli:endDate>${end}</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-i"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009999999</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>${end}</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="c-p"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009999999</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>${prior}</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="c-q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009999999</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>${end}</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="c-seg"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0009999999</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="srt:ProductOrServiceAxis">lsi:WidgetsMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>${start}</xbrli:startDate><xbrli:endDate>${end}</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></div>
<p>Form <ix:nonNumeric name="dei:DocumentType" contextRef="c-d">${form}</ix:nonNumeric> · <ix:nonNumeric name="dei:EntityRegistrantName" contextRef="c-d">Leon Synthetic Industries Inc.</ix:nonNumeric> (SYNTHETIC) · CIK <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c-d">0009999999</ix:nonNumeric> · <ix:nonNumeric name="dei:TradingSymbol" contextRef="c-d">LSYN</ix:nonNumeric></p>
${hostile}
<p style="page-break-before:always"><b>CONSOLIDATED STATEMENTS OF OPERATIONS (SYNTHETIC)</b></p>
<table>
<tr><td>Revenue</td><td>${nf('us-gaap:Revenues', 'c-d', '9,000')}</td></tr>
<tr><td>Widgets revenue</td><td>${nf('us-gaap:Revenues', 'c-seg', '4,000')}</td></tr>
${o.ytdMonths ? `<tr><td>Revenue, quarter</td><td>${nf('us-gaap:Revenues', 'c-q', '4,700')}</td></tr>` : ''}
<tr><td>Operating income</td><td>${nf('us-gaap:OperatingIncomeLoss', 'c-d', '1,100')}</td></tr>
<tr><td>Interest expense</td><td>(${o.netInterest ? nf('us-gaap:InterestIncomeExpenseNonoperatingNet', 'c-d', '120', 'sign="-"') : nf('us-gaap:InterestExpenseNonoperating', 'c-d', '120')})</td></tr>
<tr><td>Income before income taxes</td><td>${nf('us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest', 'c-d', '1,000')}</td></tr>
</table>
${balance}
<p style="page-break-before:always"><b>CONSOLIDATED STATEMENTS OF CASH FLOWS (SYNTHETIC)</b></p>
<table>
<tr><td>Depreciation and amortization</td><td>${nf('us-gaap:DepreciationDepletionAndAmortization', 'c-d', '450')}</td></tr>
<tr><td>Net cash provided by operating activities</td><td>${nf('us-gaap:NetCashProvidedByUsedInOperatingActivities', 'c-d', '1,400')}</td></tr>
<tr><td>Purchases of property and equipment</td><td>(${nf('us-gaap:PaymentsToAcquirePropertyPlantAndEquipment', 'c-d', '600')})</td></tr>
<tr><td>Capitalized software</td><td>(${nf('us-gaap:PaymentsToDevelopSoftware', 'c-d', '30')})</td></tr>
</table>
<p id="notes" style="page-break-before:always"><b>NOTE 7 — DEBT AND LEASES (SYNTHETIC)</b></p>
<table>
<tr><td>Total debt, carrying amount</td><td>${nf('us-gaap:LongTermDebt', 'c-i', '2,150')}</td></tr>
<tr><td>2026</td><td>${nf('us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths', 'c-i', '150')}</td></tr>
<tr><td>2027</td><td>${nf('us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo', 'c-i', '300')}</td></tr>
<tr><td>2028</td><td>${nf('us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree', 'c-i', '200')}</td></tr>
<tr><td>2030</td><td>${nf('us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive', 'c-i', '400')}</td></tr>
<tr><td>Thereafter</td><td>${nf('us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive', 'c-i', '1,100')}</td></tr>
<tr><td>Total operating lease liabilities</td><td><span style="-sec-ix-hidden:hidden-ol">200</span></td></tr>
<tr><td>Unused facility</td><td><ix:nonFraction unitRef="usd" contextRef="c-i" name="us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity" format="ixt:fixed-zero" scale="6" decimals="-6">—</ix:nonFraction></td></tr>
</table>
</body></html>
`;
}

export const hostileFiling = () => syntheticFiling({ hostile: true });

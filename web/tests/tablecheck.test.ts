import { describe, expect, it } from 'vitest';
import { analyse, buildTable, readAmount, tokenize, type OcrLine } from '../src/tablecheck/table';

// Constructed OCR lines (image pixels); no model runs in these tests.
let y = 0;
const H = 14;
function line(text: string, x0: number, x1: number, top = y): OcrLine { return { text, conf: 0.99, box: { x0, y0: top, x1, y1: top + H } }; }
/** One table row: a label at the left and right-aligned numbers ending at the given right edges. */
function row(label: string, cells: (string | null)[], rights = [430, 530, 630], indent = 20): OcrLine[] {
  y += 20;
  const out: OcrLine[] = [];
  if (label) out.push(line(label, indent, indent + label.length * 6));
  cells.forEach((c, i) => { if (c) out.push(line(c, rights[i] - c.length * 6, rights[i])); });
  return out;
}
function header(cells: string[], rights = [430, 530, 630]): OcrLine[] {
  y += 20;
  return cells.map((c, i) => line(c, rights[i] - c.length * 6, rights[i]));
}
function sheet(rows: OcrLine[][]): OcrLine[] { return rows.flat(); }
function reset() { y = 0; }

describe('amount grammar', () => {
  it('reads printed amounts strictly', () => {
    expect(readAmount('(1,234)')).toMatchObject({ kind: 'amount', negative: true, dp: 0 });
    expect(readAmount('$ (1,234)')).toMatchObject({ kind: 'amount', negative: true });
    expect(readAmount('$1,234.50')).toMatchObject({ kind: 'amount', negative: false, dp: 2 });
    expect(readAmount('12.5%')).toMatchObject({ kind: 'amount', percent: true, dp: 1 });
    expect(readAmount('(0.45)')).toMatchObject({ kind: 'amount', negative: true, dp: 2 });
    expect(readAmount('—').kind).toBe('dash');
    expect(readAmount('$ —').kind).toBe('dash');
    expect(readAmount('2025')).toMatchObject({ kind: 'amount', year: 2025 });
    expect(readAmount('2,025').year).toBeUndefined();
    expect(readAmount('1,2B4').kind).toBe('garbled');
    expect(readAmount('1,23').kind).toBe('garbled');
    expect(readAmount('(1,234').kind).toBe('garbled');
    expect(readAmount('Revenue').kind).toBe('text');
  });

  it('splits one OCR line into a label and trailing numbers', () => {
    const t = tokenize([{ text: 'Revenue $ 1,234 (567)', conf: 0.98, box: { x0: 0, y0: 0, x1: 210, y1: 12 } }]);
    expect(t.map((x) => [x.text, x.kind])).toEqual([['Revenue', 'text'], ['$ 1,234', 'amount'], ['(567)', 'amount']]);
    expect(t[1].box.x0).toBeGreaterThan(t[0].box.x1 - 1);
  });
});

describe('table rebuild and footing', () => {
  function balance(receivables = '500') {
    reset();
    return sheet([
      header(['2025', '2024']),
      row('Current assets:', []),
      row('Cash', ['1,000', '900'], undefined, 30),
      row('Receivables', [receivables, '400'], undefined, 30),
      row('Total current assets', ['1,500', '1,300'], undefined, 40),
      row('Property, net', ['2,000', '1,800']),
      row('Total assets', ['3,500', '3,100']),
      row('Liabilities:', []),
      row('Payables', ['1,000', '900'], undefined, 30),
      row('Debt', ['1,500', '1,400'], undefined, 30),
      row('Total liabilities', ['2,500', '2,300'], undefined, 40),
      row("Stockholders' equity", ['1,000', '800']),
      row('Total liabilities and', []),
      row("stockholders' equity", ['3,500', '3,100'], undefined, 30),
    ]);
  }

  it('finds rows, year columns and wrapped labels', () => {
    const t = buildTable(balance());
    expect(t.columns.map((c) => c.year)).toEqual([2025, 2024]);
    const last = t.rows[t.rows.length - 1];
    expect(last.fullLabel).toBe("Total liabilities and stockholders' equity");
    expect(t.rows[t.rows.length - 2].continuation).toBe(true);
  });

  it('foots a clean balance sheet, using subtotals for their blocks', () => {
    const { checks } = analyse(balance());
    const totals = checks.filter((c) => c.kind === 'total');
    expect(totals.length).toBe(8);
    expect(totals.every((c) => c.status === 'foots')).toBe(true);
    expect(totals.filter((c) => c.column === 0).map((c) => c.formula)).toEqual(
      ['1,000 + 500', '1,500 + 2,000', '1,000 + 1,500', '2,500 + 1,000']);
  });

  it('flags the line that does not foot with its formula, and never states a true value', () => {
    const { checks } = analyse(balance('600'));
    const bad = checks.filter((c) => c.status === 'does_not_foot');
    expect(bad.length).toBe(1);
    expect(bad[0]).toMatchObject({ read: '1,500', computed: '1,600', difference: '(100)', formula: '1,000 + 600' });
    // The total above a subtotal that did not foot is not checked (it would depend on that subtotal).
    const assets = checks.find((c) => c.read === '3,500' && c.column === 0 && c.status === 'not_checked');
    expect(assets?.reason).toBe('depends_on_unfooted_subtotal');
  });

  it('subtracts Less rows and treats parentheses as negatives', () => {
    reset();
    const { checks } = analyse(sheet([
      header(['2025', '2024']),
      row('Gross receivables', ['100', '90']), row('Less: allowance', ['10', '8']), row('Total receivables, net', ['90', '82']),
      row('Net income', ['50', '40']), row('Depreciation', ['20', '15']), row('Change in payables', ['(5)', '3']),
      row('Net cash provided by operating activities', ['65', '58']),
    ]));
    expect(checks.filter((c) => c.kind === 'total').map((c) => c.status)).toEqual(['foots', 'foots', 'foots', 'foots']);
  });

  it('leaves a total unchecked when a cell in its block is unreadable', () => {
    reset();
    const { checks } = analyse(sheet([
      header(['2025', '2024']), row('Cash', ['1,000', '900']), row('Receivables', ['5O0', '400']), row('Total', ['1,500', '1,300']),
    ]));
    expect(checks.find((c) => c.column === 0)?.status).toBe('not_checked');
    expect(checks.find((c) => c.column === 1)?.status).toBe('foots');
  });

  it('does not call a total wrong when a needed cell was not read', () => {
    reset();
    const { checks } = analyse(sheet([
      header(['2025', '2024']), row('Cash', ['1,000', '900']), row('Receivables', [null, '400']), row('Total', ['1,500', '1,300']),
    ]));
    expect(checks.find((c) => c.column === 0)).toMatchObject({ status: 'not_checked', reason: 'missing_cell' });
    expect(checks.find((c) => c.column === 1)?.status).toBe('foots');
  });

  it('recomputes year-on-year change and percent columns', () => {
    reset();
    const lines = sheet([
      [line('$ Change', 582, 630, 0), line('% Change', 652, 700, 0)],
      header(['2025', '2024'], [430, 530]),
      row('Revenue', ['1,200', '1,000', '200', '20.0%'], [430, 530, 630, 700]),
      row('Costs', ['700', '800', '(100)', '(12.5)%'], [430, 530, 630, 700]),
      row('Other', ['50', '40', '15', '25.0%'], [430, 530, 630, 700]),
    ]);
    const { checks } = analyse(lines);
    const change = checks.filter((c) => c.kind === 'change');
    expect(change.map((c) => c.status)).toEqual(['foots', 'foots', 'does_not_foot', 'foots', 'foots', 'foots']);
    expect(change[2]).toMatchObject({ read: '15', computed: '10', formula: '50 − 40' });
  });

  it('cross-foots a column headed Total', () => {
    reset();
    const lines = sheet([
      [line('Common', 380, 430, 0), line('Retained', 480, 530, 0), line('Other', 580, 630, 0), line('Total', 690, 730, 0)],
      row('Balance, January 1', ['100', '200', '(10)', '290'], [430, 530, 630, 730]),
      row('Net income', ['—', '50', '—', '50'], [430, 530, 630, 730]),
      row('Balance, December 31', ['100', '250', '(10)', '345'], [430, 530, 630, 730]),
    ]);
    const cross = analyse(lines).checks.filter((c) => c.kind === 'cross');
    expect(cross.map((c) => c.status)).toEqual(['foots', 'foots', 'does_not_foot']);
    expect(cross[2]).toMatchObject({ read: '345', computed: '340', difference: '5' });
  });
});

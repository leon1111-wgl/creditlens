/* Exact decimal arithmetic matching the subset of Python's decimal module the
   answer layer uses: add/sub keep the smaller exponent, division rounds to 40
   significant digits, quantize uses ROUND_HALF_EVEN. Values are int × 10^exp. */

export interface Dec { int: bigint; exp: number }

const TEN = 10n;
const pow10 = (n: number): bigint => TEN ** BigInt(n);

export function parse(text: string): Dec {
  const m = /^([+-]?)(\d*)(?:\.(\d*))?$/.exec(text.trim());
  if (!m || (m[2] === '' && (m[3] ?? '') === '')) throw new Error(`invalid decimal: ${text}`);
  const frac = m[3] ?? '';
  const digits = (m[2] + frac).replace(/^0+(?=\d)/, '') || '0';
  const value = BigInt(digits);
  return { int: m[1] === '-' ? -value : value, exp: -frac.length };
}

function align(a: Dec, b: Dec): [bigint, bigint, number] {
  const exp = Math.min(a.exp, b.exp);
  return [a.int * pow10(a.exp - exp), b.int * pow10(b.exp - exp), exp];
}

export function add(a: Dec, b: Dec): Dec { const [x, y, exp] = align(a, b); return { int: x + y, exp }; }
export function sub(a: Dec, b: Dec): Dec { const [x, y, exp] = align(a, b); return { int: x - y, exp }; }
export function cmp(a: Dec, b: Dec): number { const [x, y] = align(a, b); return x < y ? -1 : x > y ? 1 : 0; }
export function isPositive(a: Dec): boolean { return a.int > 0n; }
export function scaleb(a: Dec, n: number): Dec { return { int: a.int, exp: a.exp + n }; }

function roundHalfEven(value: bigint, divisor: bigint): bigint {
  // value / divisor rounded half-even; divisor > 0.
  const negative = value < 0n;
  const v = negative ? -value : value;
  let q = v / divisor;
  const r = v % divisor;
  const twice = r * 2n;
  if (twice > divisor || (twice === divisor && q % 2n === 1n)) q += 1n;
  return negative ? -q : q;
}

/** Python's quantize(Decimal(10**exp), rounding=ROUND_HALF_EVEN). */
export function quantize(a: Dec, exp: number): Dec {
  if (a.exp >= exp) return { int: a.int * pow10(a.exp - exp), exp };
  return { int: roundHalfEven(a.int, pow10(exp - a.exp)), exp };
}

const digitCount = (v: bigint): number => (v < 0n ? -v : v).toString().length;

/** Python a / b under a 40-digit context with ROUND_HALF_EVEN (b != 0). */
export function div40(a: Dec, b: Dec): Dec {
  if (b.int === 0n) throw new Error('division by zero');
  const negative = (a.int < 0n) !== (b.int < 0n);
  let p = a.int < 0n ? -a.int : a.int;
  let q = b.int < 0n ? -b.int : b.int;
  const idealExp = a.exp - b.exp;
  if (p === 0n) return { int: 0n, exp: idealExp };
  // Exact quotient when it terminates within 40 digits: Python returns the ideal exponent then.
  let shift = 0;
  let scaled = p;
  for (; shift <= 80; shift++) {
    if (scaled % q === 0n) {
      let coeff = scaled / q;
      let exp = idealExp - shift;
      if (digitCount(coeff) <= 40) {
        while (exp < idealExp && coeff % TEN === 0n) { coeff /= TEN; exp += 1; }
        return { int: negative ? -coeff : coeff, exp };
      }
      break;
    }
    scaled *= TEN;
  }
  // Inexact: 40 significant digits, rounded half-even.
  let e = 40 - (digitCount(p) - digitCount(q));
  let coeff = 0n;
  for (let i = 0; i < 3; i++) {
    const num = e >= 0 ? p * pow10(e) : p;
    const den = e >= 0 ? q : q * pow10(-e);
    coeff = roundHalfEven(num, den);
    const n = digitCount(coeff);
    if (n === 40) break;
    e += 40 - n;
  }
  // p/q ≈ coeff × 10^-e, so a/b ≈ coeff × 10^(idealExp - e).
  return { int: negative ? -coeff : coeff, exp: idealExp - e };
}

/** Python format(d, 'f'). */
export function formatF(a: Dec): string {
  const negative = a.int < 0n;
  let digits = (negative ? -a.int : a.int).toString();
  let out: string;
  if (a.exp >= 0) out = digits + '0'.repeat(a.exp);
  else {
    const places = -a.exp;
    if (digits.length <= places) digits = '0'.repeat(places - digits.length + 1) + digits;
    out = digits.slice(0, digits.length - places) + '.' + digits.slice(digits.length - places);
  }
  return negative ? '-' + out : out;
}

/** creditlens.evidence_answers.plain: normalized, no exponent, no trailing zeros, no -0. */
export function plain(a: Dec): string {
  if (a.int === 0n) return '0';
  let text = formatF(a);
  if (text.includes('.')) text = text.replace(/0+$/, '').replace(/\.$/, '');
  return text === '-0' || text === '0' ? '0' : text;
}

export const fromText = parse;

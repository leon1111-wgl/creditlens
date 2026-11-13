/* Number display for the credit snapshot. Amounts are shown exactly, in a stated scale (no
   rounding), so the figure on the page can be checked against the tagged cell. Ratios are rounded
   to two decimals for display only. */
import { parse as parseDec, scaleb } from '../decimal';
import { canon } from './ixbrl';

const SCALE_WORD: Record<number, string> = { 0: '', 3: '千', 6: '百万', 9: '十亿' };
export function unitWord(currency: string, scale: number): string {
  const word = SCALE_WORD[scale] ?? `×10^${scale} `;
  return word + (currency === 'USD' ? '美元' : ' ' + currency);
}
export function group(text: string): string {
  const neg = text.startsWith('-');
  const [int, frac] = (neg ? text.slice(1) : text).split('.');
  return (neg ? '−' : '') + int.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (frac ? '.' + frac : '');
}
/** Exact value in the given scale: '98657000000', 6 -> '98,657'. */
export function inScale(value: string, scale: number): string {
  return group(canon(scaleb(parseDec(value), -scale)));
}
export function amountText(value: string, scale: number, currency: string): string {
  return `${inScale(value, scale)} ${unitWord(currency, scale)}`;
}
/** Parse text produced by amountText back to an exact decimal string (used by the click check). */
export function parseAmountText(text: string): string | null {
  const m = /^(−|-)?([\d,]+(?:\.\d+)?)\s*(千|百万|十亿)?/.exec(text.trim());
  if (!m) return null;
  const scale = m[3] === '千' ? 3 : m[3] === '百万' ? 6 : m[3] === '十亿' ? 9 : 0;
  const d = scaleb(parseDec(m[2].replace(/,/g, '')), scale);
  return canon(m[1] ? { int: -d.int, exp: d.exp } : d);
}
export const multiple = (x: number) => `${x.toFixed(2)}×`;
export const percentText = (x: number) => `${(x * 100).toFixed(1)}%`;
export const ratioText = (x: number) => x.toFixed(2);

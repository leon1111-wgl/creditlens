/* The blind set's score must equal the committed report; regenerate with BLIND_WRITE=1 after a change
   and say in docs/44-browser-answers.md that later runs are not blind. */
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { expect, it } from 'vitest';
import { scoreSet } from './blind';
import { loadPack } from './helpers';

const SET = new URL('../../tests/browser_answers/blind-set.json', import.meta.url);
const REPORT = new URL('./blind-report.json', import.meta.url);

it('blind-set score matches the committed report', () => {
  const report = scoreSet(loadPack(), JSON.parse(readFileSync(SET, 'utf8')));
  if (process.env.BLIND_WRITE === '1' || !existsSync(REPORT)) {
    writeFileSync(REPORT, JSON.stringify(report, null, 1) + '\n');
  }
  expect(report).toEqual(JSON.parse(readFileSync(REPORT, 'utf8')));
});

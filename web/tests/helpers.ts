import { readFileSync } from 'node:fs';
import type { Pack } from '../src/types';

let cached: Pack | null = null;
export function loadPack(): Pack {
  if (!cached) cached = JSON.parse(readFileSync(new URL('../../creditlens/ask-pack.json', import.meta.url), 'utf8')) as Pack;
  return cached;
}

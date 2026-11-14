// Bundles the in-browser pages into committed scripts, so the local FastAPI service needs no Node:
//   src/main.ts      -> creditlens/ask.js        (问证据)
//   src/grounding.ts -> creditlens/grounding.js  (XBRL 对照)
//   src/tablecheck/main.ts -> creditlens/table-check.js (截图核对)
//   src/snapshot/main.ts -> creditlens/snapshot.js  (信用快照)
// CI rebuilds and fails if a committed bundle differs.
import { build } from 'esbuild';
import { readFileSync } from 'node:fs';
const version = /__version__ = "([^"]+)"/.exec(readFileSync(new URL('../creditlens/__init__.py', import.meta.url), 'utf8'))[1];
for (const [entry, outfile] of [['src/main.ts', '../creditlens/ask.js'], ['src/grounding.ts', '../creditlens/grounding.js'], ['src/tablecheck/main.ts', '../creditlens/table-check.js'], ['src/snapshot/main.ts', '../creditlens/snapshot.js']]) {
  await build({
    entryPoints: [entry], bundle: true, format: 'iife', target: 'es2020', minify: true,
    legalComments: 'none', charset: 'utf8', outfile, logLevel: 'warning', define: { __APP_VERSION__: JSON.stringify(version) },
  });
}

// Bundles the in-browser pages into committed scripts, so the local FastAPI service needs no Node:
//   src/main.ts      -> creditlens/ask.js        (问证据)
//   src/grounding.ts -> creditlens/grounding.js  (XBRL 对照)
// CI rebuilds and fails if a committed bundle differs.
import { build } from 'esbuild';
for (const [entry, outfile] of [['src/main.ts', '../creditlens/ask.js'], ['src/grounding.ts', '../creditlens/grounding.js']]) {
  await build({
    entryPoints: [entry], bundle: true, format: 'iife', target: 'es2020', minify: true,
    legalComments: 'none', charset: 'utf8', outfile, logLevel: 'warning',
  });
}

// Bundles src/main.ts into creditlens/ask.js (committed, so the local FastAPI service needs no Node).
// CI rebuilds and fails if the committed bundle differs.
import { build } from 'esbuild';
await build({
  entryPoints: ['src/main.ts'], bundle: true, format: 'iife', target: 'es2020', minify: true,
  legalComments: 'none', charset: 'utf8', outfile: '../creditlens/ask.js', logLevel: 'warning',
});

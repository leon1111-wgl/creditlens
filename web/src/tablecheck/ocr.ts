/* 截图核对 · in-browser OCR. PP-OCRv5 mobile detection + recognition through onnxruntime-web
   (WebAssembly SIMD, one thread). Every file is fetched only after the visitor clicks, from a pinned
   version, and refused unless its SHA-256 matches configs/table-check.json. */
import config from '../../../configs/table-check.json';
import type { OcrLine } from './table';

type FileSpec = { bytes: number; sha256_hex?: string; sha256_base64?: string };
interface OrtTensor { dims: readonly number[]; data: Float32Array }
interface OrtSession {
  inputNames: readonly string[]; outputNames: readonly string[];
  inputMetadata?: readonly { name: string; shape?: readonly (number | string)[] }[];
  run(feeds: Record<string, unknown>): Promise<Record<string, OrtTensor>>;
}
interface Ort {
  env: { wasm: Record<string, unknown> };
  Tensor: new (type: string, data: Float32Array, dims: number[]) => unknown;
  InferenceSession: { create(model: Uint8Array, options: Record<string, unknown>): Promise<OrtSession> };
}

export const RUNTIME_FILES = Object.entries(config.runtime.files as Record<string, FileSpec>);
export const WEIGHT_FILES = Object.entries(config.weights.files as Record<string, FileSpec>);
export const DOWNLOAD_BYTES = [...RUNTIME_FILES, ...WEIGHT_FILES].reduce((n, [, f]) => n + f.bytes, 0);

export class LoadError extends Error {
  constructor(public code: 'http' | 'hash' | 'network' | 'runtime' | 'cancelled', public file: string, detail = '') {
    super(`${code}: ${file}${detail ? ` (${detail})` : ''}`);
  }
}

/** Where the shipped path cannot run, the page hides the controls instead of failing later. */
export function canRun(): { ok: boolean; reason?: 'no_wasm' | 'no_simd' | 'insecure' | 'no_canvas' } {
  if (typeof WebAssembly !== 'object' || typeof WebAssembly.validate !== 'function') return { ok: false, reason: 'no_wasm' };
  // i32.const 0; i8x16.splat; i8x16.popcnt (the wasm-feature-detect SIMD probe)
  const simd = new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 1, 5, 1, 96, 0, 1, 123, 3, 2, 1, 0, 10, 10, 1, 8, 0, 65, 0, 253, 15, 253, 98, 11]);
  if (!WebAssembly.validate(simd)) return { ok: false, reason: 'no_simd' };
  if (typeof crypto === 'undefined' || !crypto.subtle) return { ok: false, reason: 'insecure' };
  if (typeof createImageBitmap !== 'function' || typeof document.createElement('canvas').getContext !== 'function') return { ok: false, reason: 'no_canvas' };
  return { ok: true };
}

function toHex(bytes: Uint8Array): string { return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join(''); }
function toBase64(bytes: Uint8Array): string { let s = ''; bytes.forEach((b) => { s += String.fromCharCode(b); }); return btoa(s); }

async function fetchChecked(url: string, name: string, spec: FileSpec, onBytes: (n: number) => void, signal: AbortSignal): Promise<ArrayBuffer> {
  let response: Response;
  try {
    response = await fetch(url, { signal, mode: 'cors', credentials: 'omit', cache: 'force-cache' });
  } catch (error) {
    if (signal.aborted) throw new LoadError('cancelled', name);
    throw new LoadError('network', name, String(error));
  }
  if (!response.ok || !response.body) throw new LoadError('http', name, String(response.status));
  const reader = response.body.getReader();
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value); size += value.length; onBytes(value.length);
      if (size > spec.bytes * 1.05 + 1024) throw new LoadError('hash', name, 'larger than pinned');
    }
  } catch (error) {
    if (signal.aborted) throw new LoadError('cancelled', name);
    throw error instanceof LoadError ? error : new LoadError('network', name, String(error));
  }
  const buffer = new Uint8Array(size);
  let at = 0;
  for (const p of parts) { buffer.set(p, at); at += p.length; }
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', buffer));
  const ok = spec.sha256_hex ? toHex(digest) === spec.sha256_hex : toBase64(digest) === spec.sha256_base64;
  if (!ok || size !== spec.bytes) throw new LoadError('hash', name);
  return buffer.buffer;
}

export interface Engine { det: OrtSession; rec: OrtSession; chars: string[]; ort: Ort; initSeconds: number; downloadedBytes: number }

export async function loadEngine(onProgress: (loaded: number, total: number, file: string) => void, signal: AbortSignal): Promise<Engine> {
  let loaded = 0;
  const bump = (file: string) => (n: number) => { loaded += n; onProgress(loaded, DOWNLOAD_BYTES, file); };
  const runtimeBase = config.runtime.base_url, weightBase = config.weights.base_url;
  const fetched: Record<string, ArrayBuffer> = {};
  // Runtime first (small JS, then the 14 MB wasm), then the weights.
  for (const [name, spec] of RUNTIME_FILES) fetched[name] = await fetchChecked(runtimeBase + name, name, spec, bump(name), signal);
  for (const [name, spec] of WEIGHT_FILES) fetched[name] = await fetchChecked(weightBase + name, name, spec, bump(name), signal);
  const started = performance.now();
  const w = window as unknown as { ort?: Ort };
  if (!w.ort) {
    const url = URL.createObjectURL(new Blob([fetched['ort.wasm.min.js']], { type: 'text/javascript' }));
    await new Promise<void>((resolve, reject) => {
      const s = document.createElement('script');
      s.src = url; s.onload = () => resolve(); s.onerror = () => reject(new LoadError('runtime', 'ort.wasm.min.js'));
      document.head.append(s);
    });
  }
  const ort = w.ort;
  if (!ort) throw new LoadError('runtime', 'ort.wasm.min.js', 'no global');
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmBinary = fetched['ort-wasm-simd-threaded.wasm'];
  ort.env.wasm.wasmPaths = {
    mjs: URL.createObjectURL(new Blob([fetched['ort-wasm-simd-threaded.mjs']], { type: 'text/javascript' })),
    wasm: URL.createObjectURL(new Blob([fetched['ort-wasm-simd-threaded.wasm']], { type: 'application/wasm' })),
  };
  const options = { executionProviders: ['wasm'], graphOptimizationLevel: 'all' };
  let det: OrtSession, rec: OrtSession;
  try {
    det = await ort.InferenceSession.create(new Uint8Array(fetched['ch_PP-OCRv5_det.onnx']), options);
    rec = await ort.InferenceSession.create(new Uint8Array(fetched['ch_PP-OCRv5_rec.onnx']), options);
  } catch (error) {
    throw new LoadError('runtime', 'InferenceSession', String(error));
  }
  const dict = new TextDecoder().decode(fetched['ch_PP-OCRv5_dict.txt']).split(/\r?\n/);
  if (dict[dict.length - 1] === '') dict.pop();
  return { det, rec, chars: ['', ...dict, ' '], ort, initSeconds: (performance.now() - started) / 1000, downloadedBytes: loaded };
}

// ---------------------------------------------------------------------------
// Inference
// ---------------------------------------------------------------------------

const DET_MAX_SIDE = 1600;
const DET_THRESH = 0.3, BOX_THRESH = 0.5, UNCLIP = 1.6, MIN_SIZE = 3;
const REC_H = 48, REC_MIN_W = 320, REC_BATCH = 6;

function fixedDims(session: OrtSession): (number | null)[] | null {
  const shape = session.inputMetadata?.[0]?.shape;
  return shape ? shape.map((d) => (typeof d === 'number' && d > 0 ? d : null)) : null;
}

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  return [c, ctx];
}

export interface OcrTimings { det: number; rec: number; lines: number; boxes: number }

/** Full-image OCR: text boxes from the detector, text from the recogniser, in image pixels. */
export async function recognise(engine: Engine, source: HTMLCanvasElement): Promise<{ lines: OcrLine[]; timings: OcrTimings }> {
  const { ort } = engine;
  const W0 = source.width, H0 = source.height;
  const t0 = performance.now();
  const fixed = fixedDims(engine.det);
  const scale = Math.min(1, DET_MAX_SIDE / Math.max(W0, H0));
  const dw = fixed?.[3] ?? Math.max(32, Math.round((W0 * scale) / 32) * 32);
  const dh = fixed?.[2] ?? Math.max(32, Math.round((H0 * scale) / 32) * 32);
  const [, dctx] = canvas(dw, dh);
  dctx.imageSmoothingQuality = 'high';
  dctx.drawImage(source, 0, 0, dw, dh);
  const px = dctx.getImageData(0, 0, dw, dh).data;
  const plane = dw * dh;
  const input = new Float32Array(3 * plane);
  // PaddleOCR order: BGR, (v/255 - mean) / std with ImageNet statistics.
  const mean = [0.485, 0.456, 0.406], std = [0.229, 0.224, 0.225];
  for (let i = 0; i < plane; i++) {
    const r = px[i * 4] / 255, g = px[i * 4 + 1] / 255, b = px[i * 4 + 2] / 255;
    input[i] = (b - mean[0]) / std[0];
    input[plane + i] = (g - mean[1]) / std[1];
    input[2 * plane + i] = (r - mean[2]) / std[2];
  }
  const detOut = await engine.det.run({ [engine.det.inputNames[0]]: new ort.Tensor('float32', input, [1, 3, dh, dw]) });
  const prob = detOut[engine.det.outputNames[0]].data;
  const boxes = dbBoxes(prob, dw, dh).map((b) => ({
    x0: Math.max(0, (b.x0 * W0) / dw), y0: Math.max(0, (b.y0 * H0) / dh),
    x1: Math.min(W0, (b.x1 * W0) / dw), y1: Math.min(H0, (b.y1 * H0) / dh),
  })).filter((b) => b.x1 - b.x0 >= 2 && b.y1 - b.y0 >= 2);
  const t1 = performance.now();

  const sctx = source.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  const order = boxes.map((b, i) => ({ b, i, ratio: (b.x1 - b.x0) / (b.y1 - b.y0) })).sort((a, b) => a.ratio - b.ratio);
  const results: (OcrLine | null)[] = new Array(boxes.length).fill(null);
  const recFixed = fixedDims(engine.rec);
  for (let k = 0; k < order.length; k += REC_BATCH) {
    const batch = order.slice(k, k + REC_BATCH);
    const W = recFixed?.[3] ?? Math.max(REC_MIN_W, Math.ceil(REC_H * Math.max(...batch.map((x) => x.ratio))));
    const n = batch.length;
    const data = new Float32Array(n * 3 * REC_H * W);
    batch.forEach(({ b }, j) => {
      const bw = Math.max(1, Math.round(b.x1 - b.x0)), bh = Math.max(1, Math.round(b.y1 - b.y0));
      const tw = Math.min(W, Math.max(1, Math.ceil((REC_H * bw) / bh)));
      const [c, ctx] = canvas(tw, REC_H);
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(sctx.canvas, Math.round(b.x0), Math.round(b.y0), bw, bh, 0, 0, tw, REC_H);
      const p = ctx.getImageData(0, 0, tw, REC_H).data;
      const off = j * 3 * REC_H * W, area = REC_H * W;
      for (let y = 0; y < REC_H; y++) {
        for (let x = 0; x < tw; x++) {
          const s = (y * tw + x) * 4, d = y * W + x;
          data[off + d] = (p[s + 2] / 255 - 0.5) / 0.5;
          data[off + area + d] = (p[s + 1] / 255 - 0.5) / 0.5;
          data[off + 2 * area + d] = (p[s] / 255 - 0.5) / 0.5;
        }
      }
      void c;
    });
    const out = await engine.rec.run({ [engine.rec.inputNames[0]]: new ort.Tensor('float32', data, [n, 3, REC_H, W]) });
    const tensor = out[engine.rec.outputNames[0]];
    const [, T, C] = tensor.dims as number[];
    batch.forEach(({ b, i }, j) => {
      const { text, conf } = ctcDecode(tensor.data, j * T * C, T, C, engine.chars);
      if (text.trim()) results[i] = { text, conf, box: b };
    });
  }
  const t2 = performance.now();
  const lines = results.filter((x): x is OcrLine => x !== null);
  return { lines, timings: { det: (t1 - t0) / 1000, rec: (t2 - t1) / 1000, lines: lines.length, boxes: boxes.length } };
}

/** DB post-processing for axis-aligned text: threshold, 4-connected components, mean-probability
    score, then the usual unclip by area × ratio / perimeter. */
export function dbBoxes(prob: Float32Array, w: number, h: number): { x0: number; y0: number; x1: number; y1: number; score: number }[] {
  const seen = new Uint8Array(w * h);
  const out: { x0: number; y0: number; x1: number; y1: number; score: number }[] = [];
  const stack = new Int32Array(w * h);
  for (let start = 0; start < w * h; start++) {
    if (seen[start] || prob[start] <= DET_THRESH) continue;
    let top = 0, sum = 0, count = 0;
    let minx = w, miny = h, maxx = -1, maxy = -1;
    stack[top++] = start; seen[start] = 1;
    while (top) {
      const p = stack[--top];
      const x = p % w, y = (p - x) / w;
      sum += prob[p]; count += 1;
      if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y;
      const neighbours = [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, y > 0 ? p - w : -1, y < h - 1 ? p + w : -1];
      for (const q of neighbours) {
        if (q >= 0 && !seen[q] && prob[q] > DET_THRESH) { seen[q] = 1; stack[top++] = q; }
      }
    }
    const bw = maxx - minx + 1, bh = maxy - miny + 1;
    if (Math.min(bw, bh) < MIN_SIZE || out.length >= 3000) continue;
    const score = sum / count;
    if (score < BOX_THRESH) continue;
    const d = (bw * bh * UNCLIP) / (2 * (bw + bh));
    out.push({ x0: Math.max(0, minx - d), y0: Math.max(0, miny - d), x1: Math.min(w, maxx + 1 + d), y1: Math.min(h, maxy + 1 + d), score });
  }
  return out;
}

export function ctcDecode(data: Float32Array, offset: number, T: number, C: number, chars: string[]): { text: string; conf: number } {
  let text = '', last = -1, sum = 0, kept = 0;
  for (let t = 0; t < T; t++) {
    let best = 0, bestP = -Infinity;
    const base = offset + t * C;
    for (let c = 0; c < C; c++) { const v = data[base + c]; if (v > bestP) { bestP = v; best = c; } }
    if (best !== 0 && best !== last) { text += chars[best] ?? ''; sum += bestP; kept += 1; }
    last = best;
  }
  return { text, conf: kept ? sum / kept : 0 };
}

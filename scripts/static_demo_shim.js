/* Static GitHub Pages demo only: replays responses recorded by scripts/export_static_demo.py.
   GET requests and registered read-only queries are answered from data/manifest.json.
   Writes are never sent anywhere; they are refused so page drafts stay in this tab's memory. */
(() => {
  'use strict';
  const script = document.currentScript;
  const siteRoot = new URL('../', script.src);
  const README = 'https://github.com/leon1111-wgl/creditlens#本地运行';
  const realFetch = window.fetch.bind(window);
  const manifest = realFetch(new URL('data/manifest.json', siteRoot), {cache: 'no-cache'})
    .then(response => { if (!response.ok) throw new Error('manifest ' + response.status); return response.json(); });
  const WRITE_DETAIL = '静态演示不保存写入：本次操作没有发送到任何服务器，页面上的改动只保留在当前标签页的内存中。完整流程请按 README 本地运行。';
  const MISSING_DETAIL = '静态演示没有录制这个请求，只回放仓库中保存的结果和预设查询。完整服务请按 README 本地运行。';
  const READ_ONLY_POST = /^\/api\/research\/tasks\/[^/]+\/evidence-query$/;
  window.CREDITLENS_STATIC_DEMO = true;

  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
    return value;
  }
  function headerValue(headers, name) {
    if (!headers) return null;
    if (headers instanceof Headers) return headers.get(name);
    const found = Object.keys(headers).find(key => key.toLowerCase() === name.toLowerCase());
    return found ? headers[found] : null;
  }
  function routeKey(method, url, body) {
    const params = [...url.searchParams.entries()].sort((a, b) => a[0] === b[0] ? (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0) : a[0] < b[0] ? -1 : 1);
    let key = method + ' ' + decodeURIComponent(url.pathname) + (params.length ? '?' + params.map(([k, v]) => k + '=' + v).join('&') : '');
    if (body !== undefined) key += ' body=' + JSON.stringify(canonical(JSON.parse(body)));
    return key;
  }
  function json(status, detail) {
    return new Response(JSON.stringify({detail}), {status, headers: {'Content-Type': 'application/json'}});
  }

  let toastTimer = null;
  function notify(message) {
    const show = () => {
      let toast = document.getElementById('clStaticToast');
      if (!toast) {
        toast = document.createElement('div');
        toast.id = 'clStaticToast'; toast.className = 'cl-static-toast';
        toast.setAttribute('role', 'status'); toast.setAttribute('aria-live', 'polite');
        document.body.append(toast);
      }
      toast.textContent = message; toast.hidden = false;
      clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, 7000);
    };
    if (document.body) show(); else document.addEventListener('DOMContentLoaded', show, {once: true});
  }

  window.fetch = async function staticDemoFetch(input, init = {}) {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (url.origin !== location.origin || !(url.pathname.startsWith('/api/') || url.pathname === '/health')) return realFetch(input, init);
    const readOnlyPost = method === 'POST' && READ_ONLY_POST.test(url.pathname);
    if (!['GET', 'HEAD'].includes(method) && !readOnlyPost) {
      notify(url.pathname === '/api/row-selection-verification' ? '静态演示：上传核对需要本地服务，文件没有发送到任何服务器。'
        : '静态演示：写入没有发送或保存，改动只留在本标签页。');
      return json(403, WRITE_DETAIL);
    }
    let key;
    try { key = routeKey(method, url, readOnlyPost ? String(init.body ?? '') : undefined); }
    catch { return json(400, '静态演示无法读取这次查询内容。'); }
    const routes = (await manifest).routes;
    const role = headerValue(init.headers, 'X-Demo-Role');
    const entry = (role && routes[key + ' |role=' + role]) || routes[key];
    if (!entry) {
      notify('静态演示没有录制这项请求；完整服务请按 README 本地运行。');
      console.warn('[CreditLens static demo] unrecorded request', key);
      return json(404, MISSING_DETAIL);
    }
    const response = await realFetch(new URL(entry.file, siteRoot), {cache: 'force-cache'});
    if (!response.ok) return json(503, '静态演示的录制文件暂不可用，请刷新后重试。');
    return new Response(method === 'HEAD' ? null : await response.blob(), {status: entry.status, headers: {'Content-Type': entry.type}});
  };

  /* Links that scripts set at runtime still point at server routes; map them to exported pages. */
  let routeMap = null;
  manifest.then(data => { routeMap = data; }).catch(() => notify('静态演示的录制清单无法读取，请刷新后重试。'));
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || !routeMap || link.target === '_blank' || event.defaultPrevented) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.pathname === '/' || !routeMap.app_routes.includes(url.pathname)) return;
    event.preventDefault();
    const page = routeMap.pages[url.pathname];
    if (page) location.href = new URL(page, siteRoot).href;
    else notify('这个页面需要本地运行完整服务，请按 README 启动后打开。');
  }, true);

  function banner() {
    const style = document.createElement('style');
    style.textContent = `
.cl-static-banner{position:sticky;top:0;z-index:60;display:flex;justify-content:center;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 16px;background:var(--cl-brass-soft,#f6eedc);color:var(--cl-ink,#161d43);border-bottom:1px solid color-mix(in srgb,var(--cl-brass,#85601f) 40%,transparent);font:500 12px/1.5 var(--cl-font,system-ui,sans-serif);text-align:center}
.cl-static-banner b{color:var(--cl-brass,#85601f);font-weight:650;letter-spacing:.3px}
.cl-static-banner a{color:var(--cl-brass,#85601f);text-decoration:underline;text-underline-offset:3px}
.cl-static-toast{position:fixed;right:16px;bottom:16px;z-index:70;max-width:min(420px,calc(100vw - 32px));padding:12px 14px;border-radius:8px;background:var(--cl-night,#131a3a);color:var(--cl-night-ink,#eef0fa);border-left:3px solid var(--cl-brass-bright,#d5ac5f);box-shadow:0 10px 30px #0006;font:13px/1.55 var(--cl-font,system-ui,sans-serif)}
.cl-static-toast[hidden]{display:none}
body.cl-static .lookup-evidence{top:44px}
@media (max-width:760px){.cl-static-banner{gap:2px 6px;font-size:11px;padding:4px 10px;line-height:1.45}.cl-static-sep{display:none}}`;
    document.head.append(style);
    const strip = document.createElement('div');
    strip.className = 'cl-static-banner'; strip.setAttribute('role', 'note');
    strip.innerHTML = '<b>静态演示</b><span>页面回放仓库中保存的结果</span><span class="cl-static-sep" aria-hidden="true">·</span><span>完整服务请按 <a href="' + README + '">README</a> 本地运行</span>';
    const skip = document.querySelector('.cl-skip');
    if (skip) skip.after(strip); else document.body.prepend(strip);
    document.body.classList.add('cl-static');
  }
  if (document.body) banner(); else document.addEventListener('DOMContentLoaded', banner, {once: true});
})();

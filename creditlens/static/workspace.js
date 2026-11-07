/* Shared presentation only. Financial calculations, data and permissions stay in each page. */
(() => {
  'use strict';
  const body = document.body;
  const main = document.querySelector('main');
  if (!main) return;
  const path = location.pathname.replace(/\/$/, '') || '/';
  const groups = [
    ['研究工作区', [
      ['/', '信用复核', 'overview'], ['/tasks', '研究任务', 'tasks'],
      ['/research', '季度研究', 'chart'], ['/margin', '毛利率变化', 'chart']
    ]],
    ['证据核验', [
      ['/documents', '原页核对', 'document'], ['/cross-page', '跨页证据', 'layers'],
      ['/table-lookup', '表格取数', 'table'], ['/reference-review', '参考核对', 'check']
    ]],
    ['材料与实验', [
      ['/document-preparation', '材料准备', 'folder'], ['/experiments', '文档实验', 'lab'],
      ['/transfer', '新公司核对', 'layers'], ['/explanation-reviews', '解释补充底稿', 'document'],
      ['/lab', 'FinQA 实验室', 'lab']
    ]]
  ];
  const iconPaths = {
    overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    tasks: '<rect x="5" y="4" width="15" height="17" rx="2"/><path d="M9 4V2h7v2M9 10h7M9 15h7"/>',
    chart: '<path d="M4 3v17h17M8 15l4-5 4 2 5-7"/>',
    document: '<path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h6"/>',
    layers: '<path d="m12 3 10 5-10 5L2 8zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M3 14h18M10 4v16"/>',
    check: '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6zM8 12l3 3 5-6"/>',
    folder: '<path d="M3 7V4h7l2 3h9v13H3z"/>',
    lab: '<path d="M9 3h6M10 3v7L4 20h16l-6-10V3M8 14h8"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>'
  };
  function icon(name) {
    const wrapper = document.createElement('span');
    wrapper.className = 'cl-icon';
    wrapper.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || iconPaths.document}</svg>`;
    return wrapper;
  }
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  const oldSidebar = document.querySelector('aside.sidebar');
  const queue = oldSidebar?.querySelector('#queue');
  const newCase = oldSidebar?.querySelector('#newCase');
  const oldHeader = document.querySelector('body > header, .main > header, .shell > .workspace > header, .app > div > header');
  const identity = oldHeader?.querySelector('.identity');
  const sourceLinks = [...(oldHeader?.querySelectorAll('a[href]') || [])]
    .filter(link => new URL(link.href).origin !== location.origin);
  const frame = document.querySelector('body > .shell, body > .app');
  frame?.classList.add('cl-page-frame');
  body.classList.add('cl-workspace');
  body.dataset.workspacePage = path === '/' ? 'review' : path.slice(1);
  main.classList.add('cl-content');
  main.id ||= 'cl-main';
  main.tabIndex = -1;

  const rail = element('aside', 'cl-rail');
  rail.id = 'cl-navigation';
  rail.setAttribute('aria-label', '工作区导航');
  const brand = element('a', 'cl-brand');
  brand.href = '/';
  const mark = element('span', 'cl-brand-mark', 'C');
  mark.setAttribute('aria-hidden', 'true');
  const wordmark = element('span', 'cl-wordmark', 'CreditLens');
  wordmark.append(element('small', '', '多模态金融证据工作台'));
  brand.append(mark, wordmark);
  const close = element('button', 'cl-nav-close');
  close.type = 'button';
  close.setAttribute('aria-label', '关闭导航');
  close.append(icon('close'));
  const railHead = element('div', 'cl-rail-head');
  railHead.append(brand, close);
  rail.append(railHead);

  const nav = element('nav', 'cl-nav');
  nav.setAttribute('aria-label', '主导航');
  let pageTitle = '金融证据工作台', sectionTitle = '研究工作区';
  groups.forEach(([title, links], index) => {
    const group = element(index === 2 ? 'details' : 'section', 'cl-nav-group');
    const label = element(index === 2 ? 'summary' : 'h2', 'cl-nav-label', title);
    group.append(label);
    if (index === 2) group.open = links.some(([href]) => href === path);
    for (const [href, title, name] of links) {
      const a = element('a', 'cl-nav-link');
      a.href = href;
      a.append(icon(name), element('span', '', title));
      if (href === path) {
        a.setAttribute('aria-current', 'page');
        pageTitle = title;
        sectionTitle = groups[index][0];
      }
      group.append(a);
    }
    nav.append(group);
  });
  rail.append(nav);
  if (queue || newCase) {
    const cases = element('section', 'cl-cases');
    cases.append(element('h2', 'cl-nav-label', path === '/' ? '复核案件' : '我的研究'));
    if (newCase) cases.append(newCase);
    if (queue) cases.append(queue);
    rail.append(cases);
  }
  const railFoot = element('div', 'cl-rail-foot');
  const apiLink = element('a', '', '接口文档 ↗');
  apiLink.href = '/docs';
  railFoot.append(apiLink, element('span', '', '公开披露 · 可追溯研究'));
  sourceLinks.forEach(link => railFoot.append(link));
  rail.append(railFoot);
  oldSidebar?.remove();

  const topbar = element('div', 'cl-topbar');
  const start = element('div', 'cl-topbar-start');
  const toggle = element('button', 'cl-nav-toggle');
  toggle.type = 'button';
  toggle.setAttribute('aria-controls', rail.id);
  toggle.append(icon('menu'));
  const breadcrumb = element('div', 'cl-breadcrumb');
  breadcrumb.append(element('span', '', sectionTitle), element('span', 'cl-slash', '/'), element('strong', '', pageTitle));
  start.append(toggle, breadcrumb);
  topbar.append(start);
  if (identity) topbar.append(identity);
  else topbar.append(element('span', 'cl-local-label', '本地研究工作区'));
  oldHeader?.remove();
  const skip = element('a', 'cl-skip', '跳转到主要内容');
  skip.href = '#' + main.id;
  const backdrop = element('div', 'cl-nav-backdrop');
  backdrop.setAttribute('aria-hidden', 'true');
  body.prepend(skip, rail, backdrop, topbar);

  const narrow = matchMedia('(max-width: 900px)');
  let expanded = !narrow.matches;
  let previousFocus = null;
  function setNavigation(open, restoreFocus = false) {
    const focusWasInRail = rail.contains(document.activeElement);
    expanded = open;
    body.classList.toggle('cl-nav-open', open);
    body.classList.toggle('cl-nav-collapsed', !open);
    rail.inert = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '收起导航' : '展开导航');
    const modal = open && narrow.matches;
    if (modal) { rail.setAttribute('role', 'dialog'); rail.setAttribute('aria-modal', 'true'); }
    else { rail.removeAttribute('role'); rail.removeAttribute('aria-modal'); }
    if (frame) frame.inert = modal;
    else main.inert = modal;
    topbar.inert = modal;
    if (!open && (restoreFocus || focusWasInRail)) (previousFocus?.isConnected ? previousFocus : toggle).focus();
    else if (modal) {
      rail.scrollTop = 0;
      requestAnimationFrame(() => { if (expanded && narrow.matches) close.focus(); });
    }
  }
  rail.addEventListener('transitionend', event => {
    if (event.target === rail && event.propertyName === 'transform' && expanded && narrow.matches
        && !rail.contains(document.activeElement)) close.focus();
  });
  toggle.addEventListener('click', () => { previousFocus = toggle; setNavigation(!expanded); });
  close.addEventListener('click', () => setNavigation(false, true));
  backdrop.addEventListener('click', () => setNavigation(false, true));
  document.addEventListener('keydown', event => {
    if (!narrow.matches || !expanded) return;
    if (event.key === 'Escape') { event.preventDefault(); setNavigation(false, true); }
    if (event.key === 'Tab') {
      const items = [...rail.querySelectorAll('a, button, summary, input, select')].filter(n => !n.disabled && n.getClientRects().length);
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  rail.addEventListener('click', event => {
    if (narrow.matches && event.target.closest('#queue button')) setNavigation(false, true);
  });
  narrow.addEventListener('change', () => setNavigation(!narrow.matches));
  setNavigation(expanded);

  // Keep page-level busy/role code scoped to business controls, not shell navigation.
  const shellButtons = [toggle, close];
  new MutationObserver(() => shellButtons.forEach(button => { if (button.disabled) button.disabled = false; }))
    .observe(body, {subtree: true, attributes: true, attributeFilter: ['disabled']});

  if (path === '/') {
    const controls = document.getElementById('creation');
    const disclosure = element('details', 'cl-case-controls');
    disclosure.id = 'cl-case-controls';
    disclosure.open = true;
    const summary = element('summary');
    summary.append(element('strong', '', '案例与研究条件'), element('span', 'cl-controls-meta', '选择资料、截止日期与情景'));
    controls.before(disclosure);
    disclosure.append(summary, controls);
    let hasOpenedCase = false;
    const workspace = document.getElementById('workspace');
    const updateControls = () => {
      if (!workspace.hidden && !hasOpenedCase) { disclosure.open = false; hasOpenedCase = true; }
      const select = document.getElementById('exampleFormat');
      summary.querySelector('.cl-controls-meta').textContent = select.selectedOptions[0].textContent + ' · 截止 ' + document.getElementById('asof').value;
    };
    new MutationObserver(updateControls).observe(workspace, {attributes: true, attributeFilter: ['hidden']});
    controls.addEventListener('change', updateControls);
    if (newCase) newCase.onclick = () => {
      disclosure.open = true;
      if (narrow.matches) setNavigation(false);
      document.getElementById('asof').focus();
      disclosure.scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center'});
    };
    updateControls();
  }

  // Progressive motion: content is visible even when animation is unsupported or disabled.
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const activeAnimations = new Set();
  const animationsByNode = new WeakMap();
  const motionTargets = '.tab-view, #workspace, #results, #detail, #preview, #previewPanel, #reviewArea';
  function reveal(node) {
    if (reduceMotion.matches || node.hidden || !node.getClientRects().length || !node.animate) return;
    animationsByNode.get(node)?.cancel();
    const animation = node.animate([{opacity: .35, transform: 'translateY(6px)'}, {opacity: 1, transform: 'translateY(0)'}],
      {duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)'});
    activeAnimations.add(animation);
    animationsByNode.set(node, animation);
    animation.finished.then(() => activeAnimations.delete(animation), () => activeAnimations.delete(animation));
  }
  new MutationObserver(records => {
    const targets = new Set();
    records.forEach(record => { if (record.target.matches(motionTargets) && !record.target.hidden) targets.add(record.target); });
    targets.forEach(reveal);
  }).observe(main, {subtree: true, attributes: true, attributeFilter: ['hidden']});
  main.addEventListener('toggle', event => {
    if (event.target.matches('details') && event.target.open) {
      [...event.target.children].filter(child => child.tagName !== 'SUMMARY').forEach(reveal);
    }
  }, true);
  reduceMotion.addEventListener('change', event => {
    if (event.matches) activeAnimations.forEach(animation => animation.cancel());
  });

  // Keep native table scrolling available to keyboard users, including after async renders.
  // Enhance existing wrappers only: business tables, controls and handlers remain in place.
  const tableRegions = new Map();
  let tableHintNumber = 0, tableRefreshPending = false;
  const tableResize = typeof ResizeObserver === 'function' ? new ResizeObserver(scheduleTableRefresh) : null;
  function scheduleTableRefresh() {
    if (tableRefreshPending) return;
    tableRefreshPending = true;
    requestAnimationFrame(() => { tableRefreshPending = false; refreshTableRegions(); });
  }
  function refreshTableRegions() {
    for (const [region, state] of tableRegions) {
      if (!main.contains(region) || !region.querySelector('table')) {
        tableResize?.unobserve(region);
        tableResize?.unobserve(state.table);
        state.owned.forEach(name => region.removeAttribute(name));
        const descriptions = (region.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== state.hint.id);
        if (descriptions.length) region.setAttribute('aria-describedby', descriptions.join(' '));
        else region.removeAttribute('aria-describedby');
        region.classList.remove('cl-table-region');
        state.hint.remove();
        tableRegions.delete(region);
      }
    }
    for (const region of main.querySelectorAll('.scroll, .table-wrap')) {
      const table = region.querySelector('table');
      if (!table) continue;
      let state = tableRegions.get(region);
      if (!state) {
        const hint = element('p', 'cl-table-hint', '可横向滚动 · 聚焦表格后用 ← → 查看其余列');
        hint.id = 'cl-table-hint-' + (++tableHintNumber);
        hint.hidden = true;
        state = {table, hint, owned: ['tabindex', 'role', 'aria-label'].filter(name => !region.hasAttribute(name)
          && !(name === 'aria-label' && region.hasAttribute('aria-labelledby')))};
        tableRegions.set(region, state);
        region.classList.add('cl-table-region');
        tableResize?.observe(region);
        tableResize?.observe(table);
      }
      if (state.table !== table) {
        tableResize?.unobserve(state.table);
        tableResize?.observe(table);
        state.table = table;
      }
      if (state.hint.nextElementSibling !== region) region.before(state.hint);
      const overflow = region.clientWidth > 0 && !region.closest('details:not([open])')
        && region.scrollWidth > region.clientWidth + 1;
      if (state.hint.hidden === overflow) state.hint.hidden = !overflow;
      const labelledBy = (table.getAttribute('aria-labelledby') || '').split(/\s+/)
        .map(id => document.getElementById(id)?.textContent.trim()).filter(Boolean).join(' ');
      const scope = region.closest('section, article, details') || main;
      const heading = [...scope.querySelectorAll('h2, h3, summary')]
        .filter(node => node.compareDocumentPosition(region) & Node.DOCUMENT_POSITION_FOLLOWING).at(-1)?.textContent.trim();
      const columns = [...table.querySelectorAll('th')].slice(0, 2).map(th => th.textContent.trim()).filter(Boolean).join('、');
      const label = labelledBy || table.getAttribute('aria-label') || table.querySelector('caption')?.textContent.trim()
        || (heading || pageTitle) + '：' + (columns || '数据表 ' + state.hint.id.split('-').at(-1));
      for (const [name, value] of [['tabindex', '0'], ['role', 'region'], ['aria-label', label]]) {
        if (!state.owned.includes(name)) continue;
        if (overflow) { if (region.getAttribute(name) !== value) region.setAttribute(name, value); }
        else region.removeAttribute(name);
      }
      const descriptions = (region.getAttribute('aria-describedby') || '').split(/\s+/).filter(id => id && id !== state.hint.id);
      if (overflow) descriptions.push(state.hint.id);
      if (descriptions.length) region.setAttribute('aria-describedby', descriptions.join(' '));
      else region.removeAttribute('aria-describedby');
    }
  }
  new MutationObserver(scheduleTableRefresh).observe(main, {
    subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'open']
  });
  window.addEventListener('resize', scheduleTableRefresh);
  document.fonts?.ready.then(scheduleTableRefresh);
  scheduleTableRefresh();
})();

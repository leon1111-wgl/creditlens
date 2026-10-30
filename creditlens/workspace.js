/* Optional navigation enhancement. The original select remains the source of truth. */
(() => {
  'use strict';
  const inspector = document.querySelector('.lookup-evidence');
  const expand = document.getElementById('expandEvidence');
  if (inspector && expand) {
    function setExpanded(value) {
      inspector.classList.toggle('is-expanded', value);
      expand.setAttribute('aria-expanded', String(value));
      expand.textContent = value ? '收起阅读区' : '展开阅读区';
    }
    expand.addEventListener('click', () => setExpanded(!inspector.classList.contains('is-expanded')));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && inspector.classList.contains('is-expanded')) {
        setExpanded(false);
        expand.focus();
      }
    });
  }
  const select = document.getElementById('case');
  const host = document.getElementById('lookupCaseList');
  const search = document.getElementById('lookupSearch');
  const count = document.getElementById('lookupListCount');
  if (!select || !host || !search || !count) return;
  function render() {
    const focusedValue = document.activeElement?.dataset.caseValue;
    const query = search.value.trim().toLocaleLowerCase();
    const options = Array.from(select.options).filter(option => option.textContent.toLocaleLowerCase().includes(query));
    host.replaceChildren();
    count.textContent = query ? `筛选到 ${options.length} 项` : `${options.length} 项记录`;
    for (const option of options) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.caseValue = option.value;
      button.setAttribute('aria-current', String(option.value === select.value));
      const parts = option.textContent.split(' · ');
      button.append(document.createTextNode(parts.slice(2).join(' · ') || option.textContent));
      const meta = document.createElement('span');
      meta.textContent = parts.slice(0, 2).join(' / ');
      button.append(meta);
      button.addEventListener('click', () => {
        if (select.value === option.value) return;
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles:true}));
      });
      host.append(button);
    }
    if (!options.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = query ? '没有匹配的行名或年份。' : '当前筛选没有记录。';
      host.append(empty);
    }
    if (focusedValue) {
      const target = Array.from(host.querySelectorAll('button')).find(button => button.dataset.caseValue === focusedValue);
      target?.focus({preventScroll:true});
    }
  }
  select.addEventListener('change', render);
  search.addEventListener('input', render);
  new MutationObserver(() => { search.value = ''; render(); }).observe(select, {childList:true});
  render();
})();

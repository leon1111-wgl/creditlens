/* Light, dark or system theme. Runs in <head> so the first paint uses the saved choice. */
(() => {
  'use strict';
  const KEY = 'creditlens.theme', CHOICES = ['system', 'light', 'dark'];
  const LABELS = {system: '跟随系统', light: '浅色', dark: '深色'};
  const root = document.documentElement;
  function read() {
    try { const value = localStorage.getItem(KEY); return CHOICES.includes(value) ? value : 'system'; }
    catch { return 'system'; }
  }
  function apply(choice) {
    if (choice === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', choice);
    root.setAttribute('data-theme-choice', choice);
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      button.setAttribute('aria-label', '切换主题，当前：' + LABELS[choice]);
      const label = button.querySelector('.cl-theme-label');
      if (label) label.textContent = LABELS[choice];
    }
  }
  apply(read());
  document.addEventListener('DOMContentLoaded', () => {
    apply(read());
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      button.addEventListener('click', () => {
        const next = CHOICES[(CHOICES.indexOf(read()) + 1) % CHOICES.length];
        try { localStorage.setItem(KEY, next); } catch { /* Storage may be blocked; still apply for this page. */ }
        apply(next);
      });
    }
  });
})();

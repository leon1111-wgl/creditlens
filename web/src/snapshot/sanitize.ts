/* Makes the parsed filing safe to show: the copy goes into <iframe sandbox="allow-same-origin">
   (no allow-scripts, so nothing in it can run), and before that every script, frame, plugin,
   external link and event handler is removed and a Content-Security-Policy that loads nothing
   from the network is put first in <head>. The parent page reads the frame's DOM only to scroll
   to and mark a tagged cell. */

const DROP = new Set(['script', 'noscript', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'link', 'meta', 'base',
  'portal', 'set', 'animate', 'animatemotion', 'animatetransform', 'foreignobject', 'audio', 'video', 'source', 'track']);
const URL_ATTRS = ['href', 'xlink:href', 'src', 'action', 'formaction', 'srcset', 'ping', 'background', 'poster', 'data', 'codebase', 'cite', 'longdesc', 'usemap'];
export const FRAME_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:";
export const FRAME_STYLE = `
[data-clf], [data-clh] { cursor: pointer; text-decoration: underline dotted rgba(133,96,31,.55); text-underline-offset: 2px; }
.cl-hit { outline: 3px solid #d5ac5f !important; outline-offset: 2px; background: #f6eedc !important; color: #161d43 !important;
  box-shadow: 0 0 0 7px rgba(213,172,95,.32) !important; border-radius: 2px; scroll-margin: 35vh; }
html { scroll-behavior: auto; }`;

const tagOf = (el: Element) => (el.tagName || el.nodeName || '').toLowerCase().split(':').pop() as string;

export interface SanitizeReport { removedElements: number; removedAttributes: number; neutralisedLinks: number }

/** CSS with escapes decoded (\\75 rl( → url(), so escaped url()/@import/image-set() are found too. */
function cssPlain(css: string): string {
  return css.replace(/\\([0-9a-fA-F]{1,6})\s?/g, (_, hex) => String.fromCodePoint(parseInt(hex, 16))).replace(/\\(.)/g, '$1');
}
const CSS_FETCH = /url\s*\(|@import|image-set\s*\(|expression\s*\(|-webkit-image-set/i;
function cleanCss(css: string): string {
  if (!CSS_FETCH.test(cssPlain(css))) return css;
  // Anything that could fetch is removed with its declaration; the frame CSP would block it anyway.
  return cssPlain(css).replace(/@import[^;]*;?/gi, '').replace(/[^;{}]*(url\s*\(|image-set\s*\(|expression\s*\(|-webkit-image-set)[^;{}]*;?/gi, '');
}

export function sanitize(doc: Document): SanitizeReport {
  const report: SanitizeReport = { removedElements: 0, removedAttributes: 0, neutralisedLinks: 0 };
  for (const el of Array.from(doc.getElementsByTagName('*'))) {
    if (DROP.has(tagOf(el))) { el.remove(); report.removedElements++; }
  }
  // Comments can hide conditional markup; drop them.
  const walker = doc.createTreeWalker(doc, 128 /* NodeFilter.SHOW_COMMENT */);
  const comments: Node[] = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) comments.push(n);
  comments.forEach(c => c.parentNode?.removeChild(c));
  for (const el of Array.from(doc.getElementsByTagName('*'))) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.replace(/[\s\u0000-\u001f]+/g, '').toLowerCase();
      if (name.startsWith('on')) { el.removeAttribute(attr.name); report.removedAttributes++; continue; }
      if (!URL_ATTRS.includes(name)) {
        if (name === 'style' && CSS_FETCH.test(cssPlain(attr.value))) { el.setAttribute('style', cleanCss(attr.value)); report.removedAttributes++; }
        continue;
      }
      if ((name === 'href' || name === 'xlink:href') && value.startsWith('#')) continue; // in-document links stay
      if (name === 'src' && value.startsWith('data:image/') && !value.startsWith('data:image/svg')) continue; // inline raster images stay
      el.removeAttribute(attr.name);
      if (name === 'href' || name === 'xlink:href') report.neutralisedLinks++; else report.removedAttributes++;
    }
  }
  for (const style of Array.from(doc.getElementsByTagName('style'))) {
    const css = style.textContent || '';
    if (CSS_FETCH.test(cssPlain(css))) style.textContent = cleanCss(css);
  }
  const head = doc.head || doc.documentElement.insertBefore(doc.createElement('head'), doc.body);
  const meta = doc.createElement('meta');
  meta.setAttribute('http-equiv', 'Content-Security-Policy');
  meta.setAttribute('content', FRAME_CSP);
  const charset = doc.createElement('meta'); charset.setAttribute('charset', 'utf-8');
  const style = doc.createElement('style'); style.textContent = FRAME_STYLE;
  head.insertBefore(style, head.firstChild);
  head.insertBefore(charset, head.firstChild);
  head.insertBefore(meta, head.firstChild);
  return report;
}

export function serialize(doc: Document): string {
  return '<!doctype html>' + doc.documentElement.outerHTML;
}

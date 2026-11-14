/* Gallery index provenance: the table's numbers come from the run that computed them (computed_by), not from the
   run that selected and fetched the filers. */
export interface GalleryIndex {
  rule_id: string; run: string; commit: string; definitions: string; definitions_at_selection?: string;
  computed_by: { run: string; commit: string; definitions: string; engine?: string };
  entries: { snapshot: { status: string } | null }[];
}
export function galleryNote(g: GalleryIndex): string {
  const supported = g.entries.filter(e => e.snapshot?.status === 'ok').length;
  return `${g.entries.length} 家，按预先提交的规则（${g.rule_id}）从 8 个行业层各取 5 家（选样与取文件：运行 ${g.run}）；${supported} 家可计算，${g.entries.length - supported} 家为不支持的模板。`
    + `表中数字是同一程序在 GitHub Actions 上对这些文件的计算结果（运行 ${g.computed_by.run}，提交 ${g.computed_by.commit.slice(0, 7)}，口径 ${g.computed_by.definitions}），打开后在你的浏览器里重新计算。`;
}

# Third-party data and software

## FinQA

The benchmark is from **FinQA: A Dataset of Numerical Reasoning over Financial Data**, EMNLP 2021, by Zhiyu Chen and coauthors.

- Paper: https://aclanthology.org/2021.emnlp-main.300/
- Repository: https://github.com/czyssrs/FinQA
- Pinned revision: 0f16e2867befa6840783e58be38c9efb9229d742
- Development file: dataset/dev.json
- Repository license reported by GitHub: MIT. See the upstream LICENSE for the full notice.
- Source company reports may have separate rights. This project does not grant rights to those underlying reports.
- Raw benchmark files are downloaded separately and excluded from Git. Saved evaluation output contains case identifiers and evidence locator labels; the demonstration contains a small set of benchmark excerpts with provenance.
- Upstream answers, gold programs, model_input and precomputed retrieval outputs are never included in the runtime search index.

## Microsoft public disclosures

The example fact pack contains limited numerical facts and labels derived from Microsoft's official FY2025 Q4 investor-relations tables, disclosed on 2025-07-30. URLs, retrieval dates, source hashes and concept locators are recorded. The additional PDF example uses three financial-statement pages from the official FY2025 Form 10-K at https://microsoft.gcs-web.com/node/33951. Minimal numerical facts, row labels and locations are retained; full HTML, PDF and rendered source pages are excluded. Source materials retain their own rights and are not relicensed under this repository's MIT license. This project is not affiliated with or endorsed by Microsoft. The case is historical, not a current investment or credit recommendation.

## Software

FastAPI, Starlette, Pydantic, Uvicorn, HTTPX and their dependencies retain their respective licenses. Exact tested versions are in requirements.lock. No third-party source code has been vendored in this prototype.

BM25, Decimal arithmetic and source-grounded retrieval are established methods, not new algorithms claimed by this project. The research plan cites the original papers and repositories for QLoRA, DPO, financial benchmarks and agent learning.

## Research materials

The planning documents summarize and link public papers, official repositories and institutional publications. They do not redistribute full papers, proprietary analyst reports, licensed market databases or internal bank data.

## Optional PDF tools

The offline document adapter uses pypdfium2/PDFium and Pillow through installed packages, without vendoring their source or binaries. Versions are pinned in requirements-pdf.lock. Their licenses and required bundled notices remain with those packages: https://pypdfium2.readthedocs.io/en/stable/readme.html#licensing and https://github.com/python-pillow/Pillow/blob/main/LICENSE. A reader installing these dependencies must retain the relevant upstream notices when redistributing them.

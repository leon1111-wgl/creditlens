"""Record the deterministic workbench into a static GitHub Pages demo.

Runs the FastAPI app in-process with demo identities and temporary state, replays
the registered synthetic research scenarios through ResearchTaskStore, records the
GET responses the demo pages request, and writes HTML with relative links plus a
fetch shim (scripts/static_demo_shim.js). No OCR, model, network call or page image
of an issuer filing is produced: source-page previews are reconstructions drawn
only from OCR text spans already saved in the repository. Writes are not recorded;
the shim refuses them in the browser.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from creditlens import __version__  # noqa: E402
from creditlens.api import create_app  # noqa: E402
from creditlens.workflow import Actor  # noqa: E402
from scripts.replay_research_scenarios import _update_arguments, _verify_outcome, load_scenarios  # noqa: E402

REPO_URL = "https://github.com/leon1111-wgl/creditlens"
LOCAL_RUN_URL = REPO_URL + "#本地运行"
ROLES = ("analyst", "reviewer", "viewer")
# Server route -> exported file. Other routes stay available only in the local service.
PAGES = {
    "/intro": ("index.html", "intro.html"),
    "/table-lookup": ("table-lookup.html", "table-lookup.html"),
    "/margin": ("margin.html", "margin.html"),
    "/research": ("research.html", "research.html"),
    "/tasks": ("tasks.html", "tasks.html"),
    "/experiments": ("experiments.html", "experiments.html"),
}
APP_ROUTES = ("/", "/intro", "/tasks", "/table-lookup", "/margin", "/research", "/documents", "/cross-page",
              "/explanation-reviews", "/reference-review", "/experiments", "/transfer", "/document-preparation",
              "/lab", "/docs")
ASSETS = ("workspace.css", "workspace.js", "theme.js", "research-write-intent.js", "research-write-journal.js")
# Snapshot loaders re-validate frozen reports on every request. The export reads each once;
# responses are unchanged because the files cannot change during one export.
MEMOIZED_LOADERS = (
    ("creditlens.table_lookup_routes", "load_table_lookup_snapshot"),
    ("creditlens.word_table_routes", "load_word_table_lookup_snapshot"),
    ("creditlens.role_table_routes", "load_role_table_lookup_snapshot"),
    ("creditlens.row_disambiguation_routes", "load_row_disambiguation_snapshot"),
    ("creditlens.unit_applicability_routes", "load_unit_applicability_snapshot"),
    ("creditlens.margin_routes", "load_margin_bridge_snapshot"),
    ("creditlens.margin_claim_routes", "load_margin_claim_snapshot"),
)
QUARTER_DATES = ("2024-07-01", "2024-10-30", "2024-10-31", "2025-01-29", "2025-01-30", "2025-04-30",
                 "2025-05-01", "2025-06-30", "2025-07-30", "2025-07-31")
QUERY_ISSUERS, QUERY_METRICS, QUERY_YEARS = ("MSFT", "AAPL"), ("cfo", "capex"), (2024, 2025)


def _memoized(loader):
    cache = {}

    def load(root):
        key = str(root)
        if key not in cache:
            cache[key] = json.dumps(loader(root), ensure_ascii=False, allow_nan=False)
        return json.loads(cache[key])
    return load


def canonical_key(method: str, path: str, params: dict | None = None, body=None, role: str | None = None) -> str:
    """Matches routeKey() in static_demo_shim.js: decoded path, sorted params, canonical JSON body."""
    pairs = sorted((str(k), str(v)) for k, v in (params or {}).items())
    key = f"{method} {path}" + ("?" + "&".join(f"{k}={v}" for k, v in pairs) if pairs else "")
    if body is not None:
        key += " body=" + json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return key + (f" |role={role}" if role else "")


class Recorder:
    def __init__(self, client: TestClient, out: Path):
        self.client, self.out, self.routes = client, out, {}
        (out/"data").mkdir(parents=True, exist_ok=True)

    def _store(self, content: bytes, media_type: str) -> str:
        suffix = {"application/json": ".json", "image/svg+xml": ".svg"}[media_type]
        name = "data/" + hashlib.sha256(content).hexdigest()[:20] + suffix
        path = self.out/name
        if not path.exists():
            path.write_bytes(content)
        return name

    def put(self, key: str, content: bytes, media_type: str, status: int = 200):
        if key in self.routes:
            raise ValueError(f"Duplicate recording for {key}")
        self.routes[key] = {"file": self._store(content, media_type), "status": status, "type": media_type}

    def request(self, method: str, path: str, params: dict | None = None, body=None, roles=ROLES):
        """Record one route for each demo role; identical answers share a role-free key."""
        url = quote(path, safe="/") + ("?" + urlencode(params) if params else "")
        answers = {}
        for role in roles:
            response = self.client.request(method, url, headers={"X-Demo-Role": role},
                                           json=body if body is not None else None)
            media_type = response.headers.get("content-type", "application/json").split(";")[0]
            if media_type != "application/json":
                raise ValueError(f"Unexpected {media_type} from {path}")
            answers[role] = (response.status_code, response.content)
        first = next(iter(answers.values()))
        if all(answer == first for answer in answers.values()):
            self.put(canonical_key(method, path, params, body), first[1], "application/json", first[0])
        else:
            for role, (status, content) in answers.items():
                self.put(canonical_key(method, path, params, body, role), content, "application/json", status)
        return json.loads(first[1]) if first[0] == 200 else None

    def get(self, path: str, params: dict | None = None, roles=ROLES):
        return self.request("GET", path, params, roles=roles)


def _spans(value):
    """Yield every saved OCR span ({text, bbox}) inside a recorded response."""
    if isinstance(value, dict):
        bbox, text = value.get("bbox"), value.get("text")
        if isinstance(text, str) and isinstance(bbox, dict) and {"x0", "top", "x1", "bottom"} <= bbox.keys():
            yield text, bbox
        for item in value.values():
            yield from _spans(item)
    elif isinstance(value, list):
        for item in value:
            yield from _spans(item)


class PageSketches:
    """Collect saved spans per document page and draw them; never an image of the filing."""

    def __init__(self):
        self.pages: dict[tuple[str, int], dict] = {}

    def add(self, document_id: str, page: int, width: float, height: float, value):
        entry = self.pages.setdefault((document_id, page), {"width": width, "height": height, "spans": {}})
        if abs(entry["width"] - width) > .01 or abs(entry["height"] - height) > .01:
            raise ValueError(f"Inconsistent page size for {document_id} page {page}")
        for text, bbox in _spans(value):
            key = (text, *(round(float(bbox[k]), 1) for k in ("x0", "top", "x1", "bottom")))
            entry["spans"][key] = (text, bbox)

    @staticmethod
    def _drawable(spans):
        """Line-level spans first; word spans and re-detections inside a kept span are skipped."""
        kept = []
        boxes = sorted(((text, tuple(float(b[k]) for k in ("x0", "top", "x1", "bottom"))) for text, b in spans),
                       key=lambda item: -(item[1][2] - item[1][0]) * (item[1][3] - item[1][1]))
        for text, box in boxes:
            area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
            if any(max(0, min(box[2], k[2]) - max(box[0], k[0])) * max(0, min(box[3], k[3]) - max(box[1], k[1])) / area > .5
                   for _, k in kept):
                continue
            kept.append((text, box))
        return sorted(kept, key=lambda item: (item[1][1], item[1][0]))

    def svg(self, document_id: str, page: int) -> bytes:
        entry = self.pages[(document_id, page)]
        width, height = entry["width"], entry["height"]
        label = f"{document_id.upper()} 第 {page} 页 · 重建示意：只绘制仓库中保存的识别片段，不是原页图像。原页请打开官方文件。"
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.2f}" height="{height:.2f}" viewBox="0 0 {width:.2f} {height:.2f}">',
            f"<title>{html.escape(label)}</title>",
            f'<rect width="{width:.2f}" height="{height:.2f}" fill="#fbfaf6"/>',
            f'<rect x="6" y="6" width="{width - 12:.2f}" height="22" rx="2" fill="#efe7d3"/>',
            f'<text x="12" y="21" font-size="8.6" fill="#6b4f1d" font-family="PingFang SC, Microsoft YaHei, sans-serif">{html.escape(label)}</text>',
        ]
        for text, (x0, top, x1, bottom) in self._drawable(entry["spans"].values()):
            h = max(bottom - top, 4.0)
            parts.append(
                f'<text x="{x0:.2f}" y="{bottom - h * .22:.2f}" font-size="{h * .74:.2f}" textLength="{max(x1 - x0, 1):.2f}" '
                f'lengthAdjust="spacingAndGlyphs" fill="#25231d" font-family="Times New Roman, Times, Songti SC, serif">'
                f"{html.escape(text)}</text>")
        parts.append("</svg>")
        return "\n".join(parts).encode("utf-8")


def _seed_research_tasks(store) -> list[dict]:
    """Replay the registered synthetic scenarios for the demo identities (public-demo tenant)."""
    seed = load_scenarios(ROOT)
    analyst, reviewer = Actor("demo-analyst", "public-demo", "analyst"), Actor("demo-reviewer", "public-demo", "reviewer")
    tasks = []
    for scenario in seed["scenarios"]:
        task = store.create(analyst, {
            "bundle_id": seed["provenance"]["bundle_id"], "question": scenario["user_question"],
            "selection_reason": scenario["selection_reason"], "material_mode": "current_verified_materials",
            "as_of": None, "include_cash_flow_bridge": scenario["include_cash_flow_bridge"],
        }, "static-demo:" + scenario["id"])
        for step in scenario["steps"]:
            if step["action"] == "update":
                decisions, hypotheses = _update_arguments(task, step)
                task = store.update(task["id"], analyst, task["revision"], decisions, hypotheses,
                                    step["coverage_note"], step.get("bridge_review_note"))
            else:
                actor = analyst if step["action"] == "submit" else reviewer
                sha = None if step["action"] == "submit" else task["snapshot_sha256"]
                task = store.act(task["id"], actor, task["revision"], step["action"], step["note"], sha)
        _verify_outcome(task, scenario)
        tasks.append(task)
    return tasks


def _evidence_queries(revision: int):
    for issuer in QUERY_ISSUERS:
        for metric in QUERY_METRICS:
            for year in QUERY_YEARS:
                first = {"issuer_id": issuer, "metric": metric, "year": year}
                yield {"revision": revision, "task_kind": "metric_lookup", "observations": [first]}
                for other_year in QUERY_YEARS:
                    yield {"revision": revision, "task_kind": "operating_evidence",
                           "observations": [first, {"issuer_id": issuer, "metric": metric, "year": other_year}]}
                    for other in QUERY_ISSUERS:
                        yield {"revision": revision, "task_kind": "comparable_change",
                               "observations": [first, {"issuer_id": other, "metric": metric, "year": other_year}]}


def _rewrite_page(source: str) -> str:
    def link(match):
        attr, target = match.group(1), match.group(2)
        path = target.partition("?")[0]
        path, hash_sep, fragment = path.partition("#")
        if path.startswith("/assets/"):
            return f'{attr}="assets/{path[len("/assets/"):]}"'
        if path in PAGES:
            return f'{attr}="{PAGES[path][0]}{hash_sep}{fragment}"'
        if path in APP_ROUTES:
            return f'{attr}="{LOCAL_RUN_URL}" data-local-route="{path}" title="此页面需本地运行完整服务"'
        return match.group(0)
    page = re.sub(r'\b(href|src)="(/[^"]*)"', link, source)

    def mark_local(match):
        tag = match.group(0)
        if "data-local-route=" not in tag:
            return tag
        if 'class="' in tag:
            return re.sub(r'class="([^"]*)"', lambda m: f'class="{m.group(1)} is-local"', tag, count=1)
        return tag.replace("<a ", '<a class="is-local" ', 1)
    page = re.sub(r"<a\b[^>]*>", mark_local, page)
    marker = '<meta charset="utf-8">'
    if page.count(marker) != 1:
        raise ValueError("Page head must contain one charset declaration")
    return page.replace(marker, marker + '<script src="assets/static-demo.js"></script>', 1)


def _source_commit() -> str | None:
    if os.getenv("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def export(out: Path) -> dict:
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"Output directory must be new or empty: {out}")
    if out == ROOT or out.is_relative_to(ROOT/"creditlens") or out.is_relative_to(ROOT/"data"):
        raise ValueError("Refusing to write the static demo into source or data directories")
    out.mkdir(parents=True, exist_ok=True)
    sketches = PageSketches()
    with tempfile.TemporaryDirectory(prefix="creditlens-static-") as state, ExitStack() as stack:
        for module, name in MEMOIZED_LOADERS:
            target = __import__(module, fromlist=[name])
            stack.enter_context(mock.patch.object(target, name, _memoized(getattr(target, name))))
        state = Path(state)
        app = create_app(dataset=state/"finqa-not-used.json", audit_db=state/"audit.sqlite", auth_mode="demo")
        with TestClient(app) as client:
            rec = Recorder(client, out)
            rec.get("/health")
            rec.get("/api/session")

            # Table lookup: three saved experiments, unit checks and explicit row choices.
            reports = {endpoint: rec.get(endpoint) for endpoint in
                       ("/api/table-lookup", "/api/word-table-lookup", "/api/role-table-lookup")}
            for endpoint, report in reports.items():
                for document in report["report"]["documents"]:
                    for case in document["cases"]:
                        meta = next(p for p in document["pages"] if p["page"] == case["page"])
                        sketches.add(document["id"], case["page"], meta["width"], meta["height"], case)
            role_report = reports["/api/role-table-lookup"]
            role_sha = role_report["report_sha256"]
            for document in role_report["report"]["documents"]:
                for case in document["cases"]:
                    for method in ("text", "line", "word", "role"):
                        rec.get(f"/api/unit-applicability/cases/{case['id']}",
                                {"method": method, "expected_role_report_sha256": role_sha})
            rows = rec.get("/api/row-disambiguation", {"expected_role_report_sha256": role_sha})
            for query in rows["report"]["queries"]:
                meta = query["page_metadata"]
                sketches.add(query["document_id"], query["page"], meta["width"], meta["height"], query)
                for candidate in query["discovery"]["candidates"]:
                    params = {"expected_report_sha256": rows["report_sha256"], "row_index": candidate["row_index"]}
                    rec.get(f"/api/row-disambiguation/queries/{query['id']}/selection", params)
                    rec.get(f"/api/unit-applicability/queries/{query['id']}/selection",
                            {"row_index": candidate["row_index"], "expected_role_report_sha256": role_sha,
                             "expected_row_report_sha256": rows["report_sha256"]})

            # Gross-margin bridge and registered company statements.
            margin = rec.get("/api/margin-bridge")
            rec.get("/api/margin-claims", {"expected_margin_report_sha256": margin["report_sha256"]})
            from creditlens.margin_experiment import margin_image_metadata
            margin_pages = {}
            for case in margin["report"]["cases"]:
                meta = margin_image_metadata(margin, case["id"])
                sketches.add(meta["document_id"], meta["page"], meta["width"], meta["height"], case)
                margin_pages[case["id"]] = (meta["document_id"], meta["page"])

            # Quarter research at the presets and each registered announcement boundary.
            for as_of in QUARTER_DATES:
                rec.get("/api/research/quarters", {"as_of": as_of, "dataset": "msft-fy2025"})
            rec.get("/api/experiments")

            # Research tasks: registered synthetic scenarios, replayed for the demo identities.
            tasks = _seed_research_tasks(app.state.research_tasks)
            rec.get("/api/research/tasks")
            rec.get("/api/research/task-materials")
            rec.get("/api/research/task-contracts")
            for task in tasks:
                base = f"/api/research/tasks/{task['id']}"
                rec.get(base)
                rec.get(base + "/acceptance", {"revision": task["revision"]})
                rec.get(base + "/company-attribution", {"revision": task["revision"]})
                rec.get(base + "/export")
                for snapshot in task["snapshots"]:
                    rec.get(base + "/export", {"snapshot_id": snapshot["id"]})
                    rec.get(base + f"/snapshots/{snapshot['id']}")
                for payload in _evidence_queries(task["revision"]):
                    rec.request("POST", base + "/evidence-query", body=payload)

            # Source-page previews: reconstructions from saved spans, keyed like the page requests.
            for endpoint, report in reports.items():
                for document in report["report"]["documents"]:
                    for case in document["cases"]:
                        rec.put(canonical_key("GET", f"{endpoint}/cases/{case['id']}/page.png",
                                              {"expected_report_sha256": report["report_sha256"]}),
                                sketches.svg(document["id"], case["page"]), "image/svg+xml")
            for query in rows["report"]["queries"]:
                rec.put(canonical_key("GET", f"/api/row-disambiguation/queries/{query['id']}/page.png",
                                      {"expected_report_sha256": rows["report_sha256"]}),
                        sketches.svg(query["document_id"], query["page"]), "image/svg+xml")
            for case_id, (document_id, page) in margin_pages.items():
                rec.put(canonical_key("GET", f"/api/margin-bridge/cases/{case_id}/page.png",
                                      {"expected_report_sha256": margin["report_sha256"]}),
                        sketches.svg(document_id, page), "image/svg+xml")

    (out/"assets").mkdir()
    for name in ASSETS:
        shutil.copyfile(ROOT/"creditlens"/name, out/"assets"/name)
    shutil.copyfile(ROOT/"scripts/static_demo_shim.js", out/"assets/static-demo.js")
    for route, (target, source) in PAGES.items():
        (out/target).write_text(_rewrite_page((ROOT/"creditlens"/source).read_text(encoding="utf-8")), encoding="utf-8")
    (out/".nojekyll").write_text("", encoding="utf-8")
    manifest = {
        "schema_version": 1, "application_version": __version__, "source_commit": _source_commit(),
        "scope": "Static replay of saved results and registered synthetic research scenarios; no OCR, model or "
                 "network inference; writes are refused in the browser; page previews are span reconstructions.",
        "pages": {route: target for route, (target, _) in PAGES.items()},
        "app_routes": list(APP_ROUTES),
        "routes": dict(sorted(rec.routes.items())),
    }
    (out/"data/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT/"site")
    args = parser.parse_args(argv)
    manifest = export(args.output)
    files = {entry["file"] for entry in manifest["routes"].values()}
    size = sum((args.output/f).stat().st_size for f in files)
    print(json.dumps({"status": "exported", "output": str(args.output), "routes": len(manifest["routes"]),
                      "files": len(files), "bytes": size, "pages": manifest["pages"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

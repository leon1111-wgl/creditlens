"""Static Pages export: recorded reads only, no issuer page images, relative pages."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from scripts import export_static_demo as exporter


class StaticDemoExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.site = Path(cls.temp.name)/"site"
        cls.manifest = exporter.export(cls.site)

    def body(self, key):
        entry = self.manifest["routes"][key]
        return (self.site/entry["file"]).read_bytes()

    def test_only_reads_and_registered_read_only_queries_are_recorded(self):
        methods = {key.split(" ", 1)[0] for key in self.manifest["routes"]}
        self.assertEqual(methods, {"GET", "POST"})
        for key in self.manifest["routes"]:
            if key.startswith("POST "):
                self.assertRegex(key, r"^POST /api/research/tasks/[^/ ]+/evidence-query body=")
        self.assertIn("GET /api/table-lookup", self.manifest["routes"])
        self.assertTrue(all(entry["status"] == 200 for entry in self.manifest["routes"].values()))

    def test_research_tasks_are_the_registered_synthetic_scenarios(self):
        tasks = json.loads(self.body("GET /api/research/tasks"))
        self.assertEqual(len(tasks), 3)
        self.assertTrue(all(t["question"].startswith("【模拟业务】") for t in tasks))
        self.assertEqual(sorted(t["status"] for t in tasks), ["returned", "reviewed", "reviewed"])

    def test_page_previews_are_span_reconstructions_not_filing_images(self):
        previews = [key for key, entry in self.manifest["routes"].items() if entry["type"] == "image/svg+xml"]
        self.assertGreater(len(previews), 100)
        for key in previews:
            self.assertRegex(key, r"/page\.png\?expected_report_sha256=[0-9a-f]{64}$")
            svg = self.body(key).decode("utf-8")
            self.assertIn("重建示意", svg)
            self.assertNotIn("<image", svg)
        files = [p.name for p in self.site.rglob("*") if p.is_file()]
        self.assertFalse([name for name in files if re.search(r"\.(png|jpe?g|pdf)$", name, re.I)])

    def test_pages_use_relative_links_and_load_the_shim_first(self):
        for route, page in self.manifest["pages"].items():
            html = (self.site/page).read_text(encoding="utf-8")
            self.assertNotRegex(html, r'\b(?:href|src)="/(?!/)', route)
            self.assertEqual(re.search(r"<script[^>]*>", html).group(0), '<script src="assets/static-demo.js">')
        tasks = (self.site/"tasks.html").read_text(encoding="utf-8")
        local = re.search(r'<a [^>]*data-local-route="/explanation-reviews"[^>]*>', tasks).group(0)
        self.assertIn('class="is-local"', local)
        self.assertIn(f'href="{exporter.LOCAL_RUN_URL}"', local)

    def test_keys_match_the_shim_canonical_form(self):
        key = exporter.canonical_key("POST", "/api/x", {"b": 2, "a": "1"}, {"z": [1], "a": {"d": 1, "c": 2}}, "viewer")
        self.assertEqual(key, 'POST /api/x?a=1&b=2 body={"a":{"c":2,"d":1},"z":[1]} |role=viewer')

    def test_refuses_to_overwrite_existing_output(self):
        with self.assertRaises(ValueError):
            exporter.export(self.site)


if __name__ == "__main__":
    unittest.main()

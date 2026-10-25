from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creditlens.api import create_app
from creditlens.experiments import FILES, experiment_view, _word_checks, _stress_checks
from creditlens.visual_store import VisualArtifactError

ROOT = Path(__file__).resolve().parents[1]


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (*FILES, "configs/experiment-artifacts.json", "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json"):
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        self.view = experiment_view(self.root)

    def test_both_methods_all_conditions_and_read_only_export(self):
        self.assertEqual(self.view["word"]["word_summary"], dict(consistent=42, conflict=0, insufficient=0))
        self.assertEqual(self.view["word"]["baseline_summary"]["insufficient"], 3)
        self.assertEqual(len(self.view["word"]["changes"]), 3)
        self.assertEqual(sum(v["fact_count"] for v in self.view["robustness"]["summary_by_variant"]), 80)
        self.assertTrue(self.view["read_only"])

    def test_any_report_or_config_edit_requires_reviewed_freeze(self):
        for name in FILES:
            path = self.root/name; raw = path.read_bytes()
            path.write_bytes(raw+b" ")
            with self.subTest(name=name), self.assertRaises(VisualArtifactError):
                experiment_view(self.root)
            path.write_bytes(raw)

    def test_source_pack_drift_is_rejected(self):
        path = self.root/"data/examples/msft-fy2025-pdf.json"
        pack = json.loads(path.read_text(encoding="utf-8")); pack["title"] += " changed"
        path.write_text(json.dumps(pack), encoding="utf-8")
        with self.assertRaises(VisualArtifactError):
            experiment_view(self.root)

    def test_word_missing_facts_pages_bad_totals_and_reference_rejected_before_freeze(self):
        original = self.view["word"]
        mutations = [lambda r: r["documents"][0].update(pages=[]),
                     lambda r: r["documents"][0]["facts"].pop(),
                     lambda r: r["word_summary"].update(consistent=999),
                     lambda r: r["documents"][0]["facts"][0]["checks"][0].update(expected="wrong"),
                     lambda r: r["documents"][0]["pages"][0].update(image_sha256="0"*64)]
        for i, mutate in enumerate(mutations):
            record = deepcopy(original); mutate(record)
            with self.subTest(i=i), self.assertRaises(VisualArtifactError):
                _word_checks(record, self.view["baseline"], original["profile"])

    def test_stress_missing_conditions_or_altered_transforms_and_totals_rejected(self):
        original = self.view["robustness"]
        config = json.loads((self.root/"configs/visual-robustness.json").read_text(encoding="utf-8"))
        mutations = [lambda r: r["documents"][0]["conditions"].pop(),
                     lambda r: r["documents"][0]["conditions"][0]["transform"].update(operation="jpeg"),
                     lambda r: r["documents"][0]["conditions"][0].update(synthetic_degradation=True),
                     lambda r: r["documents"][0].update(width=999),
                     lambda r: r["summary_by_variant"][0]["summary"].update(consistent=999)]
        for i, mutate in enumerate(mutations):
            record = deepcopy(original); mutate(record)
            with self.subTest(i=i), self.assertRaises(VisualArtifactError):
                _stress_checks(record, self.view["baseline"], config)
        record = deepcopy(original)
        record["documents"][0]["conditions"].reverse()
        _stress_checks(record, self.view["baseline"], config)

    def test_validated_baseline_is_not_reread(self):
        with patch("creditlens.experiments.VisualStore.report", side_effect=AssertionError("second read")):
            self.assertEqual(experiment_view(self.root)["baseline"], self.view["baseline"])

    def test_api_auth_read_only_cache_and_failure_responses(self):
        principals = [dict(key_sha256=sha256(b"test-key").hexdigest(), user_id="v", tenant_id="a", role="viewer")]
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(self.root/"missing.json", self.root/"audit.sqlite", auth_mode="api_key", principals=principals)) as client:
            headers = {"Authorization": "Bearer test-key"}
            self.assertEqual(client.get("/api/experiments").status_code, 401)
            self.assertEqual(client.get("/api/experiments", headers={"X-Demo-Role": "viewer"}).status_code, 401)
            response = client.get("/api/experiments", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), self.view)
            self.assertEqual(client.post("/api/experiments", headers=headers).status_code, 405)
            (self.root/FILES[1]).write_text("{}", encoding="utf-8")
            self.assertEqual(client.get("/api/experiments", headers=headers).status_code, 409)
            (self.root/FILES[1]).unlink()
            self.assertEqual(client.get("/api/experiments", headers=headers).status_code, 503)


if __name__ == "__main__":
    unittest.main()

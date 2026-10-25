"""Synthetic observations exercise separation, persistence and atomic workflow."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from creditlens.diligence import digest
from creditlens.reference_contract import build_reference_catalog
from creditlens.reference_review_store import ReferenceReviewStore
from creditlens.workflow import Actor

ROOT = Path(__file__).resolve().parents[1]
FIRST = Actor("synthetic-first", "synthetic-tenant", "analyst")
SECOND = Actor("synthetic-second", "synthetic-tenant", "analyst")
THIRD = Actor("synthetic-third", "synthetic-tenant", "reviewer")
VIEWER = Actor("synthetic-viewer", "synthetic-tenant", "viewer")
OTHER = Actor("synthetic-first", "other-tenant", "analyst")
NOTE = "Synthetic workflow review only, not a human annotation."


def synthetic_observation(value="123.45"):
    return {"status": "answerable", "raw_value": value,
            "amount_bbox": {"x0": 10, "top": 10, "x1": 50, "bottom": 20},
            "unit_text": "SYNTHETIC_UNITS_CANARY", "currency_text": None,
            "section_text": "SYNTHETIC_SECTION_CANARY", "section_note": None,
            "reason": "SYNTHETIC_REASON_CANARY"}


class ReferenceReviewStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_reference_catalog(ROOT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"synthetic.sqlite3"
        self.store = ReferenceReviewStore(self.path, ROOT)
        self.current = deepcopy(self.catalog)
        self.catalog_patch = patch.object(self.store, "_catalog", side_effect=lambda: deepcopy(self.current))
        self.catalog_patch.start(); self.addCleanup(self.catalog_patch.stop)
        self.task = self.catalog["tasks"][0]
        self.payload = {"task_id": self.task["id"], "expected_catalog_sha256": self.catalog["catalog_sha256"]}

    def error(self, status, call, code=None):
        with self.assertRaises(HTTPException) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        if code:
            self.assertEqual(caught.exception.detail["code"], code)
        return caught.exception

    def create(self, key="create", actor=FIRST):
        return self.store.create(actor, self.payload, key)

    def two(self, second=None):
        created = self.create()
        first = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        return self.store.submit(first["id"], SECOND, 2, second or synthetic_observation(), "second")

    def test_catalog_is_real_forty_tasks_but_inputs_have_no_prior_answer(self):
        self.assertEqual(len(self.catalog["tasks"]), 40)
        real = ReferenceReviewStore(Path(self.temp.name)/"real-contract.sqlite3", ROOT)
        result = real.create(FIRST, self.payload, "real-create")
        self.assertEqual(result["task"], self.task)
        self.assertNotIn("raw_value", json.dumps(result))
        self.assertNotIn("amount_bbox", json.dumps(result))
        self.assertFalse(result["human_gold"])

    def test_first_observation_hidden_from_every_other_role_and_summary(self):
        created = self.create()
        first = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        own_sha = first["observations"][0]["observation"]["observation_sha256"]
        self.assertTrue(first["observations"][0]["visible"])
        for actor in (SECOND, THIRD, VIEWER):
            result = self.store.get(created["id"], actor)
            self.assertIsNone(result["observations"][0]["observation"])
            self.assertFalse(result["observations"][0]["visible"])
            self.assertIsNone(result["comparison"])
            rendered = json.dumps([result, self.store.list(actor)])
            for secret in (own_sha, "123.45", "SYNTHETIC_UNITS_CANARY", "SYNTHETIC_REASON_CANARY"):
                self.assertNotIn(secret, rendered)
            error = self.error(409, lambda: self.store.export(created["id"], actor))
            self.assertNotIn(own_sha, json.dumps(error.detail))

    def test_second_reveals_both_and_third_freezes_without_human_gold(self):
        data = self.two()
        self.assertEqual(data["status"], "agreed")
        self.assertTrue(all(o["visible"] for o in data["observations"]))
        frozen = self.store.freeze(data["id"], THIRD, 3, NOTE)
        exported = self.store.export(data["id"], VIEWER)
        self.assertEqual(frozen["status"], "frozen")
        self.assertEqual(exported["content"]["freeze"]["actor"], THIRD.user_id)
        self.assertFalse(exported["human_gold"])
        self.assertTrue(exported["independence_not_verified"])
        self.assertEqual(exported["content_sha256"], digest(exported["content"]))

    def test_same_principal_changing_role_cannot_take_second_or_review(self):
        created = self.create()
        first = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        changed = Actor(FIRST.user_id, FIRST.tenant_id, "reviewer")
        self.error(403, lambda: self.store.submit(first["id"], changed, 2, synthetic_observation(), "again"))
        self.error(403, lambda: self.store.close(first["id"], changed, 2, NOTE))
        second = self.store.submit(first["id"], SECOND, 2, synthetic_observation(), "second")
        self.error(403, lambda: self.store.freeze(second["id"], changed, 3, NOTE))

    def test_creator_can_review_only_if_not_an_observer(self):
        created = self.create(actor=THIRD)
        self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        self.store.submit(created["id"], SECOND, 2, synthetic_observation(), "second")
        self.assertEqual(self.store.freeze(created["id"], THIRD, 3, NOTE)["status"], "frozen")

    def test_viewer_cannot_write_and_analyst_cannot_review(self):
        self.error(403, lambda: self.create(actor=VIEWER))
        created = self.create()
        self.error(403, lambda: self.store.submit(created["id"], VIEWER, 1, synthetic_observation(), "viewer"))
        self.error(403, lambda: self.store.close(created["id"], SECOND, 1, NOTE))
        self.error(401, lambda: self.store.get(created["id"], Actor("x", "synthetic-tenant", "admin")))

    def test_cross_tenant_get_list_submit_export_and_previous_are_isolated(self):
        created = self.create()
        for call in (lambda: self.store.get(created["id"], OTHER),
                     lambda: self.store.export(created["id"], OTHER),
                     lambda: self.store.submit(created["id"], OTHER, 1, synthetic_observation(), "foreign")):
            self.error(404, call)
        self.assertEqual(self.store.list(OTHER), [])
        self.store.close(created["id"], THIRD, 1, NOTE)
        payload = {**self.payload, "previous_review_id": created["id"], "reason": NOTE}
        self.error(404, lambda: self.store.create(OTHER, payload, "foreign-previous"))

    def test_original_idempotency_response_does_not_gain_second_observation(self):
        created = self.create()
        first = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        self.store.submit(created["id"], SECOND, 2, synthetic_observation("456.78"), "second")
        with patch.object(self.store, "_catalog", side_effect=AssertionError("Historical replay is offline")):
            self.assertEqual(self.store.create(FIRST, self.payload, "create"), created)
            replay = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
            self.assertEqual(replay, first)
            self.assertEqual(len(replay["observations"]), 1)
            self.assertNotIn("456.78", json.dumps(replay))

    def test_idempotency_key_binds_caller_payload_and_operation(self):
        created = self.create()
        self.error(409, lambda: self.store.create(SECOND, self.payload, "create"))
        self.error(409, lambda: self.store.create(FIRST, {**self.payload, "reason": NOTE}, "create"))
        self.error(409, lambda: self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "create"))

    def test_stale_and_third_observation_do_not_modify_slots(self):
        data = self.two()
        self.error(409, lambda: self.store.submit(data["id"], THIRD, 2, synthetic_observation(), "stale"))
        self.error(409, lambda: self.store.submit(data["id"], THIRD, 3, synthetic_observation(), "third"))
        self.assertEqual(len(self.store.get(data["id"], VIEWER)["observations"]), 2)

    def test_concurrent_same_revision_has_one_winner(self):
        created = self.create()
        def submit(actor):
            try:
                return self.store.submit(created["id"], actor, 1, synthetic_observation(), actor.user_id)["revision"]
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, (FIRST, SECOND)))
        self.assertEqual(sorted(results), [2, 409])
        self.assertEqual(len(self.store.get(created["id"], VIEWER)["observations"]), 1)

    def test_disagreement_cannot_freeze_close_and_new_round_keep_history(self):
        data = self.two(synthetic_observation("-123.45"))
        self.assertEqual(data["status"], "disagreed")
        before = self.store.get(data["id"], VIEWER)
        self.error(409, lambda: self.store.freeze(data["id"], THIRD, 3, NOTE))
        closed = self.store.close(data["id"], THIRD, 3, NOTE)
        new = self.store.create(FIRST, {**self.payload, "previous_review_id": data["id"], "reason": NOTE}, "new-round")
        self.assertEqual(new["round"], 2)
        self.assertEqual(new["observations"], [])
        self.assertEqual(closed["observations"], before["observations"])
        self.assertEqual(closed["comparison"], before["comparison"])
        self.error(409, lambda: self.store.submit(closed["id"], THIRD, 4, synthetic_observation(), "closed-submit"))

    def test_frozen_new_round_preserves_original_export(self):
        data = self.two()
        self.store.freeze(data["id"], THIRD, 3, NOTE)
        original = self.store.export(data["id"], VIEWER)
        new = self.store.create(FIRST, {**self.payload, "previous_review_id": data["id"], "reason": NOTE}, "new-round")
        self.assertEqual(new["round"], 2)
        self.assertEqual(new["observations"], [])
        self.assertEqual(self.store.export(data["id"], VIEWER), original)

    def test_incomplete_close_still_hides_first_and_allows_empty_new_round(self):
        created = self.create()
        self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        closed = self.store.close(created["id"], THIRD, 2, NOTE)
        self.assertIsNone(closed["observations"][0]["observation"])
        self.assertNotIn("SYNTHETIC_REASON_CANARY", json.dumps(closed))
        new = self.store.create(SECOND, {**self.payload, "previous_review_id": created["id"], "reason": NOTE}, "new")
        self.assertEqual(new["observations"], [])
        self.assertIsNone(self.store.get(created["id"], SECOND)["observations"][0]["observation"])

    def test_registration_drift_blocks_submit_freeze_but_not_close_or_history(self):
        data = self.two()
        original = self.store.get(data["id"], VIEWER)
        self.current["catalog_sha256"] = "a"*64
        self.error(409, lambda: self.store.freeze(data["id"], THIRD, 3, NOTE), "reference_registration_changed")
        self.assertEqual(self.store.get(data["id"], VIEWER), original)
        self.assertEqual(self.store.close(data["id"], THIRD, 3, NOTE)["status"], "closed")
        payload = {**self.payload, "expected_catalog_sha256": "a"*64, "previous_review_id": data["id"], "reason": NOTE}
        self.assertEqual(self.store.create(FIRST, payload, "new-version")["catalog_sha256"], "a"*64)

    def test_missing_catalog_does_not_block_close_reads_or_idempotent_replay(self):
        created = self.create()
        first = self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first")
        with patch.object(self.store, "_catalog", side_effect=HTTPException(409, {"code": "reference_registration_unavailable"})):
            self.error(409, lambda: self.store.submit(created["id"], SECOND, 2, synthetic_observation(), "second"))
            self.assertEqual(self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "first"), first)
            self.assertEqual(self.store.close(created["id"], THIRD, 2, NOTE)["status"], "closed")
            self.assertEqual(self.store.get(created["id"], VIEWER)["status"], "closed")
            self.error(409, lambda: self.store.create(FIRST, {**self.payload, "previous_review_id": created["id"], "reason": NOTE}, "new"))

    def test_registry_change_between_checks_rolls_back_observation(self):
        created = self.create()
        changed = deepcopy(self.current); changed["catalog_sha256"] = "b"*64
        with patch.object(self.store, "_catalog", side_effect=[deepcopy(self.current), changed]):
            self.error(409, lambda: self.store.submit(created["id"], FIRST, 1, synthetic_observation(), "changed"))
        self.assertEqual(self.store.get(created["id"], VIEWER)["observations"], [])
        self.assertEqual(self.store.get(created["id"], VIEWER)["revision"], 1)

    def test_rebuilt_store_reads_frozen_export_without_live_catalog(self):
        data = self.two(); self.store.freeze(data["id"], THIRD, 3, NOTE)
        original = self.store.export(data["id"], VIEWER)
        rebuilt = ReferenceReviewStore(self.path, ROOT)
        with patch.object(rebuilt, "_catalog", side_effect=AssertionError("No live registry read")):
            self.assertEqual(rebuilt.export(data["id"], VIEWER), original)
            self.assertEqual(rebuilt.get(data["id"], VIEWER)["status"], "frozen")

    def test_nonanswer_agreement_and_different_free_notes_are_not_human_gold(self):
        obs = {key: None for key in synthetic_observation()}
        obs.update(status="insufficient", reason="Missing synthetic evidence one")
        created = self.create(); self.store.submit(created["id"], FIRST, 1, obs, "first")
        second = self.store.submit(created["id"], SECOND, 2, {**obs, "reason": "Missing synthetic evidence two"}, "second")
        self.assertEqual(second["status"], "agreed")
        self.assertEqual(second["comparison"]["answer_status"], "insufficient")
        self.assertTrue(second["comparison"]["notes_differ"])
        frozen = self.store.freeze(created["id"], THIRD, 3, NOTE)
        self.assertFalse(frozen["human_gold"])

    def test_invalid_observation_boolean_revision_and_injected_claims_fail_without_echo(self):
        created = self.create()
        bads = []
        for change in ({"actor": "attacker"}, {"human_gold": True}, {"amount_bbox": {"x0": float("nan"), "top": 1, "x1": 2, "bottom": 3}}):
            bads.append({**synthetic_observation(), **change})
        for index, bad in enumerate(bads):
            error = self.error(422, lambda: self.store.submit(created["id"], FIRST, 1, bad, f"bad-{index}"))
            self.assertNotIn("SYNTHETIC", json.dumps(error.detail))
        self.error(422, lambda: self.store.submit(created["id"], FIRST, True, synthetic_observation(), "bool"))
        self.assertEqual(self.store.get(created["id"], VIEWER)["revision"], 1)

    def test_create_injection_previous_reason_empty_key_and_duplicate_round_fail(self):
        self.error(422, lambda: self.store.create(FIRST, {**self.payload, "tenant_id": "evil"}, "injection"))
        self.error(422, lambda: self.store.create(FIRST, self.payload, ""))
        created = self.create()
        self.error(409, lambda: self.create("duplicate"))
        self.error(422, lambda: self.store.create(FIRST, {**self.payload, "previous_review_id": created["id"]}, "no-note"))
        self.error(409, lambda: self.store.create(FIRST, {**self.payload, "previous_review_id": created["id"], "reason": NOTE}, "active"))

    def test_corrupt_stored_digest_is_rejected(self):
        created = self.create()
        with self.store.db(write=True) as db:
            db.execute("UPDATE reference_reviews SET payload_sha256=? WHERE id=?", ("0"*64, created["id"]))
        self.error(409, lambda: self.store.get(created["id"], FIRST), "stored_reference_integrity_failed")

    def test_replay_matches_saved_synthetic_artifact_without_creating_human_gold(self):
        from scripts.replay_reference_review import build_replay
        report = build_replay(ROOT)
        saved = json.loads((ROOT/"artifacts/reference-review-replay.json").read_text(encoding="utf-8"))
        self.assertEqual(report, saved)
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertEqual(report["summary"]["human_gold"], 0)
        self.assertEqual(report["summary"]["synthetic_observations"], 4)
        self.assertEqual(report["scope"]["independent_human_annotations"], 0)

    def test_replay_output_is_new_json_in_isolated_directory(self):
        from scripts.replay_reference_review import output_path
        root = Path(self.temp.name)
        expected = root/".run/reproductions/replay.json"
        self.assertEqual(output_path(root, Path("replay.json")), expected)
        for path in (root/"artifacts/replay.json", Path("nested/replay.json"), Path("replay.txt")):
            with self.assertRaises(ValueError):
                output_path(root, path)
        expected.parent.mkdir(parents=True)
        expected.write_text("preserve", encoding="utf-8")
        with self.assertRaises(ValueError):
            output_path(root, expected)
        self.assertEqual(expected.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()

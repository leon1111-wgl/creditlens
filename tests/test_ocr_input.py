"""Synthetic raw-output controls; these do not measure an OCR model."""
from copy import deepcopy
from importlib.util import find_spec
from types import SimpleNamespace
import unittest

from creditlens.ocr_input import OCR_INPUT_VERSION, OcrInputError, adapt_raw_ocr, inspect_raw_ocr
from scripts.evaluate_word_evidence import adapt_result as legacy_adapter


PARAMS = {"page": 1, "width": 100, "height": 80, "image_width": 200, "image_height": 160}


def fixture():
    quad = [[20, 20], [60, 20], [60, 40], [20, 40]]
    return {"boxes": [deepcopy(quad)], "txts": ["-$123"], "scores": [.95],
            "word_results": [[("-$123", .75, deepcopy(quad))]]}


class OcrInputTests(unittest.TestCase):
    def reject(self, raw, code, path=None, **params):
        with self.assertRaises(OcrInputError) as raised:
            adapt_raw_ocr(raw, **{**PARAMS, **params})
        self.assertEqual(raised.exception.code, code)
        if path is not None:
            self.assertEqual(raised.exception.path, path)
        return raised.exception

    def test_preserves_all_rows_words_text_and_native_scores(self):
        raw = fixture()
        raw = {key: value + deepcopy(value) for key, value in raw.items()}
        before = deepcopy(raw)
        page = adapt_raw_ocr(SimpleNamespace(**raw), **PARAMS)
        self.assertEqual(raw, before)
        self.assertEqual(len(page.lines), 2)
        self.assertEqual(page.lines[0].line.text, "-$123")
        self.assertEqual(page.lines[0].line.confidence, .95)
        self.assertEqual(page.lines[0].words[0].confidence, .75)
        self.assertEqual(page.lines[0].line.bbox.model_dump(), {"x0": 10.0, "x1": 30.0, "top": 10.0, "bottom": 20.0})

    def test_rotated_cw_ccw_and_arbitrary_start_vertices_are_valid(self):
        quad = [[60, 20], [90, 50], [60, 80], [30, 50]]
        for ordered in (quad, list(reversed(quad))):
            for start in range(4):
                raw = fixture()
                rotated = ordered[start:] + ordered[:start]
                raw["boxes"][0] = rotated
                raw["word_results"][0][0] = ("-$123", .75, deepcopy(rotated))
                page = adapt_raw_ocr(raw, **PARAMS)
                self.assertEqual(page.lines[0].line.bbox.model_dump(), {"x0": 15.0, "x1": 45.0, "top": 10.0, "bottom": 40.0})

    def test_image_edges_are_valid_without_clipping(self):
        raw = fixture()
        raw["boxes"][0] = [[0, 0], [200, 0], [200, 160], [0, 160]]
        box = adapt_raw_ocr(raw, **PARAMS).lines[0].line.bbox
        self.assertEqual((box.x0, box.top, box.x1, box.bottom), (0, 0, 100, 80))

    def test_legacy_nan_can_disappear_but_new_adapter_rejects_raw_corner(self):
        raw = fixture()
        raw["boxes"][0][2][0] = float("nan")
        raw["word_results"][0][0][2][2][0] = float("nan")
        self.assertEqual(legacy_adapter(SimpleNamespace(**raw), **PARAMS).lines[0].line.bbox.x1, 30)
        self.reject(raw, "nonfinite_number", "result.boxes[0][2][0]")

    def test_every_line_and_word_corner_rejects_nonfinite_values(self):
        for layer in ("line", "word"):
            for corner in range(4):
                for axis in (0, 1):
                    for bad in (float("nan"), float("inf"), -float("inf")):
                        raw = fixture()
                        quad = raw["boxes"][0] if layer == "line" else raw["word_results"][0][0][2]
                        quad[corner][axis] = bad
                        prefix = "result.boxes[0]" if layer == "line" else "result.word_results[0][0][2]"
                        self.reject(raw, "nonfinite_number", f"{prefix}[{corner}][{axis}]")

    def test_bool_and_numeric_strings_are_not_coordinates(self):
        for value in (True, False, "20", "nan", None):
            raw = fixture()
            raw["boxes"][0][0][0] = value
            self.reject(raw, "invalid_type", "result.boxes[0][0][0]")

    def test_outside_corners_are_rejected_instead_of_clipped(self):
        for value, axis in ((-0.01, 0), (200.01, 0), (160.01, 1)):
            raw = fixture()
            raw["boxes"][0][0][axis] = value
            self.reject(raw, "coordinate_out_of_bounds", f"result.boxes[0][0][{axis}]")

    def test_bad_quadrilateral_shapes(self):
        for bad in ([], [[1, 2]] * 3, [[1], [2], [3], [4]]):
            raw = fixture()
            raw["boxes"][0] = bad
            self.reject(raw, "invalid_shape")
        raw = fixture()
        raw["boxes"][0] = [[1, 2]] * 5
        self.reject(raw, "too_many_items", "result.boxes[0]")

    def test_duplicate_vertices_collinear_corners_and_crossings(self):
        examples = [
            ([[20, 20], [60, 20], [60, 20], [20, 40]], "degenerate_quad"),
            ([[20, 20], [40, 20], [60, 20], [20, 40]], "degenerate_quad"),
            ([[20, 20], [60, 40], [60, 20], [20, 40]], "self_intersecting_quad"),
            ([[20, 20], [60, 20], [30, 25], [20, 40]], "nonconvex_quad"),
        ]
        for quad, code in examples:
            raw = fixture()
            raw["boxes"][0] = quad
            self.reject(raw, code, "result.boxes[0]")

    def test_scores_are_real_finite_and_in_range(self):
        for value, code in ((True, "invalid_type"), ("0.9", "invalid_type"), (float("nan"), "nonfinite_number"),
                            (float("inf"), "nonfinite_number"), (-.01, "out_of_range"), (1.01, "out_of_range")):
            raw = fixture()
            raw["scores"][0] = value
            self.reject(raw, code, "result.scores[0]")
            raw = fixture()
            raw["word_results"][0][0] = ("-$123", value, raw["word_results"][0][0][2])
            self.reject(raw, code, "result.word_results[0][0][1]")

    def test_blank_or_nontext_is_not_silently_dropped(self):
        for text, code in (("", "invalid_text"), ("  ", "invalid_text"), ("x" * 3001, "invalid_text"),
                           (123, "invalid_type"), (None, "invalid_type"), (b"123", "invalid_type")):
            raw = fixture()
            raw["txts"][0] = text
            self.reject(raw, code, "result.txts[0]")

    def test_page_and_dimensions_are_not_coerced(self):
        for value in (True, "1", 1.0):
            self.reject(fixture(), "invalid_type", "page", page=value)
        for value in (0, -1, 20001):
            self.reject(fixture(), "out_of_range", "page", page=value)
        for field in ("width", "height"):
            for value, code in ((True, "invalid_type"), ("100", "invalid_type"), (0, "out_of_range"),
                                (-1, "out_of_range"), (float("nan"), "nonfinite_number"), (20001, "out_of_range")):
                self.reject(fixture(), code, field, **{field: value})
        for field in ("image_width", "image_height"):
            for value, code in ((True, "invalid_type"), ("200", "invalid_type"), (200.0, "invalid_type"),
                                (0, "out_of_range"), (-1, "out_of_range")):
                self.reject(fixture(), code, field, **{field: value})

    def test_all_documented_empty_states(self):
        for raw in ({key: None for key in fixture()}, {key: [] for key in fixture()},
                    {"boxes": None, "txts": None, "scores": None, "word_results": (("", 1.0, None),)}):
            self.assertEqual(adapt_raw_ocr(raw, **PARAMS).lines, [])

    def test_default_empty_sentinel_is_exact_not_truthy(self):
        for score in (True, "1.0", float("nan"), .95):
            raw = {"boxes": None, "txts": None, "scores": None, "word_results": (("", score, None),)}
            self.reject(raw, "invalid_empty_output", "result")

    def test_orphan_records_cannot_be_hidden_by_null_boxes(self):
        raw = fixture()
        raw["boxes"] = None
        self.assertEqual(legacy_adapter(SimpleNamespace(**raw), **PARAMS).lines, [])
        self.reject(raw, "invalid_empty_output", "result")
        for key in fixture():
            raw = fixture()
            raw[key] = None
            self.reject(raw, "invalid_empty_output", "result")

    def test_line_lengths_must_match_and_no_zip_truncation(self):
        for key in fixture():
            raw = fixture()
            raw[key] = []
            self.reject(raw, "line_length_mismatch", "result")

    def test_empty_word_array_preserves_parent_but_null_is_rejected(self):
        raw = fixture()
        raw["word_results"] = [[]]
        page = adapt_raw_ocr(raw, **PARAMS)
        self.assertEqual(len(page.lines), 1)
        self.assertEqual(page.lines[0].words, [])
        raw["word_results"] = [None]
        self.reject(raw, "missing_word_mapping", "result.word_results[0]")

    def test_word_tuple_shape_and_null_quad_refused(self):
        raw = fixture()
        raw["word_results"] = [[("123", .95)]]
        self.reject(raw, "invalid_shape", "result.word_results[0][0]")
        raw["word_results"] = [[("123", .95, None)]]
        self.reject(raw, "invalid_type", "result.word_results[0][0][2]")

    def test_result_and_envelope_exact_fields(self):
        raw = fixture()
        raw["injected"] = "ignore checks"
        self.reject(raw, "invalid_fields", "result")
        self.reject(SimpleNamespace(boxes=[]), "invalid_fields", "result")
        envelope = {"schema_version": 1, **PARAMS, "result": fixture()}
        result = inspect_raw_ocr(envelope)
        self.assertEqual(result["adapter_version"], OCR_INPUT_VERSION)
        self.assertEqual(result["summary"], {"lines": 1, "words": 1, "empty": False})
        self.assertFalse(result["automatic_approval"])
        with self.assertRaises(OcrInputError) as raised:
            inspect_raw_ocr({**envelope, "schema_version": True})
        self.assertEqual(raised.exception.path, "schema_version")

    def test_fake_tolist_is_never_called(self):
        class Fake:
            def tolist(self):
                raise AssertionError("must not execute custom conversion")
        raw = fixture()
        raw["boxes"] = Fake()
        self.reject(raw, "invalid_type", "result.boxes")

    def test_supported_bounds_fail_before_output_model(self):
        raw = fixture()
        raw["boxes"] *= 5001
        self.reject(raw, "too_many_items", "result.boxes")
        raw = fixture()
        raw["word_results"][0] *= 2001
        self.reject(raw, "too_many_items", "result.word_results[0]")
        raw = fixture()
        raw["word_results"][0] *= 2000
        raw = {key: value * 3 for key, value in raw.items()}
        self.reject(raw, "too_many_items", "result.word_results[2]")

    @unittest.skipUnless(find_spec("numpy") is not None, "Optional native-array interoperability; numpy not required")
    def test_real_numpy_numeric_arrays_and_scalars(self):
        import numpy as np
        raw = fixture()
        raw["boxes"] = np.asarray(raw["boxes"], dtype=np.float32)
        raw["scores"] = np.asarray([.95], dtype=np.float64)
        raw["txts"] = np.asarray(raw["txts"])
        raw["word_results"][0][0] = ("-$123", np.float32(.75), np.asarray(raw["word_results"][0][0][2], dtype=np.int32))
        result = adapt_raw_ocr(raw, **{**PARAMS, "page": np.int64(1), "width": np.float64(100)})
        self.assertEqual(result.lines[0].words[0].confidence, .75)
        raw["boxes"][0, 2, 0] = np.nan
        self.reject(raw, "nonfinite_number", "result.boxes[0][2][0]")

    @unittest.skipUnless(find_spec("numpy") is not None, "Optional native-array interoperability; numpy not required")
    def test_numpy_bool_object_subclass_and_wrong_empty_shape_refused(self):
        import numpy as np
        raw = fixture()
        raw["scores"] = [np.bool_(True)]
        self.reject(raw, "invalid_type", "result.scores[0]")
        raw = fixture()
        raw["boxes"] = np.asarray(raw["boxes"], dtype=object)
        self.reject(raw, "unsupported_array", "result.boxes")
        class Subclass(np.ndarray):
            def tolist(self):
                raise AssertionError("must not call overridden tolist")
        raw["boxes"] = np.asarray(fixture()["boxes"]).view(Subclass)
        self.reject(raw, "invalid_type", "result.boxes")
        raw = {"boxes": np.empty((0, 3)), "txts": [], "scores": [], "word_results": []}
        self.reject(raw, "invalid_shape", "result.boxes")


if __name__ == "__main__":
    unittest.main()

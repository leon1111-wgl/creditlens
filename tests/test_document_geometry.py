"""PDF page-coordinate regressions without importing an optional PDF library."""
import unittest

from creditlens.document import EvidenceError, Token, pdfium_tokens


class TextPage:
    def __init__(self, glyphs):
        self.glyphs = glyphs
        self.closed = False

    def count_chars(self):
        return len(self.glyphs)

    def get_text_range(self, index, count):
        return self.glyphs[index][0]

    def get_charbox(self, index):
        bounds = self.glyphs[index][1]
        if bounds is None:
            raise AssertionError("Whitespace must not request a glyph rectangle")
        if isinstance(bounds, Exception):
            raise bounds
        return bounds

    def close(self):
        self.closed = True


class Page:
    def __init__(self, bbox=(0, 0, 200, 300), glyphs=(), *, rotation=0, size=None):
        self.bbox, self.rotation = bbox, rotation
        self.size = size if size is not None else (bbox[2]-bbox[0], bbox[3]-bbox[1])
        self.text = TextPage(glyphs)
        self.text_opened = False

    def get_bbox(self): return self.bbox
    def get_rotation(self): return self.rotation
    def get_width(self): return self.size[0]
    def get_height(self): return self.size[1]
    def get_textpage(self):
        self.text_opened = True
        return self.text

    def get_mediabox(self):
        raise AssertionError("Use inherited get_bbox(), not a potentially incomplete MediaBox getter")

    def get_cropbox(self):
        raise AssertionError("Use the MediaBox/CropBox intersection, not CropBox alone")


class DocumentGeometryTests(unittest.TestCase):
    def test_zero_origin_retains_existing_positions_and_word_grouping(self):
        page = Page(glyphs=[("1", (20, 250, 25, 260)), ("2", (26, 250, 31, 260)),
                            (" ", None), ("3", (50, 250, 55, 260))])
        self.assertEqual(pdfium_tokens(page), [Token("12", 20, 40, 31, 50), Token("3", 50, 40, 55, 50)])
        self.assertTrue(page.text.closed)

    def test_nonzero_visible_origin_translates_both_axes(self):
        page = Page((10, 20, 210, 320), [("A", (30, 260, 40, 280))])
        self.assertEqual(pdfium_tokens(page), [Token("A", 20, 40, 30, 60)])

    def test_negative_media_origin_uses_the_same_transform(self):
        page = Page((-100, -200, 100, 100), [("A", (-90, 70, -80, 80))])
        self.assertEqual(pdfium_tokens(page), [Token("A", 10, 20, 20, 30)])

    def test_media_crop_intersection_is_authoritative_even_with_inherited_boxes(self):
        media = (-20, 50, 300, 400)
        crop = (0, 40, 290, 450)
        visible = (max(media[0], crop[0]), max(media[1], crop[1]), min(media[2], crop[2]), min(media[3], crop[3]))
        page = Page(visible, [("A", (10, 380, 30, 390))])
        self.assertEqual(pdfium_tokens(page), [Token("A", 10, 10, 30, 20)])

    def test_glyph_on_visible_edges_remains_inside_fractional_page_dimensions(self):
        page = Page((3.25, 4.5, 203.3736, 304.6236), [("X", (3.25, 4.5, 203.3736, 304.6236))])
        token, = pdfium_tokens(page)
        self.assertEqual((token.x0, token.top), (0, 0))
        self.assertEqual((token.x1, token.bottom), page.size)

    def test_fully_invisible_glyphs_are_rejected_even_when_separated_by_whitespace(self):
        for outside in ((-10, 50, 0, 60), (200, 50, 210, 60), (20, -10, 30, 0), (20, 300, 30, 310)):
            page = Page(glyphs=[("1", (20, 250, 25, 260)), (" ", None), ("X", outside),
                                (" ", None), ("2", (26, 250, 31, 260))])
            with self.subTest(outside=outside), self.assertRaisesRegex(EvidenceError, "Clipped"):
                pdfium_tokens(page)
            self.assertTrue(page.text.closed)

    def test_fully_clipped_sign_decimal_percent_or_digit_cannot_change_visible_number(self):
        cases = [
            [("-", (-5, 250, 0, 260)), ("1", (0, 250, 5, 260)), ("2", (6, 250, 11, 260))],
            [(".", (-2, 250, 0, 260)), ("5", (0, 250, 5, 260))],
            [("1", (188, 250, 193, 260)), ("2", (194, 250, 199, 260)), ("%", (200, 250, 205, 260))],
            [("1", (195, 250, 200, 260)), ("2", (200, 250, 205, 260))],
            [("1", (20, 250, 25, 260)), ("0", (200, 250, 205, 260)), ("2", (26, 250, 31, 260))],
            [("-", (-5, 250, 0, 260)), (" ", None), ("1", (0, 250, 5, 260))],
            [("-", (-5, 250, 0, 260)), ("", None), ("1", (0, 250, 5, 260))],
        ]
        for glyphs in cases:
            page = Page(glyphs=glyphs)
            with self.subTest(text="".join(g[0] for g in glyphs)), self.assertRaisesRegex(EvidenceError, "Clipped PDF glyph"):
                pdfium_tokens(page)
            self.assertTrue(page.text.closed)

    def test_partly_clipped_nonspace_glyphs_fail_closed_on_all_edges(self):
        for clipped in ((-1, 50, 5, 60), (195, 50, 201, 60), (20, -1, 30, 5), (20, 295, 30, 301)):
            page = Page(glyphs=[("(", clipped), ("1", (20, 250, 25, 260))])
            with self.subTest(clipped=clipped), self.assertRaisesRegex(EvidenceError, "Clipped"):
                pdfium_tokens(page)
            self.assertTrue(page.text.closed)

    def test_rotation_is_rejected_before_opening_textpage(self):
        for rotation in (90, 180, 270, -90, None):
            page = Page(rotation=rotation)
            with self.subTest(rotation=rotation), self.assertRaisesRegex(EvidenceError, "Rotated"):
                pdfium_tokens(page)
            self.assertFalse(page.text_opened)

    def test_invalid_visible_box_or_render_dimensions_are_rejected_before_text(self):
        cases = [Page((0, 0, 0, 300)), Page((20, 0, 10, 300)), Page((0, 0, float("nan"), 300)),
                 Page(size=(float("inf"), 300)), Page(size=(-1, 300)), Page(size=(201, 300)),
                 Page(size=(200, 301)), Page(size=(None, 300))]
        for page in cases:
            with self.subTest(bbox=page.bbox, size=page.size), self.assertRaises(EvidenceError):
                pdfium_tokens(page)
            self.assertFalse(page.text_opened)

    def test_invalid_glyph_rectangles_and_library_errors_always_close_textpage(self):
        for bounds in ((20, 30, 20, 40), (20, 40, 30, 40), (30, 30, 20, 40),
                       (20, 30, float("nan"), 40), (20, 30, 40), RuntimeError("PDF read failed")):
            page = Page(glyphs=[("1", bounds)])
            with self.subTest(bounds=bounds), self.assertRaises((EvidenceError, RuntimeError)):
                pdfium_tokens(page)
            self.assertTrue(page.text.closed)

    def test_horizontal_and_vertical_token_breaks_are_unchanged(self):
        page = Page((10, 20, 210, 320), [("A", (20, 280, 25, 290)), ("B", (29, 280, 34, 290)),
                                       ("C", (35, 250, 40, 260))])
        self.assertEqual([t.text for t in pdfium_tokens(page)], ["A", "B", "C"])

    def test_whitespace_is_safe_but_empty_character_slots_are_rejected(self):
        page = Page(glyphs=[("\n", None), (" ", None)])
        self.assertEqual(pdfium_tokens(page), [])
        self.assertTrue(page.text.closed)
        unreadable = Page(glyphs=[("1", (20, 250, 25, 260)), ("", None), ("2", (26, 250, 31, 260))])
        with self.assertRaisesRegex(EvidenceError, "Unreadable PDF character"):
            pdfium_tokens(unreadable)
        self.assertTrue(unreadable.text.closed)


if __name__ == "__main__":
    unittest.main()

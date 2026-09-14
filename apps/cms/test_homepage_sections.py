from django.test import TestCase

from apps.cms import homepage_sections as hs


class EffectiveOrderTests(TestCase):
    def test_empty_saved_is_the_canonical_default_order(self):
        self.assertEqual(hs.effective_order("default", []), hs.section_keys_for_skin("default"))
        self.assertEqual(hs.effective_order("default", None), hs.section_keys_for_skin("default"))

    def test_saved_order_is_used_as_is_when_complete(self):
        saved = list(reversed(hs.section_keys_for_skin("default")))
        self.assertEqual(hs.effective_order("default", saved), saved)

    def test_missing_keys_are_appended_in_canonical_order(self):
        saved = ["testimonials"]
        result = hs.effective_order("default", saved)
        self.assertEqual(result[0], "testimonials")
        self.assertEqual(set(result), set(hs.section_keys_for_skin("default")))
        self.assertEqual(len(result), len(hs.section_keys_for_skin("default")))

    def test_unknown_or_stale_keys_are_dropped(self):
        result = hs.effective_order("default", ["not_a_real_section", "promo"])
        self.assertNotIn("not_a_real_section", result)
        self.assertEqual(result[0], "promo")

    def test_unknown_skin_falls_back_to_default_catalogue(self):
        self.assertEqual(hs.sections_for_skin("bloom"), hs.sections_for_skin("default"))


class MoveTests(TestCase):
    def test_move_up_swaps_with_previous(self):
        order = hs.effective_order("default", [])
        second = order[1]
        moved = hs.move("default", [], second, "up")
        self.assertEqual(moved[0], second)

    def test_move_down_swaps_with_next(self):
        order = hs.effective_order("default", [])
        first = order[0]
        moved = hs.move("default", [], first, "down")
        self.assertEqual(moved[1], first)

    def test_move_up_at_top_is_a_noop(self):
        order = hs.effective_order("default", [])
        moved = hs.move("default", [], order[0], "up")
        self.assertEqual(moved, order)

    def test_move_down_at_bottom_is_a_noop(self):
        order = hs.effective_order("default", [])
        moved = hs.move("default", [], order[-1], "down")
        self.assertEqual(moved, order)

    def test_move_unknown_key_is_a_noop(self):
        order = hs.effective_order("default", [])
        moved = hs.move("default", [], "not_a_real_key", "up")
        self.assertEqual(moved, order)

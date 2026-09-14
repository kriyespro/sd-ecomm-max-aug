from django.test import TestCase

from apps.projects.models import Project
from apps.projects.verticals import wants_jewellery_sizes, wants_size_color


class VerticalSizeBuilderTests(TestCase):
    def _project(self, vertical):
        return Project.objects.create(
            name="V", status="active", feature_flags={"vertical": vertical},
        )

    def test_clothing_and_fashion_want_size_color_but_not_jewellery_labelling(self):
        for vertical in ("clothing", "fashion"):
            p = self._project(vertical)
            self.assertTrue(wants_size_color(p))
            self.assertFalse(wants_jewellery_sizes(p))

    def test_jewellery_wants_size_color_with_jewellery_labelling(self):
        p = self._project("jewellery")
        self.assertTrue(wants_size_color(p))
        self.assertTrue(wants_jewellery_sizes(p))

    def test_fmcg_wants_neither(self):
        p = self._project("fmcg")
        self.assertFalse(wants_size_color(p))
        self.assertFalse(wants_jewellery_sizes(p))

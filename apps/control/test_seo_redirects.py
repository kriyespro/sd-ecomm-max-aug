"""/admin/seo/redirects/ + /admin/seo/meta/: path normalization so what a
merchant types actually matches request.path later (see
apps.core.views.not_found / apps.seo.services.meta_for / sitemap_entries)."""

from django.test import TestCase

from apps.control.forms import RedirectForm, SeoMetaForm
from apps.projects.models import Project


class PathNormalizationTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="NormCo", status="active", currency="INR")

    def test_redirect_paths_get_leading_and_trailing_slash(self):
        form = RedirectForm(
            data={"from_path": "old-page", "to_path": "new-page", "is_permanent": True, "is_active": True},
            project=self.project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["from_path"], "/old-page/")
        self.assertEqual(form.cleaned_data["to_path"], "/new-page/")

    def test_redirect_to_an_absolute_url_is_left_alone(self):
        form = RedirectForm(
            data={"from_path": "old-page", "to_path": "https://elsewhere.test/x",
                  "is_permanent": True, "is_active": True},
            project=self.project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["to_path"], "https://elsewhere.test/x")

    def test_redirect_to_itself_is_rejected(self):
        form = RedirectForm(
            data={"from_path": "/same/", "to_path": "same", "is_permanent": True, "is_active": True},
            project=self.project,
        )
        self.assertFalse(form.is_valid())

    def test_seo_meta_path_normalized(self):
        form = SeoMetaForm(data={"path": "p/foo", "robots": "noindex,follow"}, project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["path"], "/p/foo/")

from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase, override_settings

from apps.categories.models import Category
from apps.cms.models import Menu, MenuItem, Page, PublishStatus
from apps.core.store_resolver import bust_project_chrome
from apps.projects.models import Project
from apps.projects.models import Domain
from apps.shopfront.context import base_context


class PrimaryNavTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="NavShop", status="active")
        self.cat = Category.objects.create(project=self.project, name="Hats", is_active=True)
        bust_project_chrome(self.project.pk)

    def _nav(self):
        bust_project_chrome(self.project.pk)
        req = RequestFactory().get("/")
        req.project = self.project
        req.user = AnonymousUser()
        req.session = SessionStore()
        req.skin_slug = "default"
        return base_context(req, self.project)["primary_nav"]

    def test_falls_back_to_categories_without_a_menu(self):
        nav = self._nav()
        self.assertEqual([n["label"] for n in nav], ["Hats"])
        self.assertIn("?category=hats", nav[0]["url"])

    def test_uses_main_menu_when_it_has_items(self):
        page = Page.objects.create(
            project=self.project, title="About Us", status=PublishStatus.PUBLISHED,
        )
        menu = Menu.objects.create(project=self.project, name="Header", location="main")
        MenuItem.objects.create(menu=menu, label="Shop", link_type="category",
                                category=self.cat, order=1)
        MenuItem.objects.create(menu=menu, label="About", link_type="page",
                                page=page, order=2)
        MenuItem.objects.create(menu=menu, label="Blog", link_type="external",
                                url="https://blog.example.com", open_in_new_tab=True, order=3)

        nav = self._nav()
        self.assertEqual([n["label"] for n in nav], ["Shop", "About", "Blog"])
        self.assertIn("?category=hats", nav[0]["url"])
        self.assertEqual(nav[1]["url"], "/app/page/about-us/")
        self.assertEqual(nav[2]["url"], "https://blog.example.com")
        self.assertTrue(nav[2]["new_tab"])

    def test_nested_children_and_inactive_items_excluded(self):
        menu = Menu.objects.create(project=self.project, name="Header", location="main")
        parent = MenuItem.objects.create(menu=menu, label="Catalog", link_type="url",
                                         url="/shop/", order=1)
        MenuItem.objects.create(menu=menu, label="Hats", link_type="category",
                                category=self.cat, parent=parent, order=1)
        MenuItem.objects.create(menu=menu, label="Hidden", link_type="url",
                                url="/x/", parent=parent, order=2, is_active=False)

        nav = self._nav()
        self.assertEqual(len(nav), 1)
        self.assertEqual([c["label"] for c in nav[0]["children"]], ["Hats"])

    def test_inactive_menu_is_ignored(self):
        menu = Menu.objects.create(project=self.project, name="Header",
                                   location="main", is_active=False)
        MenuItem.objects.create(menu=menu, label="Nope", link_type="url", url="/n/")
        self.assertEqual([n["label"] for n in self._nav()], ["Hats"])

    def test_footer_menu_does_not_drive_the_main_nav(self):
        menu = Menu.objects.create(project=self.project, name="Footer", location="footer")
        MenuItem.objects.create(menu=menu, label="Terms", link_type="url", url="/terms/")
        self.assertEqual([n["label"] for n in self._nav()], ["Hats"])


@override_settings(ALLOWED_HOSTS=["*"])
class PrimaryNavRenderTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="RenderNav", status="active", feature_flags={"onboarded": True},
        )
        Domain.objects.create(project=self.project, host="nav.test", is_verified=True)
        Category.objects.create(project=self.project, name="Rings", is_active=True)

    def test_menu_labels_render_in_the_storefront_header(self):
        menu = Menu.objects.create(project=self.project, name="Header", location="main")
        MenuItem.objects.create(menu=menu, label="Lookbook", link_type="external",
                                url="https://example.com/lookbook", order=1)
        bust_project_chrome(self.project.pk)

        resp = self.client.get("/", HTTP_HOST="nav.test")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Lookbook")

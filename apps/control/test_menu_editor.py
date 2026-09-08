from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.models import Membership, StoreRole
from apps.categories.models import Category
from apps.cms.models import Menu, MenuItem, Page, PublishStatus
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.projects.models import Project

User = get_user_model()


class MenuEditorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("m", "m@t.test", "pw", is_staff=True)
        self.project = Project.objects.create(
            name="MenuCo", feature_flags={"onboarded": True, "vertical": "general"},
        )
        Membership.objects.create(user=self.user, project=self.project,
                                  role=StoreRole.OWNER, is_active=True)
        self.client.force_login(self.user)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

        self.menu = Menu.objects.create(project=self.project, name="Header", location="main")
        self.p1 = Page.objects.create(project=self.project, title="About",
                                      status=PublishStatus.PUBLISHED)
        self.p2 = Page.objects.create(project=self.project, title="Contact",
                                      status=PublishStatus.PUBLISHED)
        self.c1 = Category.objects.create(project=self.project, name="Shoes", is_active=True)
        self.c2 = Category.objects.create(project=self.project, name="Bags", is_active=True)

    def _add(self, **data):
        return self.client.post(
            f"/admin/cms/menus/{self.menu.pk}/items/add/", data
        )

    def _move(self, item, action):
        return self.client.post(
            f"/admin/cms/menus/{self.menu.pk}/items/{item.pk}/move/", {"action": action}
        )

    def _labels(self):
        return list(
            self.menu.items.order_by("parent_id", "order").values_list("label", flat=True)
        )

    def test_detail_page_renders(self):
        r = self.client.get(f"/admin/cms/menus/{self.menu.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Add pages")
        self.assertContains(r, "Add categories")
        self.assertContains(r, "Shoes")

    def test_quick_add_pages_sets_labels_and_order(self):
        self._add(kind="pages", page_ids=[self.p1.pk, self.p2.pk])
        items = list(self.menu.items.order_by("order"))
        self.assertEqual([i.label for i in items], ["About", "Contact"])
        self.assertEqual([i.link_type for i in items], ["page", "page"])
        self.assertEqual([i.order for i in items], [1, 2])

    def test_quick_add_category_under_parent_makes_a_dropdown(self):
        self._add(kind="categories", category_ids=[self.c1.pk])
        parent = self.menu.items.get(label="Shoes")
        self._add(kind="categories", category_ids=[self.c2.pk], parent=parent.pk)

        child = self.menu.items.get(label="Bags")
        self.assertEqual(child.parent_id, parent.pk)

    def test_reorder_up_down(self):
        self._add(kind="pages", page_ids=[self.p1.pk, self.p2.pk])
        second = self.menu.items.get(label="Contact")
        self._move(second, "up")
        self.assertEqual(self._labels(), ["Contact", "About"])
        self._move(second, "down")
        self.assertEqual(self._labels(), ["About", "Contact"])

    def test_indent_then_outdent(self):
        self._add(kind="pages", page_ids=[self.p1.pk, self.p2.pk])
        contact = self.menu.items.get(label="Contact")

        self._move(contact, "indent")
        contact.refresh_from_db()
        self.assertEqual(contact.parent_id, self.menu.items.get(label="About").pk)

        self._move(contact, "outdent")
        contact.refresh_from_db()
        self.assertIsNone(contact.parent_id)

    def test_delete_parent_removes_children(self):
        self._add(kind="categories", category_ids=[self.c1.pk])
        parent = self.menu.items.get(label="Shoes")
        self._add(kind="categories", category_ids=[self.c2.pk], parent=parent.pk)

        self.client.post(
            f"/admin/cms/menus/{self.menu.pk}/items/{parent.pk}/delete/"
        )
        self.assertEqual(self.menu.items.count(), 0)

    def test_custom_link_needs_label_and_url(self):
        self._add(kind="link", label="Sale", url="")
        self.assertEqual(self.menu.items.count(), 0)
        self._add(kind="link", label="Sale", url="/shop/?sort=discount")
        self.assertEqual(self.menu.items.get().link_type, "url")

    def test_indent_lifts_grandchildren_to_top(self):
        # a -> [b (child)], then nest c under... build: a, c ; indent b already child of a
        self._add(kind="pages", page_ids=[self.p1.pk])          # About  (order 1)
        self._add(kind="categories", category_ids=[self.c1.pk])  # Shoes  (order 2)
        self._add(kind="categories", category_ids=[self.c2.pk], parent=self.menu.items.get(label="Shoes").pk)
        shoes = self.menu.items.get(label="Shoes")
        # nest Shoes under About — its child Bags must pop to top level
        self._move(shoes, "indent")
        bags = self.menu.items.get(label="Bags")
        self.assertIsNone(bags.parent_id)

"""Marketing -> Store credit (/admin/marketing/credits/): loyalty settings
form + gift-card issuance. Service-level logic is covered by apps.credits.tests."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.accounts.models import Membership, StoreRole
from apps.control.mixins import ACTIVE_PROJECT_SESSION_KEY
from apps.credits.models import GiftCard, LoyaltyProgram
from apps.projects.models import Project

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["*"])
class CreditSettingsTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(
            name="CreditAdminCo", status="active", feature_flags={"onboarded": True},
        )
        self.owner = User.objects.create_user("co", "co@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=self.owner, role=StoreRole.OWNER)
        self.client.force_login(self.owner)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()

    def test_screen_reachable(self):
        resp = self.client.get("/admin/marketing/credits/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Store credit")

    def test_saving_turns_loyalty_on(self):
        resp = self.client.post("/admin/marketing/credits/", {
            "is_active": "on", "earn_percent": "5",
        })
        self.assertRedirects(resp, "/admin/marketing/credits/")
        program = LoyaltyProgram.objects.get(project=self.project)
        self.assertTrue(program.is_active)
        self.assertEqual(program.earn_percent, Decimal("5"))

    def test_issuing_a_gift_card_creates_it(self):
        resp = self.client.post("/admin/marketing/credits/gift-card/", {
            "value": "250", "issued_to_email": "friend@t.test",
        })
        self.assertRedirects(resp, "/admin/marketing/credits/")
        card = GiftCard.objects.get(project=self.project)
        self.assertEqual(card.value, Decimal("250"))
        self.assertEqual(card.issued_to_email, "friend@t.test")

    def test_issuing_with_a_zero_value_is_rejected(self):
        resp = self.client.post("/admin/marketing/credits/gift-card/", {"value": "0"})
        self.assertRedirects(resp, "/admin/marketing/credits/")
        self.assertFalse(GiftCard.objects.filter(project=self.project).exists())

    def test_nav_lists_the_screen_under_marketing(self):
        body = self.client.get("/admin/products/").content.decode()
        self.assertIn("/admin/marketing/credits/", body)
        self.assertIn("Store credit", body)

    def test_staff_without_manage_role_is_denied(self):
        staff = User.objects.create_user("cs", "cs@t.test", "pw", is_staff=True)
        Membership.objects.create(project=self.project, user=staff, role=StoreRole.STAFF)
        self.client.force_login(staff)
        s = self.client.session
        s[ACTIVE_PROJECT_SESSION_KEY] = self.project.pk
        s.save()
        resp = self.client.get("/admin/marketing/credits/")
        self.assertEqual(resp.status_code, 403)

    def test_gift_cards_list_on_the_screen(self):
        GiftCard.objects.create(project=self.project, code="ZZZTOP1", value=Decimal("300"))
        resp = self.client.get("/admin/marketing/credits/")
        self.assertContains(resp, "ZZZTOP1")

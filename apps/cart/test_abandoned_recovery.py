"""Abandoned-cart recovery — only reaches a cart with a known email (a
registered shopper, today; a guest with no captured email can't be
reached yet — that's a real gap, not a bug), sends once, never twice."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.cart.tasks import send_abandoned_cart_emails_task
from apps.catalog.models import Product
from apps.notifications.models import Event, NotificationLog
from apps.projects.models import Domain, Project

User = get_user_model()


class AbandonedCartRecoveryTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name="RecoverCo", status="active", currency="INR")
        Domain.objects.create(project=self.project, host="recover.test", is_verified=True)
        self.product = Product.objects.create(
            project=self.project, title="Widget", price=Decimal("499"), status="active",
        )

    def _idle_cart(self, *, user=None, email="", hours_ago=4):
        cart = Cart.objects.create(project=self.project, user=user, email=email)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2, unit_price=Decimal("499"))
        Cart.objects.filter(pk=cart.pk).update(
            updated_at=timezone.now() - timedelta(hours=hours_ago)
        )
        return Cart.objects.get(pk=cart.pk)

    def test_registered_shopper_gets_recovered(self):
        user = User.objects.create_user("shopper", "shopper@t.test", "pw")
        cart = self._idle_cart(user=user)
        with self.captureOnCommitCallbacks(execute=True):
            sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 1)
        cart.refresh_from_db()
        self.assertIsNotNone(cart.recovery_sent_at)
        log = NotificationLog.objects.get(project=self.project, event=Event.CART_ABANDONED)
        self.assertEqual(log.to_address, "shopper@t.test")
        self.assertIn("998", log.body)  # 2 x 499

    def test_never_sent_twice(self):
        user = User.objects.create_user("shopper2", "shopper2@t.test", "pw")
        self._idle_cart(user=user)
        send_abandoned_cart_emails_task()
        second_run = send_abandoned_cart_emails_task()
        self.assertEqual(second_run, 0)
        self.assertEqual(
            NotificationLog.objects.filter(project=self.project, event=Event.CART_ABANDONED).count(),
            1,
        )

    def test_guest_cart_with_no_email_is_skipped(self):
        self._idle_cart(user=None, email="")
        sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 0)

    def test_guest_cart_with_captured_email_is_recovered(self):
        self._idle_cart(user=None, email="guest@t.test")
        sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 1)
        log = NotificationLog.objects.get(project=self.project, event=Event.CART_ABANDONED)
        self.assertEqual(log.to_address, "guest@t.test")

    def test_too_recent_is_not_touched_yet(self):
        user = User.objects.create_user("shopper3", "shopper3@t.test", "pw")
        self._idle_cart(user=user, hours_ago=1)  # under the 3h idle threshold
        sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 0)

    def test_converted_cart_is_never_touched(self):
        user = User.objects.create_user("shopper4", "shopper4@t.test", "pw")
        cart = self._idle_cart(user=user)
        cart.converted_order_id = 1
        cart.save(update_fields=["converted_order_id"])
        sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 0)

    def test_empty_cart_is_never_touched(self):
        cart = Cart.objects.create(project=self.project, user=None)
        Cart.objects.filter(pk=cart.pk).update(
            updated_at=timezone.now() - timedelta(hours=4)
        )
        sent = send_abandoned_cart_emails_task()
        self.assertEqual(sent, 0)

    def test_store_with_no_domain_yet_is_skipped_not_crashed(self):
        no_domain = Project.objects.create(name="NoDomainCo", status="active", currency="INR")
        user = User.objects.create_user("shopper5", "shopper5@t.test", "pw")
        cart = Cart.objects.create(project=no_domain, user=user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1, unit_price=Decimal("499"))
        Cart.objects.filter(pk=cart.pk).update(
            updated_at=timezone.now() - timedelta(hours=4)
        )
        sent = send_abandoned_cart_emails_task()  # must not raise
        self.assertEqual(sent, 0)

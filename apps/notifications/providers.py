"""Notification provider abstraction. No gateway is hardcoded into send logic."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


class ProviderError(Exception):
    pass


class NotificationProvider:
    key = ""

    def __init__(self, config=None):
        self.config = config or {}

    def send(self, *, to, subject, body, from_address="", html_body=""):
        raise NotImplementedError


class DjangoEmailProvider(NotificationProvider):
    """Sends through Django's configured EMAIL_BACKEND (console in dev, SMTP/SES
    in prod via settings). ``email_config`` can override the from address.
    """

    key = "django"

    def send(self, *, to, subject, body, from_address="", html_body=""):
        sender = from_address or getattr(settings, "DEFAULT_FROM_EMAIL", "webmaster@localhost")
        msg = EmailMultiAlternatives(subject, body, sender, [to])
        if html_body:
            msg.attach_alternative(html_body, "text/html")
        msg.send(fail_silently=False)
        return {"provider": self.key}


class ConsoleProvider(NotificationProvider):
    key = "console"

    def send(self, *, to, subject, body, from_address="", html_body=""):
        print(f"[notification] to={to} subject={subject!r}\n{body}")
        return {"provider": self.key}


class NullSMSProvider(NotificationProvider):
    """Placeholder until a real SMS/WhatsApp gateway is wired (project.md: later)."""

    key = "null"

    def send(self, *, to, subject, body, from_address="", html_body=""):
        return {"provider": self.key, "note": "SMS provider not configured"}


_EMAIL = {DjangoEmailProvider.key: DjangoEmailProvider, ConsoleProvider.key: ConsoleProvider}
_SMS = {NullSMSProvider.key: NullSMSProvider}


def email_provider(key, config=None):
    return _EMAIL.get(key or "django", DjangoEmailProvider)(config)


def sms_provider(key, config=None):
    return _SMS.get(key or "null", NullSMSProvider)(config)

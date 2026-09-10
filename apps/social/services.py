"""Connect flow + share logic. Best-effort — a failure is logged, never raised
into a product save."""

import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.html import strip_tags

from . import clients, oauth
from .models import ShareStatus, SocialAccount, SocialPost, SocialProvider

logger = logging.getLogger(__name__)


def ensure_fresh(account):
    """Refresh the access token if it expires within 5 minutes."""
    exp = account.token_expires_at
    if exp and exp - timezone.now() > timedelta(minutes=5):
        return account
    if not account.refresh_token:
        return account
    tok = oauth.refresh(
        account.provider, refresh_token=account.refresh_token,
        client_id=account.client_id, client_secret=account.client_secret,
    )
    account.access_token = tok["access_token"]
    account.refresh_token = tok["refresh_token"] or account.refresh_token
    account.token_expires_at = timezone.datetime.fromtimestamp(
        tok["expires_at"], tz=timezone.get_current_timezone(),
    )
    account.save(update_fields=["access_token", "refresh_token", "token_expires_at"])
    return account


def store_tokens(account, tok):
    account.access_token = tok["access_token"]
    account.refresh_token = tok["refresh_token"] or account.refresh_token
    account.scope = tok["scope"]
    account.token_expires_at = timezone.datetime.fromtimestamp(
        tok["expires_at"], tz=timezone.get_current_timezone(),
    )
    account.save()


def load_targets(account):
    """Boards (Pinterest) / locations (GBP) the connected account can post to."""
    ensure_fresh(account)
    if account.provider == SocialProvider.PINTEREST:
        user = clients.pinterest_user(account.access_token)
        account.account_name = user.get("username", "")
        account.save(update_fields=["account_name"])
        return clients.pinterest_boards(account.access_token)
    return clients.gbp_locations(account.access_token)


def _product_payload(product, base_url):
    link = f"{base_url.rstrip('/')}/p/{product.slug}/"
    title = product.title
    desc = strip_tags(product.short_description or product.description or product.title)[:480]
    image = ""
    for im in product.images.all():
        if im.image:
            url = im.image.url
            image = url if url.startswith("http") else base_url.rstrip("/") + url
            if im.is_primary:
                break
    return title, desc, link, image


def share_product(account, product):
    """Post one product to one channel. Returns the ``SocialPost`` row.
    Raises ``SocialAPIError`` only when a retry could help."""
    post, created = SocialPost.objects.get_or_create(
        project=account.project, provider=account.provider, product=product,
    )
    if not created and post.status == ShareStatus.POSTED:
        return post

    if not account.ready:
        post.status = ShareStatus.SKIPPED
        post.error = "Channel not connected."
        post.save()
        return post

    base_url = getattr(product.project, "public_url", "") or ""
    title, desc, link, image = _product_payload(product, base_url)
    if not image:
        post.status = ShareStatus.SKIPPED
        post.error = "Product has no image."
        post.save()
        return post

    try:
        ensure_fresh(account)
        if account.provider == SocialProvider.PINTEREST:
            ext, url = clients.pinterest_create_pin(
                account.access_token, board_id=account.target_id,
                title=title, description=desc, link=link, image_url=image,
            )
        else:
            summary = f"{title}\n\n{desc}"
            ext, url = clients.gbp_create_post(
                account.access_token, location=account.target_id,
                summary=summary, link=link, image_url=image,
            )
        post.external_id, post.url = ext, url
        post.status = ShareStatus.POSTED
        post.error = ""
        if account.last_error:
            account.last_error = ""
            account.save(update_fields=["last_error"])
    except clients.SocialAPIError as exc:
        post.status = ShareStatus.FAILED
        post.error = str(exc)[:255]
        account.last_error = str(exc)[:255]
        account.save(update_fields=["last_error"])
        post.save()
        if exc.retryable:
            raise
        return post
    except oauth.OAuthError as exc:
        post.status = ShareStatus.FAILED
        post.error = f"token refresh failed: {exc}"[:255]
        post.save()
        return post

    post.save()
    return post

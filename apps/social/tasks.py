"""Async social posting — never on the request/save path."""

import logging

from celery import shared_task

from .clients import SocialAPIError

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.social.tasks.share_product_task",
    bind=True, max_retries=3, default_retry_delay=120,
)
def share_product_task(self, account_id, product_id):
    from apps.catalog.models import Product

    from .models import SocialAccount
    from .services import share_product

    account = SocialAccount.objects.filter(pk=account_id).first()
    product = Product.objects.filter(pk=product_id).first()
    if account is None or product is None:
        return "gone"
    try:
        post = share_product(account, product)
    except SocialAPIError as exc:
        raise self.retry(exc=exc)
    logger.info("social share %s product %s -> %s",
                account.provider, product_id, post.status)
    return post.status

"""IndexNow — tell Bing / Yandex / Seznam a URL changed. Google ignores it and
relies on the sitemap in robots.txt instead."""

import json
import logging
import urllib.error
import urllib.request

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.indexnow.org/indexnow"


@shared_task(name="apps.seo.tasks.indexnow_submit")
def indexnow_submit(host, urls):
    key = settings.SEO_INDEXNOW_KEY
    if not key or not host or not urls:
        return "skipped"
    body = json.dumps({
        "host": host,
        "key": key,
        "keyLocation": f"https://{host}/{key}.txt",
        "urlList": list(urls)[:10000],
    }).encode()
    req = urllib.request.Request(
        _ENDPOINT, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return f"ok {resp.status}"
    except urllib.error.HTTPError as exc:
        # 422 = key/host mismatch, 403 = key not found at keyLocation
        logger.warning("indexnow %s: %s", exc.code, exc.read()[:200])
        return f"http {exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.warning("indexnow unreachable: %s", exc)
        return "unreachable"

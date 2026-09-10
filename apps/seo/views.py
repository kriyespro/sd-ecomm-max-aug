from django.conf import settings
from django.http import Http404, HttpResponse
from django.views import View


class IndexNowKeyView(View):
    """Serves the IndexNow key at ``/<key>.txt`` on every storefront host so the
    IndexNow API can verify ownership. Route is only registered when a key is
    configured."""

    def get(self, request):
        key = settings.SEO_INDEXNOW_KEY
        if not key:
            raise Http404
        return HttpResponse(key, content_type="text/plain")

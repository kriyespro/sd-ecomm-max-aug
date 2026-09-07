"""Backfill UGCVideo.youtube_id from youtube_url.

The reel iframe renders ``src="{{ v.embed_url }}"`` and embed_url is empty
whenever youtube_id is blank — a broken black box on the storefront. Rows added
before the id was parsed on every save (or before the URL regex learned the
``watch?...&v=`` / ``/live/`` shapes) can carry a good URL but a blank id.
Recompute it here; new writes stay covered by UGCVideo.save().
"""

from django.db import migrations


def _backfill(apps, schema_editor):
    from apps.cms.models import _extract_youtube_id

    UGCVideo = apps.get_model("cms", "UGCVideo")
    for pk, url, current in UGCVideo.objects.values_list("pk", "youtube_url", "youtube_id"):
        parsed = _extract_youtube_id(url)
        if parsed and parsed != current:
            UGCVideo.objects.filter(pk=pk).update(youtube_id=parsed)


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0009_ugcvideo"),
    ]

    operations = [
        migrations.RunPython(_backfill, migrations.RunPython.noop),
    ]

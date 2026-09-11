"""Per-store AI key management + the rotation that spreads calls across a
store's keys and OpenRouter's free models so no single one gets rate-limited
or flagged for hammering the free tier.
"""

import json
import logging

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from . import openrouter
from .models import MAX_KEYS_PER_STORE, AiProviderKey

logger = logging.getLogger(__name__)

# Bounded so a "Generate" click never hangs: at most this many (key, model)
# attempts before giving up and telling the merchant to try again.
_MAX_ATTEMPTS = 10
_MODEL_POOL = 5

# Some "$0-priced" free models on OpenRouter still 400/403/404 every plain
# chat-completions call (e.g. an agentic-harness-only or deprecated model) —
# that's a property of the MODEL, not of whichever key hit it. Once seen,
# skip it platform-wide for a while instead of burning every store's attempt
# budget on the same dead model.
_DEAD_MODEL_TTL = 1800
_MODEL_LEVEL_STATUSES = {400, 403, 404}


def _dead_model_key(model):
    return f"ai:openrouter:dead_model:{model}"


class AiError(Exception):
    """Expected, user-facing problem — no key, all attempts exhausted."""


def add_key(*, project, api_key, label="", actor=None, request=None):
    api_key = (api_key or "").strip()
    if not api_key:
        raise AiError("Paste an OpenRouter API key.")
    if not api_key.startswith("sk-or-"):
        raise AiError("That doesn't look like an OpenRouter key — it should start with “sk-or-”.")
    count = AiProviderKey.objects.filter(project=project).count()
    if count >= MAX_KEYS_PER_STORE:
        raise AiError(f"Limit is {MAX_KEYS_PER_STORE} keys per store — remove one first.")

    from apps.core.models import AuditLog
    from apps.core.services import record_audit

    row = AiProviderKey.objects.create(
        project=project, api_key=api_key, label=(label or "").strip() or f"Key {count + 1}",
    )
    record_audit(actor=actor, project=project, action=AuditLog.Action.CREATE,
                 target=row, changes={"label": row.label}, request=request)
    return row


def remove_key(*, project, key_id, actor=None, request=None):
    from apps.core.models import AuditLog
    from apps.core.services import record_audit

    row = AiProviderKey.objects.filter(project=project, pk=key_id).first()
    if row is None:
        return
    record_audit(actor=actor, project=project, action=AuditLog.Action.DELETE,
                 target=row, request=request)
    row.delete()


def toggle_key(*, project, key_id):
    row = AiProviderKey.objects.filter(project=project, pk=key_id).first()
    if row is None:
        return None
    row.is_active = not row.is_active
    row.save(update_fields=["is_active"])
    return row


def _rotation_order(project):
    """Least-recently-used active key first — a simple round robin."""
    return list(
        AiProviderKey.objects.filter(project=project, is_active=True)
        .order_by(F("last_used_at").asc(nulls_first=True))
    )


def generate(*, project, system_prompt, user_prompt, max_tokens=800):
    """Returns ``(text, model_id)``. Model is the outer loop, key the inner one:
    a model-level error (400/403/404 — the model itself rejects plain chat
    calls) blacklists that model for everyone and moves to the next model
    instead of wasting every key on it; a key-level error (401/429/5xx) just
    tries the next key against the same, presumably-fine, model."""
    keys = _rotation_order(project)
    if not keys:
        raise AiError("Add an OpenRouter API key first (Settings → AI).")

    candidates = openrouter.ranked_free_models()[:_MODEL_POOL] or ["openrouter/auto"]
    models = [m for m in candidates if not cache.get(_dead_model_key(m))] or candidates
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]

    attempts = 0
    last_error = None
    for model in models:
        if attempts >= _MAX_ATTEMPTS:
            break
        for key in keys:
            if attempts >= _MAX_ATTEMPTS:
                break
            attempts += 1
            try:
                text = openrouter.chat(api_key=key.api_key, model=model, messages=messages,
                                       max_tokens=max_tokens)
            except openrouter.OpenRouterError as exc:
                last_error = exc
                AiProviderKey.objects.filter(pk=key.pk).update(last_error=str(exc)[:255])
                logger.info("ai key %s failed on %s: %s", key.pk, model, exc)
                if exc.status in _MODEL_LEVEL_STATUSES:
                    cache.set(_dead_model_key(model), True, _DEAD_MODEL_TTL)
                    break  # this model is broken, not the key — try the next model
                continue
            AiProviderKey.objects.filter(pk=key.pk).update(
                last_used_at=timezone.now(), last_error="", request_count=F("request_count") + 1,
            )
            return text, model

    raise AiError(
        "AI is busy right now — every key hit a snag. Try again in a minute."
        + (f" ({last_error})" if last_error else "")
    )


_PRODUCT_SYSTEM_PROMPT = (
    "You write concise, honest ecommerce product copy for an Indian online "
    "store. Given a short brief, return ONLY a JSON object — no markdown "
    "fences, no commentary — with exactly these keys: "
    '"title" (<=70 chars), "short_description" (<=160 chars, one line), '
    '"description" (2-3 short plain-text paragraphs, no markdown/headings), '
    '"seo_title" (<=60 chars), "seo_description" (<=160 chars), '
    '"tags" (5-8 comma-separated lowercase keywords). '
    "Never invent specific numeric claims (weight, dimensions, certifications) "
    "the brief didn't mention."
)


def generate_product_copy(project, brief):
    brief = (brief or "").strip()
    if not brief:
        raise AiError("Describe the product first.")
    if len(brief) > 500:
        brief = brief[:500]

    user_prompt = f'Store: {project.name}\nBrief: "{brief}"'
    text, model = generate(
        project=project, system_prompt=_PRODUCT_SYSTEM_PROMPT, user_prompt=user_prompt,
    )
    data = _parse_json_object(text)
    return {
        "title": _clip(data.get("title"), 70),
        "short_description": _clip(data.get("short_description"), 160),
        "description": (data.get("description") or "").strip()[:2000],
        "seo_title": _clip(data.get("seo_title"), 60),
        "seo_description": _clip(data.get("seo_description"), 160),
        "tags": _clip(data.get("tags"), 255),
        "model": model,
    }


def _clip(value, length):
    return (value or "").strip()[:length]


def _parse_json_object(text):
    """Free models sometimes wrap JSON in ```json fences or add a stray
    sentence — pull out the first {...} block rather than failing outright."""
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AiError("The AI didn't return usable content — try rephrasing the brief.")
    try:
        return json.loads(raw[start:end + 1])
    except ValueError as exc:
        raise AiError("The AI's response wasn't valid — try again.") from exc

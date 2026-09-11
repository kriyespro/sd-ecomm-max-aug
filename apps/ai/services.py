"""Per-store AI key management + the rotation that spreads calls across a
store's keys and OpenRouter's free models so no single one gets rate-limited
or flagged for hammering the free tier.
"""

import json
import logging
import re
import time

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from . import openrouter
from .models import MAX_KEYS_PER_STORE, AiProviderKey

logger = logging.getLogger(__name__)

# "Generate" is a plain synchronous request — gunicorn (30s) and nginx's
# proxy_read_timeout (30s) both kill it past that, and the client then sees a
# 502/504 HTML page, not our JSON error. So the rotation is bounded by wall
# clock first, attempt count second: stop well before either timeout so a
# clean AiError always makes it back to the browser. Each individual call
# gets a short timeout too, so one hanging attempt can't eat the whole budget.
_TIME_BUDGET_SECONDS = 18
_CALL_TIMEOUT_SECONDS = 7
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


def generate(*, project, system_prompt, user_prompt, max_tokens=800, parse=None):
    """Returns ``(result, model_id)`` — ``result`` is the raw text, or
    ``parse(text)`` when ``parse`` is given. Model is the outer loop, key the
    inner one:
    - a model-level API error (400/403/404 — the model itself rejects plain
      chat calls) blacklists that model for everyone and moves to the next
      model instead of wasting every key on it;
    - a key-level API error (401/429/5xx) tries the next key against the
      same, presumably-fine, model;
    - a successful call whose text fails ``parse`` (a ``ValueError``) is
      treated the same as a key-level failure — some free models just don't
      reliably follow a "JSON only" instruction, so try the next one rather
      than surfacing a parse error the merchant can't act on.
    """
    keys = _rotation_order(project)
    if not keys:
        raise AiError("Add an OpenRouter API key first (Settings → AI).")

    candidates = openrouter.ranked_free_models()[:_MODEL_POOL] or ["openrouter/auto"]
    models = [m for m in candidates if not cache.get(_dead_model_key(m))] or candidates
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}]

    started = time.monotonic()
    attempts = 0
    last_error = None
    for model in models:
        if attempts >= _MAX_ATTEMPTS or time.monotonic() - started > _TIME_BUDGET_SECONDS:
            break
        for key in keys:
            if attempts >= _MAX_ATTEMPTS or time.monotonic() - started > _TIME_BUDGET_SECONDS:
                break
            attempts += 1
            try:
                text = openrouter.chat(api_key=key.api_key, model=model, messages=messages,
                                       max_tokens=max_tokens, timeout=_CALL_TIMEOUT_SECONDS)
            except openrouter.OpenRouterError as exc:
                last_error = exc
                AiProviderKey.objects.filter(pk=key.pk).update(last_error=str(exc)[:255])
                logger.info("ai key %s failed on %s: %s", key.pk, model, exc)
                if exc.status in _MODEL_LEVEL_STATUSES:
                    cache.set(_dead_model_key(model), True, _DEAD_MODEL_TTL)
                    break  # this model is broken, not the key — try the next model
                continue

            if parse is not None:
                try:
                    result = parse(text)
                except ValueError as exc:
                    last_error = exc
                    logger.warning(
                        "ai key %s model %s returned unparseable content: %s | raw=%r",
                        key.pk, model, exc, text[:500],
                    )
                    AiProviderKey.objects.filter(pk=key.pk).update(last_error=f"bad output: {exc}"[:255])
                    continue
            else:
                result = text

            AiProviderKey.objects.filter(pk=key.pk).update(
                last_used_at=timezone.now(), last_error="", request_count=F("request_count") + 1,
            )
            return result, model

    raise AiError(
        "AI is busy right now — every key hit a snag. Try again in a minute."
        + (f" ({last_error})" if last_error else "")
    )


_PRODUCT_SYSTEM_PROMPT = (
    "You write concise, honest ecommerce product copy for an Indian online "
    "store. Given a short brief, respond with ONE JSON object and NOTHING "
    "else: no markdown code fences, no preamble like \"Here you go\", no "
    "explanation before or after. Your entire response must start with { and "
    "end with }. Use exactly these keys: "
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
    data, model = generate(
        project=project, system_prompt=_PRODUCT_SYSTEM_PROMPT, user_prompt=user_prompt,
        max_tokens=1000, parse=_parse_json_object,
    )
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
    """Free models sometimes wrap JSON in ```json fences, add a stray
    sentence, or leave a trailing comma. Raises ``ValueError`` (not
    ``AiError``) on failure — the rotation in ``generate()`` treats that as
    "try the next model", not a final failure the merchant sees."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty response")

    try:
        return json.loads(raw)
    except ValueError:
        pass

    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object found in response")
    candidate = raw[start:end + 1]
    try:
        return json.loads(candidate)
    except ValueError:
        pass

    # One common repair: a trailing comma before a closing brace/bracket.
    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(repaired)
    except ValueError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

"""OpenRouter client — stdlib only. Discovers the free-tier ("$0 per token")
model catalog and ranks it so callers always try the strongest free model
first, with a few solid fallbacks behind it.
"""

import json
import logging
import urllib.error
import urllib.request

from django.core.cache import cache

logger = logging.getLogger(__name__)

_API = "https://openrouter.ai/api/v1"
_MODELS_CACHE_KEY = "ai:openrouter:free_models"
_MODELS_CACHE_TTL = 3600  # OpenRouter's free catalog changes slowly; refetch hourly.

# Substring preferences, strongest first, matched against a free model's id
# (e.g. "meta-llama/llama-3.3-70b-instruct:free"). Not exhaustive — anything
# free that doesn't match one of these still gets used, just ranked below by
# context length.
_PREFERRED = [
    "llama-3.3-70b", "llama-3.1-70b", "deepseek-chat",
    "qwen-2.5-72b", "qwen-2.5-coder-32b", "gemini-2.0-flash", "gemini-flash",
    "mistral-small", "phi-4", "gemma-2-27b", "gemma-2-9b",
]

# Two kinds of free-tier listing that are the wrong tool for "return one JSON
# object of product copy" entirely:
# - reasoning models spend most/all of max_tokens on an internal chain-of-
#   thought before ever emitting the answer, often returning nothing usable;
# - non-text models (music/audio/image/video generation) OpenRouter also
#   lists at $0 during preview — e.g. google/lyria-3-pro-preview turned out
#   to be a MUSIC model and duly returned song lyrics, not JSON.
# Excluded outright rather than just ranked lower.
_EXCLUDE = [
    "-r1", "r1-", "r1:", "qwq", "-o1", "o1-", "-o3", "o3-", "thinking", "reasoning",
    "lyria", "whisper", "tts", "dall-e", "stable-diffusion", "flux", "imagen",
    "veo-", "-veo", "sora", "music", "-audio", "audio-", "image-gen", "-image:",
]

# Used only if OpenRouter's catalog is unreachable — a small set of models
# that have reliably had a free tier.
_FALLBACK_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
]


class OpenRouterError(Exception):
    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


def _is_free(model):
    pricing = model.get("pricing") or {}
    try:
        return float(pricing.get("prompt", 1)) == 0 and float(pricing.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def _is_text_chat_model(model):
    """Reject anything that isn't a plain text-in/text-out chat model — by the
    id denylist first, then by OpenRouter's own architecture metadata when
    present (catches a non-text model whose name doesn't give it away)."""
    if any(bad in model.get("id", "").lower() for bad in _EXCLUDE):
        return False
    arch = model.get("architecture") or {}
    out = arch.get("output_modalities")
    if out is not None and list(out) != ["text"]:
        return False
    return True


def list_free_models():
    """Every ``:free`` text chat model OpenRouter currently lists — reasoning
    models and non-text (audio/image/video) listings excluded, see
    ``_EXCLUDE``. Cached an hour."""
    cached = cache.get(_MODELS_CACHE_KEY)
    if cached is not None:
        return cached
    req = urllib.request.Request(f"{_API}/models")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        logger.warning("openrouter model catalog unreachable", exc_info=True)
        return []
    free = [m for m in data.get("data", []) if _is_free(m) and _is_text_chat_model(m)]
    cache.set(_MODELS_CACHE_KEY, free, _MODELS_CACHE_TTL)
    return free


def ranked_free_models():
    """Free model ids, best first, per ``_PREFERRED`` then by context length."""
    free = list_free_models()
    if not free:
        return list(_FALLBACK_MODELS)

    ids = [m["id"] for m in free]
    ranked = []
    for pref in _PREFERRED:
        ranked += [i for i in ids if pref in i and i not in ranked]
    rest = sorted(
        (m for m in free if m["id"] not in ranked),
        key=lambda m: m.get("context_length") or 0, reverse=True,
    )
    ranked += [m["id"] for m in rest if m["id"] not in ranked]
    return ranked


def chat(*, api_key, model, messages, max_tokens=800, temperature=0.7, timeout=25):
    body = {
        "model": model, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    req = urllib.request.Request(
        f"{_API}/chat/completions", data=json.dumps(body).encode(), method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter asks for these on free-tier calls; harmless otherwise.
            "HTTP-Referer": "https://shopinaday.com",
            "X-Title": "shopinaday",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise OpenRouterError(f"{model} -> {exc.code}: {detail}", status=exc.code) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise OpenRouterError(f"{model} unreachable: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"{model}: unexpected response shape") from exc

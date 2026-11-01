"""Perplexity web client: cookie auth + ask SSE + media parsing."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PPLX_BASE = "https://www.perplexity.ai"
PPLX_AUTH_SESSION = f"{PPLX_BASE}/api/auth/session"
PPLX_SSE_ASK = f"{PPLX_BASE}/rest/sse/perplexity_ask"

DEFAULT_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": PPLX_BASE,
    "referer": f"{PPLX_BASE}/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}

VIDEO_EXT_RE = re.compile(r"\.(mp4|webm|mov)(\?|$)", re.I)


class PplxError(RuntimeError):
    pass


@dataclass
class MediaItem:
    url: str
    kind: str = "image"  # image | video | unknown
    thumbnail_url: Optional[str] = None
    download_url: Optional[str] = None
    mime_type: Optional[str] = None
    source: Optional[str] = None
    generation_model: Optional[str] = None
    prompt: Optional[str] = None


@dataclass
class AskResult:
    answer: str = ""
    backend_uuid: Optional[str] = None
    display_model: Optional[str] = None
    image_generation: bool = False
    media: list[MediaItem] = field(default_factory=list)
    web_results: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def images(self) -> list[MediaItem]:
        return [m for m in self.media if m.kind == "image"]

    @property
    def videos(self) -> list[MediaItem]:
        return [m for m in self.media if m.kind == "video"]


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cookie_dict(cookie: str) -> dict[str, str]:
    value = (cookie or "").strip()
    if not value:
        raise PplxError("PPLX_COOKIE is empty — copy value from .env.example into .env")
    # Allow pasting "name=value" by mistake.
    if value.startswith("__Secure-next-auth.session-token="):
        value = value.split("=", 1)[1].strip()
    return {"__Secure-next-auth.session-token": value}


def _guess_kind(url: str, mime: Optional[str] = None) -> str:
    if mime:
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
    if VIDEO_EXT_RE.search(url or ""):
        return "video"
    return "image"


def _merge_media(bucket: list[MediaItem], item: MediaItem) -> None:
    for existing in bucket:
        if existing.url == item.url:
            for attr in (
                "thumbnail_url",
                "download_url",
                "mime_type",
                "source",
                "generation_model",
                "prompt",
            ):
                if getattr(existing, attr) is None and getattr(item, attr) is not None:
                    setattr(existing, attr, getattr(item, attr))
            if existing.kind == "unknown" and item.kind != "unknown":
                existing.kind = item.kind
            return
    bucket.append(item)


def _meta_model(obj: dict[str, Any]) -> Optional[str]:
    meta = obj.get("generated_media_metadata")
    if isinstance(meta, dict):
        for key in ("model_str", "model"):
            val = meta.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _first_str(obj: dict[str, Any], keys: list[str]) -> Optional[str]:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _media_from_raw(raw: dict[str, Any]) -> Optional[MediaItem]:
    """Convert one media dict into MediaItem; skip pending placeholders."""
    url = _first_str(raw, ["image", "url", "video", "video_url", "src"])
    if not url:
        return None
    if url.startswith("pplx-pending-media://"):
        return None
    if not (url.startswith("http://") or url.startswith("https://")):
        return None
    mime = _first_str(raw, ["mime_type", "mimetype", "content_type", "type"])
    medium = raw.get("medium")
    if medium == "video" or raw.get("video") or raw.get("video_url"):
        kind = "video"
    elif medium == "image":
        kind = "image"
    else:
        kind = _guess_kind(url, mime)
    return MediaItem(
        url=url,
        kind=kind,
        thumbnail_url=_first_str(raw, ["thumbnail", "thumbnail_url"]),
        mime_type=mime,
        source=_first_str(raw, ["source"]),
        generation_model=_meta_model(raw),
        prompt=_first_str(raw, ["prompt", "description", "caption", "name"]),
    )


def _collect_media_lists(obj: Any) -> list[dict[str, Any]]:
    """Walk common media containers used by current Perplexity SSE."""
    rows: list[dict[str, Any]] = []
    if not isinstance(obj, dict):
        return rows
    for key in ("media_items", "generated_media_items"):
        items = obj.get(key)
        if isinstance(items, list):
            rows.extend([x for x in items if isinstance(x, dict)])
    media_block = obj.get("media_block")
    if isinstance(media_block, dict):
        rows.extend(_collect_media_lists(media_block))
    inline = obj.get("inline_entity_block")
    if isinstance(inline, dict):
        rows.extend(_collect_media_lists(inline))
    return rows


def extract_media(event: dict[str, Any]) -> list[MediaItem]:
    """Pull generated images/videos from one SSE JSON event."""
    found: list[MediaItem] = []

    # Top-level media_items (legacy / some clients)
    for raw in _collect_media_lists(event):
        item = _media_from_raw(raw)
        if item:
            _merge_media(found, item)

    blocks = event.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue

            # Current web shape: media_block / inline_entity_block.generated_media_items
            for raw in _collect_media_lists(block):
                item = _media_from_raw(raw)
                if item:
                    _merge_media(found, item)

            assets = block.get("assets")
            if isinstance(assets, list):
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    for key, default_kind in (
                        ("generated_image", "image"),
                        ("generated_video", "video"),
                        ("video", "video"),
                    ):
                        blob = asset.get(key)
                        if not isinstance(blob, dict):
                            continue
                        url = _first_str(blob, ["url", "src", "video_url", "image"])
                        if not url or url.startswith("pplx-pending-media://"):
                            continue
                        mime = _first_str(blob, ["mime_type", "mimetype", "content_type", "type"])
                        download = None
                        info = asset.get("download_info")
                        if isinstance(info, list):
                            for row in info:
                                if isinstance(row, dict) and isinstance(row.get("url"), str):
                                    download = row["url"]
                                    break
                        _merge_media(
                            found,
                            MediaItem(
                                url=url,
                                kind=default_kind if default_kind != "image" else _guess_kind(url, mime),
                                thumbnail_url=_first_str(blob, ["thumbnail_url", "thumbnail"]),
                                download_url=download,
                                mime_type=mime,
                                source=_first_str(asset, ["source"]),
                                generation_model=_meta_model(asset) or _meta_model(blob),
                                prompt=_first_str(blob, ["prompt", "description", "caption"]),
                            ),
                        )

            content = block.get("content")
            if isinstance(content, dict):
                for list_key, default_kind in (
                    ("image_results", "image"),
                    ("video_results", "video"),
                    ("media_results", "unknown"),
                ):
                    rows = content.get(list_key)
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        item = _media_from_raw(row)
                        if item:
                            if default_kind != "unknown":
                                item.kind = default_kind
                            _merge_media(found, item)

    return found


def _extract_answer(event: dict[str, Any]) -> str:
    blocks = event.get("blocks")
    if isinstance(blocks, list):
        texts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            usage = str(block.get("intended_usage") or "")
            if not usage.startswith("ask_text"):
                continue
            md = block.get("markdown_block") or {}
            if isinstance(md, dict):
                chunk = md.get("answer") or md.get("text") or ""
                if isinstance(chunk, str) and chunk:
                    texts.append(chunk)
        if texts:
            # Prefer longest cumulative answer seen in this event.
            return max(texts, key=len)

    answer = event.get("answer")
    if isinstance(answer, str):
        return answer
    return ""


def _iter_sse_json(response) -> Iterator[dict[str, Any]]:
    event_name = "message"
    data_lines: list[str] = []

    def flush() -> Optional[dict[str, Any]]:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = "message"
            return None
        payload = "\n".join(data_lines).strip()
        data_lines = []
        name = event_name
        event_name = "message"
        if name in {"end_of_stream", "done"}:
            return {"_sse_event": name}
        if name == "error":
            return {"_sse_event": "error", "detail": payload}
        if not payload or payload == "[DONE]":
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {"_sse_event": "parse_error", "detail": payload[:500]}
        if isinstance(parsed, dict):
            # Some builds nest JSON in text.
            text = parsed.get("text")
            if isinstance(text, str):
                try:
                    nested = json.loads(text)
                    if isinstance(nested, (dict, list)):
                        parsed["text"] = nested
                except json.JSONDecodeError:
                    pass
            return parsed
        return {"_sse_event": "non_object", "detail": parsed}

    for raw_line in response.iter_lines():
        if raw_line is None:
            continue
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace")
        else:
            line = str(raw_line)
        if line.endswith("\r"):
            line = line[:-1]
        if line == "":
            item = flush()
            if item is not None:
                yield item
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
    item = flush()
    if item is not None:
        yield item


class PerplexityWebClient:
    def __init__(
        self,
        cookie: Optional[str] = None,
        *,
        impersonate: Optional[str] = None,
        api_version: Optional[str] = None,
        default_model: Optional[str] = None,
        default_mode: Optional[str] = None,
        language: Optional[str] = None,
        incognito: Optional[bool] = None,
    ):
        self.cookies = _cookie_dict(cookie or os.getenv("PPLX_COOKIE", ""))
        self.impersonate = impersonate or os.getenv("PPLX_IMPERSONATE", "chrome")
        self.api_version = api_version or os.getenv("PPLX_API_VERSION", "2.18")
        self.default_model = default_model or os.getenv("PPLX_MODEL", "pplx_pro")
        self.default_mode = default_mode or os.getenv("PPLX_MODE", "copilot")
        self.language = language or os.getenv("PPLX_LANGUAGE", "en-US")
        self.incognito = _env_bool("PPLX_INCOGNITO", True) if incognito is None else incognito
        self.session = cffi_requests.Session(
            headers=DEFAULT_HEADERS.copy(),
            cookies=self.cookies,
            impersonate=self.impersonate,
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "PerplexityWebClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def whoami(self) -> dict[str, Any]:
        resp = self.session.get(PPLX_AUTH_SESSION, timeout=30)
        if resp.status_code != 200:
            raise PplxError(f"session check HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if not isinstance(data, dict) or not data.get("user"):
            raise PplxError("session is not authenticated — refresh PPLX_COOKIE")
        user = data.get("user") or {}

        quotas: dict[str, Any] = {}
        try:
            rate = self.session.get(f"{PPLX_BASE}/rest/rate-limit/all", timeout=30)
            if rate.status_code == 200:
                payload = rate.json()
                if isinstance(payload, dict):
                    for key in (
                        "remaining_pro",
                        "remaining_research",
                        "remaining_labs",
                        "remaining_agentic_research",
                    ):
                        if key in payload:
                            quotas[key] = payload.get(key)
        except Exception:
            quotas = {}

        return {
            "authenticated": True,
            "subscription_status": user.get("subscription_status") or data.get("subscription_status"),
            "subscription_tier": user.get("subscription_tier"),
            "subscription_source": user.get("subscription_source"),
            "quotas": quotas,
            "client_defaults": {
                "model": self.default_model,
                "mode": self.default_mode,
                "language": self.language,
                "incognito": self.incognito,
            },
            # Kept for debugging/smoke tests; CLI does not print these.
            "email": user.get("email"),
            "name": user.get("name"),
        }

    def ask(
        self,
        query: str,
        *,
        model: Optional[str] = None,
        mode: Optional[str] = None,
        sources: Optional[list[str]] = None,
        follow_up_uuid: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        keep_events: bool = False,
    ) -> AskResult:
        query = (query or "").strip()
        if not query:
            raise PplxError("query is empty")

        model_pref = model or self.default_model
        pplx_mode = mode or self.default_mode
        if model_pref in {"pplx_pro", "turbo", "auto"} and mode is None:
            # Match archived proxy: auto/turbo tend to use concise.
            pplx_mode = "concise"

        payload = {
            "query_str": query,
            "params": {
                "attachments": list(attachments or []),
                "frontend_context_uuid": str(uuid4()),
                "frontend_uuid": str(uuid4()),
                "is_incognito": self.incognito,
                "language": self.language,
                "last_backend_uuid": follow_up_uuid,
                "mode": pplx_mode,
                "model_preference": model_pref,
                "source": "default",
                "sources": sources or ["web"],
                "search_focus": "internet",
                "search_recency_filter": None,
                "timezone": "Asia/Taipei",
                "visitor_id": str(uuid4()),
                "user_nextauth_id": str(uuid4()),
                "prompt_source": "user",
                "query_source": "home",
                "browser_history_summary": [],
                "is_related_query": False,
                "is_sponsored": False,
                "is_nav_suggestions_disabled": False,
                "use_schematized_api": True,
                "send_back_text_in_streaming_api": False,
                "supported_block_use_cases": [
                    "answer_modes",
                    "media_items",
                    "knowledge_cards",
                    "inline_entity_cards",
                    "place_widgets",
                    "finance_widgets",
                    "sports_widgets",
                    "shopping_widgets",
                    "search_result_widgets",
                ],
                "client_coordinates": None,
                "version": self.api_version,
            },
        }

        resp = self.session.post(PPLX_SSE_ASK, json=payload, stream=True, timeout=300)
        if resp.status_code != 200:
            body = resp.text[:500] if hasattr(resp, "text") else ""
            raise PplxError(f"ask HTTP {resp.status_code}: {body}")

        result = AskResult()
        try:
            for event in _iter_sse_json(resp):
                if event.get("_sse_event") == "end_of_stream":
                    break
                if event.get("_sse_event") == "error":
                    raise PplxError(f"SSE error: {event.get('detail')}")
                if event.get("_sse_event"):
                    continue

                if keep_events:
                    result.events.append(event)

                answer = _extract_answer(event)
                if answer and len(answer) >= len(result.answer):
                    result.answer = answer

                uuid = event.get("backend_uuid")
                if isinstance(uuid, str) and uuid:
                    result.backend_uuid = uuid

                display = event.get("display_model")
                if isinstance(display, str) and display:
                    result.display_model = display

                classifier = event.get("classifier_results")
                if isinstance(classifier, dict) and classifier.get("image_generation") is True:
                    result.image_generation = True

                for media in extract_media(event):
                    _merge_media(result.media, media)
                    if media.kind == "image":
                        result.image_generation = True

                # Optional web results on some event shapes.
                wr = event.get("web_results")
                if isinstance(wr, list) and wr:
                    result.web_results = [x for x in wr if isinstance(x, dict)]
        finally:
            resp.close()

        return result

    def chat(self, query: str, **kwargs) -> AskResult:
        return self.ask(query, **kwargs)

    def generate_image(self, prompt: str, **kwargs) -> AskResult:
        text = prompt.strip()
        if not text.lower().startswith("generate"):
            text = f"Generate an image of {text}"
        return self.ask(text, **kwargs)

    def generate_video(self, prompt: str, **kwargs) -> AskResult:
        text = prompt.strip()
        if not text.lower().startswith("generate"):
            text = f"Generate a video of {text}"
        return self.ask(text, **kwargs)


def download_url(url: str, dest: Path, session: Optional[cffi_requests.Session] = None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    own = session is None
    sess = session or cffi_requests.Session(impersonate=os.getenv("PPLX_IMPERSONATE", "chrome"))
    try:
        resp = sess.get(url, timeout=120)
        if resp.status_code != 200:
            raise PplxError(f"download HTTP {resp.status_code} for {url}")
        dest.write_bytes(resp.content)
        return dest
    finally:
        if own:
            sess.close()

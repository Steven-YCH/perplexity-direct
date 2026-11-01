#!/usr/bin/env python3
"""Command-line entrypoint for perplexity-direct."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from pplx_client import ROOT, AskResult, PerplexityWebClient, PplxError, download_url

OUT = ROOT / "out"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _safe_name(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())[:limit].strip("_")
    return slug or "media"


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _print_result(result: AskResult, *, show_media: bool = True) -> None:
    if result.display_model:
        print(f"[model] {result.display_model}")
    if result.backend_uuid:
        print(f"[backend_uuid] {result.backend_uuid}")
    if result.image_generation:
        print("[classifier] image_generation=true")
    if result.answer:
        print("\n--- answer ---")
        print(result.answer)
    if show_media and result.media:
        print("\n--- media ---")
        for i, item in enumerate(result.media, 1):
            print(f"{i}. [{item.kind}] {item.url}")
            if item.generation_model:
                print(f"   model={item.generation_model}")
            if item.download_url:
                print(f"   download={item.download_url}")
    elif show_media:
        print("\n(no media URLs parsed)")


def _save_chat(result: AskResult, prompt: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"chat_{_stamp()}.md"
    body = [
        f"# Chat {_stamp()}\n",
        "## Prompt\n",
        prompt.strip() + "\n",
        "## Meta\n",
        f"- model: {result.display_model or ''}",
        f"- backend_uuid: {result.backend_uuid or ''}\n",
        "## Answer\n",
        (result.answer or "").strip() + "\n",
    ]
    path.write_text("\n".join(body), encoding="utf-8")
    print(f"[saved] {_rel(path)}")
    return path


def _save_media(result: AskResult, prefix: str, client: PerplexityWebClient) -> list[Path]:
    saved: list[Path] = []
    OUT.mkdir(parents=True, exist_ok=True)
    for i, item in enumerate(result.media, 1):
        url = item.download_url or item.url
        ext = ".mp4" if item.kind == "video" else ".png"
        lower = url.lower()
        if ".jpg" in lower or ".jpeg" in lower:
            ext = ".jpg"
        elif ".webp" in lower:
            ext = ".webp"
        elif ".webm" in lower:
            ext = ".webm"
        elif ".mp4" in lower:
            ext = ".mp4"
        path = OUT / f"{prefix}_{i}{ext}"
        try:
            download_url(url, path, session=client.session)
            print(f"[saved] {_rel(path)}")
            saved.append(path)
        except PplxError as exc:
            print(f"[warn] could not download {url}: {exc}")
    return saved


def cmd_whoami(_: argparse.Namespace) -> int:
    with PerplexityWebClient() as client:
        info = client.whoami()
        # No personal identifiers in CLI output.
        print(
            json.dumps(
                {
                    "authenticated": bool(info.get("authenticated")),
                    "subscription_status": info.get("subscription_status"),
                    "subscription_tier": info.get("subscription_tier"),
                    "quotas": info.get("quotas") or {},
                    "client_defaults": info.get("client_defaults") or {},
                },
                indent=2,
            )
        )
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    with PerplexityWebClient() as client:
        result = client.chat(args.prompt, model=args.model, mode=args.mode, keep_events=args.raw)
        if args.raw:
            dump = OUT / f"chat_raw_{_stamp()}.json"
            OUT.mkdir(parents=True, exist_ok=True)
            dump.write_text(
                json.dumps({"answer": result.answer, "events": result.events}, indent=2),
                encoding="utf-8",
            )
            print(f"[raw] {_rel(dump)}")
        else:
            _print_result(result, show_media=False)
        if not args.no_save:
            _save_chat(result, args.prompt)
    return 0


def cmd_image(args: argparse.Namespace) -> int:
    with PerplexityWebClient() as client:
        result = client.generate_image(
            args.prompt, model=args.model, mode=args.mode, keep_events=args.raw
        )
        _print_result(result)
        if args.raw:
            dump = OUT / f"image_raw_{_stamp()}.json"
            OUT.mkdir(parents=True, exist_ok=True)
            dump.write_text(json.dumps(result.events, indent=2), encoding="utf-8")
            print(f"[raw] {_rel(dump)}")
        if not args.no_download:
            _save_media(result, f"image_{_safe_name(args.prompt)}_{_stamp()}", client)
        if not result.images and not args.raw:
            print(
                "\nNo image URLs found. Try --raw to inspect SSE shape, "
                "or refresh cookie / confirm image quota on your plan."
            )
            return 2
    return 0


def cmd_video(args: argparse.Namespace) -> int:
    with PerplexityWebClient() as client:
        result = client.generate_video(
            args.prompt, model=args.model, mode=args.mode, keep_events=args.raw
        )
        _print_result(result)
        if args.raw:
            dump = OUT / f"video_raw_{_stamp()}.json"
            OUT.mkdir(parents=True, exist_ok=True)
            dump.write_text(json.dumps(result.events, indent=2), encoding="utf-8")
            print(f"[raw] {_rel(dump)}")
        if not args.no_download:
            _save_media(result, f"video_{_safe_name(args.prompt)}_{_stamp()}", client)
        if not result.videos and not result.media:
            print(
                "\nNo video/media URLs parsed. Re-run with --raw and inspect the dump."
            )
            return 2
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    with PerplexityWebClient() as client:
        result = client.ask(
            args.prompt, model=args.model, mode=args.mode, keep_events=args.raw
        )
        _print_result(result)
        if args.raw:
            dump = OUT / f"ask_raw_{_stamp()}.json"
            OUT.mkdir(parents=True, exist_ok=True)
            dump.write_text(json.dumps(result.events, indent=2), encoding="utf-8")
            print(f"[raw] {_rel(dump)}")
        if args.download:
            _save_media(result, f"ask_{_safe_name(args.prompt)}_{_stamp()}", client)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Perplexity web cookie client")
    p.add_argument("--model", default=None, help="model_preference override")
    p.add_argument("--mode", default=None, help="concise|copilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("whoami", help="Validate cookie / show subscription status")
    w.set_defaults(func=cmd_whoami)

    c = sub.add_parser("chat", help="Text ask")
    c.add_argument("prompt")
    c.add_argument("--raw", action="store_true")
    c.add_argument("--no-save", action="store_true", help="Do not write out/chat_*.md")
    c.set_defaults(func=cmd_chat)

    i = sub.add_parser("image", help="Generate an image")
    i.add_argument("prompt")
    i.add_argument("--raw", action="store_true")
    i.add_argument("--no-download", action="store_true")
    i.set_defaults(func=cmd_image)

    v = sub.add_parser("video", help="Generate a video")
    v.add_argument("prompt")
    v.add_argument("--raw", action="store_true")
    v.add_argument("--no-download", action="store_true")
    v.set_defaults(func=cmd_video)

    a = sub.add_parser("ask", help="Raw ask (any prompt)")
    a.add_argument("prompt")
    a.add_argument("--raw", action="store_true")
    a.add_argument("--download", action="store_true")
    a.set_defaults(func=cmd_ask)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PplxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

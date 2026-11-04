#!/usr/bin/env python3
"""Export __Secure-next-auth.session-token from Chrome into .env as PPLX_COOKIE.

Usage:
  python scripts/export_chrome_cookies.py
  python cli.py auth --from-chrome
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = REPO_ROOT / ".env"
SESSION_COOKIE = "__Secure-next-auth.session-token"
DOMAIN = "perplexity.ai"


def _load_browser_cookie3():
    try:
        import browser_cookie3
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "browser-cookie3 is required.\n"
            "Install with: python -m pip install 'browser-cookie3>=0.19.1'\n"
            f"Details: {exc}"
        ) from exc
    return browser_cookie3


def export_session_cookie(
    *,
    browser: str = "chrome",
    profile: str = "Default",
    env_file: Path = DEFAULT_ENV,
) -> str:
    bc3 = _load_browser_cookie3()
    kwargs = {"domain_name": DOMAIN}
    home = Path.home()
    if browser == "chrome":
        kwargs["cookie_file"] = str(home / ".config" / "google-chrome" / profile / "Cookies")
    elif browser == "chromium":
        kwargs["cookie_file"] = str(home / ".config" / "chromium" / profile / "Cookies")

    loaders = {
        "chrome": bc3.chrome,
        "chromium": bc3.chromium,
        "edge": getattr(bc3, "edge", None),
    }
    loader = loaders.get(browser)
    if loader is None:
        raise SystemExit(f"Unsupported browser: {browser}")
    try:
        cookies = list(loader(**kwargs))
    except TypeError:
        cookies = list(loader(domain_name=DOMAIN))

    session = next((c.value for c in cookies if c.name == SESSION_COOKIE and c.value), None)
    if not session:
        raise SystemExit(
            f"Missing {SESSION_COOKIE}. Sign in to https://www.perplexity.ai "
            f"in {browser} and retry."
        )

    path = env_file.expanduser()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing and (REPO_ROOT / ".env.example").exists():
        existing = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    lines = existing.splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if re.match(r"^PPLX_COOKIE=", line):
            out.append(f"PPLX_COOKIE={session}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"PPLX_COOKIE={session}")
    if out and out[-1] != "":
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export Chrome Perplexity cookie into .env")
    parser.add_argument("--browser", choices=("chrome", "chromium", "edge"), default="chrome")
    parser.add_argument("--profile", default="Default")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    args = parser.parse_args(argv)
    session = export_session_cookie(
        browser=args.browser, profile=args.profile, env_file=args.env_file
    )
    print(f"Updated {args.env_file}")
    print(f"PPLX_COOKIE set from {SESSION_COOKIE} (len={len(session)})")
    print("Validate with: python cli.py whoami")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

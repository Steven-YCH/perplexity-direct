"""Minimal chat smoke test. Run from repo root with .env filled."""

from pplx_client import PerplexityWebClient


def main() -> None:
    with PerplexityWebClient() as client:
        info = client.whoami()
        print("authenticated:", bool(info.get("email") or info.get("name")))
        print("subscription_status:", info.get("subscription_status"))
        result = client.chat("Reply with exactly: PPLX_DIRECT_OK")
        print("model:", result.display_model)
        print("answer:", result.answer)


if __name__ == "__main__":
    main()

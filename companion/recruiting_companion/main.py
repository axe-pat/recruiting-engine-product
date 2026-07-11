from __future__ import annotations

import argparse
import sys

from .api import make_server
from .auth import TokenStore
from .config import Settings
from .service import CompanionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Recruiting Engine local companion")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="start the loopback API")
    subparsers.add_parser(
        "show-pairing",
        help="print the current one-time pairing token",
    )
    subparsers.add_parser(
        "rotate-pairing",
        help="invalidate any old pairing token and issue a new one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    command = arguments.command or "serve"
    settings = Settings.from_env()
    settings.prepare()
    tokens = TokenStore(settings.user_dir)
    bootstrap = tokens.bootstrap()

    if command == "show-pairing":
        if not bootstrap.pairing_path.exists():
            print(
                "No active pairing token. Run `python -m recruiting_companion rotate-pairing`.",
                file=sys.stderr,
            )
            return 1
        print(bootstrap.pairing_path.read_text(encoding="utf-8").strip())
        return 0
    if command == "rotate-pairing":
        print(tokens.rotate_pairing_token())
        return 0

    service = CompanionService(settings)
    server = make_server(settings, service, tokens)
    print(f"Recruiting Engine companion listening on http://{settings.host}:{settings.port}")
    print(f"User-local data: {settings.user_dir}")
    if bootstrap.pairing_path.exists():
        print(f"One-time pairing token: {bootstrap.pairing_path}")
    else:
        print("Pairing already completed; rotate the pairing token to connect a new device.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping companion")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

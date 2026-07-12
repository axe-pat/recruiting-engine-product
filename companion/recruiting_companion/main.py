from __future__ import annotations

import argparse
import sys

from .api import make_server
from .auth import AuthStateError, AuthStateHealthyError, TokenStore
from .config import Settings
from .service import CompanionService


def _report_auth_repair_required(tokens: TokenStore) -> None:
    print(
        "Local auth state is inconsistent. Run "
        "`python -m recruiting_companion repair-auth` explicitly.",
        file=sys.stderr,
    )
    print(f"Auth state: {tokens.state_path}", file=sys.stderr)
    print(f"Private bearer: {tokens.bearer_path}", file=sys.stderr)


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
    subparsers.add_parser(
        "issue-local-activation",
        help="issue one short-lived, single-use local UI activation ticket",
    )
    subparsers.add_parser(
        "repair-auth",
        help="replace auth material only when bearer/state is inconsistent",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    command = arguments.command or "serve"
    settings = Settings.from_env()
    settings.prepare()
    tokens = TokenStore(settings.user_dir)
    bootstrap = None
    try:
        bootstrap = tokens.bootstrap()
    except AuthStateError:
        if command != "repair-auth":
            _report_auth_repair_required(tokens)
            return 1

    if command == "repair-auth":
        try:
            repaired = tokens.repair_auth()
        except AuthStateHealthyError:
            print(
                "Auth state is already consistent; no credentials were changed.",
                file=sys.stderr,
            )
            print(f"Auth state: {tokens.state_path}", file=sys.stderr)
            print(f"Private bearer: {tokens.bearer_path}", file=sys.stderr)
            return 1
        print("Local auth state repaired; all prior sessions were invalidated.")
        print(f"Auth state: {repaired.state_path}")
        print(f"Private bearer: {repaired.bearer_path}")
        print(f"One-time pairing file: {repaired.pairing_path}")
        return 0

    if bootstrap is None:
        return 1

    if tokens.local_bearer_token() is None:
        _report_auth_repair_required(tokens)
        return 1

    if command == "issue-local-activation":
        try:
            ticket = tokens.issue_local_activation_ticket()
        except AuthStateError:
            _report_auth_repair_required(tokens)
            return 1
        print(ticket)
        return 0

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

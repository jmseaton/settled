"""Generate the values that go in `.env`.

    docker compose exec backend python -m app.auth
    docker compose exec backend python -m app.auth --api-token

Reads the password from a TTY prompt rather than argv, because a password in
argv is a password in `ps`, in the shell history, and in anything scraping
either. `--stdin` is the scripted alternative — still not argv:

    printf '%s' "$PASSWORD" | docker compose run --rm -T backend python -m app.auth --stdin

The hash goes to stdout and nothing else does, so the output can be piped or
copied without picking up prose.
"""

import argparse
import getpass
import secrets
import sys

from app.auth.passwords import hash_password

MIN_LENGTH = 12


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.auth",
        description="Generate SETTLED_AUTH_PASSWORD_HASH (or SETTLED_API_TOKEN).",
    )
    parser.add_argument(
        "--api-token",
        action="store_true",
        help="print a random API token for the host-cron path instead of hashing a password",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="read the password from stdin instead of prompting (for scripts and CI)",
    )
    parser.add_argument(
        "--allow-short",
        action="store_true",
        help=f"accept a password shorter than {MIN_LENGTH} characters",
    )
    args = parser.parse_args(argv)

    if args.api_token:
        print(secrets.token_urlsafe(32))
        return 0

    # A trailing newline from `echo` is not part of the password, and a
    # password that silently differs from the one you typed is the worst
    # possible outcome here.
    password = sys.stdin.readline().rstrip("\n") if args.stdin else getpass.getpass("Password: ")
    if not password:
        print("empty password", file=sys.stderr)
        return 1
    if len(password) < MIN_LENGTH and not args.allow_short:
        # A hosted service would rate-limit its way out of this. This one is
        # a box on a network, so length is the whole of the defence.
        print(
            f"password is shorter than {MIN_LENGTH} characters; "
            "use a longer one or pass --allow-short",
            file=sys.stderr,
        )
        return 1
    # Nothing to confirm against when it arrived on a pipe.
    if not args.stdin and password != getpass.getpass("Confirm: "):
        print("passwords do not match", file=sys.stderr)
        return 1

    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

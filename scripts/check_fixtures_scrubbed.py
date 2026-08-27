#!/usr/bin/env python3
"""Fail if a committed fixture carries a real account identifier.

`fixtures/README.md` says it plainly: *git history is permanent — scrub
before the first commit, not after*. Every other check in this repo can be
run late and still do its job. This one cannot, so it runs on every pull
request, before anything reaches a branch someone might merge.

What this catches: regenerating a fixture from IBKR and committing it
without re-running the substitutions. That is the realistic mistake — the
files look identical at a glance, and the account number is one field on
one line.

What this does not catch: an identifier that was already public, or a
scrubbing scheme that replaces a real ID with a different real ID. This is
a guard against forgetting, not a privacy proof.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

#: The placeholders the fixtures are documented to use.
PLACEHOLDER_ACCOUNT = "U0000000"
PLACEHOLDER_DELIVERING_ACCOUNT = "Z00000000"
PLACEHOLDER_BROKER_ID = "0000"
PLACEHOLDER_MASKED_REF = "J******00"

ALLOWED = {
    PLACEHOLDER_ACCOUNT,
    PLACEHOLDER_DELIVERING_ACCOUNT,
    PLACEHOLDER_BROKER_ID,
    PLACEHOLDER_MASKED_REF,
}

#: IBKR account numbers are a letter followed by 7-8 digits.
ACCOUNT_PATTERN = re.compile(r"\b[UZ]\d{7,8}\b")

#: The `.tlg` ACT_INF record carries the holder's legal name and mailing
#: address (§2.1). Both are replaced wholesale, so anything else is a leak.
EXPECTED_ACT_INF_NAME = "Test Account Holder"
EXPECTED_ACT_INF_ADDRESS = "1 Test Street  Anytown PA 00000 United States"


def check_account_identifiers(path: pathlib.Path, text: str) -> list[str]:
    found = {m.group(0) for m in ACCOUNT_PATTERN.finditer(text)}
    leaked = sorted(found - ALLOWED)
    return [
        f"{path.relative_to(ROOT)}: unscrubbed account identifier {ident!r} "
        f"(expected {PLACEHOLDER_ACCOUNT!r} or {PLACEHOLDER_DELIVERING_ACCOUNT!r})"
        for ident in leaked
    ]


def check_tlg_act_inf(path: pathlib.Path, text: str) -> list[str]:
    problems: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.startswith("ACT_INF|"):
            continue
        fields = line.split("|")
        if len(fields) < 5:
            problems.append(f"{path.relative_to(ROOT)}:{lineno}: malformed ACT_INF record")
            continue
        _, account, name, _account_type, address = fields[:5]
        if account != PLACEHOLDER_ACCOUNT:
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno}: ACT_INF account is {account!r}, "
                f"expected {PLACEHOLDER_ACCOUNT!r}"
            )
        if name != EXPECTED_ACT_INF_NAME:
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno}: ACT_INF holder name is {name!r}, "
                f"expected {EXPECTED_ACT_INF_NAME!r} — this field carries a real legal name"
            )
        if address != EXPECTED_ACT_INF_ADDRESS:
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno}: ACT_INF address is {address!r}, "
                f"expected the placeholder — this field carries a real mailing address"
            )
    return problems


def check_flex_attributes(path: pathlib.Path, text: str) -> list[str]:
    """The Flex XML omits name and address entirely; only account numbers and
    the delivering broker's details ever needed replacing (§0.2)."""
    problems: list[str] = []
    for attribute, expected in (
        ("accountId", PLACEHOLDER_ACCOUNT),
        ("account", PLACEHOLDER_DELIVERING_ACCOUNT),
        ("deliveringBroker", PLACEHOLDER_BROKER_ID),
    ):
        for match in re.finditer(rf'\b{attribute}="([^"]*)"', text):
            value = match.group(1)
            if value and value != expected:
                problems.append(
                    f"{path.relative_to(ROOT)}: {attribute}={value!r}, expected {expected!r}"
                )
    return problems


#: The de-linking pass (fixtures/README.md) moves every date well clear of the
#: present. A file regenerated from IBKR carries present-day dates, so a floor
#: catches that without this script having to know the offset it is checking
#: for — which is the whole reason pass 2 cannot be checked directly.
EARLIEST_PLAUSIBLE_YEAR = 2030

DATE_PATTERN = re.compile(r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b")


def check_dates_are_delinked(path: pathlib.Path, text: str) -> list[str]:
    stale = sorted(
        {m.group(0) for m in DATE_PATTERN.finditer(text) if int(m.group(1)) < EARLIEST_PLAUSIBLE_YEAR}
    )
    if not stale:
        return []
    return [
        f"{path.relative_to(ROOT)}: {len(stale)} date(s) before {EARLIEST_PLAUSIBLE_YEAR}, "
        f"starting {stale[0]} — this looks like broker output that has not been "
        f"de-linked (fixtures/README.md, pass 2)"
    ]


def main() -> int:
    if not FIXTURES.is_dir():
        print(f"No fixtures directory at {FIXTURES}", file=sys.stderr)
        return 1

    paths = sorted(p for p in FIXTURES.iterdir() if p.suffix in {".tlg", ".xml"})
    if not paths:
        print(f"No fixtures found in {FIXTURES}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        problems += check_account_identifiers(path, text)
        problems += check_dates_are_delinked(path, text)
        if path.suffix == ".tlg":
            problems += check_tlg_act_inf(path, text)
        else:
            problems += check_flex_attributes(path, text)

    if problems:
        print("Fixture scrubbing check FAILED:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nRe-run the substitutions in fixtures/README.md before committing."
            "\nIf this identifier is already in git history, rewriting the branch is"
            "\nnot enough — the history is permanent and the credential is exposed.",
            file=sys.stderr,
        )
        return 1

    print(f"Fixture scrubbing check passed ({len(paths)} files).")
    for path in paths:
        print(f"  - {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

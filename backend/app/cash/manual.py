"""§7.1 — the manual entry path: CRUD and CSV paste.

Flex covers everything from the day the query window starts. This path
covers the three things it cannot: **pre-Flex history**, **corrections**,
and **recategorisation** of a transaction IB filed somewhere you disagree
with.

Every row written here carries `source = MANUAL`, and nothing in the import
pipeline will ever overwrite one. That is the whole contract: a correction
that a nightly resync silently reverts is worse than no correction at all,
because you stop checking.
"""

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.cash_transaction import CashSource, CashTransaction, CashTransactionType

#: Accepted CSV headers, lowercased. Extra columns are ignored; a missing
#: required column is an error naming the column, because a paste that
#: half-works is harder to debug than one that refuses.
REQUIRED_COLUMNS = {"date", "type", "amount"}
OPTIONAL_COLUMNS = {"note", "currency"}

_DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%d/%m/%Y")


class CashInputError(ValueError):
    pass


@dataclass
class CsvResult:
    inserted: int = 0
    rows_read: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "inserted": self.inserted,
            "rows_read": self.rows_read,
            "errors": self.errors,
            "source": CashSource.MANUAL.value,
            "note": (
                "Rows entered here are marked MANUAL and survive every resync (§7.1)."
            ),
        }


def parse_date(raw: str) -> dt.date:
    text = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CashInputError(f"Unrecognized date {raw!r}; expected YYYY-MM-DD")


def parse_type(raw: str) -> CashTransactionType:
    key = (raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    try:
        return CashTransactionType(key)
    except ValueError:
        raise CashInputError(
            f"Unknown type {raw!r}. Expected one of: "
            + ", ".join(t.value for t in CashTransactionType)
        ) from None


def parse_amount(raw) -> float:
    text = str(raw).strip().replace(",", "").replace("$", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        raise CashInputError(f"Amount {raw!r} is not a number") from None
    return -value if negative else value


def create_transaction(
    db: Session,
    account_id: str,
    *,
    occurred_on: dt.date,
    kind: CashTransactionType,
    amount: float,
    note: str | None = None,
    currency: str = "USD",
) -> CashTransaction:
    """Create one manual row.

    The sign is taken as given rather than derived from the type: §7.1's
    convention is deposits positive, withdrawals negative, and a user who
    enters a withdrawal as `-500` and one who enters it as `500` mean the
    same thing. Normalizing here keeps the equity curve from moving the
    wrong way on a typo.
    """
    signed = amount
    if kind in (CashTransactionType.WITHDRAWAL, CashTransactionType.TRANSFER_OUT):
        signed = -abs(amount)
    elif kind in (CashTransactionType.DEPOSIT, CashTransactionType.TRANSFER_IN):
        signed = abs(amount)

    row = CashTransaction(
        account_id=account_id,
        occurred_on=occurred_on,
        type=kind,
        amount=signed,
        currency=currency or "USD",
        source=CashSource.MANUAL,
        note=note,
    )
    db.add(row)
    db.flush()
    return row


def import_csv(db: Session, account_id: str, text: str) -> CsvResult:
    """Paste path: `date,type,amount[,note][,currency]`.

    Rows are validated one at a time and a bad row does not abort the
    others — a paste of forty rows with one typo should import thirty-nine
    and name the fortieth, not reject the lot.
    """
    result = CsvResult()
    stripped = (text or "").strip()
    if not stripped:
        result.errors.append("Nothing to import: the pasted text is empty.")
        return result

    reader = csv.DictReader(io.StringIO(stripped))
    if not reader.fieldnames:
        result.errors.append("No header row found. Expected: date,type,amount[,note][,currency]")
        return result

    headers = {(h or "").strip().lower() for h in reader.fieldnames}
    missing = REQUIRED_COLUMNS - headers
    if missing:
        result.errors.append(
            f"Missing required column(s): {', '.join(sorted(missing))}. "
            "Expected header: date,type,amount[,note][,currency]"
        )
        return result

    for line_no, raw_row in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): v for k, v in raw_row.items()}
        result.rows_read += 1
        try:
            occurred_on = parse_date(row.get("date", ""))
            kind = parse_type(row.get("type", ""))
            amount = parse_amount(row.get("amount", ""))
        except CashInputError as exc:
            result.errors.append(f"Line {line_no}: {exc}")
            continue

        create_transaction(
            db,
            account_id,
            occurred_on=occurred_on,
            kind=kind,
            amount=amount,
            note=(row.get("note") or "").strip() or None,
            currency=(row.get("currency") or "USD").strip().upper(),
        )
        result.inserted += 1

    return result


def serialize(row: CashTransaction) -> dict:
    return {
        "id": row.id,
        "occurred_on": row.occurred_on.isoformat(),
        "type": row.type.value,
        "amount": float(row.amount),
        "currency": row.currency,
        "source": row.source.value,
        "broker_transaction_id": row.broker_transaction_id,
        "note": row.note,
        "is_external_flow": row.is_external_flow,
    }

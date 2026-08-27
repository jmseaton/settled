"""§3.8 — Flex error classification.

Classify by code and never blindly retry everything. The distinction that
matters: a transient error is worth retrying, a credential error must *not*
be retried and needs a human, and the two look identical if you only check
whether the request failed.
"""

import enum


class ErrorClass(str, enum.Enum):
    TRANSIENT = "TRANSIENT"
    RATE_LIMIT = "RATE_LIMIT"
    CREDENTIAL = "CREDENTIAL"
    CONFIGURATION = "CONFIGURATION"
    REQUEST = "REQUEST"
    UNKNOWN = "UNKNOWN"


#: Statement/P&L data not ready, or the server is busy.
TRANSIENT_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1019", "1021"}

#: One request per second and ten per minute, per token.
RATE_LIMIT_CODES = {"1018"}

#: Needs human action. Never retried.
CREDENTIAL_CODES = {
    "1012",  # token expired
    "1015",  # token invalid
    "1013",  # IP restriction
    "1011",  # service account inactive
}

CONFIGURATION_CODES = {
    "1014",  # query invalid
    "1010",  # legacy Flex no longer supported
    "1016",  # account invalid
    # Lockout after repeated failures — "Too many failed attempts. Please
    # review your configuration." Every request then fails identically,
    # including ones that worked minutes earlier, which makes it look like
    # a sudden total outage rather than a consequence of what came before.
    #
    # It belongs here for one reason above all: **retrying it is what
    # caused it**, and would deepen it. It is also the only code whose
    # trigger is the app's own behaviour, so a retry policy that treats it
    # as worth another go turns a self-inflicted wound into a permanent
    # one. Alerting, never retried, and the count that produced it is what
    # `attempt_budget` exists to keep small.
    "1025",
}

REQUEST_CODES = {
    "1003",  # statement not available
    "1017",  # reference code invalid
}


def classify(code: str | None) -> ErrorClass:
    if code is None:
        return ErrorClass.UNKNOWN
    code = str(code).strip()
    if code in TRANSIENT_CODES:
        return ErrorClass.TRANSIENT
    if code in RATE_LIMIT_CODES:
        return ErrorClass.RATE_LIMIT
    if code in CREDENTIAL_CODES:
        return ErrorClass.CREDENTIAL
    if code in CONFIGURATION_CODES:
        return ErrorClass.CONFIGURATION
    if code in REQUEST_CODES:
        return ErrorClass.REQUEST
    return ErrorClass.UNKNOWN


#: §3.8 — how many attempts each class is allowed *in total*, the first one
#: included. Retrying is not one decision but three:
#:
#:   * transient and rate-limit ride the full backoff ladder (`None` means
#:     "as many as the caller allows");
#:   * a request-specific failure gets exactly one restart from SendRequest
#:     — "restart from SendRequest once, then give up for this run". 1003
#:     and 1017 are both properties of *this exchange* rather than of the
#:     token or the query: a reference code can expire between the two
#:     calls, and a statement can be unavailable for a reference code that a
#:     fresh SendRequest will happily reissue. One clean restart is the
#:     documented remedy, and it is the whole remedy — a second restart is
#:     just rate-limit budget spent on an answer that will not change;
#:   * credential and configuration get none, because retrying them wastes
#:     that budget and delays the alert a human has to act on.
#:
#: UNKNOWN gets one attempt too: it is where an unparseable body and an
#: unrecognised code land, and neither becomes truer on a second look.
ATTEMPT_BUDGET: dict[ErrorClass, int | None] = {
    ErrorClass.TRANSIENT: None,
    ErrorClass.RATE_LIMIT: None,
    ErrorClass.REQUEST: 2,
    ErrorClass.CREDENTIAL: 1,
    ErrorClass.CONFIGURATION: 1,
    ErrorClass.UNKNOWN: 1,
}


def attempt_budget(error_class: ErrorClass, ceiling: int) -> int:
    """Total attempts allowed for this class, never above the caller's cap."""
    budget = ATTEMPT_BUDGET.get(error_class, 1)
    return ceiling if budget is None else min(budget, ceiling)


def is_retryable(error_class: ErrorClass) -> bool:
    """Whether another attempt is worth making at all.

    Worth making is not the same as worth making repeatedly — see
    `attempt_budget`, which is what a retry loop should actually consult.
    """
    return ATTEMPT_BUDGET.get(error_class, 1) != 1


class FlexError(Exception):
    def __init__(
        self,
        code: str | None,
        message: str,
        error_class: ErrorClass,
        *,
        step: str | None = None,
    ):
        self.code = code
        self.error_class = error_class
        #: The bare message, without the code/class/step prefix, so a caller
        #: with more context than the parser had can re-raise a better one
        #: without either re-parsing the formatted string or losing what
        #: IBKR actually said.
        self.message = message
        #: Which half of the two-step exchange (§3.7) produced this. The
        #: same code means different things on either side and wants a
        #: different fix: 1003 from SendRequest is IBKR declining to build a
        #: statement for what was asked (the date window, or a query that
        #: does not permit overrides), while 1003 from GetStatement is a
        #: statement that was accepted for generation and then could not be
        #: retrieved under that reference code. Without the step, the stored
        #: error reads identically for both and sends you to the wrong one.
        self.step = step
        #: How many attempts the exchange had made when this was raised.
        #: Set by `FlexClient.fetch`, which is the only thing that counts;
        #: a FlexError raised outside a retry loop is by definition the
        #: first. `flex_sync_runs.attempt_count` records it, and on a failed
        #: run — the row that has to explain a stale dashboard — the
        #: difference between "tried once" and "tried six times over half an
        #: hour" is most of what there is to know.
        self.attempts = 1
        where = f" from {step}" if step else ""
        super().__init__(f"Flex error {code} ({error_class.value}){where}: {message}")

"""§8.2 / §14.6.3 — how much data a Sharpe ratio actually needs.

The spec left the gate at "≥30 trading days" and said so plainly: *a
reasonable default, not a researched one*. This module replaces the guess
with the arithmetic.

**The estimator has a known standard error.** For IID returns, the
asymptotic standard error of an estimated Sharpe ratio is

    SE(SR) = sqrt((1 + SR² / 2) / n)

(Lo, *The Statistics of Sharpe Ratios*, Financial Analysts Journal 2002).
`SR` and `SE` are both per-period; annualizing the ratio by √252
annualizes its standard error by the same factor, because scaling an
estimator scales its error.

Two things follow, and they are what makes the threshold defensible:

  * A Sharpe can be reported **with its uncertainty** rather than gated
    behind a cutoff. "3.91 ± 2.05" says everything the cutoff was trying to
    say, and says it continuously.
  * The sample size needed for a Sharpe to be distinguishable from zero
    **depends on the Sharpe**. Rearranging for `SR / SE(SR) ≥ z`:

        n ≥ z² (1 + SR² / 2) / SR²

    A daily Sharpe of 0.25 needs about 65 observations at 95%; a weaker
    one needs far more. So "30 days" is neither right nor wrong in general
    — it is an answer to a question that has a different answer for every
    account, and the app can compute the right one from the data in front
    of it.

The threshold is kept as a **display default**, now configurable, because
some cutoff has to decide whether to draw the number at all. It is no
longer what carries the epistemics; the interval is.

Caveat worth keeping in view: Lo's formula assumes IID returns. Trading
returns are autocorrelated and fat-tailed, both of which make the true
error *larger* than this. So the interval below is a floor on the
uncertainty, not a bound — which is the safe direction to be wrong in.
"""

import math

#: Normal quantiles for the two confidence levels worth reporting.
Z_95 = 1.959963985
Z_68 = 1.0

TRADING_DAYS_PER_YEAR = 252


def sharpe_standard_error(sharpe_per_period: float, n: int) -> float | None:
    """Lo (2002) asymptotic standard error, in per-period units."""
    if n < 2:
        return None
    return math.sqrt((1.0 + (sharpe_per_period**2) / 2.0) / n)


def observations_for_significance(
    sharpe_per_period: float, z: float = Z_95
) -> int | None:
    """How many observations this Sharpe would need to clear zero.

    Returns None for a Sharpe of zero — no sample size makes a zero
    distinguishable from zero, and reporting a huge number would imply
    otherwise.
    """
    if not sharpe_per_period:
        return None
    needed = (z**2) * (1.0 + (sharpe_per_period**2) / 2.0) / (sharpe_per_period**2)
    return max(2, math.ceil(needed))


def describe_sharpe(
    annualized_sharpe: float | None,
    n_observations: int,
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> dict:
    """The Sharpe, its interval, and what the sample would need to be.

    Reported alongside the point estimate everywhere a Sharpe is shown, so
    the reader is never handed a bare 3.91 off ten weeks of data.
    """
    if annualized_sharpe is None or n_observations < 2:
        return {
            "sharpe": annualized_sharpe,
            "standard_error": None,
            "confidence_95": None,
            "significant_at_95": None,
            "observations": n_observations,
            "observations_needed_95": None,
            "note": "Not enough observations to estimate an uncertainty.",
        }

    scale = math.sqrt(periods_per_year)
    per_period = annualized_sharpe / scale
    se_per_period = sharpe_standard_error(per_period, n_observations)
    if se_per_period is None:
        return {
            "sharpe": annualized_sharpe,
            "standard_error": None,
            "confidence_95": None,
            "significant_at_95": None,
            "observations": n_observations,
            "observations_needed_95": None,
            "note": "Not enough observations to estimate an uncertainty.",
        }

    se = se_per_period * scale
    low, high = annualized_sharpe - Z_95 * se, annualized_sharpe + Z_95 * se
    significant = low > 0 or high < 0
    needed = observations_for_significance(per_period)

    if significant:
        note = (
            f"{annualized_sharpe:.2f} ± {se:.2f} (1 s.e.); the 95% interval "
            f"[{low:.2f}, {high:.2f}] excludes zero on {n_observations} observations."
        )
    elif needed is not None:
        note = (
            f"{annualized_sharpe:.2f} ± {se:.2f} (1 s.e.). The 95% interval "
            f"[{low:.2f}, {high:.2f}] includes zero, so this is not yet distinguishable "
            f"from no edge at all — a Sharpe this size needs about {needed} observations "
            f"and there are {n_observations}."
        )
    else:
        note = (
            f"{annualized_sharpe:.2f} ± {se:.2f} (1 s.e.). A Sharpe of zero is never "
            "distinguishable from zero at any sample size."
        )

    return {
        "sharpe": annualized_sharpe,
        "standard_error": se,
        "confidence_95": [low, high],
        "significant_at_95": significant,
        "observations": n_observations,
        "observations_needed_95": needed,
        "note": note,
        "method": (
            "Standard error from Lo (2002), SE(SR) = sqrt((1 + SR²/2)/n), annualized by "
            "the same √252 as the ratio. Assumes IID returns; real trading returns are "
            "autocorrelated and fat-tailed, both of which widen the true interval — so "
            "this is a floor on the uncertainty, not a bound (§14.6.3)."
        ),
    }

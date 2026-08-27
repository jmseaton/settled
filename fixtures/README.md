# Test Fixtures

Three files, derived from real IBKR exports and then anonymized. Safe for version
control and CI. Referenced by §16 of the build spec.

| File | Source | Purpose |
|---|---|---|
| `fixture-complete.tlg` | IBKR TradeLog export + a reconstructed BCGX opening | Every trade is a complete round trip |
| `fixture-orphan.tlg` | Same, with the reconstructed opening removed | **The realistic case.** BCGX was transferred in by ACATS, so no opening execution exists in the account. Only fixture exercising §5.5 |
| `fixture-flex.xml` | Activity Flex Query, `LastNCalendarDays=90` | 2031-05-12 to 2031-08-08. Drives §16.1 and the cross-format equivalence test |

## Anonymization

These files went through two passes. Both matter, and the second is the one that is
easy to skip.

### Pass 1 — identity scrub

| Original | Replacement | Where |
|---|---|---|
| Account ID | `U0000000` | `.tlg` `ACT_INF` / `STK_LOT` / `OPT_LOT`; XML `accountId` throughout |
| Account holder name | `Test Account Holder` | `.tlg` `ACT_INF` only |
| Mailing address | `1 Test Street  Anytown PA 00000 United States` | `.tlg` `ACT_INF` only |
| Delivering broker's account no. | `Z00000000` | XML `Transfer/@account` |
| Delivering broker DTC id | `0000` | XML `Transfer/@deliveringBroker` |
| Masked account ref in fee lines | `J******00` | XML `CashTransaction/@description` |
| Customer type | `Individual` | XML `AccountInformation/@customerType` |

The Flex XML contains **no** name or address — IBKR's `AccountInformation` element
omits both (`accountName`, `acctAlias`, `masterName` and `traderID` are all empty).

`scripts/check_fixtures_scrubbed.py` enforces this pass on every pull request.

### Pass 2 — de-linking

Removing the account number does not de-identify a brokerage statement. The trades
still named **real instruments on real dates**, and the price of a real instrument on
a real date is public. Anyone could divide a fixture price by the published close,
recover the ratio between these files and the original, and read the account's size,
deposits and P&L straight off. Scrubbing the identifiers alone would have left all of
that in the open.

So, in addition:

- every instrument was replaced with a fictional ticker, and its `conid`, `isin`,
  `cusip`, `securityID` and `figi` regenerated (ISINs and CUSIPs carry valid check
  digits, so format validation still passes);
- every date was shifted by a constant whole number of weeks, chosen so that each
  trading day maps onto another NYSE trading day and every weekday is preserved;
- every monetary value — prices, strikes, proceeds, commissions, fees, cost basis,
  cash transactions and NAV alike — was scaled by a single constant, in exact decimal
  arithmetic.

**The ticker map, the date offset and the scale factor are deliberately not recorded
here or anywhere else in this repository.** Publishing them would undo the pass.

The futures root (`MES`) is left as-is: `app/contracts/reference.py` keys its
multiplier, tick and exercise-style rules on the real product root, so a fictional one
would not resolve. The date shift is what de-links it — the window now sits far enough
out that no index history exists to compare against.

### What this preserves, and what it costs

Scaling is uniform and linear, so every relationship in the data still holds exactly:
`proceeds = price × quantity × multiplier`, `netCash = proceeds + commission`, leg sums
against group totals, both reconciliations. Ratios are scale-invariant and so are
unchanged — `ChangeInNAV/@twr` is the same number it always was. Every structural quirk
these fixtures exist to exercise survives untouched: the ACATS orphan, the two-leg combo
groups, the `CLOSED_LOT` records, the CSV-vs-XML trap, the futures-option series codes.

What it costs: the values are **no longer byte-identical to broker output**. They are a
faithful linear image of it, which is what the acceptance tests actually depend on.

## Verified after anonymization

```
fixture-flex.xml      78 executions  sum(fifoPnlRealized) = 437.68  sum(ibCommission) = -21.62
                      ChangeInNAV twr = 10.24784403   start 389.15029091478   end 4350.85747501788
                      22 two-leg brokerageOrderID combo groups
fixture-orphan.tlg    78 executions  sum(-proceeds) =  1549.56  commissions = -21.62  (1 BCGX row)
fixture-complete.tlg  79 executions  sum(-proceeds) =  -749.65  commissions = -22.11  (2 BCGX rows)
```

`fixture-orphan.tlg` and `fixture-flex.xml` describe the same 78 executions and are the
pair used for the cross-format equivalence test (spec test 26).

## Caution: the reconstructed BCGX opening is wrong

`fixture-complete.tlg` line 6 is a hand-built opening at **22.9921** dated `20310409` —
outside the file's own `20310602`–`20310808` window, with a trade ID adjacent to the
sell's and an identical timestamp and commission.

IBKR's actual carried-over basis is **24.90388/share** (`Lot/@cost = 2490.388`,
`fifoPnlRealized = 66.33080756`), and the transfer-date mark was **25.98275/share**
(`Transfer/@positionAmount = 2598.275`).

So the same trade yields +257.02, +66.33, or -41.07 depending on basis. Use this file
for round-trip mechanics only — never as a source of truth for BCGX P&L. See spec §5.5.

## Regenerating

If you regenerate either file from IBKR, **re-run both passes** before committing — the
identity scrub *and* the de-linking.

CI checks pass 1 in full. It cannot check pass 2 directly, because a checker would have
to contain the very mapping that undoes it. What it does instead is check the one thing
that gives a raw export away for free: a regenerated file carries present-day dates, so
any date before `EARLIEST_PLAUSIBLE_YEAR` fails the build. That catches the realistic
mistake — regenerate, forget, commit — without recording anything that would reverse the
anonymization.

Git history is permanent — anonymize before the first commit, not after.

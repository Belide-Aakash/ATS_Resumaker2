# card: sponsorship

Companion to `recipes/sponsorship.md`. Update in the SAME commit.

## Purpose

Turn an employer name into an evidence-backed H-1B likelihood tier from USCIS data,
or `unknown` when there is no record, one signal in the match, never a verdict on
its own.

## What it CAN verify

- How many H-1B approvals an employer has across the last three USCIS fiscal years,
  and the approval rate, traced to specific CSV rows.
- Whether a name matched **exactly**, by **prefix**, or by **fuzzy** typo fallback
  (the match confidence).
- Whether the JD itself states a sponsorship stance (that overrides history).

## What it CANNOT verify

- Whether a fuzzy name match is **actually the same legal employer** (subsidiaries,
  DBAs, renames). It flags `needs_verification`; a human confirms.
- **Future** sponsorship intent. USCIS data is historical; a company that sponsored
  heavily may have frozen, or vice versa. Past behavior is not a promise.
- Sponsorship for **this specific role/office**, the data is employer-level.

## Dependencies

- `data/cache/sponsorship/h1b_datahubexport-{2021,2022,2023}.csv`, `rapidfuzz`.
  No network, no LLM.

## Commands (annotated)

```bash
# the tier contract: zero history -> unknown; thresholds must actually clear
uv run pytest tests/unit/test_sponsorship_scorer.py -q

# sponsorship in context (runs inside the per-JD match pipeline)
uv run python -m apps.cli track add <job_url>     # match + sponsorship + apply decision
```

## What it produces

- A `SponsorSignal` (tier, confidence, `lca_count_3y`, `approval_rate`,
  `most_recent_fy`, `needs_verification`, `evidence[]`) and, after resolution, a
  `SponsorshipVerdict` (`verdict`, `hard_blocker`, `source`, `reasons`).

## Failure modes (≥4, incl. the two mandatory)

1. **drift (mandatory).** This card and `recipes/sponsorship.md` disagree on the
   thresholds or the match rules. A reader trusts stale numbers. `make verify`
   flags an unpaired/unequal-update.
2. **contract-violation (mandatory).** The USCIS match returns nothing (score
   should be `unknown`), but the tracker still shows a confident tier, e.g. an LLM
   or a UI default fills the gap with "likely to sponsor." That is a fabricated
   record-claim; Verified-Data Rule #2 forbids it. Guard: `_likelihood` returns
   `unknown` on `count_3y == 0` (G1 test); the risk is any caller that overrides it.
3. **Fuzzy false-join.** rapidfuzz matches "Databricks" to a similarly-named but
   different filer, inflating the tier for the wrong company. Guard: fuzzy floor 92
   + `confidence="low"` + `needs_verification`; still needs human sign-off (this is
   the deliberate break attempt in D4).
4. **Stale-data drift.** The shipped CSVs cover FY2021-2023; as years pass the
   "recent" window ages and tiers skew low. Detection: `most_recent_fy` in the
   evidence; refresh the CSVs to correct.
5. **Over-reading likelihood as a gate.** Treating a `low`/`unlikely` tier as a
   reason to auto-skip. It is not a blocker (only explicit JD exclusion is); doing
   so would wrongly drop real employers.

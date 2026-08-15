# recipe: sponsorship: USCIS-backed H-1B signal

Inherits `recipes/_shared.md`. **Scope note:** sponsorship is ONE signal the match
pipeline produces, not a gate on its own. Its job is to turn "Unknown" into a
recorded, evidence-backed likelihood, never to invent one.

## 1. Executive summary

Given an employer name, this stage matches it to the USCIS H-1B Employer Data Hub
(three fiscal years, shipped as CSVs), counts recent approvals, and returns a
likelihood tier (`high` / `medium` / `low` / `unknown`) with a name-match
confidence and the evidence rows behind it. Zero matched approvals returns
`unknown`, not a hopeful guess. A shaky fuzzy name match is marked for human
verification and its tier is capped. Downstream, `resolve_sponsorship` fuses this
history with the JD's own stated stance; only a JD that *explicitly* excludes
sponsorship becomes a hard blocker.

## 2. Required reads

- `recipes/_shared.md`, Verified-Data Rules #2 (no tier without a record) and #3 (fuzzy match is inferred).
- `src/resumaker/stages/sponsorship/scorer.py`, `match_employer`, `_likelihood`, `score_company`.
- `src/resumaker/stages/sponsorship/resolve.py`, `SponsorshipVerdict`, JD-stance fusion.
- `data/cache/sponsorship/h1b_datahubexport-{2021,2022,2023}.csv`, the source records.

## 3. Phase gates

- **G1, No tier without a USCIS record.**
  Command: `uv run pytest tests/unit/test_sponsorship_scorer.py -q`
  Passes when `count_3y == 0 -> "unknown"` and a real tier requires the
  volume/recency/rate thresholds to clear. **Failure path:** if a zero-history
  company returns anything but `unknown`, STOP, the scorer is fabricating a tier;
  do not surface it in the tracker.
- **G2, Fuzzy match needs sign-off.**
  Check: a match below the exact-key/prefix path clears the fuzzy floor
  (`_FUZZY_THRESHOLD = 92`) or is returned `confidence="low"` /
  `needs_verification=True`. **Failure path:** a low-confidence match must never be
  presented as certain identity; it is inferred until a human confirms.

## 4. Primary stored tools

- `src/resumaker/stages/sponsorship/scorer.py :: score_company`, name -> tier + evidence.
- `src/resumaker/stages/sponsorship/resolve.py :: resolve_sponsorship`, history + JD stance -> verdict.
- No standalone CLI subcommand: sponsorship runs inside the match pipeline
  (`apps.cli track add <url>` or `run <url>`).

## 5. Workflow

1. **Normalize + match** (`match_employer`): exact normalized key -> `high`
   confidence; prefix-family -> `low`; else rapidfuzz `token_set_ratio` typo
   fallback, which must clear `92` or is returned `low`.
2. **Count + rate** across the three USCIS FYs: `lca_count_3y`, `most_recent_fy`,
   `approval_rate`.
3. **Tier** (`_likelihood`): `0 -> unknown`; `>=100 & filed_recent & rate>=0.80 ->
   high`; `>=10 & rate>=0.50 -> medium`; else `low`. A low-confidence match caps
   `high -> medium`.
4. **Emit** `SponsorSignal(likelihood, confidence, lca_count_3y, approval_rate,
   most_recent_fy, needs_verification, evidence[])`.
5. **Resolve** (`resolve_sponsorship`): the JD's explicit stance
   (`offers/no_sponsorship/case_by_case/unclear`) overrides history for the
   verdict; `hard_blocker=True` only on an explicit exclusion.

## 6. Output contract

- **Script produces:** a `SponsorSignal` (tier + confidence + counts + evidence)
  and a `SponsorshipVerdict` (`verdict`, `hard_blocker`, `source`, `reasons`).
- **Human reads:** the tracker's sponsorship line (tier + "verify" flag when the
  match is low-confidence) and the evidence list citing FY approval counts.
- Shape rule: `lca_count_3y` and `approval_rate` are always accompanied by the FYs
  they came from; a tier is never shown without its `confidence`.

## 7. Verification checks

- `uv run pytest tests/unit/test_sponsorship_scorer.py -q`, the tier contract (G1).
- Spot check against the CSVs: a high-volume sponsor (e.g. a large tech employer)
  returns `high` with a four/five-figure `lca_count_3y`; a company absent from the
  data returns `unknown`, both traceable to CSV rows.
- `make verify`, recipe/card well-formed and paired.

## 8. Logging rules

- On a real scoring batch, append to `logs/RUN_LOG.md`: `Recipe: sponsorship`,
  inputs (companies scored), outputs (tiers assigned, count rescued from
  `unknown`, count flagged `needs_verification`), and a `### Verification check`
  line naming the CSV rows or the pytest result.

## 9. Stop conditions

- **Stop and return `unknown`** if there is no matched USCIS record. Never emit a
  tier the data does not support.
- **Stop treating a match as certain** when `confidence="low"` or
  `needs_verification=True`, hand it to a human before it influences a decision.
- **Stop short of hard-blocking on likelihood**, a low/unlikely tier is a reason,
  not a gate. Only an explicit JD exclusion (via `resolve_sponsorship`) blocks.

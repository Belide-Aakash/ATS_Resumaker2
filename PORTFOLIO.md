# ATS Resumaker 2.0: an evidence-first job-application platform

*A portfolio write-up for a technical reader. Every number here traces to a script
and a record (see `EVIDENCE_LEDGER.md`) or is labeled a judgment.*

## The problem

A serious job seeker can carefully tailor maybe a handful of applications a week,
but there are thousands of live postings across dozens of companies' hiring
systems. Time is the scarce resource, and it is easy to waste it: on a posting
that is stale, on a role that is a poor fit, or, for someone who needs visa
sponsorship, on a company that will not provide it. The usual tools make this worse
by *fabricating* confidence: a résumé generator that invents a metric, or a
"sponsorship: likely" badge with nothing behind it, spends your effort on a lie.

The specific hard case that motivated it: an international student who cannot tell,
across 80+ companies, which postings are real and live, which plausibly sponsor
H-1B, and cannot afford to mis-apply. Sponsorship is one signal in that decision,
not the whole story.

## What I built

A modular platform whose throughline is **evidence first: advise and draft from
records, never fabricate, never auto-apply.** The attested core is a three-stage
spine:

```
ingest  ->  sponsorship  ->  apply-gate
(zero-token   (USCIS-backed     (fit + hard blockers
 board sweep)   likelihood)       -> apply / skip advice)
```

- **Ingest** sweeps ~25 ATS platforms (Greenhouse, Lever, Ashby, Workday, and
  more) with no API tokens, deduping postings into a database. Its discipline: a
  board that fails to fetch is recorded as an *error with provenance*, never
  silently counted as "0 jobs."
- **Sponsorship** matches an employer to the USCIS H-1B Employer Data Hub (three
  fiscal years) and returns a likelihood tier with the approval rows behind it, or
  `unknown` when there is no record. A shaky fuzzy name match is flagged for a
  human to confirm.
- **Apply-gate** blends a deterministic fit floor with an LLM judgment clamped to
  within 25 points of that floor, then makes a hard-gated recommendation: any hard
  blocker (an explicit no-sponsorship JD when the candidate needs it, or a large
  experience gap) forces "skip" regardless of fit. It advises; the human applies.

Around the spine: a scheduled email digest of new on-target roles; a self-improving
agentic onboarder that writes a new ATS adapter, sandbox-tests it, and opens a pull
request for review; a one-click browser extension that captures any posting into
the tracker; grounded résumé generation behind a mechanical fact-gate; and
dashboards. It runs at roughly zero metered cost (Claude CLI on a subscription;
free-tier cloud).

## One honest, measurable improvement

**The EMPTY-vs-ERROR discipline turns silent connector failures into visible
errors.** A diagnostic pass (`JOB_INGESTION_FIXES.md`) found **8 watchlist
companies returning 0 jobs** that were not empty boards but *broken fetches*
masquerading as "0 jobs," plus a Workday adapter under-fetching (2 of ~15 pages).
Before this discipline, those companies looked covered and healthy; after it, they
surface as errors with their `source/token` and reason. This is now locked by tests
(`tests/unit/test_ingestion.py`) so a future connector cannot swallow its own
failure and report a false zero.

A second, traceable win: sponsorship moves from a blank "Unknown" to an
evidence-backed tier where USCIS data supports it (for example, Amazon shows
**46,630** H-1B approvals over three years, reproducible from the shipped CSVs),
while companies with no record correctly stay `unknown` rather than getting an
invented tier.

## Verified vs inferred (stated plainly)

- **From records:** posting counts and coverage (from the database); sponsorship
  approval counts and tiers (from USCIS CSVs); the apply decision and its blockers
  (deterministic).
- **Inferred, and labeled as such:** the LLM half of the fit score (clamped ±25 of
  the deterministic floor); any fuzzy employer name match (flagged
  `needs_verification` until a human confirms).
- **Profile-specific:** every fit / apply / ATS-score number is computed against
  one profile, so it illustrates one real user's runs, not the system's general
  accuracy.

Full field-by-field boundary in `EVIDENCE_LEDGER.md`.

## Failure modes and the one limitation it cannot verify

- A fuzzy employer match can join to the wrong legal entity (shown live: a
  misspelled name mis-joined to a different filer). The guard is a
  `needs_verification` flag and low confidence; a human still has to confirm.
- There is **no liveness or timeline gate** (not implemented): the system can
  recommend applying to a posting that has since closed.
- **The limitation it cannot verify:** whether an application will lead to an
  interview. An ATS-clean, well-matched résumé is necessary, not sufficient. Timing,
  who applied first, referrals, and ghost postings all sit outside what the tool can
  see. It drafts and advises; a human owns the decision to apply.

## Demo (openable)

- **Explainer video** (5:49): a walkthrough of the live platform with the
  evidence-first framing.
- **Run it yourself** (no PII, no keys needed for these):
  ```bash
  make verify            # governance conformance: recipes/cards well-formed + paired
  make doctor            # health report + PII scan over the git index
  uv run pytest tests/unit/test_apply_gate_behavior.py -q   # the gate is a gate, not a vote
  ```
- **The evidence trail:** `EVIDENCE_LEDGER.md` (every number -> its source),
  `logs/RUN_LOG.md` (a real run with two break attempts), `attestation.md`.

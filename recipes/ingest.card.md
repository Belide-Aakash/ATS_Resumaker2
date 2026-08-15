# card: ingest

The human-readable companion to `recipes/ingest.md`. Same commands, but framed as
"what can go wrong and how I'd know." Update this in the SAME commit as the recipe.

## Purpose

Sweep watched companies' ATS boards into `data/resumaker.db`, separating new from
unchanged and, the point, separating a genuinely empty board from a failed one.

## What it CAN verify

- That a board fetch **succeeded or failed**, and on failure, which `source/token`
  and why (the exception string is kept).
- That a posting is **new or unchanged** vs the last sweep (content-hash dedupe).
- Coverage counts (`new`, `unchanged`, `errors`) that trace to DB rows.

## What it CANNOT verify

- Whether an **empty** board is truly empty or is a soft failure returning `[]`
  (a 403 that yields an empty list). It flags empties with a warning but cannot, on
  its own, distinguish "no jobs" from "silently blocked", that needs a probe.
- Whether a posting is still **live** at the source right now (no liveness check).
- Whether the adapter under-fetched (e.g. paginated boards returning page 1 only).

## Dependencies

- `data/resumaker.db` (watchlist + `jobs`), network access to the boards,
  `src/resumaker/providers/sources/` adapters. No API key, no LLM.

## Commands (annotated)

```bash
# one board, live: prints new/unchanged and an errors=[...] clause only on failure
uv run python -m apps.cli ingest greenhouse <token>

# one full watchlist tick (all due boards)
uv run python -m apps.cli schedule --once

# the contract tests (EMPTY vs ERROR, dedupe, failure isolation)
uv run pytest tests/unit/test_ingestion.py -q
```

## What it produces

- Upserted rows in `data/resumaker.db :: jobs`; an `IngestResult` per company
  (`new / unchanged / errors[] / new_jobs[]`); a CLI summary line; app log lines.

## Failure modes (≥4, incl. the two mandatory)

1. **drift (mandatory).** This card and `recipes/ingest.md` disagree (a command or
   gate changed in one, not the other). Then one of them is wrong and any reader
   following the stale one is misled. `make verify` flags a recipe/card that were
   not updated together.
2. **contract-violation (mandatory).** A board fetch **errors**, but the failure is
   turned into `0` instead of an `errors[]` entry, so the tracker/mailer shows
   "0 new jobs" for a company that is actually unreachable, a silent-failure lie.
   Guarded by `_fetch_board` returning the error as a value and the G1 test; the
   real risk is a future adapter that swallows its own exception and returns `[]`.
3. **Soft-failure empty.** A board returns `[]` because of a 403/anti-bot page, not
   because it is empty. Counted as EMPTY (correctly not an error), but coverage
   silently drops. Detection: the "board returned no postings" warning + a probe of
   the source's real count.
4. **Under-fetch / pagination cap.** An adapter returns only the first page, so
   `new` looks plausible but coverage is a fraction of reality (observed on Workday
   in `JOB_INGESTION_FIXES.md`). No test catches it; needs a direct-API spot check.
5. **Dedupe-key drift.** If listing fields feeding the content hash change shape,
   unchanged postings re-count as `new`. Guarded by the dedupe test (G2).

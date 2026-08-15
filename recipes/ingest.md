# recipe: ingest: the zero-token board sweep

Inherits `recipes/_shared.md` (Prime Directive, Sources of Truth, Verified-Data Rules).

## 1. Executive summary

Ingest lists the postings on each watched company's ATS board (Greenhouse, Lever,
Ashby, Workday, and ~25 more), dedupes them into the `jobs` table, and reports
what is new. Its defining discipline is that a board that fails to fetch is
recorded as an **error with provenance**, never silently counted as "0 jobs", so
a broken connector can never masquerade as an empty one. No LLM is involved and no
API token is spent; this is deterministic collection.

## 2. Required reads

- `recipes/_shared.md`, the verified-data rules this recipe enforces (esp. #1).
- `DATA_CONTRACT.md`, where postings land (`data/resumaker.db`, Layer 2) and why the DB is never committed.
- `src/resumaker/ingestion/service.py`, `_fetch_board`, `ingest_company`, `ingest_all`.
- `src/resumaker/providers/sources/`, the board adapters and the `get_source` registry.

## 3. Phase gates

- **G1, EMPTY vs ERROR separation.**
  Command: `uv run pytest tests/unit/test_ingestion.py -k "errors_not_zero or empty_not_error or sink_its_siblings" -q`
  Passes when a raising board lands in `IngestResult.errors` (with `source/token`)
  and an empty board does not. **Failure path:** if a fetch error becomes a `0`,
  STOP, do not scale the sweep or notify on the result; the connector is lying
  about coverage. Route the failure to `errors` and fix the adapter first.
- **G2, Idempotent re-ingest.**
  Command: `uv run pytest tests/unit/test_ingestion.py -k dedupe -q`
  Passes when a re-run of unchanged postings yields `new == 0`. **Failure path:**
  if re-ingest inflates `new`, STOP, the content-hash/dedupe key is broken and
  every downstream count is untrustworthy.

## 4. Primary stored tools

- `apps/cli/main.py :: _cmd_ingest`, single-board sweep (`ingest <source> <token>`).
- `apps/cli/main.py :: schedule --once`, one full watchlist tick (`ingest_all`).
- `src/resumaker/ingestion/service.py`, the library both call.

## 5. Workflow

1. **Select boards.** A tick reads the watchlist from `data/resumaker.db`
   (`ingest_all`), or you name one board directly (`ingest greenhouse <token>`).
2. **Fetch (network).** `_fetch_board` calls `get_source(board.source).list_postings(...)`
   inside a try/except and returns `(company, board, stubs, error)`, a failure is
   a value, not an exception.
3. **Classify.** `error != ""` -> append to `res.errors`, skip. `stubs == []` ->
   log "board returned no postings" (EMPTY, distinct from error). Otherwise ->
   step 4.
4. **Filter + upsert (DB only).** `_record_stubs` drops non-tech / non-US / non-
   preferred titles, then `db.upsert_job` keys on `(source, external_id)` with a
   content hash so only genuinely new/changed rows increment `new`.
5. **Output lands** in `data/resumaker.db` (`jobs`); counts + new rows return in
   `IngestResult`.

## 6. Output contract

- **Agent/script produces:** `IngestResult(company, new, unchanged, errors[], new_jobs[])`
  per company; rows upserted into `data/resumaker.db :: jobs`.
- **Human reads:** the CLI summary line (`new / unchanged / errors=N`) and, when
  auditing, the `errors[]` list naming each failed `source/token`.
- Shape rule: `errors` is a list of strings, never a count folded into `new`. An
  empty board contributes `0 new` **and** `0 errors`.

## 7. Verification checks

- `uv run pytest tests/unit/test_ingestion.py -q`, the EMPTY/ERROR + dedupe contracts.
- Live spot check: `uv run python -m apps.cli ingest greenhouse <token>` prints
  `new` vs `unchanged`; point it at a bad token and confirm the run reports an
  `errors=[...]` clause rather than `0`.
- `make verify` confirms this recipe has all 9 sections and a paired card.

## 8. Logging rules

- Append one entry per real sweep to `logs/RUN_LOG.md`: date, `Recipe: ingest`,
  inputs (sources/scope), outputs (`new / unchanged / errors` totals), and a
  `### Verification check` line pointing at the DB counts or pytest result.
- The service itself logs an `ingested` info line per company and an `error` line
  per failed board (structured, in the app logs), the RUN_LOG is the human digest.

## 9. Stop conditions

- **Stop scaling if any board errors**, record it in `errors`, keep ingesting the
  rest (failure isolation), but do not report the sweep as clean coverage.
- **Stop starting new fetches once the wall-clock budget passes** (`settings.py`
  ingest budget, ~1200 s). A deadline cut is normal; idempotent re-ingest picks up
  skipped boards next tick.
- **Stop and do not notify** if the EMPTY/ERROR gate (G1) is red, a masked failure
  must never reach the mailer/tracker as "0 new."

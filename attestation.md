# attestation.md

Attestation for the attested spine (ingest -> sponsorship -> apply-gate). Written
against what was actually run on 2026-08-14; evidence in `logs/RUN_LOG.md` and
`EVIDENCE_LEDGER.md`. No verdict sentence: this records what was and was not
verified, and a human signs it.

### Tested

- **Apply gate is a gate, not a vote.** `tests/unit/test_apply_gate_behavior.py`
  (4 tests, green): a hard blocker forces no-apply at 95/100 fit; low sponsorship
  likelihood does not block; absent blockers, the 60/45 thresholds govern. Shown
  RED on a vote-refactor (see RUN_LOG break attempt 1).
- **EMPTY vs ERROR.** `tests/unit/test_ingestion.py` (added 3 tests, green): a
  raising board lands in `errors[]` with `source/token`; an empty board does not;
  one bad board does not sink its siblings.
- **No fabricated sponsorship tier.** `tests/unit/test_sponsorship_scorer.py`
  (3 tests, green): `count_3y == 0 -> unknown`; tiers require their thresholds.
- **Sponsorship reproduces from records.** `sponsor_signal()` over the real USCIS
  CSVs returns Amazon `high` / 46,630 approvals and `unknown` for a nonexistent
  company (RUN_LOG break attempt 2).
- **Governance conformance + privacy.** `make verify` OK (3 recipes/cards paired);
  `make doctor` privacy OK (303 tracked files, 0 PII/secret).
- **Full suite:** `uv run pytest -q` -> 199 passed.

### Did not test

- **A live full ingest sweep** against the real boards this session (network). The
  ingest EMPTY/ERROR logic is unit-tested with fakes; the corpus counts come from
  the 2026-08-13 DB snapshot, not a fresh sweep run here.
- **The full per-JD pipeline end-to-end with the LLM** (match -> grounded resume ->
  fact-gate -> ATS score) this session. Per-run numbers are from prior recorded
  runs (`data/resumaker.db`), a small sample (4 runs), and are profile-specific.
- **Liveness / OPT-timeline gating**, not implemented, so nothing to test; the
  gate does not consume a liveness or timeline signal.
- **Board under-fetch** (e.g. Workday pagination cap), observed in
  `JOB_INGESTION_FIXES.md`, no automated guard, not tested here.
- **Whether a `needs_verification` fuzzy match is the correct legal entity**, the
  scorer cannot decide this; a human must, and that human step was not exercised.
- **The OpenCATS / Affinda resume validation as a committed artifact**, done by
  the owner on a real ATS and observed to work, but not captured as a citable
  record in this repo; treated as `local-evidence`, not a tested claim here.
- **Cross-profile generalization**, every match/apply/ATS number was produced
  against one profile; the system was not run across multiple profiles.

### Broke during testing, fixed

- **Break attempt 1 (gate-as-vote): caught, no code fix needed.** The existing
  short-circuit gate already resists it; the test now locks that property so a
  future refactor into a vote fails CI.
- **Break attempt 2 (fuzzy false-join): surfaced a real limitation, not a defect
  to patch.** `"Amozon Web Servcies"` mis-joined to the wrong employer, but the
  `needs_verification` + low-confidence guard fired, so it was never presented as
  certain. The honest fix is procedural (a human confirms flagged matches), not a
  code change; it is documented as a limitation, not hidden.
- **Coverage gap closed.** The ERROR-bucket path in ingestion was previously
  unverified by tests; that gap is now closed by the three EMPTY/ERROR tests. This
  is a test addition, not a fix to broken behavior.

---

**Signature (a human signs; the system does not certify its own honesty):**

I have read the above and confirm it reflects what was and was not verified.

Signed: Aakash Belide (belide.a@northeastern.edu)   Date: 2026-08-14

# EVIDENCE_LEDGER.md: verified vs inferred, and where every number comes from

The attestation's evidence base for the attested spine (ingest -> sponsorship ->
apply-gate). Two parts: (1) a boundary table labeling every field the spine emits,
and (2) an every-number-traces ledger naming the script and the record behind each
figure. The rule (from `recipes/_shared.md`): a number without a record is labeled
a judgment or is not shown.

**Provenance labels:** `record` · `script-output` · `local-evidence` ·
`external-source` · `model-inference` · `your-input` · `missing`.

**Snapshot + generalization note.** Corpus numbers come from the local replica DB
`data/resumaker.db` (snapshot 2026-08-13; the live Turso DB may be slightly ahead)
and are a **current, growing** count, not a fixed total. Per-run match numbers
(fit, apply, ATS score) are computed against the **owner's** `data/profile/profile.json`
and are **profile-specific**, a different person's profile yields different
numbers. They illustrate one real user's runs, not the system's general accuracy.

---

## 1. Boundary table (what the spine emits)

| Field the spine emits | Label | Traces to |
|---|---|---|
| ingest `new` / `unchanged` counts | `script-output` | `ingestion/service.py` -> `data/resumaker.db :: jobs` |
| ingest `errors[]` (failed board + reason) | `script-output` (`local-evidence` of the failure) | `_fetch_board` caught exception string |
| EMPTY vs ERROR classification | `script-output` | `ingest_company` (`errors` vs "no postings" warning) |
| distinct companies / sources / postings | `script-output` | `data/resumaker.db` aggregates |
| sponsorship `likelihood` (tier) | `script-output` derived from `external-source` | `scorer._likelihood` over USCIS CSVs |
| sponsorship `lca_count_3y`, `approval_rate`, `most_recent_fy` | `external-source` | `data/cache/sponsorship/h1b_datahubexport-*.csv` |
| sponsorship name-match `confidence` / `needs_verification` | `model-inference until human sign-off` | `scorer.match_employer` (rapidfuzz) |
| sponsorship `hard_blocker` / `verdict` | `record` (from JD text) or `external-source` | `resolve.resolve_sponsorship` (JD stance overrides history) |
| fit `deterministic_0_100` | `script-output` | `role_fit.py` deterministic dimensions |
| fit `llm_0_100` (clamped ±25) | `model-inference` | `role_fit.py` LLM call, clamped to det±25 |
| fit `final_0_100` (0.5 det + 0.5 llm) | `script-output floor + model-inference clamp` | `role_fit.py:99` |
| apply `recommend_apply` / `confidence` | `script-output` | `apply_decision.decide_apply` (deterministic) |
| apply `blockers[]` | `script-output` | `decide_apply` (explicit-sponsorship-exclusion, ≥3y gap) |
| liveness / OPT-timeline gate | `missing` | **not implemented**, no such input to the decision |
| fact-gate `passed` / `blockers[]` | `script-output` vs `your-input` | `ats/fact_gate.py` over `data/profile/profile.json` |
| candidate profile facts (metrics, employers, titles) | `your-input` | `data/profile/profile.json` |
| ATS parse / score of a generated doc | `script-output` (profile-specific) | `ats/` round-trip verify + score |

## 2. Every-number-traces ledger

Each figure the deliverables may cite, with its script + record. Numbers are the
2026-08-13 `data/resumaker.db` snapshot (current + growing), unless noted.

**Corpus / ingestion (generalizable, not profile-specific):**
- **7,443 postings ingested**, `data/resumaker.db :: jobs` count. `script-output`.
- **80 distinct hiring companies in postings; 83 on the watchlist**, DB aggregates. `script-output`.
- **23 ATS sources actively producing postings; 30 adapters implemented**, DB `source` distinct vs the `providers/sources` registry. `script-output` / `structural-fact`.
- **867 distinct locations; ~1,860 postings/day over a ~4-day window**, DB aggregates. `script-output`.
- **Postings by source** (Workday 2,388 · Greenhouse 1,443 · Ashby 765 · …), DB group-by. `script-output`.
- **8 boards returned 0 jobs; Workday under-fetched ~40 of thousands (2 of ~15 pages)**, `JOB_INGESTION_FIXES.md` diagnostic. `local-evidence`.

**Sponsorship (external-source derived, reproducible from the CSVs):**
- **3-year H-1B approvals**, Amazon **46,630** · Google **17,377** · Microsoft **15,719** · Apple **11,598** · Meta **7,891** · JPMorgan **7,134** · Anthropic **7**, `scorer` over `data/cache/sponsorship/*.csv`. `external-source` + `script-output`.
- **Tiers**: `unknown` when `count_3y == 0` (no fabricated tier). `script-output` per Verified-Data Rule #2.

**Config / thresholds (developer-set, `your-input`, in source):**
- apply/marginal/high-confidence fit bars **60 / 45 / 75**, `apply_decision.py:21,22,76`.
- sponsorship volume/rate bars **100 / 0.80 / 10 / 0.50**; fuzzy floor **92**, `scorer.py`.
- LLM fit clamp **±25**, det/LLM blend **0.5/0.5**, `role_fit.py:98,99`.
- Gemini budget cap **$5.00**, ingest budget **1200 s**, onboarding max turns **60**, `settings.py`.

**Per-run match numbers (PROFILE-SPECIFIC, one real user's runs, not a benchmark):**
- pipeline runs recorded **4** (small sample); avg fit **42.8/100**; avg ATS score of generated docs **88.4/100**; fact-gate **passed** on the completed run, `data/resumaker.db :: runs`. `script-output` (fit half is `model-inference`), profile-specific.
- tracked applications **1 in snapshot** (2 live), avg fit of tracked **68.7/100**, DB `tracker`. Profile-specific.
- unique postings emailed by the digest **4,913**, DB. `script-output`.

**Cost (state precisely):**
- **~$0 metered** LLM cost, Claude CLI on the owner's subscription; `usage.jsonl` `cost_usd` sums to **~$32** but those are subscription calls "logged for visibility," not metered spend (`providers/llm/claude_cli.py`). Honest phrasing: *"~$32 of equivalent LLM work at $0 metered cost."* Never "$32 spent."
- **~$0/month infra** on free tiers at this volume, architecture claim (Cloud Run/Turso/GCS/Vercel free tiers), not a billing export.

**Do NOT present as measured** (`illustrative-mock`, hard-coded UI in `web/app/page.tsx`): "68/100 apply," "$6M fraud prevented," "71" fit badge, free-tier "% headroom" bars, MockMatch "80%." Fine as animated demo UI; never captioned as results.

## 3. Ethics gate: shown passing

**(a) Privacy.** `make doctor` scans the actual git index (`git ls-files`) against
the Layer-4 patterns in `DATA_CONTRACT.md` and hard-fails on any tracked
`data/`, `.env*`, `*.tfstate`, `profile.json`, `resume.*`, or `*.csv`. Latest run:
scanned **303 tracked files, privacy: OK** (no PII/secret tracked). `terraform.tfstate`
is gitignored and holds only Secret Manager *references*, not values.

**(b) Honesty.** The fact-gate (`src/resumaker/ats/fact_gate.py`) mechanically
blocks any metric, employer, or title on a generated resume that is not in the
profile (`passed = not blockers`). It is the evidence that the system does not
invent status or numbers about the candidate. The Verified-Data Rules
(`recipes/_shared.md`) and this ledger extend that discipline to every number the
deliverables cite.

## 4. Documentation reconciliation (code-verified)

Closing the D1 doc-vs-disk honesty items against the code in this repo, so no stale
count or half-built feature is presented as current:

- **ATS source adapters: 28**, from the live registry, not a doc claim. Reproducible:
  `uv run python -c "from resumaker.providers.sources import available_sources; print(len(available_sources()))"`.
  Most are multi-tenant ATS platforms (Greenhouse, Lever, Ashby, Workday, iCIMS,
  SmartRecruiters, Oracle Cloud, and more); a few are company-specific scrapers
  (Amazon, Apple, Google, Meta, Microsoft, Tesla). Some older product docs cite "24
  adapters" as an earlier snapshot; the code-verified current count is 28.
- **Companies: watchlist seed is 79** (`watchlist.seed.json`), and the live watchlist
  grows past that through onboarding (the 2026-08-13 DB snapshot shows about 83
  watched and about 80 appearing in postings). Older docs citing "~77 companies" are
  earlier, historical snapshots. This is a live, growing number, not a fixed total.
- **RB.5 (Profile chat-agent): not implemented yet.** It exists only as a design POC
  (`pocs/profile_agent/`) and is marked Todo in the internal build log; it is not a
  shipped feature and is not counted among working capabilities here.
- **SETUP.md script references resolve on disk** (`scripts/bootstrap.sh`,
  `scripts/run-local.sh` are present); `make doctor` re-checks that each run.

# recipes/_shared.md: the contract every recipe inherits

Every recipe in this folder assumes this header. It states the one directive, the
files that count as truth, and the verified-data rules that make a number
trustworthy. A recipe that violates any rule here is wrong even if it runs.

---

## Prime Directive

**Advise and draft from evidence; never fabricate, never auto-apply.**

The system exists to spend the user's scarce application effort on roles that are
real, live, and worth it, and to produce documents that survive a real ATS. It
does that by keeping every claim traceable:

- A **mechanical fact-gate** (`src/resumaker/ats/fact_gate.py`) blocks any metric,
  employer, or title on a generated resume that is not present in the source-of-
  truth profile. Grounding is not a suggestion; it is a hard gate.
- The system is **human-in-the-loop by design**: it scores, drafts, and
  recommends. It **never auto-applies**. The final apply / skip call is the user's.

If a recipe cannot get a number from a record, it says so ("not implemented yet"
or "model-judgment") rather than printing a confident value.

## Sources of Truth

The only things a recipe may treat as authoritative input.

| Source | Path | Layer (see `DATA_CONTRACT.md`) | Trust |
|---|---|---|---|
| Owner profile (PII + history) | `data/profile/profile.json` | Private/PII | `your-input`, the fact-gate's ground truth |
| Owner preferences / house rules | `data/profile/preferences.json`, `house_rules.json` | Private/PII | `your-input` |
| USCIS H-1B data (3 FYs) | `data/cache/sponsorship/h1b_datahubexport-{2021,2022,2023}.csv` | Source Data | `external-source` |
| Live ATS boards | Greenhouse/Lever/Ashby/Workday/... APIs via `src/resumaker/providers/sources/` | Source Data | `local-evidence` (captured per run) |
| Ingested postings / runs / tracker | `data/resumaker.db` (mirrors Turso) | Source Data | `script-output` |

Anything an LLM emits (fit rationale, keyword expansion, gap phrasing) is **never**
a source of truth. It is a judgment, clamped and labeled, and always subordinate
to the deterministic floor and the fact-gate.

## Verified-Data Rules

1. **A board timeout or error is a value, not a zero.** A failed fetch routes to
   `IngestResult.errors` with its `source/token`; it never becomes "0 jobs."
   EMPTY (fetched, returned `[]`) and ERROR (fetch raised) are distinct and stay
   distinct. (`src/resumaker/ingestion/service.py`.)
2. **No sponsorship tier without a USCIS record behind it.** `likelihood` is
   `unknown` when `count_3y == 0`; a real tier requires matched approval rows from
   the CSVs. A low-confidence name match is capped (`high` -> `medium`).
   (`src/resumaker/stages/sponsorship/scorer.py`.)
3. **A fuzzy employer match is inferred until a human confirms it.** rapidfuzz
   joins above the fuzzy floor (92) are `confidence="low"` / `needs_verification`,
   never presented as certain identity.
4. **A fit score is a judgment, never a fact.** `final_0_100` is
   `0.5*deterministic + 0.5*(LLM clamped to deterministic +/-25)`
   (`stages/role_fit.py`). It is labeled a score, never a measurement, and the LLM
   can never move it more than 25 points off the deterministic floor.
5. **The apply decision is a hard gate, not a vote.** A hard blocker (a JD that
   explicitly excludes sponsorship the candidate needs, or a >=3y experience gap)
   forces no-apply regardless of fit. This is locked by
   `tests/unit/test_apply_gate_behavior.py`. *Honest boundary:* sponsorship
   *likelihood* and liveness/OPT-timeline are **not** hard gates, likelihood is a
   soft reason, and liveness/timeline gating is **not implemented**. Recipes must
   not claim otherwise.
6. **A generated document that fails the fact-gate does not ship.** Unsupported
   metric / unknown employer / forbidden phrase => `passed=False`; the run stops
   at the gate.
7. **Nothing private or generated is committed.** Per `DATA_CONTRACT.md`, `data/`,
   `outputs/`, `.env*`, and `*.tfstate*` stay out of git; `make doctor` enforces it.

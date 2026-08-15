# DATA_CONTRACT.md

The machine-checkable version of the promise the README already makes in prose
("everything traces to your profile; PII never leaves this repo"). It partitions
every path in the repo into one of four layers and states, per layer, who may
write it, whether it may be committed, and what trusts it as truth. `make doctor`
enforces the one hard rule at the bottom (no PII in git); `make verify` enforces
the governance-file rules.

The single invariant: **source of truth flows one way.** Private input and
external records flow *into* the system; generated artifacts flow *out*. Nothing
generated is ever read back as truth, and nothing private or generated is ever
committed.

---

## Layer 1: Maintained system (the code)

Human-authored source. Reviewed, tested, committed. This is the only layer a PR
changes.

| Path | What it is |
|---|---|
| `src/resumaker/` | the library: ingestion, stages (sponsorship, role_fit, apply_decision), ats (fact_gate), providers (sources, llm), persistence |
| `apps/` | the argparse CLI (`apps/cli`) and the FastAPI app (`apps/api`) |
| `web/`, `extension/` | Next.js dashboard, MV3 browser extension |
| `scripts/`, `deploy/` | `bootstrap.sh`, `run-local.sh`; Terraform + Docker (config, not secrets) |
| `tests/` | the test suite (193 tests, incl. the gate-behavior + EMPTY/ERROR contracts) |
| `recipes/`, `*.md` governance | recipes + cards, this file, `attestation.md`, `logs/RUN_LOG.md` |

**Rule:** committed; changed only by review. No literal PII, secret, or generated
number is hard-coded here (a coverage/fit number in code would be a contract
violation; those live in the DB and in `*-audit.md`).

## Layer 2: Source data (external + captured records, read-only inputs)

Records the system reads to produce evidence. Trusted as *input truth*, never
edited by the system, **never committed** (large and/or third-party).

| Path | What it is | Provenance |
|---|---|---|
| `data/cache/sponsorship/h1b_datahubexport-{2021,2022,2023}.csv` | USCIS H-1B Employer Data Hub, 3 fiscal years | `external-source` (public USCIS) |
| `data/cache/` (board responses, usage logs) | cached ATS API responses, `usage.jsonl` | `local-evidence` (captured) |
| `data/resumaker.db` | local SQLite/libSQL replica of ingested postings, runs, tracker | `script-output` (mirrors Turso) |

**Rule:** gitignored (`data/`, `*.csv`). The system reads these; it does not
rewrite the CSVs. Every sponsorship number must name the CSV row behind it.

## Layer 3: Generated (outputs, never source of truth)

Everything the pipeline produces. **Never committed, never read back as truth.**

| Path | What it is |
|---|---|
| `outputs/<slug>/` | per-run `report.json`, `status.json`, rendered DOCX/PDF, cover letters |
| `*-audit.md` | the every-number-traces ledgers, written next to the data they inspect |
| generated resume/cover artifacts | tailored documents (may contain profile PII) |

**Rule:** gitignored (`outputs/`, `**/outputs/`). A generated artifact is evidence
of a run, not an authority; if code ever imported a number from `outputs/` as a
fact, that is a contract violation.

## Layer 4: Private / PII / secrets (never leaves the machine)

| Path | What it is |
|---|---|
| `data/profile/profile.json` (+ `preferences.json`, `house_rules.json`) | the owner's real contact PII (phone, email, address) and history: the fact-gate's source of truth |
| `.env`, `.env.*` | API keys / DB URLs |
| `data/.secrets/` | `claude_oauth_token`, `cloud_api_token` |
| `deploy/terraform/terraform.tfstate*`, `terraform.tfvars` | Terraform state / vars |

**Rule:** hard-blocked from git. This is the layer whose leak makes a PR
ungradeable, so it is the one `make doctor` scans for on every run.

---

## The hard rule (enforced, not just stated)

> **No file under Layer 4, and no `data/`/`outputs/` path, is ever tracked by git.**

This is already true and enforced by `.gitignore`:

```
.env
.env.*
!.env.example
data/                     # NEVER commit: data/profile/profile.json holds contact PII
*.csv
*.xlsx
outputs/
**/outputs/
*.tfstate
*.tfstate.*
deploy/terraform/terraform.tfvars
```

`make doctor` (see `scripts/doctor.py`) re-checks this against the *actual* git
index (`git ls-files`), so a future `git add -f` that bypasses `.gitignore` is
caught. A tracked `profile.json`, `.env`, `resume.*`, `*.tfstate`, or `*.csv` is a
hard failure, not a warning.

**Note on `terraform.tfstate`:** it exists on disk (~90 KB) and is gitignored. A
scan of it holds only Secret Manager *references* (secret names like
`gemini-api-key`, `turso-auth-token`) resolved at deploy time, not plaintext
credential values. It stays out of git regardless.

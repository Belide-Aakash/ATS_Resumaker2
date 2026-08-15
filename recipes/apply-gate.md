# recipe: apply-gate: fit + hard blockers -> apply / no-apply

Inherits `recipes/_shared.md`. This is the spine's decision point: it advises apply
or skip. It **never applies**, the human does.

## 1. Executive summary

Given a role, the pipeline scores fit (deterministic floor blended 50/50 with an
LLM judgment clamped to +/-25 of that floor) and then makes a recommendation. The
recommendation is a **hard gate**: any hard blocker forces no-apply regardless of
how high the fit is. The only hard blockers are a JD that explicitly excludes
sponsorship the candidate needs, and a years-of-experience gap of 3 or more.
Absent a blocker, the fit score drives the call against fixed thresholds. Every
per-run number here is computed against the owner's profile, so it is
profile-specific, not a universal benchmark.

## 2. Required reads

- `recipes/_shared.md`, Verified-Data Rules #4 (fit is a judgment) and #5 (gate, not vote).
- `src/resumaker/stages/role_fit.py`, the +/-25 clamp and 0.5/0.5 blend (`final_0_100`).
- `src/resumaker/stages/apply_decision.py`, `decide_apply`, thresholds `_APPLY=60`, `_MARGINAL=45`.
- `data/profile/profile.json`, candidate years / needs-sponsorship (via `persistence.profile`).

## 3. Phase gates

- **G1, Gate, not vote (the graded property).**
  Command: `uv run pytest tests/unit/test_apply_gate_behavior.py -q`
  Passes when a hard blocker forces `recommend_apply=False` even at 95/100 fit, and
  when low sponsorship likelihood does NOT block a strong fit. **Failure path:** if
  a blocked role can still be recommended because fit is high, STOP, the gate has
  become an additive vote (the named capstone failure); do not ship the decision.
- **G2, Fit stays anchored.**
  Check (`role_fit.py`): `final_0_100 = 0.5*det + 0.5*clamp(llm, det-25, det+25)`.
  **Failure path:** if the LLM can move the score more than 25 points off the
  deterministic floor, an unverifiable judgment is overriding evidence, reject it.

## 4. Primary stored tools

- `src/resumaker/stages/apply_decision.py :: decide_apply`, the deterministic combiner.
- `src/resumaker/stages/role_fit.py :: score_fit`, the fit score it thresholds.
- `apps/cli/main.py :: run --gate`, CLI path that skips resume generation on a negative decision.

## 5. Workflow

1. **Score fit** (`score_fit`): deterministic dimensions -> `det`; optional LLM ->
   clamped to `det +/- 25`; `final_0_100 = 0.5*det + 0.5*llm_clamped`.
2. **Resolve sponsorship** (see `recipes/sponsorship.md`) -> `SponsorshipVerdict`.
3. **Decide** (`decide_apply`): build `blockers`, explicit-JD-sponsorship-exclusion
   (if candidate needs it) and a `>=3y` experience gap. **Any blocker ->
   `recommend_apply=False` immediately.**
4. **Otherwise threshold fit:** `>=60 -> apply` (high conf if `>=75`, else medium);
   `>=45 -> apply, low conf` (marginal); `<45 -> no-apply`.
5. **Output** `ApplyDecision`; with `--gate`, a negative decision short-circuits
   resume generation (no wasted work on a role you should not apply to).

## 6. Output contract

- **Script produces:** `ApplyDecision(recommend_apply, confidence, reasons[], blockers[])`.
- **Human reads:** the recommendation, the confidence, the reasons (fit rationale,
  sponsorship note, experience note), and any blockers, then makes the final call.
- Shape rule: a `False` with a non-empty `blockers[]` is a hard block; a `False`
  with empty `blockers[]` is a below-threshold fit. The two are distinguishable.

## 7. Verification checks

- `uv run pytest tests/unit/test_apply_gate_behavior.py -q`, the gate property (G1) + threshold behavior.
- Live: `uv run python -m apps.cli run <url> --gate` on a role with an explicit
  "no sponsorship" JD -> `recommend_apply=False`, resume generation skipped.
- `make verify`, recipe/card well-formed and paired.

## 8. Logging rules

- On a real batch, append to `logs/RUN_LOG.md`: `Recipe: apply-gate`, inputs
  (roles scored, profile used), outputs (apply/skip counts, blockers hit), and a
  `### Verification check` line pointing at the pytest result or the decision rows.
- Record the profile identity used (it is profile-specific; a different profile
  gives different decisions).

## 9. Stop conditions

- **Stop at any hard blocker**, return no-apply regardless of fit; do not "average
  it in."
- **Stop the LLM at +/-25** of the deterministic floor, a judgment never overrides
  the evidence-based score beyond the clamp.
- **Stop before auto-applying, always**, the recipe outputs a recommendation; the
  human decides. Also: do NOT claim liveness/OPT-timeline gating, it is not
  implemented, and the recipe must not imply it is.

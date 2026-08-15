# card: apply-gate

Companion to `recipes/apply-gate.md`. Update in the SAME commit.

## Purpose

Recommend apply or skip for a role: a hard gate on real blockers, a fit threshold
otherwise. Advisory only, the human makes the final call.

## What it CAN verify

- That a hard blocker (explicit-JD-no-sponsorship-when-needed, or a `>=3y`
  experience gap) forces no-apply regardless of fit, locked by the gate test.
- That the fit score stays within `+/-25` of the deterministic floor (the LLM
  cannot run away with it).
- Which reasons and blockers drove a given decision (they are recorded).

## What it CANNOT verify

- Whether the role is **still open / live**, there is no liveness or OPT-timeline
  gate (not implemented). A stale posting can still be scored "apply."
- Whether the **fit score is objectively right**, the LLM half is a judgment, and
  the whole score is computed against ONE profile, so it does not generalize.
- Whether the candidate will actually get an interview (timing, referrals, ghost
  postings all sit outside the tool, see the honest close).

## Dependencies

- `data/profile/profile.json` (candidate years, needs-sponsorship), the fit stage,
  the sponsorship stage. LLM optional for the fit half; the decision itself is
  deterministic.

## Commands (annotated)

```bash
# the gate property: a blocker beats a 95/100 fit; likelihood does not block
uv run pytest tests/unit/test_apply_gate_behavior.py -q

# live decision with the gate wired to skip resume work on a no-apply
uv run python -m apps.cli run <job_url> --gate
```

## What it produces

- An `ApplyDecision(recommend_apply, confidence, reasons[], blockers[])`, surfaced
  in the tracker as the apply/skip recommendation with its reasons.

## Failure modes (≥4, incl. the two mandatory)

1. **drift (mandatory).** This card and `recipes/apply-gate.md` disagree on the
   thresholds (`60`/`45`) or which blockers are hard. A reader mis-reads the gate.
   `make verify` flags the unequal update.
2. **contract-violation (mandatory).** The fit script produces no score (or a
   sponsorship lookup returns nothing), but the decision still comes back a
   confident "apply" because a default or an LLM filled the gap, a recommendation
   with no evidence behind it. Verified-Data Rules #4/#5 forbid it. Guard: the
   deterministic combiner requires a real `FitScore`; the risk is a caller passing
   a fabricated one.
3. **gate-as-vote regression.** A refactor turns the hard gate into
   `net = fit - penalty`, so a 95-fit blocked role slips through. This is the exact
   named capstone failure; the G1 test goes RED on it (demonstrated in the D4 break
   attempt).
4. **stale-posting apply.** Because liveness is not gated, the decision can
   recommend applying to a role that has closed. No guard exists today; flagged as
   a known limitation, not hidden.
5. **profile over-generalization.** Reading one profile's apply-rate as the
   system's accuracy. The numbers are profile-specific; a different profile yields
   different decisions.

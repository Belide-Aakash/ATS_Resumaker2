"""Gate-behavior contract for `decide_apply` (the apply / no-apply stage).

This test locks in the ONE property that separates a real gate from a weighted
vote: a hard blocker must force `recommend_apply=False` *regardless of how high
the fit score is*. If someone ever refactors `decide_apply` into an additive
combiner (e.g. `net = fit - penalty(blockers)` thresholded at 60), a 95/100 fit
would sail past the bar despite a disqualifying blocker -- the exact
"gate-as-a-vote" failure the course names. These tests go RED on that refactor
and GREEN on the current short-circuit implementation.

Just as important, they pin down the HONEST boundary of what this gate does and
does not do, so no downstream doc can over-claim it:

  * The only hard blockers are (1) a JD that EXPLICITLY excludes sponsorship when
    the candidate needs it, and (2) a years-of-experience gap >= 3y.
  * Sponsorship *likelihood* ("unlikely" / low USCIS history) is a SOFT reason,
    never a blocker -- a low-likelihood role with a strong fit is still an apply.
  * Liveness and OPT-timeline are NOT inputs to this decision at all (not
    implemented). No test here pretends otherwise.

All cases pass `candidate_years` and `needs_sponsorship` explicitly so the test
is hermetic and never reads the real PII profile.
"""
from __future__ import annotations

from resumaker.domain import FitScore, JobPosting
from resumaker.stages.apply_decision import decide_apply
from resumaker.stages.sponsorship.resolve import SponsorshipVerdict

# A fit that clears the apply bar (>= 60) with room to spare. If the gate were a
# vote, this alone would carry a blocked role over the line.
STRONG_FIT = FitScore(final_0_100=95.0, final_1_5=5.0, rationale="strong match")


def _neutral_sponsorship() -> SponsorshipVerdict:
    """Positive, non-blocking sponsorship signal (does not affect the gate)."""
    return SponsorshipVerdict(verdict="likely", hard_blocker=False,
                              source="uscis_history")


def _explicit_no_sponsorship() -> SponsorshipVerdict:
    """The one sponsorship case that IS a hard blocker: the JD itself excludes it."""
    return SponsorshipVerdict(verdict="not_eligible", hard_blocker=True,
                              source="jd_explicit",
                              reasons=["JD: 'no visa sponsorship available'"])


# --------------------------------------------------------------------------- #
# The core property: a hard blocker beats a high fit (gate, not vote).
# --------------------------------------------------------------------------- #
def test_explicit_sponsorship_exclusion_blocks_even_a_perfect_fit():
    job = JobPosting(title="ML Engineer")  # no years requirement
    decision = decide_apply(job, STRONG_FIT, _explicit_no_sponsorship(),
                            candidate_years=8.0, needs_sponsorship=True)
    assert decision.recommend_apply is False, (
        "A JD that explicitly excludes sponsorship the candidate needs must "
        "block, even at 95/100 fit -- if this passes, the gate has become a vote."
    )
    assert decision.blockers, "A hard block must record a blocker reason."


def test_experience_gap_blocks_even_a_perfect_fit():
    # JD requires 10+ years; candidate has 2 -> gap 8y (>= 3y) is a hard blocker.
    job = JobPosting(title="Principal Engineer",
                     required_quals=["10+ years of production experience"])
    decision = decide_apply(job, STRONG_FIT, _neutral_sponsorship(),
                            candidate_years=2.0, needs_sponsorship=False)
    assert decision.recommend_apply is False, (
        "A >=3y experience gap must block regardless of a 95/100 fit."
    )
    assert decision.blockers


# --------------------------------------------------------------------------- #
# The honest boundary: likelihood is soft; absent blockers, fit governs.
# --------------------------------------------------------------------------- #
def test_low_sponsorship_likelihood_does_NOT_block_a_strong_fit():
    """'unlikely' sponsorship history is a soft reason, never a gate. This
    documents, as an executable assertion, that sponsorship *likelihood* does not
    behave like a hard gate -- only an explicit JD exclusion does."""
    job = JobPosting(title="Data Scientist")
    soft = SponsorshipVerdict(verdict="unlikely", hard_blocker=False,
                              source="uscis_history",
                              reasons=["little/no recent H-1B history"])
    decision = decide_apply(job, STRONG_FIT, soft,
                            candidate_years=5.0, needs_sponsorship=True)
    assert decision.recommend_apply is True, (
        "Low sponsorship likelihood must NOT block a strong-fit role; it is a "
        "signal, not a gate. If this flips, likelihood has been wrongly hardened."
    )
    assert not decision.blockers


def test_without_blockers_the_fit_threshold_governs():
    job = JobPosting(title="Backend Engineer")
    spon = _neutral_sponsorship()

    strong = decide_apply(job, FitScore(final_0_100=90.0), spon,
                          candidate_years=5.0, needs_sponsorship=False)
    assert strong.recommend_apply is True and strong.confidence == "high"

    marginal = decide_apply(job, FitScore(final_0_100=50.0), spon,
                            candidate_years=5.0, needs_sponsorship=False)
    assert marginal.recommend_apply is True and marginal.confidence == "low"

    weak = decide_apply(job, FitScore(final_0_100=30.0), spon,
                        candidate_years=5.0, needs_sponsorship=False)
    assert weak.recommend_apply is False

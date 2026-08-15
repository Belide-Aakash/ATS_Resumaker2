"""Tier-logic contract for the sponsorship scorer.

Locks Verified-Data Rule #2: the system never invents a sponsorship tier. With no
matched USCIS approvals the tier is `unknown` (not a hopeful `low`), and a real
tier requires the volume/recency/rate thresholds to actually clear. This tests the
pure decision function `_likelihood` directly so it needs no CSV or network.
"""
from __future__ import annotations

from resumaker.stages.sponsorship.scorer import _likelihood


def test_no_uscis_history_is_unknown_not_a_guessed_tier():
    # count_3y == 0 -> we have no record, so we say so.
    assert _likelihood(0, filed_recent=True, rate=0.99) == "unknown"


def test_high_tier_requires_volume_and_recency_and_rate():
    assert _likelihood(500, filed_recent=True, rate=0.90) == "high"
    # same volume/rate but NOT filed recently -> downgraded to medium
    assert _likelihood(500, filed_recent=False, rate=0.90) == "medium"


def test_medium_and_low_bands():
    assert _likelihood(50, filed_recent=True, rate=0.60) == "medium"   # >=10 vol, >=0.5 rate
    assert _likelihood(5, filed_recent=True, rate=0.90) == "low"       # below the medium volume bar

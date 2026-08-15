#!/usr/bin/env python3
"""verify.py - governance conformance for ATS Resumaker.

The same job `make lint`/`make type` do for code, extended to the governance
files: every recipe has the required nine sections and a paired card, every card
has the required parts and >=4 failure modes including the two mandatory ones
(drift, contract-violation), and the core contract docs exist. Exit non-zero on
any hard failure so it can gate a commit / PR. Stdlib only.

Usage:  python scripts/verify.py    (or: make verify)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "recipes"

REQUIRED_SECTIONS = [
    "executive summary", "required reads", "phase gates", "primary stored tools",
    "workflow", "output contract", "verification checks", "logging rules",
    "stop conditions",
]
REQUIRED_CARD_PARTS = ["purpose", "dependencies", "what it produces", "failure modes"]
MANDATORY_FAILURE_MODES = ["drift", "contract-violation"]

errors: list[str] = []
warnings: list[str] = []


def _headings(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            h = line.lstrip("#").strip()
            h = re.sub(r"^\d+[.)]\s*", "", h)          # drop "1." numbering
            out.add(h.lower())
    return out


def _section_body(text: str, title_kw: str) -> str:
    """Return the text under the first heading containing `title_kw`, up to the next heading."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#") and title_kw in line.lower():
            start = i + 1
            break
    if start is None:
        return ""
    body = []
    for line in lines[start:]:
        if line.lstrip().startswith("#"):
            break
        body.append(line)
    return "\n".join(body)


def check_recipe(path: Path) -> None:
    text = path.read_text()
    heads = _headings(text)
    for sec in REQUIRED_SECTIONS:
        if not any(sec == h or sec in h for h in heads):
            errors.append(f"{path.name}: missing required section '{sec}'")
    card = path.with_suffix(".card.md")
    if not card.exists():
        errors.append(f"{path.name}: no paired card ({card.name}) - recipe/card drift")


def check_card(path: Path) -> None:
    text = path.read_text()
    low = text.lower()
    for part in REQUIRED_CARD_PARTS:
        if part not in low:
            errors.append(f"{path.name}: missing card part '{part}'")
    if "can verify" not in low or ("cannot verify" not in low and "can't verify" not in low):
        errors.append(f"{path.name}: must state what it CAN and CANNOT verify")
    body = _section_body(text, "failure mode")
    for m in MANDATORY_FAILURE_MODES:
        if m not in body.lower():
            errors.append(f"{path.name}: missing mandatory failure mode '{m}'")
    n = len(re.findall(r"(?m)^\s*\d+[.)]\s", body))
    if n < 4:
        errors.append(f"{path.name}: only {n} failure modes listed (need >= 4)")
    recipe = path.with_name(path.name.replace(".card.md", ".md"))
    if not recipe.exists():
        errors.append(f"{path.name}: card has no paired recipe ({recipe.name})")


def main() -> int:
    if not (RECIPES / "_shared.md").exists():
        errors.append("recipes/_shared.md missing (the contract header)")
    recipes = sorted(p for p in RECIPES.glob("*.md")
                     if p.name != "_shared.md" and not p.name.endswith(".card.md"))
    cards = sorted(RECIPES.glob("*.card.md"))
    if not recipes:
        errors.append("no recipes found under recipes/")
    for r in recipes:
        check_recipe(r)
    for c in cards:
        check_card(c)

    for doc in ("DATA_CONTRACT.md",):
        if not (ROOT / doc).exists():
            errors.append(f"{doc} missing")
    for doc in ("attestation.md", "logs/RUN_LOG.md"):
        if not (ROOT / doc).exists():
            warnings.append(f"{doc} not present yet (expected before the PR)")

    print(f"verify: {len(recipes)} recipe(s), {len(cards)} card(s) checked")
    for w in warnings:
        print(f"  WARN  {w}")
    if errors:
        for e in errors:
            print(f"  FAIL  {e}")
        print(f"verify: FAILED ({len(errors)} error(s))")
        return 1
    print("verify: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

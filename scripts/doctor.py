#!/usr/bin/env python3
"""doctor.py - read-only health report for ATS Resumaker.

Reports repo health and, most importantly, runs a PRIVACY scan over the actual git
index (`git ls-files`) so a PII/secret file that slipped past `.gitignore` (e.g. a
`git add -f`) is caught. The privacy scan HARD-FAILS (exit 1); everything else is
advisory. Read-only: it changes nothing. Stdlib only.

Usage:  python scripts/doctor.py    (or: make doctor)
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "recipes"

# Files that must NEVER be tracked (see DATA_CONTRACT.md Layer 4 + .gitignore).
PII_PATTERNS = [
    (r"^data/", "anything under data/ (PII profile, USCIS CSVs, DB, secrets)"),
    (r"(^|/)\.env$", ".env"),
    (r"(^|/)\.env\.(?!example)", ".env.<something>"),
    (r"\.tfstate($|\.)", "terraform state"),
    (r"(^|/)terraform\.tfvars$", "terraform vars"),
    (r"(^|/)profile\.json$", "profile.json (contact PII)"),
    (r"(^|/)resume\.(docx|pdf|json)$", "a resume artifact"),
    (r"\.secrets/", "a .secrets/ file"),
    (r"\.(csv|xlsx)$", "a spreadsheet/CSV (gitignored data)"),
]


def _git(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""


def privacy_scan() -> list[str]:
    code, out = _git(["ls-files"])
    if code != 0:
        print("  privacy: SKIP (not a git repo / git unavailable)")
        return []
    tracked = [l for l in out.splitlines() if l.strip()]
    hits = []
    for path in tracked:
        for pat, label in PII_PATTERNS:
            if re.search(pat, path):
                hits.append(f"{path}  ->  {label}")
                break
    print(f"  privacy: scanned {len(tracked)} tracked file(s)")
    return hits


def recipe_status() -> None:
    recipes = sorted(p for p in RECIPES.glob("*.md")
                     if p.name != "_shared.md" and not p.name.endswith(".card.md"))
    for r in recipes:
        card = r.with_suffix(".card.md")
        has_card = "card" if card.exists() else "NO CARD"
        together = ""
        c1, h1 = _git(["log", "-1", "--format=%H", "--", str(r.relative_to(ROOT))])
        c2, h2 = _git(["log", "-1", "--format=%H", "--", str(card.relative_to(ROOT))]) if card.exists() else (0, "")
        if h1.strip() and h2.strip() and h1.strip() != h2.strip():
            together = "  (WARN: recipe & card not last changed in the same commit)"
        print(f"  recipe {r.name:22} [{has_card}]{together}")


def env_report() -> None:
    print(f"  python: {sys.version.split()[0]}")
    print(f"  uv:     {'present' if shutil.which('uv') else 'MISSING'}")
    setup = ROOT / "SETUP.md"
    if setup.exists():
        text = setup.read_text()
        for ref in re.findall(r"scripts/[\w.\-/]+\.sh", text):
            exists = (ROOT / ref).exists()
            if not exists:
                print(f"  SETUP.md refers to {ref} which is MISSING on disk")


def main() -> int:
    print("doctor: ATS Resumaker health report")
    print("- environment")
    env_report()
    print("- recipes")
    recipe_status()
    print("- privacy (hard gate)")
    hits = privacy_scan()
    if hits:
        for h in hits:
            print(f"  FAIL  tracked PII/secret: {h}")
        print(f"doctor: PRIVACY FAILURE ({len(hits)} tracked sensitive file(s))")
        return 1
    print("  privacy: OK (no PII/secret tracked)")
    print("doctor: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

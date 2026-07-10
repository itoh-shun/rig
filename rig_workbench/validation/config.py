"""validation config: path constants (split from scripts/validate.py).

The original script computed ROOT as ``Path(__file__).parent.parent``
(scripts/ → repo root). From this package the equivalent is three levels up
(validation/ → rig_workbench/ → repo root).
"""

import pathlib

# ── path constants ───────────────────────────────────────────────────────────
ROOT     = pathlib.Path(__file__).resolve().parent.parent.parent
SKILLS   = ROOT / "skills" / "rig"
RECIPES  = SKILLS / "recipes"
FACETS   = SKILLS / "facets"
PATTERNS = SKILLS / "patterns"
AGENTS   = ROOT / "agents"

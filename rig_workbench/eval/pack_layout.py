"""Where a pack keeps its evaluation cases — the one thing eval asks of the packs pillar.

`eval promote --into PACK` writes an approved case into a pack instead of into the
repository, so it has to know the directory a pack keeps evaluation cases in. That
directory is `packs`' declaration (`packs/model.ASSET_DIRS`), and reaching for it from
`eval/promote.py` was a cross-pillar import in a judgement module —
`tests/test_layering_contract.py` counts it wherever it sits, and it sat inside the
function with a comment saying a module-level one would close an import cycle. A
function-local import hides a dependency rather than removing it.

So the dependency is inverted, the same way `eval/affected.py` inverted the brick graph:
`promote.py` states what it needs as `PackCaseDir` — a pack in, the directory its cases
live in out — and this module satisfies it. Declared a shell in
`tests/test_layering_contract.py` because an adapter exists to hold what the protocol may
not.

The check that `into` really is a pack comes with it, because it is the same question:
without it a mistyped path writes an approved case into an ordinary directory where
nothing will ever read it, and the author's next `pack validate` reports the case as
missing rather than misplaced. What is deliberately *not* checked here is whether the pack
owns the prompt surfaces the case names — `validate_pack` already refuses a case not bound
to the pack's own prompt assets, and a second copy of that rule one import away from the
first would be free to drift.
"""

from __future__ import annotations

import pathlib

from ..packs.model import ASSET_DIRS
from .cases import EvalCaseError


def pack_case_dir(into: pathlib.Path | str) -> pathlib.Path:
    """`<pack>/evals/cases`, after checking that `into` is in fact a pack."""
    try:
        pack = pathlib.Path(into).resolve()
        is_pack = (pack / "pack.yaml").is_file()
    except OSError as exc:
        raise EvalCaseError(f"filesystem error resolving pack: {exc}") from exc
    if not is_pack:
        raise EvalCaseError(f"not a pack directory (no pack.yaml): {pack}")
    return pack / ASSET_DIRS["eval-case"]


#: The one layout there is, named so a caller passes a value rather than a function whose
#: module it has to know — as `ports.local`'s adapter instances are named.
PACK_CASE_DIR = pack_case_dir

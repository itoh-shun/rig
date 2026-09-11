"""What an evaluation case *is* — the narrow half of this pillar's borrowing from `eval`.

`eval_bridge.py` holds everything `packs` needs of evaluation: the case schema, the
execution identity, the result gate, the runner. This module holds only the first of those,
and it exists as a separate file for a reason a measurement supplied rather than taste.

`validation.py` asks one question about evaluation — is the document in the pack's
`evals/cases/` a well-formed case — and it was getting that answer through the full bridge.
That gave `packs.validation` a module-level path to `eval.gate`, which reaches
`eval.affected`, `eval.source_graph`, `orchestrate.graph` and `orchestrate.recipes`, which
reach back into `packs.resolver`. The result was a new ten-module import cycle, caught by
`tests/test_architecture_inventory.py`'s frozen cycle set — a regression, because stage 3
exists to remove cycles rather than to trade one shape of coupling for another. Splitting
the narrow question into its own module cuts it: `eval.cases` reaches nothing that comes
back here, which is why `validation.py` could import it directly before and why it can
depend on this now.

`eval_bridge.py` takes its case names from here rather than importing `eval.cases` a second
time, so the pillar still borrows the case schema in exactly one place. Declared a shell in
`tests/test_layering_contract.py`, for the reason `PORT_ADAPTERS` gives for `ports/local.py`:
an adapter exists precisely to hold what the protocol may not. It imports no judgement
module, so the inverted edge stays one-way.
"""

from __future__ import annotations

from typing import Any

from ..eval.cases import EvalCaseError, canonical_json, validate_case

#: The error the case machinery raises, re-published so a caller can catch it without
#: naming another pillar's class.
CaseError: type[Exception] = EvalCaseError


class _CaseSchema:
    """The case schema, under the names this pillar's protocols ask for."""

    CaseError: type[Exception] = EvalCaseError

    @staticmethod
    def validate_case(case: Any) -> dict:
        return validate_case(case)

    @staticmethod
    def canonical_json(value: Any) -> str:
        return canonical_json(value)


#: The shipped implementation, named as a value so a caller passes one rather than a module
#: whose address it would have to know — as `ports.local`'s instances are.
CASE_SCHEMA = _CaseSchema()

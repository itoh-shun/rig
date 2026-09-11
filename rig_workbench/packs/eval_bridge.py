"""The evaluation machinery this pillar borrows, in the one module allowed to name it.

A pack carries its own evidence: approved evaluation cases, and results measured against
them. `packs` therefore has to ask three questions it does not own the answers to — is this
a well-formed case, what exactly was the repository when this result was produced, and does
this result clear release policy — and all three are `eval`'s, because `eval` is what
produces the evidence in the first place. Re-deciding any of them here would put two
definitions of "an approved measurement" in one repository, and the pack installer would be
enforcing the copy nobody runs.

`tests/test_layering_contract.py` lets a judgement module import the standard library, its
own pillar and the six ports; `eval` is none of those. Before this module existed the edge
sat in `evidence.py`, `installer.py` and `tester.py` — and `evidence.py` reached for
`eval.runner._git_identity`, a *private* name, which is a dependency on another pillar's
internals rather than on anything it published.

So the edge is inverted, not moved. Each caller states the narrow thing it needs as a
protocol of its own and takes it as a keyword argument; this module satisfies all of them
with one object, because protocols are structural and the callers' needs overlap
(`validate_case` and the result gate are wanted by more than one). It is declared a shell
in `tests/test_layering_contract.py` for the reason `PORT_ADAPTERS` gives for
`ports/local.py`: an adapter exists precisely to hold what the protocol may not.

What this module adds beyond holding the imports is one thing, and it is the reason the
private reach-in does not survive: `git_identity` is a public name here for what `eval`
spells `_git_identity`. The bridge takes on the coupling so that no judgement module in
`packs` states a signature in terms of another pillar's underscore.

`scanners.py` is the other half of the same split and holds the content sensors — a
different collaborator with a different shape, grouped by what it is rather than by which
pillar it is currently filed under. The arrow stays one-way from both: neither adapter
imports a `packs` judgement module.
"""

from __future__ import annotations

import pathlib
from typing import Any

from ..eval.cases import EvalCaseError, canonical_json, validate_case
from ..eval.execution import execution_diff_sha256
from ..eval.gate import quality_result_failures
from ..eval.runner import _git_identity


class _EvalPillar:
    """One object satisfying every protocol `packs` declares about evaluation evidence.

    One rather than one per caller: `evidence.py`, `installer.py` and `tester.py` want
    overlapping subsets, and a protocol is structural, so three narrow declarations can be
    met by the same value without any of them learning about the others' needs.
    """

    #: The exception the case and result machinery raises, named so a caller can write
    #: `except evaluation.CaseError` without importing the class it is.
    CaseError: type[Exception] = EvalCaseError

    @staticmethod
    def validate_case(case: Any) -> dict:
        return validate_case(case)

    @staticmethod
    def canonical_json(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def git_identity(repo: pathlib.Path) -> tuple[str | None, str | None, str]:
        """Commit, base commit and availability for the tree a measurement ran against.

        Public here, private there. `eval.runner._git_identity` is the only implementation
        and reaching for it from a judgement module was a dependency on another pillar's
        internals; holding it in the adapter is what stops that reach from propagating
        into the signatures `packs` writes.
        """
        return _git_identity(repo)

    @staticmethod
    def execution_diff(repo: pathlib.Path, *, base: str,
                       ignored_untracked_prefixes: tuple[str, ...] = ()) -> str:
        return execution_diff_sha256(
            repo, base=base, ignored_untracked_prefixes=ignored_untracked_prefixes,
        )

    @staticmethod
    def result_failures(result: dict, case: dict, *, expected_commit: str | None = None,
                        expected_base: str | None = None, expected_diff: str | None = None,
                        verify_attestation: bool = True) -> list[str]:
        """Why a result does not clear release policy, as a list of labels.

        The keyword surface is `eval.gate.quality_result_failures`' own, narrowed to what
        this pillar passes, so that a caller here reads the same as the caller there.
        """
        return quality_result_failures(
            result, case, expected_commit=expected_commit, expected_base=expected_base,
            expected_diff=expected_diff, verify_attestation=verify_attestation,
        )


#: The shipped implementation, named as a value so a caller passes one rather than a module
#: it would otherwise have to know the address of — as `ports.local`'s instances are.
EVALUATION = _EvalPillar()

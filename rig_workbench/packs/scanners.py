"""The content sensors this pillar borrows, in the one module allowed to know where they live.

A pack is a bundle of text somebody else wrote, and every rule `packs` applies to that text
— no injection marker, no destructive command, no credential, no path that escapes the
tree, no `checks:` step the orchestrator would refuse — is a question already answered
somewhere else in this repository. Answering them a second time here would be worse than
an import: two copies of a sensor drift, and the copy nobody runs is the one that goes
stale. So the sensors are reused, and the point of this module is *where the reuse sits*.

`tests/test_layering_contract.py` lets a judgement module import the standard library, its
own pillar and the six ports. `workbench.injection`, `workbench.destructive`,
`workbench.secrets`, `eval.safety` and `orchestrate.gates` are none of those, and before
this module existed the edges to them sat in `manifest.py`, `validation.py` and `lock.py`
— the three files that decide whether a pack may be installed. Two of them at module
level, one hidden inside a function, which looks like a fix and is not.

They are not moved here; they are inverted. `manifest.py` states what it needs of a line
sensor (`LineScanner`) and of the text policy (`TextSafety`), `validation.py` states what
it needs of a file sensor (`FileScanner`) and of the recipe gate (`RecipeGate`), and this
module satisfies those shapes without any of them naming another pillar. It is declared a
shell in `tests/test_layering_contract.py` for the reason `PORT_ADAPTERS` gives for
`ports/local.py`: an adapter exists precisely to hold what the protocol may not.

Grouped by *what the collaborator is* rather than by which pillar it came from. These five
are one thing — checks on content the pack did not write and rig did not generate — and
splitting them by their current address would produce three adapter modules that each hold
one import and say nothing. `eval_bridge.py` is the other half of the same split: it holds
the evaluation *machinery* (cases, results, the runner), which is a different collaborator
with a different shape, not a different address.

Nothing in the judgement layer imports this module for its behaviour; it imports it for
the default binding, exactly as `eval/affected.py` imports `SOURCE_TREE_GRAPH` from its own
pillar's adapter. The arrow stays one-way: this module imports no `packs` judgement module.
"""

from __future__ import annotations

import pathlib

from ..eval.safety import unsafe_key_reason, unsafe_text_reason
from ..orchestrate.gates import validate_executable_recipe
from ..workbench.destructive import scan_file as _destructive_scan_file
from ..workbench.destructive import scan_line as _destructive_scan_line
from ..workbench.injection import scan_file as _injection_scan_file
from ..workbench.injection import scan_line as _injection_scan_line
from ..workbench.secrets import scan_line as _secret_scan_line


class _SharedTextSafety:
    """`eval.safety`'s two questions, under the names `manifest.TextSafety` asks for.

    The same policy evaluation cases are held to, deliberately: a string refused in a case
    and accepted in the manifest that ships the case would be one rule with two answers.
    """

    @staticmethod
    def key_reason(key: object) -> str | None:
        return unsafe_key_reason(key)

    @staticmethod
    def text_reason(value: str) -> str | None:
        return unsafe_text_reason(value)


def _prose_scan_line(line: str, rel: str, lineno: int) -> list[dict]:
    """Injection and destructive findings on one line, in one list.

    Concatenated rather than reported separately because the one caller
    (`manifest._reject_unsafe`) reads only whether the list is empty: a manifest string is
    refused as an "unsafe manifest instruction" whichever of the two sensors objected.
    `validation.py` does distinguish them, and takes the two scanners separately.
    """
    return _injection_scan_line(line, rel, lineno) + _destructive_scan_line(line, rel, lineno)


def _credential_scan_line(line: str, rel: str, lineno: int) -> list[dict]:
    """The secret sensor, with entropy scoring off.

    `skip_entropy=True` is the setting `lock.refuse_credentials` has always used and it is
    bound here rather than left to the caller: a lock file is machine-written canonical
    JSON full of hex digests, and entropy scoring on that is a false-positive generator,
    not a check.
    """
    return _secret_scan_line(line, rel, lineno, skip_entropy=True)


def _injection_file(path: pathlib.Path, rel: str | None = None) -> list[dict]:
    return _injection_scan_file(path, rel)


def _destructive_file(path: pathlib.Path, rel: str | None = None) -> list[dict]:
    return _destructive_scan_file(path, rel)


def _recipe_gate(recipe: object) -> dict:
    return validate_executable_recipe(recipe)


#: The shipped implementations, named as values so a caller passes one rather than a
#: function whose module it would have to know — as `ports.local`'s adapter instances are.
MANIFEST_TEXT_SAFETY = _SharedTextSafety()
PROSE_LINE_SCANNER = _prose_scan_line
CREDENTIAL_LINE_SCANNER = _credential_scan_line
INJECTION_FILE_SCANNER = _injection_file
DESTRUCTIVE_FILE_SCANNER = _destructive_file
RECIPE_GATE = _recipe_gate

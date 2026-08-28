"""A declared dependency that is not importable is a failure, never a skip.

This exists because of a real hour lost to the opposite. `cryptography` was installed and
imported fine at the top level, but its `_cffi_backend` was missing, so the eleven publisher
tests guarded by `importorskip("cryptography")` turned into eleven skips. The suite reported
green. Nothing in the log said the signing path had gone unexercised, because a skip is the
one result nobody scans for — a failure count of zero reads as success even when the passing
count quietly dropped by eleven.

Those guards are gone now, so each of those tests fails on its own. This file is the rule
behind that edit, stated once where it cannot be forgotten: the guards were removed by hand
in two files, and the next dependency added to `pyproject.toml` would not be covered by
either. Here it is covered the moment it is declared.

The distinction the suite has to keep is between a dependency and an extra. `mcp` is an
extra: absent by design in a plain install, and `importorskip` is exactly right for it. What
is checked here is only what `[project] dependencies` promises is always present.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re

import pytest

try:                                        # 3.11+ ships it; the dev extra backfills below
    import tomllib
except ModuleNotFoundError:                 # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Distribution name → the module it actually installs, where the two differ. Kept as a table
#: rather than guessed with a normalisation rule, because guessing is how a typo becomes a
#: silently unchecked dependency: an unknown name here fails loudly instead.
IMPORT_NAMES = {"pyyaml": "yaml"}


def _importorskip_calls(source: str) -> set[str]:
    """The literal first argument of every `importorskip(...)` *call* in `source`.

    Parsed rather than matched. A regex over the text counts the name written inside a
    comment or a docstring, which is how a check ends up reporting its own explanation as a
    violation — and, worse, how it would report a genuine guard as absent if somebody's prose
    happened to sit where the pattern anchored.
    """
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.attr if isinstance(function, ast.Attribute) else getattr(
            function, "id", None)
        if name != "importorskip" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            targets.add(first.value)
    return targets


def _importorskip_targets(directory: pathlib.Path) -> set[str]:
    return {name
            for path in sorted(directory.glob("test_*.py"))
            for name in _importorskip_calls(path.read_text(encoding="utf-8"))}


def _declared() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = []
    for requirement in data["project"]["dependencies"]:
        # Strip the version specifier and any environment marker; what is left is the name.
        name = re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0].strip()
        names.append(name.lower().replace("_", "-"))
    return names


def test_the_dependency_list_is_readable_and_not_empty():
    """A positive control. If the parse above ever returned `[]` — a renamed table, a moved
    file — every check below would pass by having nothing to check, which is the failure mode
    this whole file exists to refuse."""
    assert len(_declared()) >= 3


@pytest.mark.parametrize("distribution", _declared())
def test_every_declared_dependency_imports(distribution):
    """Not `importorskip`. A declared dependency that cannot be imported means the install is
    broken, and a broken install has to be loud: skipping here would reproduce, one level up,
    the exact silence that made the original failure cost an hour."""
    module = IMPORT_NAMES.get(distribution, distribution.replace("-", "_"))
    importlib.import_module(module)


def test_cryptography_can_actually_do_the_work_and_not_merely_import():
    """The failure that motivated this file passed a plain `import cryptography`. What was
    missing was `_cffi_backend`, which only surfaces when something reaches the backend — so
    an import check alone would have reported the broken install as healthy. Ed25519 is what
    the publisher signs with, so this is the smallest operation that proves the dependency is
    usable rather than merely present."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    signature = private.sign(b"rig")
    private.public_key().verify(signature, b"rig")


def test_no_declared_dependency_is_guarded_by_importorskip_anywhere_in_the_suite():
    """The rule, enforced where it can be broken. Removing the eleven guards fixed the
    instance; a new one added tomorrow would restore the silence, and nothing else in the
    suite would notice — the tests behind it would simply stop running."""
    declared = {IMPORT_NAMES.get(name, name.replace("-", "_")) for name in _declared()}
    declared.discard("pytest")              # importorskip is pytest's own, always present
    assert _importorskip_targets(ROOT / "tests") & declared == set(), (
        "a declared dependency is guarded by importorskip; import it directly so a broken "
        "install fails instead of skipping")


def test_the_scan_reads_code_and_not_prose():
    """A positive control, and not a theoretical one: the first version of the scan was a
    regex, and it failed on the sentence in this file's own docstring that names the guard it
    forbids. A check that cannot tell an example from an occurrence reports the thing it
    exists to catch — so the scan parses, and this proves the difference both ways."""
    source = (
        'import pytest\n'
        '# pytest.importorskip("cryptography") in a comment is not a guard\n'
        'TEXT = "importorskip(\'cryptography\')"\n'
        'def test_real():\n'
        '    pytest.importorskip("mcp")\n'
    )
    assert _importorskip_calls(source) == {"mcp"}

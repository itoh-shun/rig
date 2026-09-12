"""The package's own top-level modules, in the one module allowed to know they are up there.

Four of this pillar's edges point at neither a pillar nor a port but at
`rig_workbench/`'s own top-level modules: `repo_paths`, which answers "where is
`scripts/<name>.py`?", `caller`, which answers "what invoked this process?", and
`bench_providers`, which holds the patch-application machinery a tool-free local generator
is given writable parity through. `tests/test_layering_contract.py` refuses these for the
same reason it refuses a cross-pillar import — a judgement module may reach the standard
library, its own pillar and the six ports, and `rig_workbench.repo_paths` is none of the
three — and the reason is not a technicality: each of them is a place this pillar knows
about the repository's own layout, and a runner that knows the layout is a runner that
breaks when the layout moves.

They are not moved here; they are inverted. `commands.py` and `mcp_scan.py` state what they
need as `ScriptLocator`, and `providers.py` states what it needs as `CallerIdentity` and
`PatchApplier`, and this module satisfies those shapes without any of them naming a module
outside the pillar. It is declared a shell in `tests/test_layering_contract.py` for the
reason `PORT_ADAPTERS` gives for `ports/local.py`: an adapter exists precisely to hold what
the protocol may not.

**A fourth adapter rather than a fourth entry in `pack_surfaces.py`.** Grouped by what the
collaborator is, which is the rule `packs/scanners.py` states: these three are one thing —
the package's own shared utilities, owned by no pillar — and filing them under the pack
resolver or the governance layer would put an answer about `scripts/dashboard.py` in a
module about trust stores. The cycle measurement permits either (the `packs`, `govern` and
root groups were measured safe together), so this is a choice about what the module says,
made where the graph left it free.

**`bench_providers` is reached through the module, not through bound names.**
`tests/test_bench_providers.py` substitutes `bench_providers._run_git_apply` by assigning to
the module attribute, so the lookup has to happen at call time; a `from ... import` here
would freeze the shipped function and make that substitution silently stop biting. The
alias the old import carried — `_bench_provider_patches` — said the same thing and is the
reason this is worth a paragraph rather than a diff.

**`caller` stays inside its method.** `rig_workbench.caller` pulls in `workbench.injection`
for the shared list of characters that make printed text lie, and the orchestrator should
not need the workbench package to start — the rule `providers._caller_record` already
carried, kept where the import now lives.

The arrow stays one-way: this module imports no `orchestrate` judgement module.
"""

from __future__ import annotations

import pathlib
from typing import Any

from .. import bench_providers as _bench_providers
from .. import repo_paths as _repo_paths


class _ScriptLocator:
    """`repo_paths`, under the names `commands.ScriptLocator` asks for."""

    @staticmethod
    def find(name: str) -> pathlib.Path | None:
        """The repository's `scripts/<name>`, or None when no checkout holds one."""
        return _repo_paths.find_script(name)

    @staticmethod
    def expected(name: str) -> pathlib.Path:
        """Where `scripts/<name>` would be — the path an error message names."""
        return _repo_paths.script_path(name)


class _PatchApplier:
    """`bench_providers`' patch machinery, under the names `providers.PatchApplier` asks for.

    Every call is an attribute lookup on the module rather than a name bound at import, for
    the reason the module docstring gives.
    """

    @staticmethod
    def patch_prompt(prompt: str, workspace: pathlib.Path) -> str:
        return _bench_providers._patch_prompt(prompt, workspace)

    @staticmethod
    def validate(workspace: pathlib.Path, patch: str) -> None:
        _bench_providers._validate_unified_diff(workspace, patch)

    @staticmethod
    def apply(workspace: pathlib.Path, patch: str, *, check_only: bool) -> Any:
        return _bench_providers._run_git_apply(workspace, patch, check_only=check_only)


def _caller_record() -> dict:
    """What invoked this process, as the record telemetry writes."""
    from .. import caller

    return caller.detect().as_record()


#: The shipped implementations, named as values so a caller passes one rather than a module
#: whose address it would have to know — as `ports.local`'s instances are.
SCRIPT_LOCATOR = _ScriptLocator()
PATCH_APPLIER = _PatchApplier()
CALLER_IDENTITY = _caller_record

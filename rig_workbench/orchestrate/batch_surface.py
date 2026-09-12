"""The workbench's batch bookkeeping, in the one module allowed to know where it lives.

Three of this pillar's edges point at the workbench's own record of what a batch did:
`workbench.batch` turns a provider's reply into a task id and a finished GO into the
"what you must do next" block, `workbench.state` answers where the repository is, and
`workbench.run_index` lists the projects that have ever recorded a run. All three sat
inside function bodies in `queueing.py` and `commands.py` — deferred imports, which look
like a fix and are not: a hidden edge is still an edge, and
`tests/test_layering_contract.py` counts it wherever the statement sits.

They are not moved here; they are inverted. `queueing.py` states what it needs of the
batch bookkeeping as `BatchSummary` and `commands.py` states what it needs of the run log
as `ProjectIndex`, and this module satisfies those shapes without either of them naming
another pillar.

**Why this is not in `pack_surfaces.py` or `govern_surfaces.py`.** Not taste — a
measurement. `workbench.batch` reaches `workbench.progress`, and `workbench.progress`
reaches `orchestrate.recipes`; `workbench.run_index` reaches `orchestrate.config`. Put
those three imports in the same module as the `packs` and `govern` collaborators and that
module is imported by `orchestrate.recipes` and `orchestrate.config` alike, closing a new
six-module runtime cycle — `orchestrate.config`, `orchestrate.recipes`, the adapter,
`workbench.batch`, `workbench.progress`, `workbench.run_index` — that
`tests/test_architecture_inventory.py` freezes the absence of. Keeping the workbench half
in its own module means `orchestrate.recipes` never imports it, and the return path never
closes. That is the same constraint `packs` met when `case_schema.py` had to be split out
of `eval_bridge.py`, arrived at the same way: by running the graph, not by reading it.

**The imports stay inside the functions, and here that is not hiding anything.**
`workbench.batch` and `workbench.state` are the two heaviest things a `queue go` can pull
in, and both call sites are wrapped in `except Exception` because a rendering problem in a
summary must not turn a completed batch into a traceback — an import failure included.
That is the reason `rig_surfaces._ParserSource.build` gives for the one import it keeps
method-local: deferred because the caller genuinely may not need it or may survive without
it, in a module whose whole purpose is to hold the edge. The rule is not being dodged; the
module holding the statement is declared.

The arrow stays one-way: this module imports no `orchestrate` judgement module.
"""

from __future__ import annotations


def _task_id(text: str) -> str:
    """The workbench task id a provider's reply registered, or empty.

    Best-effort by contract, and never a reason for a batch to fail: `workbench.batch`
    reports an unrecoverable id rather than guessing one, and the caller records the empty
    string as the honest answer.
    """
    try:
        from ..workbench.batch import find_task_id

        return find_task_id(text)
    except Exception:  # noqa: BLE001 - a missing link is not a failed batch
        return ""


def _summary_lines(results: list[dict]) -> list[str]:
    """The regrouped "what you must do next" block for a finished batch, or nothing.

    Empty when there is no repository to group against, which is the same answer
    `workbench.batch` gives for a batch it cannot place.
    """
    try:
        from ..workbench.batch import group_batch, render_batch
        from ..workbench.state import maybe_repo_root

        root = maybe_repo_root()
        if root is None:
            return []
        return render_batch(group_batch(root, results))
    except Exception:  # noqa: BLE001 - the batch already ran; rendering may not undo that
        return []


class _WorkbenchBatch:
    """`workbench.batch` and `workbench.state`, under the names `queueing.BatchSummary` asks for."""

    @staticmethod
    def task_id(text: str) -> str:
        return _task_id(text)

    @staticmethod
    def lines(results: list[dict]) -> list[str]:
        return _summary_lines(results)


def _known_projects() -> list[str]:
    """Every repository that has recorded a run, newest first.

    Not wrapped: `fleet --discovered` has nothing to report without this, and a caller that
    cannot read the run log should say so rather than report an empty fleet as a finding.
    """
    from ..workbench.run_index import known_projects

    return known_projects()


#: The shipped implementations, named as values so a caller passes one rather than a
#: function whose module it would have to know — as `ports.local`'s adapter instances are.
WORKBENCH_BATCH = _WorkbenchBatch()
KNOWN_PROJECTS = _known_projects

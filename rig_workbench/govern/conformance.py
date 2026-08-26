"""govern.conformance — does this project actually clear the common policy?

Governance that is only ever asserted is prose. This module measures: it takes
the effective policy, looks at what the repository really contains and what its
recent runs really did, and returns a list of checks with verdicts. Every check
answers a question an auditor would ask out loud, and every failure names the
file or the run that caused it.

`rollup` then does the part the picture in the request is actually about:

    team A ─┐
    team B ─┼─→ common policy ─→ one table, one compliance number per team
    team C ─┘

several project reports, aggregated by team, against the same policy. Without
that, "we have a common policy" is a claim; with it, it is a measurement.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import pathlib

from ..workbench.reporting import TaskRecords, read_all_tasks
from . import ledger, waiver
from .approval import evaluate, load_approvals
from .identity import load_org_binding
from .policy import PERMISSIONS, EffectivePolicy, PolicyError, effective_policy
from .rbac import holders_of, permissions_of

# Verdicts, ordered worst-first for reporting.
FAIL, WARN, PASS, NA = "fail", "warn", "pass", "n/a"
_RANK = {FAIL: 0, WARN: 1, PASS: 2, NA: 3}
ICON = {FAIL: "✗", WARN: "⚠", PASS: "✓", NA: "-"}


@dataclasses.dataclass
class Check:
    id: str
    verdict: str
    detail: str
    evidence: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Report:
    root: pathlib.Path
    project: str
    org: str | None
    team: str | None
    policy_layers: list[str]
    checks: list[Check]
    error: str | None = None
    #: Every record under the runs directory and what could not be read there — not the
    #: window-filtered subset, so the shortfall stays a statement about the directory. None
    #: for a report that stopped before reading any runs: that and "nothing unreadable" are
    #: different answers, and only the second is a claim about what is there.
    tasks: TaskRecords | None = None
    #: How many of those records fell inside `--since-days`, which is what the run-derived
    #: checks counted. Kept beside the total rather than replacing it.
    runs_in_window: int | None = None

    @property
    def unreadable_note(self) -> str:
        """The clause every rendered rate carries, or empty when everything was read.

        `TaskRecords.note()` and not a second wording: the board, the digest and this report
        say the same shortfall the same way because they call the same method.
        """
        return self.tasks.note() if self.tasks else ""

    @property
    def applicable(self) -> list[Check]:
        return [c for c in self.checks if c.verdict != NA]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.applicable if c.verdict == PASS)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == FAIL]

    @property
    def score(self) -> float:
        """Share of applicable checks that pass, 0.0–1.0.

        Zero when the policy itself does not load or the repository is unbound.
        Those reports stop after one or two checks, and scoring the fraction that
        ran would say "100%" about a project whose policy is broken — the single
        most misleading number this report could produce.

        The rule that follows from it, stated once here because three checks obey it: **a
        check with zero evidence is `NA`, and being unable to read the evidence is itself a
        failed check.** A check that had nothing to read is not a pass, so it leaves this
        denominator rather than adding to the numerator; and the reason it had nothing —
        `.rig/runs` could not be listed — is an observed fact about the repository, so
        `runs_listing` states it as a FAIL the way `_check_ledger` states an unverifiable
        ledger. Without both halves, a `chmod 000 .rig/runs` scored 7/7 = 100%, identical to a
        project with no runs at all, and that vacuous 100% was averaged into the org rate.

        Partial loss is the other case and keeps a real verdict: a check that read some
        records is stating something about the records it read, and names beside it how many
        it could not (`TaskRecords.note()`). Zero evidence is not a small amount of it."""
        if self.error:
            return 0.0
        total = len(self.applicable)
        return (self.passed / total) if total else 0.0

    @property
    def findings(self) -> list[str]:
        """Short ids for what is wrong, for the per-team column. A load error has
        no Check to point at, so it gets one of its own."""
        return (["policy_error"] if self.error else []) + [c.id for c in self.failed]

    @property
    def verdict(self) -> str:
        if self.error or self.failed:
            return FAIL
        if any(c.verdict == WARN for c in self.checks):
            return WARN
        return PASS if self.applicable else NA

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "root": str(self.root),
            "org": self.org,
            "team": self.team,
            "policy_layers": self.policy_layers,
            "verdict": self.verdict,
            "score": round(self.score, 4),
            "passed": self.passed,
            "applicable": len(self.applicable),
            "error": self.error,
            # The score above is computed from checks whose run counts came from the records
            # that could be read, and a machine consumer cannot see the note printed beside
            # it. `null` means this report stopped before reading any runs — not that every
            # record was readable.
            "task_records": None if self.tasks is None else {
                # `read` and `unreadable` describe the whole runs directory, so a consumer
                # can add them for the number of records that were attempted. `in_window` is
                # the subset the checks above actually counted.
                "read": len(self.tasks.tasks),
                "in_window": self.runs_in_window,
                "unreadable": list(self.tasks.unreadable),
                "collection_error": self.tasks.collection_error,
            },
            "checks": [dataclasses.asdict(c) for c in self.checks],
        }


def _load_tasks(root: pathlib.Path, since_days: int) -> tuple[TaskRecords, tuple[dict, ...]]:
    """Everything under the runs directory, and the subset of it inside the window.

    Two values, not a narrowed `TaskRecords`. This function used to walk
    `.rig/runs/*/task.json` itself and `continue` past anything it could not parse, so a
    report built from 52 of 55 records was presented as the conformance rate — the least
    visible way this report can be wrong, because the number it prints is the number an org
    acts on. `read_all_tasks` (#488) is the single rule for what a usable record is, and it
    carries what it could not read, so no check below can take the runs without the shortfall.

    The window is applied to a separate tuple and never folded back into the records, because
    `TaskRecords.note()` renders "N of <tasks + unreadable>" — replacing its tasks with the
    in-window ones would make that total mean "in-window readable plus unreadable" and print
    a smaller attempted count than the directory holds. That is the same class of quietly
    shrunken denominator this change exists to remove. `cmd_stats` filters a local list for
    the same reason.

    Records that could not be read are in neither set's exclusion: a record whose
    `updated_at` was never read cannot be shown to fall outside `--since-days`, so the window
    is not allowed to be the reason it disappears.
    """
    records = read_all_tasks(root / ".rig" / "runs")
    cutoff = (datetime.datetime.now().astimezone()
              - datetime.timedelta(days=since_days)).isoformat(timespec="seconds")
    in_window = tuple(task for task in records.tasks
                      if (task.get("updated_at") or task.get("created_at") or "") >= cutoff)
    return records, in_window


#: Returned by `_acceptance` for a gate record that is there and cannot be read. Absent and
#: unreadable both used to come back as None, and the caller skipped both — so an accepted run
#: whose `acceptance.json` was corrupt was scanned for nothing and then counted among the runs
#: reported clean, with no note anywhere, because its `task.json` had read fine.
UNREADABLE_ACCEPTANCE = object()


def _acceptance(root: pathlib.Path, task_id: str) -> dict | None | object:
    """The run's gate record, None when it has none, `UNREADABLE_ACCEPTANCE` when it cannot
    be read. Three answers because the caller owes a different sentence to each.

    `OSError` as well as bad JSON: `_task_record` treats every way a file fails to yield a
    usable record the same way, and a permission bit is not a smaller obstacle than a
    truncated write.
    """
    p = root / ".rig" / "runs" / task_id / "acceptance.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNREADABLE_ACCEPTANCE


def evaluate_project(root: pathlib.Path, *, since_days: int = 90) -> Report:
    """Run every conformance check against one repository."""
    binding = load_org_binding(root)
    checks: list[Check] = []
    project = root.name

    if binding.error:
        return Report(root, project, None, None, [], [], error=binding.error)
    if not binding.bound:
        return Report(root, project, None, None, [],
                      [Check("org_binding", FAIL,
                             "no .rig/org.json — this repository is not bound to an org or team",
                             ["run `rig-wb govern init --org <org> --team <team>`"])])
    checks.append(Check("org_binding", PASS, f"bound to {binding.label()}"))

    try:
        eff = effective_policy(root, binding.raw)
    except PolicyError as e:
        return Report(root, project, binding.org, binding.team, [], checks, error=str(e))

    layer_labels = [layer.label() for layer in eff.layers]
    if not eff.active:
        checks.append(Check("policy_layers", FAIL,
                            "no policy layer resolved — the org policy is not reaching this repository",
                            ["set policy_layers in .rig/org.json, or drop a document in .rig/policy/"]))
        return Report(root, project, binding.org, binding.team, layer_labels, checks)

    scopes = {layer.scope for layer in eff.layers}
    if "org" in scopes:
        checks.append(Check("policy_layers", PASS,
                            f"{len(eff.layers)} layer(s): {', '.join(layer_labels)}"))
    else:
        checks.append(Check("policy_layers", FAIL,
                            "no org-scope layer — this project's policy is local, so there is no "
                            "common bar to compare it against", layer_labels))

    # Read once and hand the same records to every check that measures runs, so the three
    # of them cannot disagree about how many records there were or which could not be read.
    records, in_window = _load_tasks(root, since_days)

    checks.append(_check_roles(eff))
    checks.append(_check_permission_holders(eff))
    # Before the three checks it decides the applicability of, so it is also the FAIL the
    # rollup's "worst finding" column reaches first for a project whose runs never opened.
    checks.append(_check_runs_listing(records))
    checks.append(_check_criteria_wired(root, eff, records, in_window))
    checks.append(_check_approvals(root, eff, records, in_window))
    checks.append(_check_waivers(root, eff))
    checks.append(_check_force_rate(records, in_window, since_days))
    checks.append(_check_ledger(root, eff))
    checks.append(_check_legacy_access(root, eff))

    return Report(root, project, binding.org, eff.team or binding.team, layer_labels, checks,
                  tasks=records, runs_in_window=len(in_window))


def _check_roles(eff: EffectivePolicy) -> Check:
    if not eff.roles:
        return Check("rbac_roles", WARN,
                     "the policy defines no roles, so every actor may do everything",
                     ["add a `roles` map to the org layer"])
    unassigned = [r for r in eff.roles if not any(r in got for got in eff.members.values())]
    if not eff.members:
        return Check("rbac_roles", FAIL,
                     f"{len(eff.roles)} role(s) defined but no members assigned — nobody holds any permission",
                     sorted(eff.roles))
    if unassigned:
        return Check("rbac_roles", WARN,
                     f"{len(eff.members)} member(s); role(s) with nobody in them: {', '.join(sorted(unassigned))}")
    return Check("rbac_roles", PASS,
                 f"{len(eff.roles)} role(s), {len(eff.members)} member entr(ies), all roles assigned")


def _check_permission_holders(eff: EffectivePolicy) -> Check:
    """A permission nobody can exercise is a permanently closed door. Usually that
    is the intent for `accept.force`; for `accept` it is a project that has locked
    itself out."""
    if not eff.roles:
        return Check("permission_holders", NA, "no roles defined")
    held = set()
    for actor in eff.members:
        held |= permissions_of(eff, actor)
    critical = [p for p in ("accept", "approve") if p not in held and holders_of(eff, p)]
    if critical:
        return Check("permission_holders", FAIL,
                     f"no member holds {', '.join(critical)} — the flow cannot complete",
                     [f"{p}: role(s) {', '.join(holders_of(eff, p))} have nobody in them" for p in critical])
    unheld = [p for p in PERMISSIONS if p not in held]
    return Check("permission_holders", PASS,
                 f"{len(held)}/{len(PERMISSIONS)} permissions have a holder"
                 + (f" (unheld: {', '.join(unheld)})" if unheld else ""))


#: The one sentence the three run-derived checks say when there is nothing behind them. They
#: are `NA` and not `PASS` in that case, so they leave the score's denominator instead of
#: adding a pass to its numerator — see `_check_runs_listing` for why this is the zero-evidence
#: case and a partly-unreadable directory is not.
UNLISTED = "the runs directory could not be listed, so this check had no record to read"


def _check_runs_listing(records: TaskRecords) -> Check:
    """Whether this project's run evidence could be listed at all — a check, not a footnote.

    This is an observation, not an inference about the records. `read_all_tasks` returned a
    real `PermissionError`/`NotADirectoryError`; what could not be established is anything
    about the runs, and *that* is what this check states. Earlier rounds rejected touching the
    score on the grounds that a WARN "would assert non-compliance about something never read".
    That conflates two claims: this one is about the directory, which was read, and says
    nothing about the records inside it.

    `_check_ledger` already holds the rule. A ledger line that is not valid JSON is not
    evidence that any policy was violated either, and it is a FAIL — because an audit trail
    that cannot be verified fails the audit, whatever it would have said. Run records are the
    same kind of evidence, so an audit that could not open them does not pass.

    Measured, this is what the disclosure-only version left standing: a project with
    `chmod 000 .rig/runs` produced exactly the checks and exactly the 1.0 score of a project
    with no runs at all — `required_criteria` and `approvals` passing against zero records,
    `force_rate` already NA — so the score could not tell "no runs happened" from "nobody
    could look", and a note beside it could not either, because the number is what gets
    averaged. With this check, the same project scores 5/6 rather than 7/7, and its verdict
    turns FAIL on its own: `Report.verdict` needed no special case, because the state finally
    has a Check to point at.

    NA and not PASS when the listing worked, so the healthy path keeps the denominator it has
    (`legacy_access` and `audit_ledger` take the same shape) — a project that reads fine must
    score exactly what it scored before.

    Deliberately silent about individually unreadable records. Those are the partial-loss case:
    the checks did read evidence, their verdicts are statements about it, and the count of what
    they could not read is already named in each of their details. Zero evidence is not a small
    amount of evidence.
    """
    if records.collection_error is None:
        return Check("runs_listing", NA, "the runs directory was readable")
    return Check("runs_listing", FAIL,
                 f"the runs directory could not be listed ({records.collection_error}) — no "
                 f"run-derived check had a record to read, so nothing about this project's "
                 f"runs was measured",
                 ["make .rig/runs readable, then re-run `rig-wb govern conformance`"])


def _check_criteria_wired(root: pathlib.Path, eff: EffectivePolicy, records: TaskRecords,
                          in_window: tuple[dict, ...]) -> Check:
    """Policy-required criteria are injected into every new gate by
    `workbench.state.build_acceptance`. This check catches the runs that predate
    the requirement, or were built while a layer was missing — they are the ones
    that would otherwise be accepted without ever seeing the criterion."""
    required = {t: c for t, c in eff.require_criteria.items() if c}
    if not required:
        return Check("required_criteria", NA, "the policy requires no extra criteria")
    if records.collection_error is not None:
        return Check("required_criteria", NA, UNLISTED)
    tasks = list(in_window)
    offenders: list[str] = []
    unreadable_gates: list[str] = []
    for task in tasks:
        if task.get("status") != "accepted":
            continue
        acc = _acceptance(root, task.get("task_id", ""))
        if acc is UNREADABLE_ACCEPTANCE:
            # Not an offender: a criterion cannot be shown missing from a record nobody read.
            # Not a skip either, which is what it was — the run then landed in the count of
            # runs reported clean.
            unreadable_gates.append(task.get("task_id", "?"))
            continue
        if not acc:
            continue
        present = {c.get("name") for c in acc.get("checks", [])}
        want = eff.required_criteria_for(task.get("task_type") or "", acc.get("presets") or [])
        missing = [c for c in want if c not in present]
        if missing:
            offenders.append(f"{task.get('task_id')}: missing {', '.join(missing)}")
    total = sum(len(v) for v in required.values())
    # Two files stand behind this verdict and each gets its own clause. `records.note()`
    # covers the run records: it is what makes "no run skipped a criterion" read as "no run I
    # could read did". It says nothing about `acceptance.json`, which is the file this check
    # actually scans and a separate way to be unreadable — a run whose `task.json` parses and
    # whose gate record does not is counted by the first clause as read.
    shortfall = records.note() + (
        f" — {len(unreadable_gates)} accepted run(s) had an acceptance record that could not "
        f"be read, so they were not scanned: {', '.join(unreadable_gates[:10])}"
        if unreadable_gates else "")
    if offenders:
        return Check("required_criteria", FAIL,
                     f"{len(offenders)} accepted run(s) were gated without policy-required "
                     f"criteria{shortfall}", offenders[:10])
    return Check("required_criteria", PASS,
                 f"{total} policy-required criterion/criteria wired into the gate; "
                 f"{len(tasks)} run(s) in the window are clean{shortfall}")


def _check_approvals(root: pathlib.Path, eff: EffectivePolicy, records: TaskRecords,
                     in_window: tuple[dict, ...]) -> Check:
    quorums = {t: r for t, r in eff.approvals.items() if (r.get("quorum") or 0) > 0}
    if not quorums:
        return Check("approvals", NA, "the policy requires no approvals")
    if records.collection_error is not None:
        return Check("approvals", NA, UNLISTED)
    offenders: list[str] = []
    checked = 0
    for task in in_window:
        if task.get("status") != "accepted":
            continue
        rule = eff.approval_rule(task.get("task_type") or "")
        if (rule.get("quorum") or 0) <= 0:
            continue
        checked += 1
        status = evaluate(eff, task, load_approvals(root, task.get("task_id", "")))
        if not status.satisfied:
            offenders.append(f"{task.get('task_id')} ({task.get('task_type')}): "
                             f"{status.counted}/{status.required} approvals")
    shortfall = records.note()
    if offenders:
        return Check("approvals", FAIL,
                     f"{len(offenders)} of {checked} accepted run(s) were applied without "
                     f"their approvals{shortfall}", offenders[:10])
    rules = ", ".join(f"{t}≥{r['quorum']}" for t, r in sorted(quorums.items()))
    return Check("approvals", PASS, f"required ({rules}); {checked} accepted run(s) in the "
                                    f"window satisfied it{shortfall}")


def _check_waivers(root: pathlib.Path, eff: EffectivePolicy) -> Check:
    waivers = waiver.load_waivers(root)
    if not waivers:
        return Check("waivers", PASS, "no exceptions outstanding")
    active = [w for w in waivers if waiver.is_active(w)]
    expired = [w for w in waivers if not w.get("revoked") and not waiver.is_active(w)]
    non_waivable = set((eff.waivers or {}).get("non_waivable") or [])
    violating = [w for w in active if set(w.get("criteria") or []) & non_waivable]
    if violating:
        return Check("waivers", FAIL,
                     f"{len(violating)} live waiver(s) cover criteria the org marked non-waivable",
                     [f"{w['id']}: {', '.join(sorted(set(w['criteria']) & non_waivable))}" for w in violating])
    if active:
        return Check("waivers", WARN,
                     f"{len(active)} live waiver(s), {len(expired)} lapsed",
                     [f"{w['id']} → {', '.join(w.get('criteria') or [])} until {w.get('expires')} "
                      f"(by {w.get('granted_by')})" for w in active[:10]])
    return Check("waivers", PASS, f"no live waivers ({len(expired)} lapsed, kept for the record)")


def _check_force_rate(records: TaskRecords, in_window: tuple[dict, ...],
                      since_days: int) -> Check:
    """The single most informative number in the whole report: how often the gate
    was overridden rather than met.

    It is also the one a lost record moves the most. A record that could not be read may
    have been an accepted, forced run, so 1/1 (fail) and 1/2 (warn) differ by a file this
    function never opened — which is why the count of what could not be read is stated
    beside the rate in every branch, including the one that reports no accepted runs at all.
    """
    if records.collection_error is not None:
        # Not "no accepted runs in the last N days" with a note appended. That branch is a
        # statement about the window; nobody looked at the window here.
        return Check("force_rate", NA, UNLISTED)
    accepted = [t for t in in_window if t.get("status") == "accepted"]
    shortfall = records.note()
    if not accepted:
        return Check("force_rate", NA,
                     f"no accepted runs in the last {since_days} days{shortfall}")
    forced = [t for t in accepted if t.get("forced")]
    rate = len(forced) / len(accepted)
    counted = f"{len(forced)}/{len(accepted)} accepted runs were forced ({rate:.0%})"
    if rate >= 0.25:
        return Check("force_rate", FAIL,
                     f"{counted} — the gate is being routed around, not met{shortfall}",
                     [t.get("task_id", "?") for t in forced[:10]])
    if forced:
        return Check("force_rate", WARN, counted + shortfall,
                     [t.get("task_id", "?") for t in forced[:10]])
    return Check("force_rate", PASS, counted + shortfall)


def _check_ledger(root: pathlib.Path, eff: EffectivePolicy) -> Check:
    result = ledger.verify(root)
    if not result.entries:
        if eff.audit_chain_required:
            return Check("audit_ledger", WARN,
                         "the policy requires a chained audit trail and the ledger is empty "
                         "(expected on a repository that has not accepted anything yet)")
        return Check("audit_ledger", NA, "no ledger entries yet")
    if not result.ok:
        return Check("audit_ledger", FAIL, result.summary(), result.problems[:10])
    return Check("audit_ledger", PASS, result.summary())


def _check_legacy_access(root: pathlib.Path, eff: EffectivePolicy) -> Check:
    """`.rig/access.json` still works, and still only covers `accept`. Once a policy
    exists, keeping both means two sources of truth for one question."""
    p = root / ".rig" / "access.json"
    if not p.is_file():
        return Check("legacy_access", NA, "no legacy .rig/access.json")
    if eff.roles:
        return Check("legacy_access", WARN,
                     ".rig/access.json and the policy's roles both restrict accept — "
                     "two sources of truth for one decision",
                     ["fold it in with `rig-wb govern migrate`, then delete the file"])
    return Check("legacy_access", WARN,
                 ".rig/access.json is in use but the policy defines no roles",
                 ["`rig-wb govern migrate` converts it into a policy layer"])


# ── org rollup ───────────────────────────────────────────────────────────────
def rate_qualifier(unread: int, unlisted: int) -> str:
    """The parenthetical a rendered conformance rate carries, or "" when nothing was lost.

    Module level, not a `Rollup` helper, because `rig-wb evidence` prints per-team rates from
    the same dicts and printed them bare: `govern rollup` said `94% (1 unread, 1 unlisted)`
    for a team while `evidence fleet` said `score=94%` for that same team from that same dict,
    and the org clause above it named the project without naming its team, so the two numbers
    could not be joined by a reader. A rate whose shortfall lives elsewhere on the page is
    read without it — which is the rule the team cell was already written to, one renderer
    short. One builder, so a third caller cannot invent a fourth wording.
    """
    parts = ([f"{unread} unread"] if unread else []) + ([f"{unlisted} unlisted"] if unlisted else [])
    return f" ({', '.join(parts)})" if parts else ""


@dataclasses.dataclass
class Rollup:
    reports: list[Report]

    @property
    def teams(self) -> dict[str, list[Report]]:
        out: dict[str, list[Report]] = {}
        for r in self.reports:
            out.setdefault(r.team or "(unassigned)", []).append(r)
        return out

    @property
    def score(self) -> float:
        return (sum(r.score for r in self.reports) / len(self.reports)) if self.reports else 0.0

    @staticmethod
    def unreadable_in(reports: list[Report]) -> list[str]:
        """`<project>/<run directory>` for every record these reports could not read.

        The aggregate is where a lost record is hardest to notice — one project's shortfall
        is averaged into a fleet number — so it travels up with the scores rather than
        stopping at the project report that found it.
        """
        return [f"{r.project}/{name}" for r in reports if r.tasks for name in r.tasks.unreadable]

    @staticmethod
    def unlisted_in(reports: list[Report]) -> list[str]:
        """Every project here whose runs directory could not be listed at all.

        A second list rather than more entries in `unreadable_in`, because the two facts are
        not the same size and only one of them is a count of records. `unreadable_in` names
        run directories, so `len()` of it is exactly "how many records could not be read"; a
        directory that could not be listed yields no names and no total, and folding it in
        would print a record count nobody measured.

        It has to reach this layer under its own name even though the score now moves.
        `runs_listing` makes such a project FAIL and drops its three run-derived checks out of
        the denominator, so it scores 5/6 instead of 7/7 and no longer lifts the org rate by
        being unmeasurable. What that arithmetic still cannot say is *which* project, or how
        many: a fleet number is one figure, and "85%" does not tell a reader that one of the
        two projects behind it was never read. So the names travel up beside the rate as well.
        """
        return [r.project for r in reports if r.tasks and r.tasks.collection_error is not None]

    def to_dict(self) -> dict:
        return {
            "projects": len(self.reports),
            "score": round(self.score, 4),
            "unreadable_task_records": self.unreadable_in(self.reports),
            # Separate key, separate meaning: the list above counts records, this one names
            # projects whose runs directory yielded no count at all. A consumer that added
            # them would be reporting a number of records that was never measured.
            "unlisted_runs_directories": self.unlisted_in(self.reports),
            "teams": {
                team: {
                    "projects": len(rs),
                    "score": round(sum(r.score for r in rs) / len(rs), 4),
                    "unreadable_task_records": self.unreadable_in(rs),
                    "unlisted_runs_directories": self.unlisted_in(rs),
                    "failing": [r.project for r in rs if r.verdict == FAIL],
                    "findings": sorted({f for r in rs for f in r.findings}),
                }
                for team, rs in sorted(self.teams.items())
            },
            "reports": [r.to_dict() for r in self.reports],
        }

    def markdown(self) -> str:
        orgs = sorted({r.org for r in self.reports if r.org})
        lines = [f"## rig govern rollup: {', '.join(orgs) or '(no org)'}", ""]
        unreadable = self.unreadable_in(self.reports)
        unlisted = self.unlisted_in(self.reports)
        lines.append(f"projects: {len(self.reports)}  ·  org conformance: {self.score:.0%}"
                     + (f"  ·  {len(unreadable)} task record(s) could not be read "
                        f"({', '.join(unreadable[:10])}), so they are counted neither as "
                        f"conforming nor as failing" if unreadable else "")
                     # Not phrased as a record count. Nobody knows how many records are in a
                     # directory that could not be listed; what is known is that the checks
                     # which count runs had nothing to count.
                     + (f"  ·  {len(unlisted)} project(s) whose runs directory could not be "
                        f"listed ({', '.join(unlisted[:10])}), so their run-derived checks are "
                        f"not applicable and each fails runs_listing" if unlisted else ""))
        lines.append("")
        lines.append("| team | projects | conformance | failing checks |")
        lines.append("|---|---|---|---|")
        for team, rs in sorted(self.teams.items()):
            team_score = sum(r.score for r in rs) / len(rs)
            failing = sorted({f for r in rs for f in r.findings})
            # In the cell, not in a footnote: this column is the number a team is read on,
            # and a rate whose shortfall lives elsewhere on the page is read without it.
            lines.append(f"| {team} | {len(rs)} | {team_score:.0%}"
                         + rate_qualifier(len(self.unreadable_in(rs)), len(self.unlisted_in(rs)))
                         + f" | {', '.join(failing) if failing else '—'} |")
        lines.append("")
        lines.append("| project | team | verdict | score | worst finding |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(self.reports, key=lambda r: (r.team or "", r.project)):
            worst = sorted(r.checks, key=lambda c: _RANK[c.verdict])
            unlisted = r.tasks.collection_error if r.tasks else None
            # No special case for the unlisted project any more. It used to need one: every
            # check that could have found something ran against nothing and passed, so this
            # column had no FAIL or WARN to point at and printed "—" for the row a reader most
            # needed to look at. `runs_listing` is that FAIL, and it is ordered ahead of the
            # checks whose applicability it decides, so the ordinary path below reaches it.
            note = r.error or (f"{worst[0].id}: {worst[0].detail}"
                               if worst and worst[0].verdict in (FAIL, WARN) else "—")
            unread = len(r.tasks.unreadable) if r.tasks else 0
            lines.append(f"| {r.project} | {r.team or '—'} | {ICON[r.verdict]} {r.verdict} | "
                         f"{r.score:.0%}" + rate_qualifier(unread, 1 if unlisted else 0)
                         + f" | {note} |")
        return "\n".join(lines)


def rollup(roots: list[pathlib.Path], *, since_days: int = 90) -> Rollup:
    return Rollup([evaluate_project(r, since_days=since_days) for r in roots])

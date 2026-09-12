"""The compact task package a fresh agent session receives instead of a transcript (#460).

When rig creates a task worktree through Orca and starts an agent inside it, that agent
starts from nothing. Handing it the root session's whole conversation would carry unrelated
history, stale hypotheses and other tasks into the implementation session; handing it
nothing would make it guess. What travels is a package: goal, the constraints rig imposes,
the acceptance criteria the gate will ask about, the identifiers a later run needs to find
this one, where to write things down — and, since #T1, every figure the handoff carries
together with how it was established.

Composed from what the task record already holds. Nothing here is inferred from the task
text, and the goal is fenced as untrusted text the same way the orchestrator fences it —
an issue body pasted into `new` is data describing the task, not instructions to the agent.

Relayed measurements
--------------------

The numbers that went wrong in this repository did not travel through commit messages. They
travelled from lane to lane inside handoff prompts, as bare assertions in prose, and nothing
in the package ever had to say where one came from. `compose` had no column for a figure at
all, so a number could only ride along inside the goal text, where it is indistinguishable
from a fact.

`measurements` is that column, and it is a schema rather than a paragraph: every figure
states its origin, or the package is refused before any agent reads it.

**The vocabulary is borrowed whole, not restated.** `production_outcome` already ranks how a
number came to be known — `measured`, `reported`, `estimated`, `unmeasured`, `inconclusive`,
with `declared_by` beside them — and a second set of words for the same three ideas is the
drift this repository keeps finding in other people's repositories. So the tags here *are*
`production_outcome.MEASURED`, `REPORTED` and `UNMEASURED`, imported; what the task called
computed / attested / unobserved is those three, in the words that already exist.

* `measured` — a command produced it, and `source` **is** that command, so the receiver can
  run it again. `declared_by` is refused on it: a re-runnable command does not need a person
  standing behind it, and a figure offering both origins lets a reader pick the stronger one.
* `reported` — a person or a document stated it, and `declared_by` names them. `source` is
  optional beside it and says where they stated it. It settles nothing the receiver has not
  checked; `production_outcome` lets `reported` settle a metric because an adapter's named
  human source is narrower than anyone who can write into a task record.
* `unmeasured` — nobody looked. It keeps `production_outcome`'s meaning exactly: *there is no
  number*. So it names the metric and carries no `value`, and an entry that writes a figure
  down under the one tag excused from saying where it came from is refused.

A figure also carries `unit` and `observed_at` whenever it carries a `value`, for the
reasons `production_outcome` gives: a number without a unit cannot be compared to another,
and an undated one relays as `measured` however many lanes ago anyone looked — the staleness
this section exists to catch, arriving under the strongest of the three origins. And a metric
named twice is refused, as `validate_expectation` refuses a repeated id: three entries that
each state their origin perfectly still rebuild the original defect if two of them are
`p95`.

`estimated` and `inconclusive` are in the borrowed vocabulary and neither is an origin a
handoff may relay — refused **by name, with the reason**, the way `production_outcome`
refuses a bar key on an observation. An estimate does not settle a figure (`SETTLING`), and
to a receiver a figure that settles nothing is one nobody measured. `inconclusive` ranks a
comparison that was attempted, which is an outcome, not the origin of a number.

**The section is unconditional.** With no figures it says so, because the rule it states is
about the whole package: anything not listed — a number inside the goal text included — is
unobserved, and the receiver measures it before acting on it. A section that disappeared when
the caller had nothing to relay would leave exactly the numbers this exists to catch looking
like the rest of the prompt.

**A caller that builds the list must not mirror `criteria`.** `compose(..., criteria=list(...))`
passes `[]` happily; `measurements=[]` is refused. A caller assembling figures from a task
record passes `None` when it has none — `list(task.get("measurements") or []) or None`, not
`list(...)`. No caller in the tree builds the list yet.

**An empty list is refused, and `None` is not.** Every per-entry rule below reads an empty
list without objecting, so a caller who built a list and got nothing would be told the
package was fine by a check that looked at nothing. `None` says no figure travelled and is
rendered as that sentence; `[]` says a scan happened, and rig does not accept a scan of
nothing as evidence.
"""

from __future__ import annotations

from ..orchestrate.quarantine import strip_invisible, wrap_untrusted
from . import production_outcome as outcome

#: The three origins a relayed figure may carry, in `production_outcome`'s words.
RELAYED_KINDS = (outcome.MEASURED, outcome.REPORTED, outcome.UNMEASURED)

#: Closed, the way `production_outcome.ENTRY_KEYS` is: a key nothing renders is a key whose
#: author believes the receiver was told something.
RELAYED_KEYS = frozenset({"metric", "value", "unit", "observed_at", "kind", "source",
                          "declared_by"})

#: The two words from the same vocabulary that are not origins, each with why it is not.
_NOT_AN_ORIGIN = {
    outcome.ESTIMATED: (
        f"an estimate does not settle a figure (`production_outcome.SETTLING` holds "
        f"{outcome.MEASURED} and {outcome.REPORTED} and not this), and a figure that settles "
        f"nothing is one the receiver has to measure: write {outcome.UNMEASURED} and drop "
        f"the number"),
    outcome.INCONCLUSIVE: (
        "`inconclusive` is an outcome word — somebody looked and the looking settled nothing. "
        "It ranks a comparison that was attempted, not where a figure came from; a handoff "
        f"relays the figure, so the origins are {', '.join(RELAYED_KINDS)}"),
}

_TEXT_FIELDS = ("metric", "unit", "source", "declared_by")

#: What a stated figure owes beside its origin, in `production_outcome`'s words and for its
#: reasons: `unit` because "a number without one cannot be compared to another"
#: (`validate_expectation`), and `observed_at` because `ENTRY_KEYS` dates every observation.
#: A figure relayed three lanes ago is still tagged `measured`, and without a date the tag
#: says the command was run without saying when — which is the staleness this section exists
#: to catch, arriving under the strongest of the three origins.
_STATED_FIELDS = ("unit", "observed_at")


def _stated(entry: dict, field: str) -> bool:
    return outcome.nonempty_text(entry.get(field))


def _refusals(where: str, entry: object) -> list[str]:
    """Every way this entry fails to state its own origin, not the first one.

    Collected rather than short-circuited, for the reason `production_outcome` collects:
    an author refused once per fix learns nothing from the second refusal the first could
    have carried.
    """
    problems: list[str] = []
    if not isinstance(entry, dict):
        return [f"{where}: expected an object, got {type(entry).__name__}"]

    unknown = sorted(set(entry) - RELAYED_KEYS)
    if unknown:
        problems.append(
            f"{where}: {', '.join(repr(key) for key in unknown)} — a relayed figure is "
            f"{', '.join(sorted(RELAYED_KEYS))} and nothing else. Nothing renders another "
            f"key, so its author would believe the receiver had been told something")
    for field in _TEXT_FIELDS:
        if field in entry and not _stated(entry, field):
            problems.append(f"{where}: {field} {entry.get(field)!r} is not text with "
                            f"something in it")
            continue
        if not _stated(entry, field):
            continue
        text = entry[field]
        if text.splitlines() != [text]:
            # `splitlines()` rather than a scan for \n and \r: it is the set of breaks
            # Python itself treats as line ends — U+2028, U+2029, U+0085, U+000B, U+000C
            # among them — and a metric carrying one of those renders a second bullet into
            # the section that no check above ever read.
            problems.append(
                f"{where}: {field} spans more than one line, and one relayed figure renders "
                f"as exactly one — a field carrying any line break writes entries into the "
                f"section that nothing validated")
        if strip_invisible(text)[1]:
            # Not stripped quietly, the way `wrap_untrusted` strips the goal. The goal is
            # data the receiver is told to distrust; these fields are the evidence, and a
            # command or a name silently rewritten between validation and rendering is one
            # the receiver cannot go back to.
            problems.append(
                f"{where}: {field} carries zero-width or bidi control characters. They hide "
                f"from a reader what the model reads, and a reordered command or attribution "
                f"is a forged origin, not a typo")
    if _stated(entry, "source") and "`" in entry["source"]:
        problems.append(
            f"{where}: source carries a backtick, and the command renders inside an inline "
            f"code span — a backtick closes it early and puts the rest of the line outside "
            f"the span the receiver is told to re-run")
    if "metric" not in entry:
        problems.append(f"{where}: names no metric, so nothing says what the figure is about")

    kind = entry.get("kind")
    if kind not in RELAYED_KINDS:
        reason = _NOT_AN_ORIGIN.get(kind)
        problems.append(
            f"{where}: kind {kind!r} is not one of {', '.join(RELAYED_KINDS)} — every relayed "
            f"figure says how it was established" + (f". {reason}" if reason else ""))
        return problems

    if kind == outcome.UNMEASURED:
        # `unmeasured` is production_outcome's "nobody looked, there is no number", and a
        # number written down under it would be the untraceable figure with the one tag that
        # excuses it from naming an origin.
        for field in ("value", *_STATED_FIELDS, "source", "declared_by"):
            if field in entry:
                problems.append(
                    f"{where}: kind {outcome.UNMEASURED} says nobody looked, so there is no "
                    f"{field} to carry. Name the metric alone, or state the origin of the "
                    f"figure under {outcome.MEASURED} or {outcome.REPORTED}")
        return problems

    if not outcome.finite_number(entry.get("value")):
        problems.append(f"{where}: value {entry.get('value')!r} is not a finite number, and "
                        f"kind {kind} says one was established")
    if not _stated(entry, "unit"):
        problems.append(f"{where}: has no unit, and a number without one cannot be compared "
                        f"to another — which is the only thing the receiver can do with it")
    if outcome.offset_timestamp(entry.get("observed_at")) is None:
        problems.append(
            f"{where}: observed_at {entry.get('observed_at')!r} is not an ISO 8601 timestamp "
            f"with an offset. A figure carries the moment it was established or it relays as "
            f"{kind} forever, however many lanes ago anyone actually looked")
    if kind == outcome.MEASURED:
        if not _stated(entry, "source"):
            problems.append(
                f"{where}: kind {outcome.MEASURED} says a command produced this figure, so "
                f"source has to be that command — the receiver re-runs it rather than "
                f"believing the number")
        if "declared_by" in entry:
            problems.append(
                f"{where}: declared_by belongs to {outcome.REPORTED}. A command is what "
                f"stands behind a {outcome.MEASURED} figure, and an entry offering both "
                f"origins lets a reader take whichever is more convenient")
    elif not _stated(entry, "declared_by"):
        problems.append(
            f"{where}: kind {outcome.REPORTED} says somebody stated this figure, so "
            f"declared_by has to name them — an unattributed claim is one the receiver "
            f"cannot go back to")
    return problems


def _repeated(entries: list) -> list[str]:
    """A metric named twice, refused the way `validate_expectation` refuses a repeated id.

    Without it `p95 = 574 measured`, `p95 = 820 reported` and `p95 unmeasured` compose into
    one section, each entry stating its own origin perfectly. Two figures for one metric is
    two answers, and nothing in the package tells the receiver which of them to act on —
    which is the handoff this section was added to stop, reassembled out of valid parts.
    """
    problems, seen = [], set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not outcome.nonempty_text(entry.get("metric")):
            continue
        name = entry["metric"].strip()
        if name in seen:
            problems.append(f"measurements[{index}]: relays {name!r} twice. Two figures for "
                            f"one metric is two answers, and a package that carries both "
                            f"leaves the receiver to pick the convenient one")
        seen.add(name)
    return problems


def validate_measurements(entries: object) -> list[str]:
    """Every way this list of relayed figures is not one. Empty means it is one."""
    if not isinstance(entries, list):
        return [f"measurements: expected a list, got {type(entries).__name__}"]
    if not entries:
        return ["measurements: the list is empty, and every rule below reads it without "
                "objecting — accepting it would report a scan of nothing as a package that "
                "stated its figures. Pass measurements=None to say no figure travelled with "
                "this handoff; the section then says exactly that"]
    return [problem for index, entry in enumerate(entries)
            for problem in _refusals(f"measurements[{index}]", entry)] + _repeated(entries)


def _figure(entry: dict) -> str:
    """One relayed figure as one line: what it is, what it says, and how it is known."""
    kind = entry["kind"]
    if kind == outcome.UNMEASURED:
        return (f"- {entry['metric']} — {outcome.UNMEASURED}: nobody looked, so there is no "
                f"number. Measure it yourself before you act on it.")
    head = (f"- {entry['metric']} = {entry['value']} {entry['unit']} — {kind} "
            f"{entry['observed_at']}: ")
    if kind == outcome.MEASURED:
        return head + (f"`{entry['source']}` produced it. Re-run that command if the work "
                       f"turns on the number, or if that timestamp is old.")
    where = f", in {entry['source']}" if entry.get("source") else ""
    return head + (f"{entry['declared_by']} stated it{where}. A statement is not a "
                   f"measurement; check it before the work turns on it.")


def _relayed(measurements: list[dict] | None) -> list[str]:
    """The section, present whether or not anything was relayed."""
    lines = ["", "## Relayed measurements", ""]
    if measurements:
        lines += ["Every figure this handoff carries, and how it was established. None of it "
                  "is evidence you produced.", ""]
        lines += [_figure(entry) for entry in measurements]
    else:
        lines.append("No figure was relayed with this handoff.")
    lines += ["",
              f"Any figure not listed above is {outcome.UNMEASURED} — including every number "
              f"inside the goal text, which nothing here established. Measure it before you "
              f"act on it, and never pass it on as though it had been."]
    return lines


def compose(task: dict, *, criteria: list[str] | None = None,
            measurements: list[dict] | None = None) -> str:
    """The package as one prompt, sections in a fixed order.

    `measurements` is keyword-only and defaults to `None`, so the callers that predate the
    section compose exactly as before. A list that does not state the origin of every figure
    in it raises `ValueError` rather than composing: a handoff is refused where it is built,
    not judged later by the receiver who has no way to check it.
    """
    if measurements is not None:
        problems = validate_measurements(measurements)
        if problems:
            raise ValueError(
                "a task package cannot relay a figure whose origin it does not state:\n"
                + "\n".join(f"- {problem}" for problem in problems))
    route = task.get("route") if isinstance(task.get("route"), dict) else {}
    lines = [
        "# rig task package",
        "",
        f"task_id: {task.get('task_id', '?')}",
        f"task_type: {task.get('task_type', '?')}",
        f"recipe: {task.get('recipe') or '(none)'}",
        f"base: {task.get('base_branch', '?')} @ {task.get('base_commit', '?')}",
        f"branch: {task.get('branch') or '(none)'}",
        "",
        "## Goal",
        "",
        wrap_untrusted(str(task.get("input") or ""), "task text"),
    ]
    lines += _relayed(measurements)
    lines += [
        "",
        "## Constraints",
        "",
        "- Work only inside this worktree. rig accepts or discards the result; you do not.",
        "- Do not edit `.rig/`, CI workflows or the acceptance gate; that is tampering and the gate detects it.",
        "- Keep the diff to what the goal needs. Unrelated changes fail `no_unrelated_diff`.",
        "- Never commit secrets. The gate scans the diff and refuses accept on a finding.",
    ]
    if criteria:
        lines += ["", "## Acceptance criteria (what the gate will ask)", ""]
        lines += [f"- {name}" for name in criteria]
    reviewers = route.get("reviewers") if isinstance(route.get("reviewers"), list) else []
    if reviewers:
        lines += ["", "## Independent review", "",
                  "A separate verifier judges the result; do not try to pass it by describing "
                  "the work — the diff is what is judged. Reviewers: " + ", ".join(map(str, reviewers))]
    lines += [
        "",
        "## Where to write things down",
        "",
        f"- Progress: `rig-wb wb step {task.get('task_id', '<task_id>')} ...` per step.",
        f"- For a later run: `rig-wb wb note {task.get('task_id', '<task_id>')} \"<what it should know>\" --about <path>`.",
        "- When done, stop. Accept is a human decision made outside this session.",
    ]
    return "\n".join(lines) + "\n"

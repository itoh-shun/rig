"""orchestrate run-state: state + gate evaluation (split from scripts/orchestrate.py)."""

import os
import json
import datetime
import pathlib
import hashlib
import re
import secrets
import stat

from . import config
from .gates import is_runtime_gate, validate_executable_steps
from .secure_fs import atomic_append_line, atomic_write_bytes, read_bytes as read_secure_bytes

# Bumped to 2 when the preflight gained the verdict-less-executor rule (#496/#497).
# A run-state written under version 1 carries a six-field `execution` record, which
# no longer matches what the current policy computes; rather than silently reading
# the old record as agreement, `_state_no_orchestrate` refuses it by version and the
# run stops with that reason named. An in-flight run started before the upgrade has
# to be restarted, which is the honest outcome — its steps were admitted under a
# rule that no longer holds.
#: An orchestrate run had no identity of any kind. The state carried recipe, goal, steps and
#: cursor; the telemetry record identified a run by timestamp, recipe and project. Two things
#: followed. A board row could not point at the run behind it, because there was nothing to
#: point with. And two runs of the same recipe starting in the same second were one record's
#: worth of identity between them — not a theoretical case, since the queue dispatches items
#: in parallel by design.
#:
#: `orc-` rather than `rig-` on purpose: a workbench task and an orchestrate run are different
#: execution models, and a joined board should not have to guess which kind of thing a row is.
#: The random suffix is what makes this an identity rather than a label — `make_task_id` can
#: omit one because creating the task directory surfaces a collision, and an orchestrate run
#: creates no directory, so a collision here would silently merge two runs in the log.
RUN_ID_RE = re.compile(r"^orc-\d{8}-\d{6}-[a-z0-9-]{1,32}-[0-9a-f]{6}$")


def make_run_id(recipe: str) -> str:
    """A stable, sortable, collision-resistant id for one orchestrate run."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    words = re.findall(r"[A-Za-z0-9]+", recipe or "")
    slug = "-".join(word.lower() for word in words)[:32].strip("-") or "run"
    return f"orc-{stamp}-{slug}-{secrets.token_hex(3)}"


EXECUTION_POLICY_VERSION = 2
_EXECUTION_FIELDS = (
    "structurally_valid", "orchestratable", "manual_only",
    "unsupported_gates", "verdictless_gates", "errors", "reason",
)
_EXECUTION_STATE_FIELDS = frozenset({
    "execution_policy_version", "no_orchestrate", "execution",
})


def _exact_equal(left: object, right: object) -> bool:
    """Compare JSON-shaped values without bool/int coercion or extra fields."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _exact_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _exact_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _state_no_orchestrate(state: dict) -> tuple[object, str | None]:
    """Read persisted policy while allowing safe pre-policy run-state files."""
    present = _EXECUTION_STATE_FIELDS.intersection(state)
    if not present:
        return False, None  # legacy state: only code-backed gates remain executable
    if present != _EXECUTION_STATE_FIELDS:
        return None, "execution policy schema is incomplete"
    version = state["execution_policy_version"]
    if type(version) is not int or version != EXECUTION_POLICY_VERSION:
        return None, f"unsupported execution policy version: {version!r}"
    if type(state["no_orchestrate"]) is not bool:
        return None, "execution policy no_orchestrate must be an exact boolean"
    return state["no_orchestrate"], None


def _invalidate_execution(execution: dict, error: str) -> dict:
    report = dict(execution)
    report["structurally_valid"] = False
    report["orchestratable"] = False
    report["errors"] = [*execution["errors"], error]
    report["reason"] = error
    return report


def enforce_executable_state(state: dict) -> dict:
    """Recompute execution safety from steps plus persisted manual-only provenance."""
    no_orchestrate, policy_error = _state_no_orchestrate(state)
    persisted = state.get("execution")
    execution = validate_executable_steps(
        state.get("steps"), no_orchestrate=no_orchestrate,
    )
    if policy_error:
        execution = _invalidate_execution(execution, policy_error)
    elif state.get("execution_policy_version") == EXECUTION_POLICY_VERSION:
        expected = {field: execution[field] for field in _EXECUTION_FIELDS}
        if not isinstance(persisted, dict) or not _exact_equal(persisted, expected):
            execution = _invalidate_execution(
                execution, "persisted execution provenance is inconsistent with run-state policy",
            )
    state["execution"] = execution
    if execution["orchestratable"]:
        return execution
    # Name the step, not just the reason: "computationally nonexecutable" with `at: —`
    # sends the reader hunting through the whole recipe for the one step that is wrong.
    offenders = [*execution.get("verdictless_gates", []), *execution["unsupported_gates"]]
    at = offenders[0]["step"] if offenders else "—"
    state["stopped"] = {
        "reason": f"computationally nonexecutable: {execution['reason']}",
        "kind": "BLOCKED",
        "at": at,
    }
    return execution

# ── run-state ────────────────────────────────────────────────────────────────
def _recipe_owner_provenance(source: str) -> dict | None:
    """Resolve an installed recipe source to its validated owner identity."""
    from rig_workbench.packs.catalog import discover_builtin_packs
    from rig_workbench.packs.resolver import resolved_collection

    try:
        source_path = pathlib.Path(source).resolve(strict=True)
    except OSError:
        return None
    candidates = [
        (record.id, record.path, record.manifest)
        # Installed packs are repository state (#471). Resolved from a linked worktree
        # this collection is empty, so a repository-installed recipe gets no owner and
        # no provenance — and the resume-time integrity check has nothing to compare
        # against. It worked from the main checkout and would have stopped working the
        # moment a run started anywhere else.
        for record in resolved_collection(project=config.INVOCATION_CWD,
                                          shared=config.STATE_ROOT)
    ]
    candidates.extend(
        (pack_id, root, manifest)
        for (_namespace, pack_id), (root, manifest) in discover_builtin_packs().items()
    )
    for owner, root, manifest in candidates:
        root = root.resolve()
        try:
            relative = source_path.relative_to(root).as_posix()
        except ValueError:
            continue
        declared = {
            item for paths in manifest.get("assets", {}).values() for item in paths
        }
        if relative in declared:
            return {
                "source": str(source_path),
                "owner": owner,
                "root": str(root),
                "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            }
    return None


def new_state(
    recipe: str, steps: list[dict], goal: str | None, execution: dict | None = None,
) -> dict:
    if execution is None:
        execution = validate_executable_steps(steps)
    no_orchestrate = execution.get("manual_only") if isinstance(execution, dict) else None
    bound_steps = []
    provenance_by_source = {}
    for original in steps:
        step = dict(original)
        source = step.get("recipe_source")
        provenance = _recipe_owner_provenance(source) if isinstance(source, str) else None
        if provenance is not None:
            step["recipe_owner"] = provenance["owner"]
            step["recipe_owner_root"] = provenance["root"]
            provenance_by_source[provenance["source"]] = provenance
        bound_steps.append(step)
    steps = bound_steps
    state = {
        # Created once, here, and carried through `save_state`/`load_state` unchanged — a
        # resumed run is the same run, and a second id would split its telemetry in two.
        "run_id": make_run_id(recipe),
        "recipe": recipe,
        "goal": goal,
        "steps": steps,
        "cursor": 0,
        # `approvals` holds the human-gate decisions for a step (v2.1), symmetric with
        # `verdicts` (a model's judgment) and `checks` (a machine's). Empty for every
        # step that declares no human gate, which is all of them by default.
        "step_state": {s["id"]: {"status": "pending", "retries": 0, "checks": [],
                                 "verdicts": [], "approvals": []}
                       for s in steps},
        "adaptive": {
            "assessment": None,
            "invocation_limit": 3,
            "invocations": 0,
        },
        "stopped": None,
        "done": False,
        "history": [],
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "no_orchestrate": no_orchestrate,
        "execution": execution,
    }
    if provenance_by_source:
        state["recipe_provenance"] = [
            provenance_by_source[source] for source in sorted(provenance_by_source)
        ]
    enforce_executable_state(state)
    return state


def save_state(state: dict, path: pathlib.Path) -> None:
    """Persist potentially sensitive run state without following filesystem links."""
    if state.get("secure_runtime"):
        persisted = json.loads(json.dumps(state, ensure_ascii=False))
        goal = persisted.get("goal")
        if isinstance(goal, str):
            persisted["secure_runtime"]["goal_sha256"] = hashlib.sha256(
                goal.encode("utf-8")
            ).hexdigest()
            persisted["goal"] = None
        payload = json.dumps(persisted, ensure_ascii=False, indent=2).encode("utf-8")
        atomic_write_bytes(path, payload)
        return
    path = pathlib.Path(path).absolute()
    parent = path.parent
    created_parent = not parent.exists()

    # A run-state contains the goal and may contain user/project identifiers.  Refuse
    # link traversal before creating or opening anything; in particular, chmod must
    # never be applied through a symlink to an unrelated directory or file.
    for component in (parent, *parent.parents):
        try:
            if component.is_symlink():
                raise OSError(f"refusing symlinked run-state path: {component}")
        except OSError:
            raise
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(parent, dir_flags)
    try:
        if created_parent:
            os.fchmod(dir_fd, 0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path.name, flags, 0o600, dir_fd=dir_fd)
        try:
            os.fchmod(fd, 0o600)
            payload = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(payload)
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(dir_fd)


def _verdict_summary(v: dict) -> dict:
    """One verdict's runs.jsonl summary (pure). Additive: per-criterion verdicts and the
    judge-panel multi-PASS record (order_sensitive + pass_set) only appear when present,
    so old-format verdicts keep the historical {by, ok} shape."""
    rec = {"by": v.get("by"), "ok": bool(v.get("ok"))}
    if v.get("criteria"):
        rec["criteria"] = [{"n": c.get("n"), "verdict": c.get("verdict"),
                            "anchor": c.get("anchor", "")} for c in v["criteria"]]
    if v.get("order_sensitive"):
        rec["order_sensitive"] = True
        rec["pass_set"] = v.get("pass_set", [])
    return rec


def classify_failure(state: dict) -> str | None:
    """Best-guess a MAST-style failure-mode code for a stopped / escalated / blocked run.

    Pure and deterministic: derives a taxonomy code purely from signals already present in
    `state` (no model call). The vocabulary and the "which rig gate/brick should have caught
    it" mapping live in `skills/engine/patterns/failure-taxonomy.md` (adapted from MAST,
    arXiv 2503.13657 — 3 categories / 14 modes). Returns a code string, or None when the state
    shows no failure (a successful or still-running run — successful runs carry no failure_mode).

    Design note (MODEL-suggested-but-deterministically-stored): this is the deterministic
    best-guess from state. A richer classification could be model-supplied later (e.g. an
    escalation reviewer emitting a code); a future caller may pass that through instead, but
    the value telemetry records here is always the reproducible from-state one.

    Signal rules (first match wins):
      - a verdict from the generator itself (by=self/generator/producer/"") → `verification:self-grading`
        (self-graded gate; the BLOCKED path — grader != generator was violated).
      - escalated (stopped) on a step whose declared machine `checks` ran and failed →
        `verification:incorrect-implementation` (K retries exhausted; the sensor kept catching a bad impl).
      - escalated on a gated step (acceptance/review-gate) with no declared checks and no verdict →
        `verification:missing` (no independent verification was ever produced — a no-verifier stall).
      - any other stopped run → `unclassified` (a code exists but the signal is ambiguous; never silently dropped).
      - no failure signal → None.
    """
    ss = state.get("step_state") or {}

    # Self-grading can be present without `stopped` (compute_next returns BLOCKED without stopping).
    for st in ss.values():
        for v in st.get("verdicts") or []:
            if str(v.get("by", "")).lower() in ("", "self", "generator", "producer"):
                return "verification:self-grading"

    stopped = state.get("stopped")
    if not stopped:
        return None  # successful or in-progress run — no failure mode

    sid = stopped.get("at")
    step = next((s for s in (state.get("steps") or []) if s.get("id") == sid), None)
    st = ss.get(sid, {})
    if step is not None:
        declared = step.get("checks") or []
        ran = st.get("checks") or []
        if declared and any(not c.get("ok") for c in ran):
            return "verification:incorrect-implementation"
        if step.get("gate") in ("acceptance-gate", "review-gate") and not declared and not st.get("verdicts"):
            return "verification:missing"
    return "unclassified"


def telemetry_append(state: dict, final: str, *, caller_record: dict | None = None) -> None:
    """Append a one-line JSON summary of a single RUN to .rig/runs.jsonl (run telemetry).

    An execution log on par with run-state.json, not the knowledge layer (no approval needed;
    .rig/ is already gitignored). Aggregation is the `runs` subcommand. A write failure must
    not break the RUN result (best-effort).

    `caller_record` is handed in rather than looked up. This module holds gate evaluation,
    and `test_caller_contract` refuses any mention of the caller in it — deliberately, at file
    granularity, because a gate that can see who called it is a gate that can soften for one
    harness. Recording the value is not branching on it, but the way to keep that true is for
    the decision to be made outside this file and the value to arrive as data. `None` records
    nothing, which is what every existing caller of this function gets.
    """
    try:
        ss = state["step_state"]
        # step id -> most recent auto-route decision (#264; a step retried keeps its last decision)
        auto_routed: dict[str, dict] = {}
        learned_predictions: dict[str, dict] = {}
        fable_events: dict[str, list] = {}
        for h in state.get("history", []):
            if h.get("action") == "AUTO_ROUTE":
                auto_routed[h["step"]] = {"model": h.get("model"), "reason": h.get("reason")}
            elif h.get("action") == "LEARNED_ROUTE_PREDICTION":
                learned_predictions[h["step"]] = {k: v for k, v in h.items() if k not in ("action", "step")}  # #305
            elif h.get("action") in ("FABLE_REFUSAL", "FABLE_FALLBACK"):
                fable_events.setdefault(h["step"], []).append(
                    {k: v for k, v in h.items() if k not in ("action", "step")} |
                    {"kind": "refusal" if h["action"] == "FABLE_REFUSAL" else "fallback"})  # #297
        rec = {
            "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            # Absent, not null, for a state written before run ids existed: this log is read
            # by aggregation that treats a present key as a measured fact, and `None` would
            # claim the run was identified as nothing.
            **({"run_id": state["run_id"]} if state.get("run_id") else {}),
            "recipe": state["recipe"],
            "backend": "orchestrate",
            "invoker": os.environ.get("RIG_INVOKER") or "direct",
            # Who invoked rig, alongside `invoker`, which is what launched the process — the
            # two answer different questions once another agent is the one typing. Absent
            # when nothing identifies a caller, same rule as `run_id` and `perf` above. The
            # workbench producer has recorded this on the task since #428; this log carried
            # it from neither side until now, so a session or caller column had no source.
            # Absent when the driver handed nothing, same rule as `run_id` and `perf`.
            **({"caller": caller_record} if isinstance(caller_record, dict) else {}),
            "final": final,
            "steps_total": len(state["steps"]),
            "steps_passed": sum(1 for st in ss.values() if st.get("status") == "passed"),
            "retries": sum(st.get("retries", 0) for st in ss.values()),
            "escalated_at": (state.get("stopped") or {}).get("at") if state.get("stopped") else None,
            "token_usage": state.get("token_usage") or {},  # #271/#296: provider -> {prompt/completion_tokens, calls}
            # #502: where the time went, by phase. Absent when nothing was timed — a record
            # carrying `"perf": {}` would read as "measured, and it was nothing".
            **({"perf": state["perf"]} if state.get("perf") else {}),
            **({"perf_budget_broken": state["perf_budget_broken"]}
               if state.get("perf_budget_broken") else {}),
            "steps": [{"id": s["id"], "status": ss[s["id"]].get("status"),
                       "retries": ss[s["id"]].get("retries", 0),
                       "model": ss[s["id"]].get("model"),  # actually-used generator model (#293; None = provider default)
                       "verdicts": [_verdict_summary(v)
                                    for v in ss[s["id"]].get("verdicts", [])],
                       **({"auto_route": auto_routed[s["id"]]} if s["id"] in auto_routed else {}),
                       **({"learned_route": learned_predictions[s["id"]]} if s["id"] in learned_predictions else {}),
                       **({"fable_events": fable_events[s["id"]]} if s["id"] in fable_events else {})}
                      for s in state["steps"]],
        }
        # Failure-mode taxonomy (additive; absent for successful runs). Deterministic best-guess
        # from state — see classify_failure / skills/engine/patterns/failure-taxonomy.md.
        failure_mode = classify_failure(state)
        if failure_mode is not None:
            rec["failure_mode"] = failure_mode
    except Exception:
        return  # a record that could not be built is a record not worth writing

    append_run_record(rec,
                      secure=bool(state.get("secure_runtime")),
                      secure_history_path=state.get("secure_history_path"))


def append_run_record(rec: dict, *, secure: bool = False,
                      secure_history_path: str | None = None,
                      runs_path: pathlib.Path | None = None,
                      project: pathlib.Path | None = None) -> None:
    """Append one finished telemetry record to `.rig/runs.jsonl`, then mirror it into the
    global index (`~/.rig/runs.jsonl`) with `project` attached for cross-project rollups.

    Split out of `telemetry_append` so a second backend can record through the same
    writer instead of a parallel one. Building the record stays with each backend —
    they know different things about a run — but *where* a record lands, keeping a
    secure run out of ambient cross-project state, and the rule that a failed write
    never breaks the run are one decision, and it is made here.

    `runs_path` / `project` override the process-wide defaults, which are resolved from
    the cwd at import time. That is right for orchestrate, which runs inside the repo it
    is orchestrating, and wrong for a caller that already knows which repository the run
    belongs to — a workbench task carries its own root.
    """
    target = runs_path or config.RUNS_PATH
    try:
        encoded = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        if secure:
            if not isinstance(secure_history_path, str):
                raise OSError("secure runtime history path is missing")
            atomic_append_line(pathlib.Path(secure_history_path), encoded)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(encoded.decode("utf-8"))
    except Exception:
        pass

    if secure:
        return  # never mirror a sensitive run into ambient cross-project state

    # Keep the per-project log (cwd/.rig) while enabling cross-project aggregation of
    # how much rig-wb is used overall. The `project` field preserves provenance.
    # Write failures are swallowed (best-effort; the cwd-side record is primary).
    try:
        global_path = config.GLOBAL_RUNS_PATH
        global_path.parent.mkdir(parents=True, exist_ok=True)
        global_rec = dict(rec)
        global_rec["project"] = str(project or config.INVOCATION_CWD)
        with global_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(global_rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _validate_recipe_provenance(state: dict) -> None:
    """Fail a resumed owner-bound run closed if its exact pack owner is unavailable."""
    persisted = state.get("recipe_provenance")
    owner_bound_steps = [
        step for step in state.get("steps") or [] if step.get("recipe_owner")
    ]
    if persisted is None and not owner_bound_steps:
        return  # genuinely legacy run-state
    if not isinstance(persisted, list) or not persisted:
        state["stopped"] = {
            "reason": "owner-bound recipe provenance is missing from run-state",
            "kind": "BLOCKED", "at": "—",
        }
        return
    by_source = {}
    for expected in persisted:
        if not isinstance(expected, dict) or not isinstance(expected.get("source"), str):
            state["stopped"] = {
                "reason": "owner-bound recipe provenance is malformed",
                "kind": "BLOCKED", "at": "—",
            }
            return
        current = _recipe_owner_provenance(expected["source"])
        if current is None:
            state["stopped"] = {
                "reason": f"recipe owner disappeared for {expected['source']}",
                "kind": "BLOCKED", "at": "—",
            }
            return
        if current != expected:
            state["stopped"] = {
                "reason": f"recipe owner provenance changed for {expected['source']}",
                "kind": "BLOCKED", "at": "—",
            }
            return
        by_source[expected["source"]] = expected
    for step in owner_bound_steps:
        record = by_source.get(step.get("recipe_source"))
        if (
            record is None
            or record["owner"] != step.get("recipe_owner")
            or record["root"] != step.get("recipe_owner_root")
        ):
            state["stopped"] = {
                "reason": f"step `{step.get('id')}` lost its owner-bound recipe provenance",
                "kind": "BLOCKED", "at": step.get("id", "—"),
            }
            return


def _validate_secure_review_category_binding(state: dict) -> None:
    """Reject missing, changed, or duplicated secure Japanese category bindings."""
    if state.get("recipe") != "japanese-writing" or not state.get("secure_runtime"):
        return
    allowed = {"general", "incident_report", "support_reply"}
    category = state.get("review_category")
    secure_category = state["secure_runtime"].get("review_category")
    bindings = [
        row for row in state.get("history") or []
        if row.get("action") == "BIND_REVIEW_CATEGORY"
    ]
    if (
        category not in allowed
        or secure_category != category
        or bindings != [{"action": "BIND_REVIEW_CATEGORY", "category": category}]
    ):
        raise OSError("secure Japanese review category binding is missing or changed")


def _validate_secure_material_profile_binding(state: dict) -> None:
    """Reject missing or changed secure Japanese style-material bindings."""
    if state.get("recipe") != "japanese-writing" or not state.get("secure_runtime"):
        return
    allowed = {"none", "technical", "conversation"}
    profile = state.get("material_profile")
    secure = state["secure_runtime"]
    if (
        profile not in allowed
        or secure.get("material_profile") != profile
        or not _exact_equal(state.get("material_provenance"), secure.get("material_provenance"))
        or not _exact_equal(state.get("material_snapshot"), secure.get("material_snapshot"))
    ):
        raise OSError("secure Japanese material profile binding is missing or changed")
    write = next(
        (step for step in state.get("steps") or [] if step.get("id") == "write"), None
    )
    if write is None:
        raise OSError("secure Japanese material profile binding has no write step")
    try:
        from .providers import japanese_material_metadata
        expected = japanese_material_metadata(write, profile)
    except Exception as error:
        raise OSError(
            "secure Japanese material profile binding provenance cannot be verified"
        ) from error
    if not _exact_equal(state.get("material_provenance"), expected):
        raise OSError("secure Japanese material profile binding is missing or changed")
    snapshot = state.get("material_snapshot")
    if profile == "none":
        if snapshot is not None:
            raise OSError("secure Japanese material profile binding has an unexpected snapshot")
        return
    if not isinstance(snapshot, dict) or set(snapshot) != {"path", "sha256", "size_bytes"}:
        raise OSError("secure Japanese material profile binding has no sealed snapshot")
    try:
        payload = read_secure_bytes(pathlib.Path(str(snapshot["path"])))
    except OSError as error:
        raise OSError("secure Japanese material profile snapshot cannot be verified") from error
    if (
        len(payload) != snapshot.get("size_bytes")
        or hashlib.sha256(payload).hexdigest() != snapshot.get("sha256")
    ):
        raise OSError("secure Japanese material profile snapshot changed")


def load_state(path: pathlib.Path) -> dict:
    path = pathlib.Path(path).absolute()
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise OSError("run-state must be a caller-owned regular file with one link")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    state = json.loads(payload.decode("utf-8"))
    if state.get("secure_runtime"):
        secure_payload = read_secure_bytes(path)
        if secure_payload != payload:
            raise OSError("secure run-state changed during verification")
        state = json.loads(secure_payload.decode("utf-8"))
    _validate_secure_review_category_binding(state)
    _validate_secure_material_profile_binding(state)
    _validate_recipe_provenance(state)
    enforce_executable_state(state)
    return state


def _distill_failures(st: dict) -> str | None:
    """Distill this attempt's failed checks and dissenting verdicts into one short string
    for the retry generator (#333: reviewer findings must survive the RETRY record reset,
    or the retry is blind — it never sees why the previous attempt failed).

    Pure: reads only st["checks"] / st["verdicts"] (as populated for the just-failed attempt,
    before compute_next resets them). Returns None when there is nothing to report.
    """
    parts = []
    for c in st.get("checks") or []:
        if not c.get("ok"):
            parts.append(f"check failed: {c.get('cmd')}")
    for v in st.get("verdicts") or []:
        if not v.get("ok"):
            note = str(v.get("note", ""))[:240]
            parts.append(f"{v.get('by')}: {note}")
    if not parts:
        return None
    joined = "; ".join(parts)
    if len(joined) > 800:
        joined = joined[:800] + "…"
    return joined


# ── Gate evaluation (deterministic, pure functions) ──────────────────────────
def answered_criteria(declared: list, verdict: dict) -> set:
    """Which of the step's declared criteria this verdict actually answered.

    `_build_verify_prompt` numbers `step["acceptance"]` positionally, so a `CRITERION <n>`
    line answers a declared criterion only when `n` indexes that list. Counting the parsed
    lines instead would let thirteen lines numbered 20..32 satisfy an arity rule while
    answering nothing declared, and would double-count a criterion answered twice.
    """
    return {c["n"] for c in (verdict.get("criteria") or [])
            if isinstance(c.get("n"), int) and 1 <= c["n"] <= len(declared)}


def gate_outcome(step: dict, st: dict) -> str:
    """Deterministically judge the current step's pass/fail.
    Returns: pass | fail | incomplete | self-graded | unsupported | unanswered
    """
    declared = step["checks"]
    ran = st["checks"]
    verdicts = st["verdicts"]

    # Machine sensors (checks) — if declared, the primary evidence. Require all run and all ok.
    if declared:
        if len(ran) < len(declared):
            return "incomplete"        # not yet checked
        if any(not c["ok"] for c in ran):
            return "fail"

    # Inferential verification (verdict). acceptance-gate/review-gate mean an independent
    # judgment decides the step; checks[] are a precondition for that judgment, never a
    # substitute for it. Until #496 a runtime-gated step that also declared checks[] passed
    # on the checks alone with `verdicts: []` — the gate's name promised a judgment that
    # nobody had made.
    gate = step["gate"]
    if not gate:
        return "pass"  # gate-less steps pass through (when checks are empty)
    if gate and not is_runtime_gate(gate):
        return "unsupported"
    if is_runtime_gate(gate) and not verdicts:
        return "incomplete"            # awaiting the independent verifier's judgment

    # Enforce grader != generator (prevents self-grading bias; policies/independent-verification)
    if any(str(v.get("by", "")).lower() in ("", "self", "generator", "producer") for v in verdicts):
        return "self-graded"

    # A passing verdict has to answer every criterion the step declared. `_judge_output`'s
    # all-UNKNOWN guard cannot hold this line: it reads `if ok and criteria and
    # all(UNKNOWN)`, so an empty criteria list is vacuously all-UNKNOWN and skips the
    # check — which made the gate non-monotonic, answering one criterion UNKNOWN failing a
    # step that answering nothing at all passed.
    #
    # The rule is arity, not a floor of one: 1 of 13 with VERDICT PASS is `unanswered` too.
    # An earlier pass stopped at the floor because the shipped mock provider emitted a
    # single `CRITERION 1:` line whatever a step declared, so arity would have turned every
    # mock-driven bench/eval run of a 13-criterion `bugfix` acceptance step from DONE into
    # ESCALATE — re-baselining the eval harness instead of enforcing the contract. #519
    # removed that: mock now emits one line per declared criterion, measured here on
    # `bugfix` (13/13) and `adaptive-bugfix` (4/4), both still DONE.
    #
    # What this does NOT judge is the answers themselves. A verdict that answers all
    # thirteen and marks some UNKNOWN satisfies arity; whether that is a pass is
    # `_judge_output`'s question, and the two are kept apart on purpose — conflating them
    # would let a judge buy its way past arity with UNKNOWN, or fail a step for a shape
    # that is exactly what the contract asked for.
    declared_criteria = step.get("acceptance") or []
    if declared_criteria and any(
            v.get("ok") and len(answered_criteria(declared_criteria, v)) < len(declared_criteria)
            for v in verdicts):
        return "unanswered"
    if any(not v["ok"] for v in verdicts):
        return "fail"

    return "pass"


def govern_root() -> pathlib.Path:
    """Where `.rig/` lives for governance lookups — the nearest ancestor holding one,
    falling back to the invocation cwd. Same walk-up rule the rig-wb CLI uses, so a
    run started from a subdirectory still sees its org policy."""
    cwd = config.INVOCATION_CWD
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".rig").is_dir():
            return candidate
    return cwd


def stage_gate_status(step: dict, st: dict):
    """This step's human-gate status, or None when it has no human gate (v2.1).

    Resolved live against the effective policy rather than baked into the
    run-state: an org that tightens `stage:<id>` while a run is parked has
    tightened it for that run too, which is the whole point of the policy layer.

    A policy that fails to load is *not* treated as "no gate" — that would let a
    broken document silently open a stage. It raises, and the caller surfaces it.
    """
    try:
        from ..govern.policy import effective_policy
        from ..govern.stage import evaluate_stage
    except ImportError:                                    # pragma: no cover - packaging guard
        return None
    eff = effective_policy(govern_root())
    return evaluate_stage(eff, step, st.get("approvals") or [],
                          author=st.get("ran_as") or "")


def _record_actor(step: dict, st: dict) -> tuple[str | None, str | None]:
    """Stamp the running identity onto the step. Returns (blocking reason, advisory note).

    `ran_as` is what separation of duties compares an approver against, so it has
    to be recorded when the work happens, not when the approval arrives. A broken
    policy blocks (the same fail-closed rule as accept); an execution that does not
    hold the step's owning role only warns — see govern.stage.actor_mismatch.
    """
    try:
        from ..govern.identity import current_actor, org_binding_path
        from ..govern.policy import effective_policy
        from ..govern.stage import actor_mismatch
    except ImportError:                                    # pragma: no cover - packaging guard
        return None, None
    root = govern_root()
    # Cheap exit for the overwhelmingly common case. Resolving an identity shells
    # out to `git config`, and doing that at every step START of every run — in
    # repositories with no policy and no step that declares an owner — would be a
    # subprocess per step to record a value nothing reads.
    if not (step.get("actor") or step.get("human_gate") or org_binding_path(root).is_file()):
        return None, None
    try:
        actor = current_actor(root)
        eff = effective_policy(root)
        note = actor_mismatch(eff, step, actor)
    except Exception as e:
        return f"step `{step.get('id')}`: governance cannot be evaluated: {e}", None
    st["ran_as"] = actor
    return None, note


def compute_next(state: dict) -> tuple[str, str]:
    """Deterministically compute and apply the next action from the state (mutates state).
    Returns: (action_code, message)
    """
    enforce_executable_state(state)
    if state["stopped"]:
        if state["stopped"].get("kind") == "BLOCKED":
            return "BLOCKED", f"Blocked: {state['stopped']['reason']}"
        return "STOPPED", f"Stopped: {state['stopped']['reason']}"
    steps = state["steps"]
    if state["cursor"] >= len(steps):
        state["done"] = True
        return "DONE", "All steps complete."

    step = steps[state["cursor"]]
    sid = step["id"]
    st = state["step_state"][sid]

    if st["status"] == "pending":
        st["status"] = "running"
        # Who this attempt runs as. Recorded here, not at approval time, because it is
        # what separation of duties compares an approver against (v2.1).
        blocked, note = _record_actor(step, st)
        if blocked:
            state["stopped"] = {"reason": blocked, "kind": "BLOCKED", "at": sid}
            return "BLOCKED", blocked
        start_entry = {"action": "START", "step": sid}
        if note:
            start_entry["actor_note"] = note
        state["history"].append(start_entry)
        gate = step["gate"] or "none"
        need = []
        if step["checks"]:
            need.append(f"check ({len(step['checks'])} machine checks)")
        if is_runtime_gate(step["gate"]):
            # Not "…and not step['checks']": since #496 checks[] are a precondition for the
            # verdict, never a substitute, so a runtime-gated step needs the verdict either
            # way. The old wording told the operator a step with checks needed no verdict —
            # which is exactly the state the gate now refuses to pass.
            need.append("verdict (independent verifier judgment; grader != generator)")
        if step.get("human_gate"):
            need.append("human sign-off (`orchestrate approve`)")
        need_s = " → ".join(need) if need else "(no gate: just run next after the work)"
        owner = f" / actor: {step['actor']}" if step.get("actor") else ""
        warning = f"\n  [WARN] {note}" if note else ""
        return "START", (f"Run step `{sid}` (instruction: {step['instruction']} / gate: {gate}{owner}). "
                         f"Delegate the work, finish {need_s}, then `next`.{warning}")

    # status == "running" or "awaiting_approval"
    outcome = gate_outcome(step, st)
    if outcome == "incomplete":
        return "AWAIT", f"step `{sid}` awaits gate evaluation. Run `check` / `verdict`, then `next`."
    if outcome == "self-graded":
        return "BLOCKED", (f"step `{sid}`: a verdict from the generator itself (by=self/generator) is invalid. "
                           f"An independent verifier's `verdict` is required (grader != generator).")
    if outcome == "unsupported":
        return "BLOCKED", (f"step `{sid}` uses unsupported executable gate `{step['gate']}`. "
                           "Use the manual engine for this recipe; custom prompt patterns "
                           "must not pass through as executable gates.")
    if outcome == "pass":
        # The machine is satisfied; a human gate, if declared, still is not.
        try:
            approval = stage_gate_status(step, st)
        except Exception as e:                    # policy/human_gate is unusable → refuse to guess
            state["stopped"] = {"reason": f"step `{sid}`: human gate cannot be evaluated: {e}",
                                "kind": "BLOCKED", "at": sid}
            return "BLOCKED", state["stopped"]["reason"]
        if approval is not None and not approval.satisfied:
            if st["status"] != "awaiting_approval":
                st["status"] = "awaiting_approval"
                state["history"].append({"action": "AWAIT_APPROVAL", "step": sid})
            if approval.denials:
                who = ", ".join(d.get("actor", "?") for d in approval.denials)
                note = next((d.get("note") for d in approval.denials if d.get("note")), "")
                return "AWAIT_APPROVAL", (
                    f"step `{sid}` was rejected by {who}"
                    + (f": {note}" if note else "")
                    + ". Address it, then ask for a fresh approval (`orchestrate approve`).")
            return "AWAIT_APPROVAL", (
                f"step `{sid}` passed its gate and awaits human sign-off "
                f"({approval.counted}/{approval.required}"
                + (f", from {', '.join(approval.rule['roles'])}" if approval.rule.get("roles") else "")
                + "). Approve with `orchestrate approve " + sid + "`.")
        st["status"] = "passed"
        state["cursor"] += 1
        state["history"].append({"action": "PASS", "step": sid})
        if state["cursor"] >= len(steps):
            state["done"] = True
            return "DONE", f"step `{sid}` passed. All steps complete."
        nxt = steps[state["cursor"]]["id"]
        return "ADVANCE", f"step `{sid}` passed → next is step `{nxt}`. Start it with `next`."
    # fail
    st["retries"] += 1
    K = step["max_retries"]
    # Distill BEFORE the reset below (or an ESCALATE too) wipes checks/verdicts — reviewer
    # findings must survive the record reset or the retry is blind (#333).
    findings = _distill_failures(st)
    # Record which kind of failure this was. "unanswered" in particular is invisible in the
    # verdict records themselves — every verdict there says ok — so without this the reader
    # of a run record sees a step that failed with nothing that failed.
    fail_entry = {"action": "FAIL", "step": sid, "try": st["retries"], "outcome": outcome}
    if outcome == "unanswered" and findings is None:
        # Every verdict on an "unanswered" step says ok, so _distill_failures finds nothing
        # and the retry would be blind — it would see "failed" with no statement of what.
        # Name the shortfall and the criteria still owed: "answer them all" is not actionable
        # to a verifier that believes it already did, and the numbers are what it re-emits.
        declared_criteria = step.get("acceptance") or []
        owed = sorted(set(range(1, len(declared_criteria) + 1)).difference(
            *(answered_criteria(declared_criteria, v) for v in st["verdicts"] if v.get("ok"))
        )) if declared_criteria else []
        reached = len(declared_criteria) - len(owed)
        findings = (
            f"the verdict passed step `{sid}` answering {reached} of its "
            f"{len(declared_criteria)} declared criteria. Emit one "
            "`CRITERION <n>: PASS|FAIL|UNKNOWN — <anchor>` line per criterion"
            + (f"; still unanswered: {', '.join(str(n) for n in owed)}." if owed else ".")
        )
    if findings is not None:
        fail_entry["findings"] = findings
    state["history"].append(fail_entry)
    if st["retries"] >= K:
        state["stopped"] = {"reason": f"step `{sid}` failed the gate {K} times → escalating", "at": sid}
        return "ESCALATE", state["stopped"]["reason"] + " (no infinite loops; hand off to the user)."
    # Retry: redo this step (records are reset), but carry the distilled findings forward
    # via last_failure so _build_step_contract's previous_failure: line still shows them.
    st["status"] = "pending"
    st["checks"] = []
    st["verdicts"] = []
    if findings is not None:
        st["last_failure"] = findings
    return "RETRY", f"step `{sid}` failed → retrying (try {st['retries']+1}/{K}). Address the findings and rerun."

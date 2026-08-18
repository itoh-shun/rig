"""orchestrate queueing: task queue + cmd_queue (split from scripts/orchestrate.py)."""

import sys
import os
import json
import contextlib
import subprocess
import threading
import concurrent.futures as futures

try:
    import fcntl  # POSIX: cross-process mutual exclusion for .rig/queue.json
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

from . import config
from . import dependencies as deps
from .providers import _build_prompt, run_provider

# ── Task queue (stack up, then GO; tracker integration) ──────────────────────
# Holds "stack tasks -> GO in one batch" in a local json file or an external tracker
# (GitHub/GitLab Issues). Backends are swappable: local (.rig/queue.json) / github
# (gh CLI) / gitlab (glab CLI).
# With Issue integration, state is tracked via labels: rig-queue -> rig-running -> rig-done / rig-failed.
QUEUE_LABEL = "rig-queue"
# The "active" labels queue list should surface (rig-done is excluded: already closed; #211).
QUEUE_LABELS_ACTIVE = ["rig-queue", "rig-running", "rig-failed"]
# All state labels the queue manages (used to compute which old labels to remove; #223).
QUEUE_LABELS_ALL = ["rig-queue", "rig-running", "rig-failed", "rig-done"]
QUEUE_PATH = config.INVOCATION_CWD / ".rig" / "queue.json"
# Statuses an item can be re-resolved out of. `running` is excluded on purpose: a live
# provider owns it, and rewriting its status from under that process is the lost-update
# class of bug this file already carries a lock for.
RESOLVABLE = ("queued", "waiting", "blocked")


def _runs_dir():
    """Where the workbench keeps its task records.

    Derived from `QUEUE_PATH` rather than from a fresh repo-root lookup, so the queue and
    the tasks it depends on are always read out of the same `.rig/` — including under the
    tests that rebind `QUEUE_PATH` to a scratch directory.
    """
    return QUEUE_PATH.parent / "runs"


def _gh_cli(backend: str) -> str:
    return {"github": "gh", "gitlab": "glab"}[backend]


def _cli_run(argv: list[str]) -> tuple[int, str, str]:
    """Run gh/glab as a subprocess. Returns (127, "", err) instead of crashing when the CLI is absent."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found (CLI not installed)"


class QueueCorrupt(RuntimeError):
    """`.rig/queue.json` exists but cannot be parsed.

    Raised instead of degrading to an empty queue: the old behaviour swallowed every
    exception and returned `{"items": []}`, so the very next `_local_save` persisted that
    empty queue and **destroyed the whole backlog** (#360). A queue we cannot read is a
    stop-and-tell condition, never a silent reset.
    """


# Guards the read-modify-write of .rig/queue.json (#360). `queue go` mutates the store from
# `--max-parallel` threads (default 3, so this is on by default), and every mutation was an
# unlocked load -> modify -> save: concurrent threads clobbered each other's writes, leaving
# items stuck at running/queued after GO reported them done (a stuck `queued` item is then
# re-executed by the next GO). Same defect class already fixed for the trust store in
# recipes.py `_record_trust`. Two layers are needed:
#   - threading.Lock  -> `queue go`'s ThreadPoolExecutor (in-process threads)
#   - fcntl.flock     -> separate processes (a `queue add`/`queue list` in another terminal
#                        while GO runs, and the rig/claude providers' own subprocesses)
_QUEUE_WRITE_LOCK = threading.Lock()


@contextlib.contextmanager
def _queue_locked():
    """Serialize one whole read-modify-write cycle on `.rig/queue.json`.

    The flock is **blocking** (unlike `workbench.state.task_lock`, which dies non-blocking):
    queue mutations are short, and waiting is always better than dropping a status update.
    Without fcntl (Windows) only the in-process lock applies — that still covers `queue go`,
    which is where the parallelism actually comes from.
    """
    with _QUEUE_WRITE_LOCK:
        if fcntl is None:
            yield
            return
        QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_file = QUEUE_PATH.with_name(QUEUE_PATH.name + ".lock")
        with lock_file.open("a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass


def _local_load() -> dict:
    """Read the queue store. Missing file = a fresh queue; unparseable file = QueueCorrupt.

    Also normalizes a hand-edited store: a missing `items` becomes `[]`, and a missing or
    stale `next_id` is recomputed as max(id)+1 so ids stay unique instead of raising KeyError
    or handing out a duplicate.
    """
    if not QUEUE_PATH.exists():
        return {"items": [], "next_id": 1}
    try:
        raw = QUEUE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        raise QueueCorrupt(f"cannot read {QUEUE_PATH}: {e}") from e
    try:
        q = json.loads(raw)
    except ValueError as e:
        raise QueueCorrupt(f"{QUEUE_PATH} is not valid JSON: {e}") from e
    if not isinstance(q, dict):
        raise QueueCorrupt(f"{QUEUE_PATH} must contain a JSON object, got {type(q).__name__}")
    items = q.get("items")
    q["items"] = items if isinstance(items, list) else []
    numeric = [it["id"] for it in q["items"]
               if isinstance(it, dict) and isinstance(it.get("id"), int)]
    least_free = max(numeric) + 1 if numeric else 1
    next_id = q.get("next_id")
    q["next_id"] = next_id if isinstance(next_id, int) and next_id >= least_free else least_free
    return q


def _local_save(q: dict) -> None:
    """Write the queue store atomically (tmp + os.replace).

    A plain `write_text` truncates before writing, so a reader could observe a half-written
    file — which under the old `except Exception: return empty` load meant "queue vanished".
    """
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_name(QUEUE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, QUEUE_PATH)


def queue_add(backend: str, task: str, cfg: dict, depends_on=None,
              dependency_policy: str | None = None) -> dict:
    """Stack one task. `depends_on` makes it wait for another item's *acceptance* (#427).

    Dependencies are local-backend only. The github/gitlab backends carry state in issue
    labels, which cannot hold an edge list, so a `--depends-on` there is refused rather
    than silently dropped — dropping it would run the dependent immediately, which is the
    one outcome the flag exists to prevent.
    """
    declared = deps.normalise(depends_on)
    policy = dependency_policy or (deps.DEFAULT_POLICY if declared else None)
    if backend == "local":
        with _queue_locked():
            q = _local_load()
            if declared:
                deps.validate_new(q["items"], declared, policy)
            item = {"id": q["next_id"], "task": task, "status": "queued", "note": ""}
            if declared:
                item["depends_on"] = declared
                item["dependency_policy"] = policy
            q["items"].append(item)
            q["next_id"] += 1
            _local_save(q)
        return item
    if declared:
        raise deps.DependencyError(
            f"--depends-on needs the local queue backend; {backend} tracks state in issue "
            f"labels, which cannot hold a dependency edge")
    cli = _gh_cli(backend)
    argv = [cli, "issue", "create", "-t", task, "-l", QUEUE_LABEL, "-b", "rig queue task"]
    if cfg.get("repo"):
        argv += ["-R", cfg["repo"]]
    rc, out, err = _cli_run(argv)
    if rc != 0:
        return {"id": None, "task": task, "status": "error", "note": (err or out)[:200]}
    return {"id": out.strip().split("/")[-1] or "?", "task": task, "status": "queued"}


def queue_list(backend: str, cfg: dict) -> list[dict]:
    """Return every active item (queued/running/failed). done (already closed) is excluded.

    Label transitions (queue_set_status) drop the old label, so filtering by a single `-l`
    label makes items that moved to running/failed vanish from the listing (#211). Query each
    QUEUE_LABELS_ACTIVE label individually and dedup/merge by id (github) or by line (gitlab,
    text-only output).
    """
    if backend == "local":
        # done (equivalent to closed) is excluded (#215: github/gitlab exclude them naturally
        # via --state open; this fixes the asymmetry where local kept them in queue.json forever).
        return [it for it in _local_load()["items"] if it.get("status") != "done"]
    cli = _gh_cli(backend)
    R = (["-R", cfg["repo"]] if cfg.get("repo") else [])
    if backend == "github":
        seen: dict[object, dict] = {}
        for label in QUEUE_LABELS_ACTIVE:
            argv = [cli, "issue", "list", "-l", label, "--state", "open",
                    "--json", "number,title,labels,comments"] + R
            rc, out, err = _cli_run(argv)
            if rc != 0:
                return [{"id": None, "task": f"[{cli} error: {(err or '')[:120]}]", "status": "error"}]
            try:
                rows = json.loads(out or "[]")
            except Exception:
                rows = []
            for x in rows:
                labels = {lbl.get("name") for lbl in (x.get("labels") or [])}
                st = ("running" if "rig-running" in labels
                      else "failed" if "rig-failed" in labels
                      else "queued")
                # Use the latest comment (the failure reason / completion comment written by
                # queue_set_status) as the displayed note (#214: fixes queue list dropping notes).
                comments = x.get("comments") or []
                note = comments[-1].get("body", "") if comments else ""
                seen[x.get("number")] = {"id": x.get("number"), "task": x.get("title"),
                                          "status": st, "note": note}
        return list(seen.values())
    # gitlab (glab) only has text output, with no labels/comments, so query per label and
    # dedup/merge per line (status stays fixed at "queued" as before; #211 visibility recovery
    # is the main goal). Note display is unsupported on gitlab (same root cause as the existing
    # inability to fetch ids individually; #214).
    seen_lines: dict[str, dict] = {}
    for label in QUEUE_LABELS_ACTIVE:
        argv = [cli, "issue", "list", "-l", label, "--state", "open"] + R
        rc, out, err = _cli_run(argv)
        if rc != 0:
            return [{"id": None, "task": f"[{cli} error: {(err or '')[:120]}]", "status": "error"}]
        for ln in out.splitlines():
            if ln.strip():
                seen_lines[ln] = {"id": None, "task": ln, "status": "queued"}
    return list(seen_lines.values())


def _queue_relabel_args(status: str) -> list[str]:
    """gh/glab relabel arguments for the new status (`--add-label X --remove-label Y ...`).

    The removal targets are "every queue label other than the new one", not a fixed
    QUEUE_LABEL (#223: fixes the bug where transitions like running->failed/done left the old
    label behind because removal was hard-coded, so queue_list's label->status mapping kept
    returning the wrong state). Extracting this helper lets selftest verify the argv
    construction directly (without real CLI calls).
    """
    label = {"queued": "rig-queue", "running": "rig-running",
              "done": "rig-done", "failed": "rig-failed"}.get(status)
    if not label:
        return []
    args = ["--add-label", label]
    for old in QUEUE_LABELS_ALL:
        if old != label:
            args += ["--remove-label", old]
    return args


def queue_set_status(backend: str, item_id, status: str, note: str, cfg: dict,
                     task_id: str | None = None) -> bool:
    """Returns whether the item was found (local) / the update was attempted (gh).

    `task_id` links the item to the workbench task its provider registered. Before #427
    that link was computed by GO and then thrown away, which left the queue unable to say
    anything about whether an item's *result* had been accepted — the one question a
    dependency edge asks.
    """
    if backend == "local":
        with _queue_locked():
            q = _local_load()
            matched = False
            for it in q["items"]:
                if str(it["id"]) == str(item_id):
                    it["status"] = status
                    it["note"] = note[:300]
                    # Set when one is supplied; a transition that could not re-derive
                    # the id must not erase the one already recorded. Requeueing is the
                    # exception and the important one: `queue retry` says this item is
                    # going to produce a *different* result, so keeping the old link
                    # would let a dependent be released against work being replaced.
                    if task_id:
                        it["task_id"] = task_id
                    elif status == "queued":
                        it.pop("task_id", None)
                    matched = True
            if matched:
                _local_save(q)
        return matched
    cli = _gh_cli(backend)
    R = (["-R", cfg["repo"]] if cfg.get("repo") else [])
    relabel = _queue_relabel_args(status)
    if relabel:
        _cli_run([cli, "issue", "edit", str(item_id)] + relabel + R)
    if note:
        _cli_run([cli, "issue", "comment", str(item_id), "-b", note] + R)
    if status == "done":
        _cli_run([cli, "issue", "close", str(item_id)] + R)
    elif status == "queued":
        # retry (#213): reopen so that items already closed as done become active again
        # (already-open issues are a no-op, no crash; for the common case of retrying from
        # failed, i.e. not closed, this effectively does nothing).
        _cli_run([cli, "issue", "reopen", str(item_id)] + R)
    return True


def resolve_dependencies() -> tuple[list[dict], list[dict]]:
    """Re-evaluate every resolvable local item, persist the verdict, and return
    `(runnable, held)`.

    One locked read-modify-write, like every other mutation in this file. The verdict is
    written down rather than recomputed on demand for two reasons: it is what survives the
    restart the acceptance criteria ask about, and `mission_worker` loops while anything is
    `queued` — a dependent parked at `queued` would spin that worker several times a second
    with nothing to run.

    An item is never resolved out of `running`: a live provider owns it.
    """
    runs = _runs_dir()
    runnable, held = [], []
    with _queue_locked():
        q = _local_load()
        rows = q["items"]
        for it in rows:
            if not isinstance(it, dict) or it.get("status") not in RESOLVABLE:
                continue
            verdict = deps.resolve(it, rows, runs)
            if verdict["state"] == deps.READY:
                # Items held on a previous GO come back as `queued` so that the store
                # reads the same whether they waited or never had a dependency at all.
                it["status"] = "queued"
                if it.get("dependency_note"):
                    it.pop("dependency_note")
                runnable.append(it)
            else:
                it["status"] = verdict["state"]
                it["dependency_note"] = verdict["reason"][:300]
                held.append({"id": it["id"], "task": it.get("task"),
                             "state": verdict["state"], "reason": verdict["reason"]})
        _local_save(q)
    return runnable, held


def dependency_graph() -> dict:
    """The local queue's dependency graph, for Mission Control and any other client."""
    return deps.graph(_local_load()["items"], _runs_dir())


def queue_claim(backend: str, item_id, cfg: dict) -> bool:
    """Take ownership of a queued item, or report that someone else already has.

    GO has always marked an item `running` unconditionally at dispatch, which leaves a
    window where two `queue go` processes both read it as `queued` and both execute it.
    That predates dependencies, but dependencies raise the cost: the two runs produce two
    workbench tasks, only one of which ends up linked, so a dependent can be released
    against a result nobody kept.

    The compare-and-set closes the window to one lock acquisition without changing what
    happens when GO dies mid-batch — only the items actually claimed are left `running`,
    exactly as before.
    """
    if backend != "local":
        # Issue-label backends have no read-modify-write to make atomic, and rig is not
        # inventing a lock over someone else's tracker. Behaviour there is unchanged.
        queue_set_status(backend, item_id, "running", "", cfg)
        return True
    with _queue_locked():
        q = _local_load()
        for it in q["items"]:
            if str(it["id"]) == str(item_id):
                if it.get("status") != "queued":
                    return False
                it["status"] = "running"
                _local_save(q)
                return True
    return False


def _build_queue_task_prompt(task: str, provider: str) -> str:
    """Generation prompt that dispatches each queue item.

    The `rig`/`claude` providers run in parallel as **separate processes** of headless
    `claude -p` (`queue go --max-parallel N`). Multiple processes share the same working
    directory, so without routing through the workbench's isolated worktree (`/rig:rig`)
    there is a **risk of parallel tasks fighting over files**. Hence the rig/claude providers
    are explicitly instructed to run `/rig:rig "<task>"`, which automatically isolates each
    task in its own worktree.
    Accepting is not the queue's job (the user applies results individually via
    `/rig:rig board` -> `accept`).
    """
    if provider in ("rig", "claude"):
        return (
            "Invoke the `rig` skill via the Skill tool and execute the following task in an "
            "isolated worktree per `facets/instructions/workbench` (the `/rig:rig` unified entry). "
            "It runs in parallel with other queue items, so **never write to the main working tree** "
            "(do not accept; do triage, implementation, and the acceptance-gate judgment inside the "
            "isolated worktree, and leave applying to the user, who will list results with "
            "`/rig:rig board` after the queue finishes and `/rig:rig accept` them individually).\n"
            f'Run: /rig:rig "{task}"\n'
            "Once the gate is settled (one of passed/passed_with_warnings/failed), output "
            "'STATUS: done' at the end."
        )
    return _build_prompt({"recipe": "queue", "goal": task}, {"id": "task", "instruction": task}, None)


def _build_queue_verify_prompt(task: str, product: str) -> str:
    return (f"You are an independent verifier (a separate process and role from the agent that "
            f"generated this step). For the result of queue task \"{task}\", judge (1) whether it "
            f"meets the acceptance criteria, and (2) **whether it stayed entirely inside the "
            f"isolated worktree without directly modifying the main working tree** (no writes to "
            f"main before accept). End with exactly "
            f"'VERDICT: PASS' or 'VERDICT: FAIL'.\n--- product ---\n{product[:2000]}")


def cmd_queue(args):
    if not args or args[0] not in ("add", "list", "go", "done", "retry"):
        print("[ERROR] usage: queue <add|list|go|done|retry> [...] "
              "[--backend local|github|gitlab] [--repo owner/repo]")
        sys.exit(1)
    sub, rest = args[0], args[1:]
    backend, cfg = "local", {}
    gen, ver, max_parallel = "rig", None, 3
    free = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--backend" and i + 1 < len(rest):
            backend = rest[i + 1]
            i += 2
        elif a == "--repo" and i + 1 < len(rest):
            cfg["repo"] = rest[i + 1]
            i += 2
        elif a == "--provider" and i + 1 < len(rest):
            gen = rest[i + 1]
            i += 2
        elif a == "--verifier-provider" and i + 1 < len(rest):
            ver = rest[i + 1]
            i += 2
        elif a == "--max-parallel" and i + 1 < len(rest):
            max_parallel = int(rest[i + 1])
            i += 2
        elif a == "--provider-cmd" and i + 1 < len(rest):
            cfg["provider_cmd"] = rest[i + 1]
            i += 2
        elif a == "--depends-on" and i + 1 < len(rest):
            cfg.setdefault("depends_on", []).append(rest[i + 1])
            i += 2
        elif a == "--dependency-policy" and i + 1 < len(rest):
            cfg["dependency_policy"] = rest[i + 1]
            i += 2
        else:
            free.append(a)
            i += 1
    ver = ver or gen

    try:
        return _cmd_queue_dispatch(sub, free, backend, cfg, gen, ver, max_parallel)
    except deps.DependencyError as e:
        # A refusal, not a crash: the declaration was rejected and nothing was stored.
        print(f"[ERROR] {e}")
        sys.exit(1)
    except QueueCorrupt as e:
        # Never "recover" by rewriting an empty store — that is how a backlog disappears (#360).
        print(f"[ERROR] {e}")
        print("        rig refuses to touch an unreadable queue store (rewriting it empty would "
              "lose the backlog). Repair or move the file, then retry.")
        sys.exit(1)


def _cmd_queue_dispatch(sub, free, backend, cfg, gen, ver, max_parallel):
    if sub == "add":
        if not free:
            print("[ERROR] queue add \"<task>\"")
            sys.exit(1)
        it = queue_add(backend, " ".join(free), cfg, cfg.get("depends_on"),
                       cfg.get("dependency_policy"))
        print(f"queued [{backend}]: #{it['id']} {it['task']}  ({it['status']})"
              + (f" — {it.get('note','')}" if it.get("status") == "error" else ""))
        if it.get("depends_on"):
            print(f"  depends on #{', #'.join(it['depends_on'])} "
                  f"(policy: {it['dependency_policy']} — each must be **accepted**, not "
                  f"merely finished; accepting is a person's action)")
        return
    if sub == "list":
        items = queue_list(backend, cfg)
        print(f"## rig queue [{backend}]  ({len(items)} items)")
        for it in items:
            line = f"  [{it.get('status','?'):<8}] #{it.get('id')}  {it.get('task')}"
            note = it.get("note")
            if note:
                line += f" — {note}"
            print(line)
            if it.get("depends_on"):
                print(f"      depends on #{', #'.join(str(d) for d in it['depends_on'])}"
                      f"  (policy: {it.get('dependency_policy')})")
            if it.get("dependency_note"):
                print(f"      {it['dependency_note']}")
        return
    if sub == "done":
        if not free:
            print("[ERROR] queue done <id>")
            sys.exit(1)
        queue_set_status(backend, free[0], "done", "manually marked done", cfg)
        print(f"done [{backend}]: #{free[0]}")
        return
    if sub == "retry":
        if not free:
            print("[ERROR] queue retry <id>")
            sys.exit(1)
        queue_set_status(backend, free[0], "queued", "", cfg)
        print(f"retry [{backend}]: #{free[0]} → queued")
        return
    # go: run the stacked tasks in one batch (independent tasks in parallel; each task gated)
    #
    # Dependencies are resolved first and the verdict is persisted, so what follows is the
    # batch this file has always run. Only the local backend has edges to resolve.
    held: list[dict] = []
    if backend == "local":
        _, held = resolve_dependencies()
    items = [it for it in queue_list(backend, cfg) if it.get("status") == "queued"]
    if not items:
        if held:
            print(f"## rig queue GO [{backend}]  nothing runnable — "
                  f"{len(held)} item(s) held on dependencies\n")
            for line in _held_lines(held):
                print(line)
            return
        print(f"Queue is empty [{backend}]. Stack tasks with `queue add`.")
        return
    print(f"## rig queue GO [{backend}]  {len(items)} items / provider={gen} / parallel={max_parallel}"
          + (f" / {len(held)} held on dependencies" if held else "") + "\n")

    def _set_status(item_id, status: str, note: str = "", task_id: str = "") -> None:
        """Record a transition, and say so when it did not land (never fail silently; #360).

        The return value of queue_set_status used to be discarded, so a lost update was
        invisible: GO printed DONE while the store still said running/queued.
        """
        if not queue_set_status(backend, item_id, status, note, cfg, task_id or None):
            print(f"  [WARN] #{item_id}: could not record status '{status}' "
                  f"(item not found in the {backend} queue) — reconcile with "
                  f"`queue done {item_id}` or `queue retry {item_id}`")

    def _run_one(it):
        task = it["task"]
        task_id = ""
        if not queue_claim(backend, it["id"], cfg):
            # Another GO process took it between the listing and here.
            return {"id": it["id"], "task": task, "ok": False, "task_id": "",
                    "skipped": True}
        try:
            rc, out = run_provider(gen, "generator", _build_queue_task_prompt(task, gen), cfg)
            # The only trace linking this queue item to the workbench task it created:
            # registration happened inside the provider's own session (see
            # workbench.batch for why an unrecoverable id is reported, not guessed).
            # It is persisted onto the item, because a dependency edge asks whether this
            # item's *result* was accepted, and without the link there is nothing to ask.
            task_id = _find_task_id(out)
            rc2, vout = run_provider(ver, "verifier", _build_queue_verify_prompt(task, out), cfg, persona="queue")
            ok = ("VERDICT: PASS" in vout) and ("VERDICT: FAIL" not in vout)
            note = ("✅ rig: gate settled (needs /rig:rig board → accept)" if ok else "❌ rig: verification FAIL") + f" ({gen}→{ver})"
        except Exception as e:  # noqa: BLE001 - one bad item must not abandon the batch
            # ex.map propagates the first exception and discards the other results, which
            # left every remaining item pinned at `running` with no way to tell why (#360).
            ok, note = False, f"❌ rig: {type(e).__name__}: {e}"[:300]
        _set_status(it["id"], "done" if ok else "failed", note, task_id)
        return {"id": it["id"], "task": task, "ok": ok, "task_id": task_id}

    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        every = list(ex.map(_run_one, items))
    # An item another process claimed was not this batch's work and is not this batch's
    # failure. It is reported, and it is not counted.
    skipped = [r for r in every if r.get("skipped")]
    results = [r for r in every if not r.get("skipped")]
    for r in skipped:
        print(f"  [SKIP] #{r['id']}  {r['task']} — claimed by another `queue go`")
    done = sum(1 for r in results if r["ok"])
    for r in results:
        print(f"  [{'DONE' if r['ok'] else 'FAIL'}] #{r['id']}  {r['task']}")
    print(f"\n=== GO complete: {done}/{len(results)} done [{backend}] ==="
          + (f"  ({len(skipped)} claimed elsewhere)" if skipped else ""))
    # `done` counts settled gates, not finished work: every one of those tasks is still
    # sitting in its own worktree waiting for a person to accept or discard it. Say so.
    for line in _batch_lines(results):
        print(line)
    for line in _held_lines(held):
        print(line)
    # The exit code has always meant "did this batch's items succeed", and held items are
    # not this batch's items — they are work that correctly has not started. Reporting them
    # as a failure would make every dependency-using queue look broken to CI.
    sys.exit(0 if done == len(results) else 1)


def _held_lines(held: list[dict]) -> list[str]:
    """What did not start, and what would make it start.

    Printed as loudly as the accept reminder, because the state it describes is
    indistinguishable from "the queue silently skipped my task" if it is not said out
    loud. A dependent cannot clear inside this GO: the edge is acceptance, and accepting
    is a person's action.
    """
    if not held:
        return []
    lines = ["", f"=== {len(held)} item(s) held on dependencies ==="]
    for row in held:
        lines.append(f"  [{row['state']:<7}] #{row['id']}  {row['task']}")
        lines.append(f"            {row['reason']}")
    lines.append("  These do not run in this batch. Accept what they depend on "
                 "(`workbench.py board` → `accept`), then run GO again.")
    return lines


def _find_task_id(text: str) -> str:
    """Best-effort, and never a reason for GO to fail."""
    try:
        from ..workbench.batch import find_task_id
        return find_task_id(text)
    except Exception:  # noqa: BLE001
        return ""


def _batch_lines(results: list[dict]) -> list[str]:
    """The regrouped "what you must do next" block, or nothing.

    Imported lazily and wrapped: the batch already ran, and a rendering problem in the
    summary must not turn a completed GO into a traceback.
    """
    try:
        from ..workbench.batch import group_batch, render_batch
        from ..workbench.state import maybe_repo_root

        root = maybe_repo_root()
        if root is None:
            return []
        return render_batch(group_batch(root, results))
    except Exception:  # noqa: BLE001
        return []


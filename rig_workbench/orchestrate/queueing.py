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


def queue_add(backend: str, task: str, cfg: dict) -> dict:
    if backend == "local":
        with _queue_locked():
            q = _local_load()
            item = {"id": q["next_id"], "task": task, "status": "queued", "note": ""}
            q["items"].append(item)
            q["next_id"] += 1
            _local_save(q)
        return item
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


def queue_set_status(backend: str, item_id, status: str, note: str, cfg: dict) -> bool:
    """Returns whether the item was found (local) / the update was attempted (gh)."""
    if backend == "local":
        with _queue_locked():
            q = _local_load()
            matched = False
            for it in q["items"]:
                if str(it["id"]) == str(item_id):
                    it["status"] = status
                    it["note"] = note[:300]
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
        else:
            free.append(a)
            i += 1
    ver = ver or gen

    try:
        return _cmd_queue_dispatch(sub, free, backend, cfg, gen, ver, max_parallel)
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
        it = queue_add(backend, " ".join(free), cfg)
        print(f"queued [{backend}]: #{it['id']} {it['task']}  ({it['status']})"
              + (f" — {it.get('note','')}" if it.get("status") == "error" else ""))
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
    items = [it for it in queue_list(backend, cfg) if it.get("status") == "queued"]
    if not items:
        print(f"Queue is empty [{backend}]. Stack tasks with `queue add`.")
        return
    print(f"## rig queue GO [{backend}]  {len(items)} items / provider={gen} / parallel={max_parallel}\n")

    def _set_status(item_id, status: str, note: str = "") -> None:
        """Record a transition, and say so when it did not land (never fail silently; #360).

        The return value of queue_set_status used to be discarded, so a lost update was
        invisible: GO printed DONE while the store still said running/queued.
        """
        if not queue_set_status(backend, item_id, status, note, cfg):
            print(f"  [WARN] #{item_id}: could not record status '{status}' "
                  f"(item not found in the {backend} queue) — reconcile with "
                  f"`queue done {item_id}` or `queue retry {item_id}`")

    def _run_one(it):
        task = it["task"]
        _set_status(it["id"], "running")
        try:
            rc, out = run_provider(gen, "generator", _build_queue_task_prompt(task, gen), cfg)
            rc2, vout = run_provider(ver, "verifier", _build_queue_verify_prompt(task, out), cfg, persona="queue")
            ok = ("VERDICT: PASS" in vout) and ("VERDICT: FAIL" not in vout)
            note = ("✅ rig: gate settled (needs /rig:rig board → accept)" if ok else "❌ rig: verification FAIL") + f" ({gen}→{ver})"
        except Exception as e:  # noqa: BLE001 - one bad item must not abandon the batch
            # ex.map propagates the first exception and discards the other results, which
            # left every remaining item pinned at `running` with no way to tell why (#360).
            ok, note = False, f"❌ rig: {type(e).__name__}: {e}"[:300]
        _set_status(it["id"], "done" if ok else "failed", note)
        return (it, ok)

    with futures.ThreadPoolExecutor(max_workers=max(1, max_parallel)) as ex:
        results = list(ex.map(_run_one, items))
    done = sum(1 for _, ok in results if ok)
    for it, ok in results:
        print(f"  [{'DONE' if ok else 'FAIL'}] #{it['id']}  {it['task']}")
    print(f"\n=== GO complete: {done}/{len(results)} done [{backend}] ===")
    sys.exit(0 if done == len(results) else 1)


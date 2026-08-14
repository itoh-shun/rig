"""workbench instincts: continuous cross-session instinct-learning layer (issue #306).

Lightweight, low-confidence, unverified pattern accumulation — "this project
tends to be written this way", "searching here is faster" — completely
separate from `facets/knowledge` (rig's verified-knowledge wiki). What's
stored here are confidence-scored hunches, never to be confused with the
knowledge layer.

Actual pattern extraction (what's worth learning from a diff/session) is the
model's own judgment call — the Stop hook (hooks/suggest-instincts.sh) only
reminds the model to consider proposing one. What this module handles
deterministically is storage, decay, conflict resolution, and injection
selection.

State lives in two tiers, one JSON object per line in each:

  project  `<repo>/.rig/instincts.jsonl`   facts about *this codebase*
  host     `~/.rig/instincts.jsonl`        facts about the harness/host itself

Recording always writes the project tier. A record only reaches the host tier by
being promoted one at a time (`--promote <id>`), never by merging one repo's store
into the other — most instincts are repo-specific (a migration version collision,
a class name, a config file), and injection is capped at
`_INSTINCT_INJECT_CHAR_LIMIT` characters, so wholesale merging would evict the
relevant records to make room for irrelevant ones. What generalizes is the
narrow class of facts that are about the tooling rather than the code ("subagents
sometimes return an idle notification instead of their result", "this machine has
no jq"), and a human decides which those are.

Reads that only display or select (listing, injection) see both tiers with the
project tier winning ties; every write path stays confined to a single tier.

Record shape:
  id / text / evidence / source_task_ids / confidence / first_seen /
  last_seen / hit_count / decay_reason / status (active/muted/expired) /
  supersedes / promoted_at (host tier only)
"""

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys

from .secrets import PATTERNS as _SECRET_PATTERNS
from .state import load_task, now_iso, repo_root

INSTINCTS_PATH_NAME = "instincts.jsonl"
TIER_PROJECT = "project"
TIER_HOST = "host"
_INSTINCT_CONFIDENCE_THRESHOLD = 0.7   # below this, never selected for the next session's injection
_INSTINCT_INJECT_CHAR_LIMIT = 500      # keeps context-minimal intact
_INSTINCT_DECAY_DAYS = 30              # unrefreshed last_seen past this many days triggers decay
_INSTINCT_DECAY_AMOUNT = 0.1
_INSTINCT_EXPIRE_FLOOR = 0.2           # below this after decay, status becomes expired
_INSTINCT_TEXT_CHAR_LIMIT = 300

# Local absolute paths and ENV_VAR=value-shaped assignments are excluded on top of
# secrets.py's named credential patterns — neither is a secret per se, but both are
# machine/session-specific noise that shouldn't be learned as a durable project pattern.
_INSTINCT_LOCAL_PATH_RE = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+")
_INSTINCT_ENV_ASSIGN_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\s*=\s*\S+")


def _instinct_is_learnable(text: str) -> tuple[bool, str]:
    """Reject candidates that contain secrets, local paths, or environment-specific
    assignments (the learning-forbidden filter)."""
    for kind, rx in _SECRET_PATTERNS:
        if rx.search(text):
            return False, f"looks like it contains a secret ({kind}); rejected"
    if _INSTINCT_LOCAL_PATH_RE.search(text):
        return False, "contains a local absolute path; rejected (machine-specific, not a durable project pattern)"
    if _INSTINCT_ENV_ASSIGN_RE.search(text):
        return False, "looks like an ENV_VAR=value assignment; rejected (environment-specific, not durable)"
    if len(text) > _INSTINCT_TEXT_CHAR_LIMIT:
        return False, f"exceeds {_INSTINCT_TEXT_CHAR_LIMIT} chars; too large a candidate (summarize and resubmit)"
    return True, ""


def _instincts_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / INSTINCTS_PATH_NAME


def _host_instincts_path() -> pathlib.Path:
    """The host tier lives beside the other cross-project rig state in `~/.rig/`.

    `RIG_USER_HOME` redirects it, the same override `packs/resolver.py` uses for the
    user tier — without it a test that never touches the host tier would still read
    (and, once a record is selected for injection, write) the developer's real store.
    """
    home = pathlib.Path(os.environ.get("RIG_USER_HOME") or pathlib.Path.home()).expanduser()
    return home / ".rig" / INSTINCTS_PATH_NAME


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")


def load_instincts(root: pathlib.Path) -> list[dict]:
    return _read_jsonl(_instincts_path(root))


def save_instincts(root: pathlib.Path, instincts: list[dict]) -> None:
    _write_jsonl(_instincts_path(root), instincts)


def load_host_instincts() -> list[dict]:
    return _read_jsonl(_host_instincts_path())


def save_host_instincts(instincts: list[dict]) -> None:
    _write_jsonl(_host_instincts_path(), instincts)


def _load_tiered(root: pathlib.Path) -> tuple[list[tuple[str, dict]], list[dict], list[dict]]:
    """Return `(merged, project, host)` for the read-only paths.

    `merged` pairs each record with its tier, project first. A host record whose id
    already exists in the project tier is left out of `merged` — the repo's own copy
    is the more specific one — but is still present in `host`, so callers that write
    a tier back write the whole file and never drop a shadowed record.

    The backing lists hold the same objects as `merged`, so mutating a record through
    `merged` (bumping `hit_count`, refreshing `last_seen`) and then saving the tier
    persists that mutation.
    """
    project = load_instincts(root)
    host = load_host_instincts()
    project_ids = {r["id"] for r in project}
    merged = [(TIER_PROJECT, r) for r in project]
    merged += [(TIER_HOST, r) for r in host if r["id"] not in project_ids]
    return merged, project, host


def add_instinct(root: pathlib.Path, text: str, evidence: str, task_id: str | None,
                 confidence: float, supersedes: str | None = None) -> dict:
    """Record a new instinct candidate. Raises ValueError when it hits the
    learning-forbidden filter (the CLI caller surfaces the reason verbatim to the
    user). Passing `supersedes` explicitly mutes that existing id — recognizing
    that two instincts contradict is a judgment call, so the model declares it
    rather than this code inferring semantic conflicts on its own.
    """
    ok, reason = _instinct_is_learnable(text)
    if not ok:
        raise ValueError(reason)
    instincts = load_instincts(root)
    now = now_iso()
    rec = {
        "id": f"in-{hashlib.sha256((text + now).encode()).hexdigest()[:10]}",
        "text": text, "evidence": evidence,
        "source_task_ids": [task_id] if task_id else [],
        "confidence": max(0.0, min(1.0, confidence)),
        "first_seen": now, "last_seen": now, "hit_count": 1,
        "decay_reason": None, "status": "active", "supersedes": [supersedes] if supersedes else [],
    }
    if supersedes:
        for other in instincts:
            if other["id"] == supersedes and other["status"] == "active":
                other["status"] = "muted"
                other["decay_reason"] = f"superseded by {rec['id']} (explicit)"
    instincts.append(rec)
    save_instincts(root, instincts)
    return rec


def decay_instincts(root: pathlib.Path, now: datetime.datetime | None = None) -> int:
    """Lower the confidence of any active instinct whose `last_seen` hasn't been
    refreshed in `_INSTINCT_DECAY_DAYS` days or more. Drops it to status=expired
    once confidence falls below the floor. Implicit knowledge rots by design
    rather than accumulating forever. Both tiers decay under the same rules —
    promotion buys a wider audience, not immunity from rotting. Returns the total
    count of instincts changed."""
    now = now or datetime.datetime.now().astimezone()
    project = load_instincts(root)
    host = load_host_instincts()
    project_changed = _decay_records(project, now)
    host_changed = _decay_records(host, now)
    if project_changed:
        save_instincts(root, project)
    if host_changed:
        save_host_instincts(host)
    return project_changed + host_changed


def _decay_records(records: list[dict], now: datetime.datetime) -> int:
    changed = 0
    for rec in records:
        if rec["status"] != "active":
            continue
        last_seen = datetime.datetime.fromisoformat(rec["last_seen"])
        age_days = (now - last_seen).days
        if age_days >= _INSTINCT_DECAY_DAYS:
            rec["confidence"] = round(max(0.0, rec["confidence"] - _INSTINCT_DECAY_AMOUNT), 3)
            changed += 1
            if rec["confidence"] < _INSTINCT_EXPIRE_FLOOR:
                rec["status"] = "expired"
                rec["decay_reason"] = f"unused for {age_days} days; confidence decayed below {_INSTINCT_EXPIRE_FLOOR}"
    return changed


def select_for_injection(root: pathlib.Path, task_id: str | None = None) -> tuple[list, int]:
    """Choose which instincts to inject at the next session start (pure selection
    logic, deterministic).

    Walks active instincts from both tiers in (confidence desc, project-before-host,
    id asc) order — deterministic — and picks as many as fit within
    `_INSTINCT_INJECT_CHAR_LIMIT` characters (context-minimal). The tier only breaks
    ties: a promoted host instinct still has to out-score a project one to take its
    place, which is why promotion is deliberate and per-record rather than a merge.
    Bumps `hit_count` and refreshes `last_seen` on every selected record (being
    injected counts as being used, feeding back into the next decay evaluation), then
    saves back only the tiers that actually changed. `task_id` is accepted so a caller
    can log which instincts were injected into which session, if it chooses to.
    """
    merged, project, host = _load_tiered(root)
    candidates = sorted(
        ((tier, r) for tier, r in merged
         if r["status"] == "active" and r["confidence"] >= _INSTINCT_CONFIDENCE_THRESHOLD),
        key=lambda tr: (-tr[1]["confidence"], 0 if tr[0] == TIER_PROJECT else 1, tr[1]["id"]),
    )
    selected, total_chars = [], 0
    now = now_iso()
    touched: set[str] = set()
    for tier, rec in candidates:
        if total_chars + len(rec["text"]) > _INSTINCT_INJECT_CHAR_LIMIT:
            continue
        selected.append(rec)
        total_chars += len(rec["text"])
        rec["hit_count"] += 1
        rec["last_seen"] = now
        touched.add(tier)
    if TIER_PROJECT in touched:
        save_instincts(root, project)
    if TIER_HOST in touched:
        save_host_instincts(host)
    return selected, total_chars


def promote_instinct(root: pathlib.Path, target_id: str) -> dict:
    """Move one instinct from the project tier to the host tier.

    Deliberately one record at a time. Most instincts describe a codebase and would
    be noise everywhere else; the ones worth promoting describe the harness or the
    machine. Deciding which is which is a judgment call, so a human names the id
    rather than this code guessing from the text.

    The host tier is written before the project tier is rewritten: if the second
    write fails the record exists in both places (visible, correctable) instead of
    in neither (lost).
    """
    project = load_instincts(root)
    rec = next((r for r in project if r["id"] == target_id), None)
    if rec is None:
        raise KeyError(f"instinct '{target_id}' not found in the project tier")
    host = load_host_instincts()
    if any(r["id"] == target_id for r in host):
        raise ValueError(f"instinct '{target_id}' is already in the host tier")
    promoted = dict(rec, promoted_at=now_iso())
    host.append(promoted)
    save_host_instincts(host)
    save_instincts(root, [r for r in project if r["id"] != target_id])
    return promoted


def demote_instinct(root: pathlib.Path, target_id: str) -> dict:
    """Move one instinct back from the host tier into this repo's project tier —
    the inverse of `promote_instinct`, so a wrong promotion is not a one-way door."""
    host = load_host_instincts()
    rec = next((r for r in host if r["id"] == target_id), None)
    if rec is None:
        raise KeyError(f"instinct '{target_id}' not found in the host tier")
    project = load_instincts(root)
    if any(r["id"] == target_id for r in project):
        raise ValueError(f"instinct '{target_id}' is already in this repo's project tier")
    demoted = {k: v for k, v in rec.items() if k != "promoted_at"}
    project.append(demoted)
    save_instincts(root, project)
    save_host_instincts([r for r in host if r["id"] != target_id])
    return demoted


def cmd_instincts(args: argparse.Namespace) -> None:
    root = repo_root()
    if args.add:
        if args.task_id:
            load_task(root, args.task_id)  # raises if the task_id doesn't exist (fail loud, not silently record garbage)
        try:
            rec = add_instinct(root, args.add, args.evidence or "", args.task_id,
                               args.confidence, args.supersedes)
        except ValueError as e:
            print(f"[ERROR] instinct candidate rejected: {e}")
            sys.exit(1)
        print(f"instinct recorded: {rec['id']} (confidence={rec['confidence']})"
              + (f". Muted {args.supersedes}" if args.supersedes else ""))
        return
    if args.promote or args.demote:
        move, target_id = ((promote_instinct, args.promote) if args.promote
                           else (demote_instinct, args.demote))
        try:
            rec = move(root, target_id)
        except (KeyError, ValueError) as e:
            print(f"[ERROR] {e.args[0] if e.args else e}")
            sys.exit(1)
        if args.promote:
            print(f"{rec['id']} promoted to the host tier ({_host_instincts_path()}). "
                  "It is now injected in every repo, competing on confidence for the "
                  f"{_INSTINCT_INJECT_CHAR_LIMIT}-char budget. Undo: instincts --demote {rec['id']}")
        else:
            print(f"{rec['id']} demoted back to this repo's project tier "
                  f"({_instincts_path(root)}).")
        return
    if args.mute or args.expire:
        target_id, new_status = (args.mute, "muted") if args.mute else (args.expire, "expired")
        # Muting has to reach whichever tier holds the record, or a promoted instinct
        # would be un-silenceable from the repo that is being bothered by it.
        instincts, save = load_instincts(root), lambda recs: save_instincts(root, recs)
        found = next((r for r in instincts if r["id"] == target_id), None)
        if not found:
            instincts, save = load_host_instincts(), save_host_instincts
            found = next((r for r in instincts if r["id"] == target_id), None)
        if not found:
            print(f"[ERROR] instinct '{target_id}' not found")
            sys.exit(1)
        found["status"] = new_status
        found["decay_reason"] = f"manually set to {new_status}"
        save(instincts)
        print(f"{target_id} set to {new_status}.")
        return
    if args.decay:
        n = decay_instincts(root)
        print(f"Decayed {n} instinct(s) ({_INSTINCT_DECAY_DAYS}+ days without a last_seen refresh).")
        return
    if args.inject_preview:
        selected, total_chars = select_for_injection(root, args.task_id)
        if args.json:
            print(json.dumps({"selected": selected, "total_chars": total_chars}, ensure_ascii=False))
            return
        if not selected:
            print("No instincts qualify for injection (below the confidence threshold, or none recorded).")
            return
        print(f"## Instincts to be injected next session ({len(selected)}; {total_chars}/{_INSTINCT_INJECT_CHAR_LIMIT} chars)\n")
        for rec in selected:
            print(f"- [{rec['confidence']}] {rec['text']}")
        return
    # default: list everything (/rig:rig instincts)
    merged, project, host = _load_tiered(root)
    if not merged:
        print("No instincts recorded.")
        return
    host_count = sum(1 for tier, _ in merged if tier == TIER_HOST)
    scope = f"{len(project)} project" + (f" + {host_count} host" if host_count else "")
    print(f"## rig instincts ({len(merged)}; {scope}; unverified patterns, separate from facets/knowledge)\n")
    for tier, rec in sorted(merged, key=lambda tr: -tr[1]["confidence"]):
        mark = {"active": "●", "muted": "○", "expired": "×"}.get(rec["status"], "?")
        inject = " -> next injection" if rec["status"] == "active" and rec["confidence"] >= _INSTINCT_CONFIDENCE_THRESHOLD else ""
        where = " [host]" if tier == TIER_HOST else ""
        print(f"{mark} [{rec['id']}]{where} confidence={rec['confidence']} hit={rec['hit_count']}{inject}")
        print(f"    {rec['text']}")
        if rec.get("evidence"):
            print(f"    evidence: {rec['evidence']}")
    print("\nDiscard: workbench.py instincts --mute <id>  /  run decay: workbench.py instincts --decay")
    print("Applies everywhere, not just here? workbench.py instincts --promote <id>")

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

State lives at `<repo>/.rig/instincts.jsonl`, one JSON object per line:
  id / text / evidence / source_task_ids / confidence / first_seen /
  last_seen / hit_count / decay_reason / status (active/muted/expired) /
  supersedes
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys

from .secrets import PATTERNS as _SECRET_PATTERNS
from .state import load_task, now_iso, repo_root

INSTINCTS_PATH_NAME = "instincts.jsonl"
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


def load_instincts(root: pathlib.Path) -> list[dict]:
    path = _instincts_path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def save_instincts(root: pathlib.Path, instincts: list[dict]) -> None:
    path = _instincts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in instincts), encoding="utf-8")


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
    rather than accumulating forever. Returns the count of instincts changed."""
    now = now or datetime.datetime.now().astimezone()
    instincts = load_instincts(root)
    changed = 0
    for rec in instincts:
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
    if changed:
        save_instincts(root, instincts)
    return changed


def select_for_injection(root: pathlib.Path, task_id: str | None = None) -> tuple[list, int]:
    """Choose which instincts to inject at the next session start (pure selection
    logic, deterministic).

    Walks active instincts in (confidence desc, id asc) order — deterministic —
    and picks as many as fit within `_INSTINCT_INJECT_CHAR_LIMIT` characters
    (context-minimal). Bumps `hit_count` and refreshes `last_seen` on every
    selected record (being injected counts as being used, feeding back into the
    next decay evaluation). `task_id` is accepted so a caller can log which
    instincts were injected into which session, if it chooses to.
    """
    instincts = load_instincts(root)
    candidates = sorted(
        (r for r in instincts if r["status"] == "active" and r["confidence"] >= _INSTINCT_CONFIDENCE_THRESHOLD),
        key=lambda r: (-r["confidence"], r["id"]),
    )
    selected, total_chars = [], 0
    now = now_iso()
    for rec in candidates:
        if total_chars + len(rec["text"]) > _INSTINCT_INJECT_CHAR_LIMIT:
            continue
        selected.append(rec)
        total_chars += len(rec["text"])
        rec["hit_count"] += 1
        rec["last_seen"] = now
    if selected:
        save_instincts(root, instincts)
    return selected, total_chars


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
    if args.mute or args.expire:
        target_id, new_status = (args.mute, "muted") if args.mute else (args.expire, "expired")
        instincts = load_instincts(root)
        found = next((r for r in instincts if r["id"] == target_id), None)
        if not found:
            print(f"[ERROR] instinct '{target_id}' not found")
            sys.exit(1)
        found["status"] = new_status
        found["decay_reason"] = f"manually set to {new_status}"
        save_instincts(root, instincts)
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
    instincts = load_instincts(root)
    if not instincts:
        print("No instincts recorded.")
        return
    print(f"## rig instincts ({len(instincts)}; unverified patterns, separate from facets/knowledge)\n")
    for rec in sorted(instincts, key=lambda r: -r["confidence"]):
        mark = {"active": "●", "muted": "○", "expired": "×"}.get(rec["status"], "?")
        inject = " -> next injection" if rec["status"] == "active" and rec["confidence"] >= _INSTINCT_CONFIDENCE_THRESHOLD else ""
        print(f"{mark} [{rec['id']}] confidence={rec['confidence']} hit={rec['hit_count']}{inject}")
        print(f"    {rec['text']}")
        if rec.get("evidence"):
            print(f"    evidence: {rec['evidence']}")
    print("\nDiscard: workbench.py instincts --mute <id>  /  run decay: workbench.py instincts --decay")

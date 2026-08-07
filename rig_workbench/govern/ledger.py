"""govern.ledger — the tamper-evident audit trail.

v1's `.rig/audit.jsonl` recorded forced accepts as plain appended JSON lines.
Honest, and trivially editable: delete the line about last Friday's `--force`
and the record simply says it never happened. For one developer keeping notes
that is fine. For an org that has to answer "show me every override in Q3" it
is not evidence.

The ledger is the same append-only file with two additions:

  * **hash chain** — every entry carries `prev`, the hash of the entry before
    it, so removing or editing any entry breaks every hash after it. `verify`
    reports the first break and its sequence number.
  * **signature** — each entry's hash is HMAC-SHA256'd with the repository's
    `.rig/provenance.key` (the key workbench.state already creates and keeps out
    of git). Same-machine tamper evidence: someone who can edit the ledger but
    not read the key cannot forge a consistent chain.

Deliberately HMAC and not Ed25519/SLSA, for the reason `state.sign_provenance`
already gives: this file must stay standard-library-only. The ledger proves the
record was not edited after the fact on a machine holding the key; it is not
third-party public attestation.

`.rig/audit.jsonl` keeps being written in its v1 shape so `workbench audit`,
`digest` and every existing reader keep working unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import os
import pathlib

LEDGER_REL = ".rig/ledger.jsonl"
GENESIS = "0" * 64


def ledger_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "ledger.jsonl"


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical(entry: dict) -> bytes:
    """Bytes that the hash covers: every field except the hash and signature."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()


def _key(root: pathlib.Path) -> bytes | None:
    """The signing key, if this repository has one. Never creates it here —
    signing is opportunistic, and a read-only checkout must still be able to
    append (an unsigned entry is still chained)."""
    p = root / ".rig" / "provenance.key"
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def _sign(root: pathlib.Path, digest: str) -> str | None:
    key = _key(root)
    if key is None:
        return None
    return hmac.new(key, digest.encode("ascii"), hashlib.sha256).hexdigest()


def read_ledger(root: pathlib.Path) -> list[dict]:
    p = ledger_path(root)
    if not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_malformed": line})
    return out


def last_entry(root: pathlib.Path) -> dict | None:
    entries = read_ledger(root)
    return entries[-1] if entries else None


def append(root: pathlib.Path, action: str, *, actor: str, subject: str = "",
           org: str | None = None, team: str | None = None,
           data: dict | None = None) -> dict:
    """Append one governance event and return it.

    Never raises: an audit trail that can break the operation it is recording
    would get switched off within a week. A failure to write is visible as a
    gap, which `verify` reports.
    """
    prev_entries = read_ledger(root)
    prev = prev_entries[-1].get("hash", GENESIS) if prev_entries else GENESIS
    entry = {
        "seq": len(prev_entries),
        "ts": _now(),
        "actor": actor,
        "action": action,
        "subject": subject,
        "org": org,
        "team": team,
        "data": data or {},
        "invoker": os.environ.get("RIG_INVOKER") or "direct",
        "prev": prev,
    }
    digest = entry_hash(entry)
    entry["hash"] = digest
    sig = _sign(root, digest)
    if sig:
        entry["sig"] = sig
    try:
        p = ledger_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass
    return entry


@dataclasses.dataclass
class VerifyResult:
    ok: bool
    entries: int
    signed: int
    problems: list[str]

    def summary(self) -> str:
        if self.ok:
            signed = f", {self.signed} signed" if self.signed else ", unsigned"
            return f"ledger intact — {self.entries} entries{signed}"
        return f"ledger BROKEN — {len(self.problems)} problem(s) over {self.entries} entries"


def verify(root: pathlib.Path) -> VerifyResult:
    """Walk the chain and report the first break in each category.

    Checks, in order of how damning they are: a malformed line, a hash that does
    not match its own content (edited entry), a `prev` that does not match the
    previous entry's hash (removed or reordered entry), a sequence number that
    skips, and a signature that does not verify against the local key.
    """
    entries = read_ledger(root)
    problems: list[str] = []
    signed = 0
    key = _key(root)
    prev_hash = GENESIS
    for index, entry in enumerate(entries):
        where = f"entry #{index}"
        if "_malformed" in entry:
            problems.append(f"{where}: line is not valid JSON")
            prev_hash = None
            continue
        seq = entry.get("seq")
        if seq != index:
            problems.append(f"{where}: seq is {seq}, expected {index} (an entry was removed or reordered)")
        if prev_hash is not None and entry.get("prev") != prev_hash:
            problems.append(
                f"{where}: prev {str(entry.get('prev'))[:12]} does not match the previous entry's hash "
                f"{str(prev_hash)[:12]} (the chain is cut here)")
        recomputed = entry_hash(entry)
        if entry.get("hash") != recomputed:
            problems.append(f"{where}: content does not match its hash (this entry was edited after the fact)")
        elif key is not None:
            if "sig" not in entry:
                problems.append(f"{where}: unsigned, but this repository has a provenance key")
            elif not hmac.compare_digest(entry["sig"], _sign(root, recomputed) or ""):
                problems.append(f"{where}: signature does not verify")
            else:
                signed += 1
        prev_hash = entry.get("hash")
    return VerifyResult(ok=not problems, entries=len(entries), signed=signed, problems=problems)


def export(root: pathlib.Path, fmt: str = "jsonl", since: str | None = None,
           action: str | None = None) -> str:
    """Serialise the ledger for a compliance reviewer who does not have the repo."""
    entries = [e for e in read_ledger(root) if "_malformed" not in e]
    if since:
        entries = [e for e in entries if (e.get("ts") or "")[:10] >= since]
    if action:
        entries = [e for e in entries if e.get("action") == action]
    if fmt == "jsonl":
        return "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries)
    if fmt == "csv":
        cols = ("seq", "ts", "actor", "action", "subject", "org", "team", "hash")
        rows = [",".join(cols)]
        for e in entries:
            rows.append(",".join(_csv_cell(e.get(c)) for c in cols))
        return "\n".join(rows)
    if fmt == "markdown":
        rows = ["| seq | ts | actor | action | subject |", "|---|---|---|---|---|"]
        for e in entries:
            rows.append(f"| {e.get('seq')} | {e.get('ts')} | {e.get('actor')} | "
                        f"{e.get('action')} | {e.get('subject')} |")
        return "\n".join(rows)
    raise ValueError(f"unknown export format '{fmt}' (jsonl, csv, markdown)")


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text

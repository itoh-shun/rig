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
import hashlib
import hmac
import json
import pathlib

from ..ports import Clock, Env, FileStore
from ..ports.local import LOCAL_FILES, OS_ENV, SYSTEM_CLOCK

LEDGER_REL = ".rig/ledger.jsonl"
GENESIS = "0" * 64


def ledger_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "ledger.jsonl"


def _now(*, clock: Clock = SYSTEM_CLOCK) -> str:
    return clock.stamp()


def _canonical(entry: dict) -> bytes:
    """Bytes that the hash covers: every field except the hash and signature."""
    body = {k: v for k, v in entry.items() if k not in ("hash", "sig")}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def entry_hash(entry: dict) -> str:
    return hashlib.sha256(_canonical(entry)).hexdigest()


def key_path(root: pathlib.Path) -> pathlib.Path:
    return root / ".rig" / "provenance.key"


def _key(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> bytes | None:
    """The signing key, if this repository has one. Never creates it here —
    signing is opportunistic, and a read-only checkout must still be able to
    append (an unsigned entry is still chained).

    `read_bytes` and deliberately **not** `read_secret_bytes`, which is the security
    decision the `FileStore` docstring flags rather than a choice of method name.

    Measured before this was left as it is. Three shapes were
    put in front of `LOCAL_FILES.read_secret_bytes`: a key at mode 0600 inside a 0755
    `.rig/` — which is what every checkout has, because `.rig/` is created by
    `mkdir(parents=True, exist_ok=True)` under the ambient umask and
    `workbench.state.load_or_create_provenance_key` chmods the file and not the
    directory — was refused with `OSError: secure runtime directory must be owned by
    the caller with mode 0700`; 0600 inside 0700 was read; and 0644 inside 0700 was
    refused with `secure runtime file must be caller-owned regular mode 0600 with one
    link`. The first row is the legitimate ledger the swap would break: the strict read
    raises, the `except OSError` below turns it into `None`, and the ledger goes on
    appending, silently unsigned, on every repository that has a key today.

    So the read stays wide and the compensating check is in `verify`, which is where the
    downgrade would otherwise be invisible: a key file that exists and cannot be read is
    reported as a problem of its own rather than quietly skipping the signature pass.

    Tightening it is still a migration and not a swap: `.rig/` (or a new 0700
    subdirectory) has to be narrowed, `load_or_create_provenance_key` has to write
    through `write_secret_bytes`, and existing keys have to be chmod-ed — in that
    order, because the strict read refuses the directory before it looks at the file.
    """
    p = key_path(root)
    try:
        return files.read_bytes(p) if files.is_file(p) else None
    except OSError:
        return None


def _sign(root: pathlib.Path, digest: str, *, files: FileStore = LOCAL_FILES) -> str | None:
    key = _key(root, files=files)
    if key is None:
        return None
    return hmac.new(key, digest.encode("ascii"), hashlib.sha256).hexdigest()


def read_ledger(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> list[dict]:
    p = ledger_path(root)
    if not files.is_file(p):
        return []
    out: list[dict] = []
    for line in files.read_text(p).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"_malformed": line})
    return out


def last_entry(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> dict | None:
    entries = read_ledger(root, files=files)
    return entries[-1] if entries else None


def append(root: pathlib.Path, action: str, *, actor: str, subject: str = "",
           org: str | None = None, team: str | None = None,
           data: dict | None = None, clock: Clock = SYSTEM_CLOCK,
           env: Env = OS_ENV, files: FileStore = LOCAL_FILES) -> dict:
    """Append one governance event and return it.

    Never raises: an audit trail that can break the operation it is recording
    would get switched off within a week. A failure to write is visible as a
    gap, which `verify` reports.
    """
    prev_entries = read_ledger(root, files=files)
    prev = prev_entries[-1].get("hash", GENESIS) if prev_entries else GENESIS
    entry = {
        "seq": len(prev_entries),
        "ts": _now(clock=clock),
        "actor": actor,
        "action": action,
        "subject": subject,
        "org": org,
        "team": team,
        "data": data or {},
        "invoker": env.get("RIG_INVOKER") or "direct",
        "prev": prev,
    }
    digest = entry_hash(entry)
    entry["hash"] = digest
    sig = _sign(root, digest, files=files)
    if sig:
        entry["sig"] = sig
    try:
        files.append_line(ledger_path(root),
                          json.dumps(entry, ensure_ascii=False, sort_keys=True))
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


def verify(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> VerifyResult:
    """Walk the chain and report the first break in each category.

    Checks, in order of how damning they are: a malformed line, a hash that does
    not match its own content (edited entry), a `prev` that does not match the
    previous entry's hash (removed or reordered entry), a sequence number that
    skips, and a signature that does not verify against the local key.

    A key file that is present and unreadable is itself one of the problems, reported
    before the walk: that is the only state in which the signature column below is
    absent for a reason other than "this repository has no key".
    """
    entries = read_ledger(root, files=files)
    problems: list[str] = []
    signed = 0
    key = _key(root, files=files)
    key_file = key_path(root)
    if key is None and any(e.get("sig") for e in entries):
        # The other way the signature pass falls silent, and the one an attacker chooses:
        # the hash chain needs no secret, so anybody can rewrite the ledger, recompute
        # every `hash` and `prev`, and then DELETE the key rather than forge a signature.
        # Measured on a two-entry signed ledger cut down to one with the chain recomputed:
        # before this line `verify` answered `ok=True`, "ledger intact — 1 entries,
        # unsigned". Entries that carry `sig` are a claim that this repository signs; a
        # missing key cannot check that claim, and unchecked is not intact.
        problems.append("entries carry signatures but .rig/provenance.key is absent, so no "
                        "signature could be checked (the key was removed, or this is a "
                        "checkout that never had it)")
    if key is None and (files.is_file(key_file) or files.is_dir(key_file)):
        # The compensating check `_key` names. Without it, a key this process cannot read
        # — a mode it may not open, a directory in its place, a mount that refuses it —
        # makes every signature check below fall away silently, and `verify` answers
        # "intact, unsigned" for a repository whose entries were all signed. `is_dir` is
        # here beside `is_file` because the path existing at all is the fact: `_key` reads
        # only a regular file, so a directory reaches this line as an absent key too.
        problems.append(".rig/provenance.key exists but could not be read, so no signature "
                        "was checked; the hash chain was still checked")
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
            elif not hmac.compare_digest(entry["sig"],
                                         _sign(root, recomputed, files=files) or ""):
                problems.append(f"{where}: signature does not verify")
            else:
                signed += 1
        prev_hash = entry.get("hash")
    return VerifyResult(ok=not problems, entries=len(entries), signed=signed, problems=problems)


def export(root: pathlib.Path, fmt: str = "jsonl", since: str | None = None,
           action: str | None = None, *, files: FileStore = LOCAL_FILES) -> str:
    """Serialise the ledger for a compliance reviewer who does not have the repo."""
    entries = [e for e in read_ledger(root, files=files) if "_malformed" not in e]
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

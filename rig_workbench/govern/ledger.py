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

#: How each listing names the ways an actor could have reached it. Two listings, two files,
#: two different sets of sources, and naming the wrong ones is its own small lie: the chain
#: (`govern audit log`) carries entries written by `govern approve` and `govern waiver`,
#: which honour `--actor` and `identity.current_actor`'s `RIG_ACTOR` / `RIG_USER` /
#: `git config user.name`, while `.rig/audit.jsonl` (`workbench audit`) is written only by
#: `accept`, whose actor is `state.current_identity` — `RIG_USER`, then `git config
#: user.name`, and nothing else.
LEDGER_ACTOR_SOURCES = "--actor, RIG_ACTOR, RIG_USER or git config user.name"
AUDIT_LOG_ACTOR_SOURCES = "RIG_USER or git config user.name"


def actor_note(sources: str) -> str:
    """What a listing has to say about its `actor` column, once, at the top.

    An entry's actor is whatever name the command was run under, and this repository has no
    identity provider: every way of supplying one is a name typed by the caller and none is
    checked against anything. A listing that prints the column without saying so invites the
    reading the column cannot support — that the name is who did it — and entries written
    before any record said so are exactly as unauthenticated as the ones written after, so
    the caveat belongs on the listing rather than on the entries that happen to carry a
    field.
    """
    return f"actor names are self-asserted — nothing here authenticates them ({sources})"

#: How many identical events in a row are written before the run is collapsed.
#:
#: **Why there is a cap at all.** Every append is one line and one `read_ledger`, so a
#: caller that can make the same event happen over and over costs the repository both disk
#: and time without bound: measured at this commit's parent, 1000 identical appends wrote
#: 369,890 bytes over 1000 lines and took 2.5s, and the time is quadratic because each
#: append re-reads the file to find `prev`. `govern audit export` (a read-only command that
#: records that it ran) and `govern approve grant` (re-runnable, and `upsert` keeps
#: `approvals.json` at one decision while the chain grows a line each time) are both
#: loopable today by a caller who is otherwise getting nowhere, and `accept --force`'s
#: `accept_refused` line (a3a7508) is the loudest of them: eight refusal paths each write
#: one, so the caller a governance boundary is refusing is the caller who can write most.
#:
#: **Why the bound is a cap and not a rewrite.** Collapsing a run into one line carrying a
#: live count would mean rewriting the last line on every repeat; `FileStore.append_line`
#: exists precisely because this file "appends and does not rewrite, which is what makes
#: the hash chain an append-only record rather than a file that gets rebuilt", and a
#: non-atomic whole-file rewrite of an audit trail trades a bounded file for a losable one.
#:
#: **So the cap is per event per day, and the count it leaves is a floor.** An event is counted by
#: what it is *and the date it happened on*; the first `REPEAT_CAP` of each are written as they
#: always were, the next carries that day's line count for it (`REPEAT_CAP + 1`) in `collapsed`,
#: and the rest of that day are not written and come back from `append` marked `suppressed`. How
#: many there were is not recorded anywhere. Tomorrow the same event starts again. The bound is
#: therefore `REPEAT_CAP + 1` lines per event per day, which is constant, and a campaign that runs
#: for a week is a week of entries rather than one — the magnitude within a day is floored at
#: `REPEAT_CAP + 1`, the timeline across days is kept exactly.
#:
#: **Two shapes were tried first and are recorded because each was wrong in a way worth
#: knowing.** A cap over a *consecutive run* was defeated outright by alternating two events:
#: neither is ever at the tail twice, so 200 alternating calls wrote 200 lines. And
#: re-emitting the count at doubling intervals — 4, 8, 16 — cannot be done here at all:
#: advancing to 8 requires knowing that events 5, 6 and 7 happened, and a suppressed event
#: leaves nothing to count. Append-only, bounded, and exactly counted are not available at
#: once in this file, which is appended and never rebuilt; the third would need either a
#: rewrite of the tail entry (`write_text` here is not atomic, so a crash would trade a
#: bounded file for a lost one) or a counter file beside the chain. The residual is recorded
#: as debt: within one day, an event that happened `REPEAT_CAP + 1` times and one that
#: happened a thousand times read the same, and no reader can tell them apart.
#:
#: The chain is untouched by any of it: what is written is written the same way, `seq` stays
#: dense, and `verify` covers `collapsed` like every other field because it is inside the
#: hash. Readers keep the same line shape — a collapsed line carries the same `action`,
#: `actor`, `subject` and `data` as the lines it caps — and the two that must not read a
#: capped line as a single event say so themselves. `collapsed_note` renders the cap in
#: `workbench audit` and `govern audit log`, so the printed count reads as the floor it is;
#: `workbench.state.audit_event_weights` keeps a *window* that holds the capped line without
#: the lines before it from counting it as one. Over a whole file the weights are all 1 and
#: sum to the line count — neither of them recovers how many events there were, because
#: nothing records it.
REPEAT_CAP = 3

#: Fields a repeat is judged by: what happened, to whom, under whose name. Not the time of
#: day, `seq`, `prev`, `hash`, `sig` (every entry differs there by construction) and not
#: `collapsed` (the marker must read as one more of the event it caps, or it would never cap
#: it). The *date* is part of it — see `REPEAT_CAP`: it is what keeps the cap from silencing
#: an event that legitimately recurs next week, and what keeps a campaign that runs for days
#: visible as days.
_IDENTITY_FIELDS = ("action", "actor", "subject", "org", "team", "invoker")


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


#: The shortest byte string this repository will sign with, and the one rule about it.
#:
#: HMAC takes a key of any length, including none, so "is there a file" was never the
#: question — `.rig/provenance.key` at zero bytes signed every entry with a secret anybody
#: reproduces with `touch`, and a one-byte key written by `echo > .rig/provenance.key` was
#: brute-forced in five guesses. 16 bytes is 128 bits, the conventional floor for an HMAC
#: secret, and it costs nothing rig has ever written: there is exactly one writer of this
#: file — `workbench.state.load_or_create_provenance_key`, the only `secrets.token_bytes`
#: call in the tree — and it generates 32 bytes. So the only keys this refuses are keys nobody
#: generated — an empty file, a stray newline, a truncated copy — and it refuses them
#: loudly rather than signing with them.
MIN_KEY_BYTES = 16


def usable_key(raw: bytes | None) -> bytes | None:
    """`raw` if it can be signed with, else `None`. **The single definition, on purpose.**

    There are three readers of `.rig/provenance.key` — `_key` here,
    `workbench.state.provenance_key`, and the loader
    `workbench.state.load_or_create_provenance_key`, which is also the one creator and asks
    before it makes a key where there is none. All three take their bytes from
    `observe_key_file`; all three decide with this. They were fixed one at a time once already: closing the
    empty key in the ledger left the provenance signer recomputing under the same empty
    secret, so a record rewritten to a different `accepted_by` and re-signed still printed
    `valid, untampered`. All of them ask this function, so what counts as a key is one
    answer in one place and a third reader cannot quietly disagree with it.
    """
    return raw if raw is not None and len(raw) >= MIN_KEY_BYTES else None


def _key(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> bytes | None:
    """The signing key, if this repository has one — and a file too short to be a key is
    not one (`usable_key` is the rule; the comment above the read says what signing with an
    empty one produced). Never creates it here —
    signing is opportunistic, and a read-only checkout must still be able to
    append (an unsigned entry is still chained).

    `read_bytes` and deliberately **not** `read_secret_bytes`, which is the security
    decision the `FileStore` docstring flags rather than a choice of method name.

    Measured before this was left as it is. Three shapes were
    put in front of `LOCAL_FILES.read_secret_bytes`: a key at mode 0600 inside a 0755
    `.rig/` — which is what every checkout has, because `.rig/` is created by
    `mkdir(parents=True, exist_ok=True)` under the ambient umask and
    `workbench.state`'s creator chmods the file and not the
    directory (the `chmod` moved into `_create_key_if_absent`'s temporary file, where it is
    applied before the key is linked into place; the observable mode is the same 0600) — was refused with `OSError: secure runtime directory must be owned by
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
    # A file too short to be a key is not a key, and signing with one is worse than not
    # signing: HMAC accepts a zero-length secret, so every entry got a `sig` that anybody
    # can recompute — `touch .rig/provenance.key` anywhere reproduces it — while `verify`
    # reported them "signed". Measured before this rule existed, on a repository whose key
    # file was zero bytes: `_key` returned b"", the appended entry carried a `sig`, `verify`
    # answered `ok=True`, "ledger intact — 1 entries, 1 signed", and that signature
    # recomputed byte-for-byte under `hmac.new(b"", ...)`. `None` is the answer the
    # unreadable key already gives, and it is the right one for the same reason: the file is
    # there and this process has no secret out of it. `verify`'s existing "exists but could
    # not be read" problem then covers this without a second shape, and `signs_here` — which
    # asks about the path, not the bytes — keeps answering that this repository signs, so an
    # unsigned entry beside an unusable key is still reported.
    #
    # The read itself is `observe_key_file`, with the kind dropped — this function is that
    # function minus one element of the tuple, so the two cannot drift.
    return usable_key(observe_key_file(key_path(root), files=files)[0])


#: What `observe_key_file` was able to establish about the path.
#:
#: `"other"` is the only one that asserts anything about the *kind* of the file, and the
#: two messages built on this — `verify`'s problem below and the signer's set-aside warning
#: — say "nothing was signed with what is there" on the strength of it. It is what
#: `is_file` answering `False` means, and that is a slightly wider thing than "a `stat`
#: said not-a-regular-file": `pathlib` turns `ENOENT`, `ENOTDIR`, `EBADF` and `ELOOP` into
#: `False` as well. All four are safe to put here, and for the same reason the honest ones
#: are — no such path, a non-directory in the way, a bad descriptor and a symlink loop are
#: each a thing that cannot be a key and that every reader of this path already refuses.
#: What is *not* safe is the errno `pathlib` re-raises: `EACCES` on the `stat` establishes
#: nothing at all, so it is `"unknown"` and routes with `"regular"` to the branch that
#: assumes a key may be in there. Sending it the other way claims nothing was signed with
#: what is at the path, over a live key — a symlink to a real 32-byte key through a
#: directory this process may not traverse is the reproducible case, and there is no
#: reading it from here to find out.
KeyFileKind = str  # "regular" | "other" | "unknown"


def observe_key_file(p: pathlib.Path, *,
                     files: FileStore = LOCAL_FILES) -> tuple[bytes | None, KeyFileKind]:
    """The bytes this process read from the key path, and what it established about its kind.

    **The single observation, for the same reason `usable_key` is the single rule.** Every
    read of `.rig/provenance.key` in this repository is now this function: `_key` above,
    `workbench.state.provenance_key`, and the loader that sets an unusable key aside
    (`workbench.state.load_or_create_provenance_key`). There were three hand-written copies
    of "`is_file`, then `read_bytes`, `OSError` to `None`" before, and two of them grew a
    message an operator acts on — the set-aside warning and `verify`'s problem below —
    which have to tell the same three situations apart, because each has a different
    remedy: a file below the floor is measured and permanent, a regular file we may not
    open can be a whole key and wants its permissions fixed, and a path that is not a
    regular file was never read as a key at all. Copies agreeing today is not one reader:
    the split was made on the signing side first and the collapsed clause survived here,
    in a message no test asserted, which is what one observation exists to stop.

    It lives in `govern` and not in `workbench` because that is the direction the
    dependency already runs — `workbench.state` imports `MIN_KEY_BYTES` and `usable_key`
    from here, and `tests/test_layering_contract.py` forbids a judgement module of a
    migrated pillar reaching the other way. It takes a `FileStore` for the same reason
    every read in this module does: `verify` is called with one.

    **The `is_file` call is inside the `try`.** It swallows `ENOENT`, `ENOTDIR`, `EBADF`
    and `ELOOP` and re-raises everything else, so a key symlinked through a directory this
    process may not traverse came out of both callers as a raised `PermissionError` —
    measured on `verify`, in a child that dropped to an unprivileged uid:
    `PermissionError: [Errno 13] Permission denied: .../.rig/provenance.key`, out of a
    function whose contract is to report problems rather than raise them.

    **This narrows the sibling race; it does not close it.** The `stat` and the `read` are
    two calls (and the signer's `rename` is a third), so a sibling that changes the kind in
    between is reported under the kind seen first. That is the residual, and it is written
    down rather than claimed away.
    """
    try:
        regular = files.is_file(p)
    except OSError:
        return None, "unknown"
    if not regular:
        return None, "other"
    try:
        return files.read_bytes(p), "regular"
    except OSError:
        return None, "regular"


def key_path_present(p: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> bool:
    """Whether there is something at the key path — the one presence question both sides ask.

    `verify` asks it to tell *the key is gone* from *something is at the key path that is
    not a key*, and `load_or_create_provenance_key` asks it to tell *make a key here* from
    *set this aside first*. They used to ask it two different ways — `is_file` or `is_dir`
    here, `Path.is_symlink() or Path.exists()` there — and the two answers disagreed on
    every shape that is neither a regular file nor a directory. Measured on a one-entry
    signed ledger with a FIFO at the key path: `verify` reported `.rig/provenance.key is
    absent ... (the key was removed, or this is a checkout that never had it)` while
    `accept` on the same repository set that path aside as not a regular file. A device and
    a dangling symlink gave the same pair, and an operator reading "removed" goes looking
    for a backup of a key that was never there.

    **`!= "absent"`, and that is the whole of the guard.** `FileStore.presence` answers
    `"present"`, `"absent"` or `"unknown"`, and only a settled absence is read as absence,
    so a denial or a symlink loop above the path counts as something being there. Both
    sides want that direction and for the same reason: on this one the alternative is
    telling an operator their key was removed on the strength of a `stat` that never
    answered, and on the other it is generating a fresh key over a path that may hold the
    real one.

    **This replaces `_is_dir` rather than sitting beside it.** That helper existed to keep
    `Path.is_dir()`'s re-raised `EACCES` out of `verify`, and it paid for that with `False`
    — the answer that says the key is gone. The port answers `"unknown"` there instead, so
    a refusal no longer has to be spent on a wrong reading, and the cost `_is_dir` wrote
    down (a directory whose `is_dir` was refused mid-race dropping the third problem on an
    unsigned ledger) goes with it. What is *not* claimed here is that `verify` cannot raise
    at all: with `.rig/` itself untraversable, `read_ledger`'s own `is_file` raises before
    this function is reached — measured, in a child that dropped to an unprivileged uid,
    `PermissionError: [Errno 13] Permission denied: .../.rig/ledger.jsonl`, unchanged by
    this commit and not the key path's problem to fix.

    **It narrows the sibling race; it does not close it.** This is a call beside the
    observation's `stat` and read, so a sibling that changes the path in between is reported
    from a state that never existed, exactly as `observe_key_file` records of its own pair.
    `verify` asks this one *first* for that reason, and the comment there has the two
    interleavings and which command produces each.
    """
    return files.presence(p) != "absent"


def signs_here(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> bool:
    """Whether this repository signs what it appends — i.e. it has a provenance key.

    The question `approval.ledger_attestations` asks before it decides to hold decisions to
    the chain. A keyed repository is one where every honest ledger entry carries a `sig`, so
    an *absent or unsigned* ledger there is not the ordinary "this team has not turned the
    chain on" — it is a chain that should exist and does not, which is the shape a
    forged approval needs. Where there is no key, the chain cannot attest anything and
    the policy's `audit.chain_required` is what decides.

    The *path* and not the read: a key that exists and cannot be read is still a repository
    that signs, and `verify` is where that is a problem of its own (it says so in as many
    words). Answering "no key" here would hand a decision back to the looser reading
    exactly when the stricter one is called for.

    **And a `stat` that refuses answers `True`,** the same direction `key_path_present`
    above takes a refusal in, and for the reason in the paragraph above. `Path.is_file()`
    re-raises `EACCES`, so the bare call put `PermissionError` out of
    `approval.ledger_attestations` — a decision path — on a key behind a directory this
    process may not traverse; that is a crash, not a verdict. Of the two verdicts available
    where nothing could be established, "this repository signs" is the stricter one, and it
    is the one this function's whole argument says to take when it cannot tell.
    """
    try:
        return files.is_file(key_path(root))
    except OSError:
        return True


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

    Repeats of one event are bounded — see `REPEAT_CAP`. The `REPEAT_CAP + 1`-th carries that
    day's line count for it (`REPEAT_CAP + 1`) in `collapsed`; the rest of that day are not
    written and come back marked `suppressed`. How many there were is not recorded anywhere.
    A blocked caller costs four lines a day.
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
    on_record = _event_total(prev_entries, entry)
    if on_record > REPEAT_CAP:
        # Not written. The candidate comes back rather than the last entry on disk, so a
        # caller is told which event was refused and how much of it is on record, not what
        # some earlier entry happened to say.
        return {**entry, "collapsed": on_record, "suppressed": True}
    if on_record == REPEAT_CAP:
        entry["collapsed"] = on_record + 1
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


def collapsed_note(entry: dict) -> str:
    """What a listing prints beside an entry that closed a run of identical events, or "".

    Both listings render it — `workbench audit` and `govern audit log` — because a capped
    run that looks like an ordinary tail is a count read as complete: measured, 50 refused
    forces left four `accept_refused` lines and `wb audit` printed
    `## rig audit (latest 4 / 4 total)` over four identical entries, from which the only
    honest reading was "four refusals".

    **The number is a floor, and the wording says so rather than implying a total.**
    `collapsed` is how many lines this event has on that day's record, which is
    `REPEAT_CAP + 1` and no more; how many repeats followed is not recorded anywhere, for
    the reason `REPEAT_CAP` sets out. A reader must be able to see that the count is capped
    — the defect this exists to fix was a listing printing `4 / 4 total` over 50 refusals —
    so the line says both what is there and that more is not.
    """
    n = entry.get("collapsed")
    if not isinstance(n, int) or n <= 0:
        return ""
    return f"  (+{n - 1} more like it that day; further repeats that day were not recorded)"


def _repeat_key(entry: dict) -> tuple:
    return tuple(entry.get(field) for field in _IDENTITY_FIELDS) + (
        str(entry.get("ts") or "")[:10],
        json.dumps(entry.get("data") or {}, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), default=str),)


def _event_total(entries: list[dict], candidate: dict) -> int:
    """How many lines this event already has on today's record, anywhere in the ledger.

    **Anywhere, and not only at the tail.** The first shape of this counted a consecutive
    run, which bounded a loop of one event and did nothing about a loop of two: alternating
    A and B, neither is ever at the tail twice, so the file grew a line per call exactly as
    before — measured, 200 alternating calls wrote 200 lines, none collapsed. Counting every
    occurrence closes that; scoping the key to the day (`_repeat_key`) is what keeps it from
    also silencing an event that recurs legitimately a week later.

    A `collapsed` entry already accounts for every line before it with the same key, so it
    sets the running count rather than adding to it.
    """
    key = _repeat_key(candidate)
    total = 0
    for previous in entries:
        if "_malformed" in previous or _repeat_key(previous) != key:
            continue
        carried = previous.get("collapsed")
        total = carried if isinstance(carried, int) and carried > 0 else total + 1
    return total


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


#: What a new key does to this ledger, on the two branches that send the operator to make
#: one. **Measured, on a two-entry signed ledger, by running the loader and verifying
#: again** — not reasoned from the fact that the key changed.
#:
#: The sentence these branches used to end on was "this problem stops once it has". It does
#: stop, and that is the wrong half to report: what replaces it is `entry #0: signature does
#: not verify` on every entry, which is the most alarming line this tool prints and the one
#: that reads as tampering. An operator (or an agent) told the remedy clears it up runs
#: `accept` and lands in an incident. The signing side's own prose has carried the
#: compensating clause since it was written; this half had dropped it.
#:
#: **And where nothing was signed it is a different permanent failure, not none.** Measured
#: on two unsigned entries beside a short key: `entry #0: unsigned, but this repository has
#: a provenance key`, on every entry, and it does not go away either. Scoping the first
#: clause to "every entry signed before it" made it true of that shape rather than
#: informative, so the second shape is named instead of left to be inferred.
#:
#: The hedge sits on the move and not on the key, because that is the order `accept` runs
#: in: `_set_unusable_key_aside` failing means it never reaches the creation and refuses
#: with `provenance_key_unavailable`. "generates a key if it can" put the condition on the
#: half that is not the one that fails.
#:
#: `tests/test_govern_ledger.py::test_the_remedies_promise_only_what_the_next_accept_does`
#: drives the fixture, runs the loader and asserts the aftermath, and asserts that this
#: string is in the problems that promise it. What that test measures is what these
#: remedies are allowed to say.
_AFTERMATH = ("this problem is then replaced by `signature does not verify` on every entry "
              "signed before it, or by `unsigned, but this repository has a provenance "
              "key` where there were none, and neither goes away")


def verify(root: pathlib.Path, *, files: FileStore = LOCAL_FILES) -> VerifyResult:
    """Walk the chain and report the first break in each category.

    Checks, in order of how damning they are: a malformed line, a hash that does
    not match its own content (edited entry), a `prev` that does not match the
    previous entry's hash (removed or reordered entry), a sequence number that
    skips, and a signature that does not verify against the local key.

    A key file that is present and yields no key — too short to sign with (`MIN_KEY_BYTES`),
    unreadable, or not a regular file at all — is itself one of the problems, reported
    before the walk in the shape the situation earns: that is the only state in which the
    signature column below is absent for a reason other than "this repository has no key".
    """
    entries = read_ledger(root, files=files)
    problems: list[str] = []
    signed = 0
    key_file = key_path(root)
    # **Presence first, and the order is load-bearing under a sibling.** These are two
    # separate looks at one path, so a sibling that changes it in between is reported from
    # a state that never existed; what the order decides is *which* interleaving that is.
    # Asked after the observation, the ordinary concurrent `accept` — set an unusable file
    # aside, link a fresh key — produced a kind from before the rename with a presence from
    # after it, and `verify` asserted `it is not a regular file … no entry in this ledger
    # was signed with it` over a live 32-byte key; driven with a store that links a key
    # inside the presence call, that is what it printed. Asked first, the same interleaving
    # answers `absent`, the observation then reads the key that is really there, and
    # `verify` comes back `ok=True` with no key problem at all — driven the same way.
    #
    # What is left, and it is not claimed away: a key removed between these two calls still
    # reaches the kind branch, driven. No command here does that — the loader returns early
    # on a usable key and only renames what it read as unusable — so it wants a hand `rm`
    # or a rotation by hand, where the previous order wanted an ordinary `accept`.
    key_present = key_path_present(key_file, files=files)
    # One look at the bytes, shared with the signer, rather than a `_key` read and a second
    # `is_file` beside it that could disagree with it.
    observed, key_kind = observe_key_file(key_file, files=files)
    key = usable_key(observed)
    # Present, in the sense that matters here: something is at the path. A zero-byte file, a
    # one-byte file, a directory and a FIFO are all a key this process cannot sign with, and
    # none of them is the key being *gone* — saying "absent" over one of them describes the
    # wrong event to whoever is reading the problem.
    #
    # **One question, and the signer asks the same one.** This used to be `key_kind` plus an
    # `is_dir`, which made presence mean "a regular file, a directory, or a `stat` that
    # refused" — so a FIFO, a device and a dangling symlink were reported as the key being
    # removed while `accept` on the same repository set them aside as not regular files.
    # `key_path_present` above is what both sides ask now, and `"unknown"` still counts as
    # present: the call that would settle it is the call that just refused, and of the two
    # readings — "something is there this process may not look at" and "gone" — only the
    # first can be established.
    if key is None and any(e.get("sig") for e in entries):
        # The other way the signature pass falls silent, and the one an attacker chooses:
        # the hash chain needs no secret, so anybody can rewrite the ledger, recompute
        # every `hash` and `prev`, and then DELETE the key rather than forge a signature.
        # Measured on a two-entry signed ledger cut down to one with the chain recomputed:
        # before this line `verify` answered `ok=True`, "ledger intact — 1 entries,
        # unsigned". Entries that carry `sig` are a claim that this repository signs; a
        # missing key cannot check that claim, and unchecked is not intact.
        problems.append(
            "entries carry signatures but .rig/provenance.key "
            + ("is present and is not a usable key, so no signature could be checked "
               "(see the next problem)" if key_present else
               "is absent, so no signature could be checked (the key was removed, or this "
               "is a checkout that never had it)"))
    if key is None and key_present:
        # The compensating check `_key` names. Without it, a key this process cannot read
        # — a mode it may not open, a directory in its place, a mount that refuses it —
        # makes every signature check below fall away silently, and `verify` answers
        # "intact, unsigned" for a repository whose entries were all signed.
        #
        # Three reasons, because what the operator should do next differs and one clause
        # covering all of them gives two of them the wrong instruction. It read "it is
        # unreadable, or shorter than the 16 bytes a signing key must have" over every
        # shape: measured, an 8-byte file and a 32-byte key this process may not open
        # produced byte-identical problems.
        #
        # Only the first measured anything. Its bytes were counted, they are under the
        # floor, `usable_key` is the one rule and every reader applies it, so entries
        # signed with that file are unverifiable for good and there is nothing to fix.
        #
        # A regular file that would not open was not measured at all: a whole 32-byte key
        # behind a mode, an owner, or a directory this process may not traverse arrives
        # here with its bytes intact, and its entries verify again the moment the
        # permissions do. Telling that operator the same thing as the first sends them to
        # re-sign a ledger that was never broken.
        #
        # A path `is_file` answered `False` about is the third, and it is the only one
        # that may claim anything about the *kind*: every read of this path goes through
        # `observe_key_file`, which returns before it opens anything `is_file` refuses, so
        # what is sitting there has never been read as a key and no signature in this
        # ledger was made with it. A `stat` that did not answer must never borrow that
        # claim — see `KeyFileKind` — so it falls to the `else` below rather than being
        # routed by a reader who remembers to. The enumeration in it is open and matches
        # the signer's word for word, because every kind in it now reaches this branch:
        # while presence was `is_dir`, a directory was the only one that could.
        if observed is not None:
            reason = (f"it holds {len(observed)} byte(s), below the {MIN_KEY_BYTES} bytes "
                      "a signing key must have")
            remedy = ("There is nothing to repair on that file — every reader refuses it "
                      "and entries signed with it can never be verified again. The next "
                      "`accept` sets it aside under .rig/provenance.key.unusable, numbered "
                      "past any already there, and then generates a key, or refuses "
                      "without generating one if it cannot move it; " + _AFTERMATH)
        elif key_kind == "other":
            reason = ("it is not a regular file, such as a FIFO, a directory, a device, "
                      "or a symlink that resolves to nothing")
            remedy = ("Find out what is at the key path — no entry in this ledger was "
                      "signed with it, because a path of that kind is never read as a key, "
                      "so there is no key to recover. Identify it rather than opening it, "
                      "because reading a FIFO blocks until something writes. The next "
                      "`accept` sets it aside under .rig/provenance.key.unusable, numbered "
                      "past any already there, and then generates a key, or refuses "
                      "without generating one if it cannot move it; " + _AFTERMATH)
        else:
            # "regular" and "unknown" both land here, and that is the safe direction: this
            # branch claims nothing about the contents, it asks the operator to go and
            # look. The branch above does make a claim, so it needs a `stat` that answered.
            reason = "the permissions may not allow it"
            remedy = ("Fix the permissions on it and on the directories above it, then "
                      "re-run; its contents are unread, so whether these entries still "
                      "verify is unknown until something can read it")
        problems.append(f".rig/provenance.key exists but could not be read as a key "
                        f"({reason}), so no signature was checked; the hash chain was "
                        f"still checked. {remedy}")
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

"""The drill judge (`instructions/drill` ③-b): does a credited finding assert the seeded defect?

The scorer in `detection_corpus` decides *where* a finding points and *whether* it
touches the right topic. Both are decidable from the text, and both are pinned by
tests. What it cannot decide is the **direction** of the claim: `concept` is a list of
topic words, so "avoids the N+1 query" and "has an N+1 query" match identically while
making opposite claims. Two attempts to settle direction with a regex were written,
measured and removed — a correctness-verb cue list cost six true positives across the
ideal reviews, and a clause-level negation test scored "`reportUsage` does not await
`client.send`" as zero while an attacker only had to write "awaits the send" to walk
past. Direction is a semantic call, and this module is where it is made.

Four properties this module exists to hold:

**Narrowing only.** The judge sees only the pairs the deterministic layer already
credits, and its verdict can only take credit away. It can never add a detection. A
judge that could add credit would put the DECOY and NARRATION defences — which today
are deterministic and pinned — behind a model call, and the judge is load-bearing
enough already.

**Blind by construction — prompt *and* executor.** The prompt is built from exactly two
strings: the seed's `summary` and the finding's body. Not the `concept` regex, not the
answer key's `location`, not the other seeds, not the scoreboard. Building it here
rather than in a subagent dispatched by a session that holds the answer key is the
difference between blindness as a discipline and blindness as a property of the code.

A blind prompt is not enough, because the judge providers are agents with filesystem
access. Two rounds of this were needed and both were found by someone else, which is
the argument for having someone else look.

Round one: run from rig's own working directory, `codex exec` reads
`corpora/fixture/*/case.json` — every seed's `summary`, `concept` and `location`. Every
judge call now starts in a fresh empty directory outside the repository.

Round two: an empty directory is not an empty environment. The inherited `PATH` carries
`~/.claude/plugins/cache/.../rig/<version>/bin`, and the installed plugin **ships the
drill corpus**, so `echo $PATH` reaches the same answer key by another road. Alongside
it, `OLDPWD` points at rig's tree and a companion transcript variable points at the
session that planted the seeds.

Round three: filtering directories by name is not enough either. `~/.local/bin` holds
`codex` — so it went on the rebuilt `PATH` — and it also holds `rig-wb`, whose
`drill-corpus list` prints the corpus root, after which one `cat` gives every seed's
`summary`, `concept` and `location`. Two commands, no absolute path needed. So `PATH`
is now the system directories plus one temporary directory holding a single symlink to
the provider, and rig's own environment variables are dropped except
`RIG_PROVIDER_SUBPROCESS`, which `run_provider` adds and which names nothing.

State the property honestly, because it is weaker than "isolated": this **reduces
breadcrumbs**. It does not sandbox. `HOME` has to stay — the provider reads its own
credentials from it — and so do the system directories, because the provider needs a
shell. A judge that already knew an absolute path could still follow it. What is removed
is the ways to *learn* one from rig, and three rounds of somebody else looking says that
list is discovered rather than reasoned out.

**Replayable, not deterministic.** The word matters: the same pair asked twice can
answer differently — under the first prompt the same five bytes scored 4/5, 3/5, 3/5 and
3/5 across re-rolls — so what the ledger buys is that a *recorded* run replays
identically, not that the judge is a function. Verdicts land in a JSONL ledger keyed by
a content hash
of everything that could change the answer, the seed's `summary` text included — edit
a fixture and the old verdict stops matching rather than silently carrying over. A
replay reads the ledger; only new content reaches a provider. The fixture corpus ships
on a promise of byte-for-byte repeatability and a model call does not get to break it.

**Fail-closed into "unmeasured", never into a number.** An unparseable verdict, a
provider outage, or no ledger at all yields `None` — not a verdict. Callers turn that
into `unadjudicated`, and `drill.md` already forbids emitting a rate that was not
measured. An outage must read as "could not measure", never as a low or a high score.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import tempfile
from typing import Any, Callable

from ..orchestrate.providers import run_provider

#: The judge defaults to a different model family from the reviewer personas it scores.
#: `policies/independent-verification` asks for an independent party, and two Claude
#: instances sharing a failure mode is exactly the correlation it is written against.
#: codex's verifier role runs under `--sandbox read-only` (see `CodexRuntime`).
DEFAULT_JUDGE_PROVIDER = "codex"

#: `run_provider` has no `judge` role and does not need one: rig's existing judge panel
#: is dispatched as `verifier`, which is what this is. Reusing it keeps the call inside
#: `perf.PHASES` (`provider_verifier`) rather than falling to the untimed path.
JUDGE_ROLE = "verifier"

#: Not every provider label can carry this prompt. `rig` is the `claude` binary with
#: `RIG_VER_PREFIX` prepended to every verifier prompt (`ClaudeCliRuntime`), so the
#: judge question would arrive wrapped in rig's own PASS/FAIL scaffolding and
#: `parse_verdict` would answer `None` on every pair — a judge that fails closed on
#: everything, which reads as an outage rather than as a misconfiguration. `mock` and
#: `cmd` do not answer the question at all. Refusing here is louder than 45 unusable
#: verdicts.
JUDGE_PROVIDERS = ("codex", "claude")

VERDICTS = ("ASSERTS", "DENIES", "NEITHER")

#: Environment variables the judge subprocess keeps. Everything else is dropped, so a
#: new rig variable is invisible to the judge by default rather than by remembering.
#: `HOME` is here because the CLI providers read their own credentials from it, which is
#: also the limit of what this can claim — see the module docstring.
_ENV_KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "TERM", "TZ", "TMPDIR",
             "LANG", "LC_ALL", "LC_CTYPE", "XDG_RUNTIME_DIR")

#: The judge's `PATH`, plus one directory holding a link to the provider and nothing
#: else. The inherited `PATH` is not usable at any strength: `~/.claude/plugins/.../rig/*/bin`
#: is on it and that plugin ships `corpora/fixture`, and `~/.local/bin` — where `codex`
#: itself lives here — also holds `rig-wb`, whose `drill-corpus list` prints the corpus
#: root and every seed's `summary`, `concept` and `location` in two commands. Filtering
#: directories by name was tried and lost to the second of those; a directory is admitted
#: now only if this module put the single file in it.
_SYSTEM_PATH = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/local/sbin", "/usr/sbin", "/sbin")


def judge_shim(provider: str, into: pathlib.Path,
               environ: dict[str, str] | None = None) -> pathlib.Path | None:
    """A directory containing one symlink to `provider` and nothing else.

    Putting the provider's own directory on `PATH` puts everything beside it there too.
    On this machine that is `~/.local/bin`, which holds `rig-wb` — so a judge kept out
    of the repository and out of the plugin cache could still run `rig-wb wb drill-corpus
    list`, be told where the corpus is, and read the answer key. Same class of hole as
    the plugin `PATH`, a third install layout.

    Linking also settles the symlink problem the directory approach had: `claude` is
    found at `~/.local/bin/claude` but resolves into a `versions/` directory holding
    `2.1.259`, `2.1.263` and no executable of that name, so offering the resolved
    directory gave a judge that returned 127 on every pair. A link named `<provider>`
    pointing at whatever `shutil.which` found works in both layouts.
    """
    source = dict(os.environ if environ is None else environ)
    binary = shutil.which(provider, path=source.get("PATH"))
    if not binary:
        return None
    shim = pathlib.Path(into)
    shim.mkdir(parents=True, exist_ok=True)
    link = shim / provider
    if not link.exists():
        link.symlink_to(pathlib.Path(binary))
    return shim


def judge_env(provider: str, environ: dict[str, str] | None = None,
              shim: pathlib.Path | None = None) -> dict[str, str]:
    """The environment a judge subprocess runs in: what it needs, and nothing that points.

    Allowlist rather than denylist. A denylist has to be updated every time rig adds a
    variable, and the failure mode is silent — the judge simply learns something new and
    nobody notices until an attacker looks. `PATH` is rebuilt rather than filtered for
    the same reason; see `judge_shim` and `_SYSTEM_PATH`.

    Without a `shim` the provider is not reachable at all, which is deliberate: this
    function alone cannot make the judge runnable, so nothing can make it runnable by
    accident. `Adjudicator` supplies one.

    The honest limit: `HOME` stays, because the providers read their credentials from it,
    and the system directories stay, because the provider needs a shell. A judge that
    already knew an absolute path could follow it. What this removes is the ways to learn
    one from rig.
    """
    source = dict(os.environ if environ is None else environ)
    env = {k: source[k] for k in _ENV_KEEP if k in source}
    path = ([str(shim)] if shim is not None else []) + list(_SYSTEM_PATH)
    env["PATH"] = os.pathsep.join(path)
    return env

_VERDICT_RE = re.compile(r"(?i)^[\s*_`#>-]*(ASSERTS|DENIES|NEITHER)[\s*_`.!]*$")


def judge_prompt(summary: str, body: str) -> str:
    """The whole of what the judge sees. Two texts and one question about direction.

    TEXT A is deliberately first and deliberately unlabelled as to provenance: naming
    it "the reviewer's finding" invites the judge to grade the reviewer, and naming
    TEXT B "the planted defect" tells it there is an answer key to agree with.
    """
    return (
        "You are comparing two texts that describe the same part of a code change.\n"
        "\n"
        "TEXT A\n"
        "------\n"
        f"{body.strip()}\n"
        "\n"
        "TEXT B\n"
        "------\n"
        f"{summary.strip()}\n"
        "\n"
        "Decide how TEXT A relates to the specific problem stated in TEXT B.\n"
        "\n"
        "  ASSERTS - TEXT A reports the problem in TEXT B as a problem: something that\n"
        "            is wrong with the code and wants fixing. A problem reported as\n"
        "            something missing still counts as ASSERTS when the absence is the\n"
        "            problem: \"does not await\", \"never calls\", \"the ownership check is\n"
        "            gone\", \"no longer copies\" all report a defect, not its absence.\n"
        "  DENIES  - TEXT A says the code is all right in exactly the respect TEXT B\n"
        "            describes. Two forms count here, not one:\n"
        "              * it contradicts TEXT B outright - it does await, it does call\n"
        "                the check, the behaviour is unchanged and fine; and\n"
        "              * it describes the very mechanism TEXT B describes and then\n"
        "                excuses it - \"intentional\", \"by design\", \"by arrangement\",\n"
        "                \"accepted here\", \"nothing is broken\", \"no action required\",\n"
        "                \"recording it so nobody re-opens it\". Naming a mechanism and\n"
        "                waiving it is not reporting a problem. The question is not\n"
        "                whether TEXT A mentions the mechanism; it is whether TEXT A\n"
        "                says something is wrong.\n"
        "  NEITHER - TEXT A is about the same code or topic but does neither.\n"
        "\n"
        "Judge only what TEXT A claims. Do not judge whether it is well written, whether\n"
        "its severity is right, or whether it is factually correct about code you cannot\n"
        "see - a badly explained real problem is still ASSERTS. A confident severity\n"
        "label on a text that says no action is required does not make it ASSERTS.\n"
        "\n"
        "Give one short line of reasoning. Then put the verdict alone on the final\n"
        "line, as a single word: ASSERTS, DENIES, or NEITHER.\n"
    )


#: Derived from the prompt text, not maintained by hand. It is part of the ledger key, so
#: a reworded prompt invalidates every verdict it produced. An integer was tried first and
#: an attacker pointed out the obvious: edit the wording, forget the bump, and every stale
#: verdict is inherited in silence. A hash cannot be forgotten.
JUDGE_PROMPT_VERSION = hashlib.sha256(
    judge_prompt("<TEXT A>", "<TEXT B>").encode("utf-8")
).hexdigest()[:16]


def parse_verdict(out: str) -> str | None:
    """The last non-empty line, or nothing.

    Strict on purpose. A judge that rambled past its verdict, or hedged with two of
    them, has not answered; `None` routes that to `unadjudicated`, where it shows up
    as a gap in the measurement rather than as a detection either way. Scanning the
    whole output for the first verdict-shaped word would read the reasoning line
    ("this is not a DENIES because...") as the answer.
    """
    for line in reversed((out or "").splitlines()):
        if not line.strip():
            continue
        match = _VERDICT_RE.match(line)
        return match.group(1).upper() if match else None
    return None


def _normalize(text: str) -> str:
    """Whitespace-insensitive form for hashing, so reflowing a fixture is not a change."""
    return " ".join((text or "").split())


def ledger_key(
    case_id: str,
    violation_id: str,
    summary: str,
    body: str,
    provider: str,
    model: str | None,
    prompt_version: str = JUDGE_PROMPT_VERSION,
) -> str:
    """Content hash of everything that could change the verdict.

    `summary` and `body` are hashed as text rather than by id: a seed whose `summary`
    is rewritten is a different question, and inheriting the old answer for it would
    be exactly the stale-cache failure this ledger exists to make impossible.
    """
    # NUL as the separator, not a space: normalized text can contain spaces, and
    # ("a b", "c") must not hash the same as ("a", "b c").
    payload = "\x00".join([
        case_id, violation_id, _normalize(summary), _normalize(body),
        provider, model or "", prompt_version,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Ledger:
    """Append-only JSONL of judge verdicts, read into memory once.

    The raw provider output is stored beside the parsed verdict. A verdict that gets
    disputed later is then auditable without re-running the judge, which by then may
    answer differently.
    """

    def __init__(self, path: pathlib.Path | str | None):
        self.path = pathlib.Path(path) if path is not None else None
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path is not None and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = entry.get("key")
                if key:
                    self.entries[key] = entry

    def get(self, key: str) -> str | None:
        """A recorded verdict, re-derived from the recorded output.

        Reading the `verdict` field alone made the ledger a place to write scores by
        hand: an attacker appended thirteen lines carrying `"verdict": "ASSERTS"` with
        `returncode: 1` and `raw: "(never ran)"`, ran `--judge-offline`, and published
        86.7% detection without a single provider call. So the entry has to carry an
        answer a judge could actually have given, and the verdict has to be what parsing
        that answer produces. Forging one now means writing plausible judge output —
        which is still possible, but is no longer a one-field edit and leaves the
        fabricated text on the record.
        """
        entry = self.entries.get(key)
        if not entry or entry.get("returncode") != 0:
            return None
        verdict = entry.get("verdict")
        if verdict not in VERDICTS or parse_verdict(entry.get("raw") or "") != verdict:
            return None
        return verdict

    def digest(self) -> str | None:
        """sha256 of the ledger file as it stands, so a row can name the evidence it used."""
        if self.path is None or not self.path.exists():
            return None
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def put(self, key: str, verdict: str | None, record: dict[str, Any]) -> None:
        entry = {"key": key, "verdict": verdict, **record}
        self.entries[key] = entry
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One `os.write` to an O_APPEND descriptor rather than a buffered `open("a")`.
        # Two `score --judge` runs sharing a ledger can interleave a buffered write above
        # the pipe-buffer size, and a torn line is dropped silently by `json.JSONDecodeError`
        # on the next read — the verdict vanishes without becoming `unadjudicated`.
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(descriptor, line)
        finally:
            os.close(descriptor)


class Adjudicator:
    """Callable passed to `score_review` as its `adjudicate` hook.

    `detection_corpus` imports this module only from its CLI entry point, which is the
    composition root; the scoring functions take a plain callable, so the scorer stays
    pure, offline, and testable against a fake that answers from a dict —
    which is also what makes the calibration set runnable without a provider.
    """

    def __init__(
        self,
        provider: str = DEFAULT_JUDGE_PROVIDER,
        model: str | None = None,
        ledger: pathlib.Path | str | None = None,
        cfg: dict[str, Any] | None = None,
        offline: bool = False,
    ):
        if provider not in JUDGE_PROVIDERS:
            raise SystemExit(
                f"[ERROR] {provider!r} cannot be a drill judge; use one of "
                f"{', '.join(JUDGE_PROVIDERS)} (see JUDGE_PROVIDERS)"
            )
        self.provider = provider
        self.model = model
        self.ledger = Ledger(ledger)
        self.cfg = dict(cfg or {})
        if model:
            self.cfg.setdefault("model", model)
        # Long enough that a slow answer is an answer. A timeout is not a verdict, and
        # the pair it kills lands in `unadjudicated` — correct, but it costs a
        # measurement, so the budget is generous rather than tight.
        self.cfg.setdefault("timeout", 600)
        # An empty directory to start the judge in, so the answer key is not underfoot.
        # See the module docstring: `codex exec` inherits rig's cwd otherwise and can
        # read the corpus it is being asked about. Held on the instance so it lives
        # exactly as long as the judge does and is removed with it.
        self._workdir = tempfile.TemporaryDirectory(prefix="rig-judge-")
        self._shimdir = tempfile.TemporaryDirectory(prefix="rig-judge-bin-")
        # Assigned, not `setdefault`: a caller-supplied `cfg` must not be able to put the
        # judge back in rig's tree or hand it rig's environment. `setdefault` made the
        # blindness a property of who constructs the object rather than of the object.
        self.cfg["cwd"] = self._workdir.name
        # The empty directory alone was not enough: `PATH` reached the installed
        # plugin's copy of the corpus.
        shim = judge_shim(self.provider, pathlib.Path(self._shimdir.name) / "bin")
        self.cfg["env"] = judge_env(self.provider, shim=shim)
        if self.cfg.pop("secure_runtime", None):
            # The secure path dispatches through a pre-built launcher and never reads
            # `cfg["env"]` (`providers._dispatch_provider`), so honouring it here would
            # silently drop the scrub. Refuse rather than run unscrubbed.
            raise SystemExit("[ERROR] the drill judge does not run under secure_runtime; "
                             "its environment scrub would be silently dropped")
        #: With no provider allowed, a pair the ledger has never seen stays unjudged
        #: rather than being sent anywhere. `--judge-offline` replays a recorded run.
        self.offline = offline
        if offline and self.ledger.path is None:
            # A replay with nothing to replay from answers `None` to every pair and
            # produces a row that is guaranteed `adjudicated: false`. That is a
            # guaranteed-worthless run, and it is quiet — refuse it at the door.
            raise SystemExit("[ERROR] --judge-offline needs a --judge-ledger to replay; "
                             "without one every pair is unadjudicated")
        self.calls = 0
        self.cache_hits = 0

    def provenance(self) -> dict[str, Any]:
        """What produced these verdicts, for the drill row to carry.

        A scored row used to name no provider, no model, no prompt and no ledger, so a
        run of forty-five real judge calls and a hand-written JSONL produced rows that
        were identical field for field. `calls` against `cache_hits` also makes a replay
        legible: four hits and one call against a ledger that should have been empty is
        a question worth asking.
        """
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "ledger": str(self.ledger.path) if self.ledger.path else None,
            "ledger_sha256": self.ledger.digest(),
            "offline": self.offline,
            "calls": self.calls,
            "cache_hits": self.cache_hits,
        }

    def __call__(
        self, case: dict[str, Any], violation: dict[str, Any], finding: Any
    ) -> str | None:
        summary = (violation.get("summary") or "").strip()
        body = (getattr(finding, "body", "") or "").strip()
        if not summary or not body:
            # Nothing to compare. A seed with no direction-bearing summary is a corpus
            # defect, and answering it with a guess would hide that behind a number.
            return None
        key = ledger_key(
            case.get("id", ""), violation.get("id", ""), summary, body,
            self.provider, self.model,
        )
        cached = self.ledger.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if self.offline:
            return None

        prompt = judge_prompt(summary, body)
        self.calls += 1
        returncode, out = run_provider(self.provider, JUDGE_ROLE, prompt, self.cfg)
        verdict = parse_verdict(out) if returncode == 0 else None
        self.ledger.put(key, verdict, {
            "case": case.get("id"),
            "violation": violation.get("id"),
            "provider": self.provider,
            "model": self.model,
            "prompt_version": JUDGE_PROMPT_VERSION,
            "returncode": returncode,
            "raw": out,
        })
        return verdict


def make_adjudicator(
    provider: str | None,
    model: str | None = None,
    ledger: pathlib.Path | str | None = None,
    offline: bool = False,
) -> Callable[..., str | None] | None:
    """`None` when no judge was asked for — which the scorer reads as "not adjudicated"."""
    if not provider and not offline:
        return None
    return Adjudicator(
        provider=provider or DEFAULT_JUDGE_PROVIDER,
        model=model, ledger=ledger, offline=offline,
    )

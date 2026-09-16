"""What rig does when the console cannot encode what rig is about to say.

The defect, reproduced before the fix and driven here as a real process rather than as a
monkeypatched `print`:

    PYTHONIOENCODING=ascii python3 -c "
    from rig_workbench.workbench.state import warn
    warn('... still verifies is unknown — read it before deleting it')"
    → UnicodeEncodeError: 'ascii' codec can't encode character '—'

`sys.stdout` is opened with `errors="strict"`, so on a console whose encoding cannot
represent a character rig is about to print — `PYTHONIOENCODING=ascii`, a `LANG=C` CI
runner — `print` raises instead of printing. rig does not degrade; it dies at whichever
line first holds a non-ASCII character, and rig's prose is deliberately not ASCII.

That line is the worst case rather than an arbitrary one. `_set_unusable_key_aside`
renames an unusable provenance key to `.unusable` and *then* warns that it did so, so a
raise there leaves the file moved and the sentence unsaid, in the middle of `accept`.

**Driven through a real encoder, never through a stand-in.** Every test here either
spawns a process with `PYTHONIOENCODING` set, or writes into an `io.TextIOWrapper` opened
with a narrow encoding. A monkeypatched `print` would prove that the code takes a branch;
it would not prove that the branch it takes is the one a `cp932` or `ascii` encoder
actually forces, which is the whole of the defect.

**Both directions, because a fix at an output boundary can break the output.** The
degraded side is tested here, and so is the untouched side: on a console that can encode
it, every byte must be the byte that was written before. That half is asserted three ways
— `write_line` against `print` on the same stream, a hardened stream against a strict one,
and the whole `[WARN]` line compared to its literal bytes through a real process.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, subprocess_timeout

from rig_workbench import console, context_meter
from rig_workbench.workbench import state

#: The sentence from `state.py:792`, the one printed after the key has already been moved.
SET_ASIDE_WARNING = "... still verifies is unknown — read it before deleting it"

#: A line that is non-ASCII in two scripts at once. `warn` carries both in this tree: the
#: em dash is in every operator message, and `wb board` / `wb status` answer in Japanese.
MIXED = "鍵は .unusable へ移した — 消す前に読むこと"

#: Measured on a developer machine, as `conftest.subprocess_timeout` asks: the `-c` probes
#: cost 0.09-0.13s and the two `--help` probes 0.30s and 0.33s. Both land under the suite's
#: 30s floor; the numbers are recorded so a successor has something to raise rather than a
#: literal to guess at.
PROBE_MEASURED_SECONDS = 0.5
PROBE_TIMEOUT_SECONDS = subprocess_timeout(PROBE_MEASURED_SECONDS)


def _run(argv: list[str], *, encoding: str,
         extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """rig in a child process whose stdout/stderr really are that narrow.

    Bytes, not text: the point of the exercise is what reached the stream, so nothing is
    decoded on the way back. `PYTHONUTF8` is cleared because UTF-8 mode overrides
    `PYTHONIOENCODING` and would silently turn every probe here into a UTF-8 one — the
    suite's own `conftest` sets both for other tests, and an inherited `PYTHONUTF8=1`
    would make these pass without ever reaching an encoder that refuses anything.

    `extra_env` exists for the one probe that has to pin the child's *locale*, because what
    CPython resolves an unset error handler to depends on it.
    """
    env = dict(os.environ, PYTHONIOENCODING=encoding, COLUMNS="80",
               PYTHONPATH=os.pathsep.join(
                   p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
               RIG_NO_CONTEXT_METER="1", **(extra_env or {}))
    env.pop("PYTHONUTF8", None)
    return subprocess.run([sys.executable, *argv], cwd=str(REPO_ROOT), env=env,
                          capture_output=True, timeout=PROBE_TIMEOUT_SECONDS)


def _rendered(encoding: str, write) -> bytes:
    """The bytes `write` puts on a real `TextIOWrapper` opened at `encoding`.

    The wrapper is flushed rather than closed: closing it closes the `BytesIO` under it,
    and the bytes are the answer.
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding=encoding, newline="")
    write(stream)
    stream.flush()
    return buffer.getvalue()


def _warn_program(message: str) -> list[str]:
    return ["-c", "from rig_workbench.workbench.state import warn\n"
                  f"warn({message!r})\n"]


def test_the_warning_after_the_key_was_moved_survives_an_ascii_console() -> None:
    """The reproduction, unchanged, as a process. It exited non-zero and printed nothing.

    Three assertions rather than one, because "did not crash" is not the requirement: the
    operator has a key sitting in a `.unusable` file and needs the sentence that says so.
    So the exit status is clean, the sentence arrives on stdout, and the words on both
    sides of the character that could not be encoded are still there.
    """
    proc = _run(_warn_program(SET_ASIDE_WARNING), encoding="ascii")

    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert b"UnicodeEncodeError" not in proc.stderr
    assert proc.stdout == (
        b"[WARN] ... still verifies is unknown \\u2014 read it before deleting it\n")


def test_the_warning_is_byte_for_byte_unchanged_on_a_console_that_can_encode_it() -> None:
    """Property 1 at the process level: the UTF-8 console sees exactly what it always saw.

    The expectation is the literal line, encoded — not a re-derivation through the same
    code under test, which would agree with itself however wrong it was.
    """
    proc = _run(_warn_program(SET_ASIDE_WARNING), encoding="utf-8")

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == f"[WARN] {SET_ASIDE_WARNING}\n".encode("utf-8")


def test_a_stop_survives_a_narrow_stream_the_caller_supplied() -> None:
    """`die`, on a stderr that is not the process's own. The exit status travels with it.

    **The crash surface of this defect is stdout, and that was measured rather than
    assumed.** CPython opens the real `sys.stderr` with `backslashreplace` already —
    `PYTHONIOENCODING=ascii` gives `stdout ascii strict | stderr ascii backslashreplace`,
    and the `:strict` suffix does not move stderr either — so the 74 bare `print(...,
    file=sys.stderr)` sites in this tree were never dying. `die` and `reject` are spelled
    the same way as `warn` anyway, because `sys.stderr` is only *usually* that stream: a
    program that imported rig can have redirected it, which is what this does with a real
    `ascii` wrapper, and on the tree before the fix this raises `UnicodeEncodeError` out
    of `die` before it ever reaches its `sys.exit`.

    That last part is why the status is asserted next to the text. A `die` whose `print`
    raised never reached `sys.exit(ERROR)`, so what the caller read was whatever the
    unwinding produced: the encoder silently taking the exit-code contract with it.
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", newline="")

    with contextlib.redirect_stderr(stream):
        with pytest.raises(SystemExit) as stop:
            state.die(MIXED)
    stream.flush()

    assert stop.value.code == 2
    assert buffer.getvalue() == (
        b"[ERROR] \\u9375\\u306f .unusable \\u3078\\u79fb\\u3057\\u305f "
        b"\\u2014 \\u6d88\\u3059\\u524d\\u306b\\u8aad\\u3080\\u3053\\u3068\n")


def test_nothing_is_dropped_when_the_encoder_refuses_a_character() -> None:
    """Property 2, against a real `ascii` encoder: replaced, and visibly so.

    `ignore` would satisfy "did not crash" and fail this: it deletes the character, so a
    Japanese line degrades to an empty one and an operator cannot tell damage from a
    sentence that never had the word. What is asserted is that every character survives
    as *something* — the ASCII ones as themselves, the rest as an escape naming the
    codepoint — so the count of tokens is the count of characters.
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="ascii", newline="")

    console.write_line(MIXED, stream=stream)
    stream.flush()
    written = buffer.getvalue().decode("ascii")

    assert written.endswith("\n")
    assert written[:-1] == "".join(
        ch if ch.isascii() else f"\\u{ord(ch):04x}" for ch in MIXED)
    for word in (".unusable",):                     # the ASCII run is untouched
        assert word in written
    assert "?" not in written and "�" not in written


def test_only_what_the_encoder_refused_is_escaped() -> None:
    """The escapes come from the codec that objected, not from a codec chosen in advance.

    A `cp932` console — the one a Japanese Windows terminal actually has — can carry every
    kanji in this line and not the em dash. So the Japanese must arrive as `cp932` bytes
    and only the dash as an escape: degrading a line the console could have shown is the
    same loss as dropping it, just quieter. Hard-coding `"ascii"` in `degrade` passes every
    other test in this file, because every other one drives it at `ascii`; here it turns
    the whole line into escapes and fails.
    """
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp932", newline="")

    console.write_line(MIXED, stream=stream)
    stream.flush()
    written = buffer.getvalue()

    assert "鍵".encode("cp932") in written          # carried, not escaped
    assert b"\\u2014" in written                    # the one character cp932 refuses
    assert written.decode("cp932") == MIXED.replace("—", "\\u2014") + "\n"


def test_the_line_is_the_bytes_print_writes_when_the_encoder_accepts_it() -> None:
    """Property 1 at the mechanism: `write_line` is `print`, byte for byte.

    Compared against `print` itself on an identically opened stream rather than against a
    hand-written expectation, so the claim under test is the one the docstring makes —
    same text, same newline, same stream — and not a restatement of it.
    """
    texts = ["", "plain ascii", SET_ASIDE_WARNING, MIXED, "trailing spaces   ",
             "a line\nwith an embedded newline"]
    for text in texts:
        expected = _rendered("utf-8", lambda stream, t=text: print(t, file=stream))
        measured = _rendered("utf-8",
                             lambda stream, t=text: console.write_line(t, stream=stream))
        assert measured == expected, repr(text)


def test_hardening_changes_no_byte_a_utf8_stream_could_already_carry() -> None:
    """The other half of property 1: `harden` is a no-op on a console that can encode.

    An error handler is consulted only for a character the encoder refuses, so this is
    true by construction — which is exactly the kind of claim that is worth a measurement,
    because it is also what a change of *encoding* would look like from the inside until
    the day it did not. The encoding is asserted unmoved for the same reason.
    """
    corpus = [SET_ASIDE_WARNING, MIXED, "ASCII only", "emoji 🍣 and 한국어"]
    seen: dict[str, str] = {}

    def write_all(stream: io.TextIOWrapper) -> None:
        for text in corpus:
            print(text, file=stream)

    def harden_then_write_all(stream: io.TextIOWrapper) -> None:
        assert console.harden(stream) is True
        seen["errors"], seen["encoding"] = stream.errors, stream.encoding
        write_all(stream)

    strict = _rendered("utf-8", write_all)
    hardened = _rendered("utf-8", harden_then_write_all)

    assert seen == {"errors": console.REPLACEMENT, "encoding": "utf-8"}
    assert hardened == strict


def test_a_stream_that_cannot_be_reconfigured_is_reported_rather_than_raised() -> None:
    """Hardening must never become a new way for rig to fail before it has said anything.

    Both refusals are real rather than defensive: a `StringIO` (or any substitute a caller
    put in `sys.stdout`) has no `reconfigure` at all, and a stream whose handles have been
    closed — a daemon started with `>&-`, a detached service — raises `ValueError` from it.
    `harden` answers `False` to both, and the process carries on to print what it can.
    """
    assert console.harden(io.StringIO()) is False

    closed = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="")
    closed.close()
    assert console.harden(closed) is False

    console.harden_streams()          # whole-process call, with nothing raised


def test_an_error_handler_the_operator_named_is_left_alone() -> None:
    """`PYTHONIOENCODING=ascii:replace` is a choice, and rig does not overrule it.

    The output is `?`, not `\\u2014`: hardening replaces only the default that raises. A
    fix that reconfigured every stream regardless would silently change the output of a
    console somebody had already configured, which is the same class of surprise as the
    crash it is fixing.

    **Driven through a real entry point on purpose.** This assertion used to run a bare
    `-c` program, which reaches `write_line` but never `harden` — so it passed identically
    with the strict-only guard deleted, and the paragraph in `console.py` arguing for that
    guard had nothing holding it. `rig-wb --help` goes through `exitcodes.guard`, which is
    the only place the guard is consulted, and with those three lines removed this line
    comes back carrying an em dash instead of a `?`.
    """
    proc = _run(["-m", "rig_workbench.cli", "--help"], encoding="ascii:replace")

    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert b"quality-gated AI workbench" in proc.stdout
    assert b"?" in proc.stdout                      # the handler the operator named
    assert b"\\u2014" not in proc.stdout            # not the one rig would have chosen


def test_a_stream_that_reports_no_handler_is_hardened_rather_than_skipped() -> None:
    """The other half of the same guard, and the other way to get it wrong.

    `harden` reads `errors` and leaves anything that is not `"strict"` alone. An
    `io.TextIOBase` subclass reports `errors` as `None` rather than raising — which is
    what `context_meter._CountingStream` does while it wraps the real stdout for the
    length of a `wb` command — so reading `None` as "already handled" would skip exactly
    the process rig meters. The real wrapper is used here rather than a stand-in, because
    the `None` is a property of that class and a fake would be asserting my own
    assumption about it.
    """
    buffer = io.BytesIO()
    real = io.TextIOWrapper(buffer, encoding="ascii", newline="")
    wrapper = context_meter._CountingStream(real)

    assert wrapper.errors is None                   # the shape the guard has to survive
    assert console.harden(wrapper) is True
    assert real.errors == console.REPLACEMENT

    console.write_line(MIXED, stream=wrapper)
    wrapper.flush()
    assert b"\\u9375" in buffer.getvalue()          # degraded through the wrapper


@pytest.mark.parametrize(
    "argv, identifying",
    [
        pytest.param(["scripts/workbench.py", "--help"], b"scan-ja-prose",
                     id="scripts/workbench.py — the shim: 74 references in 13 files under commands/skills"),
        pytest.param(["-m", "rig_workbench.cli", "--help"], b"quality-gated AI workbench",
                     id="rig-wb — the installed entry point, through exitcodes.guard"),
        # Two of the six standalone scripts, driven for real. The census below proves the
        # call is *written* in all fourteen entries; it proves that by substring, so
        # commenting one call out and leaving the text behind passes it. These two run.
        # `prose_rhythm.py` is the one whose crash was measured on the base (10 references
        # in 8 files, and it was excluded by the count this rework replaced); `notify.py`
        # is the cheapest second, an argparse description with an em dash in it.
        pytest.param(["scripts/prose_rhythm.py", "README.ja.md"], b"prose-rhythm: README.ja.md",
                     id="scripts/prose_rhythm.py — standalone, package import on the entry path"),
        pytest.param(["scripts/notify.py", "--help"], b"Slack/Teams webhook notifications",
                     id="scripts/notify.py — standalone, argparse writing the stream itself"),
    ],
)
def test_a_process_entry_point_prints_its_help_instead_of_crashing(
        argv: list[str], identifying: bytes) -> None:
    """The two application points, each driven as the process an operator actually runs.

    `--help` is the probe because argparse writes it to the stream itself, before any rig
    code would get a chance to sanitise anything — on this tree both help texts carry the
    em dash, and both of these commands died on it. The identifying fragment is checked so
    that a command which exits 0 having printed nothing cannot pass.
    """
    proc = _run(argv, encoding="ascii")

    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert b"UnicodeEncodeError" not in proc.stderr
    assert identifying in proc.stdout
    assert b"\\u2014" in proc.stdout          # degraded, and visibly


def test_the_presenter_adapter_is_not_covered_outside_a_process_rig_started() -> None:
    """The boundary of this fix, recorded as a measurement rather than as a sentence.

    `ConsolePresenter` — the adapter the five migrated pillars send all 357 of their
    output calls through — is a bare `print`. In a rig process that is enough, because the
    stream underneath it has been hardened at the entry point. In a program that imported
    `rig_workbench` and called into it directly, nothing hardened anything and the adapter
    still raises.

    This test exists so that "covered by the entry point, not by the adapter" is checkable.
    It is not a statement that the behaviour is desirable. The place it closes for good is
    the `workbench` pillar's migration onto the `Presenter` port, after which the port has
    one implementation to fix instead of 729 `print` sites; the commit that closes it
    should delete this test and the paragraph in `rig_workbench/console.py` that names the
    same gap.
    """
    proc = _run(["-c", "from rig_workbench.ports.local import CONSOLE\n"
                       "CONSOLE.out('—')\n"], encoding="ascii")

    assert proc.returncode == 1
    assert b"UnicodeEncodeError" in proc.stderr


def test_a_step_check_is_not_reached_by_the_declaration() -> None:
    """The other boundary of the child-process half, recorded the way the Presenter one is.

    A recipe's `checks:` are run by `orchestrate/providers._run_step_checks` with
    `shell=True` and the output sent to `DEVNULL`, which `ProcessRunner` has neither of —
    a permanent exemption with a `noqa` and a reason at the site. So the declaration in
    `ports/local.py` never reaches them: a Python check that prints non-ASCII still dies
    under an ascii console, and the step records `ok: False` for a reason that is the
    operator's terminal rather than the check's subject.

    Recorded rather than fixed. Closing it means moving the check runner onto the port,
    which is a change to how checks are *run* — `shell=True`, the discarded output, and
    the exemption that exists for both — and not to how rig speaks. The commit that makes
    that move should delete this test and the paragraph in `rig_workbench/console.py`
    naming the same gap.
    """
    proc = _run(["-c", "import sys\n"
                       "from rig_workbench.orchestrate.providers import _run_step_checks\n"
                       "state = {}\n"
                       "_run_step_checks({'checks': [sys.executable + ' -c \"print(chr(0x2014))\"']},\n"
                       "                 state, {})\n"
                       "sys.exit(0 if state['checks'][0]['ok'] is False else 3)\n"],
                encoding="ascii")

    assert proc.returncode == 0, (proc.returncode, proc.stderr.decode("ascii", "replace"))


def test_every_process_entry_under_scripts_hardens_its_streams() -> None:
    """The criterion, checked against the tree rather than against a list somebody typed.

    The rule `console.py` states is a class, not a selection: **every** `__main__` block
    under `scripts/` carries the call. This test derives both sides from the tree, so a
    new entry point fails it until it does — which is the failure mode that was missed
    when the set was four hand-picked shims chosen by a reference count that turned out to
    be wrong (`prose_rhythm.py`, 10 references and reproducing the crash, was excluded by
    a count that read it as 1).

    The package's own `__main__` blocks are the named gap and are asserted as such, so
    that the number in `console.py` cannot drift away from the tree either.
    """
    def mains(directory: pathlib.Path) -> list[str]:
        return sorted(
            path.relative_to(REPO_ROOT).as_posix()
            for path in directory.rglob("*.py")
            if "__pycache__" not in path.parts
            and 'if __name__ == "__main__":' in path.read_text(encoding="utf-8")
        )

    def hardened(paths: list[str]) -> list[str]:
        return [p for p in paths
                if "harden_streams()" in (REPO_ROOT / p).read_text(encoding="utf-8")]

    script_entries = mains(REPO_ROOT / "scripts")
    package_entries = mains(REPO_ROOT / "rig_workbench")

    assert hardened(script_entries) == script_entries, (
        "every process entry under scripts/ must call harden_streams(); missing: "
        f"{sorted(set(script_entries) - set(hardened(script_entries)))}")
    assert hardened(package_entries) == [], (
        "the package's own __main__ blocks are the recorded gap — a module that now calls "
        "harden_streams() should be named in rig_workbench/console.py instead of appearing "
        f"here: {hardened(package_entries)}")
    # 15 since #624 added scripts/release_notes.py — the notes release.yml publishes
    # moved out of a YAML `run:` block so a test could reach them.
    assert len(script_entries) == 15, script_entries
    assert len(package_entries) == 17, package_entries


def test_a_child_process_is_told_what_encoding_the_pipe_is() -> None:
    """The third boundary: rig's own UTF-8 bytes, read by a child rig mis-instructed.

    `ProcessRunner.run` pins its pipes to UTF-8 in text mode; a Python child reads them
    through `PYTHONIOENCODING`, which it inherits from the operator's console. When those
    two disagree the child dies on rig's bytes — `sys.stdin.read()` raising
    `UnicodeDecodeError` before it has read a word — and nothing about that is the child's
    fault or the work's.

    The child here is a real interpreter reading a real pipe, and the text is checked
    back out of it, so a fix that merely stopped the crash while mangling the prompt
    would fail this.
    """
    payload = MIXED + "\n"
    proc = _run(["-c", "import sys\n"
                       "from rig_workbench.ports.local import SUBPROCESS\n"
                       "echo = [sys.executable, '-c', 'import sys; sys.stdout.write(sys.stdin.read())']\n"
                       f"r = SUBPROCESS.run(echo, input={payload!r})\n"
                       f"sys.exit(0 if (r.returncode, r.stdout) == (0, {payload!r}) else 3)\n"],
                encoding="ascii")

    assert proc.returncode == 0, (proc.returncode, proc.stderr.decode("ascii", "replace"))


def test_the_child_keeps_the_handler_the_operator_named_and_only_loses_the_codec() -> None:
    """Pinning the pipe's codec must not quietly pin its error handler as well.

    Declaring a bare `utf-8` declares `strict` with it, and that takes `surrogateescape`
    away from a child that had it by default — the handler that lets a program print a
    filename which is not valid UTF-8 instead of dying on it. Dying on it is the same
    false verdict this change exists to stop, arriving through the other door. So the
    codec is pinned and the handler is carried, which also means an operator who named one
    keeps it here exactly as they keep it on rig's own streams.

    Both halves are checked in one child because they are one branch: the handler under
    `ascii:replace` is the operator's, and under nothing at all it is the default CPython
    would have given.
    """
    report = ("import sys\n"
              "from rig_workbench.ports.local import SUBPROCESS\n"
              "probe = [sys.executable, '-c', 'import sys; print(sys.stdout.encoding, sys.stdout.errors)']\n"
              "sys.stdout.write(SUBPROCESS.run(probe).stdout)\n")

    named = _run(["-c", report], encoding="ascii:replace")
    assert named.returncode == 0, named.stderr.decode("ascii", "replace")
    assert named.stdout.strip() == b"utf-8 replace"

    # With nothing named, the handler is checked against the child rig never touched rather
    # than against `PIPE_ERRORS` — reading the constant under test would agree with itself,
    # and did: setting it to `"strict"` passed until this assertion compared the two
    # children. Under `LC_ALL=C` the invariant is that going through the port changes the
    # *codec* and nothing else.
    #
    # **The locale is pinned by this probe rather than inherited, because the claim is only
    # true for some locales and the host decides which it can produce.** CPython resolves an
    # unset handler to `surrogateescape` under `C`/`POSIX`/coercion targets and to `strict`
    # under a genuine UTF-8 locale, so on a machine that ships only the first kind this
    # assertion would read as universal while measuring one case — and on a machine with a
    # generated `en_US.UTF-8` the same line fails, correctly, because there the port
    # *overrides* `strict` on purpose (`ports/local.py` argues that override and states its
    # cost). Pinning `LC_ALL=C` makes the probe say which case it is testing, on every host.
    compare = ("import os, subprocess, sys\n"
               "from rig_workbench.ports.local import SUBPROCESS\n"
               "probe = [sys.executable, '-c', 'import sys; print(sys.stdout.errors)']\n"
               "clean = dict(os.environ); clean.pop('PYTHONIOENCODING', None)\n"
               "without_rig = subprocess.run(probe, capture_output=True, text=True,\n"
               "                             env=clean).stdout.strip()\n"
               "sys.stdout.write(without_rig + '|' + SUBPROCESS.run(probe).stdout.strip())\n")
    unset = _run(["-c", compare], encoding="",          # nothing named, as a `LANG=C` runner
                 extra_env={"LC_ALL": "C", "LANG": "C", "LC_CTYPE": "C"})
    assert unset.returncode == 0, unset.stderr.decode("ascii", "replace")
    without_rig, through_port = unset.stdout.decode("ascii").split("|")
    assert through_port == without_rig, (without_rig, through_port)
    assert through_port != "strict"                     # the handler that dies on a surrogate


def test_bytes_mode_declares_nothing_to_the_child() -> None:
    """The port claims UTF-8 in text mode only, so it speaks for the child in text mode only.

    In bytes mode rig decodes nothing and encodes nothing, so it has no encoding to
    declare on the child's behalf — and declaring one anyway would change what a child
    writes on a pipe whose bytes rig is hashing or framing (`eval/execution.py`,
    `eval/affected.py`). The operator's own value reaches the child untouched there.
    """
    report = ("import sys\n"
              "from rig_workbench.ports.local import SUBPROCESS\n"
              "prog = \"import os,sys; sys.stdout.write(os.environ.get('PYTHONIOENCODING','<unset>'))\"\n"
              "probe = [sys.executable, '-c', prog]\n"
              "text_mode = SUBPROCESS.run(probe).stdout\n"
              "bytes_mode = SUBPROCESS.run(probe, text=False).stdout.decode()\n"
              "sys.stdout.write(text_mode + '|' + bytes_mode)\n")

    proc = _run(["-c", report], encoding="ascii:replace")

    assert proc.returncode == 0, proc.stderr.decode("ascii", "replace")
    assert proc.stdout == b"utf-8:replace|ascii:replace"


def test_an_encoding_failure_in_a_provider_is_not_a_verdict() -> None:
    """What the mis-instructed child cost: a judgement rig never made.

    With `PYTHONIOENCODING=ascii` inherited, the mock provider exited 1 decoding the
    prompt, and the orchestrator wrote `generator failed (exit 1)` — `BLOCKED`, exit 1,
    which `exitcodes.py` defines as "rig judged this and the answer is no". The console's
    encoding decided a verdict about the work.

    Asserted at the provider boundary rather than by driving a whole run: the run takes
    twelve seconds and the thing under test is one call. `run_provider` returning 0 is
    the difference; the orchestrator's own mapping of a failed provider onto `BLOCKED` is
    older than this change and is left exactly as it was.
    """
    proc = _run(["-c", "import sys\n"
                       "from rig_workbench.orchestrate import providers\n"
                       f"code, _out = providers.run_provider('mock', 'generator', {MIXED!r}, {{}})\n"
                       "sys.exit(0 if code == 0 else 3)\n"],
                encoding="ascii")

    assert proc.returncode == 0, (proc.returncode, proc.stderr.decode("ascii", "replace"))


def test_a_line_is_dropped_where_print_drops_it() -> None:
    """`sys.stdout is None` — a pythonw process, a service with detached handles.

    `print` writes nothing and raises nothing there. `write_line` says it writes what
    `print` writes, so it has to be silent there too rather than raising `AttributeError`
    in the one environment where nobody is reading the output to notice.
    """
    with contextlib.redirect_stdout(None):
        console.write_line("nothing to write this to")
        print("and neither does print")


def test_hardening_is_done_once_and_not_again(monkeypatch) -> None:
    """A process is entered once, and a later pass must not reach a wrapped stream.

    `context_meter` swaps `sys.stdout` for a counting wrapper part-way through a `wb`
    command, and that wrapper reports no `errors` of its own — so a second
    `harden_streams()` would read "unknown, therefore strict" and reconfigure the real
    stdout underneath it, overriding a handler the operator had named. Today only the
    call order keeps that from happening, which is not a guarantee; this is.
    """
    monkeypatch.setattr(console, "_hardened", False)
    first = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="")
    monkeypatch.setattr(sys, "stdout", first)
    console.harden_streams()
    assert first.errors == console.REPLACEMENT

    second = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="")
    monkeypatch.setattr(sys, "stdout", second)
    console.harden_streams()
    assert second.errors == "strict"

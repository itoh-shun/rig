"""The five call shapes `ProcessRunner` has to serve, each against the real call it replaces.

`tests/test_ports.py` already holds the adapter to `subprocess.run` for the shape govern
brought (捕捉されたテキスト, `cwd`, `env`, `timeout`). Stage 3's second pillar
(`rig_workbench/eval/`, `rig_workbench/packs/`, `orchestrate/recipes.py`) brought three more,
measured over the AST before anything moved and recorded in the design brief's
「2 本目の柱は最初のポートに収まらない」:

* 25 of its 30 `subprocess.*` sites pass `encoding="utf-8", errors="replace"`, which is
  **not** what a bare `text=True` does — that decodes with the locale's encoding and *strict*
  errors, so on output that will not decode it raises `UnicodeDecodeError` where the pair
  returns U+FFFD (`test_text_mode_replaces_undecodable_bytes_as_errors_replace_does` runs both);
* 3 sites read **bytes** (`eval/execution.py:43`, `eval/gate.py:45`, `eval/affected.py:412`),
  and one of them frames binary by length, so decoding destroys the value rather than
  cosmetically altering it;
* 3 sites pass `input=`, two as `str` (`eval/runner.py:305`, `:409`) and one as `bytes`
  (`eval/affected.py:412`).

**Every test below launches a real process**, and asserts the adapter's `CompletedProcess`
equals what `subprocess.run` spelled the way that pillar spells it today returns — same
`returncode`, same `stdout`, same `stderr`, same type. Asserting that the adapter "passes the
right keywords" would pass just as well against an adapter that passed them to nothing, and a
check that cannot fail is the thing this repository measures other repositories for. The one
test that does read keywords (`test_text_mode_hands_subprocess_the_decoding_the_sites_spell`)
reads them off a spy that still runs the real call and still compares its result; it is there
for what output alone cannot pin down — which *error handler* produced a given string, since
`replace` and `ignore` and `surrogateescape` differ only on the bytes a fixture happens to hold.

The undecodable-byte fixture is the point of `errors="replace"`, so it is not optional here:
a process whose stdout is not valid utf-8 is the whole of the difference between the two
spellings, and it is not a hypothetical one — `git` emits it as soon as a repository holds a
path or an author name written in another encoding.
"""

import inspect
import subprocess
import sys

import pytest

from rig_workbench.ports import ProcessRunner
from rig_workbench.ports.local import SUBPROCESS

# A child that writes bytes that are not valid utf-8 (a lone 0xFF, then a truncated
# two-byte sequence), a well-formed non-ascii string, a line on stderr, and exits 3.
# `sys.stdout.buffer` is used deliberately: the child must write bytes we chose, not
# whatever its own text layer would encode.
_SPEAK_INVALID = (
    "import sys;"
    " sys.stdout.buffer.write(b'head\\xff mid \\xe3\\x81 tail \\xe6\\x97\\xa5');"
    " sys.stdout.buffer.flush();"
    " sys.stderr.buffer.write(b'warn \\xfe');"
    " sys.stderr.buffer.flush();"
    " sys.exit(3)"
)

# A child that reads stdin and answers with a length-framed echo — the shape
# `eval/affected.py:412` reads back from `git cat-file --batch`, where the framing is
# what makes text-mode decoding destructive rather than merely lossy.
_FRAME_STDIN = (
    "import sys;"
    " data = sys.stdin.buffer.read();"
    " sys.stdout.buffer.write(len(data).to_bytes(4, 'big') + data + b'\\xff\\x00');"
    " sys.stdout.buffer.flush()"
)

# A child that echoes its decoded stdin, as `eval/runner.py:305` feeds a prompt to a provider.
_ECHO_STDIN = "import sys; sys.stdout.write(sys.stdin.read().upper())"


def _argv(script: str) -> list[str]:
    return [sys.executable, "-c", script]


# ── the reference calls: today's arguments, spelled as the call sites spell them ──


def _raw_govern_text(argv, **kwargs):
    """`govern/identity.current_actor` and `govern/cli._head`: captured, `text=True`."""
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def _raw_pillar_text(argv, **kwargs):
    """The 25 text sites, e.g. `eval/gate.py:55` and `packs/publisher.py:423`."""
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def _raw_bytes(argv, **kwargs):
    """`eval/execution.py:43`, `eval/gate.py:45`, `eval/affected.py:412`: no text at all."""
    return subprocess.run(argv, capture_output=True, **kwargs)


def _same(got, raw) -> None:
    """Same result, and the same kind of result — `str` and `bytes` compare unequal anyway,
    but a type that changed under a caller reading `.stdout.strip()` is the failure worth
    naming rather than inferring from a mismatch."""
    assert type(got.stdout) is type(raw.stdout)
    assert type(got.stderr) is type(raw.stderr)
    assert (got.returncode, got.stdout, got.stderr) == (raw.returncode, raw.stdout, raw.stderr)


# ── shape 1: plain text, the shape govern already had ────────────────────────


def test_text_mode_returns_what_the_govern_shape_returns():
    """The swap the first pillar made must keep working; clean utf-8 decodes identically."""
    argv = _argv("import sys; sys.stdout.write('user.name is alice'); sys.exit(0)")
    got = SUBPROCESS.run(argv)
    _same(got, _raw_govern_text(argv))
    assert isinstance(got.stdout, str) and got.stdout.strip() == "user.name is alice"


# ── shape 2: text with encoding/errors, the shape the second pillar spells ────


def test_text_mode_returns_what_the_pillar_two_shape_returns():
    argv = _argv("import sys; sys.stdout.write('変更: src/日本語.py'); sys.exit(0)")
    got = SUBPROCESS.run(argv)
    _same(got, _raw_pillar_text(argv))
    assert got.stdout == "変更: src/日本語.py"


def test_text_mode_replaces_undecodable_bytes_as_errors_replace_does():
    """The case the pair exists for: stdout that is not valid utf-8.

    The port used to promise a bare `text=True`, and that promise cannot be kept on these
    bytes at all: strict decoding raises out of the call. The pair the call sites spell
    returns U+FFFD and lets the caller read `returncode`, which is what they do with it.
    """
    argv = _argv(_SPEAK_INVALID)
    got = SUBPROCESS.run(argv)
    raw = _raw_pillar_text(argv)
    _same(got, raw)
    assert got.returncode == 3
    # And the spelling the port used to carry does not merely differ, it refuses: a bare
    # `text=True` decodes with strict errors, so these bytes end the call instead of the run.
    with pytest.raises(UnicodeDecodeError):
        _raw_govern_text(argv)
    # Non-vacuous: the fixture really did produce undecodable bytes, so `replace` is doing
    # something. Three of them: the lone 0xFF, the truncated 0xE3 0x81, and 0xFE on stderr.
    assert got.stdout.count("�") == 2 and got.stderr.count("�") == 1
    # And the decodable parts survive untouched.
    assert got.stdout.startswith("head") and got.stdout.endswith("日")


# ── shape 3: bytes ───────────────────────────────────────────────────────────


def test_bytes_mode_returns_the_bytes_the_process_wrote():
    argv = _argv(_SPEAK_INVALID)
    got = SUBPROCESS.run(argv, text=False)
    raw = _raw_bytes(argv)
    _same(got, raw)
    assert isinstance(got.stdout, bytes)
    assert got.stdout == b"head\xff mid \xe3\x81 tail \xe6\x97\xa5"
    assert got.stderr == b"warn \xfe" and got.returncode == 3


def test_the_mode_switch_is_real_and_not_a_no_op():
    """The two modes must disagree on this fixture, or every test above is measuring one path.

    `text=False` keeps the bytes the process wrote; `text=True` cannot, because two of them
    are not a character. If these ever came back equal, the switch would be decorative.
    """
    argv = _argv(_SPEAK_INVALID)
    as_bytes = SUBPROCESS.run(argv, text=False).stdout
    as_text = SUBPROCESS.run(argv).stdout
    assert as_bytes != as_text.encode("utf-8")
    assert b"\xff" in as_bytes and "�" not in as_bytes.decode("utf-8", "surrogateescape")


# ── shape 4: input= as str ───────────────────────────────────────────────────


def test_input_as_str_matches_the_shape_eval_runner_uses():
    """`eval/runner.py:305` and `:409`: a prompt in, captured text out, in text mode."""
    argv = _argv(_ECHO_STDIN)
    payload = "review this diff\nplease\n"
    got = SUBPROCESS.run(argv, input=payload)
    _same(got, _raw_pillar_text(argv, input=payload))
    assert got.stdout == payload.upper()


def test_input_none_is_the_same_call_as_no_input_at_all():
    """The default must not quietly open a pipe on stdin the old call left alone."""
    argv = _argv("import sys; sys.stdout.write(str(sys.stdin.read() == ''))")
    _same(SUBPROCESS.run(argv, input=None), _raw_pillar_text(argv))


# ── shape 5: input= as bytes ─────────────────────────────────────────────────


def test_input_as_bytes_matches_the_shape_eval_affected_uses():
    """`eval/affected.py:412`: object ids in as ascii bytes, length-framed binary out.

    Both halves are bytes and the framing is a length prefix, so this is the site that could
    not have been served by a text-only port at all.
    """
    argv = _argv(_FRAME_STDIN)
    payload = b"7f3a\n9c01\n"
    got = SUBPROCESS.run(argv, input=payload, text=False)
    raw = _raw_bytes(argv, input=payload)
    _same(got, raw)
    assert got.stdout == len(payload).to_bytes(4, "big") + payload + b"\xff\x00"


def test_bytes_mode_rejects_a_str_input_exactly_as_subprocess_does():
    """The caller matches `input` to `text`, as `subprocess.run` makes it. Same failure."""
    argv = _argv(_ECHO_STDIN)
    with pytest.raises(TypeError):
        SUBPROCESS.run(argv, input="not bytes", text=False)
    with pytest.raises(TypeError):
        _raw_bytes(argv, input="not bytes")


# ── the two things output cannot show ────────────────────────────────────────


def test_text_mode_hands_subprocess_the_decoding_the_sites_spell(monkeypatch):
    """`encoding="utf-8", errors="replace"`, not a bare `text=True` and not another handler.

    That the pair is not a bare `text=True` is already shown on output, one test above. What
    output cannot show is *which* handler replaced the byte: `ignore` would drop it, and
    `surrogateescape` — which `eval/affected.py:381` uses deliberately — would keep it as a
    lone surrogate, and either could pass a fixture chosen to suit it. So the spy reads the
    keywords, and then calls the real `subprocess.run`, so the call under inspection is still
    a real process and its result is still compared.
    """
    seen: list[dict] = []
    real = subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    argv = _argv(_SPEAK_INVALID)

    text_result = SUBPROCESS.run(argv)
    assert seen[-1]["encoding"] == "utf-8" and seen[-1]["errors"] == "replace"
    assert "text" not in seen[-1] and seen[-1]["capture_output"] is True
    assert isinstance(text_result.stdout, str)

    bytes_result = SUBPROCESS.run(argv, text=False)
    assert not {"encoding", "errors", "text"} & set(seen[-1])
    assert isinstance(bytes_result.stdout, bytes)


def test_the_protocol_and_the_adapter_declare_the_same_run():
    """The overloads are the port's claim about the return type; the adapter has to hold it.

    A protocol that grew a parameter the adapter does not take would still satisfy
    `isinstance` (a `runtime_checkable` Protocol checks names, not signatures), so the
    agreement is asserted here instead of assumed there.
    """
    assert inspect.signature(ProcessRunner.run) == inspect.signature(SUBPROCESS.run.__func__)
    parameters = inspect.signature(ProcessRunner.run).parameters
    assert [p.name for p in parameters.values()] == [
        "self", "argv", "cwd", "env", "timeout", "input", "text"]
    assert parameters["text"].default is True and parameters["input"].default is None
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               for name, p in parameters.items() if name not in ("self", "argv"))

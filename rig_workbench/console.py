"""What happens when rig's words meet an encoder that cannot carry them.

rig's prose is deliberately not ASCII. 202 files under `rig_workbench/` and `scripts/`
hold non-ASCII string constants, the Japanese output is product surface, and the em dash
is in the house style of every docstring and operator message here. None of that is a
defect. The defect is what Python does with it on a console that cannot encode it:
`sys.stdout` is opened with `errors="strict"`, so `print` **raises** `UnicodeEncodeError`
rather than printing something. On a `PYTHONIOENCODING=ascii` console, or a
`LANG=C` CI runner, rig does not degrade — it crashes, mid-command, at whichever line
happened to hold the character.

The worst case is not cosmetic, and it is why this module exists rather than a
`.replace()` in the one place it was first noticed. `workbench/state.py`'s
`_set_unusable_key_aside` renames an unusable provenance key to `.unusable` and *then*
warns the operator that it did so. The rename has already happened when the warning is
printed. A raise there leaves a key moved aside and no sentence saying so, in the middle
of `accept`.

The policy
----------

**`backslashreplace`, everywhere, for every stream rig writes a word to.** The four
handlers that could stand here are not equivalent for the operator reading the result:

* `strict` is today's behaviour: nothing is printed and the command dies. The sentence is
  lost *and* the work stops.
* `ignore` deletes the character. `... still verifies is unknown  read it before deleting
  it` reads as a typo, not as damage, and a Japanese line degrades to an empty one. An
  operator cannot tell a dropped character from a sentence that never had one.
* `replace` writes `?` (or U+FFFD). Better — something is visibly missing — but every
  lost character looks alike, so `日本語` and `한국어` both come back as `???` and the
  line cannot be recovered or grepped.
* `backslashreplace` writes `\\u2014`, `\\u65e5\\u672c\\u8a9e`. Ugly, and deliberately so:
  it is unambiguous (it can only have come from a replacement), it is reversible (the
  codepoint is right there, and `"...".encode().decode("unicode_escape")` reads it back),
  and it never shortens a word into a different word.

Property 2 of this fix is "nothing silently dropped", and `backslashreplace` is the only
one of the four that satisfies it.

It is also not a convention invented here: it is what CPython already gives `sys.stderr`.
Measured, because the number it produces belongs in the coverage claim — under
`PYTHONIOENCODING=ascii` a process reports `stdout ascii strict | stderr ascii
backslashreplace`, and the `:strict` suffix does not move stderr either. So the defect's
crash surface is **stdout**: 655 of this tree's 729 bare `print` sites and 349 of the 357
`Presenter` calls, with the other 74 + 8 writing to a stream that already degrades. What
this module does is make stdout behave the way stderr has all along.

Byte-identity, which is property 1
----------------------------------

Nothing here changes a single byte on a console that *can* encode what rig prints. Both
halves are no-ops in that case by construction, not by promise:

* `harden` changes the stream's **error handler** and never its encoding. An error
  handler is consulted only for a character the encoder refuses, so on a UTF-8 console —
  where no character is refused — a hardened stream and a strict one emit the same bytes.
* `write_line` writes the line exactly as `print` does and only touches the text after
  the write has already raised `UnicodeEncodeError`, which on a UTF-8 console it does not.

Where this is applied, and where it is not
------------------------------------------

Two application points, because they close different holes and neither closes both.

1. **`harden_streams()` at a process entry**, and the rule is a whole class rather than a
   list: `exitcodes.run_guarded`, which every installed console script goes through, plus
   **every `__main__` block under `scripts/`** — all fourteen, counted from the tree, not
   the four that a hand-count of `python3 scripts/...` references once made look busiest.
   That count was wrong — it matched one spelling of the path and missed the rest, reading
   `ja_textlint` as 1 reference where the tree holds 7 and `prose_rhythm` as 1 where it
   holds 10 — and the wrongness is the argument for the rule:
   a threshold has to be re-measured to stay true, and "every process entry under
   `scripts/`" does not. `tests/test_console_encoding.py` derives the same set from the
   tree and fails when a new entry point does not carry the call.

   One call covers *every* output site in that process: the 729 bare `print` calls in the
   unmigrated pillars, the 357 `Presenter` calls in the migrated ones (`ConsolePresenter`
   is a `print` too), argparse's own `--help` — everything, including code written after
   this module. What it cannot cover is a process rig did not start: a library caller who
   imports `rig_workbench` and never calls a `main` keeps whatever streams their own
   program opened.

2. **`write_line` at the call site**, used today by `workbench/state.py`'s three
   last-resort speech functions (`die`, `reject`, `warn`). These are called from 37
   modules and reached by library callers directly, and one of them is the set-aside
   warning above, where the filesystem has already been changed by the time the sentence
   is printed. That sentence must not depend on somebody having remembered to harden the
   stream, so it does not.

The other 726 bare `print` sites, and `ConsolePresenter` with them, are covered by (1) and
not by (2): in a rig process they degrade, in a library caller's process they still raise.
The 17 `__main__` blocks *inside* the package are the other side of that line — measured
still crashing under an ascii console (`python -m rig_workbench.govern.cli --help`,
`python -m rig_workbench.orchestrate.cli`), invoked as a process by nothing in `commands/`,
`skills/`, `packs/`, `agents/` or `hooks/` (0 references each), and reached in every path
rig's own surface uses through the guarded `rig-wb`. What they are missing first is
`exitcodes.guard` — their exit codes, not their encoding — and that is the change that
should give them this one, rather than a fifteenth copy of the call.

The place the first gap closes for good is the `workbench` pillar's migration onto the
`Presenter` port, where those sites stop being `print` at all.

A third boundary, outside this module but part of the same defect: rig writes UTF-8 into a
child process's pipe and, through an inherited `PYTHONIOENCODING`, used to tell that child
to read it as something else. A provider handed a prompt with an em dash in it exited 1 on
`sys.stdin.read()`, and the orchestrator recorded that as a failed generator — the
operator's console deciding a verdict about the work. That half is fixed where the pipe's
encoding is chosen, in `ports/local.py`'s `SubprocessRunner.run`, and not here. It pins the
codec and *carries* the error handler rather than replacing it, which is the same
distinction this module draws: taking a child's `surrogateescape` away would have killed
the same class of run through the other door, on a child printing a filename that is not
valid UTF-8.

It does not reach every child. `orchestrate/providers.py`'s step `checks:` run outside the
port (`shell=True`, output to `DEVNULL`, with a `noqa` and a reason at the site), so a
Python check that prints non-ASCII still exits 1 under an ascii console. That one is named
and not fixed here: it needs the check runner to go through the port, which is a change to
how checks are run and not to how rig speaks.

This module imports `sys` and nothing else. It is below everything, including
`exitcodes`, and it must stay that way: a module that rig calls in order to be able to
speak cannot be one that has opinions about the rest of rig.
"""

from __future__ import annotations

import sys
from typing import TextIO

#: The one replacement policy, named once. See the module docstring for why it is this
#: handler and not `replace` or `ignore`.
REPLACEMENT = "backslashreplace"


def harden(stream: object) -> bool:
    """Swap one stream's strict encoder for the degrading one. Returns whether it did.

    Idempotent and best-effort, in both cases on purpose:

    * A stream whose `errors` is already something other than `"strict"` is left alone.
      An operator who wrote `PYTHONIOENCODING=ascii:replace` named a handler, and rig
      overriding a named choice would be the same kind of surprise this module exists to
      remove. Only the default that raises is replaced. A stream that reports no `errors`
      at all — `io.TextIOBase` subclasses report `None`, which is what
      `context_meter._CountingStream` does while wrapping the real stdout — is treated as
      unknown-and-therefore-strict and hardened, because the cost of being wrong that way
      is a handler swap and the cost of being wrong the other way is the crash.

    * Anything that goes wrong is swallowed, and the two ways it can go wrong take the
      same path on purpose: a stream with no `reconfigure` at all (a `StringIO`, a
      substitute a caller put in `sys.stdout`) raises `AttributeError` from the call, and a
      closed or detached one raises `ValueError`. An explicit `callable()` check ahead of
      the `try` was removed because it decided nothing the `except` did not already decide
      — a mutation that deleted it left every test passing, which is the definition of a
      line that is not doing anything. A helper whose whole purpose is to stop rig failing
      on its own output must never become a new way for rig to fail.
    """
    errors = getattr(stream, "errors", None)
    if isinstance(errors, str) and errors != "strict":
        return False
    try:
        stream.reconfigure(errors=REPLACEMENT)
    except Exception:
        return False
    return True


#: Set by the first `harden_streams()` in this process. Entering a process happens once,
#: and so does this: a later call must not reach a stream that something has since wrapped.
#: `context_meter` swaps `sys.stdout` for a counting wrapper part-way through `wb` commands,
#: and a wrapper reports no `errors` of its own — so a second pass would read "unknown,
#: therefore strict" and reconfigure the real stdout underneath it, overriding a handler the
#: operator may have named. Today the order happens to keep that unreachable; this makes it
#: so regardless of order.
_hardened = False


def harden_streams() -> None:
    """Both of rig's output streams, at a process entry point. Idempotent per process.

    Call it before anything is parsed or printed: argparse writes `--help` and its usage
    errors straight to these streams, and rig's own `--help` carries the em dash and the
    Japanese that started this.

    **`sys.stdin` is deliberately not included, and the asymmetry is the point.** An error
    handler on an input stream does not degrade rig's words, it rewrites the operator's:
    `--goal-stdin` fed through a narrow console would land in `task.json` with the parts
    the console could not decode replaced, and every later reader would treat the altered
    text as what was asked for. A crash there is the honest answer, and it already has a
    correct one — `exitcodes.guard` reports a `UnicodeDecodeError` as ERROR (2), "rig could
    not produce an answer", which is exactly what it is.
    """
    global _hardened
    if _hardened:
        return
    _hardened = True
    harden(sys.stdout)
    harden(sys.stderr)


def degrade(text: str, encoding: str) -> str:
    """`text` with every character `encoding` refuses spelled as `\\uXXXX`.

    The encoding is the one the failed write reported (`UnicodeEncodeError.encoding`), not
    one guessed from the stream, so the escapes match the encoder that actually objected —
    and, because that encoder just ran, it is a codec that exists and is a text codec. Two
    guards that used to stand here (a `LookupError` arm for an unknown codec name, a
    `UnicodeError` arm for a round trip that would not close) are gone for that reason: a
    sweep of all 98 registered codec names found the only `LookupError` raisers to be the
    `base64_codec`/`hex_codec` transforms, which cannot be a text stream's encoding, and no
    codec was found that reaches the other. Re-decoding through the same codec is what
    turns the escape *bytes* back into text the stream will then accept.
    """
    return text.encode(encoding, REPLACEMENT).decode(encoding, "replace")


def write_line(text: str = "", *, stream: TextIO | None = None) -> None:
    """One line, as `print(text, file=stream)` writes it — and printed even if it cannot.

    The happy path *is* the old behaviour: the same text, the same trailing newline, on
    the same stream, resolved at call time exactly as `print` resolves `sys.stdout`. The
    only difference is the second attempt, which the encoder's refusal is the sole way to
    reach.

    Written in one `write` rather than `print`'s two (the text, then the newline) so that
    a refusal cannot leave half a line behind: `TextIOWrapper.write` encodes the whole
    string before it emits anything, so nothing at all reaches the stream when it raises
    — measured, and the reason the fallback can simply write the line again.
    """
    out = sys.stdout if stream is None else stream
    if out is None:
        # `print` writes nothing and raises nothing when `sys.stdout` is None — a pythonw
        # process, a service started with its handles detached. Saying "as `print` writes
        # it" and then raising `AttributeError` there would make the claim above false in
        # the one environment nobody is watching the output of.
        return
    try:
        out.write(text + "\n")
    except UnicodeEncodeError as exc:
        out.write(degrade(text, exc.encoding) + "\n")

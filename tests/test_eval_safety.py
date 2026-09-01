"""What `unsafe_path_reason` refuses, and what it must stop refusing.

The doubled-separator branch had no test at all before this file: five references to
these functions existed in `tests/`, every one about the secret-value rule. So the
branch that decides whether a UNC path may be recorded was free to be narrowed or
widened without anything noticing, and it was wrong in a way nothing reported —
`\\s` in a `regex:` check was refused after a quote and accepted after a caret,
because the old branch required a prefix character and a path does not become one by
virtue of what precedes it (#563).

The true-positive cases here are the load-bearing half. A narrowing that removed them
would look exactly like a narrowing that fixed the false positives.
"""

import sys

import pytest

sys.path.insert(0, ".")

from rig_workbench.eval.safety import unsafe_path_reason, unsafe_text_reason  # noqa: E402


REFUSED = [
    ("unc backslash", "see \\\\server\\share\\file", "absolute path"),
    ("unc forward slash", "see //server/share/file", "absolute path"),
    ("unc host at end of value", "see \\\\fileserver", "absolute path"),
    ("unc host mid sentence", "copy from \\\\fileserver\\ now", "absolute path"),
    ("absolute posix", "open /etc/passwd", "absolute path"),
    ("windows drive backslash", "open C:\\Windows\\x", "absolute path"),
    ("windows drive slash", "open C:/Windows/x", "absolute path"),
    ("home relative", "open ~/.ssh/id_rsa", "home-relative absolute path"),
    ("file uri", "open file:///etc/passwd", "file URI"),
    ("file uri percent encoded", "open file%3A///etc/passwd", "file URI"),
    ("traversal", "open ../../etc/passwd", "path traversal"),
    ("control character", "a\x01b", "control character"),
    ("unicode format control", "a\u200eb", "Unicode format control"),
]

ACCEPTED = [
    # The case this issue was found on: a `regex:` check whose `\s` is JSON-escaped,
    # sitting after a quote.
    ("regex escape after a quote", '{"c":["regex:\\"lens\\"\\\\s*:\\\\s*\\"x\\""]}'),
    # The same two backslashes after a caret. This one shipped and passed all along,
    # which is what made the old rule's verdict a function of punctuation.
    ("regex escape after a caret", '{"c":["regex:^(?=[^\\\\n]*cache)[^\\\\n?]+[?]$"]}'),
    ("escaped crlf", 'candidate.replace("\\r\\n", "\\\\n")'),
    ("escaped hex escape", 'chunks.append(f"\\\\x{cp:04x}")'),
    ("escaped bracket", 'hdr = "^## \\\\[" ver "\\\\]"'),
    ("escaped quote", 'os.fsdecode(item).replace("\\\\", "/")'),
    # rig's own URI scheme for wiki material, which appears in pack assets — the text
    # this scanner reads.
    ("pack uri", "wiki: pack://project/company-security"),
    ("pack uri in a sentence", "material addressed as `pack://<scope>/<id>/x`"),
    ("https url", "see https://example.com/a/b"),
    ("repository relative path", "skills/engine/facets/policies/risk-based-testing.md"),
]


@pytest.mark.parametrize("name,value,reason", REFUSED, ids=[c[0] for c in REFUSED])
def test_a_path_that_leaves_the_tree_is_refused(name, value, reason):
    assert unsafe_path_reason(value) == reason


@pytest.mark.parametrize("name,value", ACCEPTED, ids=[c[0] for c in ACCEPTED])
def test_an_escape_that_is_not_a_path_is_accepted(name, value):
    assert unsafe_path_reason(value) is None


def test_the_verdict_does_not_depend_on_what_precedes_the_escape():
    """The defect itself, pinned as a property rather than as two examples.

    The old branch required one of `[\\s'\"=(\\[{,:]` before the doubled separator, so
    the same two backslashes were refused after a quote and accepted after a caret.
    Neither verdict was about paths.
    """
    for escape in ("\\\\s*", "\\\\n", "\\\\d+", "\\\\."):
        verdicts = {prefix: unsafe_path_reason(f"{prefix}{escape}")
                    for prefix in ('"', "^", "[", " ", "=", "x")}
        assert len(set(verdicts.values())) == 1, (escape, verdicts)
        assert set(verdicts.values()) == {None}, (escape, verdicts)


def test_a_real_unc_path_is_refused_wherever_it_appears():
    """The same property in the other direction: prefix must not rescue a real path."""
    for prefix in ('"', "^", "[", " ", "=", "x"):
        assert unsafe_path_reason(f"{prefix}\\\\server\\share") == "absolute path"


def test_text_scanning_keeps_everything_path_scanning_refuses():
    """`unsafe_text_reason` is `unsafe_path_reason` plus the secret rules, and the
    narrowing must not have cost it any of the first half."""
    for _name, value, reason in REFUSED:
        assert unsafe_text_reason(value) == reason
    assert unsafe_text_reason("sk-AbCdEf0123456789") == "secret-like value"
    assert unsafe_text_reason("password: hunter2") == "secret-like assignment"

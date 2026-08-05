import pytest

from rig_workbench.eval.runner import _check


# Formatting tolerance of the deterministic checker, asserted against spec strings
# built inline. Nothing here reads a pack, a manifest or a bundled eval case: the
# behaviour under test belongs to _check, so it must survive any pack coming or going.
#
# Two spec shapes are exercised, both drawn from rig's review-verdict contract
# (skills/rig/facets/output-contracts/review-verdict.md):
#   LABELLED_FIELD — a line-anchored `判定:` label whose value may be spaced,
#                    backtick-wrapped, or pushed onto the following line
#   VERDICT_TAIL   — the terminal `判定:` / `確信度:` pair, which must end the output

LABELLED_FIELD = "regex:(?m)^判定:[ \t]*(?:\n[ \t]*)?`?APPROVE`?[ \t]*$"
VERDICT_TAIL = (
    "regex:判定: (APPROVE|REJECT|APPROVE_WITH_CONDITIONS)"
    "\n確信度: (高|中|低)\n?$"
)


# ── 1. labelled field: whitespace and backtick tolerance ─────────────────────

@pytest.mark.parametrize("output", [
    "判定: APPROVE\n",
    "判定: APPROVE",           # no trailing newline
    "判定:APPROVE\n",          # no separating space
    "判定:   APPROVE\n",       # widened space
    "判定: APPROVE  \n",       # trailing spaces
    "判定:\tAPPROVE\t\n",      # tabs on both sides
    "判定: `APPROVE`\n",       # backtick-wrapped value
    "判定:\nAPPROVE\n",        # value pushed onto the next line
    "判定:\n  `APPROVE`  \n",  # next line, indented and backtick-wrapped
    "根拠:\n1. a — `src/a.py:1`\n判定: APPROVE\n確信度: 高\n",  # mid-document
])
def test_labelled_field_spec_tolerates_spacing_and_backtick_variants(output):
    assert _check(LABELLED_FIELD, output, 0)["status"] == "pass"


@pytest.mark.parametrize("output", [
    "判定: XAPPROVE\n",        # value glued to a prefix
    "判定: APPROVEX\n",        # value glued to a suffix
    "判定: APPROVE 補足\n",     # trailing prose on the value line
    "判定: ``APPROVE``\n",     # doubled backticks are not tolerated
    "判定:\n\nAPPROVE\n",      # only one intervening newline is allowed
    "# 判定: APPROVE\n",       # label not at the start of a line
    "承認: APPROVE\n",         # different label
    "判定: REJECT\n",          # different value
])
def test_labelled_field_spec_rejects_altered_labels_and_decorated_values(output):
    assert _check(LABELLED_FIELD, output, 0)["status"] == "fail"


# ── 2. verdict tail: nothing may follow the confidence field ─────────────────

@pytest.mark.parametrize("output", [
    "根拠:\n1. a — `src/a.py:1`\n判定: APPROVE\n確信度: 高\n",
    "判定: REJECT\n確信度: 中",                       # no trailing newline
    "判定: APPROVE_WITH_CONDITIONS\n確信度: 低\n",     # longest alternative wins
])
def test_verdict_tail_spec_accepts_a_terminal_confidence_line(output):
    assert _check(VERDICT_TAIL, output, 0)["status"] == "pass"


@pytest.mark.parametrize("output", [
    "判定: APPROVE\n確信度: 高\n補足があります",   # prose after the confidence line
    "判定: APPROVE\n確信度: 高 (根拠3点)\n",       # prose on the confidence line
    "判定: APPROVE note\n確信度: 高\n",            # prose on the verdict line
    "判定: APPROVE  \n確信度: 高\n",               # this tail spec allows no padding
    "判定: APPROVE\n\n確信度: 高\n",               # blank line between the two fields
    "判定: MAYBE\n確信度: 高\n",                   # verdict outside the vocabulary
    "判定: APPROVE\n確信度: とても高い\n",          # confidence outside the vocabulary
    "判定: APPROVE\n",                            # confidence line missing entirely
])
def test_verdict_tail_spec_rejects_trailing_content_after_the_confidence_field(output):
    assert _check(VERDICT_TAIL, output, 0)["status"] == "fail"


def test_dollar_anchored_tail_admits_one_trailing_newline_but_z_anchored_does_not():
    # `$` outside MULTILINE also matches immediately before a final newline, so a
    # `\n?$` tail still passes with one extra blank line at the end. `\Z` does not.
    # Both spellings occur in rig specs; the difference is load-bearing.
    padded = "判定: APPROVE\n確信度: 高\n\n"
    assert _check(VERDICT_TAIL, padded, 0)["status"] == "pass"
    z_anchored = VERDICT_TAIL.replace("\n?$", "\n?\\Z")
    assert _check(z_anchored, padded, 0)["status"] == "fail"
    assert _check(z_anchored, "判定: APPROVE\n確信度: 高\n", 0)["status"] == "pass"


# ── 3. contains / not_contains are literal, not formatting-tolerant ──────────

def test_contains_specs_keep_colons_in_the_argument_and_match_literally():
    # the spec is split on its FIRST colon, so the argument may itself contain one
    assert _check("contains:確信度: 高", "判定: APPROVE\n確信度: 高\n", 0)["status"] == "pass"
    # and unlike the regex specs above, the argument gets no whitespace tolerance
    assert _check("contains:確信度: 高", "判定: APPROVE\n確信度:  高\n", 0)["status"] == "fail"


def test_not_contains_spec_fails_on_any_substring_hit_including_a_quoted_one():
    assert _check("not_contains:今日", "本日20:00に公開\n", 0)["status"] == "pass"
    assert _check("not_contains:今日", "今日20:00に公開\n", 0)["status"] == "fail"
    assert _check("not_contains:今日", "`今日` は使わない\n", 0)["status"] == "fail"


# ── 4. malformed and oversized specs never masquerade as a pass ──────────────

def test_malformed_and_unsupported_specs_are_failed_or_unmeasured_never_passed():
    broken = _check("regex:判定: (APPROVE", "判定: APPROVE", 0)
    assert broken["status"] == "fail"
    assert broken["detail"]

    for spec in ("judgement:APPROVE", "regex"):
        result = _check(spec, "判定: APPROVE", 0)
        assert result["status"] == "unmeasured"
        assert result["detail"] == "unsupported deterministic check"


def test_regex_specs_are_measured_up_to_500_characters_and_unmeasured_beyond():
    output = "a" * 501
    assert _check("regex:" + "a" * 500, output, 0)["status"] == "pass"
    oversized = _check("regex:" + "a" * 501, output, 0)
    assert oversized["status"] == "unmeasured"
    assert oversized["detail"] == "unsupported deterministic check"

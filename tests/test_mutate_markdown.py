"""M9 — strip Markdown notation while keeping every word (jp-natural-writing).

This is the mutation for the one external finding this benchmark had no answer to: Nagai
(Open Data Lab, 2026-07) measured that removing Markdown-residue detection collapsed their
rule detector's AI-group median from 71.8 to 7.2, i.e. its discriminative power was
essentially symbol-hunting. Their detector was rule-based and the judge here is an LLM, so
it does not transfer automatically — it transfers as a question about how much of this
benchmark's 92-98% is layout.

For that question to be answerable, M9 has to change exactly ONE dimension. Every test
here is about that: notation goes, words stay, and nothing gets restructured. A mutation
that also drops or merges content would make the re-judgement uninterpretable.
"""

import pathlib
from importlib import util as _importlib_util

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = (REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing" / "mutate.py")

_spec = _importlib_util.spec_from_file_location("mutate", MODULE)
mutate = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)

M9 = mutate.mutate_M9


# ---- notation is removed -----------------------------------------------------

def test_headings_bullets_quotes_and_rules_lose_their_markers():
    out = M9("# 見出し\n\n- 箇条書き\n\n> 引用\n\n---\n\n1. 序数")
    assert "#" not in out and ">" not in out
    assert "- " not in out
    assert "---" not in out


def test_emphasis_and_code_spans_lose_their_delimiters():
    out = M9("これは **強調** と `コード` と *斜体* です。")
    assert "*" not in out and "`" not in out


def test_nested_emphasis_is_resolved():
    """(**`x`**) needs more than one pass; a single sub leaves the inner pair behind."""
    out = M9("**`pytest -q`** を実行。")
    assert "*" not in out and "`" not in out


def test_fences_and_table_separators_disappear():
    out = M9("```bash\npytest\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "```" not in out and "|" not in out


# ---- words survive -----------------------------------------------------------

def test_every_word_survives():
    src = ("# はじめに\n\nこの記事では **pytest** を解説します。\n\n"
           "## 手順\n- `pip install pytest` を実行\n\n> 引用文です。\n\n"
           "![図](https://x/y.png) と [リンク](https://z) があります。")
    out = M9(src)
    for word in ("はじめに", "pytest", "解説します", "手順", "pip install pytest",
                 "引用文です", "図", "リンク"):
        assert word in out, word


def test_link_and_image_keep_their_text_and_drop_their_target():
    out = M9("[クリック](https://example.com/page) してください。")
    assert "クリック" in out
    assert "example.com" not in out


def test_table_cells_become_prose_rather_than_vanishing():
    """Deleting rows would remove content, not notation."""
    out = M9("| 項目 | 値 |\n|---|---|\n| 失敗数 | 3 |")
    assert "失敗数" in out and "3" in out and "項目" in out


# ---- nothing is restructured -------------------------------------------------

def test_adjacent_table_rows_stay_on_separate_lines():
    r"""Regression: `\s*$` under re.M swallowed the blank line between rows and merged
    them, which is content restructuring rather than notation removal."""
    out = M9("| a | 1 |\n|---|---|\n| b | 2 |\n| c | 3 |")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 3, out


def test_plain_prose_is_untouched():
    src = "見出し無し。\n\n次の段落。"
    assert M9(src) == src


def test_paragraph_breaks_are_preserved():
    out = M9("# A\n\n本文1。\n\n## B\n\n本文2。")
    assert "本文1。" in out and "本文2。" in out
    assert "\n\n" in out


def test_deterministic():
    src = "# x\n\n- **a**\n\n| p | q |\n|---|---|\n| 1 | 2 |"
    assert M9(src) == M9(src)


# ---- registry ----------------------------------------------------------------

def test_m9_is_registered_and_chainable():
    assert "M9" in mutate.MUTATIONS
    assert mutate.MUTATIONS["M9"][1] is M9


def test_m9_removes_the_form_markers_the_profiler_counts():
    """Closes the loop with corpus_profile: after M9 the document must carry no form
    markers, since that is the dimension the ablation claims to remove."""
    spec = _importlib_util.spec_from_file_location(
        "corpus_profile", MODULE.parent / "corpus_profile.py")
    cp = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(cp)

    src = ("# はじめに\n\n本文です。\n\n- 箇条書き\n\n![図](a.png)\n\n"
           "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n")
    before, after = cp.profile_text(src), cp.profile_text(M9(src))
    assert before["headings_per1k"] > 0 and after["headings_per1k"] == 0
    assert before["has_image"] and not after["has_image"]
    assert before["has_table"] and not after["has_table"]
    assert before["has_code_fence"] and not after["has_code_fence"]
    assert before["bullets_per1k"] > 0 and after["bullets_per1k"] == 0

"""Corpus genre profiling and non-technical prose ingest for jp-natural-writing.

These live here rather than beside the benchmark because CI runs pytest with
testpaths=["tests"]; the modules are loaded by path, same as test_prose_rhythm.

The load-bearing test in this file is test_html_to_text_preserves_*. The profiler reads
form markers in markdown notation, so every corpus has to arrive in markdown. The first
extractor flattened blog HTML to bare prose, and the resulting profile showed every form
metric dropping to exactly 0.0 — which reads as a successful genre control and is really
just the extractor. That is R2's failure mode (an analyser artifact landing on one side
manufactures a between-population gap), and these tests exist to keep it fixed.
"""

import json
import pathlib
import subprocess
import sys
from importlib import util as _importlib_util

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing"


def _load(name):
    spec = _importlib_util.spec_from_file_location(name, BENCH / f"{name}.py")
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus_profile = _load("corpus_profile")
fetch_prose = _load("fetch_prose_corpus")


QIITA_ISH = """# はじめに
この記事では Python の環境構築について解説します。

## 手順
- pip を入れる
- venv を作る

![screenshot](https://example.com/a.png)

| 項目 | 値 |
|---|---|
| a | 1 |

```bash
pip install foo
```

以上です。
"""

BLOG_ISH = """今年は色々買ったので、よかったものを書く。

自分がいちばん使ったのは炊飯器だった。
毎朝これで米を炊く。旨い。

来年もたぶん同じことをしている。
"""


# ---- form markers ------------------------------------------------------------

def test_markdown_form_markers_detected():
    p = corpus_profile.profile_text(QIITA_ISH)
    assert p["headings_per1k"] > 0
    assert p["has_intro_heading"]
    assert p["bullets_per1k"] > 0
    assert p["has_image"] and p["has_table"] and p["has_code_fence"]


def test_bare_prose_has_no_form_markers():
    p = corpus_profile.profile_text(BLOG_ISH)
    assert p["headings_per1k"] == 0
    assert not p["has_intro_heading"]
    assert not (p["has_image"] or p["has_table"] or p["has_code_fence"])


def test_intro_heading_needs_a_heading_not_just_the_word():
    """「はじめに」 in running prose is not the platform's opening-heading tic."""
    assert not corpus_profile.profile_text("はじめに断っておくと、これは日記だ。")["has_intro_heading"]


# ---- register ----------------------------------------------------------------

def test_polite_rate_separates_desu_masu_from_plain_form():
    polite = corpus_profile.profile_text("これは本です。とても良いと思います。")
    plain = corpus_profile.profile_text("これは本だ。とても良いと思う。")
    assert polite["polite_pct"] == 100.0
    assert plain["polite_pct"] == 0.0


def test_first_person_counted_across_variants():
    p = corpus_profile.profile_text("私は行った。僕も行った。自分も行った。")
    assert p["first_person_per1k"] > 0


def test_taigendome_counts_noun_final_sentences():
    p = corpus_profile.profile_text("今日の収穫は大根。明日も畑。")
    assert p["taigendome_pct"] == 100.0
    assert corpus_profile.profile_text("今日は大根を採った。")["taigendome_pct"] == 0.0


# ---- shape -------------------------------------------------------------------

def test_ends_unresolved_reads_the_tail_only():
    """A piece that mentions an open problem mid-way and then wraps up neatly is not
    ending unresolved — counting it would inflate the very metric E3 cared about."""
    mid = "原因はまだ分からない。\n" + "\n".join(f"追記{i}。" for i in range(10)) + "\n解決した。"
    assert not corpus_profile.profile_text(mid)["ends_unresolved"]
    assert corpus_profile.profile_text("色々試した。\n原因はまだ分からない。")["ends_unresolved"]


def test_sentence_stats_ignore_headings_and_table_rows():
    p = corpus_profile.profile_text(QIITA_ISH)
    assert p["sentences"] > 0
    assert p["sent_len_mean"] < 200  # a heading swallowed as a sentence would blow this up


# ---- aggregation -------------------------------------------------------------

def test_boolean_metrics_aggregate_as_percentages(tmp_path):
    (tmp_path / "a.md").write_text(QIITA_ISH, encoding="utf-8")
    (tmp_path / "b.md").write_text(BLOG_ISH, encoding="utf-8")
    agg = corpus_profile.profile_corpus(tmp_path)
    assert agg["n"] == 2
    assert agg["has_image"] == 50.0
    assert agg["has_intro_heading"] == 50.0


def test_empty_corpus_reports_zero_rather_than_raising(tmp_path):
    assert corpus_profile.profile_corpus(tmp_path) == {"corpus": tmp_path.name, "n": 0}


def test_index_json_is_not_profiled_as_a_document(tmp_path):
    (tmp_path / "a.md").write_text(BLOG_ISH, encoding="utf-8")
    (tmp_path / "index.json").write_text('[{"file": "a.md"}]', encoding="utf-8")
    assert corpus_profile.profile_corpus(tmp_path)["n"] == 1


# ---- html extraction: the R2 guard -------------------------------------------

HTML = """<div class="entry-content">
<p>今年もよろしく。</p>
<h2>買ってよかったもの</h2>
<ul><li>炊飯器</li><li>椅子</li></ul>
<figure><img src="a.jpg"><figcaption>これ</figcaption></figure>
<table><tr><td>項目</td><td>値</td></tr></table>
<pre>echo hi</pre>
</div>"""


def test_html_to_text_preserves_headings_lists_images_tables_fences():
    """Both corpora must reach the profiler in the same notation. Flattening this side is
    how every form metric read 0.0 for blogs and looked like a genre difference."""
    p = corpus_profile.profile_text(fetch_prose.html_to_text(HTML))
    assert p["headings_per1k"] > 0
    assert p["bullets_per1k"] > 0
    assert p["has_image"]
    assert p["has_table"]
    assert p["has_code_fence"]


def test_extract_entry_takes_the_largest_block_not_the_first():
    """Hatena pages open with an inactive-blog ad in its own div.entry-content; a
    first-match extractor returned that as a 30-character article."""
    page = ('<div class="entry-content">この広告は、90日以上更新していないブログに表示しています。'
            '</div>' + HTML)
    assert "買ってよかったもの" in fetch_prose.extract_entry(page)


def test_extract_entry_handles_nested_divs():
    page = '<div class="entry-content"><div class="inner"><p>本文だ。</p></div>後書き。</div>'
    out = fetch_prose.extract_entry(page)
    assert "本文だ" in out and "後書き" in out


def test_extract_entry_returns_none_when_absent():
    assert fetch_prose.extract_entry("<div class='other'>x</div>") is None


# ---- subtitle ingest ---------------------------------------------------------

SRT = """1
00:00:01,000 --> 00:00:04,000
今日は料理の話をします。

2
00:00:04,000 --> 00:00:07,000
今日は料理の話をします。

3
00:00:07,000 --> 00:00:11,000
まず米を研ぎます。
"""


def test_subtitle_strips_scaffolding_and_dedupes_rolling_captions():
    out = fetch_prose.subtitle_to_text(SRT)
    assert "-->" not in out and "00:00" not in out
    assert out.count("今日は料理の話をします") == 1
    assert "まず米を研ぎます" in out


def test_subtitle_output_has_no_article_form():
    """The point of tagging spoken documents: they carry none of the article template, so
    they would separate from an arm's output on layout rather than authorship."""
    p = corpus_profile.profile_text(fetch_prose.subtitle_to_text(SRT))
    assert p["headings_per1k"] == 0
    assert not (p["has_image"] or p["has_table"] or p["has_code_fence"])


def test_vtt_header_and_cue_tags_removed():
    vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\n<v Speaker>こんにちは。</v>\n"
    out = fetch_prose.subtitle_to_text(vtt)
    assert out.startswith("こんにちは") and "WEBVTT" not in out and "<v" not in out


# ---- host filtering ----------------------------------------------------------

def test_search_hatena_keeps_only_personal_blog_hosts(monkeypatch):
    """Hatena Bookmark indexes the whole web; the first probe returned cnn.co.jp, which is
    professionally edited copy and a different register again."""
    rss = ("<link>https://b.hatena.ne.jp/channel</link>"
           "<link>https://www.cnn.co.jp/style/a.html</link>"
           "<link>https://someone.hatenablog.com/entry/2022/12/31/1</link>"
           "<link>https://corp.example.com/blog/1</link>").encode()
    monkeypatch.setattr(fetch_prose, "_get", lambda *a, **k: rss)
    out = fetch_prose.search_hatena("日記", "2018-01-01", "2022-12-31", 3)
    assert out == ["https://someone.hatenablog.com/entry/2022/12/31/1"]


# ---- CLI ---------------------------------------------------------------------

def test_cli_compares_two_corpora_and_reports_whether_form_moved(tmp_path):
    a, b = tmp_path / "qiita", tmp_path / "blog"
    a.mkdir(), b.mkdir()
    (a / "x.md").write_text(QIITA_ISH, encoding="utf-8")
    (b / "y.md").write_text(BLOG_ISH, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BENCH / "corpus_profile.py"), str(a), str(b)],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "form が動いた" in proc.stdout


def test_cli_json_mode_is_machine_readable(tmp_path):
    (tmp_path / "x.md").write_text(BLOG_ISH, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BENCH / "corpus_profile.py"), str(tmp_path), "--json"],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)[0]["n"] == 1


def test_cli_rejects_a_path_that_is_not_a_directory(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BENCH / "corpus_profile.py"), str(f)],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode != 0


def test_local_source_requires_an_input_directory():
    args = type("A", (), {"in_dir": None})()
    with pytest.raises(SystemExit):
        fetch_prose.collect_local(args)

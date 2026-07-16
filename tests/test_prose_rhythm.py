"""Deterministic Japanese prose-rhythm sensor (advisory machine backing for
the rhythm axis of knowledge/ai-writing-smells).

All metrics are surface proxies — these tests pin the deterministic behavior
of each detector on synthetic text, plus the CLI's file/stdin/json paths.
"""

import json
import pathlib
import subprocess
import sys

from importlib import util as _importlib_util

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "prose_rhythm.py"

_spec = _importlib_util.spec_from_file_location("prose_rhythm", SCRIPT)
prose_rhythm = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(prose_rhythm)


LONG = "この文はとても長く書かれていて、読点をいくつも挟みながら、ひたすら息継ぎなしにどこまでも続いていく、典型的な長文の見本である。"
SHORT = "短い。"


def _metrics(text):
    return {f["metric"] for f in prose_rhythm.analyze(text)["findings"]}


# ---- long-run ----------------------------------------------------------------

def test_three_consecutive_long_sentences_flagged():
    assert "long-run" in _metrics(LONG * 3)


def test_long_sentences_broken_by_a_short_beat_not_flagged():
    text = LONG + LONG + SHORT + LONG + LONG
    assert "long-run" not in _metrics(text)


# ---- uniform-beat --------------------------------------------------------------

def test_identical_length_sentences_flag_uniform_beat():
    text = "これはだいたい同じ長さで書かれた一文になっている。" * 10
    assert "uniform-beat" in _metrics(text)


def test_varied_lengths_do_not_flag_uniform_beat():
    text = (SHORT + LONG) * 5
    assert "uniform-beat" not in _metrics(text)


def test_too_few_sentences_never_flag_uniformity():
    text = "同じ長さの文である。" * 3
    assert "uniform-beat" not in _metrics(text)


# ---- ending-run ---------------------------------------------------------------

def test_repeated_endings_flagged():
    text = "実装を進めています。テストを書いています。結果を確認しています。"
    assert "ending-run" in _metrics(text)


def test_alternating_endings_not_flagged():
    text = "実装を進めた。テストを書いています。結果はこうだ。次を確認します。"
    assert "ending-run" not in _metrics(text)


# ---- progress narration (topic test) -------------------------------------------

def test_progress_narration_detected():
    text = "本節ではリトライの仕組みを扱う。実際のコードはこうなっている。"
    found = [f for f in prose_rhythm.analyze(text)["findings"] if f["metric"] == "progress"]
    assert len(found) == 1
    assert "本節では" in found[0]["detail"]


def test_situation_updating_text_not_flagged_as_progress():
    text = "リトライは三回で止まる。四回目はエスカレーションに回る。"
    assert "progress" not in _metrics(text)


# ---- connectives ----------------------------------------------------------------

def test_connective_heavy_text_flagged():
    text = "しかし、動く。つまり、正しい。また、速い。さらに、安い。したがって、良い。" \
           "一方、重い。そして、遅い。ただし、動く。"
    assert "connectives" in _metrics(text)


def test_sparse_connectives_not_flagged():
    text = "動いた。速かった。しかし重い。設定を変えた。軽くなった。それで十分だった。" \
           "翌日も動いた。誰も文句を言わなかった。"
    assert "connectives" not in _metrics(text)


# ---- non-prose exclusion ---------------------------------------------------------

def test_code_fences_headings_tables_excluded():
    text = ("# 見出しは無視される\n\n"
            "```\n" + LONG * 5 + "\n```\n\n"
            "| 表の行も | 無視される |\n\n" + SHORT)
    result = prose_rhythm.analyze(text)
    assert result["sentences"] == 1
    assert not result["findings"]


# ---- uniform-para -----------------------------------------------------------------

def test_identical_paragraph_shapes_flagged():
    para = "一文目はこう始まる。二文目で少し流す。三文目で止める。"
    text = "\n\n".join([para] * 4)
    assert "uniform-para" in _metrics(text)


# ---- CLI --------------------------------------------------------------------------

def _run_cli(args, stdin=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, input=stdin, timeout=30)


def test_cli_file_and_json(tmp_path):
    f = tmp_path / "draft.md"
    f.write_text("本章では全体像を説明します。" + LONG * 3, encoding="utf-8")
    r = _run_cli([str(f)])
    assert r.returncode == 0  # advisory: findings never change the exit code
    assert "prose-rhythm" in r.stdout and "progress" in r.stdout

    r = _run_cli(["--json", str(f)])
    data = json.loads(r.stdout)
    metrics = {x["metric"] for x in data[str(f)]["findings"]}
    assert "progress" in metrics and "long-run" in metrics


def test_cli_stdin_clean_text():
    r = _run_cli(["-"], stdin="よく書けた。短い。それだけだ。")
    assert r.returncode == 0
    assert "No rhythm findings" in r.stdout


def test_cli_missing_file_errors():
    r = _run_cli(["/nonexistent/x.md"])
    assert r.returncode == 2

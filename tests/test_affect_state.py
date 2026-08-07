"""Seeded 五感 / 喜怒哀楽 state for the jp-natural-writing benchmark.

The module lives under benchmarks/ next to writer_ledger.py, but its tests live here
because CI runs `pytest -q` with testpaths=["tests"] — the benchmark directory's own
test_mde_calibration.py is not reached by it. Loaded by path, same as test_prose_rhythm.

What these pin is mostly *scarcity*. The design premise is that supplying sensory and
emotional state only helps if most of it goes unused: writer_bio supplied always-
applicable material, its 生活習慣 entries were used in 8/8 articles, and it landed on the
null floor. So the tests that matter here are the ones asserting the draw stays sparse and
uneven, and that an empty draw is a legitimate outcome rather than an error.
"""

import pathlib
import subprocess
import sys
from importlib import util as _importlib_util

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = (REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing"
          / "affect_state.py")

_spec = _importlib_util.spec_from_file_location("affect_state", MODULE)
affect_state = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(affect_state)


IDS = [f"E{i}" for i in range(5)]


# ---- determinism -------------------------------------------------------------

def test_same_seed_same_draw():
    assert affect_state.draw("s1", IDS) == affect_state.draw("s1", IDS)


def test_different_seeds_diverge():
    """The whole point of seeding. If every seed produced the same state, the arm would
    be a constant and the fingerprint of one article would be the fingerprint of all."""
    draws = {
        affect_state.state_fingerprint(str(s), affect_state.draw(str(s), IDS))["sha1"]
        for s in range(40)
    }
    assert len(draws) > 20


def test_entry_ids_change_the_draw():
    assert affect_state.draw("s1", IDS) != affect_state.draw("s1", [f"X{i}" for i in range(5)])


# ---- scarcity: the writer_bio failure mode -----------------------------------

def test_most_entries_emit_nothing():
    """writer_bio's material applied to every article and went to the floor. If this
    ever approaches 1.0 the arm has become writer_bio and is not worth generating."""
    stats = affect_state.audit(trials=400, entries_per_article=5)
    assert stats["emit_rate"] < 0.6


def test_some_articles_draw_nothing_at_all():
    """An article with no sensory state has to be a possible outcome, not a retry."""
    stats = affect_state.audit(trials=400, entries_per_article=5)
    assert stats["zero_moment_share"] > 0.10


def test_moment_count_varies_between_articles():
    """A constant count per article is a fingerprint even when the count is low."""
    stats = affect_state.audit(trials=400, entries_per_article=5)
    assert stats["sd_moments"] > 0.5


def test_rare_senses_stay_rare():
    """嗅覚 and 味覚 rarely have anything to do with reading a stack trace. Uniform
    coverage of the five senses would be embodiment performed rather than recorded."""
    stats = affect_state.audit(trials=400, entries_per_article=5)
    assert stats["per_sense"]["味覚"] < stats["per_sense"]["視覚"]
    assert stats["per_sense"]["嗅覚"] < stats["per_sense"]["視覚"]


def test_flat_control_is_uniform_by_construction():
    """The ablation arm must actually reproduce the failure mode it exists to isolate."""
    stats = affect_state.audit(trials=100, entries_per_article=5, drift=False)
    assert stats["emit_rate"] == 1.0
    assert stats["sd_moments"] == 0.0
    assert stats["zero_moment_share"] == 0.0


def test_flat_and_drift_share_the_note_pool():
    """Scarcity is meant to be the only difference between the two arms. If disabling the
    walk also shifted which notes appear, the comparison would confound the two."""
    # Any seed that emits will do; hardcoding one couples the test to the walk's constants.
    seed = next(s for s in map(str, range(50)) if affect_state.draw(s, IDS))
    drifting = affect_state.draw(seed, IDS)
    flat = affect_state.draw(seed, IDS, drift=False)
    by_index = {m["index"]: (m["sense"], m["sense_note"], m["affect"]) for m in flat}
    for m in drifting:
        assert by_index[m["index"]] == (m["sense"], m["sense_note"], m["affect"])


def test_threshold_is_the_knob():
    lax = affect_state.draw("s1", IDS, threshold=0.0)
    strict = affect_state.draw("s1", IDS, threshold=1.01)
    assert len(lax) == len(IDS)
    assert strict == []


# ---- rendering ---------------------------------------------------------------

def test_empty_draw_renders_empty_not_placeholder():
    assert affect_state.render([]) == ""


def test_note_style_names_no_axes():
    """Supply-side rendering. A named axis is a target, and targets get satisfied
    uniformly — that is 則2, measured twice in this benchmark."""
    out = affect_state.render(affect_state.draw("s7", IDS, threshold=0.0), style="note")
    for axis in affect_state.AFFECTS:
        assert f"{axis} " not in out
    assert "五感" not in out and "喜怒哀楽" not in out


def test_label_style_names_and_scores_axes():
    out = affect_state.render(affect_state.draw("s7", IDS, threshold=0.0), style="label")
    assert "/5" in out
    assert any(s in out for s in affect_state.SENSES)


def test_unknown_style_is_an_error():
    with pytest.raises(ValueError):
        affect_state.render(affect_state.draw("s1", IDS, threshold=0.0), style="bogus")


# ---- containment gate --------------------------------------------------------

def test_entries_cover_every_number_in_the_notes():
    """Without this the whitelist check reads 03:40 and 400 as fabrications and deletes
    the supplied state over three revise rounds — supplied, then mechanically removed."""
    moments = affect_state.draw("s3", IDS, threshold=0.0)
    gate_entries = affect_state.entries(moments)
    assert len(gate_entries) == len(moments)
    allowed = {t for e in gate_entries for t in e["tokens"]}
    for m in moments:
        for num in affect_state._NUM.findall(m["sense_note"] + m["affect_note"]):
            assert num.strip(".,/-") in allowed


def test_entries_match_the_ledger_entry_shape():
    """writer_ledger.unlisted_specifics indexes these keys directly."""
    for entry in affect_state.entries(affect_state.draw("s3", IDS, threshold=0.0)):
        assert set(entry) >= {"kind", "when", "status", "fact", "urls", "tokens",
                              "id", "used_in"}


def test_entries_accept_an_injected_tokenizer():
    """hidden_check passes writer_ledger._tokens so the gate never sees two tokenizers."""
    out = affect_state.entries(affect_state.draw("s3", IDS, threshold=0.0),
                               tokens_fn=lambda *p: ["INJECTED"])
    assert all(e["tokens"] == ["INJECTED"] for e in out)


# ---- fingerprint -------------------------------------------------------------

def test_fingerprint_distinguishes_drift_from_flat():
    a = affect_state.state_fingerprint("s", affect_state.draw("s", IDS))
    b = affect_state.state_fingerprint("s", affect_state.draw("s", IDS, drift=False),
                                       drift=False)
    assert a["seed"] == b["seed"]
    assert (a["sha1"], a["drift"]) != (b["sha1"], b["drift"])


def test_fingerprint_records_the_seed_for_replay():
    fp = affect_state.state_fingerprint("abc", affect_state.draw("abc", IDS))
    assert fp["seed"] == "abc"
    assert fp["moments"] == len(affect_state.draw("abc", IDS))


# ---- CLI ---------------------------------------------------------------------

def _run(*args):
    return subprocess.run([sys.executable, str(MODULE), *args],
                          capture_output=True, text=True, timeout=120)


def test_cli_audit_json_is_machine_readable():
    import json
    proc = _run("--audit", "50", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["trials"] == 50


def test_cli_preview_reports_an_empty_draw_without_failing():
    """An empty state is the design working, so the CLI has to say so and exit 0 — a
    non-zero exit here would invite a retry loop, and retrying until something is emitted
    is precisely how the arm would turn back into writer_bio."""
    ids = [f"E{i}" for i in range(5)]
    empty = next(s for s in map(str, range(50)) if not affect_state.draw(s, ids))
    proc = _run("--seed", empty, "--preview")
    assert proc.returncode == 0, proc.stderr
    assert "何も出さなかった" in proc.stdout

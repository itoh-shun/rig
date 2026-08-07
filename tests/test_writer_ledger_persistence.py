"""Pinned-ledger state persistence (jp-natural-writing writer arms).

The bug these pin down. A pin file carries a `path` key baked in when it was created,
holding an absolute scratchpad path from the machine that made it — and not even the same
filename (`ledger-agent.json` was read while `.../scratchpad/ledger_agent.json` was
written). build_ledger read the pin, save_state wrote to the stale path, and the two never
met. So for every pinned arm — which is every writer_* arm ever measured — `used_in` and
`articles` never came back:

  * render_prior always answered 「まだ何も書いていない」, so the self-reference the arm's
    docstring credits could not happen at all;
  * cost() was always 0, so sample_incident kept choosing the same root, and 7 of 8 topics
    shared the spine L025/L040/L041 — one commit and two failing tests.

Those runs were eight articles about one incident, which is a live candidate explanation
for the judge's most cited complaint (「文体の均質さ」, 19 of 24 losing verdicts).

The fix splits the roles: the pin is an immutable snapshot, working_path(pin) holds the
mutable state, and the stored `path` is never trusted again. persist=False reproduces the
old behaviour on purpose so the fix is measurable rather than silently folded in.
"""

import json
import pathlib
from importlib import util as _importlib_util

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BENCH = REPO_ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing"

_spec = _importlib_util.spec_from_file_location("writer_ledger", BENCH / "writer_ledger.py")
wl = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(wl)

TOPICS = ["Python", "機械学習", "クラウドコンピューティング", "Web開発",
          "データベース設計", "リモートワーク", "セキュリティ対策", "チーム開発"]


@pytest.fixture
def pin(tmp_path):
    """A pin carrying a deliberately WRONG `path`, exactly like the committed ones."""
    entries = [{
        "kind": "commit" if i % 3 else "test", "when": f"2026-07-{10 + i:02d}",
        "status": "未解決" if i % 2 else "解決",
        "fact": f"事実{i}: token{i} が失敗している", "urls": [],
        "tokens": [f"token{i}"], "id": f"L{i:03d}", "used_in": [],
    } for i in range(40)]   # ~ the real pins' size; a 12-entry ledger forces reuse on its
    #                          own and would make the shared-spine test measure scarcity
    #                          rather than the persistence bug
    p = tmp_path / "ledger-agent.json"
    p.write_text(json.dumps({
        "writer_id": "w1", "author_filter": "agent", "entries": entries, "articles": [],
        "path": "/nonexistent/machine/scratchpad/ledger_agent.json",
    }, ensure_ascii=False), encoding="utf-8")
    return p


# ---- the fix -----------------------------------------------------------------

def test_stored_path_from_the_pin_is_never_trusted(pin):
    """The whole bug in one assertion."""
    state = wl.build_ledger(pin=pin, author="agent")
    assert state["path"] != "/nonexistent/machine/scratchpad/ledger_agent.json"
    assert state["path"] == str(wl.working_path(pin))


def test_recorded_articles_come_back_on_the_next_load(pin):
    state = wl.build_ledger(pin=pin, author="agent")
    incident = wl.sample_incident(state, "Python")
    wl.record_article(state, "Python", "記事1", incident["entries"], when="2026-08-07")

    reloaded = wl.build_ledger(pin=pin, author="agent")
    assert len(reloaded["articles"]) == 1
    assert "記事1" in wl.render_prior(reloaded)


def test_consumed_entries_are_deprioritised_across_topics(pin):
    """cost() stuck at 0 is what made the sampler reuse one root."""
    state = wl.build_ledger(pin=pin, author="agent")
    incident = wl.sample_incident(state, "Python")
    wl.record_article(state, "Python", "記事1", incident["entries"], when="2026-08-07")

    reloaded = wl.build_ledger(pin=pin, author="agent")
    assert any(e["used_in"] for e in reloaded["entries"])


def test_topics_stop_sharing_one_spine(pin):
    """Regression on the symptom: 7 of 8 topics used to receive the identical spine."""
    spines = []
    for topic in TOPICS:
        state = wl.build_ledger(pin=pin, author="agent")
        incident = wl.sample_incident(state, topic)
        spines.append(tuple(e["id"] for e in incident["spine"]))
        wl.record_article(state, topic, f"記事:{topic}", incident["entries"],
                          when="2026-08-07")
    assert len(set(spines)) >= len(TOPICS) - 1, spines


def test_the_pin_itself_is_never_mutated(pin):
    before = pin.read_text(encoding="utf-8")
    state = wl.build_ledger(pin=pin, author="agent")
    incident = wl.sample_incident(state, "Python")
    wl.record_article(state, "Python", "記事1", incident["entries"], when="2026-08-07")
    assert pin.read_text(encoding="utf-8") == before


def test_working_path_sits_beside_the_pin_and_is_not_the_pin(pin):
    w = wl.working_path(pin)
    assert w != pin
    assert w.parent == pin.parent
    assert w.name.endswith(".state.json")   # covered by .gitignore


# ---- the preserved old behaviour ---------------------------------------------

def test_persist_false_accumulates_nothing(pin):
    """The control arm has to actually reproduce the pre-fix behaviour."""
    state = wl.build_ledger(pin=pin, author="agent", persist=False)
    incident = wl.sample_incident(state, "Python")
    wl.record_article(state, "Python", "記事1", incident["entries"], when="2026-08-07")

    reloaded = wl.build_ledger(pin=pin, author="agent", persist=False)
    assert reloaded["articles"] == []
    assert wl.render_prior(reloaded) == "（まだ何も書いていない）"


def test_persist_false_writes_no_file_at_all(pin):
    """Old behaviour wrote to a stray path nobody read; ephemeral should write nothing."""
    wl.working_path(pin).unlink(missing_ok=True)
    state = wl.build_ledger(pin=pin, author="agent", persist=False)
    wl.save_state(state)
    assert not wl.working_path(pin).exists()


def test_persist_false_reproduces_the_shared_spine(pin):
    """Direct evidence that the arm labelled 'pre-fix' is really pre-fix."""
    spines = set()
    for topic in TOPICS:
        state = wl.build_ledger(pin=pin, author="agent", persist=False)
        incident = wl.sample_incident(state, topic)
        spines.add(tuple(e["id"] for e in incident["spine"]))
        wl.record_article(state, topic, f"記事:{topic}", incident["entries"],
                          when="2026-08-07")
    assert len(spines) < len(TOPICS)


def test_the_two_modes_actually_differ(pin):
    """If these ever converge, the ablation arm has stopped measuring anything."""
    wl.working_path(pin).unlink(missing_ok=True)

    def run(persist):
        out = []
        for topic in TOPICS:
            state = wl.build_ledger(pin=pin, author="agent", persist=persist)
            incident = wl.sample_incident(state, topic)
            out.append(tuple(e["id"] for e in incident["spine"]))
            wl.record_article(state, topic, f"記事:{topic}", incident["entries"],
                              when="2026-08-07")
        return out

    fixed = run(True)
    wl.working_path(pin).unlink(missing_ok=True)
    assert len(set(fixed)) > len(set(run(False)))

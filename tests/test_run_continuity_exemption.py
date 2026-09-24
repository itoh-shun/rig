"""Every copy of the run-status header rule carries the no-information wake-up exemption.

The rule "restate the run-status header at the top of every turn" is repeated in many
command bodies, the reminder hook, and (in Japanese, "run-status ヘッダを…再掲") pack commands. SKILL.md §6 ① exempts one kind of turn: a
`completed` task-notification whose result is only the harness's "already delivered as a
message, not repeated" sentence, not one that merely quotes it. A copy that states the rule without the exemption tells
the model to narrate exactly the turns the exemption exists to silence, so each copy must
carry the same sentence in its own language, word for word. The whole repo is scanned, not
a list of directories, so a copy in a new place is caught too.
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The directive in each language it is written in. Matched per line.
DIRECTIVE_EN = re.compile(r"restate th(?:is|e) run-status header", re.IGNORECASE)
DIRECTIVE_JA = re.compile(r"run-status ヘッダ.*再掲")

#: Written out rather than read from a file, so a copy cannot drift by editing the source.
EXEMPTION_EN = ("Exception (SKILL.md §6 ①): a turn woken only by a `<task-notification>` whose "
                "status is `completed` and whose result is only the harness's already-delivered "
                "sentence (the report was delivered to you as a message and is not repeated) gets "
                "no header and no narration; a result that merely quotes that sentence is not "
                "exempt, and a `failed`, `killed` or `stopped` notification never is.")
#: The Japanese copies live in packs, whose assets the pack validator scans for secret-shaped
#: values; `task-notification` contains `sk-notification`, which it refuses, so the tag is
#: described ("バックグラウンドタスクの通知") rather than named.
EXEMPTION_JA = ("例外（SKILL.md §6 ①）は、バックグラウンドタスクの通知だけで起きたターンである。"
                "その status が `completed` で、result の中身が「報告はメッセージとして届け済みで、"
                "ここでは繰り返さない」というハーネスの一文だけなら、ヘッダもナレーションも出さない。"
                "この一文を引用しただけの result や、`failed`・`killed`・`stopped` の通知は例外にならない。")

#: Not prose the model is handed: tests quote the directive, the changelogs and the dated
#: design history record it, dependencies and build output are not ours.
EXCLUDED_DIRS = {".git", "node_modules", "build", "dist", "tests", "__pycache__"}
EXCLUDED_PREFIXES = ("docs/CHANGELOG", "docs/superpowers/", "CHANGELOG")
#: The source the copies cite; it states the exemption in its own words (see below).
SOURCE = "skills/engine/SKILL.md"
TEXT_SUFFIXES = {".md", ".mdc", ".sh", ".py", ".toml", ".json", ".yaml", ".yml", ".txt"}


def _prose_files(root):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if EXCLUDED_DIRS & set(rel.parts[:-1]):
            continue
        name = rel.as_posix()
        if name.startswith(EXCLUDED_PREFIXES) or name == SOURCE:
            continue
        yield path


def missing_exemptions(root=REPO_ROOT):
    """(file, language) for every file whose directive lacks that language's exemption."""
    missing, found = [], []
    for path in _prose_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        lines = text.splitlines()
        name = path.relative_to(root).as_posix()
        for lang, directive, exemption in (("en", DIRECTIVE_EN, EXEMPTION_EN),
                                           ("ja", DIRECTIVE_JA, EXEMPTION_JA)):
            if any(directive.search(line) for line in lines):
                found.append((name, lang))
                if exemption not in text:
                    missing.append((name, lang))
    return found, missing


def test_the_directive_is_found_where_it_is_known_to_live():
    """Guards the scan itself: a glob or regex that finds nothing would pass vacuously."""
    found, _ = missing_exemptions()
    assert {("commands/go.md", "en"), ("commands/dev.md", "en"), ("commands/loop.md", "en"),
            ("hooks/remind-rig-header.sh", "en"),
            ("packs/domain/decision-humor/commands/duck.md", "ja"),
            ("packs/domain/sales/commands/sales.md", "ja"),
            ("packs/domain/video-storytelling/commands/movie.md", "ja")} <= set(found)
    assert sum(1 for _, lang in found if lang == "en") >= 18
    assert sum(1 for _, lang in found if lang == "ja") >= 7


def test_every_copy_of_the_directive_carries_the_exemption():
    _, missing = missing_exemptions()
    assert missing == []


@pytest.mark.parametrize("lang, body", [
    ("en", "While a RUN is active, Restate this run-status header at the top of every turn.\n"),
    ("ja", "RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。\n"),
])
def test_a_new_copy_without_the_exemption_is_caught(tmp_path, lang, body):
    """Mutation guard for the scan: a new pack command stating only the directive fails,
    and the same file with the matching-language sentence passes."""
    target = tmp_path / "packs" / "domain" / "new" / "commands" / "new.md"
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    assert missing_exemptions(tmp_path)[1] == [("packs/domain/new/commands/new.md", lang)]
    wrong = EXEMPTION_JA if lang == "en" else EXEMPTION_EN
    target.write_text(body + wrong, encoding="utf-8")
    assert missing_exemptions(tmp_path)[1] == [("packs/domain/new/commands/new.md", lang)]
    right = EXEMPTION_EN if lang == "en" else EXEMPTION_JA
    target.write_text(body + right, encoding="utf-8")
    assert missing_exemptions(tmp_path)[1] == []


def test_skill_md_states_the_exemption_it_is_cited_for():
    skill = (REPO_ROOT / SOURCE).read_text(encoding="utf-8")
    assert "情報のない起床は例外" in skill

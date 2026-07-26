#!/usr/bin/env python3
"""A writer's ledger: a fixed, externally-sourced inventory of what one person did.

The recurring failure of every gate so far is that a *requirement* gets satisfied
uniformly. "Include >=3 verifiable proper nouns" made the generator manufacture
specifics it had no source for, and evenly-sprinkled humanity is itself the tell.

This module replaces the demand-side quota with a supply-side constraint. It builds a
ledger of facts that were *not invented by the generator* — real commit subjects, a real
failing assertion, real tool output, real measured numbers from this benchmark's own
result files — and the article may state a verifiable specific only if that string is in
the ledger. There is no floor. Nothing asks for three of anything.

Two consequences fall out without being instructed:

  * The article cannot fabricate. PostgreSQL 16 is not in the ledger, so it cannot
    appear. This is the v2 failure mode removed at the source rather than forbidden.
  * The article is uneven. The ledger is small, lumpy, and only partly related to the
    assigned tag, so specifics cluster where the ledger is dense and the writing goes
    vague where it is empty — which is what the human corpus does.

An entry with status 未解決 carries no resolution facts. The generator therefore has
nothing with which to tie it off, and the article ends unresolved because it is out of
material, not because it was told to leave a loose end.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import threading
from pathlib import Path

# collect_live drives arms through a thread pool, and this module holds mutable state
# that every topic reads and writes. Without the lock two topics can sample from, and
# write back over, the same ledger concurrently.
STATE_LOCK = threading.Lock()

HERE = Path(__file__).parent
REPO = HERE.parents[2]
STATE_PATH = HERE / "writer_state.json"

# --------------------------------------------------------------------------- probes
#
# Every probe reads something that already exists on this machine. Nothing here is
# written by a model. A probe that fails contributes no entries rather than a plausible
# substitute — a thin ledger is the correct outcome when the material is not there.


def _run(argv: list[str], cwd: Path = REPO, timeout: int = 400) -> str:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return proc.stdout


def _tokens(*parts: str) -> list[str]:
    """Specific-looking strings a fact licenses the article to use."""
    out: list[str] = []
    for part in parts:
        out += re.findall(r"[A-Za-z][A-Za-z0-9_.+#/-]{1,}", part)
        out += re.findall(r"\d[\d,.]*", part)
    seen: dict[str, None] = {}
    for token in out:
        seen.setdefault(token.strip(".,/-"), None)
    return [t for t in seen if t]


def probe_commits(limit: int = 40) -> list[dict]:
    """Real commit subjects. The embarrassing ones are the point.

    `fix(benchmark): repair SyntaxError in --topics pushed in the previous commit` is a
    sentence no model writes about itself unprompted, and it is true.
    """
    entries = []
    raw = _run(["git", "log", f"-{limit}", "--date=short", "--format=%h|%ad|%s"])
    for line in raw.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, when, subject = parts
        entries.append({
            "kind": "commit",
            "when": when,
            "status": "解決",
            "fact": f"{when} のコミット {sha}: {subject}",
            "tokens": _tokens(sha, when, subject),
        })
    return entries


def probe_test_failures() -> list[dict]:
    """A real failing test, with its real assertion text.

    Recorded as 未解決 because it is: nothing in this ledger closes it.
    """
    raw = _run([sys.executable, "-m", "pytest", "tests", "-q", "--no-header", "-x"])
    entries = []
    fail_line = next((l for l in raw.splitlines() if l.startswith("FAILED ")), "")
    assert_lines = [l.strip() for l in raw.splitlines() if l.strip().startswith("E ")][:3]
    if fail_line:
        detail = " / ".join(a[2:].strip() for a in assert_lines)
        entries.append({
            "kind": "failure",
            "when": "",
            "status": "未解決",
            "fact": f"pytest がまだ落ちている: {fail_line[7:].strip()} — {detail}",
            "tokens": _tokens(fail_line, detail),
        })
    summary = next((l for l in raw.splitlines() if " passed" in l and "=" not in l[:2]), "")
    if summary:
        entries.append({
            "kind": "tool_output",
            "when": "",
            "status": "解決",
            "fact": f"テスト全体の結果行: {summary.strip()}",
            "tokens": _tokens(summary),
        })
    return entries


def probe_env() -> list[dict]:
    """Versions of what is actually installed here."""
    entries = []
    for label, argv in (
        ("Python", [sys.executable, "-V"]),
        ("uv", ["uv", "--version"]),
        ("カーネル", ["uname", "-r"]),
    ):
        out = _run(argv).strip()
        if out:
            entries.append({
                "kind": "env",
                "when": "",
                "status": "解決",
                "fact": f"手元の {label}: {out}",
                "tokens": _tokens(out),
            })
    return entries


def probe_results() -> list[dict]:
    """Numbers this benchmark actually measured, including the ones that went backwards."""
    entries = []
    for path in sorted((HERE / "results").glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for name, arm in (data.get("arms") or {}).items():
            stats = arm.get("stats") or {}
            if "mean" not in stats:
                continue
            entries.append({
                "kind": "measurement",
                "when": path.stem[:10],
                "status": "解決",
                "fact": (
                    f"{path.name} の計測: アーム {name} の平均スコアは {stats['mean']}、"
                    f"中央値 {stats.get('median')}、平均 {stats.get('mean_chars')} 字"
                ),
                "tokens": _tokens(path.name, name, str(stats["mean"]),
                                  str(stats.get("median")), str(stats.get("mean_chars"))),
            })
        if data.get("n") and data.get("mean") is not None:
            entries.append({
                "kind": "measurement",
                "when": path.stem[:10],
                "status": "未解決",
                "fact": (
                    f"人間が書いた記事 {data['n']} 本を同じ判定にかけたら平均 {data['mean']}。"
                    f"こちらの手元の数字とはまだ開きがある"
                ),
                "tokens": _tokens(str(data["n"]), str(data["mean"])),
            })
    return entries


def probe_files() -> list[dict]:
    """Real paths and real sizes in this checkout."""
    entries = []
    for rel in ("benchmarks/tasks/jp-natural-writing/hidden_check.py",
                "tests/test_bench_providers.py", "rig_workbench", "pyproject.toml"):
        path = REPO / rel
        if not path.exists():
            continue
        if path.is_dir():
            count = len(list(path.rglob("*.py")))
            fact = f"{rel} 配下に .py が {count} 本ある"
            tokens = _tokens(rel, str(count))
        else:
            lines = len(path.read_text(errors="replace").splitlines())
            fact = f"{rel} は {lines} 行"
            tokens = _tokens(rel, str(lines))
        entries.append({"kind": "file", "when": "", "status": "解決",
                        "fact": fact, "tokens": tokens})
    return entries


PROBES = (probe_commits, probe_test_failures, probe_env, probe_results, probe_files)


# ---------------------------------------------------------------------------- state


def build_ledger(force: bool = False) -> dict:
    """Build (or load) the writer's state. Probes run once; runs are reproducible after."""
    if STATE_PATH.exists() and not force:
        return json.loads(STATE_PATH.read_text())

    entries: list[dict] = []
    for probe in PROBES:
        try:
            entries += probe()
        except Exception:  # a probe that breaks contributes nothing, never a substitute
            continue
    for i, entry in enumerate(entries):
        entry["id"] = f"L{i:03d}"
        entry.setdefault("used_in", [])

    state = {"writer_id": "w1", "entries": entries, "articles": []}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------- sampling

# Which ledger entries a tag pulls on. Deliberately loose: for リモートワーク the repo
# has nothing to say, the sample comes out topic-blind, and the article ends up being
# about whatever the writer was actually doing — which is what the human corpus does
# (one article tagged セキュリティ対策 is about how many copies of a doujin book to print).
TOPIC_HINTS = {
    "Python": ["python", "pytest", "py", "uv"],
    "機械学習": ["bench", "model", "invariance", "score"],
    "クラウドコンピューティング": ["provider", "headless", "timeout", "run"],
    "Web開発": ["web", "vscode", "action"],
    "データベース設計": ["json", "state", "corpus"],
    "リモートワーク": [],
    "セキュリティ対策": ["security", "sast", "authz", "scanner"],
    "チーム開発": ["review", "gate", "commit", "PR"],
}


def sample_for_topic(state: dict, topic: str, k: int = 7, seed_extra: str = "") -> list[dict]:
    """Pick this article's material. Lumpy on purpose.

    Unresolved entries are weighted in, so the same open problem recurs across articles
    without any instruction to "carry a frustration" — it is simply still in the ledger.
    Entries already spent are deprioritised so successive articles are not the same story.
    """
    rng = random.Random(f"{state['writer_id']}|{topic}|{seed_extra}")
    hints = TOPIC_HINTS.get(topic, [])
    entries = list(state["entries"])
    if not entries:
        return []

    def relevance(e: dict) -> float:
        blob = (e["fact"] + " " + " ".join(e["tokens"])).lower()
        hit = sum(1 for h in hints if h.lower() in blob)
        return hit + (0.7 if e["status"] == "未解決" else 0.0) - 1.2 * len(e.get("used_in", []))

    ranked = sorted(entries, key=lambda e: (-relevance(e), e["id"]))
    near = ranked[: max(1, k // 2)]
    rest = [e for e in entries if e not in near]
    rng.shuffle(rest)
    picked = near + rest[: max(0, k - len(near))]
    rng.shuffle(picked)
    return picked


def render_ledger(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        when = f"[{e['when']}] " if e.get("when") else ""
        mark = " ←まだ直っていない" if e["status"] == "未解決" else ""
        lines.append(f"- {when}{e['fact']}{mark}")
    return "\n".join(lines)


def render_prior(state: dict, limit: int = 3) -> str:
    """Titles this writer has already published, so self-reference has something to point at."""
    articles = state.get("articles", [])[-limit:]
    if not articles:
        return "（まだ何も書いていない）"
    return "\n".join(f"- {a['when']} 「{a['title']}」（タグ: {a['topic']}）" for a in articles)


def record_article(state: dict, topic: str, title: str, entries: list[dict], when: str) -> None:
    ids = {e["id"] for e in entries}
    for entry in state["entries"]:
        if entry["id"] in ids:
            entry.setdefault("used_in", []).append(title)
    state["articles"].append({"when": when, "topic": topic, "title": title,
                              "consumed": sorted(ids)})
    save_state(state)


# ------------------------------------------------------------------ whitelist check

# Naming a widely-known tool is not fabrication; claiming a version, a size, a date or an
# error string is. Only precision is constrained, so the gate never pushes the article
# toward the vague generic prose that sank the lint-gated arm.
COMMON = {
    "ai", "web", "pc", "it", "os", "url", "api", "cpu", "gpu", "ram", "sql", "http",
    "https", "ci", "cd", "pr", "oss", "ide", "cli", "gui", "ui", "ux", "db", "id",
    "json", "yaml", "csv", "html", "css", "git", "github", "gitlab", "slack", "zoom",
    "docker", "kubernetes", "linux", "unix", "mac", "macos", "windows", "ubuntu",
    "python", "javascript", "typescript", "java", "ruby", "go", "rust", "php", "c",
    "node", "npm", "pip", "uv", "poetry", "pytest", "unittest", "django", "flask",
    "fastapi", "react", "vue", "numpy", "pandas", "scikit-learn", "sklearn",
    "pytorch", "tensorflow", "keras", "jupyter", "colab", "kaggle", "qiita", "zenn",
    "aws", "gcp", "azure", "s3", "ec2", "lambda", "rds", "mysql", "postgresql",
    "postgres", "sqlite", "redis", "mongodb", "nginx", "apache", "vscode", "vim",
    "emacs", "chatgpt", "claude", "gpt", "llm", "readme", "todo", "ok", "ng", "vpn",
    "saas", "paas", "iaas", "sso", "mfa", "csrf", "xss", "waf", "tls", "ssl", "ssh",
    "rest", "grpc", "graphql", "orm", "crud", "mvc", "dry", "yagni", "tdd", "bdd",
    "etl", "bi", "ml", "dl", "nlp", "cnn", "rnn", "lstm", "bert", "cv", "ocr",
}

_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9_.+#/-]{1,}")
# Two digits or more. A one-digit number ("3日ほど") is texture, not a claim; a
# two-digit one ("767バイト", "2021年") is a checkable assertion and needs a source.
_NUM = re.compile(r"\d[\d,.]*\d")


def unlisted_specifics(text: str, entries: list[dict]) -> list[str]:
    """Specific-looking strings the writer has no record of. These are fabrications."""
    allowed = {t.lower() for e in entries for t in e["tokens"]}
    allowed |= {w.lower() for e in entries for w in _ASCII.findall(e["fact"])}
    allowed |= {n for e in entries for n in _NUM.findall(e["fact"])}

    findings: list[str] = []
    for match in _ASCII.findall(text):
        token = match.strip("./-").lower()
        if len(token) < 2 or token in COMMON or token in allowed:
            continue
        if any(token in a for a in allowed):
            continue
        findings.append(match)
    for match in _NUM.findall(text):
        if match in allowed or any(match in a for a in allowed):
            continue
        findings.append(match)

    seen: dict[str, None] = {}
    for f in findings:
        seen.setdefault(f, None)
    return list(seen)


if __name__ == "__main__":
    st = build_ledger(force="--force" in sys.argv)
    print(f"{len(st['entries'])} entries, {len(st['articles'])} articles")
    for topic in ("Python", "リモートワーク"):
        print(f"\n--- {topic} ---")
        print(render_ledger(sample_for_topic(st, topic)))

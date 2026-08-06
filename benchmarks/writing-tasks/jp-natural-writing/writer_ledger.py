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

import hashlib
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
    """Run a probe command. A command that could not run is reported, not swallowed.

    Silence and emptiness look identical here, and that is expensive: a checkout without
    pytest makes `python3 -m pytest` exit 1 with an empty stdout and "No module named
    pytest" on stderr, which probe_test_failures reads as "no tests are failing". Measured
    on this machine that left the ledger with 1 未解決 entry out of 89 — and 未解決 material
    is the entire reason the writer arm works. It is the same failure the module already
    fixed once for probe_results (one AttributeError silently cost every measurement entry).

    Note the discriminating condition is nonzero-exit AND empty stdout, not FileNotFoundError:
    the probe runs `sys.executable -m pytest`, and sys.executable always exists. pytest
    exits nonzero with a full report when tests fail, which is the case that must stay quiet.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except FileNotFoundError:
        print(f"[writer_ledger] probe tool missing: {argv[0]} — contributes nothing",
              file=sys.stderr)
        return ""
    except subprocess.TimeoutExpired:
        # The tool exists and simply did not finish. A thin ledger IS correct here.
        print(f"[writer_ledger] probe timed out after {timeout}s: {' '.join(argv[:3])}",
              file=sys.stderr)
        return ""
    if proc.returncode != 0 and not proc.stdout.strip():
        print(f"[writer_ledger] probe produced nothing and exited {proc.returncode}: "
              f"{' '.join(argv[:4])}\n  stderr: {proc.stderr.strip()[:200]}", file=sys.stderr)
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


# Who wrote the material. The judge caught the fieldnote arms on provenance —
# 「承認プロンプトや cwd 維持といったコーディングエージェント特有の体験記述」— and the
# conclusion recorded then was that grounding in real material only works if the material
# is human. This repo has both: of the last 200 commits, 83 are Claude's and 50 are the
# maintainer's. Splitting the probe by author turns "does provenance leak into the prose"
# into one controlled comparison instead of a change of task.
AGENT_AUTHORS = ("Claude",)


def probe_commits(limit: int = 40, author: str = "all") -> list[dict]:
    """Real commit subjects. The embarrassing ones are the point.

    `fix(benchmark): repair SyntaxError in --topics pushed in the previous commit` is a
    sentence no model writes about itself unprompted, and it is true.

    `author` selects whose commits count: "all", "human" (everything not authored by an
    agent) or "agent". The window widens for the filtered variants so the entry count
    stays comparable — a human-only ledger built from the same 40 commits would be a
    quarter the size, and ledger size is itself a variable (a thin ledger makes the prose
    vague where it is empty).
    """
    entries = []
    window = limit if author == "all" else limit * 4
    raw = _run(["git", "log", f"-{window}", "--date=short", "--format=%h|%ad|%an|%s"])
    for line in raw.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, when, who, subject = parts
        is_agent = any(who.startswith(a) for a in AGENT_AUTHORS)
        if (author == "human" and is_agent) or (author == "agent" and not is_agent):
            continue
        if len(entries) >= limit:
            break
        entries.append({
            "kind": "commit",
            "when": when,
            "status": "解決",
            "fact": f"{when} のコミット {sha}: {subject}",
            # Real, verifiable URLs — the largest human/AI gap analyze_gap found
            # (link_per1k 5.56 vs 0.01, d=1.73) and the only never-varied dimension.
            # Derived, not fetched, so they exist for every commit and fabricate nothing.
            "urls": ([f"https://github.com/itoh-shun/rig/commit/{sha}"]
                     + [f"https://github.com/itoh-shun/rig/pull/{m}"
                        for m in re.findall(r"#(\d{2,5})", subject)]),
            "author": who,
            "tokens": _tokens(sha, when, subject)
                      + [f"https://github.com/itoh-shun/rig/commit/{sha}"]
                      + [f"https://github.com/itoh-shun/rig/pull/{m}"
                         for m in re.findall(r"#(\d{2,5})", subject)],
        })
    return entries


def probe_test_failures() -> list[dict]:
    """Every failing test, each with its own assertion text.

    Recorded as 未解決 because they are: nothing in this ledger closes them.

    Without -x. With it, pytest stopped at the first failure and the ledger held exactly
    one open item out of 59 entries (3%), so sample_incident() had almost nothing to build
    an incident around and reused the same root across topics. The suite actually has four
    failures; the flag was hiding three. Costs the full suite (~2 min) instead of a partial
    one, which is worth it — an open bug with its real assertion text is the material the
    one reproducibly human-judged article was built from.
    """
    raw = _run([sys.executable, "-m", "pytest", "tests", "-q", "--no-header"], timeout=900)
    entries = []

    # Each failure's traceback sits under a ____ test_name ____ banner; the summary lines at
    # the end give the node ids. Pair them so every entry carries its own assertion text
    # rather than the first failure's.
    sections = re.split(r"^_{5,} (.+?) _{5,}$", raw, flags=re.MULTILINE)
    per_test = {}
    for i in range(1, len(sections) - 1, 2):
        name = sections[i].strip()
        body = sections[i + 1]
        errs = [l.strip()[2:].strip() for l in body.splitlines() if l.strip().startswith("E ")]
        per_test[name] = " / ".join(x for x in errs[:3] if x)

    for line in raw.splitlines():
        if not line.startswith("FAILED "):
            continue
        node = line[7:].split(" - ")[0].strip()
        short = node.rsplit("::", 1)[-1]
        detail = per_test.get(short, "")
        entries.append({
            "kind": "failure",
            "when": "",
            "status": "未解決",
            "fact": f"pytest がまだ落ちている: {node} — {detail}",
            "tokens": _tokens(node, detail),
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
        # Not every record is an arm run: the paired-comparison files are JSON arrays, and
        # calling .get on one raised AttributeError, which build_ledger's blanket except
        # turned into "this probe found nothing" — ten measurement entries silently gone.
        if not isinstance(data, dict):
            continue
        for name, arm in (data.get("arms") or {}).items():
            stats = arm.get("stats") or {}
            if "mean" not in stats:
                continue
            entries.append({
                "kind": "measurement",
                "when": path.stem[:10],
                "status": "解決",
                # Only the fields this record actually has. The older result files predate
                # mean_chars, and rendering it unconditionally put 「平均 None 字」 into the
                # ledger as a fact — junk the generator then has to either copy or trip over.
                "fact": "、".join(
                    [f"{path.name} の計測: アーム {name} の平均スコアは {stats['mean']}"]
                    + [f"中央値 {stats['median']}" for _ in (1,) if stats.get("median") is not None]
                    + [f"平均 {stats['mean_chars']} 字" for _ in (1,) if stats.get("mean_chars")]
                ),
                "tokens": _tokens(path.name, name, str(stats["mean"]),
                                  str(stats.get("median") or ""), str(stats.get("mean_chars") or "")),
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


def ledger_sha1(state: dict) -> str:
    """Fingerprint of the material, independent of article history."""
    facts = "\n".join(e["fact"] for e in state["entries"])
    return hashlib.sha1(facts.encode("utf-8")).hexdigest()[:12]


def build_ledger(force: bool = False, pin: Path | None = None,
                 author: str = "all") -> dict:
    """Build (or load) the writer's state. Probes run once; runs are reproducible after.

    `pin` names a snapshot file to build into and read back from. Without it the state
    lives at one fixed path that every run shares, and the ledger is rebuilt from
    `git log`'s most recent commits — so it changes whenever the repo does. The recorded
    incident-sampling comparison lost a third run to exactly this: run3 scored 0/24
    against run1's 12/24 and was found afterwards to have been built on a different
    ledger, making it uncomparable rather than a refutation. Recording the sha1 detected
    that after the fact; a pin prevents it.
    """
    path = pin or STATE_PATH
    if path.exists() and not force:
        return json.loads(path.read_text())

    entries: list[dict] = []
    for probe in PROBES:
        try:
            entries += (probe(author=author) if probe is probe_commits else probe())
        except Exception as exc:
            # A broken probe still contributes nothing rather than a substitute — but it
            # must not be indistinguishable from a probe that had nothing to say. One
            # AttributeError in probe_results cost the ledger every measurement entry and
            # nothing anywhere reported it.
            print(f"[writer_ledger] probe {probe.__name__} failed: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            continue
    for i, entry in enumerate(entries):
        entry["id"] = f"L{i:03d}"
        entry.setdefault("used_in", [])

    state = {"writer_id": "w1", "author_filter": author, "entries": entries, "articles": []}
    state["sha1"] = ledger_sha1(state)
    state["path"] = str(path)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    return state


def save_state(state: dict) -> None:
    Path(state.get("path") or STATE_PATH).write_text(
        json.dumps(state, ensure_ascii=False, indent=1))


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
    """FLAT SAMPLING — kept as the recorded negative result, not as a candidate.

    A flat list of k peer facts is rendered as k peer sections. Measured at 79.0 against
    bare's 89.2, and every verdict named the same thing:
    「各節が『事実→内省→日付への接続』という同一テンプレートで反復され」. The generator
    also quoted each commit subject verbatim and then paraphrased it in Japanese, and it
    spent a whole section on 「pyproject.toml が 88 行」 because the line was in the
    inventory. Supply-as-a-list becomes supply-as-an-obligation.

    sample_incident() replaces it: the material is shaped as one incident plus scraps, so
    there is no list to walk.
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


# Facts that are true but carry no incident. They were the worst of the flat sample: the
# generator gave 「pyproject.toml は 88 行」 its own heading because it was on the list.
FILLER_KINDS = {"file", "env"}

# Tokens shared by half the ledger say nothing about which entries belong together.
_GENERIC_TOKENS = {"benchmark", "rig", "feat", "fix", "test", "docs", "the", "a", "of",
                   "to", "for", "in", "and", "2026", "07", "26", "json", "py"}


def sample_incident(state: dict, topic: str, seed_extra: str = "", scrap_k: int = 2) -> dict:
    """Shape the material as ONE incident plus a couple of scraps.

    `scrap_k` is the surplus knob. The redefinition memo argues the mechanism is finiteness
    rather than realness — a writer discards because they hold more than the piece needs,
    and the discard is what reads as human. Scraps are the only surplus in this design, so
    varying their count varies surplus with the spine, the topic and the ledger all fixed.

    The spine is the open problem and whatever in the ledger shares rare tokens with it,
    in time order — a chain of one thing, not a list of many. Scraps are handed over
    separately and marked as things the writer already knows, so they read as asides
    rather than as sections owed a paragraph.

    Returns {"goal", "spine", "scraps", "entries"}; `entries` is the union, which is what
    the whitelist check licenses.
    """
    rng = random.Random(f"{state['writer_id']}|{topic}|{seed_extra}|incident")
    entries = [e for e in state["entries"] if e["kind"] not in FILLER_KINDS]
    if not entries:
        entries = list(state["entries"])
    if not entries:
        return {"goal": "", "spine": [], "scraps": [], "entries": []}

    def cost(e: dict) -> float:
        return len(e.get("used_in", []))

    open_items = [e for e in entries if e["status"] == "未解決"]
    root = min(open_items or entries, key=lambda e: (cost(e), e["id"]))

    root_tokens = {t.lower() for t in root["tokens"]} - _GENERIC_TOKENS
    hints = {h.lower() for h in TOPIC_HINTS.get(topic, [])}

    def affinity(e: dict) -> int:
        blob = (e["fact"] + " " + " ".join(e["tokens"])).lower()
        return sum(1 for t in root_tokens if len(t) > 3 and t in blob) + \
               sum(1 for h in hints if h in blob)

    others = [e for e in entries if e["id"] != root["id"]]
    chained = sorted(others, key=lambda e: (-affinity(e), cost(e), e["id"]))[:2]
    spine = sorted([root] + chained, key=lambda e: (e.get("when") or "9999", e["id"]))

    pool = [e for e in others if e not in chained]
    rng.shuffle(pool)
    scraps = sorted(pool[:scrap_k], key=lambda e: cost(e))

    goal = f"{root['fact']}。これをなんとかしたかった" if root["status"] == "未解決" else ""
    return {"goal": goal, "spine": spine, "scraps": scraps, "entries": spine + scraps}


def render_ledger(entries: list[dict], show_urls: bool = False) -> str:
    lines = []
    for e in entries:
        when = f"[{e['when']}] " if e.get("when") else ""
        mark = " ←まだ直っていない" if e["status"] == "未解決" else ""
        lines.append(f"- {when}{e['fact']}{mark}")
        if show_urls:
            for url in e.get("urls", []):
                lines.append(f"  {url}")
    return "\n".join(lines)


def render_incident(sample: dict, show_urls: bool = False) -> str:
    """Render as an incident, not as an inventory."""
    out = ["この記事で書くのは、次の一件です。", "", "＜経過（時系列）＞",
           render_ledger(sample["spine"], show_urls)]
    if sample["scraps"]:
        out += ["", "＜同じ時期に手元にあった、別件のメモ＞",
                "（あなたはこれを既に知っています。記事の主題ではないので、",
                "  必要なら一言触れるだけでよく、説明しなくて構いません）",
                render_ledger(sample["scraps"], show_urls)]
    return "\n".join(out)


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


def verbatim_echoes(text: str, entries: list[dict], span: int = 24, allow: int = 1) -> list[str]:
    """Long stretches copied straight out of the ledger.

    The flat-sample arm quoted each commit subject in full and then restated it in
    Japanese, and the judge read exactly that: 「ログ出力を機械的に記事化した」. The
    whitelist stops the article inventing facts; this stops it transcribing them. Between
    the two, the only thing left that satisfies both is the writer's own sentences.

    One long quote is allowed — pasting the error message once is what a person does.
    Everything past the first is a finding.
    """
    hits: list[str] = []
    for entry in entries:
        fact = entry["fact"]
        for i in range(0, max(0, len(fact) - span) + 1):
            chunk = fact[i:i + span]
            if chunk.strip() and chunk in text:
                hits.append(chunk)
                break
    return hits[allow:]


if __name__ == "__main__":
    st = build_ledger(force="--force" in sys.argv)
    print(f"{len(st['entries'])} entries, {len(st['articles'])} articles")
    for topic in ("Python", "リモートワーク"):
        print(f"\n--- {topic} ---")
        print(render_ledger(sample_for_topic(st, topic)))


# --------------------------------------------------------------------------- biography
#
# A second ledger, for the writer rather than the work. The artifact ledger bounds what the
# writer has DONE; nothing bounds what they KNOW, where they came from, or when they are
# free to sit down. So the current writer has no ignorance — and a human tech article is
# full of 「詳しくないけど」「昔からよく分かっていない」, which is the finiteness of knowledge
# leaving a trace the same way an open bug leaves one.
#
# Everything here is FICTIONAL fixture data. It is not the maintainer and must not be
# presented as anyone real; E2 measured that the judge never verifies material anyway, so
# there is nothing to gain from borrowing a real life and an obvious reason not to.
#
# Carried as a ledger, never as adjectives. 「几帳面な性格」 is performed uniformly — that is
# what `novoice` (75% -> 100%) and the quota gate both demonstrated. A finite list of jobs
# held, things known, things NOT known and hours available is material: it licenses some
# sentences and makes others unwritable, which is the only mechanism that has ever worked here.
BIOGRAPHY = {
    "一人称": "自分",
    "経歴": [
        "2016-2019 受託開発で PHP と jQuery。案件は中小企業の業務システムが多かった",
        "2019-2023 SaaS 企業で Python と Django。ここで初めてテストを書く習慣がついた",
        "2023- 同じ会社で社内ツール寄りの担当に移り、CI とビルド周りを見ている",
    ],
    "知っていること": [
        "pytest とその -x / -q あたりのよく使う引数",
        "git の日常操作と、たまに使う worktree",
        "subprocess の returncode と、シェルが 126/127 を返す条件",
    ],
    "知らないこと": [
        "Rust と Go は書いたことがない。読むのも自信がない",
        "Kubernetes は触ったことがなく、名前しか知らない",
        "型システムの理論的な話は昔から分かっていない",
    ],
    "実体験": [
        "2019年に本番の migration を down なしで流して半日戻せなかった",
        "2021年に AWS SAA を一度落ちて、翌年に取り直した",
        "2024年に CI が緑なのに手元だけ落ちる件を3日追って、原因が locale だった",
    ],
    "生活習慣": [
        "平日は退勤後の 22 時以降しか自分の作業に触れない",
        "土曜の午前にまとめて触ることが多い",
        "記事は書きかけで放置することが多く、過去に3本下書きのまま残っている",
    ],
}


def biography_entries(bio: dict | None = None) -> list[dict]:
    """The biography as ledger entries, so the containment gate covers it too.

    Without this the gate reads PHP, Django, SAA and every year in the career as
    fabrications and strips them out over three revise rounds — the biography would be
    supplied and then mechanically deleted. Same rule as the artifact ledger: a
    biographical specific may appear only because it is in the biographical ledger.
    """
    bio = bio or BIOGRAPHY
    out = []
    for key in ("経歴", "知っていること", "知らないこと", "実体験", "生活習慣"):
        for i, fact in enumerate(bio[key]):
            out.append({
                "kind": "bio", "when": "", "status": "解決",
                "fact": f"{key}: {fact}", "urls": [], "tokens": _tokens(fact),
                "id": f"B{key[:2]}{i}", "used_in": [],
            })
    return out


def render_biography(bio: dict | None = None) -> str:
    bio = bio or BIOGRAPHY
    lines = [f"一人称は「{bio['一人称']}」。"]
    for key in ("経歴", "知っていること", "知らないこと", "実体験", "生活習慣"):
        lines.append(f"\n{key}:")
        lines += [f"- {v}" for v in bio[key]]
    return "\n".join(lines)

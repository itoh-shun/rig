#!/usr/bin/env python3
"""Measure how AI-generated the produced Japanese reads, judged by a separate model.

Two modes:

  fixture (default)  Score the checked-in narrow/ and canonical/ implementations.
                     Deterministic and offline-ish, but both sides are hand-written
                     fixtures — it demonstrates the metric, it does not measure rig.

  --live             Generate the Japanese for real, once per arm, with the *same*
                     generator model throughout so only the harness differs:
                       bare    — one shot, no gate
                       selfrev — same round budget as a gated arm, but no verifier and
                                 no criteria. Without this control, a gated arm's win
                                 cannot be told apart from simply spending more compute.
                       rig     — v1 gate: generic "write it well" criteria, and the
                                 generator verifies its own output
                       rig2    — v2 gate: criteria retuned to the tells the judge
                                 actually penalised, verified by a different model
                     selfrev → rig2 is the number that says whether the gate earned its
                     keep. bare → rig2 alone does not.

The judge is a separate `claude -p` process pinned to read-only tools, and it is never
told which arm produced the text. Lower score = reads more human-written.
"""

import argparse
import json
import re
import os
import subprocess
import statistics
import tempfile
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent

DEFAULT_JUDGE_MODEL = "claude-opus-5"
DEFAULT_GEN_MODEL = "claude-sonnet-5"

MAX_PARALLEL = 2

# Run-to-run spread on the v1 gate arm was 11 points at n=4 (57.8 then 46.5), which is
# wider than the gap between two gate revisions is likely to be. Comparing gate versions
# needs more samples than comparing gate-vs-no-gate did.
TOPICS = [
    "Python",
    "機械学習",
    "クラウドコンピューティング",
    "Web開発",
    "データベース設計",
    "リモートワーク",
    "セキュリティ対策",
    "チーム開発",
]

# Judge runs read-only, same enforcement rig pins on its own verifier role
# (rig_workbench/orchestrate/providers.py::_READONLY_ENFCE).
READONLY_TOOLS = ["--allowedTools", "Read,Grep,Glob"]

# v1: the criteria the first live run used. Kept so gate revisions are measurable
# against each other, not just against no-gate.
#
# It has a known flaw: it asks for a well-made article (varied sentences, consistent
# voice, a writer's perspective) and the arm duly produced well-made articles that the
# judge still scored 68-76. 「声のトーンが一貫していること」 actively pushes toward the
# uniformity the judge penalises.
GATE_CRITERIA_V1 = """- テンプレート的な言い回しの使い回しがないこと（「〜について」の連発、「このブログ記事では」などの定型導入）
- 文長と文構造にばらつきがあること（同じ骨格の文が並んでいない）
- 説明のための説明になっていないこと（中身のない一般論で埋めていない）
- 書き手の視点や具体が入っていること
- 声のトーンが一貫していること"""

# v2: REGRESSED. Kept as evidence, not as a candidate.
#
# Derived from the judge's complaints about v1, one criterion per complaint. It scored
# 46.6 against v1's 30.9 on the same topics and judge (results/2026-07-26-v2-regression).
# The verdicts say why, and it is not that the generator ignored the criteria — it is
# that the generator satisfied them exactly:
#
#   「具体的なバージョン番号や数値が各文に均等に配置され、どの文も『事実＋未解決の締め』
#     という同じ構造で終わり」
#   「曖昧化マーカーが一定間隔で配置されている」
#   「脱線先がすべて…具体名詞に収束しており、話題のばらつき方が不自然に管理されている」
#
# A criterion precise enough to check is precise enough to perform, and evenly performed
# humanity is itself the tell. The quota lines are the worst of it: "最低3つ" made the
# model manufacture specifics it did not have, and it got them wrong — one sample put
# PostgreSQL 16 in the title over a MySQL anecdote and misstated MySQL's index limit.
# Demanding content the generator has no source for buys fabrication, not detail.
GATE_CRITERIA_V2 = """- 検証可能な固有名詞・数値・バージョン・エラーメッセージが最低3つ入っていること。
  「知人が経営する中小企業」のような検証不能で一般的な例は不可。
- 解決していない問題・妥協・未練を最低1つ、解決しないまま書くこと。
  最後にすべてがきれいに片付く文章は不可。
- 構成が整いすぎていないこと。「体験談フック→定義→対比→締めの宣言」のような
  完璧な型に沿わないこと。脱線・言い直し・話の順序の乱れがあってよい。
- 記事の予告や宣言で締めないこと（「本記事では〜解説します」「〜をお届けします」
  「〜を整理してみたい」）。
- 締めに反転レトリックを使わないこと（「Aより先に、Bを書く」「Aではなく、Bだ」型）。
- 具体例のない羅列をしないこと（「Web開発からデータ分析、AIまで幅広く」型）。
- 文長を不揃いにすること。短い断片文を混ぜてよい。"""

# v3: same evidence as v2, opposite construction. v2 mandated the cures and got them
# administered in even doses; v3 only forbids the symptoms. An absence cannot be
# distributed evenly, so there is less for the generator to perform — and nothing here
# asks for specifics it would have to invent.
GATE_CRITERIA_V3 = """以下は満たすべきノルマではなく、避けるべき禁止事項です。該当があれば FAIL としてください。

- 記事の予告・宣言で締めている（「本記事では〜解説します」「〜をお届けします」
  「ぜひ参考にしてください」「〜を整理してみたい」）
- 定型の導入で始まっている（「近年〜増えています」「〜ではないでしょうか」
  「〜と感じていませんか」「〜も少なくないでしょう」）
- 具体例のない一般論を羅列している（「Web開発からデータ分析、AIまで幅広く」型）
- すべての文が同じ骨格・同じ長さ・同じ語尾で並んでいる
- 起承転結がきれいに閉じており、引っかかりが残っていない
- 締めに反転レトリック（「Aより先に、Bだ」型）や教訓のまとめを置いている

重要: 上記を避けるために要素を機械的に配置しないこと。固有名詞・数値・口語表現・脱線を
均等に散りばめた文章は、それ自体が「不自然に管理されている」として検出されます。
書けることだけを書き、書けないことは無理に足さないこと。"""


def run_claude(
    prompt: str, model: str, extra_argv: list[str], timeout: int = 300, attempts: int = 4
) -> str:
    """Invoke headless claude and return its text output.

    Headless claude occasionally exits non-zero with an empty stderr under
    concurrency, so transient failures are retried with backoff rather than
    aborting the whole run and losing the other arm's work.
    """
    argv = ["claude", "-p", prompt, "--output-format", "text", "--model", model] + extra_argv
    last = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(2**attempt)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=HERE)
        except subprocess.TimeoutExpired:
            last = f"timeout after {timeout}s"
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        last = (
            f"exit {proc.returncode}; "
            f"stderr={proc.stderr.strip()[:200] or '<empty>'}; "
            f"stdout={proc.stdout.strip()[:200] or '<empty>'}"
        )
    raise RuntimeError(f"claude failed after {attempts} attempts: {last}")


def extract_json(raw: str) -> dict:
    """Pull the first JSON object out of a model response (tolerates ``` fences)."""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()).strip()
    candidates = [stripped]
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Generated Japanese sometimes carries a raw newline inside a string value,
        # which is legal prose and illegal JSON. Escaping those is a safe last resort;
        # a genuinely malformed response still falls through to the caller's retry.
        try:
            return json.loads(candidate.replace("\r\n", "\\n").replace("\n", "\\n"))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"no parseable JSON in response: {raw[:300]}")


def run_claude_json(prompt: str, model: str, extra_argv: list[str], attempts: int = 3) -> dict:
    """run_claude, but the unit being retried is call-and-parse.

    A response that arrives intact but unparseable is as useless as one that never
    arrived, and it used to abort the whole run and discard every finished arm.
    """
    last = ""
    for _ in range(attempts):
        raw = run_claude(prompt, model, extra_argv)
        try:
            return extract_json(raw)
        except ValueError as exc:
            last = str(exc)
    raise ValueError(f"unparseable JSON after {attempts} attempts: {last}")


# --------------------------------------------------------------------------- judge

JUDGE_PROMPT = """以下の日本語テキストが AI によって生成されたものか、人間が書いたものかを判定してください。

テキスト:
\"\"\"
{text}
\"\"\"

判定の観点:
- テンプレート的・機械的な言い回しの反復
- 不自然に均質な文長・文構造
- 中身のない一般論での埋め合わせ
- 語彙の多様性、具体性、書き手の視点の有無
- 自然な話し言葉のリズム

0-100 のスコアで答えてください:
  0-20   明らかに人間が書いた
  20-40  おそらく人間
  40-60  どちらとも言えない
  60-80  おそらく AI 生成
  80-100 明らかに AI 生成

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"score": <0-100 の数値>, "reasoning": "<日本語で一文>"}}"""


def judge(text: str, model: str) -> dict:
    """Score how AI-generated `text` reads. Judge does not know which arm made it."""
    result = run_claude_json(JUDGE_PROMPT.format(text=text), model, READONLY_TOOLS)
    return {
        "score": max(0.0, min(100.0, float(result["score"]))),
        "reasoning": str(result.get("reasoning", "")),
    }


# ---------------------------------------------------------------------- generation

# Every prompt that emits a draft repeats this. Without it on the revise prompts, the
# gated arms drifted to ~2x the bare arm's length, so "the gate helped" was confounded
# with "the gated arm wrote more". Length is a confound worth removing outright.
#
# The target is a whole article, not the 100-200 char intro this benchmark started with,
# because lint.py's document-level detectors have minimum sizes and almost none of them
# reached quorum on an intro: burstiness needs 6 sentences, sentence variance 5,
# paragraph metrics 3-4 paragraphs, nominal_ending 2000 chars. At intro length the only
# live checks were forbidden_phrase and low_specificity. A ~1700-char draft fires the
# rest (measured: 10 findings, 7 of them critical, on an ungated Sonnet article).
LENGTH_SPEC = "本文は 1500〜2500 字。見出しと複数段落で構成すること。この分量は書き直しても必ず守ること。"

GEN_PROMPT = """{topic} をテーマにした日本語のブログ記事を書いてください。
{length_spec}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""

VERIFY_PROMPT = """あなたは独立した検証者です。以下の日本語テキストが受入基準を満たすか判定してください。

テキスト:
\"\"\"
{text}
\"\"\"

受入基準:
{criteria}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"verdict": "PASS" または "FAIL", "issues": ["<満たしていない基準と、その具体的な箇所>"]}}"""

REVISE_PROMPT = """以下の日本語テキストが受入ゲートで FAIL しました。指摘を踏まえて書き直してください。

現在のテキスト:
\"\"\"
{text}
\"\"\"

検証者の指摘:
{issues}

受入基準:
{criteria}

{length_spec}

重要: 推敲して「整える」のではありません。整った文章ほど AI が書いたと判定されます。
具体を足し、整いすぎた構成をむしろ崩してください。きれいにまとめないこと。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"title": "<タイトル>", "description": "<導入文>"}}"""


def generate_bare(topic: str, model: str) -> dict:
    """Bare arm: one shot, no gate."""
    out = run_claude_json(GEN_PROMPT.format(topic=topic, length_spec=LENGTH_SPEC), model, [])
    return {"title": out["title"], "description": out["description"], "rounds": 1}


SELFREV_PROMPT = """以下の日本語テキストをもっと良くしてください。
{length_spec}

現在のテキスト:
\"\"\"
{text}
\"\"\"

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"title": "<タイトル>", "description": "<導入文>"}}"""


def generate_selfrev(topic: str, model: str, rounds: int = 3) -> dict:
    """Control arm: same round budget as the rig arm, but no gate.

    The rig arm spends 2-3 model calls per topic while the bare arm spends 1, so a
    rig-vs-bare gap confounds "the gate helped" with "more compute helped". This arm
    holds the round count and isolates the gate: the generator just revises its own
    output, with no independent verifier and no acceptance criteria.
    """
    out = run_claude_json(GEN_PROMPT.format(topic=topic, length_spec=LENGTH_SPEC), model, [])
    for _ in range(rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        out = run_claude_json(SELFREV_PROMPT.format(text=text, length_spec=LENGTH_SPEC), model, [])
    return {"title": out["title"], "description": out["description"], "rounds": rounds}


def generate_rig(
    topic: str,
    model: str,
    criteria: str = GATE_CRITERIA_V1,
    verify_model: str | None = None,
    max_rounds: int = 3,
) -> dict:
    """rig arm: generate, verify in a separate read-only process, revise until PASS.

    verify_model defaults to `model`, which is the configuration the first live run
    used — and which quietly violates rig's own stated design ("one class of model
    does not review its own artifacts", README §1). Pass a different model to honour
    it; the arm table wires that up for the v2 arm.
    """
    verify_model = verify_model or model
    out = run_claude_json(GEN_PROMPT.format(topic=topic, length_spec=LENGTH_SPEC), model, [])
    rounds = 1
    gate_log = []

    for _ in range(max_rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        verdict = run_claude_json(
            VERIFY_PROMPT.format(text=text, criteria=criteria), verify_model, READONLY_TOOLS
        )
        passed = str(verdict.get("verdict", "")).upper() == "PASS"
        gate_log.append({"round": rounds, "verdict": "PASS" if passed else "FAIL",
                         "issues": verdict.get("issues", [])})
        if passed:
            break
        issues = "\n".join(f"- {i}" for i in verdict.get("issues", []))
        out = run_claude_json(
            REVISE_PROMPT.format(text=text, issues=issues, criteria=criteria, length_spec=LENGTH_SPEC), model, []
        )
        rounds += 1

    return {
        "title": out["title"],
        "description": out["description"],
        "rounds": rounds,
        "gate_log": gate_log,
    }


def generate_rig_v2(topic: str, model: str) -> dict:
    """rig arm with the retuned gate: v2 criteria + a verifier that is not the generator.

    Regressed, and unattributably so: it changed the criteria, the verifier model, and
    the revise wording in one step. The rig1x arm below splits the verifier change back
    out so the next comparison is attributable.
    """
    return generate_rig(topic, model, criteria=GATE_CRITERIA_V2, verify_model=DEFAULT_JUDGE_MODEL)


# --------------------------------------------------------- mechanical detector (lint)
#
# coji/natural-japanese's lint.py, used as the verifier instead of a model. Its premise
# is the one this benchmark arrived at the hard way: 「検出は機械、判断は人間（またはAI）」
# — detect mechanically, leave the fix to the agent, because a model asked to check for
# a tell will perform the absence of it. Our v2 gate is exactly that failure.
#
# It is also calibrated against a corpus (103 human / 81 AI documents) rather than
# against one model's taste, and that calibration removed checks that punish good human
# writing (「最後に」: human 48 vs AI 2). Nothing in our LLM gate has that property.
#
# Not vendored — MIT-licensed but externally maintained, so it is referenced by path.
LINT_PATH_ENV = "RIG_JP_LINT_PATH"


def resolve_lint_path() -> Path | None:
    """Locate lint.py, or None when the checkout is not available."""
    env = os.environ.get(LINT_PATH_ENV)
    candidates = [Path(env)] if env else []
    candidates += [
        HERE / "natural-japanese/skills/natural-japanese/scripts/lint.py",
        HERE.parents[2] / "natural-japanese/skills/natural-japanese/scripts/lint.py",
    ]
    return next((c for c in candidates if c.is_file()), None)


def lint_findings(text: str, lint_path: Path) -> list[dict]:
    """Run lint.py over `text` and return its findings.

    lint.py exits 0 whether or not it finds anything (it is a lint, not a CI gate), so
    a non-zero exit means the tool itself failed and must not be read as "clean".
    """
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        tmp = fh.name
    try:
        proc = subprocess.run(
            ["uv", "run", str(lint_path), tmp, "--json"],
            capture_output=True, text=True, timeout=300, cwd=lint_path.parent,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"lint.py failed: {proc.stderr.strip()[:300]}")
        return json.loads(proc.stdout).get("findings", [])
    finally:
        Path(tmp).unlink(missing_ok=True)


LINT_REVISE_PROMPT = """以下の日本語テキストを、静的解析器が検出した指摘にもとづいて直してください。

現在のテキスト:
\"\"\"
{text}
\"\"\"

検出された指摘（形態素解析による機械的検出。何をどう直すかはあなたの判断です）:
{findings}

{length_spec}

指摘を機械的に潰すのではなく、なぜその表現になったかを考えて書き直してください。
検出を避けるために要素を均等に配置すると、それ自体が不自然さになります。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""


def generate_riglint(topic: str, model: str, max_rounds: int = 3) -> dict:
    """rig arm whose verifier is lint.py rather than a model.

    Detection is deterministic and corpus-calibrated; the revision is still the model's
    judgement. That split is the whole point — it is the half of the loop that cannot be
    talked into passing.
    """
    lint_path = resolve_lint_path()
    if lint_path is None:
        raise SystemExit(
            f"riglint needs coji/natural-japanese. Clone it and set {LINT_PATH_ENV} to "
            "skills/natural-japanese/scripts/lint.py"
        )

    out = run_claude_json(GEN_PROMPT.format(topic=topic, length_spec=LENGTH_SPEC), model, [])
    rounds = 1
    gate_log = []

    for _ in range(max_rounds - 1):
        text = f"{out['title']}\n\n{out['description']}"
        findings = lint_findings(text, lint_path)
        gate_log.append({
            "round": rounds,
            "verdict": "PASS" if not findings else "FAIL",
            "issues": [f"{f.get('category')}: {f.get('detail') or f.get('excerpt', '')[:80]}"
                       for f in findings],
        })
        if not findings:
            break
        rendered = "\n".join(
            f"- [{f.get('severity')}] {f.get('category')}: "
            f"{f.get('detail') or ''} 該当箇所: {f.get('excerpt', '')[:60]}"
            for f in findings
        )
        out = run_claude_json(
            LINT_REVISE_PROMPT.format(text=text, findings=rendered, length_spec=LENGTH_SPEC),
            model, [],
        )
        rounds += 1

    return {
        "title": out["title"],
        "description": out["description"],
        "rounds": rounds,
        "gate_log": gate_log,
    }


def generate_rig_v3(topic: str, model: str) -> dict:
    """Ablation: v3 criteria, self-verified as in v1. Isolates the criteria change."""
    return generate_rig(topic, model, criteria=GATE_CRITERIA_V3)


def generate_rig_v1_xmodel(topic: str, model: str) -> dict:
    """Ablation: v1 criteria, cross-model verifier. Isolates the verifier change."""
    return generate_rig(topic, model, criteria=GATE_CRITERIA_V1, verify_model=DEFAULT_JUDGE_MODEL)


# ------------------------------------------------------------------------- fixtures


def load_fixture(dirname: str):
    """Load the checked-in BlogGenerator from narrow/ or canonical/."""
    import importlib.util

    path = HERE / dirname / "blog_generator.py"
    spec = importlib.util.spec_from_file_location(f"blog_generator_{dirname}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BlogGenerator()


def collect_fixture(dirname: str) -> list[dict]:
    gen = load_fixture(dirname)
    return [
        {
            "topic": t,
            "title": gen.generate_title(t),
            "description": gen.generate_description(t),
            "rounds": 1,
        }
        for t in TOPICS
    ]


LIVE_ARMS = {
    "bare": ("bare — 1 shot, no gate", generate_bare),
    "selfrev": ("self-revise — same rounds, no gate (compute control)", generate_selfrev),
    "rig": ("rig v1 — generic gate, self-verified", generate_rig),
    "rig2": ("rig v2 — quota gate, cross-model verifier (regressed)", generate_rig_v2),
    "rig3": ("rig v3 — prohibitions only, self-verified", generate_rig_v3),
    "rig1x": ("rig v1 criteria, cross-model verifier (ablation)", generate_rig_v1_xmodel),
    "riglint": ("rig + lint.py — mechanical detector as verifier", generate_riglint),
}


def collect_live(arm: str, model: str) -> list[dict]:
    fn = LIVE_ARMS[arm][1]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        return list(pool.map(lambda t: {"topic": t, **fn(t, model)}, TOPICS))


# ----------------------------------------------------------------------------- main


def score_arm(samples: list[dict], judge_model: str) -> list[dict]:
    """Judge every sample, and count lint findings on it as a second opinion.

    The judge is the independent outcome measure. lint counts are informative for the
    ungated arms, but for riglint they are teaching-to-the-test — that arm optimises
    against this exact detector, so its low count is not evidence of anything.
    """
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(
            pool.map(lambda s: judge(f"{s['title']}\n{s['description']}", judge_model), samples)
        )
    scored = [{**s, **v} for s, v in zip(samples, verdicts)]

    lint_path = resolve_lint_path()
    if lint_path is not None:
        for sample in scored:
            try:
                findings = lint_findings(f"{sample['title']}\n\n{sample['description']}", lint_path)
            except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
                continue
            sample["lint_total"] = len(findings)
            sample["lint_critical"] = sum(1 for f in findings if f.get("severity") == "critical")
    return scored


def report(label: str, scored: list[dict]) -> dict:
    print(f"\n[{label}]")
    for s in scored:
        rounds = f"  [{s['rounds']} round(s)]" if s["rounds"] > 1 else ""
        lint = f"  lint {s['lint_total']}({s['lint_critical']}c)" if "lint_total" in s else ""
        print(f"  {s['topic']}: {s['score']:.1f}/100{rounds}{lint}")
        print(f"    {s['title']}")
        print(f"    → {s['reasoning']}")
    scores = [s["score"] for s in scored]
    # Length is reported on every arm because it was a live confound once already: the
    # gated arms drifted to twice the bare arm's length, so a score gap could have been
    # "wrote more" rather than "wrote better". If arms diverge here again, the score
    # comparison is not clean.
    lengths = [len(s["title"]) + len(s["description"]) for s in scored]
    linted = [s for s in scored if "lint_total" in s]
    stats = {
        "mean_chars": round(statistics.mean(lengths), 1),
        "mean_lint": round(statistics.mean([s["lint_total"] for s in linted]), 2) if linted else None,
        "mean_lint_critical": (
            round(statistics.mean([s["lint_critical"] for s in linted]), 2) if linted else None
        ),
        "mean": round(statistics.mean(scores), 2),
        # n is small and one sample can swing the mean hard (a single 15 in an
        # otherwise 68-76 arm moved it 14 points), so the median is reported too.
        "median": round(statistics.median(scores), 2),
        "min": min(scores),
        "max": max(scores),
    }
    print(f"  平均 {stats['mean']:.1f} / 中央値 {stats['median']:.1f} "
          f"(範囲 {stats['min']:.0f}-{stats['max']:.0f}, 平均 {stats['mean_chars']:.0f}字)")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="generate for real instead of scoring fixtures")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--gen-model", default=DEFAULT_GEN_MODEL, help="live mode only; same for every arm")
    ap.add_argument(
        "--arms",
        default="bare,selfrev,rig,rig2",
        help="live mode only; comma-separated subset of " + ",".join(LIVE_ARMS),
    )
    ap.add_argument("--json-out", type=Path, help="write the full result record here")
    args = ap.parse_args()

    arms: dict[str, dict] = {}

    if args.live:
        names = [a.strip() for a in args.arms.split(",") if a.strip()]
        unknown = [n for n in names if n not in LIVE_ARMS]
        if unknown:
            raise SystemExit(f"unknown arm(s): {unknown}; known: {list(LIVE_ARMS)}")
        print(f"live mode — generator: {args.gen_model}, judge: {args.judge_model}")
        for name in names:
            arms[name] = {"label": LIVE_ARMS[name][0], "samples": collect_live(name, args.gen_model)}
    else:
        print(f"fixture mode — judge: {args.judge_model}")
        print("note: both arms are hand-written fixtures; use --live to measure rig itself")
        arms["bare"] = {"label": "narrow fixture (no gate)", "samples": collect_fixture("narrow")}
        arms["rig"] = {"label": "canonical fixture (gated)", "samples": collect_fixture("canonical")}

    for arm in arms.values():
        arm["samples"] = score_arm(arm["samples"], args.judge_model)
        arm["stats"] = report(arm["label"], arm["samples"])

    gated = [n for n in ("riglint", "rig3", "rig2", "rig1x", "rig") if n in arms]
    baseline = arms["bare"]["stats"]["mean"]
    treatment = arms[gated[0]]["stats"]["mean"] if gated else baseline
    improvement = baseline - treatment
    pct = (improvement / baseline * 100) if baseline else 0.0

    print(f"\n{'=' * 60}")
    print(f"AI らしさスコア (低いほど自然) — judge: {args.judge_model}")
    print(f"{'=' * 60}")
    for name, arm in arms.items():
        st = arm["stats"]
        lint = f" / lint {st['mean_lint']:>5.2f}" if st.get("mean_lint") is not None else ""
        print(f"  {name:<8} 平均 {st['mean']:>5.1f} / 中央値 {st['median']:>5.1f}"
              f" / {st['mean_chars']:>5.0f}字{lint}")
    if "riglint" in arms:
        print("  注: riglint は lint に対して最適化しているため、その lint 値は証拠にならない")

    spread = max(a["stats"]["mean_chars"] for a in arms.values()) - min(
        a["stats"]["mean_chars"] for a in arms.values())
    if spread > 60:
        print(f"  ⚠ アーム間の平均字数が {spread:.0f}字 開いている — スコア差が長さ由来の可能性")
    best = gated[0] if gated else "bare"
    print(f"\n  bare → {best} 改善: {improvement:.1f} points ({pct:.1f}%)")

    if "selfrev" in arms:
        # The number that decides whether the gate earned its keep, rather than the
        # extra rounds it happens to spend.
        gate_effect = arms["selfrev"]["stats"]["mean"] - treatment
        print(f"  selfrev → rig (ゲート単独の効果): {gate_effect:.1f} points")
        if gate_effect < 5:
            print("  ⚠ ゲートの寄与は計算量の増加と区別できない")

    print(f"  判定: {'PASS' if improvement >= 5 else 'FAIL'}")

    results = {
        "mode": "live" if args.live else "fixture",
        "judge_model": args.judge_model,
        "gen_model": args.gen_model if args.live else None,
        "arms": arms,
        "improvement_points": round(improvement, 2),
        "improvement_percent": round(pct, 1),
        "compared_arm": best,
        "gate_effect_vs_selfrev": (
            round(arms["selfrev"]["stats"]["mean"] - treatment, 2) if "selfrev" in arms else None
        ),
        "success": improvement >= 5,
    }

    if args.json_out:
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")

    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()

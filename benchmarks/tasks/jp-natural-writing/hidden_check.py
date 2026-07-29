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
import hashlib
import json
import re
import os
import unicodedata
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


# ------------------------------------------------------------------ writer's ledger
#
# Every gate above changes how the generator is *instructed*. This one changes what it
# has to work with: a fixed inventory of facts it did not invent (writer_ledger.py), from
# which it may draw and beyond which it may not. The constraint is supply-side, so there
# is no quota to satisfy uniformly and no unavailable content to fabricate.

WRITER_PROMPT = """あなたは Qiita に技術記事を投稿している一人のエンジニアです。
これはあなた自身の手元の記録で、他人に説明するために書かれたものではありません。

{ledger}

これまでに自分が書いた記事:
{prior}

書き方について:
- 事実として書けるのは、上の記録にあることだけです。記録にないバージョン番号・日付・
  数値・エラーメッセージ・ファイル名・製品名を書いてはいけません。
- 記録にないことは書かないでください。埋めるために一般論を足す必要はありません。
- 記録の行を順番になぞって一行ずつ節にしないでください。あなたはこの一件の当事者なので、
  すでに知っていることを自分に向かって説明する必要はありません。長く引用するのも一度だけです。
- この記事につけるタグは「{topic}」です。タグに合わせて話題を広げたり、
  そのテーマの解説をしたりする必要はありません。
- まだ直っていないと書かれているものは、直っていません。まとめないでください。

{length_spec}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""

WRITER_REVISE_PROMPT = """以下はあなたが書いた記事ですが、機械的な検査で問題が見つかりました。

現在のテキスト:
\"\"\"
{text}
\"\"\"

{findings}

あなたの記録:
{ledger}

直し方: 記録にない記述は削ってください。記録から丸写しになっている箇所は、引用をやめて
自分の言葉に置き換えるか、消してください。別の具体を足して埋めないこと。
削った結果ぼんやりした段落が残っても、それで構いません。

{length_spec}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""


def generate_writer(topic: str, model: str, max_rounds: int = 3) -> dict:
    """Writer arm: a persistent authored identity carried as data, not as adjectives.

    The identity is a ledger of real artifacts on this machine — commit subjects, a
    failing assertion with its real text, versions, this benchmark's own recorded
    numbers. Three properties do the work, and none of them is an instruction the
    generator can perform:

      * Closed vocabulary. A verifiable specific may appear only if its string is in the
        sampled ledger; the check is mechanical and post hoc. There is no minimum, so
        adding specifics cannot satisfy it — only having a source can.
      * Scarcity and mismatch. The sample is small and often unrelated to the tag, so
        detail clusters where the ledger is dense and thins where it is empty. The
        unevenness is a property of the material, not a style the model is imitating.
      * Nothing to conclude with. Entries marked 未解決 carry no resolution facts, so the
        article runs out of material rather than tying itself off.

    State persists across topics: consumed entries are deprioritised and prior titles are
    offered back, which is where self-reference comes from.
    """
    import writer_ledger as wl

    with wl.STATE_LOCK:
        state = wl.build_ledger()
        entries = wl.sample_for_topic(state, topic)
        if not entries:
            raise SystemExit("writer ledger is empty; run writer_ledger.py --force")
        ledger = wl.render_ledger(entries)
        prior = wl.render_prior(state)

    out = run_claude_json(
        WRITER_PROMPT.format(ledger=ledger, prior=prior, topic=topic, length_spec=LENGTH_SPEC),
        model, [],
    )
    rounds = 1
    gate_log = []

    for _ in range(max_rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        findings = wl.unlisted_specifics(text, entries)
        gate_log.append({"round": rounds, "verdict": "PASS" if not findings else "FAIL",
                         "issues": findings})
        if not findings:
            break
        out = run_claude_json(
            WRITER_REVISE_PROMPT.format(
                text=text, findings="\n".join(f"- {f}" for f in findings),
                ledger=ledger, length_spec=LENGTH_SPEC),
            model, [],
        )
        rounds += 1

    with wl.STATE_LOCK:
        wl.record_article(state, topic, out["title"], entries, when="2026-07-26")
    return {"title": out["title"], "description": out["description"],
            "rounds": rounds, "gate_log": gate_log}


def generate_rig_v1_xmodel(topic: str, model: str) -> dict:
    """Ablation: v1 criteria, cross-model verifier. Isolates the verifier change."""
    return generate_rig(topic, model, criteria=GATE_CRITERIA_V1, verify_model=DEFAULT_JUDGE_MODEL)


# -------------------------------------------------------------- fieldnote (repo-grounded)
#
# Every arm above asks the generator to write *about* a topic. That framing is the
# thing the judge keeps naming: an article whose subject is 「Python」 can only be a
# survey, a survey can only be 「はじめに→特徴→活用例→まとめ」, and a survey has no
# author. The known-human corpus does not contain a single survey. Those articles are
# work logs that happen to carry a topic tag — one narrow thing the writer did, in the
# order they did it, with the dead ends left in.
#
# This arm changes the material rather than the instructions. The generator is given a
# real, runnable question about this repository, a shell, and one pass to actually
# answer it; what it saw is written down as a log. A second call — with no repo access
# and no memory of the task — turns that log into the article. Then a deterministic
# gate checks the direction of fit: every identifier, path, version and multi-digit
# number in the article must occur in the log. Anything that does not is deleted.
#
# The two failure modes this is built against:
#
#   fabrication (v2's quota gate). Nothing here asks for specifics. A source is
#   supplied and the gate only ever *removes* specifics that have no source, so a
#   thin investigation yields a thin article rather than an invented one.
#
#   uniform compliance (v2 criteria, riglint). The one shape requirement — the section
#   skeleton — is not chosen by the writer: it is computed from how many bytes of log
#   each real step produced. The step that emitted 40 lines of stderr gets 600 chars,
#   the step that just worked gets 90, and the shape is different for every topic
#   because it is a projection of a different transcript. Unevenness is inherited, not
#   performed. The containment gate is subtractive, and nothing subtractive can be
#   satisfied by placing elements at regular intervals.

REPO_ROOT = HERE.parents[2]

# Tools the investigator gets. Read-only in effect: no writes, no network. cwd is HERE
# (run_claude hardcodes it), which is inside the repo, so --add-dir lifts the sandbox to
# the repo root.
INVESTIGATE_TOOLS = [
    "--allowedTools",
    "Read,Grep,Glob,"
    "Bash(git:*),Bash(python:*),Bash(python3:*),Bash(uv:*),Bash(ls:*),Bash(cat:*),"
    "Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(find:*),Bash(date:*),"
    "Bash(sed:*),Bash(node:*),Bash(npm:*)",
    "--add-dir",
    str(REPO_ROOT),
]

# One question per topic. Each names a starting point in this repo and nothing else —
# no steps, no expected answer. They are chosen to be genuinely uncertain: several of
# them fail (semgrep is not installed, the action entrypoint wants a CI environment,
# the vscode extension wants a toolchain), and a failed investigation is better raw
# material than a clean one.
TOPIC_ASSIGNMENTS = {
    "Python": (
        "この repo の Python パッケージがどう宣言されていて、実際の実行環境と合っているかを確かめる。"
        "pyproject.toml の requires-python と依存、`python -V`、`python -m rig_workbench.cli --help` "
        "が通るか。食い違いがあればどこで吸収されているか追う。"
    ),
    "機械学習": (
        "benchmarks/tasks/jp-natural-writing/results/ の JSON に入っている判定スコアを自分で集計し直し、"
        "hidden_check.py が report() で出している平均・中央値と一致するか確かめる。"
        "judge-calibration の n=16 の分布も見る。"
    ),
    "クラウドコンピューティング": (
        "action.yml と scripts/rig-action-entrypoint.sh を読んで、この repo が GitHub Actions 上で"
        "何を前提にしているかを把握し、その entrypoint をローカルで実際に走らせてどこで落ちるか確かめる。"
    ),
    "Web開発": (
        "web/ と vscode-extension/ に何が入っていて、どこまで手元で動かせるか確かめる。"
        "package.json、ビルドやテストのコマンドを実際に叩く。"
    ),
    "データベース設計": (
        "rig の実行状態がどこにどんな形で保存されているかを追う。rig_workbench/ の runstate と provenance、"
        "tests/test_runstate.py と tests/test_provenance.py を実際に走らせて、生成される構造を見る。"
    ),
    "リモートワーク": (
        "scripts/notify.py を読み、--dry-run で実際に叩いて Slack/Teams のペイロードがどう組み立てられるか"
        "確かめる。tests/test_notify.py も走らせる。webhook を持っていない状態で何ができて何ができないか。"
    ),
    "セキュリティ対策": (
        "scripts/sast_adapter.py の run 形式を実際に走らせてみて、何が要求され何が落ちるか確かめる。"
        "tests/test_injection_scan.py と tests/test_mcp_scan.py も走らせ、何を検出して何を見落とす設計か読む。"
    ),
    "チーム開発": (
        "git log で、この repo に入って直ったバグを 1 件選んで最初から最後まで追う。"
        "`git log --oneline`、`git show <sha>` で実際の diff を見て、入った経緯と直った経緯を確かめる。"
    ),
}

INVESTIGATE_PROMPT = """あなたはこの repo で実際に手を動かして調べます。記事は書きません。

作業指示:
{assignment}

やり方は指定しません。実際にコマンドを打ち、ファイルを読み、出力を見てください。
うまくいかなかった手順、途中でやめた脇道も、消さずにそのまま残してください。
最後に成功した手順だけを並べ直さないこと。

作業しながら、以下の形式のログだけを出力してください。JSON にはしないこと。

## 環境
date: <`date` の出力そのまま>
HEAD: <`git -C {repo} rev-parse --short HEAD` の出力>
python: <`python -V` の出力>

## step 1: <この手順で何をしたか一行>
$ <実際に打ったコマンド>
<実際の出力。長ければ関係する行だけを、原文のまま貼る。要約しない>
所感: <一行。分からなかったこと、意外だったことがあれば書く。なければ書かない>
結果: 解決 / 未解決 / 脇道

## step 2: ...

規則:
- 貼る出力は実際に見たものだけ。手で書き直したり整えたりしないこと。
- step の数は決まっていません。3 でも 9 でもよい。
- ログ全体で 4000 字以内。
- ログ以外の文章（前置き・まとめ・提案）は一切出力しないこと。
- ツール呼び出しは 15 回程度まで。完全に解明する必要はない。行き詰まったら未解決のまま出す。"""


STEP_RE = re.compile(r"^## step\s*\d+\s*[:：]\s*(.+)$", re.MULTILINE)


def parse_log_steps(log: str) -> list[dict]:
    """Split an investigation log into steps, with the size of each step's raw output.

    The size is the point. It is the only unfaked signal in the log about where the
    work actually went, and it is what the section budget is computed from.
    """
    matches = list(STEP_RE.finditer(log))
    steps = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(log)
        body = log[m.end():end]
        outcome = ""
        om = re.search(r"^結果[:：]\s*(\S+)", body, re.MULTILINE)
        if om:
            outcome = om.group(1)
        steps.append({"n": i + 1, "header": m.group(1).strip(), "weight": len(body),
                      "outcome": outcome, "raw": body.strip()})
    return steps


# ~250 chars of the target length belong to no step (the opening line, whatever the
# writer says on the way out). The rest is distributed by log weight.
SKELETON_TARGET = 1750
SKELETON_FREE = 250
SKELETON_MIN = 70
SKELETON_MAX = 700


def build_skeleton(steps: list[dict]) -> str:
    """Turn the log's real shape into a per-section character budget.

    Proportional to log weight, clipped so that no step vanishes and no step eats the
    article. Clipping distorts the proportions slightly and that is fine — the property
    that matters is that the distribution comes from outside the writer.
    """
    if not steps:
        return ""
    budget = SKELETON_TARGET - SKELETON_FREE

    # Clipping each step independently to [MIN, MAX] does not preserve the sum: with many
    # steps, sum(max(MIN, share)) runs far past `budget`. Measured, fieldnote articles came
    # out at 2854-4095 chars against a 1500-2500 band, which made its score incomparable to
    # every other arm. So keep only as many steps as the budget can actually pay for, then
    # renormalise after clipping.
    keep = sorted(steps, key=lambda s: s["weight"], reverse=True)[: max(1, budget // SKELETON_MIN)]
    keep = sorted(keep, key=lambda s: s["n"])

    total = sum(s["weight"] for s in keep) or 1
    raw = [max(SKELETON_MIN, min(SKELETON_MAX, int(round(budget * s["weight"] / total))))
           for s in keep]
    over = sum(raw)
    if over > budget:
        scale = budget / over
        raw = [max(SKELETON_MIN, int(round(c * scale))) for c in raw]

    lines = []
    for s, chars in zip(keep, raw):
        tail = f"（{s['outcome']}のまま）" if s["outcome"] in ("未解決", "脇道") else ""
        lines.append(f"- step {s['n']}: {s['header']}  … 目安 {chars}字{tail}")
    return "\n".join(lines)


WRITE_PROMPT = """あなたは以下の作業ログを書いた本人です。この作業を記事にしてください。

作業ログ:
\"\"\"
{log}
\"\"\"

節の構成と分量（ログの各手順に対応。順序も分量もこの通りにすること）:
{skeleton}

規則:
- ログに出てこない固有名詞・パス・コマンド・バージョン・エラー文字列・数値を書かないこと。
  書けることだけ書く。足りないと感じても補わない。
- 上の表にない節を作らないこと。「はじめに」「まとめ」「おわりに」「今後の展望」
  「参考リンク」は作らない。
- 未解決のまま終わった手順は、未解決のまま書くこと。解決したように書き直さない。
- 読者向けの一般論・用語解説・教訓を足さないこと。この記事は報告であって解説ではない。
- タイトルは調べた対象そのものを短く指すこと。「〜とは」「〜の勘所」「〜する方法」
  「〜を考える」は使わない。
- 本文にコマンドや出力を貼るときは、ログにある文字列をそのまま使うこと。

{length_spec}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""


# --------------------------------------------------------------- containment gate

# ASCII runs that assert something checkable: identifiers, paths, commands, versions.
_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-./]{2,}")
# Multi-digit numbers and dotted versions. Single digits are exempt: 「3つ」 is prose,
# not a claim, and requiring it to be in the log produced nothing but noise.
_NUM_RE = re.compile(r"\d+(?:\.\d+)+|\d{2,}")

# ASCII that is vocabulary rather than evidence. Deliberately tiny — every addition
# here is a hole the writer can put an unsourced claim through.
_ALLOW = {"ai", "ok", "ng", "url", "pc", "os", "it", "json", "yaml", "cli", "api"}


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).lower()


def containment_violations(article: str, log: str) -> list[str]:
    """Specifics in `article` with no counterpart in `log`.

    Substring containment, not token equality, and deliberately lenient: an article
    that writes `test_runstate.py` where the log has `tests/test_runstate.py` is
    grounded. The gate is here to catch invention, not to police citation style.
    """
    hay = _norm(log)
    body = _norm(article)
    bad: list[str] = []
    for m in list(_IDENT_RE.finditer(body)) + list(_NUM_RE.finditer(body)):
        tok = m.group(0).strip("./-")
        if len(tok) < 3 or tok in _ALLOW or tok in bad:
            continue
        if tok not in hay:
            bad.append(tok)
    return bad


CONTAINMENT_REVISE_PROMPT = """以下の記事に、作業ログに存在しない語が含まれています。

記事:
\"\"\"
{text}
\"\"\"

ログに存在しない語:
{tokens}

作業ログ:
\"\"\"
{log}
\"\"\"

これらの語を、ログにある事実に置き換えるか、その部分ごと削除してください。
別の具体を新しく足さないこと。削って短くなった分を一般論で埋めないこと。
節の構成と分量は変えないこと:
{skeleton}

{length_spec}

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"title": "<タイトル>", "description": "<本文>"}}"""


def generate_fieldnote(topic: str, model: str, max_rounds: int = 3) -> dict:
    """Investigate this repo, then report what happened, then delete what was invented."""
    assignment = TOPIC_ASSIGNMENTS.get(topic)
    if assignment is None:
        raise SystemExit(f"fieldnote has no assignment for topic {topic!r}")

    log = run_claude(
        INVESTIGATE_PROMPT.format(assignment=assignment, repo=REPO_ROOT),
        model,
        INVESTIGATE_TOOLS,
        timeout=900,
        attempts=2,
    )
    steps = parse_log_steps(log)
    skeleton = build_skeleton(steps)

    out = run_claude_json(
        WRITE_PROMPT.format(log=log, skeleton=skeleton, length_spec=LENGTH_SPEC), model, []
    )
    rounds = 1
    gate_log = []

    for _ in range(max_rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        bad = containment_violations(text, log)
        gate_log.append({"round": rounds, "verdict": "PASS" if not bad else "FAIL", "issues": bad})
        if not bad:
            break
        out = run_claude_json(
            CONTAINMENT_REVISE_PROMPT.format(
                text=text,
                tokens="\n".join(f"- {t}" for t in bad),
                log=log,
                skeleton=skeleton,
                length_spec=LENGTH_SPEC,
            ),
            model,
            [],
        )
        rounds += 1

    return {
        "title": out["title"],
        "description": out["description"],
        "rounds": rounds,
        "gate_log": gate_log,
        "log_steps": len(steps),
        "log_chars": len(log),
        # Carried so fieldpaste can paste this run's own output. Re-investigating would
        # cost a second 15-minute call and produce a log describing different work than
        # the article was written from.
        "_steps": steps,
    }


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


# ------------------------------------------------------------------------- relay
#
# Every gate so far has attacked the revision loop. This attacks the emission.
#
# The observation that motivates it: `bare` runs zero revision rounds and still scores
# 88.5. So the tidying is not something the revision loop adds — it is something a model
# does on its own whenever it is asked to produce a finished article. The recap, the
# callback to the intro's promise, the 「まとめ」, the tied-off ending all arrive in the
# last stretch of any single emission. Revision rounds only re-apply that closure, which
# is why rig (74.0) and riglint (83.0) land in the same band.
#
# So relay never asks for an article. The draft is accumulated by K independent passes,
# each a fresh process that sees only the last TAIL characters — a raw slice starting
# mid-word — plus the title and topic. No pass is told K, its index, or the remaining
# budget, so no pass ever knows it is the last one, and no pass writes a conclusion.
# The harness cuts each continuation strictly inside a sentence before appending, so the
# next pass always resumes from a broken fragment.
#
# Three things the judge credits in human writing fall out without being requested:
#   - the unfulfilled promise: pass 1 writes 「本記事では〜3つの観点から」 and pass 5 has
#     never seen it, so the three observations never arrive
#   - harvested closure: each pass reaches for its recap, the harness slices it off
#     mid-sentence and buries it mid-body, where a half-written conclusion reads as a
#     digression that died
#   - forgotten redefinition: pass 5 re-introduces a term pass 2 already defined
#
# Nothing here is a criterion, which is the point. The lint arm failed because a
# checkable requirement has a uniform satisfier and the model found it (short sentences
# at regular intervals). relay asks for nothing, checks nothing, and scores nothing; the
# artifact is defective because of what each pass was denied, not what it was told. A
# maximally cooperative model could not tidy this text: no process in the pipeline ever
# holds it whole. The only component that sees all of it is a string join.

RELAY_TAIL = 260      # chars handed forward; the single tuning knob
RELAY_PASSES = 6
RELAY_TARGET = 2000   # ~333 per pass, inside the 1500-2500 band

RELAY_OPEN_PROMPT = """タイトル: {title}
テーマ: {topic}

この記事の書き出しを{budget}字程度書いてください。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"continuation": "<本文>"}}"""

RELAY_CONT_PROMPT = """タイトル: {title}
テーマ: {topic}

下は書きかけの原稿の末尾です。文の途中で切れています。

---
{tail}
---

この続きを{budget}字程度書いてください。前の部分の要約・言い直し・修正はしないこと。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと。
本文中の改行は \\n とエスケープすること:
{{"continuation": "<続きの本文>"}}"""


def _strip_tail_echo(segment: str, tail: str) -> str:
    """Drop a restatement of the tail from the front of a continuation.

    Handed a fragment ending mid-word and told to continue, the model tends to repeat the
    fragment before carrying on. Appending that verbatim produced visible seams —
    'JWTとセッションそJWTとセッションそ' — which read as generation breakage rather than as
    a human's dropped thread. Longest overlap first so the whole echo goes, not part.
    """
    if not tail:
        return segment
    for size in range(min(len(tail), len(segment)), 5, -1):
        if segment.startswith(tail[-size:]):
            return segment[size:].lstrip()
    return segment


def _cut_midsentence(segment: str, budget: int, seed: str) -> str:
    """Truncate so the segment ends strictly inside a sentence.

    Load-bearing: if a tail arrives sentence-final, the next pass opens a fresh clean
    paragraph and the seam stops doing any work. The jitter keeps the cut from landing
    at a fixed offset, which would be its own regular interval.
    """
    stops = "。！？!?」）\n"
    text = segment[: budget + 80]
    last = max((text.rfind(c, 0, budget) for c in stops), default=-1)
    jitter = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % 40 + 14
    cut = min(max((last + 1 if last >= 0 else 0) + jitter, 40), len(text))
    while cut < len(text) and text[cut - 1] in stops:
        cut += 1
    return text[:cut]


def generate_relay(topic: str, model: str) -> dict:
    """Accumulate the article from passes that never see it whole."""
    title = run_claude(
        f"次のテーマの技術ブログ記事のタイトルを1行だけ出力してください。他の文字列は不要です。\nテーマ: {topic}",
        model, [],
    ).strip().splitlines()[0].strip()

    per = RELAY_TARGET // RELAY_PASSES
    draft = ""

    for k in range(RELAY_PASSES):
        tail = draft[-RELAY_TAIL:]
        prompt = (RELAY_OPEN_PROMPT if k == 0 else RELAY_CONT_PROMPT).format(
            title=title, topic=topic, tail=tail, budget=per
        )
        # The design argued for raw text here, on the grounds that a JSON container gives
        # the model something to round off inside. Measured, the cost ran the other way:
        # raw `claude -p` appends the model's own working to the prose, and ten fragments
        # like 「309字、目標の333字に近い範囲なので、このまま出力します」 landed in the
        # articles. The judge quoted them back and the arm scored 96.0, worse than bare.
        # An envelope the commentary can sit outside of is the cheaper trade.
        segment = str(run_claude_json(prompt, model, []).get("continuation", "")).strip()
        segment = _strip_tail_echo(segment, tail)
        draft += _cut_midsentence(segment, per, f"{topic}:{k}")

    return {"title": title, "description": draft, "rounds": RELAY_PASSES}


# --------------------------------------------------------------- closure excision
#
# What the writer arm has left, measured rather than inferred. Its four verdicts all
# objected to the same thing after crediting its specificity — 「各段落が必ず箴言的な一文
# で締まる均質な構成」 — and counting paragraph-final sentences confirms it:
#
#   arm         reflective/reserved paragraph endings    para-final length mean/stdev
#   human                 0.3%                                  89.7 / 80.0
#   writer               24.7%                                  47.6 / 22.2
#   bare                  0.0%                                  65.7 / 17.8
#
# Humans essentially never close a paragraph with a reflection. writer does it in a
# quarter of them, because its ledger carries 未解決 entries and the model converts each
# one into a closing line. The grounding worked; the habit of rounding off did not go
# away, it just found new material.
#
# Telling the model to stop is the move that has failed four times: a stated criterion
# gets satisfied uniformly, and uniform is the tell. So this is not told to the model at
# all. The harness deletes the offending sentence after generation. There is no
# instruction to comply with, no criterion to perform, and nothing the generator can do
# differently — it never learns the excision happened.
#
# Note the second column too: human paragraph endings vary enormously in length (stdev
# 80) and every arm is uniform (6-34). Excision moves that as a side effect, since
# whatever preceded the closer becomes the new ending and those vary on their own.

_CLOSURE_MARKERS = re.compile(
    r"(のだと思う|のかもしれない|気づいた|確信が持て|わからない|書いておく|残しておく"
    r"|ということだ|のだろう|に尽きる|ではないか|と考えている|学んだ|教訓"
    r"|大切だ|重要だ|べきだろう|かもしれません|のだと思います)"
)


def _split_sentences(paragraph: str) -> list[str]:
    return [x for x in re.split(r"(?<=[。！？])", paragraph) if x.strip()]


def excise_closures(text: str) -> tuple[str, int]:
    """Delete paragraph-final sentences that read as a rounding-off.

    Returns the edited text and the number of sentences removed. A paragraph is left
    alone when it holds only one sentence — removing that would delete the paragraph
    rather than its closer.
    """
    out, cut = [], 0
    for para in re.split(r"(\n\s*\n)", text):
        stripped = para.strip()
        if not stripped or stripped.startswith("#") or para.isspace():
            out.append(para)
            continue
        sentences = _split_sentences(para)
        if len(sentences) >= 2 and _CLOSURE_MARKERS.search(sentences[-1]):
            out.append("".join(sentences[:-1]).rstrip())
            cut += 1
        else:
            out.append(para)
    return "".join(out), cut


def generate_writercut(topic: str, model: str) -> dict:
    """writer, with its paragraph closers removed by the harness afterwards."""
    result = generate_writer(topic, model)
    body, cut = excise_closures(result["description"])
    result["description"] = body
    result["closures_cut"] = cut
    return result


# ------------------------------------------------------- pasted artifacts (fieldpaste)
#
# Measured against the human corpus, per article:
#
#            code  list  links  images  headings
#   human     0.2   4.7   22.5     4.4      9.8
#   writer    0.0   0.2    0.0     0.0      4.0
#   bare      0.0   0.0    0.0     0.0      1.5
#
# Real Qiita posts average 22.5 links and 4.4 images. Every arm has zero of either. And
# this is not incidental to the score: judging the human corpus, the reasons given were
# 「実在URLの膨大な羅列」, 「実データの表・画像URL」, 「自作スクショによる端末比較」,
# 「Twitterリンク」. A human tech post is a document with artifacts pasted into it. Every
# arm writes an essay.
#
# Asking the model for code blocks would reopen the fabrication problem — it would invent
# plausible output, which is what the v2 quota bought. But fieldnote already ran the
# commands: its log holds the real invocation and the real stdout. So the harness pastes
# them, verbatim, and the model is not asked for anything. Nothing here can be performed
# or gamed, because the generator is not told it happens.

PASTE_MAX_CHARS = 240      # per block; long stdout is truncated, not summarised
PASTE_MAX_BLOCKS = 4       # a listing dump would swamp the prose and the length band


def _artifact_blocks(steps: list[dict]) -> list[str]:
    """Fenced blocks of the real command and its real output, per step."""
    blocks = []
    for step in steps:
        lines = [ln for ln in step.get("raw", "").splitlines() if ln.strip()]
        command = next((ln for ln in lines if ln.lstrip().startswith("$")), "")
        if not command:
            continue
        after = lines[lines.index(command) + 1:]
        output = [ln for ln in after if not ln.startswith("結果")][:6]
        body = "\n".join([command.strip()] + output)[:PASTE_MAX_CHARS]
        blocks.append(f"```\n{body}\n```")
    return blocks


def paste_artifacts(article: str, steps: list[dict]) -> tuple[str, int]:
    """Insert real command/output blocks after the article's section headings.

    Sections and steps are paired in order, which is the pairing the skeleton already
    imposed on the writing. When the article has no headings the blocks go after the
    opening paragraphs instead, so an arm that ignored the structure still gets its
    artifacts rather than silently getting none.
    """
    blocks = _artifact_blocks(steps)[:PASTE_MAX_BLOCKS]
    if not blocks:
        return article, 0

    parts = re.split(r"(?m)^(#{2,6} .*)$", article)
    if len(parts) > 1:
        rebuilt, used = [parts[0]], 0
        for i in range(1, len(parts), 2):
            heading, body = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
            rebuilt.append(heading)
            if used < len(blocks):
                para_end = body.find("\n\n", 1)
                cut = para_end + 2 if para_end != -1 else len(body)
                rebuilt.append(body[:cut] + "\n" + blocks[used] + "\n" + body[cut:])
                used += 1
            else:
                rebuilt.append(body)
        return "".join(rebuilt), used

    paragraphs = article.split("\n\n")
    for offset, block in enumerate(blocks):
        at = min(1 + offset * 2, len(paragraphs))
        paragraphs.insert(at, block)
    return "\n\n".join(paragraphs), len(blocks)


def _fieldnote_public(result: dict) -> dict:
    result.pop("_steps", None)
    return result


def generate_fieldpaste(topic: str, model: str) -> dict:
    """fieldnote, with its own real command output pasted back in by the harness."""

    result = generate_fieldnote(topic, model)
    body, pasted = paste_artifacts(result["description"], result.get("_steps", []))
    result["description"] = body
    result["artifacts_pasted"] = pasted
    result.pop("_steps", None)   # raw log text, too bulky for the result record
    return result


LIVE_ARMS = {
    "bare": ("bare — 1 shot, no gate", generate_bare),
    "selfrev": ("self-revise — same rounds, no gate (compute control)", generate_selfrev),
    "rig": ("rig v1 — generic gate, self-verified", generate_rig),
    "rig2": ("rig v2 — quota gate, cross-model verifier (regressed)", generate_rig_v2),
    "rig3": ("rig v3 — prohibitions only, self-verified", generate_rig_v3),
    "rig1x": ("rig v1 criteria, cross-model verifier (ablation)", generate_rig_v1_xmodel),
    "riglint": ("rig + lint.py — mechanical detector as verifier", generate_riglint),
    "fieldnote": ("fieldnote — investigate the repo, report it, gate on containment",
                  lambda t, m: _fieldnote_public(generate_fieldnote(t, m))),
    "writer": ("writer's ledger — closed inventory of real artifacts", generate_writer),
    "relay": ("relay — passes that never see the article whole", generate_relay),
    "writercut": ("writer + harness-side excision of paragraph closers", generate_writercut),
    "fieldpaste": ("fieldnote + harness-pasted real command output", generate_fieldpaste),
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
    global TOPICS

    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="generate for real instead of scoring fixtures")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--gen-model", default=DEFAULT_GEN_MODEL, help="live mode only; same for every arm")
    ap.add_argument(
        "--arms",
        default="bare,selfrev,rig,rig2",
        help="live mode only; comma-separated subset of " + ",".join(LIVE_ARMS),
    )
    ap.add_argument("--topics", type=int, default=len(TOPICS),
                    help="use only the first N topics (cheaper runs while iterating)")
    ap.add_argument("--json-out", type=Path, help="write the full result record here")
    args = ap.parse_args()

    TOPICS = TOPICS[: max(1, args.topics)]

    arms: dict[str, dict] = {}

    if args.live:
        names = [a.strip() for a in args.arms.split(",") if a.strip()]
        unknown = [n for n in names if n not in LIVE_ARMS]
        if unknown:
            raise SystemExit(f"unknown arm(s): {unknown}; known: {list(LIVE_ARMS)}")
        print(f"live mode — generator: {args.gen_model}, judge: {args.judge_model}")
        for name in names:
            # Generation is the expensive half — the writer arm once died on a KeyError
            # after bare and fieldnote had already produced their articles, and because
            # scoring happens only once every arm is collected, all of it was discarded.
            # A broken arm should cost its own results and nothing else.
            try:
                arms[name] = {"label": LIVE_ARMS[name][0],
                              "samples": collect_live(name, args.gen_model)}
            except Exception as exc:
                print(f"  [{name}] FAILED, continuing without it: "
                      f"{type(exc).__name__}: {exc}")
        if not arms:
            raise SystemExit("every arm failed")
    else:
        print(f"fixture mode — judge: {args.judge_model}")
        print("note: both arms are hand-written fixtures; use --live to measure rig itself")
        arms["bare"] = {"label": "narrow fixture (no gate)", "samples": collect_fixture("narrow")}
        arms["rig"] = {"label": "canonical fixture (gated)", "samples": collect_fixture("canonical")}

    for arm in arms.values():
        arm["samples"] = score_arm(arm["samples"], args.judge_model)
        arm["stats"] = report(arm["label"], arm["samples"])

    # --arms may omit either end (an ablation run comparing gate versions has no bare
    # arm), so the summary must degrade to "no baseline" rather than KeyError after the
    # expensive part has already succeeded.
    gated = [n for n in ("fieldnote", "riglint", "rig3", "rig2", "rig1x", "rig") if n in arms]
    baseline = arms["bare"]["stats"]["mean"] if "bare" in arms else None
    treatment = arms[gated[0]]["stats"]["mean"] if gated else None
    improvement = (baseline - treatment) if (baseline is not None and treatment is not None) else None
    pct = (improvement / baseline * 100) if improvement is not None and baseline else None

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
    if improvement is None:
        print("\n  (bare アームなし — 改善幅は算出せず、アーム間の相対比較のみ)")
    else:
        print(f"\n  bare → {best} 改善: {improvement:.1f} points ({pct:.1f}%)")

    # Same arm, same topics, same models, two runs: v1 measured 30.9 then 56.0. Gate
    # revisions differ by 10-20 points, i.e. less than one arm's own run-to-run spread,
    # so a single run cannot rank them. Say so where the ranking is printed.
    print("  ⚠ 単発実行の分散は同一アームで最大25点。ゲート版同士の優劣は反復実行が必要")

    if "selfrev" in arms and treatment is not None:
        # The number that decides whether the gate earned its keep, rather than the
        # extra rounds it happens to spend.
        gate_effect = arms["selfrev"]["stats"]["mean"] - treatment
        print(f"  selfrev → rig (ゲート単独の効果): {gate_effect:.1f} points")
        if gate_effect < 5:
            print("  ⚠ ゲートの寄与は計算量の増加と区別できない")

    if improvement is not None:
        print(f"  判定: {'PASS' if improvement >= 5 else 'FAIL'}")

    results = {
        "mode": "live" if args.live else "fixture",
        "judge_model": args.judge_model,
        "gen_model": args.gen_model if args.live else None,
        "arms": arms,
        "improvement_points": round(improvement, 2) if improvement is not None else None,
        "improvement_percent": round(pct, 1) if pct is not None else None,
        "compared_arm": best,
        "gate_effect_vs_selfrev": (
            round(arms["selfrev"]["stats"]["mean"] - treatment, 2)
            if "selfrev" in arms and treatment is not None else None
        ),
        "success": bool(improvement is not None and improvement >= 5),
    }

    if args.json_out:
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")

    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()

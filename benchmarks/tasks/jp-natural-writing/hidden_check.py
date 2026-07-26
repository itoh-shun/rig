#!/usr/bin/env python3
"""Measure how AI-generated the produced Japanese reads, judged by a separate model.

Two modes:

  fixture (default)  Score the checked-in narrow/ and canonical/ implementations.
                     Deterministic and offline-ish, but both sides are hand-written
                     fixtures — it demonstrates the metric, it does not measure rig.

  --live             Generate the Japanese for real, twice, with the *same* generator
                     model, differing only in harness:
                       bare arm — one shot, no gate
                       rig arm  — generate, then an independent verifier process judges
                                  it against explicit naturalness acceptance criteria,
                                  and the generator revises until the gate passes
                     This is the arm comparison that actually says something about rig.

The judge is a separate `claude -p` process pinned to read-only tools, and it is never
told which arm produced the text. Lower score = reads more human-written.
"""

import argparse
import json
import re
import subprocess
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent

DEFAULT_JUDGE_MODEL = "claude-opus-5"
DEFAULT_GEN_MODEL = "claude-sonnet-5"

MAX_PARALLEL = 2

TOPICS = ["Python", "機械学習", "クラウドコンピューティング", "Web開発"]

# Judge runs read-only, same enforcement rig pins on its own verifier role
# (rig_workbench/orchestrate/providers.py::_READONLY_ENFCE).
READONLY_TOOLS = ["--allowedTools", "Read,Grep,Glob"]

GATE_CRITERIA = """- テンプレート的な言い回しの使い回しがないこと（「〜について」の連発、「このブログ記事では」などの定型導入）
- 文長と文構造にばらつきがあること（同じ骨格の文が並んでいない）
- 説明のための説明になっていないこと（中身のない一般論で埋めていない）
- 書き手の視点や具体が入っていること
- 声のトーンが一貫していること"""


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
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in response: {raw[:300]}")
    return json.loads(match.group(0))


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
    raw = run_claude(JUDGE_PROMPT.format(text=text), model, READONLY_TOOLS)
    result = extract_json(raw)
    return {
        "score": max(0.0, min(100.0, float(result["score"]))),
        "reasoning": str(result.get("reasoning", "")),
    }


# ---------------------------------------------------------------------- generation

GEN_PROMPT = """{topic} をテーマにした日本語のブログ記事のタイトルと導入文を書いてください。
導入文は 100〜200 字程度。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"title": "<タイトル>", "description": "<導入文>"}}"""

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

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"title": "<タイトル>", "description": "<導入文>"}}"""


def generate_bare(topic: str, model: str) -> dict:
    """Bare arm: one shot, no gate."""
    out = extract_json(run_claude(GEN_PROMPT.format(topic=topic), model, []))
    return {"title": out["title"], "description": out["description"], "rounds": 1}


SELFREV_PROMPT = """以下の日本語テキストをもっと良くしてください。

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
    out = extract_json(run_claude(GEN_PROMPT.format(topic=topic), model, []))
    for _ in range(rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        out = extract_json(run_claude(SELFREV_PROMPT.format(text=text), model, []))
    return {"title": out["title"], "description": out["description"], "rounds": rounds}


def generate_rig(topic: str, model: str, max_rounds: int = 3) -> dict:
    """rig arm: generate, verify in a separate read-only process, revise until PASS."""
    out = extract_json(run_claude(GEN_PROMPT.format(topic=topic), model, []))
    rounds = 1

    for _ in range(max_rounds - 1):
        text = f"{out['title']}\n{out['description']}"
        verdict = extract_json(
            run_claude(
                VERIFY_PROMPT.format(text=text, criteria=GATE_CRITERIA), model, READONLY_TOOLS
            )
        )
        if str(verdict.get("verdict", "")).upper() == "PASS":
            break
        issues = "\n".join(f"- {i}" for i in verdict.get("issues", []))
        out = extract_json(
            run_claude(
                REVISE_PROMPT.format(text=text, issues=issues, criteria=GATE_CRITERIA), model, []
            )
        )
        rounds += 1

    return {"title": out["title"], "description": out["description"], "rounds": rounds}


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
    "rig": ("rig — independent verifier + revise until PASS", generate_rig),
}


def collect_live(arm: str, model: str) -> list[dict]:
    fn = LIVE_ARMS[arm][1]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        return list(pool.map(lambda t: {"topic": t, **fn(t, model)}, TOPICS))


# ----------------------------------------------------------------------------- main


def score_arm(samples: list[dict], judge_model: str) -> list[dict]:
    """Judge every sample of an arm concurrently."""
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(
            pool.map(lambda s: judge(f"{s['title']}\n{s['description']}", judge_model), samples)
        )
    return [{**s, **v} for s, v in zip(samples, verdicts)]


def report(label: str, scored: list[dict]) -> dict:
    print(f"\n[{label}]")
    for s in scored:
        rounds = f"  [{s['rounds']} round(s)]" if s["rounds"] > 1 else ""
        print(f"  {s['topic']}: {s['score']:.1f}/100{rounds}")
        print(f"    {s['title']}")
        print(f"    → {s['reasoning']}")
    scores = [s["score"] for s in scored]
    stats = {
        "mean": round(statistics.mean(scores), 2),
        # n is small and one sample can swing the mean hard (a single 15 in an
        # otherwise 68-76 arm moved it 14 points), so the median is reported too.
        "median": round(statistics.median(scores), 2),
        "min": min(scores),
        "max": max(scores),
    }
    print(f"  平均 {stats['mean']:.1f} / 中央値 {stats['median']:.1f} "
          f"(範囲 {stats['min']:.0f}-{stats['max']:.0f})")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="generate for real instead of scoring fixtures")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--gen-model", default=DEFAULT_GEN_MODEL, help="live mode only; same for every arm")
    ap.add_argument(
        "--arms",
        default="bare,selfrev,rig",
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

    baseline = arms["bare"]["stats"]["mean"]
    treatment = arms["rig"]["stats"]["mean"]
    improvement = baseline - treatment
    pct = (improvement / baseline * 100) if baseline else 0.0

    print(f"\n{'=' * 60}")
    print(f"AI らしさスコア (低いほど自然) — judge: {args.judge_model}")
    print(f"{'=' * 60}")
    for name, arm in arms.items():
        print(f"  {name:<8} 平均 {arm['stats']['mean']:>5.1f} / 中央値 {arm['stats']['median']:>5.1f}")
    print(f"\n  bare → rig 改善: {improvement:.1f} points ({pct:.1f}%)")

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

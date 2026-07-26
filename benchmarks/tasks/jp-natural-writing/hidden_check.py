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
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent

DEFAULT_JUDGE_MODEL = "claude-opus-5"
DEFAULT_GEN_MODEL = "claude-sonnet-5"

TOPICS = ["Python", "機械学習", "クラウドコンピューティング", "Web開発"]

# Judge runs read-only, same enforcement rig pins on its own verifier role
# (rig_workbench/orchestrate/providers.py::_READONLY_ENFCE).
READONLY_TOOLS = ["--allowedTools", "Read,Grep,Glob"]

GATE_CRITERIA = """- テンプレート的な言い回しの使い回しがないこと（「〜について」の連発、「このブログ記事では」などの定型導入）
- 文長と文構造にばらつきがあること（同じ骨格の文が並んでいない）
- 説明のための説明になっていないこと（中身のない一般論で埋めていない）
- 書き手の視点や具体が入っていること
- 声のトーンが一貫していること"""


def run_claude(prompt: str, model: str, extra_argv: list[str], timeout: int = 300) -> str:
    """Invoke headless claude and return its text output."""
    argv = ["claude", "-p", prompt, "--output-format", "text", "--model", model] + extra_argv
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=HERE)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


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


def collect_live(arm: str, model: str) -> list[dict]:
    fn = generate_bare if arm == "bare" else generate_rig
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda t: {"topic": t, **fn(t, model)}, TOPICS))
    return results


# ----------------------------------------------------------------------------- main


def score_arm(samples: list[dict], judge_model: str) -> list[dict]:
    """Judge every sample of an arm concurrently."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        verdicts = list(
            pool.map(lambda s: judge(f"{s['title']}\n{s['description']}", judge_model), samples)
        )
    return [{**s, **v} for s, v in zip(samples, verdicts)]


def report(name: str, scored: list[dict]) -> float:
    print(f"\n{name}")
    for s in scored:
        rounds = f"  [{s['rounds']} round(s)]" if s["rounds"] > 1 else ""
        print(f"  {s['topic']}: {s['score']:.1f}/100{rounds}")
        print(f"    {s['title']}")
        print(f"    → {s['reasoning']}")
    avg = sum(s["score"] for s in scored) / len(scored)
    print(f"  平均: {avg:.1f}/100")
    return avg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="generate for real instead of scoring fixtures")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--gen-model", default=DEFAULT_GEN_MODEL, help="live mode only; same for both arms")
    ap.add_argument("--json-out", type=Path, help="write the full result record here")
    args = ap.parse_args()

    if args.live:
        without_label = f"bare ({args.gen_model}, no gate)"
        with_label = f"rig ({args.gen_model} + verify/revise gate)"
        print(f"live mode — generator: {args.gen_model}, judge: {args.judge_model}")
        without = collect_live("bare", args.gen_model)
        with_ = collect_live("rig", args.gen_model)
    else:
        without_label = "narrow fixture (no gate)"
        with_label = "canonical fixture (gated)"
        print(f"fixture mode — judge: {args.judge_model}")
        print("note: both arms are hand-written fixtures; use --live to measure rig itself")
        without = collect_fixture("narrow")
        with_ = collect_fixture("canonical")

    without_scored = score_arm(without, args.judge_model)
    with_scored = score_arm(with_, args.judge_model)

    without_avg = report(f"[{without_label}]", without_scored)
    with_avg = report(f"[{with_label}]", with_scored)

    improvement = without_avg - with_avg
    pct = (improvement / without_avg * 100) if without_avg else 0.0

    results = {
        "mode": "live" if args.live else "fixture",
        "judge_model": args.judge_model,
        "gen_model": args.gen_model if args.live else None,
        "without_gate_avg": round(without_avg, 2),
        "with_gate_avg": round(with_avg, 2),
        "improvement_points": round(improvement, 2),
        "improvement_percent": round(pct, 1),
        "success": improvement >= 5,
        "samples": {"without_gate": without_scored, "with_gate": with_scored},
    }

    print(f"\n{'=' * 56}")
    print(f"AI らしさスコア (低いほど自然) — judge: {args.judge_model}")
    print(f"{'=' * 56}")
    print(f"  gate なし : {without_avg:.1f}/100")
    print(f"  gate あり : {with_avg:.1f}/100")
    print(f"  改善      : {improvement:.1f} points ({pct:.1f}%)")
    print(f"  判定      : {'PASS' if results['success'] else 'FAIL'}")

    if args.json_out:
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")

    sys.exit(0 if results["success"] else 1)


if __name__ == "__main__":
    main()

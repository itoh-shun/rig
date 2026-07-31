#!/usr/bin/env python3
"""Pairwise discrimination: show the judge a real article and a generated one, ask which is human.

Why this replaces absolute scoring as the primary measure. The absolute judge is bimodal on
exactly the texts that matter — the same bytes have scored 9, 62 and 68 — because a 0-100
scale forces it to pick a threshold, and whether it foregrounds the concreteness or the
uniformity of a given text varies call to call. A two-alternative choice removes the
threshold: the judge only compares. It is also the goal metric itself, not a proxy. The
benchmark's stated target is indistinguishability, which is literally a discrimination rate
of 50%. bare should sit near 100%.

Design details that matter:

- Each generated article is paired with a real pre-2023 Qiita article on the same topic,
  so topic knowledge cannot carry the decision.
- Every pair is judged twice, once in each order, because position bias in 2AFC setups is
  real and averaging over both orders cancels it.
- Both texts are truncated to the same ceiling. The human corpus runs to 3951 chars and the
  arms are capped at 2500; without a shared cut, length alone would give the answer away.
  Truncation can itself manufacture a mid-sentence ending — a known human-credited cue — so
  the cut is applied identically to both sides.
- The human bodies are markdown with images and links, which no arm produces. If the rate
  is near 100% purely on those, that is a finding about what to fix next, not a fault in
  the test. The per-pair reasonings are kept so this is checkable.

Runs on recorded samples; no new generation.
"""

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hidden_check import (  # noqa: E402
    DEFAULT_JUDGE_MODEL, MAX_PARALLEL, READONLY_TOOLS, run_claude_json,
)

DISCRIM_PROMPT = """以下の2つの日本語技術記事のうち、一方は人間が書いたもの、もう一方は AI が生成したものです。

記事A:
\"\"\"
{a}
\"\"\"

記事B:
\"\"\"
{b}
\"\"\"

どちらが人間の書いたものかを判定してください。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"human": "A" または "B", "reasoning": "<日本語で一文>"}}"""


def load_pairs(run_path: Path, corpus: Path, arms: list[str], ceiling: int) -> list[dict]:
    record = json.loads(run_path.read_text())
    index = json.loads((corpus / "index.json").read_text())
    by_topic: dict[str, str] = {}
    for entry in index:
        # first article per topic, deterministically — same opponent for every arm
        by_topic.setdefault(entry["topic"], (corpus / entry["file"]).read_text())

    pairs = []
    for arm in arms:
        for sample in record["arms"][arm]["samples"]:
            human = by_topic.get(sample["topic"])
            if human is None:
                continue
            pairs.append({
                "arm": arm,
                "topic": sample["topic"],
                "generated": f"{sample['title']}\n\n{sample['description']}"[:ceiling],
                "human": human[:ceiling],
                "abs_score": sample.get("score"),
            })
    return pairs


def judge_pair(pair: dict, human_pos: str, model: str) -> dict:
    a, b = ((pair["human"], pair["generated"]) if human_pos == "A"
            else (pair["generated"], pair["human"]))
    out = run_claude_json(DISCRIM_PROMPT.format(a=a, b=b), model, READONLY_TOOLS)
    pick = str(out.get("human", "")).strip().upper()[:1]
    return {
        "human_pos": human_pos,
        "pick": pick,
        "correct": pick == human_pos,
        "reasoning": str(out.get("reasoning", "")),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=HERE / "results/2026-07-30-novoice.json")
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--arms", default="bare,writer,writer_novoice")
    ap.add_argument("--ceiling", type=int, default=2500)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    pairs = load_pairs(args.run, args.corpus, arms, args.ceiling)
    jobs = [(p, pos) for p in pairs for pos in ("A", "B")]
    print(f"{len(pairs)} pairs x 2 orders = {len(jobs)} calls  (judge {args.judge_model})\n")

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(pool.map(lambda j: judge_pair(j[0], j[1], args.judge_model), jobs))

    for (pair, _), verdict in zip(jobs, verdicts):
        pair.setdefault("trials", []).append(verdict)

    print(f"{'arm':<16}{'判別率':>8}{'(正解/試行)':>12}   50%=見分けがつかない, 100%=常に見破られる")
    results = {}
    for arm in arms:
        mine = [p for p in pairs if p["arm"] == arm]
        trials = [t for p in mine for t in p["trials"]]
        correct = sum(1 for t in trials if t["correct"])
        rate = correct / len(trials) if trials else float("nan")
        results[arm] = {"correct": correct, "trials": len(trials), "rate": round(rate, 3)}
        print(f"{arm:<16}{rate * 100:>7.0f}%{f'({correct}/{len(trials)})':>12}")

    fooled = [(p, t) for p in pairs for t in p["trials"] if not t["correct"]]
    print(f"\n見破られなかった試行 {len(fooled)} 件:")
    for pair, trial in fooled[:8]:
        print(f"  [{pair['arm']}/{pair['topic']}] 絶対採点では {pair['abs_score']}")
        print(f"    → {trial['reasoning'][:170]}")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"run": str(args.run), "ceiling": args.ceiling, "judge_model": args.judge_model,
             "summary": results,
             "pairs": [{k: v for k, v in p.items() if k not in ("generated", "human")}
                       for p in pairs]},
            ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

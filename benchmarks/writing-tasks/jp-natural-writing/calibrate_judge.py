#!/usr/bin/env python3
"""Score known-human articles with the same blind judge the benchmark arms face.

The benchmark's entire claim rests on one number: how AI-written Opus 5 says a text
reads. That number has never been checked against text known to be human. Three
outcomes, and they are not equally survivable:

  human scores low (0-30)   The judge discriminates. Arm scores mean what they claim,
                            and the human mean is the target the arms are chasing.
  human scores middling     The judge has limited resolution. Gaps between arms are
                            real but the absolute scale is not.
  human scores high (60+)   The judge is measuring its own prior, not the text, and
                            every arm comparison in this benchmark is void.

Run fetch_human_corpus.py first. Bodies stay local; only the scores are worth keeping.
"""

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hidden_check import DEFAULT_JUDGE_MODEL, MAX_PARALLEL, judge  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--limit", type=int, default=16)
    ap.add_argument("--max-chars", type=int, default=2600,
                    help="truncate to the arms' length range so length is not the difference")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    index = json.loads((args.corpus / "index.json").read_text())[: args.limit]
    if not index:
        raise SystemExit("empty corpus — run fetch_human_corpus.py first")

    texts = [(r, (args.corpus / r["file"]).read_text()[: args.max_chars]) for r in index]
    print(f"judging {len(texts)} known-human articles with {args.judge_model}\n")

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(pool.map(lambda t: judge(t[1], args.judge_model), texts))

    scored = []
    for (record, _), verdict in zip(texts, verdicts):
        scored.append({**record, **verdict})
        print(f"  {record['topic']:<14} {verdict['score']:>5.1f}  ({record['created_at']}, "
              f"{record['chars']}字)")
        print(f"      {verdict['reasoning'][:150]}")

    scores = [s["score"] for s in scored]
    mean, median = statistics.mean(scores), statistics.median(scores)
    misread = sum(1 for s in scores if s >= 60)

    print(f"\n{'=' * 60}")
    print(f"既知の人間記事 (n={len(scores)}) — judge: {args.judge_model}")
    print(f"{'=' * 60}")
    print(f"  平均 {mean:.1f} / 中央値 {median:.1f} / 範囲 {min(scores):.0f}-{max(scores):.0f}")
    print(f"  60以上（AI と誤判定）: {misread}/{len(scores)} = {misread/len(scores)*100:.0f}%")
    print("\n  参考: bare 88.5 / rig 74.0 / riglint 83.0 (長文・字数対照あり)")
    if mean >= 60:
        print("\n  ⚠ 判定役は人間の文章も AI と判定している。アーム比較は無効。")
    elif mean >= 35:
        print("\n  ⚠ 分解能が低い。アーム間の差は読めても絶対値は信用できない。")
    else:
        print(f"\n  判定役は弁別できている。アームが目指すべき水準は {mean:.0f} 前後。")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"judge_model": args.judge_model, "n": len(scores), "mean": round(mean, 2),
             "median": round(median, 2), "misread_rate": round(misread / len(scores), 3),
             "samples": scored}, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

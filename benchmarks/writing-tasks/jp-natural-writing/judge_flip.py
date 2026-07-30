#!/usr/bin/env python3
"""Judge one text many times and read what changes when the verdict flips.

The arm that gets closest to human is also the least stable: on fixed material, every
generation that reached the human band carried sd 29-32, and the only two stable samples
were stably AI. The same bytes have scored 9, 62 and 68. So the instrument is not reading a
property of the text so much as choosing between two readings of it, and which one it picks
is where the remaining distance to human actually sits.

judge() keeps only the median verdict's reasoning, which is exactly the one that says
nothing about the flip. This keeps every reasoning, groups them by score band, and prints
them side by side so the two readings can be compared directly. There is no metric here —
the output is text to read.
"""

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hidden_check import (  # noqa: E402
    DEFAULT_JUDGE_MODEL, HUMAN_READ_THRESHOLD, MAX_PARALLEL, judge_once,
)

# Vocabulary the verdicts have used all session, split by which way it cuts. Counting these
# is a reading aid for spotting what the two groups dwell on; it is not evidence on its own.
CREDITS = re.compile(
    r"(具体|固有名詞|エラーメッセージ|未解決|不揃い|口語|脱線|生ログ|検証しなければ|実作業|痕跡)")
FAULTS = re.compile(
    r"(均質|同型|テンプレート|反復|定型|整いすぎ|一般論|網羅|所感|まとめ|要約|機械的)")


def load_texts(source: Path, limit: int) -> list[dict]:
    """Pick the most bimodal samples out of a recorded run or generation-variance file."""
    data = json.loads(source.read_text())
    rows = data.get("rows")
    if rows is None:
        rows = [s for arm in data["arms"].values() for s in arm["samples"]]
    scored = [r for r in rows if r.get("score_sd") is not None]
    scored.sort(key=lambda r: -r["score_sd"])
    return [{"label": r.get("topic") or f"#{r.get('nonce')}",
             "title": r["title"], "body": r["description"],
             "recorded": [int(x) for x in r.get("scores", [])]}
            for r in scored[:limit]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path,
                    default=HERE / "results/2026-07-30-generation-variance.json")
    ap.add_argument("--texts", type=int, default=2, help="how many bimodal texts to probe")
    ap.add_argument("--repeats", type=int, default=6)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    texts = load_texts(args.source, args.texts)
    jobs = [(t, i) for t in texts for i in range(args.repeats)]
    print(f"{len(texts)} texts x {args.repeats} judgments = {len(jobs)} calls "
          f"({args.judge_model})\n")

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(pool.map(
            lambda j: judge_once(f"{j[0]['title']}\n{j[0]['body']}", args.judge_model), jobs))

    out = []
    for text in texts:
        mine = [v for (t, _), v in zip(jobs, verdicts) if t is text]
        scores = [v["score"] for v in mine]
        low = [v for v in mine if v["score"] < HUMAN_READ_THRESHOLD]
        high = [v for v in mine if v["score"] >= HUMAN_READ_THRESHOLD]

        print("=" * 92)
        print(f"[{text['label']}]  記録時 {text['recorded']}  →  今回 {sorted(int(s) for s in scores)}"
              f"  (sd {statistics.pstdev(scores):.1f})")
        print("=" * 92)

        for name, group in (("人間と読んだ回", low), ("AI と読んだ回", high)):
            if not group:
                print(f"\n--- {name}: なし ---")
                continue
            credits = Counter(m for v in group for m in CREDITS.findall(v["reasoning"]))
            faults = Counter(m for v in group for m in FAULTS.findall(v["reasoning"]))
            print(f"\n--- {name} ({len(group)}/{len(mine)}) ---")
            print(f"  評価語 {dict(credits.most_common(6))}")
            print(f"  減点語 {dict(faults.most_common(6))}")
            for v in group[:2]:
                print(f"  [{v['score']:.0f}] {v['reasoning'][:230]}")

        out.append({"label": text["label"], "recorded": text["recorded"],
                    "scores": scores, "verdicts": mine})

    print("\n" + "=" * 92)
    print("同一テキストに対する二つの読み方の差を、上の講評本文で読むこと。")
    print("語のカウントは目印であって証拠ではない。")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"judge_model": args.judge_model, "repeats": args.repeats, "texts": out},
            ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

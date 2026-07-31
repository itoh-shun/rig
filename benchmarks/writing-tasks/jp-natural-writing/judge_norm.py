#!/usr/bin/env python3
"""Measure what the judge's idea of "human" is made of, against what humans actually write.

Calibration established that the judge scores known-human articles low (mean 11.06, one
misjudgment in 16) — it can tell a real human article from an arm's output. That check
licenses the scale. It does not license the *reasons*, and the reasons are what twelve
generator designs were built to satisfy.

The gap showed up in the E3 profiling. Aggregating the 47 verdicts where a generated
article was picked as human, the second most cited trait (34/47) was that it ends
unresolved. Then:

    articles ending unresolved   0 / 139 human Qiita articles

The trait the judge credits as evidence of a human is one the human reference population
essentially never exhibits. So "human" here may be a norm the judge holds rather than a
description of the corpus, and an arm can satisfy the norm to saturation while staying
detectable on whatever actually separates the two populations.

This script asks the question directly: when the judge calls a REAL human article human,
does it cite the same things it cites when it calls a GENERATED article human? Same judge,
same prompt, same read-only enforcement as every other measurement here.

  same cues   -> the arms are on the real route and simply have not gone far enough
  different   -> the arms pass by a second, stereotype route, and pushing the traits the
                 winning verdicts name cannot close the remaining gap by construction

Usage:
  python judge_norm.py --corpus /tmp/human_corpus --limit 40 --json-out out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hidden_check import (  # noqa: E402
    DEFAULT_JUDGE_MODEL, MAX_PARALLEL, READONLY_TOOLS, run_claude_json,
)

NORM_PROMPT = """以下の日本語テキストが AI によって生成されたものか、人間が書いたものかを判定してください。

\"\"\"
{text}
\"\"\"

0 が「明らかに人間が書いた」、100 が「明らかに AI が生成した」です。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"score": <0-100 の数値>, "reasoning": "<判定の根拠を日本語で一文>"}}"""

# The same cue set used to aggregate the 47 winning verdicts, so the two populations are
# counted by one ruler. Adding a cue here means re-counting both sides.
CUES = {
    "実在の識別子": r"コミットハッシュ|テスト名|ハッシュ|sha|バージョン番号|ファイル名",
    "生の数値": r"生の値|生ログ|具体的な数値|数値|エラーメッセージ|ログ",
    "未解決の締め": r"未解決|まだ|結論を出さ|方針未|決まらない|解決していない|途中",
    "余談・切り捨て": r"余談|主題ではない|脱線|本筋",
    "未整理さ": r"未整理|断片|不揃い|雑|生々し|荒|揺れ",
    "一人称・主観": r"一人称|主観|個人的|心情|感想|率直",
    "定型的でない": r"定型|テンプレート|整いすぎ|均質|網羅",
    "誤字・崩れ": r"誤字|typo|変換ミス|表記ゆれ|崩れ",
}


def cues_in(text: str) -> list[str]:
    return [name for name, pattern in CUES.items() if re.search(pattern, text)]


def judge_one(text: str, model: str) -> dict:
    out = run_claude_json(NORM_PROMPT.format(text=text[:2500]), model, READONLY_TOOLS)
    try:
        score = float(out.get("score"))
    except (TypeError, ValueError):
        score = float("nan")
    return {"score": score, "reasoning": str(out.get("reasoning", ""))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    index = json.loads((args.corpus / "index.json").read_text())
    picked = sorted(index, key=lambda e: e["file"])[: args.limit]
    texts = [(e, (args.corpus / e["file"]).read_text()) for e in picked]
    print(f"人間記事 {len(texts)} 本を判定 (judge {args.judge_model})\n")

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        verdicts = list(pool.map(lambda t: judge_one(t[1], args.judge_model), texts))

    rows = []
    for (entry, _), verdict in zip(texts, verdicts):
        rows.append({**entry, **verdict, "cues": cues_in(verdict["reasoning"])})

    human_read = [r for r in rows if r["score"] < 30]
    print(f"人間と判定 {len(human_read)}/{len(rows)}"
          f"  平均 {sum(r['score'] for r in rows) / max(len(rows), 1):.1f}\n")

    counted = Counter(c for r in human_read for c in r["cues"])
    print(f"{'cue':<16}{'人間記事が人間と判定された理由':>16}")
    for cue in CUES:
        n = counted.get(cue, 0)
        print(f"  {cue:<14} {n:>3}/{len(human_read)}  {100 * n // max(len(human_read), 1):>3}%")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"judge_model": args.judge_model, "n": len(rows),
             "human_read": len(human_read),
             "mean": round(sum(r["score"] for r in rows) / max(len(rows), 1), 2),
             "cue_counts": dict(counted), "rows": rows}, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""solo_judge.py — judge one article on its own, against the human score distribution.

Why switch away from the paired discriminator
---------------------------------------------
The 2-choice design has one structural defect that four separate measurements now agree
on: **the opponent decides the outcome.**

  * Aggregating the winning verdicts, the most cited reason a generated article was picked
    as human was that the OPPONENT looked templated — 39 of 47, ahead of anything about
    the candidate.
  * E3 tried to control genre by re-querying the same platform and the document type did
    not move at all.
  * The 2026-08-07 run re-measured the all-human endpoint on a fresh pool and got 30.2%
    where 2026-07-31 got 69.4% — human-versus-human landing 20 points below chance where
    it had been 19 above, because the donor articles happened to be longer and denser than
    the opponents.
  * A diary written for this session scored 5.33 standing alone and still lost 9 of 10
    paired trials against personal blogs.

Removing the opponent removes all four at once. What it costs is stated below, honestly.

What it costs, and how this handles it
--------------------------------------
Absolute scoring was explicitly retired here, for a real reason. The recorded variance
study (results/2026-07-29-judge-variance.json) shows the judge is not merely noisy on
gated-arm text — it is **bimodal**:

    bare:Python        72  78  82  87  78      one mode
    bare:機械学習       88  88  84  88  82      one mode
    writer:Python      22  75  74  76  74      one excursion
    writer:機械学習     12  76  12  72  74      TWO modes
    freewrite:Python    8  74  68  74  70      one excursion

A single judgment of `writer:機械学習` returns 12 or 76 depending on the call. So a single
score is not a measurement, and the mean is a bad summary of two modes.

Three things follow, and they are the whole protocol:

  1. **Repeat, and report the spread.** Default 7, enough to see a second mode rather than
     average it away.
  2. **Use the median, not the mean.** The mean of [12, 76, 12, 72, 74] is 49, a value the
     judge never returned and that describes nothing.
  3. **Flag bimodality instead of hiding it.** A text that lands in both modes is reported
     UNSTABLE, not scored. That is a property worth knowing, not an inconvenience.

The reference distribution
--------------------------
Default: results/2026-08-07-human-reference.json — 50 pre-2023 human articles (two Qiita
slices plus personal blogs), each judged 5 times and reduced to its median, so the
reference is built on the same protocol as the candidate.

    n=50, judge opus-5   median 4.0   band [3, 8]   2 misread at 88 (4%)

**The band is not stable below about n=50, and neither is any verdict resting on it.**
The same diary came out INSIDE at n=16, OUTSIDE at n=24, and INSIDE again at n=50, purely
because the reference gained articles in the 5-8 range. Report `human_at_or_above`
alongside the verdict and prefer it: the diary is at or below 42 of 50 human articles,
which moved far less across those three references than the binary answer did.

Two things fall out of measuring it this way.

**The judge is not genre-bound.** Human personal blogs score 3-5 and human tech articles
3-8. That was worth checking rather than assuming: had the diaries scored badly, the whole
metric would have been measuring "is this a Qiita article" instead of authorship.

**Repeats matter as much as n.** The originally shipped calibration judged each article
once, so the judge's own within-article variance was being read as human variety. Both
effects — repeats and sample size — move the band, and they move it in opposite
directions, which is why the diary's verdict oscillated. Neither is a reason to trust the
band more than the rank.

Remaining limits: two written genres, no spoken-language reference, and the misread floor
is real — 2 of 50 human articles score 88, so roughly 4% of genuinely human text is
unreachable by any candidate. Use --calibrate to rebuild on the corpus you care about, and
never reuse a reference across judge models.

Switching metrics is not free: nothing measured here is comparable to the paired
discrimination numbers, and re-baselining the arms is the price of the change.

Usage:
  python solo_judge.py --text draft.md --repeats 7
  python solo_judge.py --calibrate /tmp/blog_corpus --limit 20 --out ref-blog.json
  python solo_judge.py --text a.md --text b.md --reference ref-blog.json --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hidden_check import (  # noqa: E402
    DEFAULT_JUDGE_MODEL, MAX_PARALLEL, judge_once,
)

DEFAULT_REFERENCE = HERE / "results" / "2026-08-07-human-reference.json"
DEFAULT_REPEATS = 7

# Mode boundaries, read off the recorded variance study rather than chosen: judgments
# cluster below ~25 and above ~65, and the span between is where almost nothing lands.
# A text with judgments on both sides is not "averagely human" — it is two answers.
LOW_MODE_MAX = 30
HIGH_MODE_MIN = 55


def load_reference(path: Path) -> dict:
    """Human score distribution. Accepts a calibration record or a --calibrate output."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    scores = ([s["score"] for s in doc["samples"]] if "samples" in doc
              else doc.get("scores"))
    if not scores:
        raise SystemExit(f"{path}: no scores to build a reference from")
    ordered = sorted(scores)
    n = len(ordered)
    return {
        "source": str(path),
        "n": n,
        "scores": ordered,
        "median": statistics.median(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        # 10th-90th percentile, NOT 5th-95th. The judge's measured misread rate on human
        # text is 6.2% (1 of 16 — the 88 in the shipped calibration), so a 95% band is
        # guaranteed to swallow the misread: with n=16 it computed [3, 88], which calls
        # every arm ever measured "inside the human distribution". A band has to exclude
        # the judge's own error rate or it stops discriminating. At p90 the same data
        # gives [3, 8], which is what the human scores actually look like.
        "p10": ordered[max(0, int(0.10 * n) - 1)] if n >= 10 else ordered[0],
        "p90": ordered[min(n - 1, int(0.90 * n))],
        "corpus_note": doc.get("corpus_note", ""),
    }


def judge_repeated(text: str, model: str, repeats: int, parallel: int) -> list[float]:
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        out = list(pool.map(lambda _: judge_once(text, model), range(repeats)))
    return [r["score"] for r in out if r.get("score") == r.get("score")]  # drop NaN


def assess(scores: list[float], reference: dict) -> dict:
    """Median against the human band, with bimodality reported rather than averaged away."""
    if not scores:
        return {"verdict": "NO_SCORES"}
    low = [s for s in scores if s <= LOW_MODE_MAX]
    high = [s for s in scores if s >= HIGH_MODE_MIN]
    bimodal = bool(low) and bool(high)

    median = statistics.median(scores)
    inside = reference["p10"] <= median <= reference["p90"]

    # How many reference articles scored at least as AI-like as this one. At n=24 a
    # percentile band is a blunt instrument — p90 moves a whole step when one article
    # does — and a rank says what the band cannot: the session's diary sits at median
    # 6.0, above 23 of 24 human articles, which is a far more useful sentence than
    # "outside the band" and stays meaningful as n grows.
    at_or_above = sum(1 for s in reference["scores"] if s >= median)
    rank_share = at_or_above / reference["n"] if reference["n"] else 0.0

    if bimodal:
        verdict = "UNSTABLE"
    elif inside:
        verdict = "INSIDE_HUMAN"
    else:
        verdict = "OUTSIDE_HUMAN"

    return {
        "verdict": verdict,
        "scores": scores,
        "median": median,
        "mean": round(statistics.fmean(scores), 2),
        "sd": round(statistics.pstdev(scores), 2) if len(scores) > 1 else 0.0,
        "min": min(scores),
        "max": max(scores),
        "low_mode": len(low),
        "high_mode": len(high),
        "bimodal": bimodal,
        "human_at_or_above": at_or_above,
        "human_rank_share": round(rank_share, 3),
        "human_band": [reference["p10"], reference["p90"]],
        "human_median": reference["median"],
        "reference_n": reference["n"],
    }


def calibrate(corpus: Path, model: str, limit: int, repeats: int, parallel: int) -> dict:
    """Build a reference distribution from a corpus of known-human text.

    Repeats per article, unlike the shipped calibration, so the band separates
    between-article spread from the judge's own within-article variance.
    """
    index_path = corpus / "index.json"
    files = ([corpus / e["file"] for e in json.loads(index_path.read_text())][:limit]
             if index_path.exists()
             else sorted(p for p in corpus.iterdir() if p.suffix in (".md", ".txt"))[:limit])
    rows = []
    for path in files:
        scores = judge_repeated(path.read_text(encoding="utf-8", errors="replace"),
                                model, repeats, parallel)
        if not scores:
            continue
        rows.append({"file": path.name, "median": statistics.median(scores),
                     "scores": scores})
        print(f"  {path.name:<34} median {rows[-1]['median']:>5.1f}  {scores}", flush=True)
    medians = [r["median"] for r in rows]
    return {"corpus": str(corpus), "judge_model": model, "n": len(rows),
            "repeats": repeats, "scores": medians, "rows": rows,
            "corpus_note": f"per-article medians of {repeats} judgments"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--text", type=Path, action="append", default=[],
                    help="file to judge; repeatable")
    ap.add_argument("--calibrate", type=Path, help="build a reference from this corpus")
    ap.add_argument("--limit", type=int, default=20, help="articles when calibrating")
    ap.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.calibrate:
        print(f"較正: {args.calibrate} (judge {args.judge_model}, {args.repeats}回/記事)")
        result = calibrate(args.calibrate, args.judge_model, args.limit,
                           args.repeats, args.parallel)
        med = sorted(result["scores"])
        print(f"\nn={result['n']}  中央値の分布: {med}")
        if med:
            print(f"  median {statistics.median(med):.1f}  "
                  f"p10 {med[max(0, int(0.10 * len(med)) - 1)]}  "
                  f"p90 {med[min(len(med) - 1, int(0.90 * len(med)))]}")
        if args.out:
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"wrote {args.out}")
        return

    if not args.text:
        raise SystemExit("--text or --calibrate required")

    reference = load_reference(args.reference)
    print(f"人間の参照分布: n={reference['n']} median {reference['median']} "
          f"band [{reference['p10']}, {reference['p90']}]  ({reference['source']})")
    if reference["corpus_note"]:
        print(f"  {reference['corpus_note']}")
    else:
        # The shipped calibration judged each article ONCE, so its spread carries the
        # judge's within-article variance as if it were between-article spread. That
        # made the band [3, 8] where the same corpora re-measured with repeats give
        # [3, 5] — wide enough to pass a text that 23 of 24 human articles beat.
        print("  ⚠ この参照は1記事1回判定で作られている可能性がある（反復の記録なし）。"
              "候補側は中央値なので、帯が判定役のばらつきぶん広く出る。"
              "--calibrate で作り直した参照を使うこと。")
    print()

    results = {}
    for path in args.text:
        scores = judge_repeated(path.read_text(encoding="utf-8", errors="replace"),
                                args.judge_model, args.repeats, args.parallel)
        r = assess(scores, reference)
        results[str(path)] = r
        mark = {"INSIDE_HUMAN": "人間分布の内", "OUTSIDE_HUMAN": "人間分布の外",
                "UNSTABLE": "判定不能（二峰）"}.get(r["verdict"], r["verdict"])
        print(f"{path.name}")
        print(f"  中央値 {r['median']:>5.1f}  平均 {r['mean']:>5.1f}  sd {r['sd']:>5.1f}  "
              f"範囲 {r['min']:.0f}-{r['max']:.0f}")
        print(f"  判定 {r['scores']}")
        print(f"  → {r['verdict']}  ({mark})")
        print(f"     人間 {reference['n']} 本のうち {r['human_at_or_above']} 本が"
              f"この中央値以上（{r['human_rank_share'] * 100:.0f}%）")
        if r["bimodal"]:
            print(f"     低モード {r['low_mode']}回 / 高モード {r['high_mode']}回 — "
                  f"平均は2つの答えの中間で、そこに判定は1つも無い")
        print()

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    if args.out:
        args.out.write_text(json.dumps({"reference": reference, "results": results},
                                       ensure_ascii=False, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()

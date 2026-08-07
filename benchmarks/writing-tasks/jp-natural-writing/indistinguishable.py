#!/usr/bin/env python3
"""indistinguishable.py — is an arm EQUIVALENT to human text, not merely un-refuted?

The acceptance criterion this answers is "最終的に人間が書いた文章と相違ない結果であればいい".
Turning that into something a run can pass or fail takes two corrections, and both of them
change the number people would otherwise aim at.

Correction 1: the target is not 50%
-----------------------------------
The intuition is that indistinguishable means the judge is at chance. It is not, because
this discriminator is not at chance on human text. The positive control
(results/2026-07-31-mde-positive-control.json) mixed real human donor prose into an arm's
articles and re-judged at every level:

    人間テキストの混入率     判別率
      0.00 (アーム単体)      98.4%   61/62
      0.125                98.4%   61/62
      0.25                 88.7%   55/62
      0.50                 79.0%   49/62
      0.75                 74.2%   46/62
      1.00 (全文が人間)      69.4%   43/62    <- 床
      帰無 (バイト同一)      100.0%   62/62

At level 1.00 the candidate *is* a human article, and the judge still picks it correctly
69.4% of the time. So on this instrument 「人間と相違ない」 means **69.4%, not 50%**, and an
arm reported at 70% is not "still 20 points from human" — it is at the floor. Anyone
optimising toward 50% here is optimising toward a number no human text achieves.

Correction 2: absence of evidence is not equivalence
-----------------------------------------------------
Every comparison in this benchmark so far is a *difference* test: did the arm move against
a baseline. A difference test that fails to reject tells you nothing about sameness —
especially here, where the MDE is 19.4 points treating 31 pairs as independent and 29.0 on
the correct 8-article clustering. An underpowered difference test fails to reject almost
by construction, and reading that as "indistinguishable from human" would be the single
most expensive mistake available in this project.

The correct tool is an equivalence test (TOST). Rather than asking whether the arm differs
from the human floor, it asks whether the arm is within a stated margin of it, and it can
only answer yes by clearing a bar. It also has a third answer that a difference test hides:
**UNDERPOWERED** — the data cannot distinguish equivalence from a real gap. With 8 topic
clusters that is the honest verdict most of the time, and saying so is the point.

Units
-----
Clustered on topic (8), because the results file declares that itself:

    "primary_unit": "topic/generated article (8 clusters)"
    "secondary_units": ["pair (31)", "trial/order (62; anti-conservative)"]

Treating 62 trials as independent would roughly halve the interval and manufacture
significance. The bootstrap resamples topics, not trials.

Usage:
  python indistinguishable.py --run results/2026-07-31-mde-positive-control.json \\
      --arm writer_agent --floor-arm pc_human_1000 --margin 0.10
  python indistinguishable.py --run <discriminate output> --arm writer_sense --floor-rate 0.694
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

# A measured human endpoint: level-1.00 of the 2026-07-31 positive control, where the
# candidate is entirely human prose. Kept so a run with no positive control of its own can
# be scored against a real measurement rather than against 0.5.
#
# It is NOT a constant of the instrument. Rebuilding the same level-1.00 endpoint on a
# different opponent pool (2026-08-07, donors fetched pre-2022 against opponents fetched
# pre-2023) measured 30.2% — human-versus-human landing 20 points BELOW chance where it had
# been 19 points above. Inspection showed why: the donor articles were systematically
# longer and denser than the opponents (median 3425 vs 2949 chars, and the opponent pool
# reached down to 937), so that comparison measures the asymmetry between two human
# sub-populations rather than any ceiling on human-likeness.
#
# Practical consequence: prefer --floor-arm, which measures the endpoint on the pool
# actually in use, and treat --floor-rate as a fallback whose value has to be justified.
# Report the verdict against more than one candidate floor when it is close; the
# 2026-08-07 arms came out DIFFERENT at 0.302, 0.500 and 0.694 alike, which is what makes
# that conclusion safe to state.
MEASURED_HUMAN_FLOOR = 43 / 62  # 0.694 — one pool's measurement, not the instrument's

# Bootstrap replicates. Fixed, and the seed is an argument, because a gate whose verdict
# moves between invocations is not a gate.
DEFAULT_REPLICATES = 20000

# Above this, the required-cluster figure is reported as divergent rather than as a target.
# Eight topics cost a full generation run; anything past a few dozen is a different project,
# and printing "76050" as if it were a plan is worse than saying it cannot be reached.
PRACTICAL_CLUSTER_LIMIT = 200


def load_pairs(path: Path) -> list[dict]:
    """Accept either a discriminate.py output or the positive control's nested record."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if "pairs" in doc:
        return doc["pairs"]
    if "discrimination" in doc and "pairs" in doc["discrimination"]:
        return doc["discrimination"]["pairs"]
    raise SystemExit(f"{path}: no `pairs` (need discriminate.py output)")


def by_topic(pairs: list[dict], arm: str) -> dict[str, tuple[int, int]]:
    """topic -> (correct, trials) for one arm."""
    out: dict[str, list[int]] = {}
    for pair in pairs:
        if pair.get("arm") != arm:
            continue
        slot = out.setdefault(pair["topic"], [0, 0])
        for trial in pair.get("trials", []):
            slot[0] += bool(trial.get("correct"))
            slot[1] += 1
    if not out:
        raise SystemExit(f"arm not found in pairs: {arm}")
    return {t: (c, n) for t, (c, n) in out.items() if n}


def _rate(counts: tuple[int, int]) -> float:
    correct, trials = counts
    return correct / trials if trials else float("nan")


def cluster_diffs(arm_counts: dict, floor_counts: dict | None,
                  floor_rate: float | None) -> list[float]:
    """Per-topic (arm rate - floor rate).

    Paired on topic when a floor arm is supplied, since both arms were judged against the
    same opponents for that topic and the pairing removes topic difficulty. Falls back to a
    constant floor rate when the run has no positive control of its own — weaker, because
    it treats the floor as known without error, and the report says so.
    """
    if floor_counts is not None:
        shared = sorted(set(arm_counts) & set(floor_counts))
        if not shared:
            raise SystemExit("arm and floor arm share no topics")
        return [_rate(arm_counts[t]) - _rate(floor_counts[t]) for t in shared]
    return [_rate(c) - floor_rate for c in arm_counts.values()]


def bootstrap_ci(diffs: list[float], alpha: float, replicates: int,
                 seed: str) -> tuple[float, float]:
    """Percentile CI on the mean per-topic difference, resampling TOPICS.

    TOST at level alpha uses the (1 - 2*alpha) interval — that equivalence is declared when
    the two one-sided tests both reject is exactly the statement that this interval lies
    inside the margin.
    """
    rng = random.Random(f"{seed}|indistinguishable|v1")
    n = len(diffs)
    means = []
    for _ in range(replicates):
        means.append(statistics.fmean(rng.choices(diffs, k=n)))
    means.sort()
    lo = means[int(alpha * replicates)]
    hi = means[min(replicates - 1, int((1 - alpha) * replicates))]
    return lo, hi


def assess(arm_counts: dict, floor_counts: dict | None, floor_rate: float,
           margin: float, alpha: float, replicates: int, seed: str) -> dict:
    diffs = cluster_diffs(arm_counts, floor_counts, floor_rate)
    observed = statistics.fmean(diffs)
    lo, hi = bootstrap_ci(diffs, alpha, replicates, seed)

    # Three-way, and the third one is the one a difference test would have hidden.
    if -margin < lo and hi < margin:
        verdict = "EQUIVALENT"
    elif lo > margin or hi < -margin:
        verdict = "DIFFERENT"
    else:
        verdict = "UNDERPOWERED"

    # What it would take. Half-width shrinks as 1/sqrt(clusters), so the clusters needed
    # for the interval to fit inside the margin around the CURRENT point estimate is
    #     n' = n * (half_width / (margin - |observed|))^2
    # When |observed| >= margin the point estimate is already outside and no sample size
    # rescues it — the answer there is a better arm, not more topics, and conflating those
    # two is how a project spends a year collecting data against a fixed gap.
    half_width = (hi - lo) / 2
    slack = margin - abs(observed)
    if verdict == "EQUIVALENT":
        required = len(diffs)
    elif slack <= 0:
        required = None  # unreachable at this margin regardless of n
    else:
        required = max(len(diffs) + 1,
                       int(len(diffs) * (half_width / slack) ** 2 + 0.999))

    arm_rate = statistics.fmean(_rate(c) for c in arm_counts.values())
    return {
        "verdict": verdict,
        "required_clusters": required,
        "clusters": len(diffs),
        "arm_rate": round(arm_rate, 4),
        "floor_rate": round(
            statistics.fmean(_rate(c) for c in floor_counts.values()) if floor_counts
            else floor_rate, 4),
        "observed_gap": round(observed, 4),
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "ci_level": round(1 - 2 * alpha, 3),
        "margin": margin,
        "half_width": round((hi - lo) / 2, 4),
        "paired": floor_counts is not None,
        "seed": seed,
        "replicates": replicates,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--floor-arm", default=None,
                    help="arm whose candidates are entirely human (the positive control's "
                         "level-1.00 arm). Paired on topic when given.")
    ap.add_argument("--floor-rate", type=float, default=None,
                    help=f"constant human floor when the run has no positive control "
                         f"(measured default {MEASURED_HUMAN_FLOOR:.3f})")
    ap.add_argument("--margin", type=float, default=0.10,
                    help="equivalence margin in discrimination rate (default 0.10)")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    ap.add_argument("--seed", default="indistinguishable-v1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pairs = load_pairs(args.run)
    arm_counts = by_topic(pairs, args.arm)
    floor_counts = by_topic(pairs, args.floor_arm) if args.floor_arm else None
    floor_rate = args.floor_rate if args.floor_rate is not None else MEASURED_HUMAN_FLOOR

    result = assess(arm_counts, floor_counts, floor_rate, args.margin,
                    args.alpha, args.replicates, args.seed)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"arm            {args.arm}")
        print(f"判別率         {result['arm_rate'] * 100:.1f}%")
        print(f"人間の床       {result['floor_rate'] * 100:.1f}%"
              + ("  (topic でペア)" if result["paired"]
                 else "  (定数・誤差を持たない扱い＝弱い)"))
        print(f"差             {result['observed_gap'] * 100:+.1f} ポイント")
        print(f"{result['ci_level'] * 100:.0f}% CI      "
              f"[{result['ci_low'] * 100:+.1f}, {result['ci_high'] * 100:+.1f}]  "
              f"(topic {result['clusters']} クラスタで bootstrap)")
        print(f"同等マージン   ±{result['margin'] * 100:.1f} ポイント")
        print()
        if result["verdict"] == "EQUIVALENT":
            print("  判定: EQUIVALENT — マージン内で人間と同等と言える")
        elif result["verdict"] == "DIFFERENT":
            print("  判定: DIFFERENT — マージンを超えて人間と異なる")
        else:
            print("  判定: UNDERPOWERED — 同等とも異なるとも言えない")
            print(f"        CI 半幅 {result['half_width'] * 100:.1f} ポイントが"
                  f"マージン {result['margin'] * 100:.1f} に収まらない。")
            print("        差分検定なら『有意差なし』と出る領域だが、それは同等の証拠ではない。")
        if result["verdict"] != "EQUIVALENT":
            if result["required_clusters"] is None:
                print(f"        点推定 {abs(result['observed_gap']) * 100:.1f} が既に"
                      f"マージンの外 — トピックをいくら増やしても同等にはならない。"
                      f"必要なのはアームの改善。")
            elif result["required_clusters"] > PRACTICAL_CLUSTER_LIMIT:
                # n' explodes as the point estimate approaches the margin, so a literal
                # figure here reads as a bug rather than as "the slack is nearly zero".
                print(f"        点推定 {abs(result['observed_gap']) * 100:.1f} が"
                      f"マージン {result['margin'] * 100:.1f} のすぐ内側にあるため、"
                      f"必要クラスタ数が発散する（約 {result['required_clusters']}）。"
                      f"実質到達不能 — マージンを見直すか、アームを改善する。")
            else:
                print(f"        同等を示すには topic クラスタが約 "
                      f"{result['required_clusters']} 必要（現在 {result['clusters']}）。")
        print()
        if result["paired"]:
            print("  注: 床はこの run の対戦相手で実測した値。人間同士の比較は 50% に載らず、"
                  "プールによって 30.2%〜69.4% まで動く（両方とも実測）。")
            print("     結論が床の取り方に依存しないかは --floor-rate を変えて確認すること。")
        else:
            print(f"  注: 定数の床 {floor_rate * 100:.1f}% を使用。これは装置の定数ではない — "
                  f"別プールでの実測は 30.2% と {MEASURED_HUMAN_FLOOR * 100:.1f}% に割れている。")
            print("     可能なら --floor-arm で当該プールの床を測ること。")

    # Exit code is the gate: 0 only for a demonstrated equivalence. UNDERPOWERED is not a
    # pass — that conflation is the whole reason this script exists.
    raise SystemExit(0 if result["verdict"] == "EQUIVALENT" else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""affect_state.py — seeded sensory / 喜怒哀楽 state, supplied as material.

What this is for
----------------
Every intervention this benchmark has run against the *surface* of the text came back
zero, and the mutation ablation (`mutate.py`, results/2026-07-31-mutation-*.json) put a
null control under those zeros: re-judging a byte-identical article already moves one
paired observation, and no mutation beat that floor. The conclusion recorded in
docs/jp-naturalness-engineering.ja.md §2bis is that surface dimensions are exhausted, and
that anything worth trying next has to move the *material*.

This module adds a dimension the ledger never bounded: what the writer's body and mood
were doing at the moments the ledger records. 五感 (視覚/聴覚/触覚/嗅覚/味覚) and 喜怒哀楽
(喜/怒/哀/楽), drawn from a seed and attached to individual ledger entries.

Three constraints, all of them lessons this benchmark already paid for
----------------------------------------------------------------------
1. **Supply-side, never demand-side** (則1). The state is handed over as a record of what
   was already noted at the time. It is never an instruction — 「怒りを込めて書け」 is a
   requirement, and a requirement is met uniformly (則2: `rig2` answered "add digressions"
   with evenly-spaced digressions; `riglint` answered a variance threshold with
   evenly-spaced short sentences, improving lint 5.5 -> 1.0 while the blind judgment got
   *worse*). Nothing here can be satisfied. It can only be transcribed or ignored.

2. **Most of it must be unusable.** This is the writer_bio result, and it is the single
   most important design input here. `writer_bio` supplied career / knowledge / ignorance
   / habits and landed on the null floor (2 moved / 1 reversed). The recorded reason:

       台帳が均等充足を逃れるのは、エントリの大半が「使えない」ときだけである。

   Its 生活習慣 entries applied to every article, so they were used in 8/8 and 「土曜の午前」
   repeated across articles — uniform satisfaction reappearing *inside* the ledger. Its
   知らないこと entries applied to almost none, so they were used in 0/8.

   Sensory and emotional state is the *most* universally applicable material imaginable —
   there is always a body and always a mood — so the naive version of this idea is
   writer_bio's failure mode with extra steps. The threshold walk below exists to break
   that: a moment is emitted only when the seeded arousal walk crosses THRESHOLD, so most
   entries carry nothing, the count per article varies, and **an article that draws zero
   moments is a valid article**. Nothing downstream may require a moment to be present.
   `audit()` measures this instead of trusting it.

3. **Fictional fixture data, and the seed gets recorded.** Same precedent as
   writer_ledger.BIOGRAPHY: E2 measured that the judge never verifies material
   (results/2026-07-31-forgery-typo-discrimination.json — forged hashes matched the
   baseline to the aggregate), so borrowing a real person's body and moods buys nothing
   and is not ours to borrow. And per C-1, a run whose material moved underneath it is not
   comparable to anything: `state_fingerprint()` exists so a result file can say which
   draw produced it.

What this does NOT claim
------------------------
That it will work. The prior on any intervention here is bad — 22 designs, ~500
judgments, one non-zero that later failed to replicate. The MDE measurement
(results/2026-07-31-mde-positive-control.json) puts the harness's minimum detectable
effect at 19.4 points treating 31 pairs as independent, 29.0 on the correct 8-article
clustering, so this harness *cannot* see a small effect even if one exists. Read
docs/jp-affect-seed-design.ja.md for the predictions and the kill criteria before running
it, and do not report a number from this arm without the null control beside it.

Usage:
  python affect_state.py --seed 42 --preview          # one draw, rendered both ways
  python affect_state.py --audit 500                  # 則2 self-check over many seeds
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics

# --------------------------------------------------------------------------- axes

# 五感. Ordered most to least applicable to sitting at a machine — the weights below are
# the point: 嗅覚 and 味覚 are drawn rarely because they legitimately have little to do
# with reading a stack trace, and a state that produced a smell for every incident would
# be performing embodiment rather than recording it.
SENSES = ("視覚", "聴覚", "触覚", "嗅覚", "味覚")
SENSE_WEIGHTS = (0.34, 0.26, 0.24, 0.09, 0.07)

# 喜怒哀楽. Kept as four named axes rather than a valence/arousal pair because the ask was
# 喜怒哀楽 and because the axis name is what selects the note pool below.
AFFECTS = ("喜", "怒", "哀", "楽")

# Fictional. Mundane on purpose: the failure mode of "add sensory detail" is literary
# detail, and literary detail is exactly the register the judge calls 整いすぎ. These are
# shaped like something jotted down, several of them carry a number (the transcription
# shape that won 24/47 verdicts), and the obvious clichés — cold coffee, rain on the
# window, the glow of the monitor — are deliberately absent.
SENSE_NOTES: dict[str, tuple[str, ...]] = {
    "視覚": (
        "赤い FAILED が画面3つ分流れた",
        "diff が 400 行を超えて折り返しが崩れている",
        "タブが 17 個開いたままになっている",
        "行番号が4桁に入った",
        "スクロールバーがほとんど動かない",
        "モニタの右下だけ色がおかしい。前からだが今日は気になる",
    ),
    "聴覚": (
        "ファンが回りっぱなし。静かになったのは 03:40",
        "冷蔵庫のコンプレッサが唸っている",
        "外で車が2台、続けて出ていった",
        "キーの音しかしていない",
        "上の階で椅子を引く音がした",
    ),
    "触覚": (
        "手首の外側がだるい",
        "足先が冷えている",
        "椅子の背もたれが軋む",
        "指先が乾いて Enter が滑る",
        "キーボードの左端がべたついている",
    ),
    "嗅覚": (
        "換気していない部屋の匂いがする",
        "洗濯物が生乾きの匂いをさせている",
        "排気が焦げたような匂いをさせている。たぶん気のせい",
    ),
    "味覚": (
        "口の中が乾いて苦い",
        "夕飯に何を食べたか思い出せない",
        "歯磨き粉の味がまだ残っている",
    ),
}

# The 喜怒哀楽 side. Same rule: these are notes, not adjectives. 「苛立った」 is a label the
# model will smooth into prose; 「さっき直したはずだ」 is a thing that was thought.
AFFECT_NOTES: dict[str, tuple[str, ...]] = {
    "喜": (
        "通った。声が出た",
        "1行だった",
        "読みが当たった",
    ),
    "怒": (
        "同じところで3回",
        "さっき直したはずだ",
        "ログに出ていないのがいちばん腹立たしい",
        "この書き方をした人間に言いたいことがある",
    ),
    "哀": (
        "3日分が無駄になった",
        "月曜にまた同じことをする気がする",
        "誰にも説明できる形になっていない",
    ),
    "楽": (
        "ここからは早い",
        "手が勝手に動く",
        "この時間はわりと好きだ",
    ),
}

# Arousal walk. A moment is emitted only above THRESHOLD, so the emitted count per article
# is a property of the draw rather than a constant. START_RANGE is deliberately centred
# below the threshold: the modal article should get one moment or none.
THRESHOLD = 0.62
START_LO, START_HI = 0.20, 0.75
STEP_SIGMA = 0.28

_ASCII = re.compile(r"[A-Za-z][A-Za-z0-9_.+#/-]{1,}")
_NUM = re.compile(r"\d[\d,.]*")


def _tokens(*parts: str) -> list[str]:
    """Specific-looking strings a note licenses the article to use.

    Same contract as writer_ledger._tokens, reimplemented rather than imported so this
    module loads standalone (the test suite loads it by path). hidden_check passes
    writer_ledger's own function in, so the containment gate never sees two tokenizers.
    """
    out: list[str] = []
    for part in parts:
        out += _ASCII.findall(part)
        out += _NUM.findall(part)
    seen: dict[str, None] = {}
    for token in out:
        seen.setdefault(token.strip(".,/-"), None)
    return [t for t in seen if t]


def _pick_sense(rng: random.Random) -> str:
    """Weighted draw over the five senses (random.choices with a fixed seed is stable)."""
    return rng.choices(SENSES, weights=SENSE_WEIGHTS, k=1)[0]


def draw(seed: str, entry_ids: list[str], *, drift: bool = True,
         threshold: float = THRESHOLD) -> list[dict]:
    """Draw a seeded state and attach it to a minority of `entry_ids`, in order.

    Returns one record per *emitted* moment — entries that stayed below the threshold are
    simply absent, which is the whole mechanism. A record is::

        {"entry_id", "index", "arousal", "sense", "sense_note", "affect", "affect_note"}

    `drift=False` is the deliberate negative control: it disables the walk and emits at
    every entry, reproducing writer_bio's uniform-satisfaction failure on purpose so the
    two arms differ in scarcity and nothing else. It is not the good configuration; it is
    the one that isolates whether scarcity is doing the work.

    Deterministic in (seed, entry_ids, drift, threshold) — same inputs, same draw.
    """
    # The entry ids are in the seed, not just carried alongside it: the state belongs to
    # *these* moments, so two incidents drawn under one run seed must not receive the same
    # body and the same mood. Without this the run seed alone fixed everything and entry
    # identity was decorative, which a test caught by asking two different incidents for
    # their draws and getting the same one back.
    rng = random.Random(f"{seed}|{'|'.join(entry_ids)}|affect|v1")
    arousal = rng.uniform(START_LO, START_HI)
    moments: list[dict] = []

    for index, entry_id in enumerate(entry_ids):
        # The step is drawn even when it is not used. Consuming it conditionally desynced
        # the rng stream between drift=True and drift=False, so the scarcity ablation was
        # also changing every note — the two arms would have differed in scarcity *and*
        # content, which is the one thing that comparison exists to avoid. A test caught
        # it; the comment above this line previously claimed the opposite.
        step = rng.gauss(0.0, STEP_SIGMA)
        if drift:
            arousal = min(1.0, max(0.0, arousal + step))
            emit = arousal >= threshold
        else:
            emit = True

        # Likewise drawn regardless of `emit`, so a draw that emits less does not also
        # shift which notes appear.
        sense = _pick_sense(rng)
        sense_note = rng.choice(SENSE_NOTES[sense])
        affect = rng.choice(AFFECTS)
        affect_note = rng.choice(AFFECT_NOTES[affect])

        if not emit:
            continue
        moments.append({
            "entry_id": entry_id,
            "index": index,
            "arousal": round(arousal, 3),
            "sense": sense,
            "sense_note": sense_note,
            "affect": affect,
            "affect_note": affect_note,
        })
    return moments


def render(moments: list[dict], *, style: str = "note") -> str:
    """Render the drawn state as material handed to the writer.

    Two styles, because which one is correct is itself an open question this harness can
    settle with one paired comparison:

      "note"  — transcribed jottings, no axis names. Supply-side: there is nothing here to
                satisfy, so 則2 has no grip. This is the default and the intended arm.
      "label" — the same draw with 五感/喜怒哀楽 named and scored (「怒 4/5」). Closer to a
                demand-side instruction, and the prediction from 則1/則2 is that it reads
                *worse* because a named axis is a target. Included so that prediction is
                testable rather than asserted.

    Returns "" when the draw emitted nothing. Callers must handle that — an empty state is
    a legitimate outcome, not a failure to retry.
    """
    if not moments:
        return ""

    if style == "label":
        lines = ["＜そのときの状態（記録）＞"]
        for m in moments:
            level = min(5, max(1, int(round(m["arousal"] * 5))))
            lines.append(f"- {m['sense']}: {m['sense_note']} / {m['affect']} {level}/5")
        return "\n".join(lines)

    if style != "note":
        raise ValueError(f"unknown style: {style!r}")

    lines = [
        "＜作業中に手元に書き殴ったメモ＞",
        "（記事の主題ではありません。使えるものがあれば使い、無ければ捨ててください。",
        "  全部に触れる必要はまったくありません）",
    ]
    for m in moments:
        lines.append(f"- {m['sense_note']}。{m['affect_note']}")
    return "\n".join(lines)


def entries(moments: list[dict], tokens_fn=None) -> list[dict]:
    """The drawn state as ledger entries, so the containment gate covers it too.

    Without this the whitelist check reads 03:40, 400, 17 and every other number in the
    notes as fabrication and strips them over three revise rounds — the state would be
    supplied and then mechanically deleted, which is exactly what would have happened to
    the biography without biography_entries(). Same rule as everywhere else here: a
    specific may appear because it is in the supplied material, and for no other reason.
    """
    tok = tokens_fn or _tokens
    out = []
    for i, m in enumerate(moments):
        fact = f"{m['sense_note']}。{m['affect_note']}"
        out.append({
            "kind": "affect", "when": "", "status": "解決",
            "fact": fact, "urls": [], "tokens": tok(fact),
            "id": f"A{i}", "used_in": [],
        })
    return out


def state_fingerprint(seed: str, moments: list[dict], *, drift: bool = True) -> dict:
    """What a result file has to carry for a later run to be comparable to this one.

    C-1's lesson, applied to this dimension: the writer arms became incomparable across
    runs because the ledger moved and nothing recorded that it had. A seeded state has the
    same hazard with none of the excuse, since the seed is right there.
    """
    blob = "\n".join(f"{m['entry_id']}|{m['sense']}|{m['affect']}|{m['sense_note']}"
                     for m in moments)
    return {
        "seed": seed,
        "drift": drift,
        "moments": len(moments),
        "senses": sorted({m["sense"] for m in moments}),
        "affects": sorted({m["affect"] for m in moments}),
        "sha1": hashlib.sha1(blob.encode()).hexdigest()[:12],
    }


def audit(trials: int = 500, entries_per_article: int = 5, *, drift: bool = True,
          threshold: float = THRESHOLD) -> dict:
    """Measure whether the draw actually escapes uniform satisfaction.

    This is the check writer_bio did not get until after it had been run and had come back
    at the floor, at which point its 8/8 usage of 生活習慣 was visible only by reading the
    articles. It costs nothing to run first, so it runs first.

    Healthy output: `emit_rate` well under 1.0, `zero_moment_share` clearly non-zero, and
    `per_sense` not flat — 嗅覚/味覚 should be rare. If `emit_rate` approaches 1.0 the arm
    has become writer_bio and there is no reason to spend generation on it.
    """
    counts: list[int] = []
    sense_tally: dict[str, int] = {s: 0 for s in SENSES}
    affect_tally: dict[str, int] = {a: 0 for a in AFFECTS}

    for t in range(trials):
        ids = [f"E{i}" for i in range(entries_per_article)]
        moments = draw(f"audit-{t}", ids, drift=drift, threshold=threshold)
        counts.append(len(moments))
        for m in moments:
            sense_tally[m["sense"]] += 1
            affect_tally[m["affect"]] += 1

    total = sum(counts)
    slots = trials * entries_per_article
    return {
        "trials": trials,
        "entries_per_article": entries_per_article,
        "drift": drift,
        "threshold": threshold,
        "emit_rate": round(total / slots, 4) if slots else 0.0,
        "mean_moments": round(statistics.fmean(counts), 3) if counts else 0.0,
        "sd_moments": round(statistics.pstdev(counts), 3) if len(counts) > 1 else 0.0,
        "zero_moment_share": round(sum(1 for c in counts if c == 0) / trials, 4),
        "max_moments": max(counts) if counts else 0,
        "per_sense": {s: round(n / total, 4) if total else 0.0
                      for s, n in sense_tally.items()},
        "per_affect": {a: round(n / total, 4) if total else 0.0
                       for a, n in affect_tally.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", default="0", help="seed string for --preview")
    ap.add_argument("--entries", type=int, default=5,
                    help="how many ledger entries the state is attached to")
    ap.add_argument("--preview", action="store_true", help="show one draw, both renderings")
    ap.add_argument("--flat", action="store_true",
                    help="disable the arousal walk (the writer_bio-shaped control)")
    ap.add_argument("--audit", type=int, metavar="N",
                    help="draw N articles and report the emission distribution")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.audit:
        result = audit(args.audit, args.entries, drift=not args.flat)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"trials={result['trials']} entries/article={result['entries_per_article']} "
                  f"drift={result['drift']}")
            print(f"  emit_rate          {result['emit_rate']:.3f}   "
                  f"(1.0 = writer_bio の均等充足に戻っている)")
            print(f"  moments/article    mean {result['mean_moments']:.2f} "
                  f"sd {result['sd_moments']:.2f} max {result['max_moments']}")
            print(f"  zero-moment share  {result['zero_moment_share']:.3f}   "
                  f"(0 は「毎回必ず何か出る」＝指紋)")
            print("  per sense          " + " ".join(
                f"{s}:{v:.2f}" for s, v in result["per_sense"].items()))
            print("  per affect         " + " ".join(
                f"{a}:{v:.2f}" for a, v in result["per_affect"].items()))
        return

    ids = [f"E{i}" for i in range(args.entries)]
    moments = draw(args.seed, ids, drift=not args.flat)
    if args.json:
        print(json.dumps({
            "fingerprint": state_fingerprint(args.seed, moments, drift=not args.flat),
            "moments": moments,
            "entries": entries(moments),
        }, ensure_ascii=False, indent=2))
        return

    print(json.dumps(state_fingerprint(args.seed, moments, drift=not args.flat),
                     ensure_ascii=False))
    if not moments:
        print("\n（この seed は何も出さなかった。これは正常な結果であり、引き直さない）")
        return
    print("\n--- style=note (供給側・既定) ---")
    print(render(moments, style="note"))
    print("\n--- style=label (要求側寄り・対照) ---")
    print(render(moments, style="label"))


if __name__ == "__main__":
    main()

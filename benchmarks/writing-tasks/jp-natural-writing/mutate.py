#!/usr/bin/env python3
"""Mechanical, model-free mutations applied to already-generated articles.

Why this exists. Every intervention this benchmark tried acted on the generator: change
the criteria, change the persona, change the skeleton, change the material. All of them
are subject to the law the benchmark kept rediscovering — an inspectable requirement gets
satisfied uniformly, and uniformly-performed humanity is itself the tell. `rig2` answered
"add digressions" with evenly-spaced digressions; `riglint` answered a sentence-length
variance threshold with evenly-spaced short sentences. Both were named by the judge.

A mutation applied *after* generation is invisible to the generator. There is no
requirement to satisfy, so there is nothing to satisfy uniformly. That makes this the one
class of intervention the law does not reach.

Design: each mutation changes exactly one dimension, completely, and nothing else — the
same shape as the linkified control (all 18 sha mentions replaced, everything else
byte-identical), which is the only experiment here that produced a clean zero rather than
a number inside the judge's noise. Partial or randomised application would blur the
dimension being tested and buy nothing.

The one exception is M2. Deletions (M1, M3) remove a regularity and cannot create one;
merging sentences *adds* structure, and merging every adjacent pair would install a new
uniform rhythm — the trap again, one layer down. M2 therefore draws its merge points from
a deliberately lumpy distribution seeded by the article's own hash.

Nothing here calls a model. Mutations are deterministic: same input text, same output.

Usage:
  python mutate.py --run results/2026-07-30-novoice.json --arm writer --mutation M5 \\
                   --out /tmp/novoice-M5.json
  python mutate.py --run ... --arm writer --mutation M5 --report   # no write, stats only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from analyze_gap import CONNECTIVE, POLITE, REFLECT, features  # noqa: E402

SENT_SPLIT = re.compile(r"(?<=[。！？])")

# Lines that are not prose. Mutating a heading or a fenced block changes the document's
# shape rather than its register, which would confound the dimension under test.
NON_PROSE = re.compile(r"^\s*(#{1,6}\s|```|\||>|[-*]\s|\d+\.\s)")


# --------------------------------------------------------------------------- M5 polite
#
# The only major corpus dimension where the arms sit BELOW the human level:
#
#   polite_pct   human 68.7%   AI 17.6%
#
# Every other voice dimension (hedge, first person, paragraph-final reflection, type-token
# ratio) has the arms already in excess of humans, which is why "add humanity" kept
# regressing — it was adding to the over-supplied side. This is the one direction that
# has never been pushed, and it is mechanically reachable without a model.
#
# The table is empirical, not a grammar. It was built from the actual ending distribution
# of the substrate (230 sentences of the writer arm: ている 23, ていない 23, なかった 11,
# ではない 8, ...). Anything it does not match is LEFT ALONE — a conservative miss costs
# coverage, and coverage is reported. A wrong guess costs grammaticality, and broken
# Japanese is a generation artefact in its own right; `skeleton` lost that way, producing
# 「要。」 by forcing text into a shape it did not fit.
#
# Ordered longest-suffix-first; the first match wins.
POLITE_RULES: list[tuple[str, str]] = [
    # copula and negation of the copula
    ("ではなかった", "ではありませんでした"),
    ("じゃなかった", "じゃありませんでした"),
    ("ではない", "ではありません"),
    ("でもない", "でもありません"),
    ("じゃない", "じゃありません"),
    ("しかない", "しかありません"),
    # progressive / resultative — the two most frequent endings in the substrate
    ("ていなかった", "ていませんでした"),
    ("でいなかった", "でいませんでした"),
    ("ていない", "ていません"),
    ("でいない", "でいません"),
    ("ていた", "ていました"),
    ("でいた", "でいました"),
    ("ている", "ています"),
    ("でいる", "でいます"),
    ("てある", "てあります"),
    ("てくる", "てきます"),
    ("ておく", "ておきます"),
    ("てみた", "てみました"),
    ("てみる", "てみます"),
    ("てしまった", "てしまいました"),
    # existence
    ("があった", "がありました"),
    ("もあった", "もありました"),
    ("はあった", "はありました"),
    ("があった", "がありました"),
    ("がなかった", "がありませんでした"),
    ("はなかった", "はありませんでした"),
    ("もなかった", "もありませんでした"),
    ("がある", "があります"),
    ("はある", "はあります"),
    ("もある", "もあります"),
    ("がない", "がありません"),
    ("はない", "はありません"),
    ("もない", "もありません"),
    ("がいる", "がいます"),
    # opinion / modality
    ("と思った", "と思いました"),
    ("と思う", "と思います"),
    ("と考えている", "と考えています"),
    ("かもしれない", "かもしれません"),
    ("ようだ", "ようです"),
    ("そうだ", "そうです"),
    ("らしい", "らしいです"),
    ("べきだ", "べきです"),
    ("はずだ", "はずです"),
    ("わけだ", "わけです"),
    ("ことだ", "ことです"),
    ("ためだ", "ためです"),
    ("だけだ", "だけです"),
    ("だろう", "でしょう"),
    # suru / naru verbs — the productive pair, safe because the stem is exposed
    ("しなかった", "しませんでした"),
    ("しました", "しました"),
    ("した", "しました"),
    ("する", "します"),
    ("しない", "しません"),
    ("になった", "になりました"),
    ("となった", "となりました"),
    ("になる", "になります"),
    ("となる", "となります"),
    ("にならない", "such-placeholder"),  # replaced below; kept ordered for clarity
    ("なった", "なりました"),
    ("なる", "なります"),
    ("ならない", "なりません"),
    ("わかった", "わかりました"),
    ("わかる", "わかります"),
    ("わからない", "わかりません"),
    ("気づいた", "気づきました"),
    ("残っている", "残っています"),
    # bare copula, last so more specific forms win first
    ("だった", "でした"),
    ("である", "です"),
    ("のだ", "のです"),
    ("だ", "です"),
]
POLITE_RULES = [(a, b) for a, b in POLITE_RULES if b != "such-placeholder"]

TERMINATORS = "。！？"


def _split_sentences(text: str) -> list[str]:
    return [s for s in SENT_SPLIT.split(text) if s]


def _to_polite(sentence: str) -> tuple[str, bool]:
    """Convert one sentence's ending to 丁寧体. Returns (sentence, changed)."""
    body = sentence.rstrip()
    trail = sentence[len(body):]
    tail = ""
    while body and body[-1] in TERMINATORS + "」）)":
        tail = body[-1] + tail
        body = body[:-1]
    if not body or POLITE.search(body + (tail[:1] or "")):
        return sentence, False
    # 撥音便の過去形（積んだ・読んだ・選んだ）は「〜んだ」で終わるが、だ→です は誤り
    # （積んです）。正しくは積みました だが、ん が む/ぶ/ぬ のどれを隠しているかは表層
    # からは決まらないので変換しない。説明の「〜なんだ」は直前が仮名なので通す。
    if len(body) >= 3 and body.endswith("んだ") and "一" <= body[-3] <= "鿿":
        return sentence, False
    for plain, polite in POLITE_RULES:
        if body.endswith(plain):
            return body[: -len(plain)] + polite + tail + trail, True
    return sentence, False


def mutate_M5(text: str) -> str:
    """Convert plain-form (常体) sentence endings to polite form (丁寧体)."""
    out = []
    for line in text.split("\n"):
        if NON_PROSE.match(line) or not line.strip():
            out.append(line)
            continue
        out.append("".join(_to_polite(s)[0] for s in _split_sentences(line)))
    return "\n".join(out)


# ------------------------------------------------------------------ M1 connective strip


def mutate_M1(text: str) -> str:
    """Drop paragraph-opening connectives. Delete only — never substitute.

    The judge names this directly in losing reasonings: 「各段落が『まず/次に/続けて/
    対照として/さらに』と系統立った順接で始まる均質な構成」. Removing them removes a
    regularity; it cannot install one, so the full-application rule is safe here.
    """
    out = []
    for line in text.split("\n"):
        if NON_PROSE.match(line) or not line.strip():
            out.append(line)
            continue
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        m = CONNECTIVE.match(stripped)
        if m:
            rest = stripped[m.end():].lstrip("、 　")
            if rest:
                stripped = rest[0] + rest[1:]
        out.append(indent + stripped)
    return "\n".join(out)


# ------------------------------------------------------------- M3 reflective close strip


def mutate_M3(text: str) -> str:
    """Delete a paragraph's final sentence when it is a reflective close.

    `writercut` removed reflective *vocabulary* post-hoc and moved the paired score by
    -1.2 — nothing. The stated conclusion was that the closing is structural, not lexical.
    This tests that conclusion by removing the whole sentence instead of the words, and
    only where the tic actually occurs (para_final_reflect_pct: human 1.15, AI 4.58).
    """
    paras = text.split("\n\n")
    out = []
    for para in paras:
        if NON_PROSE.match(para.strip()) or not para.strip():
            out.append(para)
            continue
        sents = _split_sentences(para)
        if len(sents) >= 2 and REFLECT.search(sents[-1]):
            para = "".join(sents[:-1]).rstrip()
        out.append(para)
    return "\n\n".join(out)


# ------------------------------------------------------------------------- M2 merge


def mutate_M2(text: str, rate: float = 0.22) -> str:
    """Merge adjacent sentence pairs into one, at lumpy intervals.

    Unlike the deletions, this adds structure, so applying it everywhere (or every k-th
    time) would install exactly the kind of regular rhythm `riglint` was caught
    manufacturing. Merge points are drawn from a geometric gap distribution seeded by the
    article's own sha1: reproducible, but with clusters and long empty stretches rather
    than a beat.
    """
    seed = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    out_lines = []
    for line in text.split("\n"):
        if NON_PROSE.match(line) or not line.strip():
            out_lines.append(line)
            continue
        sents = _split_sentences(line)
        merged: list[str] = []
        i = 0
        gap = rng.randint(1, 4)
        while i < len(sents):
            can_merge = (
                i + 1 < len(sents)
                and gap <= 0
                and sents[i].rstrip().endswith("。")
                and len(sents[i]) + len(sents[i + 1]) < 160
            )
            if can_merge:
                head = sents[i].rstrip().rstrip("。")
                tail = sents[i + 1].lstrip()
                joiner = "が、" if rng.random() < 0.5 else "ので、"
                merged.append(head + joiner + tail)
                i += 2
                # geometric-ish: mostly short gaps, occasionally a long dry stretch
                gap = rng.choice([0, 1, 1, 2, 3, 5, 8])
            else:
                merged.append(sents[i])
                i += 1
                gap -= 1
            if gap < 0 and rng.random() > rate:
                gap = rng.randint(0, 3)
        out_lines.append("".join(merged))
    return "\n".join(out_lines)


# ----------------------------------------------------------------------- M4 truncation


def mutate_M4(text: str) -> str:
    """Cut the article off mid-sentence in its final section.

    Articles that simply stop are in the human corpus (the calibration set contains one
    that ends mid-sentence) and never in the arms. Applied once per article, so there is
    no distribution to make lumpy.
    """
    paras = [p for p in text.split("\n\n") if p.strip()]
    if len(paras) < 2:
        return text
    last = paras[-1]
    sents = _split_sentences(last)
    if not sents:
        return text
    keep = sents[:-1] if len(sents) > 1 else []
    cut = sents[-1]
    body = cut.rstrip().rstrip("。！？")
    if len(body) < 12:
        return "\n\n".join(paras[:-1] + ["".join(keep).rstrip()]) if keep else text
    paras[-1] = ("".join(keep) + body[: int(len(body) * 0.6)]).rstrip()
    return "\n\n".join(paras)


# ------------------------------------------------------------------------- M6 split
#
# M2's inverse, and the reason both exist. M2 was written against the recorded gap
# "human sent_len_stdev 126-169 vs AI 20-36" — merge sentences, get burstier prose. That
# gap turned out to be a defect in analyze_gap.sentences(), which split on 。 only and so
# glued every image embed, bullet list and table row in the human markdown into one
# multi-hundred-character pseudo-sentence. The arms produce almost no such lines (1.8% of
# lines vs 33.4% for the corpus), so the artefact was one-sided.
#
# Measured symmetrically — non-prose lines dropped, only terminator-ending units counted:
#
#              mean    sd
#   human      44.5   24.3
#   writer     53.8   29.3     ← already longer AND burstier than human
#   bare       50.3   18.9
#
# So merging is the same error as adding hedges: pushing further onto the over-supplied
# side. The correction runs the other way — humans write SHORTER sentences. M6 splits at
# clause junctions where the left half can stand alone, which fires only on long
# sentences that happen to contain one, so its distribution is lumpy by construction
# rather than by seeding.
SPLIT_JUNCTIONS = ("が、", "ので、", "けど、", "けれど、")


def mutate_M6(text: str, min_sentence: int = 60, min_clause: int = 15) -> str:
    """Split long sentences at clause junctions, toward the human length distribution."""
    out_lines = []
    for line in text.split("\n"):
        if NON_PROSE.match(line) or not line.strip():
            out_lines.append(line)
            continue
        pieces = []
        for sent in _split_sentences(line):
            if len(sent) < min_sentence:
                pieces.append(sent)
                continue
            cut = -1
            for junction in SPLIT_JUNCTIONS:
                idx = sent.find(junction)
                # only the first junction, and only if both halves carry weight
                if idx >= min_clause and (cut < 0 or idx < cut):
                    cut, width = idx, len(junction)
            if cut < 0 or len(sent) - cut < min_clause:
                pieces.append(sent)
                continue
            head = sent[:cut]
            tail = sent[cut + width:].lstrip()
            pieces.append(head + "。" if not head.endswith("。") else head)
            pieces.append(tail)
        out_lines.append("".join(pieces))
    return "\n".join(out_lines)


MUTATIONS = {
    "M1": ("段落頭の接続詞を削除", mutate_M1),
    "M2": ("隣接文を非一様な間隔で結合", mutate_M2),
    "M3": ("段落末の内省文を1文ごと削除", mutate_M3),
    "M4": ("末尾を文の途中で切断", mutate_M4),
    "M5": ("文末を丁寧体へ変換", mutate_M5),
    "M6": ("長文を節境界で分割", mutate_M6),
}


def stats(text: str) -> dict:
    f = features(text)
    return {
        "chars": len(text),
        "polite_pct": round(f["polite_pct"], 1),
        "connective_start_pct": round(f["connective_start_pct"], 1),
        "para_final_reflect_pct": round(f["para_final_reflect_pct"], 1),
        "sent_len_stdev": round(f["sent_len_stdev"], 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--arm", default="writer")
    ap.add_argument("--mutation", required=True,
                    help="M1..M5, or a comma-chain applied in order (e.g. M1,M3)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--report", action="store_true", help="print before/after stats only")
    args = ap.parse_args()

    steps = [s.strip() for s in args.mutation.split(",") if s.strip()]
    unknown = [s for s in steps if s not in MUTATIONS]
    if unknown:
        ap.error(f"unknown mutation(s): {', '.join(unknown)}")
    label = " + ".join(MUTATIONS[s][0] for s in steps)

    def fn(text: str) -> str:
        for step in steps:
            text = MUTATIONS[step][1](text)
        return text

    record = json.loads(args.run.read_text())
    samples = record["arms"][args.arm]["samples"]

    print(f"{args.mutation}: {label}   arm={args.arm}  n={len(samples)}\n")
    print(f"{'topic':<22}{'chars':>12}{'polite%':>16}{'conn%':>14}{'reflect%':>14}")
    before_all, after_all = [], []
    for sample in samples:
        src = sample["description"]
        dst = fn(src)
        b, a = stats(src), stats(dst)
        before_all.append(b)
        after_all.append(a)
        print(f"{sample['topic']:<22}{b['chars']:>5}→{a['chars']:<6}"
              f"{b['polite_pct']:>7.1f}→{a['polite_pct']:<8.1f}"
              f"{b['connective_start_pct']:>6.1f}→{a['connective_start_pct']:<7.1f}"
              f"{b['para_final_reflect_pct']:>6.1f}→{a['para_final_reflect_pct']:<7.1f}")
        sample["description"] = dst
        sample["mutation"] = args.mutation

    def mean(rows, key):
        return sum(r[key] for r in rows) / max(len(rows), 1)

    print(f"\n{'mean':<22}{mean(before_all,'chars'):>5.0f}→{mean(after_all,'chars'):<6.0f}"
          f"{mean(before_all,'polite_pct'):>7.1f}→{mean(after_all,'polite_pct'):<8.1f}"
          f"{mean(before_all,'connective_start_pct'):>6.1f}→{mean(after_all,'connective_start_pct'):<7.1f}"
          f"{mean(before_all,'para_final_reflect_pct'):>6.1f}→{mean(after_all,'para_final_reflect_pct'):<7.1f}")
    print("人間コーパスの水準: polite 68.7 / reflect 1.15")

    if args.report:
        return
    if not args.out:
        ap.error("--out is required unless --report")
    record["mutation"] = {"kind": args.mutation, "label": label, "arm": args.arm}
    record["arms"] = {f"{args.arm}_{args.mutation.lower().replace(',','')}": record["arms"][args.arm]}
    args.out.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()

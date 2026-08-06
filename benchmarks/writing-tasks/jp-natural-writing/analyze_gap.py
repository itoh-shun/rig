#!/usr/bin/env python3
"""Locate the human/AI gap in measurable features, and find which ones move the judge.

Ten harness designs have landed between 72 and 79 against a human calibration of 11.1,
and each was aimed at a dimension picked by reading verdicts — reflective closers,
paragraph uniformity, structural elements. That is one guess at a time. This measures
many dimensions at once and asks the recorded data two questions instead:

  separation  Which features differ most between the human corpus and every arm? A
              feature no arm matches is where the gap lives.
  correlation Across the ~60 already-scored AI samples, which features track the judge's
              score? A feature that separates human from AI but does not correlate with
              the score is not worth optimising — the judge is not reading it.

The second question is the useful one and has never been asked. Both run on data already
in results/ plus the local corpus, so neither costs a model call.

Caveat carried into the output: with ~60 samples and ~20 features, individual correlations
are noisy and some will be spurious. Only large, consistent effects are worth acting on.
"""

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

# ------------------------------------------------------------------------- features

POLITE = re.compile(r"(です|ます|でした|ました|ません)[。！？」]?$")
PLAIN = re.compile(r"(だ|である|だった|た|る)[。！？」]?$")
NOMINAL = re.compile(r"[一-鿿ァ-ヴー]$")
HEDGE = re.compile(r"(かもしれ|だろう|と思う|ような気|一概に|場合があ|可能性があ|とは限らな)")
REFLECT = re.compile(r"(のだと思う|のかもしれない|気づいた|確信が持て|わからない|書いておく"
                     r"|残しておく|ということだ|のだろう|に尽きる|ではないか|と考えている"
                     r"|学んだ|教訓|大切だ|重要だ|べきだろう)")
CONNECTIVE = re.compile(r"^(しかし|だが|また|さらに|そして|つまり|なお|ただし|一方|次に"
                        r"|まず|続けて|加えて|そのため|このように|したがって|結果として)")
FIRST_PERSON = re.compile(r"(私|自分|僕|うち)")
COLLOQUIAL = re.compile(r"(んです|んだ|けど|じゃない|ちゃっ|しまっ|なあ|かな|よね|わけ|でも)")
LATIN_OR_NUM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}")


# Markdown lines that are not prose. Splitting on 。 alone glues these into the adjacent
# text, and since none of them contain 。 the result is a single "sentence" hundreds of
# characters long. The human corpus is full of them (image embeds, bullet lists, tables)
# and the arms produce almost none, so leaving them in does not add noise symmetrically —
# it manufactures a human/AI gap on every length-derived feature. Measured on this
# corpus, dropping them moves the human sentence-length mean from 263.5 to 45.7 and the
# stdev from 169.4 to 34.7, i.e. from "5x the arms" to "the same as the arms".
NON_PROSE_LINE = re.compile(
    r"^\s*(?:!\[.*|\|.*|>.*|[-*+]\s.*|\d+\.\s.*|\[.*\]\(.*\)\s*|https?://\S+\s*)$")


def sentences(text: str) -> list[str]:
    body = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    body = re.sub(r"(?m)^#{1,6} .*$", "", body)
    body = "\n".join(ln for ln in body.split("\n") if not NON_PROSE_LINE.match(ln))
    # Split on terminators *and* line breaks: a prose line with no 。 (a caption, a short
    # aside) is one unit, not a continuation of the line above it.
    parts = re.split(r"(?<=[。！？])|\n", body)
    return [s.strip() for s in parts if s.strip()]


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text)
            if len(p.strip()) > 40 and not p.strip().startswith("#")]


def features(text: str) -> dict:
    sents = sentences(text)
    paras = paragraphs(text)
    lens = [len(s) for s in sents] or [0]
    plens = [len(p) for p in paras] or [0]
    chars = max(len(text), 1)
    per1k = 1000 / chars

    finals = Counter()
    for s in sents:
        if POLITE.search(s):
            finals["polite"] += 1
        elif NOMINAL.search(s.rstrip("。！？」")):
            finals["nominal"] += 1
        elif PLAIN.search(s):
            finals["plain"] += 1
        else:
            finals["other"] += 1
    total = sum(finals.values()) or 1

    para_final_lens = [len(sentences(p)[-1]) for p in paras if sentences(p)] or [0]
    para_final_reflect = sum(1 for p in paras if sentences(p) and REFLECT.search(sentences(p)[-1]))

    words = re.findall(r"[一-鿿ぁ-んァ-ヴA-Za-z0-9]+", text)
    return {
        "sent_len_mean": statistics.mean(lens),
        "sent_len_stdev": statistics.pstdev(lens),
        "sent_len_max": max(lens),
        "sent_len_min": min(lens),
        "para_len_stdev": statistics.pstdev(plens),
        "para_final_len_stdev": statistics.pstdev(para_final_lens),
        "para_final_reflect_pct": 100 * para_final_reflect / max(len(paras), 1),
        "final_form_variety": len([k for k, v in finals.items() if v]),
        "nominal_ending_pct": 100 * finals["nominal"] / total,
        "polite_pct": 100 * finals["polite"] / total,
        "connective_start_pct": 100 * sum(1 for s in sents if CONNECTIVE.match(s)) / max(len(sents), 1),
        "hedge_per1k": len(HEDGE.findall(text)) * per1k,
        "first_person_per1k": len(FIRST_PERSON.findall(text)) * per1k,
        "colloquial_per1k": len(COLLOQUIAL.findall(text)) * per1k,
        "question_per1k": (text.count("？") + text.count("?")) * per1k,
        "comma_per_sent": text.count("、") / max(len(sents), 1),
        "paren_per1k": (text.count("（") + text.count("(")) * per1k,
        "ellipsis_per1k": (text.count("…") + text.count("——") + text.count("―")) * per1k,
        "link_per1k": len(re.findall(r"https?://|\]\(", text)) * per1k,
        "image_per1k": text.count("![") * per1k,
        "code_fence": text.count("```") // 2,
        "list_line_per1k": len(re.findall(r"(?m)^\s*[-*+\d]+[.)]?\s+\S", text)) * per1k,
        "heading_per1k": len(re.findall(r"(?m)^#{1,6} ", text)) * per1k,
        "latin_token_per1k": len(LATIN_OR_NUM.findall(text)) * per1k,
        "type_token_ratio": len(set(words)) / max(len(words), 1),
        "zenkaku_space": text.count("　"),
    }


# --------------------------------------------------------------------------- loading


def load_human(corpus: Path, limit: int) -> list[dict]:
    index = json.loads((corpus / "index.json").read_text())[:limit]
    return [features((corpus / e["file"]).read_text()) for e in index]


def load_arms(results: Path) -> dict[str, list[tuple[dict, float]]]:
    """Every scored sample from every recorded run, keyed by arm, with its judge score."""
    per_arm: dict[str, list[tuple[dict, float]]] = {}
    for path in sorted(results.glob("*.json")):
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or "arms" not in record:
            continue
        if record.get("mode") != "live":
            continue
        for arm, data in record["arms"].items():
            for sample in data["samples"]:
                text = f"{sample['title']}\n\n{sample['description']}"
                # Short-form runs predate the length control and are a different task;
                # mixing them in would blend two populations.
                if len(text) < 1200:
                    continue
                per_arm.setdefault(arm, []).append((features(text), sample["score"]))
    return per_arm


# ------------------------------------------------------------------------- reporting


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0


def cohens_d(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    va, vb = statistics.pvariance(a), statistics.pvariance(b)
    pooled = ((va + vb) / 2) ** 0.5
    return (statistics.mean(a) - statistics.mean(b)) / pooled if pooled else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--results", type=Path, default=HERE / "results")
    ap.add_argument("--limit", type=int, default=28)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    human = load_human(args.corpus, args.limit)
    arms = load_arms(args.results)
    ai = [f for samples in arms.values() for f, _ in samples]
    scores = [s for samples in arms.values() for _, s in samples]
    keys = list(human[0])

    print(f"human n={len(human)}   AI n={len(ai)} across {len(arms)} arms "
          f"({', '.join(sorted(arms))})\n")

    rows = []
    for key in keys:
        h = [f[key] for f in human]
        a = [f[key] for f in ai]
        rows.append({
            "feature": key,
            "human": statistics.mean(h),
            "ai": statistics.mean(a),
            "d": cohens_d(h, a),
            "r_score": pearson([f[key] for f in ai], scores),
        })

    print("=" * 92)
    print("1. 人間 vs AI の分離（|d| 降順） — 差がどこにあるか")
    print("=" * 92)
    print(f"{'feature':<26}{'human':>10}{'AI':>10}{'d':>8}   {'score との r':>12}")
    for row in sorted(rows, key=lambda r: -abs(r["d"])):
        print(f"{row['feature']:<26}{row['human']:>10.2f}{row['ai']:>10.2f}"
              f"{row['d']:>8.2f}   {row['r_score']:>+12.2f}")

    print()
    print("=" * 92)
    print("2. 判定役スコアとの相関（|r| 降順） — 何を動かせばスコアが動くか")
    print("   r>0 は「その特徴が強いほど AI と判定される」")
    print("=" * 92)
    print(f"{'feature':<26}{'r':>8}{'d':>8}   方向")
    for row in sorted(rows, key=lambda r: -abs(r["r_score"]))[:12]:
        # Actionable only when the judge reads it (|r|) *and* humans differ (sign of d).
        want = "減らす" if row["r_score"] > 0 else "増やす"
        aligned = (row["r_score"] > 0) == (row["d"] < 0)
        note = f"{want}（人間側と整合）" if aligned else f"{want}（人間側と不整合 — 注意）"
        print(f"{row['feature']:<26}{row['r_score']:>+8.2f}{row['d']:>8.2f}   {note}")

    print()
    print("=" * 92)
    print("3. アーム別の平均スコアと、上位相関特徴の値")
    print("=" * 92)
    top = [r["feature"] for r in sorted(rows, key=lambda r: -abs(r["r_score"]))[:5]]
    print(f"{'arm':<12}{'n':>4}{'score':>8}" + "".join(f"{k[:14]:>16}" for k in top))
    for arm, samples in sorted(arms.items(), key=lambda kv: statistics.mean([s for _, s in kv[1]])):
        mean_score = statistics.mean([s for _, s in samples])
        vals = "".join(f"{statistics.mean([f[k] for f, _ in samples]):>16.2f}" for k in top)
        print(f"{arm:<12}{len(samples):>4}{mean_score:>8.1f}{vals}")
    print(f"{'human':<12}{len(human):>4}{'11.1':>8}"
          + "".join(f"{statistics.mean([f[k] for f in human]):>16.2f}" for k in top))

    print()
    print("注意: AI n≈{} / 特徴量 {} 個。個別の r はノイズを含み、一部は偶然。"
          .format(len(ai), len(keys)))
    print("     大きく、かつ人間側と整合している効果だけが行動に値する。")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"human_n": len(human), "ai_n": len(ai), "features": rows},
            ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

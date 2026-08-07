#!/usr/bin/env python3
"""corpus_profile.py — describe what KIND of document a corpus is made of.

Why this exists. E3 set out to remove a confound: the arms write work logs, the human
opponents are Qiita tutorials, and the winning verdicts' most cited reason for picking a
generated article was that the *opponent* looked templated (39/47). So part of the 82-98%
discrimination rate is "is this a Qiita article", not "did a human write this".

The genre control was run by re-querying Qiita with 「ハマった」「原因」「備忘録」, 91
articles were fetched, and the discrimination went *up*. Only afterwards was the operation
checked, and it had not worked at all:

                     n    はじめに見出し   画像   表    未解決で終わる
    既定コーパス      48       31%        56%   13%      0%
    ジャンル統制      91       44%        52%   13%      0%

Same document type. Qiita full-text search matches a word anywhere in the body, so a
tutorial that happens to contain 「ハマった」 in its troubleshooting section qualifies. The
genre confound was not refuted; it was left untested, after spending the judgments.

That check was a one-off computed after the fact. This is the same check as a script, so
it runs BEFORE the expensive half, on any corpus directory, on one ruler. It answers "did
my corpus actually change" — nothing else. It calls no model and needs no network.

The metrics split into three groups, and the split is the point:

  form      headings, bullets, images, tables, code fences — the article *template*.
            This is what Qiita enforces and what a personal blog or a talk does not have.
  register  polite-form rate, first person, question marks, 体言止め — the voice.
  shape     sentence and paragraph statistics, and whether the piece closes unresolved.

A corpus of spoken transcripts should come out near zero on `form` while staying high on
`register`. If it does not, the ingest is broken — subtitle files that kept their
timestamps, say — and that is worth knowing before the text reaches a judge.

Usage:
  python corpus_profile.py /tmp/human_corpus /tmp/blog_corpus --json
  python corpus_profile.py /tmp/human_corpus            # single corpus, table output
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

# Form markers, read from markdown notation. Every corpus must therefore REACH this script
# in markdown: Qiita's API already returns it, and fetch_prose_corpus.html_to_text converts
# blog HTML into it rather than flattening it. That is a hard precondition, not a detail —
# an extractor that strips headings makes this script report 0.0 headings and call it a
# genre difference. It happened during development; see html_to_text's docstring.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S", re.M)
_BULLET_RE = re.compile(r"^\s{0,4}(?:[-*+]\s+|\d{1,2}[.)]\s+)\S", re.M)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b", re.I)
_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)", re.M)

# 「はじめに」/「概要」-style opening heading — the single clearest fingerprint of the
# platform's house style, and the one E3 accidentally increased.
_INTRO_HEADING_RE = re.compile(
    r"^\s{0,3}#{1,6}\s*(はじめに|概要|背景|目的|環境|前提|この記事(で|について)|"
    r"本記事(で|について)|まとめ|おわりに|参考)", re.M)

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?])")
_POLITE_RE = re.compile(r"(です|ます|でした|ました|ません|ましょう|でしょう)[。！？!?」）)]*$")
_FIRST_PERSON_RE = re.compile(r"(私|僕|俺|自分|わたし|わたくし)")
_NOUNISH_END_RE = re.compile(r"[一-鿿ァ-ヶー]$")

# A piece that stops without tying off. 0/139 human Qiita articles did this, while the
# judge cited it in 34/47 verdicts as evidence of a human — the mismatch that motivated
# judge_norm.py. Whether other genres do it is exactly what this script is for.
_UNRESOLVED_RE = re.compile(
    r"(まだ|いまだに|未だ)(分から|わから|解決|直っ|終わ|できて)|"
    r"(分から|わから)ないままだ|"
    r"(原因|理由)は(まだ|不明)|"
    r"(続く|途中|保留|放置|お預け|そのまま)(です|だ|である)?[。！?]?$", re.M)


def _sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or _HEADING_RE.match(line) or line.startswith("|"):
            continue
        for s in _SENT_SPLIT_RE.split(line):
            s = s.strip()
            if s:
                out.append(s)
    return out


def profile_text(text: str) -> dict:
    """Per-document metrics. Rates are per 1000 characters so lengths stay comparable."""
    chars = len(text)
    per1k = (lambda n: round(n / chars * 1000, 3)) if chars else (lambda n: 0.0)
    sents = _sentences(text)
    lengths = [len(s) for s in sents]

    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    para_sents = [len(_sentences(p)) for p in paras]

    # The closing check reads the tail rather than the whole document: an article that
    # mentions an unsolved thing in the middle and then wraps up neatly is not "ending
    # unresolved", and counting it would inflate exactly the metric E3 cared about.
    tail = "\n".join(text.rstrip().split("\n")[-6:])

    polite = sum(1 for s in sents if _POLITE_RE.search(s))
    nounish = sum(1 for s in sents if _NOUNISH_END_RE.search(s.rstrip("。！？!?」）)")))

    return {
        "chars": chars,
        # form — the article template
        "headings_per1k": per1k(len(_HEADING_RE.findall(text))),
        "has_intro_heading": bool(_INTRO_HEADING_RE.search(text)),
        "bullets_per1k": per1k(len(_BULLET_RE.findall(text))),
        "has_image": bool(_IMAGE_RE.search(text)),
        "has_table": bool(_TABLE_RE.search(text)),
        "has_code_fence": bool(_FENCE_RE.search(text)),
        # register — the voice
        "polite_pct": round(polite / len(sents) * 100, 1) if sents else 0.0,
        "first_person_per1k": per1k(len(_FIRST_PERSON_RE.findall(text))),
        "question_per1k": per1k(text.count("？") + text.count("?")),
        "taigendome_pct": round(nounish / len(sents) * 100, 1) if sents else 0.0,
        # shape
        "sentences": len(sents),
        "sent_len_mean": round(statistics.fmean(lengths), 1) if lengths else 0.0,
        "sent_len_sd": round(statistics.pstdev(lengths), 1) if len(lengths) > 1 else 0.0,
        "paragraphs": len(paras),
        "para_sents_mean": round(statistics.fmean(para_sents), 2) if para_sents else 0.0,
        "ends_unresolved": bool(_UNRESOLVED_RE.search(tail)),
    }


# Which metrics are shares of documents (reported as %) rather than per-document numbers
# to be averaged. Keeping this explicit stops a boolean from being averaged into a
# meaningless 0.31 and printed next to a character count as if they were the same kind of
# quantity — the E3 table reported percentages, and this reproduces that reading exactly.
_BOOL_METRICS = ("has_intro_heading", "has_image", "has_table", "has_code_fence",
                 "ends_unresolved")


def profile_corpus(path: Path, limit: int | None = None) -> dict:
    """Aggregate every .md/.txt in a directory. index.json, if present, is ignored."""
    files = sorted(p for p in path.iterdir()
                   if p.suffix in (".md", ".txt") and p.name != "index.json")
    if limit:
        files = files[:limit]
    docs = [profile_text(p.read_text(encoding="utf-8", errors="replace")) for p in files]
    if not docs:
        return {"corpus": path.name, "n": 0}

    agg: dict = {"corpus": path.name, "n": len(docs)}
    for key in docs[0]:
        if key in _BOOL_METRICS:
            agg[key] = round(sum(1 for d in docs if d[key]) / len(docs) * 100, 1)
        else:
            agg[key] = round(statistics.fmean(d[key] for d in docs), 2)
    return agg


# Printed in the order form -> register -> shape, because that is the order in which a
# corpus swap is supposed to show up. A genre control that moved only `register` changed
# the voice, not the document type, and that is the E3 outcome restated.
_ROWS = [
    ("── form", None),
    ("headings/1k", "headings_per1k"),
    ("はじめに見出し %", "has_intro_heading"),
    ("bullets/1k", "bullets_per1k"),
    ("画像 %", "has_image"),
    ("表 %", "has_table"),
    ("コード %", "has_code_fence"),
    ("── register", None),
    ("丁寧体 %", "polite_pct"),
    ("一人称/1k", "first_person_per1k"),
    ("疑問符/1k", "question_per1k"),
    ("体言止め %", "taigendome_pct"),
    ("── shape", None),
    ("文長 mean", "sent_len_mean"),
    ("文長 sd", "sent_len_sd"),
    ("段落あたり文数", "para_sents_mean"),
    ("未解決で終わる %", "ends_unresolved"),
    ("平均字数", "chars"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("corpora", nargs="+", type=Path, help="corpus directories to compare")
    ap.add_argument("--limit", type=int, help="cap documents per corpus")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    profiles = []
    for path in args.corpora:
        if not path.is_dir():
            raise SystemExit(f"not a directory: {path}")
        profiles.append(profile_corpus(path, args.limit))

    if args.json:
        print(json.dumps(profiles, ensure_ascii=False, indent=2))
        return

    empty = [p["corpus"] for p in profiles if p["n"] == 0]
    if empty:
        print(f"⚠ 空のコーパス: {', '.join(empty)}")
        profiles = [p for p in profiles if p["n"]]
    if not profiles:
        raise SystemExit("no documents found")

    width = max(14, max(len(p["corpus"]) for p in profiles) + 2)
    print(f"{'':<18}" + "".join(f"{p['corpus']:>{width}}" for p in profiles))
    print(f"{'n':<18}" + "".join(f"{p['n']:>{width}}" for p in profiles))
    for label, key in _ROWS:
        if key is None:
            print(label)
            continue
        print(f"  {label:<16}" + "".join(f"{p[key]:>{width}}" for p in profiles))

    if len(profiles) > 1:
        # The whole reason the script exists: say out loud whether the swap moved the
        # document type, instead of leaving it to be eyeballed after the judgments are
        # already spent.
        form = ("headings_per1k", "has_intro_heading", "bullets_per1k", "has_image",
                "has_table", "has_code_fence")
        base = profiles[0]
        print()
        for other in profiles[1:]:
            moved = [k for k in form
                     if abs(other[k] - base[k]) > max(1.0, abs(base[k]) * 0.5)]
            verdict = (f"form が動いた: {', '.join(moved)}" if moved
                       else "form が動いていない — ジャンルは変わっていない")
            print(f"  {base['corpus']} → {other['corpus']}: {verdict}")


if __name__ == "__main__":
    main()

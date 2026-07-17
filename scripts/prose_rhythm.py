#!/usr/bin/env python3
"""prose_rhythm.py — deterministic Japanese prose-rhythm sensor (advisory).

Machine backing for the "rhythm" axis of knowledge/ai-writing-smells' 5-axis
scoring, the same way scan-secrets machine-backs no_secret_leak — except this
one is ADVISORY by design. A script can measure surface proxies of rhythm
(sentence-length runs, ending repetition, paragraph uniformity, progress
narration, connective density); it cannot measure whether the prose actually
switches cognitive modes. The semantic judgment stays with ai-smell-reviewer;
this gives that reviewer numbers instead of impressions.

Metrics (all deterministic, stdlib-only):
  long-run       3+ consecutive long sentences (no short "beat" between them)
  uniform-beat   sentence lengths with low variance (same-length monotony)
  low-burstiness adjacent sentences barely differ in length — even when the
                 document-wide variance passes, a locally flat beat reads
                 monotone (#318; independent field measurement reached the
                 same conclusion: AI smell shows in rhythm before vocabulary)
  ending-run     3+ consecutive sentences with the same normalized ending
  uniform-para   paragraph sentence-counts with low variation (template
                 shape — catches the "every paragraph is 3 sentences" tic,
                 not just exact equality)
  progress       progress-narration phrases — sentences that update the
                 *document* ("this section covers...") rather than the
                 *situation*; candidates for deletion under the topic test
  connectives    share of sentences opening with an explicit connective

Informational (reported, never flagged): taigendome_ratio — the share of
sentences ending on a noun (体言止め). Field measurement found AI-generated
Japanese uses taigendome at near-zero rates while human prose mixes it in,
so a long document at exactly 0% is itself a weak AI signal — but "add
taigendome" is a style call, not a defect, hence informational only.

Honest scope on the thresholds: they are uncalibrated heuristics, chosen by
eyeball against a handful of documents — NOT calibrated against a human-
prose corpus (that is the proper method, and what the field measurement
above did with 137 human / 406 AI documents). Mora-based length measurement
would beat character counts but needs morphological analysis, which a
stdlib-only script cannot do; character counts are the honest approximation.

Usage:
  python3 scripts/prose_rhythm.py <file.md> [file2...]
  cat draft.md | python3 scripts/prose_rhythm.py -
  python3 scripts/prose_rhythm.py --json <file.md>

Exit code is always 0 (advisory — findings inform the reviewer, they don't
gate). Markdown code fences, headings, and table rows are excluded from
analysis (they legitimately break prose rhythm).
"""

import argparse
import json
import re
import statistics
import sys

# Tunable, documented thresholds. Deterministic — change them here, not per run.
# Uncalibrated heuristics (see module docstring): the proper method is
# calibration against a human-prose corpus, which has not been done here.
LONG_SENTENCE_CHARS = 55      # a sentence this long or longer counts as "long"
LONG_RUN_MIN = 3              # this many consecutive long sentences = a flagged run
UNIFORM_CV_MAX = 0.30         # coefficient of variation below this = monotone beat
UNIFORM_MIN_SENTENCES = 8     # need at least this many sentences to judge uniformity
BURSTINESS_MIN = 0.35         # mean adjacent length-delta / mean length below this = flat beat
ENDING_RUN_MIN = 3            # this many identical consecutive endings = flagged
CONNECTIVE_RATIO_MAX = 0.40   # more than this share of connective-led sentences = flagged
UNIFORM_PARA_MIN = 4          # need at least this many paragraphs to judge shape
UNIFORM_PARA_CV_MAX = 0.25    # paragraph sentence-count CV below this = template shape

_SENTENCE_END_RE = re.compile(r"(?<=[。！？!?])")
_ENDING_RE = re.compile(
    r"(でしょうか|ましょう|ました|ません|でした|である|であった|だろう|だった|"
    r"です|ます|ない|たい|よう|だ|た|る|う|か)$")
_CONNECTIVE_RE = re.compile(
    r"^(しかし|だが|ただし|なので|つまり|したがって|そのため|また|さらに|"
    r"加えて|そして|一方|例えば|たとえば|要するに|ちなみに)[、,]?")
# Progress narration: sentences that update the document, not the situation
# (the "topic test" from the cognitive-rhythm principle in ai-writing-smells).
_PROGRESS_PATTERNS = (
    r"本稿では", r"本章では", r"本節では", r"この記事では",
    r"ここでは.{0,12}(見て|扱|説明|解説)", r"次に.{0,8}(見て|説明|解説)",
    r"以下では?.{0,12}(見て|説明|述べ|まとめ)", r"ここまでで", r"ここまでの話",
    r"見ていきましょう", r"について(解説|説明)します", r"次章で", r"次節で",
    r"まとめると", r"要するに",
)
_PROGRESS_RE = re.compile("|".join(f"(?:{p})" for p in _PROGRESS_PATTERNS))
# Kanji / katakana — a sentence ending on one of these with no verbal/copula
# ending is counted as taigendome (体言止め). Rough by design (no morphology).
_NOUNISH_LAST_RE = re.compile(r"[一-鿿゠-ヿ]")


def strip_non_prose(text: str) -> str:
    """Drop code fences, headings, and table rows — non-prose lines that
    legitimately break rhythm and would only produce false positives."""
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#") or stripped.startswith("|"):
            continue
        out.append(line)
    return "\n".join(out)


def split_sentences(text: str) -> list[str]:
    """Naive sentence split on 。！？!?  — good enough for an advisory proxy.
    Known limitation: punctuation inside 「」 quotes also splits."""
    sentences: list[str] = []
    for chunk in text.split("\n"):
        for s in _SENTENCE_END_RE.split(chunk):
            s = s.strip()
            if s:
                sentences.append(s)
    return sentences


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def normalized_ending(sentence: str) -> str:
    body = sentence.rstrip("。！？!?、,.）)」』")
    m = _ENDING_RE.search(body)
    return m.group(0) if m else ""


def _excerpt(s: str, limit: int = 24) -> str:
    return s[:limit] + ("…" if len(s) > limit else "")


def analyze(text: str) -> dict:
    """All metrics over one document (pure function; deterministic)."""
    prose = strip_non_prose(text)
    sentences = split_sentences(prose)
    paragraphs = split_paragraphs(prose)
    findings: list[dict] = []

    # long-run: 3+ consecutive long sentences without a short beat between them
    run: list[str] = []
    for s in sentences + [""]:  # sentinel flushes the last run
        if len(s) >= LONG_SENTENCE_CHARS:
            run.append(s)
        else:
            if len(run) >= LONG_RUN_MIN:
                findings.append({"metric": "long-run",
                                 "detail": f"{len(run)} consecutive long sentences (>= {LONG_SENTENCE_CHARS} chars)",
                                 "excerpt": _excerpt(run[0])})
            run = []

    # uniform-beat: low variance in sentence length across the document
    lengths = [len(s) for s in sentences]
    cv = None
    if len(lengths) >= UNIFORM_MIN_SENTENCES:
        mean = statistics.mean(lengths)
        cv = (statistics.pstdev(lengths) / mean) if mean else 0.0
        if cv < UNIFORM_CV_MAX:
            findings.append({"metric": "uniform-beat",
                             "detail": f"sentence-length CV {cv:.2f} < {UNIFORM_CV_MAX} across {len(lengths)} sentences",
                             "excerpt": ""})

    # low-burstiness: adjacent sentences barely differ in length (#318).
    # Document-wide CV can pass while the local beat is still flat — e.g. a
    # slow monotone drift from short to long sentences has variance but no
    # short/long alternation. Burstiness = mean |len[i]-len[i-1]| / mean len.
    burstiness = None
    if len(lengths) >= UNIFORM_MIN_SENTENCES:
        mean = statistics.mean(lengths)
        deltas = [abs(a - b) for a, b in zip(lengths, lengths[1:])]
        burstiness = (statistics.mean(deltas) / mean) if mean else 0.0
        if burstiness < BURSTINESS_MIN:
            findings.append({"metric": "low-burstiness",
                             "detail": f"adjacent length variation {burstiness:.2f} < {BURSTINESS_MIN} "
                                       f"across {len(lengths)} sentences (flat local beat)",
                             "excerpt": ""})

    # ending-run: 3+ consecutive identical normalized endings
    prev_end, end_run, end_start = "", 0, ""
    for s in sentences + [""]:
        e = normalized_ending(s) if s else ""
        if e and e == prev_end:
            end_run += 1
        else:
            if end_run >= ENDING_RUN_MIN:
                findings.append({"metric": "ending-run",
                                 "detail": f"{end_run} consecutive sentences ending in 「{prev_end}」",
                                 "excerpt": _excerpt(end_start)})
            end_run, end_start = 1, s
        prev_end = e

    # uniform-para: paragraph sentence-counts with low variation (#318 relaxed
    # from exact equality to CV — catches the "every paragraph is 2-3
    # sentences" template tic field measurement found in some model families).
    para_counts = [len(split_sentences(p)) for p in paragraphs]
    if len(para_counts) >= UNIFORM_PARA_MIN and statistics.mean(para_counts) > 1:
        p_mean = statistics.mean(para_counts)
        p_cv = statistics.pstdev(para_counts) / p_mean
        if p_cv < UNIFORM_PARA_CV_MAX:
            shape = (f"every one exactly {para_counts[0]} sentences"
                     if len(set(para_counts)) == 1
                     else f"sentence-count CV {p_cv:.2f} < {UNIFORM_PARA_CV_MAX}")
            findings.append({"metric": "uniform-para",
                             "detail": f"{len(para_counts)} paragraphs, {shape}",
                             "excerpt": ""})

    # progress: document-updating (not situation-updating) sentences
    for s in sentences:
        m = _PROGRESS_RE.search(s)
        if m:
            findings.append({"metric": "progress",
                             "detail": f"progress narration 「{m.group(0)}」 — topic-test deletion candidate",
                             "excerpt": _excerpt(s)})

    # connectives: share of sentences opening with an explicit connective
    if sentences:
        n_conn = sum(1 for s in sentences if _CONNECTIVE_RE.match(s))
        ratio = n_conn / len(sentences)
        if ratio > CONNECTIVE_RATIO_MAX and len(sentences) >= UNIFORM_MIN_SENTENCES:
            findings.append({"metric": "connectives",
                             "detail": f"{n_conn}/{len(sentences)} sentences open with a connective ({ratio:.0%} > {CONNECTIVE_RATIO_MAX:.0%})",
                             "excerpt": ""})
    else:
        ratio = 0.0

    # taigendome ratio (informational, never a finding): sentences ending on a
    # noun — no verbal/copula ending matched and the last character is
    # kanji/katakana. Field measurement found AI-generated Japanese sits at
    # ~0% while human prose mixes taigendome in, so a long document at exactly
    # 0% is a weak AI signal — but "add taigendome" is a style call, not a
    # defect, hence report-only.
    n_taigen = 0
    for s in sentences:
        if normalized_ending(s):
            continue
        body = s.rstrip("。！？!?、,.）)」』")
        if body and _NOUNISH_LAST_RE.match(body[-1]):
            n_taigen += 1
    taigen_ratio = (n_taigen / len(sentences)) if sentences else 0.0

    return {"sentences": len(sentences), "paragraphs": len(paragraphs),
            "length_cv": round(cv, 3) if cv is not None else None,
            "burstiness": round(burstiness, 3) if burstiness is not None else None,
            "connective_ratio": round(ratio, 3),
            "taigendome_ratio": round(taigen_ratio, 3),
            "findings": findings}


def format_report(result: dict, label: str) -> str:
    lines = [f"## prose-rhythm: {label}",
             f"sentences: {result['sentences']}  paragraphs: {result['paragraphs']}  "
             f"length-CV: {result['length_cv'] if result['length_cv'] is not None else 'n/a'}  "
             f"burstiness: {result['burstiness'] if result['burstiness'] is not None else 'n/a'}  "
             f"connective-ratio: {result['connective_ratio']:.0%}  "
             f"taigendome: {result['taigendome_ratio']:.0%}"]
    if not result["findings"]:
        lines.append("No rhythm findings. (Surface proxies only — a clean report does not mean the prose has rhythm; it means the script found nothing.)")
        return "\n".join(lines)
    lines.append(f"{len(result['findings'])} finding(s):")
    for f in result["findings"]:
        loc = f"  [{f['metric']}] {f['detail']}"
        if f["excerpt"]:
            loc += f"  — {f['excerpt']}"
        lines.append(loc)
    lines.append("Advisory only: these are surface proxies for the rhythm axis of "
                 "knowledge/ai-writing-smells — the semantic call stays with ai-smell-reviewer.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="deterministic Japanese prose-rhythm sensor (advisory)")
    ap.add_argument("files", nargs="+", help="files to analyze, or '-' for stdin")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    results = {}
    for path in args.files:
        if path == "-":
            results["<stdin>"] = analyze(sys.stdin.read())
        else:
            try:
                text = open(path, encoding="utf-8").read()
            except OSError as e:
                print(f"[ERROR] {path}: {e}", file=sys.stderr)
                sys.exit(2)
            results[path] = analyze(text)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print("\n\n".join(format_report(r, label) for label, r in results.items()))


if __name__ == "__main__":
    main()

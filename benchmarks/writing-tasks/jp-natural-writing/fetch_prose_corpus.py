#!/usr/bin/env python3
"""fetch_prose_corpus.py — human Japanese prose from OUTSIDE the tech-article template.

Companion to fetch_human_corpus.py, which draws from Qiita. The reason for a second
fetcher is that the first one cannot answer the question E3 asked:

    この指標は、著者性（人間かAIか）とジャンル（作業ログか紹介記事か）を分離できていない。
    いま測っているものの一部は「これは Qiita 記事らしいか」であって
    「これは人間が書いたか」ではない。

E3 tried to fix that by re-querying Qiita with 「ハマった」「原因」「備忘録」 and the
document type did not move at all (はじめに見出し 31% -> 44%, 未解決で終わる 0% -> 0%).
That was predictable in hindsight: Qiita full-text search matches a word anywhere in a
body, so a tutorial with a troubleshooting section qualifies, and every result is still a
Qiita article. **You cannot leave the template while staying on the platform.** So this
leaves the platform.

Sources
-------
  hatena   Personal blogs (hatenablog.com / hatenadiary.jp / hateblo.jp), discovered
           through Hatena Bookmark's dated RSS search. Personal essays, diaries, reviews,
           opinion — prose written by a person about their life rather than a howto.
  local    Any directory of .txt / .md / .srt / .vtt already on disk. This is the route
           for video: subtitle files are normalised (timestamps and cue numbers stripped,
           cues joined into paragraphs) and tagged mode=spoken.

Why video is ingested rather than fetched, and why it is tagged
--------------------------------------------------------------
Two things that look like one thing:

  * **Auto-generated captions are not human writing.** They are ASR output — a machine's
    transcription of a human's speech. Scoring them as "human text" would put a second
    machine in the pipeline and attribute its artifacts to the speaker. Only
    human-authored subtitles or transcripts belong here, and only the person supplying
    them can know which they have. A fetcher cannot tell them apart, so it should not
    pretend to.
  * **Speech is not writing.** A transcript has no headings, no bullets, no images. Drop
    it into the discrimination opponent pool and the judge separates it from an arm's
    output trivially — on layout, not on authorship. That would make the genre confound
    *worse* while looking like progress. Hence `mode`: `spoken` documents are recorded as
    a distinct population, and the design memo keeps them out of the opponent pool and
    uses them as a norm probe instead.

Bodies are NOT committed, exactly as with fetch_human_corpus.py — third-party content
under the authors' rights. This writes to a local directory and the repo keeps the script
plus whatever derived metrics an experiment records.

The pre-2023 cut carries over unchanged: ChatGPT shipped 2022-11-30 and LLM-assisted
posts spread through 2023, so a corpus meant to be known-human stops before it.

Usage:
  python fetch_prose_corpus.py --source hatena --out /tmp/blog_corpus
  python fetch_prose_corpus.py --source local --in ~/subs --out /tmp/talk_corpus --mode spoken
  python corpus_profile.py /tmp/human_corpus /tmp/blog_corpus /tmp/talk_corpus
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

HATENA_RSS = "https://b.hatena.ne.jp/search/text"
UA = "rig-benchmark/1.0"

# Personal-blog hosts only. Hatena Bookmark indexes the whole web, so an unfiltered search
# returns news sites and corporate posts — cnn.co.jp came back in the first probe. Those
# are professionally edited copy, which is its own register and not what this is for.
PERSONAL_HOSTS = ("hatenablog.com", "hatenadiary.jp", "hatenadiary.com", "hateblo.jp",
                  "hatenablog.jp")

# Everyday subjects, chosen to stay clear of the tech register the Qiita corpus already
# covers. The point of this corpus is prose whose subject is a life, not a system.
DEFAULT_QUERIES = ["日記", "随筆", "読書感想", "引っ越し", "料理", "旅行",
                   "転職", "子育て", "体調", "習慣"]


def _get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_hatena(query: str, begin: str, end: str, min_users: int) -> list[str]:
    """Entry URLs from Hatena Bookmark's dated RSS search, filtered to personal blogs."""
    params = {"q": query, "mode": "rss", "date_begin": begin, "date_end": end,
              "users": str(min_users), "safe": "on"}
    body = _get(f"{HATENA_RSS}?{urllib.parse.urlencode(params)}").decode("utf-8", "replace")
    urls = re.findall(r"<link>(.*?)</link>", body)
    # The first <link> is the channel itself, not a result.
    return [u for u in urls[1:] if any(h in u for h in PERSONAL_HOSTS)]


def extract_entry(page: str) -> str | None:
    """Pull the article body out of a Hatena Blog page.

    Takes the LARGEST div.entry-content rather than the first: the first is often an
    inactive-blog ad banner, which a first-match implementation returned as a 30-character
    "article" during development. Div nesting means a non-greedy regex to the next
    </div> is also wrong, so this counts depth.
    """
    best: str | None = None
    for m in re.finditer(r'<div[^>]*class="[^"]*\bentry-content\b[^"]*"[^>]*>', page):
        i = m.end()
        depth = 1
        for tag in re.finditer(r"<(/?)div\b[^>]*>", page[i:]):
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                block = page[i:i + tag.start()]
                if best is None or len(block) > len(best):
                    best = block
                break
    if best is None:
        return None
    return html_to_text(best)


def html_to_text(fragment: str) -> str:
    """HTML fragment -> markdown-ish text, PRESERVING structure.

    Structure is preserved rather than stripped because the corpora are compared against
    each other. Qiita bodies arrive from its API as markdown, so their headings, bullets
    and images survive as `#`, `-` and `![]()`. An extractor that flattened this side to
    bare prose would report every form metric as exactly 0.0 for blogs and a healthy
    number for Qiita — and that gap would be the extractor, not the genre.

    The first version of this function did exactly that, and the profile it produced
    (headings/1k 3.41 -> 0.0, 画像 66.7% -> 0.0%, every form metric to zero) looked like a
    successful genre control. It is the same failure R2 recorded: an analyser artifact
    that lands on one side only manufactures a between-population gap. Both corpora have
    to reach the profiler in the same notation or the comparison means nothing.
    """
    out = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", fragment, flags=re.S)
    # Images before figures: a figure usually wraps the img, and dropping figures first
    # would silently delete the image evidence along with the caption.
    out = re.sub(r"<img\b[^>]*>", "\n![](image)\n", out, flags=re.I)
    out = re.sub(r"<figcaption.*?</figcaption>", "", out, flags=re.S | re.I)
    for level in range(1, 7):
        out = re.sub(rf"<h{level}\b[^>]*>", f"\n\n{'#' * level} ", out, flags=re.I)
        out = re.sub(rf"</h{level}>", "\n", out, flags=re.I)
    out = re.sub(r"<li\b[^>]*>", "\n- ", out, flags=re.I)
    out = re.sub(r"<pre\b[^>]*>", "\n```\n", out, flags=re.I)
    out = re.sub(r"</pre>", "\n```\n", out, flags=re.I)
    # Tables become pipe rows so _TABLE_RE sees them the way it sees Qiita's markdown.
    out = re.sub(r"<tr\b[^>]*>", "\n|", out, flags=re.I)
    out = re.sub(r"<(td|th)\b[^>]*>", " ", out, flags=re.I)
    out = re.sub(r"</(td|th)>", " |", out, flags=re.I)
    out = re.sub(r"<br\s*/?>", "\n", out)
    out = re.sub(r"</(p|div|li|blockquote|table)>", "\n", out)
    out = re.sub(r"<[^>]+>", "", out)
    out = html.unescape(out)
    out = re.sub(r"[ \t　]+", " ", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", out).strip()


_TIMECODE_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}(:\d{2})?([.,]\d{1,3})?\s*-->\s*\d{1,2}:\d{2}")
_CUE_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_VTT_TAG_RE = re.compile(r"</?[cvbi][^>]*>|\{\\[^}]*\}")


def subtitle_to_text(raw: str) -> str:
    """Strip .srt/.vtt scaffolding and rejoin cues into prose.

    Cue text is joined with no separator rather than newlines: subtitle line breaks are
    display wrapping, not sentence boundaries, and keeping them would hand every
    shape metric a fake paragraph structure. Consecutive duplicate lines are dropped
    because rolling captions repeat the previous line on every cue.
    """
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if (not s or s.startswith("WEBVTT") or s.startswith("NOTE")
                or _TIMECODE_RE.match(s) or _CUE_INDEX_RE.match(s)):
            continue
        s = _VTT_TAG_RE.sub("", s).strip()
        if s and (not lines or lines[-1] != s):
            lines.append(s)
    text = "".join(lines)
    # Give the sentence splitter something to work with; speech has no punctuation from
    # ASR but human-authored subtitles usually do.
    return re.sub(r"([。！？])", r"\1\n", text).strip()


def collect_hatena(args) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for query in args.queries:
        try:
            urls = search_hatena(query, args.begin, args.before, args.min_users)
        except Exception as exc:
            print(f"  {query:<10} SEARCH FAILED: {type(exc).__name__}: {exc}")
            continue
        kept = 0
        for url in urls:
            if url in seen or kept >= args.per_query:
                continue
            seen.add(url)
            try:
                page = _get(url).decode("utf-8", "replace")
                text = extract_entry(page)
            except Exception as exc:
                print(f"    skip {url[:60]}: {type(exc).__name__}")
                text = None
            time.sleep(args.delay)
            if not text or not (args.min_chars <= len(text) <= args.max_chars):
                continue
            out.append({"topic": query, "text": text, "url": url})
            kept += 1
        print(f"  {query:<10} {kept:>3} entries (of {len(urls)} candidates)")
        time.sleep(args.delay)
    return out


def collect_local(args) -> list[dict]:
    src = args.in_dir
    if src is None or not src.is_dir():
        raise SystemExit("--source local requires --in <directory>")
    out = []
    for path in sorted(src.rglob("*")):
        if path.suffix.lower() not in (".txt", ".md", ".srt", ".vtt"):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        text = subtitle_to_text(raw) if path.suffix.lower() in (".srt", ".vtt") else raw.strip()
        if not (args.min_chars <= len(text) <= args.max_chars):
            print(f"  skip {path.name}: {len(text)} chars outside "
                  f"[{args.min_chars}, {args.max_chars}]")
            continue
        out.append({"topic": path.stem, "text": text, "url": str(path)})
    print(f"  local {len(out):>3} documents from {src}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("hatena", "local"), required=True)
    ap.add_argument("--out", type=Path, required=True,
                    help="local directory for bodies (never committed)")
    ap.add_argument("--in", dest="in_dir", type=Path,
                    help="source directory for --source local")
    ap.add_argument("--mode", choices=("written", "spoken"), default=None,
                    help="recorded in index.json; spoken documents must be kept out of "
                         "the discrimination opponent pool (see the module docstring). "
                         "Defaults to written for hatena, spoken for local.")
    ap.add_argument("--queries", nargs="+", default=DEFAULT_QUERIES)
    ap.add_argument("--begin", default="2018-01-01")
    ap.add_argument("--before", default="2022-12-31",
                    help="pre-2023 by default; ChatGPT shipped 2022-11-30")
    ap.add_argument("--min-users", type=int, default=3, help="Hatena bookmark floor")
    ap.add_argument("--per-query", type=int, default=6)
    ap.add_argument("--min-chars", type=int, default=1200)
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    args = ap.parse_args()

    if args.mode is None:
        args.mode = "spoken" if args.source == "local" else "written"

    args.out.mkdir(parents=True, exist_ok=True)
    docs = collect_hatena(args) if args.source == "hatena" else collect_local(args)

    index = []
    for i, doc in enumerate(docs):
        slug = f"{args.source}-{i:03d}.md"
        (args.out / slug).write_text(doc["text"], encoding="utf-8")
        index.append({"source": args.source, "mode": args.mode, "topic": doc["topic"],
                      "file": slug, "chars": len(doc["text"]), "url": doc["url"]})
    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{len(index)} documents -> {args.out}  (mode={args.mode})")
    if not index:
        raise SystemExit("ERROR: fetched zero usable documents")
    chars = sorted(r["chars"] for r in index)
    print(f"chars: min {chars[0]} / median {chars[len(chars) // 2]} / max {chars[-1]}")
    print("次: python corpus_profile.py <既存コーパス> " + str(args.out))
    if args.mode == "spoken":
        print("⚠ mode=spoken — 判別の対戦相手プールには入れないこと（話し言葉は"
              "レイアウトで自明に分離され、ジャンル交絡を悪化させる）")


if __name__ == "__main__":
    main()

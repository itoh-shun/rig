#!/usr/bin/env python3
"""Fetch pre-LLM human-written Japanese tech articles from Qiita as a reference corpus.

Why this exists: every arm of this benchmark, gated or not, is scored by one model
judging how AI-written the text reads — and that judge has never been checked against
text known to be human. If it scores real human articles as AI, the whole metric is
measuring the judge's prior rather than the text. This fetches the control group.

Why pre-2023 rather than the pre-2024 cut you might expect: ChatGPT shipped 2022-11-30,
and LLM-assisted posts spread through 2023. Cutting at 2023-01-01 costs a year of
articles and buys a corpus that is almost certainly human.

Article bodies are NOT committed. They are third-party content under the authors' rights
and Qiita's terms; this writes them to a local directory only, and the repo keeps just
this script plus whatever derived metrics an experiment records. That is the same line
coji/natural-japanese draws by shipping public-domain 青空文庫 and leaving corpus/human/web
empty.

Unauthenticated Qiita API allows 60 requests/hour, so this makes one request per topic.
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://qiita.com/api/v2/items"

# Matched to the benchmark's TOPICS so the human control is drawn from the same subject
# matter the arms are asked to write about. Comparing arms on 機械学習 against humans on
# a different subject would confound register with topic.
TOPIC_TAGS = {
    "Python": "Python",
    "機械学習": "機械学習",
    "クラウドコンピューティング": "AWS",
    "Web開発": "JavaScript",
    "データベース設計": "MySQL",
    "リモートワーク": "初心者",
    "セキュリティ対策": "Security",
    "チーム開発": "Git",
}


# Genre control. The default pool is whatever Qiita ranks for a tag, which is mostly
# tutorials and introductions — and the arms write work logs. Aggregating the winning
# verdicts, the single most cited reason a generated article was picked as human was that
# the OPPONENT looked templated (39/47), well ahead of anything about the candidate. So the
# measure conflates authorship with genre: part of the 92-95% is "is this a Qiita article",
# not "did a human write this". These terms pull human articles that are themselves
# debugging notes, so both sides of the pair are the same kind of document.
GENRE_TERMS = ["ハマった", "原因", "備忘録"]


def fetch(tag: str, before: str, min_stocks: int, per_page: int,
          genre_term: str = "") -> list[dict]:
    # `stocks:>N`, not `likes:>N` — the likes qualifier silently returns zero results
    # rather than erroring, which reads as "no pre-2023 articles exist" if unchecked.
    query = f"created:<{before} tag:{tag} stocks:>{min_stocks}"
    if genre_term:
        query += f" {genre_term}"
    url = f"{API}?{urllib.parse.urlencode({'per_page': per_page, 'page': 1, 'query': query})}"
    req = urllib.request.Request(url, headers={"User-Agent": "rig-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        remaining = resp.headers.get("rate-remaining")
        items = json.load(resp)
    print(f"  {tag:<12} {len(items):>3} articles (rate-remaining {remaining})")
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="local directory for bodies (not committed)")
    ap.add_argument("--before", default="2023-01-01")
    ap.add_argument("--min-stocks", type=int, default=10,
                    help="quality floor; unstocked posts are noise")
    ap.add_argument("--per-topic", type=int, default=10)
    ap.add_argument("--genre", action="store_true",
                    help="restrict to work-log/debugging articles (see GENRE_TERMS)")
    ap.add_argument("--min-chars", type=int, default=1200,
                    help="drop articles too short for document-level detectors")
    ap.add_argument("--max-chars", type=int, default=4000,
                    help="drop articles far longer than the arms' 1500-2500 char target")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    index = []

    for topic, tag in TOPIC_TAGS.items():
        terms = GENRE_TERMS if args.genre else [""]
        items, seen_ids = [], set()
        for term in terms:
            try:
                got = fetch(tag, args.before, args.min_stocks, args.per_topic * 3, term)
            except Exception as exc:
                print(f"  {tag:<12} FAILED: {exc}")
                continue
            for item in got:
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    items.append(item)
            time.sleep(1)
        if not items:
            continue

        kept = 0
        for item in items:
            body = item.get("body") or ""
            # Code-heavy posts are a different register from the prose the arms produce;
            # a fenced-block majority would compare prose against listings.
            fenced = body.count("```")
            if not (args.min_chars <= len(body) <= args.max_chars) or fenced > 6:
                continue
            slug = f"{topic}-{item['id'][:10]}.md"
            (args.out / slug).write_text(body, encoding="utf-8")
            index.append({
                "topic": topic, "tag": tag, "file": slug,
                "created_at": item["created_at"][:10],
                "likes": item.get("likes_count"), "chars": len(body),
                "url": item.get("url"),
            })
            kept += 1
            if kept >= args.per_topic:
                break
        time.sleep(1)

    (args.out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{len(index)} articles -> {args.out}")
    if not index:
        raise SystemExit("ERROR: fetched zero usable articles")
    chars = [r["chars"] for r in index]
    print(f"chars: min {min(chars)} / median {sorted(chars)[len(chars)//2]} / max {max(chars)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate repeatedly from identical material, then compare what lands human against what does not.

Per-article behaviour is not explained by the material. sample_incident is deterministic per
topic, so two runs of the writer arm saw the same ledger entries, and the outcomes still
diverged — チーム開発 read as human 3/3 in both runs while リモートワーク went 2/3 then 0/3.
All eight topics draw an open test failure as their root and five share the *same* root, yet
they span 0 to 3 human reads. So whatever separates them sits in the generation, not the
supply, and three attempts to explain it from the material have failed.

This holds the material fixed and varies only the generation: N articles from one topic's
incident, each judged repeatedly, then a feature comparison between the ones that land in
the human band and the ones that do not. With material controlled, a feature that separates
them is a property of the writing rather than of what there was to write about.

Sample size is the obvious limit — N generations of one topic cannot separate a real effect
from a lucky split, and the judge's own spread (sd 21-30 on this arm) sits underneath every
number here. Read it as a direction to test, not a result.
"""

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import writer_ledger as wl  # noqa: E402
from analyze_gap import features  # noqa: E402
from hidden_check import (  # noqa: E402
    DEFAULT_GEN_MODEL, DEFAULT_JUDGE_MODEL, HUMAN_READ_THRESHOLD, LENGTH_SPEC,
    MAX_PARALLEL, WRITER_PROMPT, judge, run_claude_json,
)

FEATURES = [
    "sent_len_stdev", "sent_len_max", "latin_token_per1k", "paren_per1k",
    "colloquial_per1k", "hedge_per1k", "connective_start_pct", "type_token_ratio",
    "para_final_reflect_pct", "first_person_per1k", "final_form_variety",
    "comma_per_sent", "heading_per1k", "question_per1k",
]


def generate(topic: str, model: str, ledger: str, prior: str, nonce: int) -> dict:
    """One article from fixed material. `nonce` only varies the sampling, not the prompt."""
    prompt = WRITER_PROMPT.format(
        ledger=ledger, prior=prior, topic=topic, length_spec=LENGTH_SPEC)
    out = run_claude_json(prompt, model, [])
    return {"nonce": nonce, "title": out["title"], "description": out["description"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="リモートワーク", help="one that behaved unstably")
    ap.add_argument("--n", type=int, default=6, help="generations from the same material")
    ap.add_argument("--gen-model", default=DEFAULT_GEN_MODEL)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    state = wl.build_ledger()
    incident = wl.sample_incident(state, args.topic)
    if not incident["entries"]:
        raise SystemExit("ledger is empty; run writer_ledger.py --force")
    ledger, prior = wl.render_incident(incident), wl.render_prior(state)

    print(f"topic={args.topic}  n={args.n}  gen={args.gen_model}  judge={args.judge_model}")
    print(f"素材（全生成で同一）: spine {len(incident['spine'])} / scraps {len(incident['scraps'])}")
    print(f"  根: {incident['goal'][:90] or '(未解決項目なし)'}\n")

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        articles = list(pool.map(
            lambda i: generate(args.topic, args.gen_model, ledger, prior, i), range(args.n)))
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        verdicts = list(pool.map(
            lambda a: judge(f"{a['title']}\n{a['description']}", args.judge_model), articles))

    rows = []
    for article, verdict in zip(articles, verdicts):
        text = f"{article['title']}\n\n{article['description']}"
        rows.append({**article, **verdict, "chars": len(text), "features": features(text)})

    print(f"{'#':>2}{'scores':>18}{'平均':>7}{'sd':>6}{'字数':>7}  タイトル")
    for r in rows:
        print(f"{r['nonce']:>2}{str([int(x) for x in r['scores']]):>18}{r['score']:>7.1f}"
              f"{r['score_sd']:>6.1f}{r['chars']:>7}  {r['title'][:44]}")

    landed = [r for r in rows if r["human_read"] > 0]
    missed = [r for r in rows if r["human_read"] == 0]
    print(f"\n人間帯に入った生成: {len(landed)}/{len(rows)}"
          f"  (判定単位 {sum(r['human_read'] for r in rows)}/{sum(len(r['scores']) for r in rows)})")

    if not landed or not missed:
        print("片側が空なので比較できない。--n を増やすか別トピックで。")
    else:
        print(f"\n{'feature':<24}{'入った':>10}{'入らない':>10}{'差':>9}")
        for key in FEATURES:
            a = statistics.mean([r["features"][key] for r in landed])
            b = statistics.mean([r["features"][key] for r in missed])
            rel = (a - b) / abs(b) * 100 if b else 0.0
            flag = " ←" if abs(rel) >= 25 else ""
            print(f"{key:<24}{a:>10.2f}{b:>10.2f}{rel:>+8.0f}%{flag}")
        print(f"\n← は相対差 25% 以上。n={len(landed)} vs {len(missed)} なので方向の候補にすぎない")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"topic": args.topic, "n": args.n, "threshold": HUMAN_READ_THRESHOLD,
             "material": {"goal": incident["goal"],
                          "spine": [e["fact"] for e in incident["spine"]],
                          "scraps": [e["fact"] for e in incident["scraps"]]},
             "rows": rows}, ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

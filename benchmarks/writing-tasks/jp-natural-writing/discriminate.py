#!/usr/bin/env python3
"""Pairwise discrimination: show the judge a real article and a generated one, ask which is human.

Why this replaces absolute scoring as the primary measure. The absolute judge is bimodal on
exactly the texts that matter — the same bytes have scored 9, 62 and 68 — because a 0-100
scale forces it to pick a threshold, and whether it foregrounds the concreteness or the
uniformity of a given text varies call to call. A two-alternative choice removes the
threshold: the judge only compares. It is also the goal metric itself, not a proxy. The
benchmark's stated target is indistinguishability, which is literally a discrimination rate
of 50%. bare should sit near 100%.

Design details that matter:

- Each generated article is paired with a real pre-2023 Qiita article on the same topic,
  so topic knowledge cannot carry the decision.
- Every pair is judged twice, once in each order, because position bias in 2AFC setups is
  real and averaging over both orders cancels it.
- Both texts are truncated to the same ceiling. The human corpus runs to 3951 chars and the
  arms are capped at 2500; without a shared cut, length alone would give the answer away.
  Truncation can itself manufacture a mid-sentence ending — a known human-credited cue — so
  the cut is applied identically to both sides.
- The human bodies are markdown with images and links, which no arm produces. If the rate
  is near 100% purely on those, that is a finding about what to fix next, not a fault in
  the test. The per-pair reasonings are kept so this is checkable.

Runs on recorded samples; no new generation.
"""

import argparse
import hashlib
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hidden_check import (  # noqa: E402
    DEFAULT_JUDGE_MODEL, MAX_PARALLEL, READONLY_TOOLS, run_claude_json,
)

DISCRIM_PROMPT = """以下の2つの日本語技術記事のうち、一方は人間が書いたもの、もう一方は AI が生成したものです。

記事A:
\"\"\"
{a}
\"\"\"

記事B:
\"\"\"
{b}
\"\"\"

どちらが人間の書いたものかを判定してください。

JSON オブジェクトのみを出力してください。他の文字列は一切含めないこと:
{{"human": "A" または "B", "reasoning": "<日本語で一文>"}}"""


def sample_text(sample: dict) -> str:
    """Return the exact candidate text.

    Normal benchmark records keep title and description separately. Calibration controls
    sometimes need to substitute a complete document byte-for-byte (for example a held-out
    human article); ``full_text`` is an explicit escape hatch for that case.
    """
    if "full_text" in sample:
        return str(sample["full_text"])
    return f"{sample['title']}\n\n{sample['description']}"


def load_pairs(run_paths: list[Path], corpus: Path, arms: list[str] | None,
               ceiling: int, opponents: int) -> list[dict]:
    """Build (generated, human) pairs, several human opponents per topic.

    Opponent count is the binding constraint on this test's resolving power. One article
    per topic gives 8 pairs = 16 trials per arm, and at a fooled-rate near 2/16 that
    cannot separate a doubling from noise (~150 trials would be needed). Repeating a
    judgment on the *same* pair does not help — those trials correlate. Distinct
    opponents do, and they cost nothing to add: the corpus already holds several articles
    per topic and no new generation is involved.

    Several run files can be passed. Their arms are merged into one job list so that
    every condition faces byte-identical opponents inside a single invocation — the
    paired design the linkified control used.
    """
    index = json.loads((corpus / "index.json").read_text())
    by_topic: dict[str, list[tuple[str, str]]] = {}
    for entry in sorted(index, key=lambda e: e["file"]):  # deterministic opponent order
        by_topic.setdefault(entry["topic"], []).append(
            (entry["file"], (corpus / entry["file"]).read_text()))

    pairs = []
    for run_path in run_paths:
        record = json.loads(run_path.read_text())
        for arm in (arms if arms is not None else record["arms"]):
            if arm not in record["arms"]:
                continue
            for sample in record["arms"][arm]["samples"]:
                for human_file, human in by_topic.get(sample["topic"], [])[:opponents]:
                    pairs.append({
                        "arm": arm,
                        "topic": sample["topic"],
                        "opponent": human_file,
                        # pair identity is arm-independent, so conditions can be compared
                        # pair-by-pair rather than only in aggregate
                        "pair_id": f"{sample['topic']}::{human_file}",
                        "generated": sample_text(sample)[:ceiling],
                        "human": human[:ceiling],
                        "abs_score": sample.get("score"),
                    })
    return pairs


def judge_pair(pair: dict, human_pos: str, model: str) -> dict:
    a, b = ((pair["human"], pair["generated"]) if human_pos == "A"
            else (pair["generated"], pair["human"]))
    out = run_claude_json(DISCRIM_PROMPT.format(a=a, b=b), model, READONLY_TOOLS)
    pick = str(out.get("human", "")).strip().upper()[:1]
    return {
        "human_pos": human_pos,
        "pick": pick,
        "correct": pick == human_pos,
        "reasoning": str(out.get("reasoning", "")),
    }


def job_key(pair: dict, human_pos: str) -> str:
    return f"{pair['arm']}::{pair['pair_id']}::{human_pos}"


def jobs_fingerprint(jobs: list[tuple[dict, str]], model: str) -> str:
    """Bind a checkpoint to exact inputs without storing copyrighted bodies."""
    rows = []
    for pair, human_pos in jobs:
        rows.append({
            "key": job_key(pair, human_pos),
            "generated_sha1": hashlib.sha1(pair["generated"].encode()).hexdigest(),
            "human_sha1": hashlib.sha1(pair["human"].encode()).hexdigest(),
        })
    payload = json.dumps(
        {"model": model, "jobs": rows}, ensure_ascii=False, sort_keys=True
    ).encode()
    return hashlib.sha1(payload).hexdigest()


def save_checkpoint(path: Path, fingerprint: str, verdicts: dict[str, dict]) -> None:
    payload = {
        "schema_version": 1,
        "fingerprint": fingerprint,
        "completed": len(verdicts),
        "verdicts": verdicts,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    temporary.replace(path)


def load_checkpoint(path: Path, fingerprint: str) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    if payload.get("fingerprint") != fingerprint:
        raise ValueError(
            f"checkpoint input mismatch: {path} belongs to a different batch"
        )
    return dict(payload.get("verdicts", {}))


def run_jobs(
    jobs: list[tuple[dict, str]],
    model: str,
    parallel: int,
    checkpoint: Path | None,
) -> list[dict]:
    fingerprint = jobs_fingerprint(jobs, model)
    verdicts = load_checkpoint(checkpoint, fingerprint) if checkpoint else {}
    missing = [job for job in jobs if job_key(*job) not in verdicts]
    if verdicts:
        print(f"checkpoint: {len(verdicts)} completed, {len(missing)} remaining")
    errors = []
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(judge_pair, pair, human_pos, model): (pair, human_pos)
            for pair, human_pos in missing
        }
        for future in as_completed(futures):
            pair, human_pos = futures[future]
            key = job_key(pair, human_pos)
            try:
                verdicts[key] = future.result()
            except Exception as exc:
                errors.append((key, str(exc)))
                continue
            if checkpoint:
                save_checkpoint(checkpoint, fingerprint, verdicts)
            if len(verdicts) % 25 == 0 or len(verdicts) == len(jobs):
                print(f"progress: {len(verdicts)}/{len(jobs)} judgments")
    if errors:
        preview = "; ".join(f"{key}: {message[:120]}" for key, message in errors[:3])
        raise RuntimeError(
            f"{len(errors)} judgment(s) failed; completed verdicts remain in "
            f"{checkpoint or '<no checkpoint>'}: {preview}"
        )
    return [verdicts[job_key(pair, human_pos)] for pair, human_pos in jobs]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, action="append", required=True,
                    help="run record; repeatable — all runs are judged against the same opponents")
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("--arms", default="", help="comma-separated; empty = every arm in every run")
    ap.add_argument("--opponents", type=int, default=4,
                    help="human articles per topic (raises resolving power; see load_pairs)")
    ap.add_argument("--ceiling", type=int, default=2500)
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--parallel", type=int, default=MAX_PARALLEL)
    ap.add_argument("--checkpoint", type=Path,
                    help="incremental body-free verdict cache; rerun the same command to resume")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()] or None
    pairs = load_pairs(args.run, args.corpus, arms, args.ceiling, args.opponents)
    arms = list(dict.fromkeys(p["arm"] for p in pairs))
    jobs = [(p, pos) for p in pairs for pos in ("A", "B")]
    print(f"{len(pairs)} pairs x 2 orders = {len(jobs)} calls  (judge {args.judge_model})")
    print(f"arms: {', '.join(arms)}   opponents/topic: {args.opponents}\n")

    verdicts = run_jobs(jobs, args.judge_model, args.parallel, args.checkpoint)

    for (pair, _), verdict in zip(jobs, verdicts):
        pair.setdefault("trials", []).append(verdict)

    print(f"{'arm':<16}{'判別率':>8}{'(正解/試行)':>12}   50%=見分けがつかない, 100%=常に見破られる")
    results = {}
    for arm in arms:
        mine = [p for p in pairs if p["arm"] == arm]
        trials = [t for p in mine for t in p["trials"]]
        correct = sum(1 for t in trials if t["correct"])
        rate = correct / len(trials) if trials else float("nan")
        results[arm] = {"correct": correct, "trials": len(trials), "rate": round(rate, 3)}
        print(f"{arm:<16}{rate * 100:>7.0f}%{f'({correct}/{len(trials)})':>12}")

    # Paired comparison. Aggregate rates hide the thing worth knowing: whether a mutation
    # moved *the same pairs*. The linkified control looked identical in aggregate (14/16
    # both sides) and was identical pair-by-pair too — that second fact is what made the
    # zero conclusive rather than a coincidence of matching totals.
    paired = {}
    if len(arms) >= 2:
        base = arms[0]
        by_pair = {}
        for pair in pairs:
            fooled_n = sum(1 for t in pair["trials"] if not t["correct"])
            by_pair.setdefault(pair["pair_id"], {})[pair["arm"]] = fooled_n
        for arm in arms[1:]:
            both = [(pid, v[base], v[arm]) for pid, v in by_pair.items()
                    if base in v and arm in v]
            gained = [p for p in both if p[2] > p[1]]
            lost = [p for p in both if p[2] < p[1]]
            paired[arm] = {"vs": base, "pairs": len(both),
                           "gained": len(gained), "lost": len(lost),
                           "unchanged": len(both) - len(gained) - len(lost)}
            print(f"\n対応ありペア比較  {base} → {arm}   ({len(both)} ペア)")
            print(f"  騙せる方向へ動いた {len(gained)} / 逆方向 {len(lost)} / 不動 "
                  f"{len(both) - len(gained) - len(lost)}")
            for pid, b, a in gained + lost:
                print(f"    {pid:<44} 騙せた試行 {b} → {a}")

    fooled = [(p, t) for p in pairs for t in p["trials"] if not t["correct"]]
    print(f"\n見破られなかった試行 {len(fooled)} 件:")
    for pair, trial in fooled[:10]:
        print(f"  [{pair['arm']}/{pair['topic']}] vs {pair['opponent']}")
        print(f"    → {trial['reasoning'][:170]}")

    if args.json_out:
        args.json_out.write_text(json.dumps(
            {"runs": [str(r) for r in args.run], "ceiling": args.ceiling,
             "opponents_per_topic": args.opponents,
             "judge_model": args.judge_model,
             "summary": results, "paired": paired,
             "pairs": [{k: v for k, v in p.items() if k not in ("generated", "human")}
                       for p in pairs]},
            ensure_ascii=False, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

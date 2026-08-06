#!/usr/bin/env python3
"""Build and analyse a positive-control curve for pairwise discrimination.

The benchmark historically had a null control but no positive control. This utility makes
one without generating new text:

* 0% is the recorded generated arm.
* At increasing doses, complete generated articles are replaced by held-out, same-topic,
  pre-2023 human articles.
* 100% is therefore human-vs-human, while intermediate arms are population mixtures rather
  than incoherent sentence splices.

The temporary run contains third-party article bodies and MUST stay outside the repository.
The temporary manifest may contain local corpus identifiers for reproducibility, but it
MUST stay outside the repository.  The final analysis deliberately publishes only
anonymous pair outcomes and derived numbers: no article body, source item id, body hash,
or judge commentary is retained.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from pathlib import Path

from discriminate import sample_text

DEFAULT_LEVELS = (0.0, 0.125, 0.25, 0.5, 0.75, 1.0)


def sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha1_file(path: Path) -> str:
    return sha1_bytes(path.read_bytes())


def arm_name(level: float) -> str:
    return f"pc_human_{round(level * 1000):04d}"


def load_index(corpus: Path) -> list[dict]:
    path = corpus / "index.json"
    rows = json.loads(path.read_text())
    if not rows:
        raise ValueError(f"empty corpus index: {path}")
    return sorted(rows, key=lambda row: row["file"])


def by_topic(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["topic"], []).append(row)
    return grouped


def deterministic_order(values: list[str], seed: str) -> list[str]:
    return sorted(values, key=lambda value: sha1_bytes(f"{seed}::{value}".encode()))


def choose_donors(
    topics: list[str],
    opponent_rows: list[dict],
    opponent_corpus: Path,
    donor_rows: list[dict],
    donor_corpus: Path,
    opponents: int,
    seed: str,
) -> dict[str, dict]:
    opponent_by_topic = by_topic(opponent_rows)
    pools = [
        ("opponent-corpus", opponent_corpus, opponent_rows),
        ("donor-corpus", donor_corpus, donor_rows),
    ]
    chosen: dict[str, dict] = {}
    for topic in topics:
        used = {row["file"] for row in opponent_by_topic.get(topic, [])[:opponents]}
        candidates = []
        seen = set()
        for corpus_label, corpus_path, rows in pools:
            for row in rows:
                if row["topic"] != topic or row["file"] in used:
                    continue
                body_path = corpus_path / row["file"]
                body_sha1 = sha1_file(body_path)
                identity = (row["file"], body_sha1)
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append({
                    "topic": topic,
                    "file": row["file"],
                    "corpus": corpus_label,
                    "path": body_path,
                    "sha1": body_sha1,
                    "chars": len(body_path.read_text()),
                })
        if not candidates:
            raise ValueError(
                f"no held-out donor for {topic}: need an article outside the first "
                f"{len(used)} opponents"
            )
        candidates.sort(
            key=lambda row: sha1_bytes(
                f"{seed}::{topic}::{row['file']}::{row['sha1']}".encode()
            )
        )
        chosen[topic] = candidates[0]
    return chosen


def expected_pair_count(samples: list[dict], opponent_rows: list[dict], opponents: int) -> int:
    counts = {topic: len(rows) for topic, rows in by_topic(opponent_rows).items()}
    return sum(min(counts.get(sample["topic"], 0), opponents) for sample in samples)


def prepare(
    source_run: Path,
    source_arm: str,
    opponent_corpus: Path,
    donor_corpus: Path,
    opponents: int,
    levels: tuple[float, ...],
    seed: str,
) -> tuple[dict, dict]:
    record = json.loads(source_run.read_text())
    if source_arm not in record.get("arms", {}):
        raise ValueError(f"arm {source_arm!r} not found in {source_run}")
    source_samples = record["arms"][source_arm]["samples"]
    topics = [sample["topic"] for sample in source_samples]
    if len(topics) != len(set(topics)):
        raise ValueError("positive control requires one generated article per topic")
    if not levels or levels[0] != 0.0 or levels[-1] != 1.0:
        raise ValueError("levels must start at 0 and end at 1")
    if tuple(sorted(set(levels))) != levels:
        raise ValueError("levels must be unique and increasing")

    opponent_rows = load_index(opponent_corpus)
    donor_rows = load_index(donor_corpus)
    donors = choose_donors(
        topics, opponent_rows, opponent_corpus, donor_rows, donor_corpus, opponents, seed
    )
    topic_order = deterministic_order(topics, seed)
    pair_count = expected_pair_count(source_samples, opponent_rows, opponents)
    if pair_count <= 0:
        raise ValueError("positive control has no human opponents")

    arms = {
        source_arm: copy.deepcopy(record["arms"][source_arm]),
        f"{source_arm}_null": copy.deepcopy(record["arms"][source_arm]),
    }
    arm_plan = [
        {
            "arm": source_arm,
            "target_fraction": 0.0,
            "actual_fraction": 0.0,
            "replaced_articles": 0,
            "replaced_topics": [],
            "kind": "baseline",
        },
        {
            "arm": f"{source_arm}_null",
            "target_fraction": 0.0,
            "actual_fraction": 0.0,
            "replaced_articles": 0,
            "replaced_topics": [],
            "kind": "byte-identical-null",
        },
    ]

    for level in levels[1:]:
        replace_n = round(level * len(topics))
        replaced = set(topic_order[:replace_n])
        samples = copy.deepcopy(source_samples)
        for sample in samples:
            if sample["topic"] in replaced:
                sample["full_text"] = donors[sample["topic"]]["path"].read_text()
        name = arm_name(level)
        arms[name] = {
            "label": f"positive control: {replace_n}/{len(topics)} whole articles are human",
            "samples": samples,
            "stats": {},
        }
        arm_plan.append({
            "arm": name,
            "target_fraction": level,
            "actual_fraction": replace_n / len(topics),
            "replaced_articles": replace_n,
            "replaced_topics": topic_order[:replace_n],
            "kind": "held-out-human-mixture",
        })

    temp_record = {
        "mode": "mde-positive-control",
        "source_run": str(source_run),
        "source_arm": source_arm,
        "judge_model": record.get("judge_model"),
        "arms": arms,
    }

    donor_manifest = {}
    for topic, donor in donors.items():
        donor_manifest[topic] = {
            key: donor[key] for key in ("file", "corpus", "sha1", "chars")
        }
    base_bytes = {
        sample["topic"]: sha1_bytes(sample_text(sample).encode())
        for sample in arms[source_arm]["samples"]
    }
    null_bytes = {
        sample["topic"]: sha1_bytes(sample_text(sample).encode())
        for sample in arms[f"{source_arm}_null"]["samples"]
    }
    endpoint = arms[arm_name(1.0)]["samples"]
    endpoint_bytes = {
        sample["topic"]: sha1_bytes(sample_text(sample).encode()) for sample in endpoint
    }
    manifest = {
        "schema_version": 1,
        "design": "whole-article held-out-human mixture",
        "source_run": str(source_run),
        "source_run_sha1": sha1_file(source_run),
        "source_arm": source_arm,
        "source_ledger": record.get("ledger"),
        "seed": seed,
        "levels": list(levels),
        "topics": topics,
        "topic_order": topic_order,
        "opponents_per_topic": opponents,
        "expected_pairs_per_arm": pair_count,
        "expected_trials_per_arm": pair_count * 2,
        "opponent_index_sha1": sha1_file(opponent_corpus / "index.json"),
        "donor_index_sha1": sha1_file(donor_corpus / "index.json"),
        "donors": donor_manifest,
        "arms": arm_plan,
        "preflight": {
            "null_byte_identical": base_bytes == null_bytes,
            "endpoint_all_human_donors": all(
                endpoint_bytes[topic] == donors[topic]["sha1"] for topic in topics
            ),
            "donors_disjoint_from_opponents": all(
                donor["file"] not in {
                    row["file"] for row in by_topic(opponent_rows)[topic][:opponents]
                }
                for topic, donor in donors.items()
            ),
            "no_copyrighted_bodies_in_manifest": True,
        },
    }
    if not all(manifest["preflight"].values()):
        raise ValueError(f"preflight failed: {manifest['preflight']}")
    return temp_record, manifest


def exact_sign_p(gained: int, lost: int) -> float:
    n = gained + lost
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(gained, lost) + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def cluster_signflip_p(differences: list[int]) -> float:
    nonzero = [value for value in differences if value]
    if not nonzero:
        return 1.0
    observed = abs(sum(nonzero))
    extreme = 0
    total = 2 ** len(nonzero)
    for signs in itertools.product((-1, 1), repeat=len(nonzero)):
        statistic = abs(sum(sign * value for sign, value in zip(signs, nonzero)))
        if statistic >= observed:
            extreme += 1
    return extreme / total


def approximate_matched_trials(
    effect_points: float,
    adverse_rate: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    """Normal-approximation lower bound for paired binary trials.

    ``adverse_rate`` is null-floor movement against the desired direction. The estimate
    deliberately ignores article clustering, so it is a lower bound rather than a promise.
    z values are fixed for the only supported planning convention (two-sided 5%, 80%).
    """
    if alpha != 0.05 or power != 0.8:
        raise ValueError("sample-size approximation currently supports alpha=.05, power=.8")
    effect = effect_points / 100
    discordance = effect + 2 * adverse_rate
    variance = discordance - effect ** 2
    return math.ceil(((1.959964 + 0.841621) ** 2) * variance / (effect ** 2))


def public_manifest(manifest: dict) -> dict:
    """Remove identities derived from the locally fetched third-party corpus."""
    allowed = (
        "schema_version", "design", "source_run", "source_run_sha1", "source_arm",
        "source_ledger", "seed", "levels", "topics", "topic_order",
        "opponents_per_topic", "expected_pairs_per_arm", "expected_trials_per_arm",
        "arms", "preflight",
    )
    public = {key: copy.deepcopy(manifest[key]) for key in allowed if key in manifest}
    public["donor_article_chars"] = {
        topic: donor["chars"]
        for topic, donor in manifest.get("donors", {}).items()
        if "chars" in donor
    }
    public["corpus_identifiers_omitted"] = True
    return public


def anonymous_pair_rows(rows: list[dict], base_arm: str) -> list[dict]:
    """Keep paired numeric outcomes without publishing corpus filenames or commentary."""
    base_rows = [row for row in rows if row["arm"] == base_arm]
    aliases = {
        row["pair_id"]: f"{row['topic']}::pair-{index:02d}"
        for index, row in enumerate(base_rows, 1)
    }
    public = []
    for row in rows:
        clean = {
            "arm": row["arm"],
            "topic": row["topic"],
            "pair_id": aliases[row["pair_id"]],
        }
        if "abs_score" in row:
            clean["abs_score"] = row["abs_score"]
        clean["trials"] = [
            {
                key: trial[key]
                for key in ("human_pos", "pick", "correct")
                if key in trial
            }
            for trial in row["trials"]
        ]
        public.append(clean)
    return public


def analyse(discrimination: dict, manifest: dict, alpha: float = 0.05) -> dict:
    base = manifest["source_arm"]
    expected_trials = manifest["expected_trials_per_arm"]
    expected_pairs = manifest["expected_pairs_per_arm"]
    expected_arms = [row["arm"] for row in manifest["arms"]]
    missing_arms = [arm for arm in expected_arms if arm not in discrimination["summary"]]
    wrong_trials = {
        arm: discrimination["summary"][arm]["trials"]
        for arm in expected_arms if arm in discrimination["summary"]
        and discrimination["summary"][arm]["trials"] != expected_trials
    }
    wrong_pairs = {
        arm: sum(1 for row in discrimination["pairs"] if row["arm"] == arm)
        for arm in expected_arms
        if sum(1 for row in discrimination["pairs"] if row["arm"] == arm) != expected_pairs
    }
    if missing_arms or wrong_trials or wrong_pairs:
        raise ValueError(
            f"incomplete discrimination batch: missing={missing_arms}, "
            f"trials={wrong_trials}, pairs={wrong_pairs}"
        )
    pair_rows = discrimination["pairs"]
    by_arm_pair = {(row["arm"], row["pair_id"]): row for row in pair_rows}
    base_pairs = {
        row["pair_id"]: row for row in pair_rows if row["arm"] == base
    }
    comparisons = []
    for plan in manifest["arms"][1:]:
        arm = plan["arm"]
        gained_trials = lost_trials = 0
        gained_pairs = lost_pairs = 0
        topic_differences = {topic: 0 for topic in manifest["topics"]}
        for pair_id, base_row in base_pairs.items():
            other_row = by_arm_pair[(arm, pair_id)]
            base_trials = {trial["human_pos"]: trial for trial in base_row["trials"]}
            other_trials = {trial["human_pos"]: trial for trial in other_row["trials"]}
            base_correct = sum(int(trial["correct"]) for trial in base_trials.values())
            other_correct = sum(int(trial["correct"]) for trial in other_trials.values())
            if other_correct < base_correct:
                gained_pairs += 1
            elif other_correct > base_correct:
                lost_pairs += 1
            for pos in ("A", "B"):
                b = int(base_trials[pos]["correct"])
                a = int(other_trials[pos]["correct"])
                if a < b:
                    gained_trials += 1
                elif a > b:
                    lost_trials += 1
                topic_differences[base_row["topic"]] += a - b

        base_rate = discrimination["summary"][base]["rate"]
        arm_rate = discrimination["summary"][arm]["rate"]
        delta_points = round((arm_rate - base_rate) * 100, 1)
        comparisons.append({
            **plan,
            "discrimination_rate": arm_rate,
            "delta_points_vs_baseline": delta_points,
            "gained_trials": gained_trials,
            "lost_trials": lost_trials,
            "trial_mcnemar_exact_p": round(exact_sign_p(gained_trials, lost_trials), 6),
            "gained_pairs": gained_pairs,
            "lost_pairs": lost_pairs,
            "pair_sign_exact_p": round(exact_sign_p(gained_pairs, lost_pairs), 6),
            "topic_differences_correct": topic_differences,
            "topic_cluster_signflip_p": round(
                cluster_signflip_p(list(topic_differences.values())), 6
            ),
        })

    positive = [row for row in comparisons if row["kind"] == "held-out-human-mixture"]
    null = next(row for row in comparisons if row["kind"] == "byte-identical-null")
    rates = [discrimination["summary"][base]["rate"]] + [
        row["discrimination_rate"] for row in positive
    ]
    monotonic = all(after <= before for before, after in zip(rates, rates[1:]))

    def first_detected(key: str) -> dict | None:
        rows = [
            row for row in positive
            if row["delta_points_vs_baseline"] < 0 and row[key] <= alpha
        ]
        if not rows:
            return None
        return min(rows, key=lambda row: abs(row["delta_points_vs_baseline"]))

    detected = {
        "trial_level": first_detected("trial_mcnemar_exact_p"),
        "pair_level": first_detected("pair_sign_exact_p"),
        "topic_cluster_level": first_detected("topic_cluster_signflip_p"),
    }
    mde = {
        level: (
            None if row is None else {
                "arm": row["arm"],
                "actual_fraction": row["actual_fraction"],
                "observed_difference_points": abs(row["delta_points_vs_baseline"]),
                "p": row[{
                    "trial_level": "trial_mcnemar_exact_p",
                    "pair_level": "pair_sign_exact_p",
                    "topic_cluster_level": "topic_cluster_signflip_p",
                }[level]],
            }
        )
        for level, row in detected.items()
    }
    adverse_rate = null["lost_trials"] / expected_trials
    planning_effects = [1.6, 3.2, 5.0, 10.0, 19.4, 29.0]
    power_plan = [
        {
            "effect_points": effect,
            "matched_trials_lower_bound": approximate_matched_trials(
                effect, adverse_rate, alpha, 0.8
            ),
            "generated_articles_lower_bound_at_4_opponents_x_2_orders": math.ceil(
                approximate_matched_trials(effect, adverse_rate, alpha, 0.8) / 8
            ),
        }
        for effect in planning_effects
    ]
    cluster_undetected = [
        abs(row["delta_points_vs_baseline"]) for row in positive
        if row["topic_cluster_signflip_p"] > alpha
    ]
    pair_undetected = [
        abs(row["delta_points_vs_baseline"]) for row in positive
        if row["pair_sign_exact_p"] > alpha
    ]
    public_pairs = anonymous_pair_rows(discrimination["pairs"], base)
    return {
        "schema_version": 2,
        "question": "minimum detectable effect of the 31-pair, two-order discriminator",
        "alpha": alpha,
        "primary_unit": "topic/generated article (8 clusters)",
        "secondary_units": ["pair (31)", "trial/order (62; anti-conservative)"],
        "manifest": public_manifest(manifest),
        "discrimination": {
            **{
                key: discrimination[key]
                for key in (
                    "ceiling", "opponents_per_topic", "judge_model", "summary", "paired"
                )
            },
            "pairs": public_pairs,
        },
        "response_curve_monotonic_nonincreasing": monotonic,
        "null_control_difference_points": null["delta_points_vs_baseline"],
        "human_human_endpoint": {
            "discrimination_rate": positive[-1]["discrimination_rate"],
            "distance_from_exchangeable_50_points": round(
                (positive[-1]["discrimination_rate"] - 0.5) * 100, 1
            ),
            "exchangeable_50_target_supported": False,
        },
        "comparisons": comparisons,
        "empirical_mde": mde,
        "largest_undetected_effect_points": {
            "pair_level": max(pair_undetected, default=0.0),
            "topic_cluster_level": max(cluster_undetected, default=0.0),
        },
        "power_planning_lower_bound": {
            "alpha": alpha,
            "power": 0.8,
            "null_adverse_trial_rate": round(adverse_rate, 6),
            "warning": (
                "Normal approximation only; ignores correlation among opponents/orders "
                "sharing one generated article, so actual required article count is larger."
            ),
            "estimates": power_plan,
        },
    }


def parse_levels(value: str) -> tuple[float, ...]:
    return tuple(float(part) for part in value.split(","))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare")
    prep.add_argument("--run", type=Path, required=True)
    prep.add_argument("--arm", required=True)
    prep.add_argument("--corpus", type=Path, required=True)
    prep.add_argument("--donor-corpus", type=Path, required=True)
    prep.add_argument("--opponents", type=int, default=4)
    prep.add_argument("--levels", type=parse_levels, default=DEFAULT_LEVELS)
    prep.add_argument("--seed", default="mde-positive-control-v1")
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--manifest-out", type=Path, required=True)

    ana = sub.add_parser("analyse")
    ana.add_argument("--discrimination", type=Path, required=True)
    ana.add_argument("--manifest", type=Path, required=True)
    ana.add_argument("--alpha", type=float, default=0.05)
    ana.add_argument("--date", default="")
    ana.add_argument("--branch", default="")
    ana.add_argument("--code-revision", default="")
    ana.add_argument("--json-out", type=Path, required=True)

    args = ap.parse_args()
    if args.command == "prepare":
        temp_record, manifest = prepare(
            args.run, args.arm, args.corpus, args.donor_corpus,
            args.opponents, args.levels, args.seed,
        )
        args.out.write_text(json.dumps(temp_record, ensure_ascii=False, indent=2))
        args.manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"prepared {len(temp_record['arms'])} arms")
        print(
            f"{manifest['expected_pairs_per_arm']} pairs x 2 orders = "
            f"{manifest['expected_trials_per_arm']} trials/arm"
        )
        for row in manifest["arms"]:
            print(
                f"  {row['arm']:<24} {row['replaced_articles']}/"
                f"{len(manifest['topics'])} articles "
                f"({row['actual_fraction'] * 100:.1f}%)"
            )
        print(f"preflight: {manifest['preflight']}")
        print(f"temporary copyrighted-body run: {args.out}")
        print(f"body-free manifest: {args.manifest_out}")
    else:
        result = analyse(
            json.loads(args.discrimination.read_text()),
            json.loads(args.manifest.read_text()),
            args.alpha,
        )
        result["execution"] = {
            "date": args.date,
            "branch": args.branch,
            "code_revision_before_changes": args.code_revision,
            "completed_judgments": sum(
                row["trials"] for row in result["discrimination"]["summary"].values()
            ),
            "private_input_fingerprints_omitted": True,
        }
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"monotonic: {result['response_curve_monotonic_nonincreasing']}")
        print(f"empirical MDE: {result['empirical_mde']}")
        print(f"wrote {args.json_out}")


if __name__ == "__main__":
    main()

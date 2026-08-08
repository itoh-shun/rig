#!/usr/bin/env python3
"""Blind pairwise Japanese-quality parity benchmark.

The benchmark compares a reference writer (for example GPT) with a candidate writer
(for example Claude) while keeping every judge independent from both writers.

Core invariants:
- the same prompt is sent to both writers;
- judges never receive provider/model identities;
- each case is judged in both A/B orders;
- a judge identity must differ from both writer identities;
- train/dev/holdout splits are explicit so rule evolution does not overfit holdout;
- checkpoints are bound to the exact benchmark/config/rules fingerprint.

No third-party Python packages are required. Providers are ordinary commands configured
in JSON, which keeps this harness vendor-neutral and lets it use authenticated local CLIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
DEFAULT_CASES = HERE / "parity_cases.json"
DEFAULT_CONFIG = HERE / "parity.providers.example.json"

JUDGE_PROMPT = """あなたは日本語文章品質の独立評価者です。
以下の回答Aと回答Bは、同じ依頼に対する2つの回答です。
執筆モデル名・提供元・改善版かどうかを推測せず、文章そのものだけを比較してください。

【依頼】
{prompt}

【回答A】
<<<A
{a}
A

【回答B】
<<<B
{b}
B

【評価基準】
1. 意味・事実・依頼への適合性
2. 日本語としての自然さ
3. 文脈に合う距離感・敬語
4. 必要十分な簡潔さ（説明不足も冗長さも減点）
5. 日本語らしい省略と情報順序
6. 定型的なAI文体・過剰な前置き・不自然なまとめの少なさ

差が実質的にない場合は draw を選んでください。
JSONオブジェクトだけを返してください。Markdown fenceは不要です。
{{
  "winner": "A" または "B" または "draw",
  "confidence": 0.0から1.0,
  "dimensions": {{
    "correctness": "A" または "B" または "draw",
    "naturalness": "A" または "B" または "draw",
    "context_fit": "A" または "B" または "draw",
    "conciseness": "A" または "B" または "draw",
    "tone": "A" または "B" または "draw"
  }},
  "reason": "日本語で簡潔に"
}}"""

CANDIDATE_RULE_WRAPPER = """以下の日本語品質ルールを、この依頼に答えるときだけ適用してください。
ルールを説明・引用せず、最終回答だけを書いてください。

--- 日本語品質ルール ---
{rules}
--- ルールここまで ---

【依頼】
{prompt}"""


@dataclass(frozen=True)
class ProviderSpec:
    role: str
    identity: str
    argv: tuple[str, ...]
    input_mode: str = "arg"
    output_mode: str = "stdout"
    timeout_sec: int = 300
    cwd_mode: str = "temp"
    env: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_dict(cls, role: str, data: dict[str, Any]) -> "ProviderSpec":
        argv = data.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError(f"{role}.argv must be a non-empty string array")
        identity = str(data.get("identity", "")).strip()
        if not identity:
            raise ValueError(f"{role}.identity is required")
        input_mode = str(data.get("input_mode", "arg"))
        output_mode = str(data.get("output_mode", "stdout"))
        cwd_mode = str(data.get("cwd_mode", "temp"))
        if input_mode not in {"arg", "stdin"}:
            raise ValueError(f"{role}.input_mode must be arg or stdin")
        if output_mode not in {"stdout", "file"}:
            raise ValueError(f"{role}.output_mode must be stdout or file")
        if cwd_mode not in {"temp", "here"}:
            raise ValueError(f"{role}.cwd_mode must be temp or here")
        if output_mode == "file" and not any("{output_file}" in x for x in argv):
            raise ValueError(f"{role}: output_mode=file requires {{output_file}} in argv")
        env_data = data.get("env", {})
        if not isinstance(env_data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env_data.items()
        ):
            raise ValueError(f"{role}.env must be a string map")
        return cls(
            role=role,
            identity=identity,
            argv=tuple(argv),
            input_mode=input_mode,
            output_mode=output_mode,
            timeout_sec=int(data.get("timeout_sec", 300)),
            cwd_mode=cwd_mode,
            env=tuple(sorted(env_data.items())),
        )

    def public_dict(self) -> dict[str, Any]:
        """Metadata safe to record. Environment values are intentionally omitted."""
        return {
            "role": self.role,
            "identity": self.identity,
            "argv": list(self.argv),
            "input_mode": self.input_mode,
            "output_mode": self.output_mode,
            "timeout_sec": self.timeout_sec,
            "cwd_mode": self.cwd_mode,
            "env_keys": [key for key, _ in self.env],
        }


def load_config(path: Path) -> tuple[ProviderSpec, ProviderSpec, list[ProviderSpec]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    reference = ProviderSpec.from_dict("reference", raw["reference"])
    candidate = ProviderSpec.from_dict("candidate", raw["candidate"])
    judges_raw = raw.get("judges")
    if not isinstance(judges_raw, list) or not judges_raw:
        raise ValueError("config.judges must contain at least one independent judge")
    judges = [ProviderSpec.from_dict(f"judge[{i}]", item) for i, item in enumerate(judges_raw)]
    validate_independence(reference, candidate, judges)
    return reference, candidate, judges


def validate_independence(
    reference: ProviderSpec, candidate: ProviderSpec, judges: list[ProviderSpec]
) -> None:
    writer_ids = {reference.identity.casefold(), candidate.identity.casefold()}
    if len(writer_ids) != 2:
        raise ValueError("reference and candidate identities must differ")
    seen: set[str] = set()
    for judge in judges:
        jid = judge.identity.casefold()
        if jid in writer_ids:
            raise ValueError(
                f"judge identity {judge.identity!r} matches a writer; writer and evaluator must differ"
            )
        if jid in seen:
            raise ValueError(f"duplicate judge identity: {judge.identity!r}")
        seen.add(jid)


def load_cases(path: Path, split: str) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases array")
    selected = []
    ids: set[str] = set()
    for case in cases:
        cid = str(case.get("id", "")).strip()
        prompt = str(case.get("prompt", "")).strip()
        case_split = str(case.get("split", "")).strip()
        category = str(case.get("category", "")).strip()
        if not cid or not prompt or not case_split or not category:
            raise ValueError("every case needs id, prompt, split and category")
        if cid in ids:
            raise ValueError(f"duplicate case id: {cid}")
        ids.add(cid)
        if split == "all" or case_split == split:
            selected.append({"id": cid, "prompt": prompt, "split": case_split, "category": category})
    if not selected:
        raise ValueError(f"no cases selected for split={split!r}")
    return selected


def _expand_argv(argv: tuple[str, ...], output_file: Path | None) -> list[str]:
    output_value = str(output_file) if output_file else ""
    return [part.replace("{output_file}", output_value) for part in argv]


def run_provider(spec: ProviderSpec, prompt: str, attempts: int = 3) -> str:
    """Run one provider command without a shell.

    `arg` appends the prompt as one argv item. `stdin` sends it verbatim on stdin.
    `output_mode=file` reads the file referenced by {output_file}; useful for CLIs that
    mix progress messages with their final answer on stdout.
    """
    last_error = ""
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** attempt)
        with tempfile.TemporaryDirectory(prefix="rig-jp-parity-") as temp_dir:
            output_file = Path(temp_dir) / "final.txt" if spec.output_mode == "file" else None
            argv = _expand_argv(spec.argv, output_file)
            stdin_text = None
            if spec.input_mode == "arg":
                argv.append(prompt)
            else:
                stdin_text = prompt
            cwd = Path(temp_dir) if spec.cwd_mode == "temp" else HERE
            env = os.environ.copy()
            env.update(dict(spec.env))
            try:
                proc = subprocess.run(
                    argv,
                    input=stdin_text,
                    capture_output=True,
                    text=True,
                    timeout=spec.timeout_sec,
                    cwd=cwd,
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                continue
            if proc.returncode != 0:
                last_error = (
                    f"exit {proc.returncode}; stderr={proc.stderr.strip()[:300] or '<empty>'}"
                )
                continue
            if spec.output_mode == "file":
                text = output_file.read_text(encoding="utf-8") if output_file and output_file.exists() else ""
            else:
                text = proc.stdout
            if text.strip():
                return text.strip()
            last_error = "provider returned empty output"
    raise RuntimeError(f"{spec.role}/{spec.identity} failed after {attempts} attempts: {last_error}")


def extract_json(raw: str) -> dict[str, Any]:
    stripped = raw.strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE).strip()
    candidates = [stripped]
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"no JSON object found in judge response: {raw[:300]!r}")


def normalize_pick(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if raw in {"A", "B"}:
        return raw
    if raw.casefold() in {"draw", "tie", "same", "equal", "引き分け", "同等"}:
        return "draw"
    raise ValueError(f"invalid judge winner: {value!r}")


def parse_judgment(raw: str) -> dict[str, Any]:
    data = extract_json(raw)
    winner = normalize_pick(data.get("winner"))
    confidence = data.get("confidence", 0.5)
    try:
        confidence_f = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_f = 0.5
    dimensions = data.get("dimensions", {})
    if not isinstance(dimensions, dict):
        dimensions = {}
    clean_dimensions: dict[str, str] = {}
    for key in ("correctness", "naturalness", "context_fit", "conciseness", "tone"):
        try:
            clean_dimensions[key] = normalize_pick(dimensions.get(key, "draw"))
        except ValueError:
            clean_dimensions[key] = "draw"
    return {
        "winner": winner,
        "confidence": confidence_f,
        "dimensions": clean_dimensions,
        "reason": str(data.get("reason", "")).strip(),
    }


def candidate_prompt(prompt: str, rules: str) -> str:
    if not rules.strip():
        return prompt
    return CANDIDATE_RULE_WRAPPER.format(rules=rules.strip(), prompt=prompt)


def judgment_prompt(prompt: str, reference: str, candidate: str, order: str) -> tuple[str, dict[str, str]]:
    if order == "reference_first":
        a, b = reference, candidate
        mapping = {"A": "reference", "B": "candidate", "draw": "draw"}
    elif order == "candidate_first":
        a, b = candidate, reference
        mapping = {"A": "candidate", "B": "reference", "draw": "draw"}
    else:
        raise ValueError(f"unknown order: {order}")
    return JUDGE_PROMPT.format(prompt=prompt, a=a, b=b), mapping


def normalized_winner(pick: str, mapping: dict[str, str]) -> str:
    return mapping[pick]


def fingerprint(
    cases_path: Path,
    config_path: Path,
    split: str,
    rules: str,
) -> str:
    payload = {
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "split": split,
        "rules_sha256": hashlib.sha256(rules.encode()).hexdigest(),
        "schema": 1,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_checkpoint(path: Path | None, expected_fingerprint: str) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"fingerprint": expected_fingerprint, "generations": {}, "judgments": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("fingerprint") != expected_fingerprint:
        raise ValueError("checkpoint belongs to a different benchmark/config/rules set")
    data.setdefault("generations", {})
    data.setdefault("judgments", {})
    return data


def save_checkpoint(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _generation_key(case_id: str, role: str) -> str:
    return f"{case_id}::{role}"


def generate_all(
    cases: list[dict[str, Any]],
    reference: ProviderSpec,
    candidate: ProviderSpec,
    rules: str,
    state: dict[str, Any],
    checkpoint: Path | None,
    parallel: int,
) -> None:
    jobs: list[tuple[dict[str, Any], str, ProviderSpec, str]] = []
    for case in cases:
        for role, spec, prompt in (
            ("reference", reference, case["prompt"]),
            ("candidate", candidate, candidate_prompt(case["prompt"], rules)),
        ):
            key = _generation_key(case["id"], role)
            if key not in state["generations"]:
                jobs.append((case, role, spec, prompt))
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(run_provider, spec, prompt): (case, role, spec)
            for case, role, spec, prompt in jobs
        }
        for future in as_completed(futures):
            case, role, spec = futures[future]
            key = _generation_key(case["id"], role)
            state["generations"][key] = {
                "identity": spec.identity,
                "text": future.result(),
            }
            save_checkpoint(checkpoint, state)
            print(f"generated {len(state['generations'])}/{len(cases) * 2}: {case['id']} {role}")


def _judgment_key(case_id: str, judge_identity: str, order: str) -> str:
    return f"{case_id}::{judge_identity}::{order}"


def judge_all(
    cases: list[dict[str, Any]],
    judges: list[ProviderSpec],
    state: dict[str, Any],
    checkpoint: Path | None,
    parallel: int,
) -> None:
    jobs: list[tuple[dict[str, Any], ProviderSpec, str, str, dict[str, str]]] = []
    for case in cases:
        reference = state["generations"][_generation_key(case["id"], "reference")]["text"]
        candidate = state["generations"][_generation_key(case["id"], "candidate")]["text"]
        for judge in judges:
            for order in ("reference_first", "candidate_first"):
                key = _judgment_key(case["id"], judge.identity, order)
                if key in state["judgments"]:
                    continue
                prompt, mapping = judgment_prompt(case["prompt"], reference, candidate, order)
                jobs.append((case, judge, order, prompt, mapping))
    if not jobs:
        return
    total = len(cases) * len(judges) * 2
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(run_provider, judge, prompt): (case, judge, order, mapping)
            for case, judge, order, prompt, mapping in jobs
        }
        for future in as_completed(futures):
            case, judge, order, mapping = futures[future]
            parsed = parse_judgment(future.result())
            parsed["normalized_winner"] = normalized_winner(parsed["winner"], mapping)
            parsed["order"] = order
            parsed["judge_identity"] = judge.identity
            key = _judgment_key(case["id"], judge.identity, order)
            state["judgments"][key] = parsed
            save_checkpoint(checkpoint, state)
            print(f"judged {len(state['judgments'])}/{total}: {case['id']} {judge.identity} {order}")


def _candidate_points(winner: str) -> float:
    if winner == "candidate":
        return 1.0
    if winner == "draw":
        return 0.5
    return 0.0


def _mean_ci95(values: list[float]) -> tuple[float, float, float]:
    if not values:
        return float("nan"), float("nan"), float("nan")
    mean = statistics.mean(values)
    if len(values) < 2:
        return mean, mean, mean
    se = statistics.stdev(values) / math.sqrt(len(values))
    margin = 1.96 * se
    return mean, max(0.0, mean - margin), min(1.0, mean + margin)


def build_report(
    cases: list[dict[str, Any]],
    reference: ProviderSpec,
    candidate: ProviderSpec,
    judges: list[ProviderSpec],
    state: dict[str, Any],
    split: str,
    rules_sha256: str,
) -> dict[str, Any]:
    case_rows = []
    dimension_totals: dict[str, list[float]] = {}
    order_consistent = 0
    order_pairs = 0

    for case in cases:
        per_judge = []
        case_points: list[float] = []
        for judge in judges:
            verdicts = [
                state["judgments"][_judgment_key(case["id"], judge.identity, order)]
                for order in ("reference_first", "candidate_first")
            ]
            normalized = [v["normalized_winner"] for v in verdicts]
            points = [_candidate_points(v) for v in normalized]
            case_points.extend(points)
            order_pairs += 1
            if normalized[0] == normalized[1]:
                order_consistent += 1
            for verdict in verdicts:
                mapping = (
                    {"A": "reference", "B": "candidate", "draw": "draw"}
                    if verdict["order"] == "reference_first"
                    else {"A": "candidate", "B": "reference", "draw": "draw"}
                )
                for dim, pick in verdict.get("dimensions", {}).items():
                    dimension_totals.setdefault(dim, []).append(
                        _candidate_points(normalized_winner(pick, mapping))
                    )
            per_judge.append({
                "judge": judge.identity,
                "candidate_score": round(statistics.mean(points), 3),
                "verdicts": normalized,
                "reasons": [v.get("reason", "") for v in verdicts],
            })
        case_rows.append({
            "id": case["id"],
            "split": case["split"],
            "category": case["category"],
            "candidate_score": round(statistics.mean(case_points), 3),
            "judges": per_judge,
        })

    case_scores = [row["candidate_score"] for row in case_rows]
    overall, low, high = _mean_ci95(case_scores)
    categories: dict[str, dict[str, Any]] = {}
    for category in sorted({case["category"] for case in cases}):
        values = [row["candidate_score"] for row in case_rows if row["category"] == category]
        mean, c_low, c_high = _mean_ci95(values)
        categories[category] = {
            "cases": len(values),
            "candidate_preference": round(mean, 3),
            "ci95": [round(c_low, 3), round(c_high, 3)],
        }

    dimensions = {
        key: round(statistics.mean(values), 3)
        for key, values in sorted(dimension_totals.items()) if values
    }
    return {
        "schema_version": 1,
        "metric": "candidate_preference; 0.5 means head-to-head parity",
        "split": split,
        "reference": reference.public_dict(),
        "candidate": candidate.public_dict(),
        "judges": [judge.public_dict() for judge in judges],
        "candidate_rules_sha256": rules_sha256,
        "cases": len(cases),
        "judgments": len(cases) * len(judges) * 2,
        "candidate_preference": round(overall, 3),
        "ci95_by_case": [round(low, 3), round(high, 3)],
        "gap_from_parity": round(overall - 0.5, 3),
        "order_consistency": round(order_consistent / order_pairs, 3) if order_pairs else None,
        "categories": categories,
        "dimensions": dimensions,
        "case_results": case_rows,
    }


def print_report(report: dict[str, Any]) -> None:
    score = report["candidate_preference"]
    low, high = report["ci95_by_case"]
    print("\nJapanese parity report")
    print("=" * 56)
    print(f"reference : {report['reference']['identity']}")
    print(f"candidate : {report['candidate']['identity']}")
    print("judges    : " + ", ".join(j["identity"] for j in report["judges"]))
    print(f"split     : {report['split']} ({report['cases']} cases, {report['judgments']} judgments)")
    print(f"candidate preference: {score * 100:5.1f}%  (95% CI by case {low * 100:.1f}-{high * 100:.1f}%)")
    print("                       50.0% = GPT/Claude head-to-head parity")
    print(f"order consistency    : {report['order_consistency'] * 100:5.1f}%")
    print("\ncategory")
    for name, row in report["categories"].items():
        print(f"  {name:<22} {row['candidate_preference'] * 100:5.1f}%  n={row['cases']}")
    print("\ndimensions")
    for name, value in report["dimensions"].items():
        print(f"  {name:<22} {value * 100:5.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--split", choices=("train", "dev", "holdout", "all"), default="dev")
    ap.add_argument("--candidate-rules", type=Path,
                    help="rules injected only into the candidate writer")
    ap.add_argument("--checkpoint", type=Path,
                    help="resume-safe checkpoint containing generated text and judgments")
    ap.add_argument("--json-out", type=Path)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate benchmark/config/independence without calling models")
    args = ap.parse_args()

    reference, candidate, judges = load_config(args.config)
    cases = load_cases(args.cases, args.split)
    rules = args.candidate_rules.read_text(encoding="utf-8") if args.candidate_rules else ""
    fp = fingerprint(args.cases, args.config, args.split, rules)

    print(f"validated: {reference.identity} vs {candidate.identity}; "
          f"independent judge(s): {', '.join(j.identity for j in judges)}")
    print(f"cases: {len(cases)} split={args.split} fingerprint={fp[:12]}")
    if args.dry_run:
        return

    state = load_checkpoint(args.checkpoint, fp)
    generate_all(cases, reference, candidate, rules, state, args.checkpoint, args.parallel)
    judge_all(cases, judges, state, args.checkpoint, args.parallel)
    report = build_report(
        cases, reference, candidate, judges, state, args.split,
        hashlib.sha256(rules.encode()).hexdigest(),
    )
    print_report(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()

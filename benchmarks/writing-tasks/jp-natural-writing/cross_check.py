#!/usr/bin/env python3
"""Score saved benchmark arms with an independent, corpus-calibrated detector.

Our own metric is one Opus 5 process judging surface style. coji/natural-japanese
reports that surface style can be polished until blind human judges prefer it while a
deep signal still marks it as AI, so a win on our metric is not self-validating.
lint.py is deterministic, morphological, and calibrated against 103 human / 81 AI
documents, so it is an independent second opinion on the same texts.
"""

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

LINT = Path("nj/skills/natural-japanese/scripts/lint.py")
SCRATCH = Path(__file__).parent


def lint_text(text: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        proc = subprocess.run(
            ["uv", "run", str(SCRATCH / LINT), path, "--json"],
            capture_output=True, text=True, cwd=SCRATCH, timeout=300,
        )
        if proc.returncode != 0:
            return {"error": proc.stderr.strip()[:200]}
        return json.loads(proc.stdout)
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> None:
    for record_path in sys.argv[1:]:
        record = json.loads(Path(record_path).read_text())
        print(f"\n{'=' * 70}\n{Path(record_path).name}  (mode={record.get('mode')})\n{'=' * 70}")

        for arm_name, arm in record["arms"].items():
            severities = Counter()
            categories = Counter()
            judge_scores = []
            per_sample = []

            for sample in arm["samples"]:
                text = f"{sample['title']}\n\n{sample['description']}"
                out = lint_text(text)
                if "error" in out:
                    print(f"  lint error on {sample['topic']}: {out['error']}")
                    continue
                findings = out.get("findings", [])
                for f in findings:
                    severities[f.get("severity", "?")] += 1
                    categories[f.get("category", f.get("id", "?"))] += 1
                judge_scores.append(sample["score"])
                per_sample.append((sample["topic"], sample["score"], len(findings)))

            n = len(per_sample) or 1
            print(f"\n[{arm_name}]  judge平均 {sum(judge_scores)/n:.1f}  "
                  f"lint findings計 {sum(severities.values())} ({sum(severities.values())/n:.2f}/文書)")
            if severities:
                print(f"  severity: {dict(severities)}")
                print(f"  top: {dict(categories.most_common(6))}")
            for topic, score, nf in per_sample:
                print(f"    {topic:<22} judge {score:>5.1f}   lint {nf}")


if __name__ == "__main__":
    main()

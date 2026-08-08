#!/usr/bin/env python3

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import parity


class ParityTest(unittest.TestCase):
    def spec(self, role: str, identity: str) -> parity.ProviderSpec:
        return parity.ProviderSpec(role=role, identity=identity, argv=(sys.executable, "-c", "print('ok')"))

    def test_independence_rejects_writer_as_judge(self):
        ref = self.spec("reference", "gpt")
        cand = self.spec("candidate", "claude")
        with self.assertRaisesRegex(ValueError, "writer and evaluator must differ"):
            parity.validate_independence(ref, cand, [self.spec("judge", "claude")])

    def test_independence_accepts_third_identity(self):
        parity.validate_independence(
            self.spec("reference", "gpt"),
            self.spec("candidate", "claude-sonnet"),
            [self.spec("judge", "claude-opus")],
        )

    def test_parse_judgment_and_normalize_order(self):
        raw = json.dumps({
            "winner": "B",
            "confidence": 0.9,
            "dimensions": {"naturalness": "B"},
            "reason": "Bの方が自然",
        }, ensure_ascii=False)
        parsed = parity.parse_judgment(raw)
        self.assertEqual(parsed["winner"], "B")
        prompt, mapping = parity.judgment_prompt("p", "ref", "cand", "reference_first")
        self.assertIn("ref", prompt)
        self.assertEqual(parity.normalized_winner(parsed["winner"], mapping), "candidate")

    def test_candidate_rules_only_wrap_candidate(self):
        prompt = "元の依頼"
        self.assertEqual(parity.candidate_prompt(prompt, ""), prompt)
        wrapped = parity.candidate_prompt(prompt, "短く書く")
        self.assertIn("短く書く", wrapped)
        self.assertIn(prompt, wrapped)

    def test_load_cases_split(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cases.json"
            path.write_text(json.dumps({"cases": [
                {"id": "a", "split": "train", "category": "x", "prompt": "one"},
                {"id": "b", "split": "dev", "category": "x", "prompt": "two"},
            ]}), encoding="utf-8")
            self.assertEqual([c["id"] for c in parity.load_cases(path, "dev")], ["b"])
            self.assertEqual(len(parity.load_cases(path, "all")), 2)

    def test_report_scores_by_case_not_order_as_independent_samples(self):
        ref = self.spec("reference", "gpt")
        cand = self.spec("candidate", "claude")
        judge = self.spec("judge", "gemini")
        cases = [
            {"id": "c1", "split": "dev", "category": "chat", "prompt": "p1"},
            {"id": "c2", "split": "dev", "category": "chat", "prompt": "p2"},
        ]
        state = {"generations": {}, "judgments": {}}
        for case in cases:
            state["generations"][f"{case['id']}::reference"] = {"identity": "gpt", "text": "r"}
            state["generations"][f"{case['id']}::candidate"] = {"identity": "claude", "text": "c"}
        # c1 candidate wins both orders => 1.0; c2 loses both => 0.0; overall parity 0.5.
        for cid, winner in (("c1", "candidate"), ("c2", "reference")):
            for order in ("reference_first", "candidate_first"):
                state["judgments"][f"{cid}::gemini::{order}"] = {
                    "normalized_winner": winner,
                    "order": order,
                    "dimensions": {k: "draw" for k in (
                        "correctness", "naturalness", "context_fit", "conciseness", "tone"
                    )},
                    "reason": "",
                }
        report = parity.build_report(cases, ref, cand, [judge], state, "dev", "0" * 64)
        self.assertEqual(report["candidate_preference"], 0.5)
        self.assertEqual(report["order_consistency"], 1.0)
        self.assertEqual(report["cases"], 2)
        self.assertEqual(report["judgments"], 4)

    def test_run_provider_uses_argv_without_shell(self):
        spec = parity.ProviderSpec(
            role="fake",
            identity="fake-model",
            argv=(sys.executable, "-c", "import sys; print(sys.argv[-1])"),
            input_mode="arg",
            output_mode="stdout",
        )
        self.assertEqual(parity.run_provider(spec, "日本語 prompt", attempts=1), "日本語 prompt")


if __name__ == "__main__":
    unittest.main()

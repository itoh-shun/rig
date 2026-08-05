import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from discriminate import jobs_fingerprint, load_pairs, run_jobs, sample_text
from fetch_human_corpus import main as fetch_corpus_main
from mde_calibration import analyse, approximate_matched_trials, prepare


class MdeCalibrationTest(unittest.TestCase):
    def make_corpus(self, root: Path, name: str, topics: list[str], per_topic: int) -> Path:
        corpus = root / name
        corpus.mkdir()
        index = []
        for topic in topics:
            for number in range(per_topic):
                filename = f"{topic}-{name}-{number}.md"
                body = f"{topic} human {name} {number}。"
                (corpus / filename).write_text(body)
                index.append({"topic": topic, "file": filename, "chars": len(body)})
        (corpus / "index.json").write_text(json.dumps(index))
        return corpus

    def test_full_text_overrides_title_description(self):
        self.assertEqual(sample_text({
            "title": "ignored", "description": "ignored", "full_text": "exact body"
        }), "exact body")

    def test_prepare_builds_nested_body_free_positive_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topics = ["A", "B"]
            source = root / "source.json"
            source.write_text(json.dumps({
                "ledger": {"pin": {"sha1": "ledger-sha"}},
                "arms": {
                    "writer": {
                        "label": "writer",
                        "stats": {},
                        "samples": [
                            {"topic": topic, "title": f"{topic} title", "description": "generated。"}
                            for topic in topics
                        ],
                    }
                },
            }))
            corpus = self.make_corpus(root, "opponents", topics, 2)
            donors = self.make_corpus(root, "donors", topics, 2)
            temp_record, manifest = prepare(
                source, "writer", corpus, donors, 1, (0.0, 0.5, 1.0), "seed"
            )

            self.assertTrue(manifest["preflight"]["null_byte_identical"])
            self.assertTrue(manifest["preflight"]["endpoint_all_human_donors"])
            self.assertTrue(manifest["preflight"]["no_copyrighted_bodies_in_manifest"])
            self.assertEqual(manifest["expected_pairs_per_arm"], 2)
            self.assertNotIn("full_text", json.dumps(manifest))
            self.assertNotIn("human opponents", json.dumps(manifest))
            base = temp_record["arms"]["writer"]["samples"]
            null = temp_record["arms"]["writer_null"]["samples"]
            self.assertEqual(
                [sample_text(sample) for sample in base],
                [sample_text(sample) for sample in null],
            )
            half = temp_record["arms"]["pc_human_0500"]["samples"]
            endpoint = temp_record["arms"]["pc_human_1000"]["samples"]
            self.assertEqual(sum("full_text" in sample for sample in half), 1)
            self.assertEqual(sum("full_text" in sample for sample in endpoint), 2)

    def test_load_pairs_preserves_exact_full_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = self.make_corpus(root, "corpus", ["A"], 1)
            run = root / "run.json"
            run.write_text(json.dumps({
                "arms": {
                    "control": {
                        "samples": [{
                            "topic": "A", "title": "ignored",
                            "description": "ignored", "full_text": "exact candidate",
                        }]
                    }
                }
            }))
            pairs = load_pairs([run], corpus, None, 2500, 1)
            self.assertEqual(pairs[0]["generated"], "exact candidate")

    def test_discrimination_checkpoint_resumes_only_missing_jobs_and_has_no_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint = Path(tmp) / "checkpoint.json"
            pairs = [
                {
                    "arm": "arm", "pair_id": f"T::{number}", "topic": "T",
                    "generated": f"generated secret body {number}",
                    "human": f"human copyrighted body {number}",
                }
                for number in range(3)
            ]
            jobs = [(pair, "A") for pair in pairs]
            first_calls = []

            def flaky(pair, human_pos, model):
                first_calls.append(pair["pair_id"])
                if pair["pair_id"] == "T::1":
                    raise RuntimeError("fixture failure")
                return {"human_pos": human_pos, "pick": "A", "correct": True, "reasoning": ""}

            with patch("discriminate.judge_pair", side_effect=flaky):
                with self.assertRaises(RuntimeError):
                    run_jobs(jobs, "fixture", 1, checkpoint)
            saved = checkpoint.read_text()
            self.assertNotIn("generated secret body", saved)
            self.assertNotIn("human copyrighted body", saved)
            self.assertEqual(json.loads(saved)["completed"], 2)

            resumed_calls = []

            def healthy(pair, human_pos, model):
                resumed_calls.append(pair["pair_id"])
                return {"human_pos": human_pos, "pick": "A", "correct": True, "reasoning": ""}

            with patch("discriminate.judge_pair", side_effect=healthy):
                verdicts = run_jobs(jobs, "fixture", 1, checkpoint)
            self.assertEqual(resumed_calls, ["T::1"])
            self.assertEqual(len(verdicts), 3)

    def test_checkpoint_fingerprint_changes_with_text(self):
        pair = {
            "arm": "a", "pair_id": "T::h", "generated": "one", "human": "human"
        }
        before = jobs_fingerprint([(pair, "A")], "model")
        pair["generated"] = "two"
        after = jobs_fingerprint([(pair, "A")], "model")
        self.assertNotEqual(before, after)

    def test_analyse_uses_topic_clusters_as_primary_unit(self):
        manifest = {
            "source_arm": "base",
            "topics": ["A", "B"],
            "expected_pairs_per_arm": 2,
            "expected_trials_per_arm": 4,
            "arms": [
                {"arm": "base", "kind": "baseline", "actual_fraction": 0.0},
                {"arm": "base_null", "kind": "byte-identical-null", "actual_fraction": 0.0},
                {"arm": "pc", "kind": "held-out-human-mixture", "actual_fraction": 1.0},
            ],
            "opponent_index_sha1": "private-index-fingerprint",
            "donor_index_sha1": "private-donor-index-fingerprint",
            "donors": {
                "A": {"file": "qiita-item-id.md", "sha1": "private-body-hash", "chars": 123},
            },
        }

        def pair(arm, topic, correct_a, correct_b):
            return {
                "arm": arm, "topic": topic, "pair_id": f"{topic}::human.md",
                "trials": [
                    {"human_pos": "A", "correct": correct_a},
                    {"human_pos": "B", "correct": correct_b},
                ],
            }

        discrimination = {
            "ceiling": 2500,
            "opponents_per_topic": 1,
            "judge_model": "fixture",
            "summary": {
                "base": {"correct": 4, "trials": 4, "rate": 1.0},
                "base_null": {"correct": 4, "trials": 4, "rate": 1.0},
                "pc": {"correct": 0, "trials": 4, "rate": 0.0},
            },
            "paired": {},
            "pairs": [
                pair("base", "A", True, True),
                pair("base", "B", True, True),
                pair("base_null", "A", True, True),
                pair("base_null", "B", True, True),
                pair("pc", "A", False, False),
                pair("pc", "B", False, False),
            ],
        }
        result = analyse(discrimination, manifest)
        positive = result["comparisons"][1]
        self.assertEqual(positive["delta_points_vs_baseline"], -100.0)
        self.assertEqual(positive["gained_trials"], 4)
        self.assertEqual(positive["gained_pairs"], 2)
        self.assertEqual(positive["topic_cluster_signflip_p"], 0.5)
        self.assertIsNone(result["empirical_mde"]["topic_cluster_level"])
        published = json.dumps(result)
        self.assertNotIn("qiita-item-id", published)
        self.assertNotIn("private-body-hash", published)
        self.assertNotIn("private-index-fingerprint", published)
        self.assertNotIn("human.md", published)
        self.assertEqual(result["manifest"]["donor_article_chars"], {"A": 123})

    def test_sample_size_lower_bound_grows_for_smaller_effects(self):
        large = approximate_matched_trials(10.0, 1 / 62)
        small = approximate_matched_trials(3.2, 1 / 62)
        self.assertGreater(small, large)

    def test_fetch_zero_articles_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["fetch_human_corpus.py", "--out", tmp]
            with (
                patch.object(sys, "argv", argv),
                patch("fetch_human_corpus.fetch", return_value=[]),
                patch("fetch_human_corpus.time.sleep"),
                self.assertRaisesRegex(SystemExit, "zero usable articles"),
            ):
                fetch_corpus_main()
            self.assertEqual(json.loads((Path(tmp) / "index.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()

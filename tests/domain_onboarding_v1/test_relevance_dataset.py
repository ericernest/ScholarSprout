from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.domain_onboarding.relevance import (
    load_relevance_annotations,
    ndcg_at_k,
    precision_at_k,
    summarize_relevance_annotations,
)


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixtures"
    / "domain_onboarding"
    / "v1"
    / "paper-relevance.jsonl"
)


class RelevanceDatasetTests(unittest.TestCase):
    def test_fixture_covers_six_domains_with_positive_and_negative_examples(self) -> None:
        cases = load_relevance_annotations(FIXTURE)
        summary = summarize_relevance_annotations(cases)

        self.assertEqual(summary.case_count, 6)
        self.assertEqual(summary.domain_count, 6)
        self.assertEqual(summary.paper_count, 36)
        self.assertEqual(summary.annotation_status_counts, {"seed": 6})
        self.assertTrue(all(any(p.relevance_grade == 0 for p in case.papers) for case in cases))
        self.assertTrue(all(any(p.relevance_grade >= 2 for p in case.papers) for case in cases))

    def test_ranking_metrics_use_graded_labels(self) -> None:
        grades = {"core": 3, "useful": 2, "near": 1, "wrong": 0}
        perfect = ["core", "useful", "near", "wrong"]
        reversed_order = list(reversed(perfect))

        self.assertEqual(precision_at_k(perfect, grades, 2), 1.0)
        self.assertEqual(precision_at_k(reversed_order, grades, 2), 0.0)
        self.assertEqual(ndcg_at_k(perfect, grades, 4), 1.0)
        self.assertLess(ndcg_at_k(reversed_order, grades, 4), 1.0)

    def test_duplicate_case_ids_are_rejected(self) -> None:
        line = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.jsonl"
            duplicate.write_text(f"{line}\n{line}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate relevance case_id"):
                load_relevance_annotations(duplicate)


if __name__ == "__main__":
    unittest.main()

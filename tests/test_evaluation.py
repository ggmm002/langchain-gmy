from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

EVAL_DATASET_PATH = Path(__file__).parent / "eval_dataset.json"


class IntentClassificationEvaluation(unittest.TestCase):
    """P1: 基于标注数据集的分类准确率回归测试。"""

    @classmethod
    def setUpClass(cls) -> None:
        settings = Settings(enable_llm_classifier=False)
        cls.client = TestClient(create_app(settings))
        with EVAL_DATASET_PATH.open("r", encoding="utf-8") as f:
            cls.dataset = json.load(f)

    def test_classification_accuracy(self) -> None:
        correct = 0
        total = len(self.dataset)
        failures: list[str] = []

        for case in self.dataset:
            response = self.client.post(
                "/api/query",
                json={
                    "query": case["query"],
                    "allow_action_execution": False,
                    "dry_run": True,
                },
            )
            self.assertEqual(response.status_code, 200)
            result = response.json()
            actual_intent = result["classification"]["primary_intent"]

            if actual_intent == case["expected_intent"]:
                correct += 1
            else:
                failures.append(
                    f"  [{case['description']}] "
                    f"查询='{case['query']}' "
                    f"期望={case['expected_intent']} "
                    f"实际={actual_intent}"
                )

        accuracy = correct / total if total > 0 else 0.0
        report = (
            f"\n{'=' * 60}\n"
            f"意图分类评估报告\n"
            f"{'=' * 60}\n"
            f"总用例: {total}\n"
            f"正确: {correct}\n"
            f"准确率: {accuracy:.1%}\n"
        )
        if failures:
            report += f"失败用例:\n" + "\n".join(failures) + "\n"
        report += f"{'=' * 60}"

        print(report)
        self.assertGreaterEqual(
            accuracy,
            0.80,
            f"分类准确率 {accuracy:.1%} 低于 80% 阈值。\n{report}",
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class IntentRouterApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        settings = Settings(enable_llm_classifier=False)
        cls.client = TestClient(create_app(settings))

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertFalse(payload["llm_enabled"])

    def test_knowledge_rag_route(self) -> None:
        response = self.client.post(
            "/api/query",
            json={
                "query": "请从知识库检索供应异常升级SOP，并告诉我升级条件。",
                "allow_action_execution": False,
                "dry_run": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["classification"]["primary_intent"], "knowledge_rag")
        self.assertEqual(payload["subsystem_result"]["target"], "knowledge_rag")

    def test_supply_analytics_route(self) -> None:
        response = self.client.post(
            "/api/query",
            json={
                "query": "分析华东智造近三个月动力电池模组供应量波动并生成报告。",
                "allow_action_execution": False,
                "dry_run": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["classification"]["primary_intent"], "supply_analytics")
        self.assertEqual(payload["subsystem_result"]["target"], "supply_analytics")
        self.assertGreater(payload["classification"]["confidence"], 0.5)

    def test_workflow_route(self) -> None:
        response = self.client.post(
            "/api/query",
            json={
                "query": "触发供应异常预警流程并通知采购负责人。",
                "allow_action_execution": False,
                "dry_run": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["classification"]["primary_intent"], "workflow_automation")
        self.assertTrue(payload["classification"]["requires_confirmation"])
        self.assertEqual(payload["subsystem_result"]["target"], "workflow_automation")


if __name__ == "__main__":
    unittest.main()

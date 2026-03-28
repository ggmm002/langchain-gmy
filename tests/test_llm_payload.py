from __future__ import annotations

import unittest

from app.intent_engine import LLMIntentPayload
from app.models import IntentType, RouteTarget


class LLMIntentPayloadTests(unittest.TestCase):
    def test_payload_accepts_missing_optional_fields(self) -> None:
        payload = LLMIntentPayload.model_validate(
            {
                "primary_intent": "clarification",
                "confidence": 0.41,
            }
        )
        self.assertEqual(payload.primary_intent, IntentType.CLARIFICATION)
        self.assertEqual(payload.route_target, RouteTarget.CLARIFICATION)
        self.assertEqual(payload.normalized_query, "")
        self.assertEqual(payload.rationale, "")


if __name__ == "__main__":
    unittest.main()

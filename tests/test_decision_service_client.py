import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from decision_service_client import DecisionService, DecisionServiceError


class OutcomePublicationTests(unittest.TestCase):
    def test_opt_in_leaves_applications_direct_and_waits_for_a_result_receipt(self):
        application = {"kind": "application", "event_id": "a", "revision": 1}
        event = {"kind": "result", "event_id": "r", "revision": 2, "payload": {"quality_status": "passed"}}
        client = DecisionService("https://decision.test/decision-service", "scope-token",
                                 outcome_publisher=("/trusted/bin/python", "/private/publisher.json"))
        direct = {"event_id": "a", "revision": 1, "received": True, "receipt_id": "direct"}
        with patch.object(client, "request", return_value=direct) as request, patch(
            "decision_service_client.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, '{"state":"submitted"}', ""),
        ) as publish:
            self.assertEqual(client.feedback(application), direct)
            request.assert_called_once_with("/v1/decision-feedback", application)
            with self.assertRaisesRegex(DecisionServiceError, "outcome_submitted"):
                client.feedback(event)
            self.assertEqual(json.loads(publish.call_args.kwargs["input"]), event)
            self.assertEqual(publish.call_args.kwargs["env"]["DECISION_SERVICE_TOKEN"], "scope-token")
            receipt = {"state": "recorded", "event_id": "r", "revision": 2,
                       "received": True, "receipt_id": "webhook-receipt"}
            publish.return_value = subprocess.CompletedProcess([], 0, json.dumps(receipt), "")
            self.assertEqual(client.feedback(event), receipt)
            request.assert_called_once()

    def test_default_keeps_original_result_route(self):
        result = {"kind": "result", "event_id": "r", "revision": 1}
        receipt = {"event_id": "r", "revision": 1, "received": True, "receipt_id": "direct"}
        client = DecisionService("https://decision.test", "old-scope-token")
        with patch.object(client, "request", return_value=receipt) as request:
            self.assertEqual(client.feedback(result), receipt)
            request.assert_called_once_with("/v1/decision-feedback", result)

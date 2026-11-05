import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "mcp/hindsight-memory/api_relay.py"
spec = importlib.util.spec_from_file_location("hindsight_api_relay_tested", SOURCE)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)

BANK = "cf-fe-synthetic"
CID = "a472ae10-86bc-47bd-8a9a-ce86f68cf187"
DOC = "source-revision-1"
TOKEN = "synthetic-service-token"


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.behavior = {"status": 200, "body": {"status": "healthy"}}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append({
                    "method": self.command, "path": self.path,
                    "body": json.loads(raw) if raw else None,
                    "authorization": self.headers.get("Authorization"),
                })
                if owner.behavior.get("drop"):
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if owner.behavior.get("slow"):
                    threading.Event().wait(0.3)
                body = owner.behavior.get("raw")
                if body is None:
                    body = json.dumps(owner.behavior["body"]).encode()
                try:
                    self.send_response(owner.behavior["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    for key, value in owner.behavior.get("headers", {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                    if owner.behavior.get("drip"):
                        for offset in range(0, len(body), 5):
                            self.wfile.write(body[offset:offset + 5])
                            self.wfile.flush()
                            threading.Event().wait(0.005)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        (self.root / "config").mkdir()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp/"
        (self.root / "config/mcp-client.env").write_text(
            f"HINDSIGHT_WIN_LOCAL_MCP_URL={self.url}\n", encoding="utf-8"
        )
        (self.root / "config/shared.env").write_text(
            f"HINDSIGHT_API_TENANT_API_KEY={TOKEN}\n", encoding="utf-8"
        )

    def call(self, request):
        return relay.handle_request(request, root=self.root)

    def retain(self):
        return {
            "action": "retain", "bank_id": BANK, "operation_id": CID,
            "document_id": DOC,
            "payload": {"content": "synthetic original", "metadata": {
                "source": "test:source", "content_sha256": "raw-hash-kept-as-metadata",
            }, "tags": ["synthetic"]},
        }

    def accepted(self):
        self.behavior.update(status=202, body={
            "success": True, "bank_id": BANK, "items_count": 1,
            "async": True, "operation_id": CID, "operation_ids": None,
        })

    def test_guarded_requests_never_reach_http(self):
        cases = [
            {"action": "delete_bank", "bank_id": BANK},
            {"action": "recall", "bank_id": "", "query": "x"},
            {"action": "recall", "bank_id": "default", "query": "x"},
            {"action": "recall", "bank_id": BANK, "query": "x", "url": "http://example.invalid"},
            {**self.retain(), "operation_id": None},
            {**self.retain(), "document_id": ""},
            {**self.retain(), "payload": {"content": "x", "document_id": "different"}},
        ]
        for value in cases:
            with self.subTest(value=value):
                result = self.call(value)
                self.assertEqual(result["state"], "NOT_SUBMITTED")
                self.assertFalse(result["request_started"])
        self.assertEqual(self.requests, [])

    def test_async_ack_preserves_source_and_cid_without_claiming_durable(self):
        self.accepted()
        request = self.retain()
        result = self.call(request)
        self.assertEqual(result["state"], "SUBMITTED")
        self.assertEqual(result["operation_id"], CID)
        self.assertEqual(result["document_id"], DOC)
        self.assertEqual(result["http_status"], 202)
        self.assertNotIn("durable", result)
        self.assertEqual(len(self.requests), 1)
        sent = self.requests[0]
        self.assertEqual(sent["path"], f"/v1/default/banks/{BANK}/memories")
        self.assertEqual(sent["authorization"], "Bearer " + TOKEN)
        self.assertEqual(sent["body"], {
            "async": True, "operation_id": CID,
            "items": [{**request["payload"], "document_id": DOC}],
        })
        self.assertEqual(result["body"], self.behavior["body"])

    def test_lost_post_ack_is_unknown_with_one_post_and_original_ids(self):
        self.behavior["drop"] = True
        result = self.call(self.retain())
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertTrue(result["request_started"])
        self.assertEqual((result["operation_id"], result["document_id"]), (CID, DOC))
        self.assertEqual(result["retries"], 0)
        self.assertEqual(len(self.requests), 1)

    def test_connect_failure_is_definitely_not_submitted(self):
        with patch.object(relay.http.client.HTTPConnection, "connect", side_effect=ConnectionRefusedError("synthetic")):
            result = self.call(self.retain())
        self.assertEqual(result["state"], "NOT_SUBMITTED")
        self.assertFalse(result["request_started"])
        self.assertEqual(result["operation_id"], CID)
        self.assertEqual(self.requests, [])

    def test_read_timeout_has_no_implicit_retry(self):
        self.behavior.update(slow=True, body={"results": []})
        result = self.call({
            "action": "recall", "bank_id": BANK, "query": "synthetic",
            "timeout_seconds": 0.1,
        })
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["retries"], 0)
        self.assertEqual(len(self.requests), 1)

    def test_recall_preserves_native_sources_and_usage(self):
        body = {"results": [{"text": "fact", "document_id": DOC}],
                "chunks": {"chunk": {"text": "original"}}, "usage": {"input_tokens": 7}}
        self.behavior["body"] = body
        result = self.call({"action": "recall", "bank_id": BANK, "query": "question",
                            "payload": {"include": {"chunks": {"max_tokens": 64}}, "budget": "low"}})
        self.assertEqual(result["state"], "RESPONSE")
        self.assertEqual(result["body"], body)
        self.assertEqual(self.requests[0]["body"]["query"], "question")
        self.assertEqual(self.requests[0]["path"], f"/v1/default/banks/{BANK}/memories/recall")

    def test_operation_not_found_remains_native_not_found(self):
        self.behavior["body"] = {"operation_id": CID, "status": "not_found"}
        result = self.call({"action": "get_operation", "bank_id": BANK, "operation_id": CID,
                            "payload": {"include_payload": True}})
        self.assertEqual(result["body"]["status"], "not_found")
        self.assertEqual(result["state"], "RESPONSE")
        self.assertEqual(self.requests[0]["path"],
                         f"/v1/default/banks/{BANK}/operations/{CID}?include_payload=true")

    def test_document_and_listing_routes_preserve_native_fields(self):
        self.behavior["body"] = {"id": DOC, "original_text": "original", "content_hash": "native-sanitized-hash"}
        result = self.call({"action": "get_document", "bank_id": BANK, "document_id": DOC})
        self.assertEqual(result["body"]["content_hash"], "native-sanitized-hash")
        self.assertEqual(self.requests[-1]["path"], f"/v1/default/banks/{BANK}/documents/{DOC}")
        self.behavior["body"] = {"bank_id": BANK, "total": 0, "operations": [], "limit": 20, "offset": 0}
        result = self.call({"action": "list_operations", "bank_id": BANK,
                            "payload": {"type": "batch_retain", "exclude_parents": False}})
        self.assertEqual(result["state"], "RESPONSE")
        self.assertIn("type=batch_retain", self.requests[-1]["path"])

    def test_invalid_success_ack_is_unknown_and_never_replayed(self):
        self.behavior.update(status=202, raw=b"not json")
        result = self.call(self.retain())
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["http_status"], 202)
        self.assertEqual(result["operation_id"], CID)
        self.assertEqual(len(self.requests), 1)

    def test_native_http_error_is_preserved_but_credentials_are_redacted(self):
        self.behavior.update(status=403, body={"detail": "Bearer " + TOKEN})
        result = self.call(self.retain())
        self.assertEqual(result["http_status"], 403)
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertEqual(len(self.requests), 1)

    def test_remote_origin_cannot_be_selected_by_configuration(self):
        (self.root / "config/mcp-client.env").write_text(
            "HINDSIGHT_WIN_LOCAL_MCP_URL=http://example.invalid/mcp/\n", encoding="utf-8")
        result = self.call({"action": "health"})
        self.assertEqual(result["state"], "NOT_SUBMITTED")
        self.assertEqual(self.requests, [])

    def test_missing_configuration_is_not_misreported_as_a_connect_failure(self):
        (self.root / "config/shared.env").unlink()
        result = self.call({"action": "health"})
        self.assertEqual(result["state"], "NOT_SUBMITTED")
        self.assertEqual(result["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(self.requests, [])

    def test_native_uuid_spelling_may_normalize_without_changing_the_callers_cid(self):
        self.behavior["body"] = {"operation_id": CID, "status": "pending"}
        result = self.call({"action": "get_operation", "bank_id": BANK,
                            "operation_id": CID.upper()})
        self.assertEqual(result["state"], "RESPONSE")
        self.assertEqual(result["operation_id"], CID.upper())
        self.assertEqual(result["body"]["operation_id"], CID)

    def test_reflect_and_global_read_actions_use_only_their_fixed_routes(self):
        self.behavior["body"] = {"text": "synthesis", "based_on": {"memories": ["source"]}}
        result = self.call({"action": "reflect", "bank_id": BANK, "query": "why"})
        self.assertEqual(result["state"], "RESPONSE")
        self.assertEqual(result["body"]["based_on"], {"memories": ["source"]})
        self.assertEqual(self.requests[-1]["path"], f"/v1/default/banks/{BANK}/reflect")
        self.behavior["body"] = {"banks": [], "total": 0}
        self.assertEqual(self.call({"action": "list_banks", "query": "project"})["state"], "RESPONSE")
        self.assertEqual(self.requests[-1]["path"], "/v1/default/banks?q=project")
        self.behavior["body"] = {"status": "healthy", "database": "connected"}
        self.assertEqual(self.call({"action": "health"})["state"], "RESPONSE")
        self.assertEqual(self.requests[-1]["path"], "/health")

    def test_slow_stream_cannot_extend_the_supplied_request_budget(self):
        self.behavior.update(drip=True, body={"results": [{"text": "x" * 300}]})
        result = self.call({"action": "recall", "bank_id": BANK,
                            "query": "synthetic", "timeout_seconds": 0.1})
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(len(self.requests), 1)

    def test_retry_after_is_preserved_without_retrying_in_this_layer(self):
        self.behavior.update(status=503, body={"detail": "synthetic upstream failure"},
                             headers={"Retry-After": "3"})
        result = self.call(self.retain())
        self.assertEqual(result["state"], "UNKNOWN")
        self.assertEqual(result["body"], self.behavior["body"])
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(result["response_headers"], {"Retry-After": "3"})
        self.assertEqual(len(self.requests), 1)

    def test_cli_consumes_one_json_line_and_returns_a_json_receipt(self):
        completed = subprocess.run(
            [sys.executable, str(SOURCE), "--root", str(self.root)],
            input=json.dumps({"action": "health"}) + "\n", text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "RESPONSE")
        self.assertNotIn(TOKEN, completed.stdout + completed.stderr)
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()

"""Optional FastMCP integration coverage using only in-memory synthetic servers.

Run with mcp/hindsight-memory/requirements.txt installed. The standard-library
repository suite reports one explicit module skip when FastMCP is unavailable.
No real service configuration, credentials, network endpoint, or model is used.
"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


if importlib.util.find_spec("fastmcp") is None:
    raise unittest.SkipTest("optional Hindsight MCP integration requires fastmcp==4.1.6")

from fastmcp import Client, FastMCP
from fastmcp.server import create_proxy
from fastmcp.tools import ToolResult
from mcp.types import TextContent


SOURCE = Path(__file__).resolve().parents[1] / "mcp/hindsight-memory/mcp_bridge.py"
relay_spec = importlib.util.spec_from_file_location(
    "hindsight_api_relay_bridge_tested", SOURCE.with_name("api_relay.py")
)
api_relay = importlib.util.module_from_spec(relay_spec)
relay_spec.loader.exec_module(api_relay)
spec = importlib.util.spec_from_file_location("hindsight_mcp_bridge_tested", SOURCE)
bridge = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"api_relay": api_relay}):
    spec.loader.exec_module(bridge)

BANK = "cf-fe__synthetic"
CID = "a472ae10-86bc-47bd-8a9a-ce86f68cf187"
NATIVE_CID = "4c911997-eb34-4fe9-a699-bba4dd993a3e"
DOCUMENT = "synthetic-source-revision-1"
SERVICE_TOKEN = "synthetic-hindsight-service-token"
API_KEY = "sk-fake-hindsight-key-abcdefghijklmnopqrstuvwxyz"


class HindsightMcpBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.native_calls = []
        upstream = FastMCP("synthetic-hindsight-upstream")

        @upstream.tool
        def retain(content: str, bank_id: str | None = None,
                   document_id: str | None = None,
                   metadata: dict[str, str] | None = None,
                   tags: list[str] | None = None, context: str | None = None) -> dict:
            self.native_calls.append({
                "content": content, "bank_id": bank_id,
                "document_id": document_id, "metadata": metadata,
                "tags": tags, "context": context,
            })
            return {"status": "native_mcp", "operation_id": NATIVE_CID}

        @upstream.tool
        def recall(query: str, bank_id: str | None = None) -> dict:
            self.native_calls.append({"query": query, "bank_id": bank_id})
            return {"results": []}

        @upstream.tool
        def list_banks() -> dict:
            return {"banks": []}

        @upstream.tool
        def credential_echo(mode: str, bank_id: str | None = None) -> dict:
            self.native_calls.append({"mode": mode, "bank_id": bank_id})
            if mode == "exception":
                raise RuntimeError(f"synthetic upstream exception: {SERVICE_TOKEN} {API_KEY}")
            receipt = {
                "state": "UNKNOWN" if mode == "error" else "RESPONSE",
                "operation_id": CID,
                "metadata": {
                    "source": "test:source@revision-1",
                    "echo": f"Bearer {SERVICE_TOKEN}; key={API_KEY}",
                },
            }
            return ToolResult(
                content=[TextContent(type="text", text=json.dumps(receipt)),
                         TextContent(type="text", text=f"token={SERVICE_TOKEN}; key={API_KEY}")],
                structured_content=receipt,
                meta={"trace_id": "synthetic-trace", "attempts": 1},
                is_error=mode == "error",
            )

        # The real guard runs around a real in-memory MCP proxy. Only the REST
        # boundary is replaced, so an accidental native retain remains visible.
        proxy = create_proxy(upstream, name="synthetic-guarded-proxy",
                             provider_error_strategy="raise")
        proxy.add_middleware(bridge.ProjectBankGuard(token=SERVICE_TOKEN))
        self.relay = Mock(side_effect=AssertionError("unexpected REST relay call"))
        self.relay_patch = patch.object(api_relay, "handle_request", self.relay)
        self.relay_patch.start()
        self.addCleanup(self.relay_patch.stop)
        self.modules_patch = patch.dict(sys.modules, {"api_relay": api_relay})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        self.client = await self.enterAsyncContext(Client(proxy))

    def retain_arguments(self):
        return {
            "bank_id": BANK,
            "content": "合成原文：只验证传输，不提交真实记忆。",
            "document_id": DOCUMENT,
            "metadata": {
                "source": "test:source@revision-1",
                "content_sha256": "synthetic-original-content-hash",
            },
            "tags": ["synthetic", "source-preservation"],
            "context": "synthetic integration test",
        }

    async def test_schema_requires_project_bank_and_exposes_optional_retain_cid(self):
        tools = {tool.name: tool for tool in await self.client.list_tools()}
        for name in ("retain", "recall"):
            with self.subTest(tool=name):
                schema = tools[name].input_schema
                self.assertIn("bank_id", schema["required"])
                self.assertEqual(schema["properties"]["bank_id"]["type"], "string")
        retain_schema = tools["retain"].input_schema
        self.assertEqual(retain_schema["properties"]["operation_id"]["format"], "uuid")
        self.assertNotIn("operation_id", retain_schema["required"])
        self.assertNotIn("bank_id", tools["list_banks"].input_schema.get("required", []))
        self.relay.assert_not_called()
        self.assertEqual(self.native_calls, [])

    async def test_missing_or_default_bank_blocks_both_routes_before_submission(self):
        for with_cid in (False, True):
            for bank in (None, "", " ", "default", "DEFAULT", " " + BANK):
                with self.subTest(with_cid=with_cid, bank=bank):
                    arguments = self.retain_arguments()
                    if bank is None:
                        arguments.pop("bank_id")
                    else:
                        arguments["bank_id"] = bank
                    if with_cid:
                        arguments["operation_id"] = CID
                    result = await self.client.call_tool("retain", arguments, raise_on_error=False)
                    self.assertTrue(result.is_error)
                    self.assertIn("bank_id", " ".join(part.text for part in result.content))
        self.relay.assert_not_called()
        self.assertEqual(self.native_calls, [])

    async def test_caller_cid_uses_one_rest_submission_and_preserves_source_payload(self):
        arguments = {**self.retain_arguments(), "operation_id": CID}
        original = deepcopy(arguments)
        self.relay.side_effect = None
        self.relay.return_value = {
            "state": "SUBMITTED", "operation_id": CID, "document_id": DOCUMENT,
            "attempts": 1, "retries": 0,
        }

        result = await self.client.call_tool("retain", arguments)

        expected_payload = {key: value for key, value in original.items()
                            if key not in {"bank_id", "operation_id", "document_id"}}
        self.relay.assert_called_once_with({
            "action": "retain", "bank_id": BANK, "operation_id": CID,
            "document_id": DOCUMENT, "payload": expected_payload,
        }, root=bridge.ROOT)
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "accepted")
        self.assertEqual(result.structured_content["operation_id"], CID)
        self.assertNotIn("durable", result.structured_content)
        self.assertEqual(json.loads(result.content[0].text), result.structured_content)
        self.assertEqual(arguments, original)
        self.assertEqual(self.native_calls, [])

    async def test_unknown_rest_receipt_is_mcp_error_with_cid_and_no_native_fallback(self):
        receipt = {
            "state": "UNKNOWN", "action": "retain", "bank_id": BANK,
            "operation_id": CID, "document_id": DOCUMENT,
            "request_started": True, "attempts": 1, "retries": 0,
            "error": {"code": "TIMEOUT", "message": "synthetic lost acknowledgement"},
        }
        self.relay.side_effect = None
        self.relay.return_value = receipt

        result = await self.client.call_tool(
            "retain", {**self.retain_arguments(), "operation_id": CID},
            raise_on_error=False,
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content, receipt)
        self.assertEqual(json.loads(result.content[0].text), receipt)
        self.assertEqual(result.structured_content["operation_id"], CID)
        self.relay.assert_called_once()
        self.assertEqual(self.native_calls, [])

    async def test_retain_without_caller_cid_keeps_native_mcp_route(self):
        arguments = self.retain_arguments()

        result = await self.client.call_tool("retain", arguments)

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content,
                         {"status": "native_mcp", "operation_id": NATIVE_CID})
        self.assertEqual(self.native_calls, [arguments])
        self.relay.assert_not_called()

    async def test_native_results_and_exceptions_redact_credentials_without_losing_receipt(self):
        for mode in ("success", "error", "exception"):
            with self.subTest(mode=mode):
                result = await self.client.call_tool_mcp(
                    "credential_echo", {"mode": mode, "bank_id": BANK},
                )
                wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                serialized = json.dumps(wire)
                self.assertNotIn(SERVICE_TOKEN, serialized)
                self.assertNotIn(API_KEY, serialized)
                self.assertIn("<service-token-redacted>", serialized)
                self.assertIn("<api-key-redacted>", serialized)
                self.assertEqual(wire["isError"], mode != "success")
                if mode == "exception":
                    self.assertIn("synthetic upstream exception", wire["content"][0]["text"])
                else:
                    expected = {
                        "state": "UNKNOWN" if mode == "error" else "RESPONSE",
                        "operation_id": CID,
                        "metadata": {
                            "source": "test:source@revision-1",
                            "echo": "Bearer <service-token-redacted>; key=<api-key-redacted>",
                        },
                    }
                    self.assertEqual(wire["structuredContent"], expected)
                    self.assertEqual(json.loads(wire["content"][0]["text"]), expected)
                    self.assertEqual(wire["content"][1]["text"],
                                     "token=<service-token-redacted>; key=<api-key-redacted>")
                    self.assertEqual(wire["_meta"]["trace_id"], "synthetic-trace")
                    self.assertEqual(wire["_meta"]["attempts"], 1)
                    self.assertEqual(wire["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
                                     "synthetic-hindsight-upstream")
        self.assertEqual(self.native_calls,
                         [{"mode": mode, "bank_id": BANK} for mode in ("success", "error", "exception")])
        self.relay.assert_not_called()


if __name__ == "__main__":
    unittest.main()
